# Local clusters: kiac, not podman+kind

**Status: policy set 2026-09-03.** All three of this project's local clusters
(`kiac-dev`, `kiac-man`, `kiac-prod`) run on **kiac** (`saiyam1814/tap/kiac`, brew,
currently v0.5.1) — "kind, but each node is its own Apple `container` VM" — on top of
Apple's native `container` runtime (`apple/container`), not Docker or Podman.

**Going forward, any new local cluster — including throwaway ones for mockups, demos,
or one-off experiments — should be a kiac cluster, not a temporary podman-backed kind
cluster.** Podman+kind clusters have been stood up for this kind of throwaway work
before (see `radar-rollouts-demo-control-plane` and the various randomly-named
containers still sitting in `podman ps -a` on this machine); that pattern is
deprecated. kiac clusters boot in seconds, get real per-node VM isolation, and
`metrics-server` works out of the box — there's no remaining reason to reach for
podman+kind here.

## The three clusters

| Cluster | Context | Role | Bootstrap script | `--cpus` / `--cp-memory` |
|---|---|---|---|---|
| `kiac-dev` | `kiac-dev` | Fleet's one `dev` cluster: Infisical, `platform-cicd` control plane (Tekton/PaC), Bootstrap-tier XRDs, every app's `-dev`/`-cicd` namespace pair | `gitops-cluster-dev/hack/start-kiac-dev.sh` | 5 / 20G |
| `kiac-man` | `kiac-man` | Backstage's hand-managed deploy target (`gitops-cluster-template`'s `60-backstage/` tier) | `gitops-cluster-kind-man/hack/start-kiac-man.yaml` (a bash script despite the extension) | 2 / 10G |
| `kiac-prod` | `kiac-prod` | Fleet's `upper`-type cluster | `gitops-cluster-kind-prod/hack/start-kiac-prod.yaml` (ditto) | 2 / 10G |

All three are **single-node** (`--workers 0`, so the control-plane VM carries etcd,
kube-apiserver, kubelet, containerd, the Cilium agent, and every workload — nothing is
offloaded to a separate worker), `--cni cilium --kernel full --gateway` (Gateway API +
Traefik). Recreate via the exact script/flags above, never by hand-typing
`kiac create cluster` — see [[feedback_kiac_cp_memory_flag]] in memory for the
incident a hand-typed `--memory` (worker-only, silently ignored with `--workers 0`)
instead of `--cp-memory` caused.

## The recurring failure mode: node VM reboot leaves CoreDNS/Cilium wedged

**Symptom:** `kubectl --context kiac-dev ...` starts returning `dial tcp <ip>:6443:
connect: host is down` (not a timeout — the VM's IP has changed or the VM restarted).
Confirmed twice now (2026-09-02, 2026-09-03), both times on `kiac-dev`, both times
under real load — once from a cluster-wide OOM (see the `--cp-memory` incident above),
once from a Backstage CI build running several concurrent `node-gyp` native-module
compiles (`better-sqlite3`, `keytar`, `tree-sitter`, `ssh2`, etc.) on top of a node
that `kubectl top node` already showed sitting at 83% CPU / 83% memory *before* the
build started — `kiac-dev` is carrying a lot for a single 5-vCPU/20G VM (platform-cicd
control plane + Infisical + full observability + 7 tenant apps' CI+dev namespaces, on
top of ArgoCD ×2, Kyverno, Testkube, Crossplane). Worth bumping `--cpus`/`--cp-memory`
further, or reducing concurrent workload count, if this keeps recurring rather than
treating each recurrence as a one-off.

**Root cause of the *symptom* (not the resource pressure itself):** a node VM
reboot/IP-change leaves the host's kubeconfig pointed at a stale IP, and independently
leaves CoreDNS's and Cilium's pod *sandboxes* (not just the containers) in a stale
state kubelet hasn't reconciled — `kubectl describe pod` on the CoreDNS pods shows
`Status: Running` at the top but the actual container `Terminated, Reason: Unknown,
Exit Code: 255, Restart Count: 0` — kubelet never even attempted a restart on its own.
This cascades: no CoreDNS -> no cluster DNS -> most workloads that resolve any
in-cluster or external hostname fail, which is why `kiac verify` reports it as "112
unhealthy workloads" rather than "CoreDNS is down" — the real fault is one line item
(`cluster DNS kube-dns has no ready endpoints`) buried in that report, not the 100+
downstream symptoms.

### The fix

```sh
kiac resume cluster --name dev   # or man / prod
```

This is the documented, purpose-built recovery command ("Boot a stopped cluster's VMs
and heal it after a host reboot... refreshes the host kubeconfig and networking
helpers... **safe to re-run; a running cluster is a no-op**"). Confirmed live
2026-09-03: took 34 seconds to detect the IP change, heal control-plane certs/configs,
re-point kube-proxy/cluster-info, and rewrite `~/.kube/config`'s `kiac-dev` context —
after which CoreDNS and Cilium's pod sandboxes were kicked (`SandboxChanged, it will
be killed and re-created`) and self-recovered within ~30 more seconds with no further
intervention. Went from 112 unhealthy workloads to 1 (a long-standing, unrelated
`tempo-0` CrashLoopBackOff in `observability`, and a handful of `Error`-state Job pods
whose CronJobs simply ran during the DNS-outage window and will succeed on their next
scheduled run) — both harmless leftovers, not things `kiac resume` needed to fix.

**Run `kiac resume cluster --name <cluster>` any time a `kiac-*` context stops
responding, before assuming a full recreate is needed.** Recreating from scratch is
the fallback of last resort, not the default response to "cluster is unreachable."

### Diagnostic commands, in the order to reach for them

1. `kiac get clusters` — is the cluster's VM actually running? (Bypasses a stale
   kubeconfig entirely — reads real VM state, not the K8s API.)
2. `container list` — the actual VM's current IP/CPU/memory, ground truth independent
   of kubectl.
3. `kiac resume cluster --name <cluster>` — the fix, per above. Always safe to run
   first; a no-op if nothing was actually wrong.
4. `kiac verify cluster --name <cluster>` — a real health check (node readiness,
   cluster DNS, metrics API, storage, Gateway API, LoadBalancer controller, edge
   proxy, and a live workload-health scan) that pinpoints the *actual* fault instead of
   leaving you to guess from a wall of unhealthy Pods across a dozen namespaces.

### What NOT to do

- Don't try to diagnose or recover a `kiac-*` cluster via `podman inspect`/`podman
  start`/`podman ps` — podman is not involved at all. Old `dev-control-plane`/
  `man-control-plane`/`prod-control-plane` podman container names are stale leftovers
  from a previous kind-based setup this project no longer uses; they will show as
  stopped/exited regardless of whether the real `kiac-*` cluster is healthy, and
  starting them does nothing.
- Don't hand-type `kiac create cluster` flags from memory — copy the cluster's own
  hack script/config exactly (see the table above). A single wrong flag
  (`--memory` instead of `--cp-memory`) silently no-ops on a `--workers 0` cluster and
  produces the exact same "cluster wedged under load" symptom this doc describes, for
  a completely different reason.

## Reaching these clusters: Gateway API is primary, port-forwarding is for testing only

Every UI-bearing Service on all three clusters is exposed via a real Gateway API
`HTTPRoute` through kiac's own installed Gateway (`kiac`, ns `kiac-gateway`,
`GatewayClass traefik` -> `traefik.io/gateway-controller`), reached directly at the
cluster's control-plane VM IP on port 80 (HTTP only — no TLS listener exists on any
of the three today). **`kubectl port-forward` is a testing fallback, not how you
normally reach anything here** — `/Users/jerf/tech/port-forwards.sh` still exists and
was fixed 2026-09-03 (corrected `kind-*` -> `kiac-*` context names it had carried
since before the kiac migration, dropped a dead podman-specific cgroup workaround
that doesn't apply to kiac's per-node-VM isolation, fixed a `disown`-under-`set -e`
bug that aborted the whole script when run non-interactively), but reach for it only
when you specifically need a raw port-forward for debugging — not as the everyday
path.

| Hostname | Cluster | Service | Namespace | Defined in |
|---|---|---|---|---|
| `argocd.dev.kiac.local` | dev | `argocd-server` | `argocd` | `gitops-cluster-dev/60-gateway-routes` |
| `argocd-apps.dev.kiac.local` | dev | `argocd-apps-server` | `argocd-apps` | ditto |
| `grafana.dev.kiac.local` | dev | `kube-prometheus-stack-grafana` | `observability` | ditto |
| `minio-console.dev.kiac.local` | dev | `minio-console` | `observability` | ditto |
| `infisical.dev.kiac.local` | dev | `infisical-infisical-standalone-infisical` | `infisical` | ditto |
| `tekton.dev.kiac.local` | dev | `tekton-dashboard` | `tekton-pipelines` | ditto |
| `argocd.prod.kiac.local` | prod | `argocd-server` | `argocd` | `gitops-cluster-kind-prod/50-gateway-routes` |
| `argocd-apps.prod.kiac.local` | prod | `argocd-apps-server` | `argocd-apps` | ditto |
| `grafana.prod.kiac.local` | prod | `kube-prometheus-stack-grafana` | `observability` | ditto |
| `minio-console.prod.kiac.local` | prod | `minio-console` | `observability` | ditto |
| `backstage.man.kiac.local` | man | `backstage` | `backstage` | `gitops-cluster-kind-man/60-backstage/backstage/httproute.yaml` |

### The `/etc/hosts` staleness gap — and why it exists

`/etc/hosts` must map each hostname above to its cluster's *current* control-plane
VM IP, gotten from `container list` or `kiac get nodes`. That IP is not stable: **it
changes on every VM boot, and there is no way to fix/pin it today.**

This was investigated directly 2026-09-03, prompted by the same IP churn that breaks
kubeconfig (see above). Findings, from kiac's own upstream design doc
([`docs/design/persistent-clusters.md`](https://github.com/saiyam1814/kiac/blob/main/docs/design/persistent-clusters.md)
and [`examples/resume-drill.md`](https://github.com/saiyam1814/kiac/blob/main/examples/resume-drill.md)):

- **Root cause is platform-level, not kiac's choice.** "vmnet allocates addresses
  dynamically at each boot... one machine went `.82 -> .83 -> .84 -> .85` across
  create/run/stop cycles." Confirmed independently here too: `kiac-dev` alone has
  been `.5 -> .9 -> .11` across the incidents in this doc.
- **`container machine`** (a newer, disk-persistent VM primitive from Apple) was
  investigated by kiac's maintainers as a possible fix and **rejected** — it has the
  exact same fresh-IP-per-boot behavior, so switching to it wouldn't help, and it also
  lacks a boot-command hook kiac needs for its own entrypoint logic.
- **No fix is planned upstream.** kiac's own docs say revisiting this only makes sense
  "if Apple adds a stable boot-command/entrypoint hook or stable addressing" to
  `apple/container` itself — nothing either kiac or this project can build around.
- kiac's actual answer isn't a fixed IP at all — it's **healing**: `kiac resume`
  detects the new IP and rewrites every Kubernetes-internal reference to it (certs,
  kubelet config, kube-proxy, the host's own kubeconfig). That's exactly the mechanism
  used above. **`/etc/hosts` is outside that scope** — kiac doesn't know these
  hostnames exist, so nothing heals them automatically.

**The fix for the fix:** `/Users/jerf/tech/refresh-kiac-hosts.sh` (built 2026-09-03) —
reads each running cluster's current IP via `container list` and rewrites a marked
block in `/etc/hosts` to match, leaving a stopped cluster's hostnames out entirely
rather than writing a stale entry. Needs root (`/etc/hosts` is root-owned):

```sh
sudo /Users/jerf/tech/refresh-kiac-hosts.sh
```

Idempotent and safe to re-run any time a hostname stops resolving — back up is taken
automatically before each write. Run it after any `kiac resume` where the tool
reported an IP change, or any time a `*.kiac.local` URL stops responding. Its
`CLUSTER_HOSTS` table is a manually maintained mirror of the table above and the
gateway-routes READMEs it's sourced from — update all of them together if a new
`HTTPRoute` is added.

### `*.kiac.local` doesn't resolve inside a pod at all — a different gap

`/etc/hosts` on the Mac host is invisible to pods running inside a kiac cluster's
VMs — a pod's CoreDNS forwards anything it doesn't own straight to
`/etc/resolv.conf`, it never consults the host machine's `/etc/hosts`. Anything
running as a workload that needs to reach another cluster's `*.kiac.local` hostname
needs its own fix, not just `refresh-kiac-hosts.sh`. Found and fixed three times so
far (2026-09-04), all via a literal IP in a Kubernetes `hostAliases` entry rather
than trying to make the hostname resolve in-cluster:

- Backstage's Deployment (`kiac-man`) reaching ArgoCD/kube-apiserver on dev/prod.
- external-secrets' controller (`kiac-man`, `kiac-prod`) reaching Infisical on
  `kiac-dev`.
- checkout-api's `platform-outcome-postsync`/`platform-outcome-syncfail` hook Jobs
  (`kiac-prod`) reaching `argocd-outcome-relay` on `kiac-dev` —
  `releaseTracking.relayHostAliasIP` in `idp-application`'s chart, resolved fresh
  per release by `open-release-pr.yaml`'s own downward-API `status.hostIP` (that
  Task's pod runs on the dev cluster itself, so it always knows dev's current IP
  without needing DNS or `/etc/hosts` either).

`refresh-kiac-hosts.sh` keeps the first two patched on every run; it also patches
the third for any app whose `values.yaml` already has `relayHostAliasIP` set, to
cover IP drift between releases (a real release re-bakes it fresh regardless). If a
new workload needs the same cross-cluster reachability, it needs this same
treatment, not just a `*.kiac.local` entry in `/etc/hosts`.

## See also

- [[project_kiac_dev]] in memory — running incident log for kiac-dev/kiac-prod,
  more granular/session-scoped than this doc.
- [[feedback_kiac_cp_memory_flag]] in memory — the `--memory` vs `--cp-memory` gotcha.
- [[feedback_prefer_kiac_over_podman_kind]] in memory — the policy this doc's intro
  section states.
- `docs/cluster-provisioning.md` — a different concern: provisioning new *permanent*
  fleet clusters (`gitops-cluster-template` + `hack/customize-cluster.sh`), not local
  kiac VM lifecycle.
