# GitOps strategy

**Status: structural design resolved (2026-08-12). §10 built and live-verified
2026-08-16** (see that section's own status note for the mechanism and what was
proven). This is the first design doc for Dream
IDP ([[project-dream-idp]] in `platform-cicd`'s memory), which absorbs `platform-cicd` as
one component rather than replacing it. Where this doc extends a pattern already
live-verified in `platform-cicd`, it says so and links to the source doc — the intent is
continuity of design language, not a rewrite. Where it makes a new call `platform-cicd`
never had to make (two ArgoCD instances, many prod clusters, fleet-wide cluster config),
it says that too, and flags it explicitly under "Open questions" at the end rather than
quietly picking one.

**Review rounds 1 and 2 (2026-08-12)**: every open question from the first draft is
resolved below, inline in the relevant sections, not just listed at the bottom — search
for "resolved" or "confirmed" if you're skimming for what changed.

## Terminology

Carried over unchanged from `platform-cicd/docs/multi-cluster.md`:

- **env** — a logical environment name (`dev`, `staging`, `prod`), tenant-facing.
- **cluster** — a physical Kubernetes cluster, platform-infra-facing. One dev cluster,
  potentially many prod clusters. An env and a cluster are joined only through a
  registry, never hardcoded together — this doc keeps that separation and generalizes it
  from "which cluster does this release land on" to "which clusters does this app exist
  on at all."

New for this doc:

- **cluster admin** — owns cluster-level config: what's installed, what policy applies,
  which apps are permitted on a cluster at all. Never touches an app's own source or
  deployment config.
- **app owner** — owns an app's source repo and its gitops repo. Never touches cluster
  config, never gets credentials to any cluster's API.
- **fleet** — the full set of clusters (one dev + N prod) under one cluster-admin team's
  management.

## Guiding constraint, carried forward unchanged

`platform-cicd`'s multi-cluster design (`docs/multi-cluster.md`) established, and
live-verified twice, a rule this doc treats as foundational rather than re-litigating:
**no cluster ever holds credentials for another cluster's API, and no component on one
cluster ever calls another cluster's API directly.** The only thing that ever crosses
a cluster boundary is a reviewed, merged git commit; outcomes flow back as CDEvents, not
API calls. Everything below is designed to hold that property at fleet scale, not just
for the one dev→staging hop it was originally proven on.

---

## 1. Repo topology

Six repo shapes, each with one owner:

| Repo | Owner | Contains |
|---|---|---|
| `<app-name>` | app owner | source code, `cicd.yaml`, a `platform/` self-service folder — developer-authored Crossplane Claims, plus lower-env definitions (§10) |
| `gitops-<app-name>` | app owner (writes only via CI-opened, reviewed PRs) | rendered/patched deploy config for that app's **upper environments only** — see §10 |
| `gitops-cluster-<cluster-name>` | cluster admin | one cluster's static declarative config, organized into logical groups (§3) |
| `gitops-cluster-<cluster-name>-tenants` | cluster admin (lighter review — see below) | which apps are onboarded to *this* cluster — the churny per-app-onboarding half, split out of the repo above |
| `idp-cluster-baseline` | cluster admin | shared Helm chart(s) every cluster's logical groups pin a version of — the "same tooling everywhere, thin per-cluster diff" layer. See §8 for why this is a Helm chart, not a Kustomize remote base. |
| `idp-service-catalog` | cluster admin (initially) | Crossplane XRDs/Compositions + the shared app-facing Helm chart(s) that `gitops-<app-name>` repos build on |

**One app repo, one gitops repo — unchanged from `platform-cicd`.** An app that targets
three clusters still has exactly one `gitops-<app-name>` repo; it just has three
directories inside it (see §5).

### Cluster config repo shape: per-cluster, confirmed

You've never used a monorepo for a cluster fleet, and that instinct holds up under a real
look at the tradeoffs — **per-cluster repos (`gitops-cluster-<name>`), confirmed.**

**The argument that actually settles it, not just an ergonomics preference**: §6 already
relies on `AppProject.sourceRepos` as the real, live-tested enforcement boundary for
apps — a throwaway `Application` pointed at a repo outside an `AppProject`'s
`sourceRepos` gets hard-rejected by ArgoCD at the API level, confirmed live in
`platform-cicd`. That rejection is **repo-level only**. ArgoCD has no equivalent
primitive for "this `AppProject` may sync `clusters/prod-eu/` of this repo but not
`clusters/prod-us/`" — `AppProject` scopes by repo URL, never by path. A monorepo would
leave the actual cluster boundary resting entirely on each `Application`'s own
`spec.source.path` being correct — a config-correctness convention, not something
ArgoCD would ever reject if it were wrong. A misconfigured or copy-pasted `Application`
pointing `argocd-platform` on `prod-eu` at `clusters/prod-us/` inside a shared repo
would sync without complaint. Per-cluster repos make the same enforced boundary this
doc already relies on for apps (§6) apply to clusters too, for free — this isn't just
nicer, it's what makes that boundary mean anything at the cluster level.

Everything else is secondary to that, but worth having on record:

**Pros of per-cluster repos:**
- Blast radius matches repo permissions, not directory conventions or CODEOWNERS path
  rules that have to stay correct as the fleet grows.
- A bad merge structurally cannot span two clusters — different repo, different PR,
  different history.
- Independent audit trail per cluster (`git log` on one repo is the full change history
  for that cluster, no filtering out every other cluster's noise).
- Decommissioning a cluster is archiving one repo, not surgically removing a directory
  from a shared history.
- CI/PR checks scope naturally per repo — no path-filter logic in a shared pipeline
  deciding which cluster's checks should run for a given PR (a common, quiet source of
  bugs in monorepo CI: a PR touching two clusters' directories only triggering one
  cluster's checks depending on how the filter's written).

**Cons, real, not dismissed:**
- N repos to provision instead of one — mitigate with a repo-scaffolding script once
  the logical-group directory shape (§3) is finalized; not solved by this doc.
- A genuinely fleet-wide change (a security patch every cluster needs) is N PRs, not
  one atomic commit. `idp-cluster-baseline` absorbs most of the actual content-diff cost
  (the manifest changes once, each cluster repo just bumps a pin), but opening/merging N
  PRs is still real toil at large fleet size — revisit with a bot that opens all N
  pin-bump PRs together if the fleet grows large enough for this to hurt.
- No single-repo `diff` between two clusters' config — needs small tooling (`git diff`
  across two clones, or a script) if "what's different between prod-us and prod-eu"
  becomes a frequent question. Consistent logical-group directory naming (§3) across
  every cluster repo keeps this tractable even without dedicated tooling.

### Splitting static cluster config from app onboarding

Per your steer: `gitops-cluster-<name>` should stay close to read-only in practice —
mostly-static config that changes rarely and carries real review weight. App onboarding
(which apps exist on this cluster) is a different traffic pattern — frequent,
individually low-stakes — and its PR noise shouldn't dilute review attention on the
rare, high-stakes cluster-config changes. Splitting it into
`gitops-cluster-<name>-tenants` does that.

Mechanism: the tenant-onboarding `ApplicationSet` resource itself still lives in
`gitops-cluster-<name>/02-argocd-apps/` — it's infrastructure, it rarely changes, it
belongs with the rest of static cluster config. Only its generator's `git.repoURL` field
points at `gitops-cluster-<name>-tenants` instead of a local path — no separate
`Application` resource needed, changing that one field is the whole mechanism. Each
entry in the tenants repo is the same shape as `platform-cicd`'s existing
`tenants/<app>/identity.yaml` (operator-owned, PR-reviewed, minimal — app name, repo
URLs, namespace; no live config).

**Confirmed (review round 2, 2026-08-12): per-cluster.** One
`gitops-cluster-<name>-tenants` per `gitops-cluster-<name>`, not a fleet-wide tenants
repo — keeps "which repos can this cluster admin touch" answerable by team membership
alone, same property the cluster-config repo split relies on.

## 2. Two ArgoCD instances per cluster

Every cluster — dev included — runs two ArgoCD instances, matching your requirement
directly:

- **`argocd-platform`** — cluster-admin scoped. Watches only `gitops-cluster-<name>`.
  RBAC restricted to the cluster-admin group. Manages: bootstrap namespaces/RBAC,
  CRDs and operators (Crossplane + Providers, cert-manager, External Secrets Operator,
  ingress, Kyverno), the service-catalog XRDs/Compositions themselves, the
  observability stack, and — see §4 — **its own installation**, plus the installation
  and RBAC of `argocd-apps`.
- **`argocd-apps`** — app-owner facing. Watches per-app Applications generated by an
  ApplicationSet (§5), each scoped to a real per-app `AppProject` (§6). Cluster admins
  provision the instance and its AppProjects (from `argocd-platform`); app owners never
  get access to `argocd-apps`'s own admin surface, only to their own app's `Application`
  inside their own scoped project.

This is the structural enforcement of requirement 6 (separation of duties): an app
owner's blast radius is bounded by an `AppProject`, entirely inside `argocd-apps`. A
cluster admin's actions land through `argocd-platform`, entirely inside
`gitops-cluster-<name>`. Neither instance's RBAC nor its watched repo overlaps with the
other's.

## 3. Logical groupings inside a cluster's config (requirement 7)

`gitops-cluster-<name>` (thin — mostly version pins into `idp-cluster-baseline` plus
cluster-specific values) renders as **one ArgoCD `Application` per top-level directory**,
all owned by a single app-of-apps root inside `argocd-platform`:

```
gitops-cluster-<name>/
  00-bootstrap/          namespaces, base RBAC, NetworkPolicies, ResourceQuotas
  01-argocd-platform/    argocd-platform's own install + config — see §4, "ArgoCD manages ArgoCD"
  02-argocd-apps/        argocd-apps's install, its AppProjects, the tenant-onboarding ApplicationSet
  10-crds-operators/     Crossplane + Providers/Configurations, cert-manager, ESO, ingress
  20-service-catalog/    the idp-service-catalog XRDs/Compositions, pinned to a version
  30-policy/             Kyverno policies, cluster-wide guardrails
  40-observability/      Prometheus/Grafana/Tempo/Loki stack
  50-platform-cicd/      dev cluster only — Tekton, PaC, the CDEvents broker (see §7)
```

Numeric prefixes are ordering hints for humans reading the repo, not a dependency
mechanism ArgoCD enforces — real ordering (e.g. CRDs before anything that uses them)
still needs `sync-wave` annotations where it matters, same as any app-of-apps setup.

`02-argocd-apps/`'s tenant-onboarding `ApplicationSet` resource lives here, but its
generator reads from a separate `gitops-cluster-<name>-tenants` repo, not a local
directory — see §1's "Splitting static cluster config from app onboarding" for why.

## 4. "Let ArgoCD manage ArgoCD" — the bootstrap sequence

One manual step per new cluster, reusing the `hack/bootstrap-upper-cluster.sh` precedent
from `platform-cicd`: install `argocd-platform` via script, then create a single root
`Application` ("self") pointed at that cluster's own `01-argocd-platform/` directory,
which contains `argocd-platform`'s own Helm values and `Application` manifest. From that
point on, upgrading `argocd-platform` itself — version bumps, RBAC changes, resource
limits — is an ordinary PR to `gitops-cluster-<name>`, not a manual `helm upgrade`.

`argocd-platform`'s app-of-apps root also owns `02-argocd-apps/` — so `argocd-apps` is
itself entirely GitOps-managed, but managed *by cluster admins*, never by itself and
never by app owners. This is the concrete mechanism behind "cluster admins manage
cluster config, app owners manage their own app" holding even for the platform's own
control plane: an app owner has no path, accidental or otherwise, to alter `argocd-apps`'s
RBAC, because that file lives in a repo they have no write access to.

## 5. Per-app delivery: one Application, one namespace, N clusters (requirements 4, 5)

Reuses the two-source `Application` pattern from `platform-cicd`'s tenant-onboarding
ApplicationSet (`charts/platform-cicd-control-plane/templates/argocd/
tenant-onboarding-applicationset.yaml`), live-attack-tested there: `helm.valuesObject`
(operator-owned, source 0) takes real precedence over `helm.valueFiles` (developer-owned,
source 1) for any overlapping key — confirmed live, zero effect from a planted hostile
override.

Generalized for the IDP:

- **Source 0**: `idp-service-catalog`'s shared chart, pinned to a version, with an
  operator-owned `valuesObject` — namespace, app identity, which cluster/env this
  rendering targets.
- **Source 1**: `$ref`-only into the app's own `gitops-<app-name>` repo,
  `directory: {exclude: "*"}` (closes a real gotcha `platform-cicd` hit live — a
  `$ref`-only source is still auto-detected as a plain directory source and applies
  every YAML in it unless explicitly excluded), `valueFiles: [$appsrc/<cluster>/<env>/
  values.yaml]`.

`gitops-<app-name>` layout — **upper environments only**, see §10 for where lower/
ephemeral envs live instead:

```
gitops-<app-name>/
  kind-prod-1/staging/values.yaml
  kind-prod-2/prod/values.yaml
```

One `values.yaml` per cluster×env an app actually targets — satisfies requirement 4 (an
app can deploy to more than one cluster) structurally: adding a cluster is adding a
directory, not restructuring anything.

**One Application, one namespace, bundled supporting components (requirement 5)**: the
shared chart's templates render everything for one app+env — the app's own Deployment/
Service, and any Crossplane Claims for components the app owns the lifecycle of (a
cache, an auth sidecar's config) — as part of the *same* Helm release, into the *same*
`app-<name>-<env>` namespace (naming convention unchanged from `platform-cicd`'s
`docs/naming-conventions.md`). Nothing here creates a second `Application` per component;
"supporting components are part of the application" is enforced by them being template
outputs of one chart, not a separate onboarding path.

## 6. AppProject scoping — closing a known gap, not just repeating it

`platform-cicd`'s `docs/multi-cluster.md` names an explicit, deliberate gap: a
cluster-mapped release's `Application` uses `project: default` rather than a real scoped
`AppProject`, because that piece wasn't built yet. This design closes it from day one,
since a fleet of many prod clusters is exactly where an unscoped project becomes a real
risk rather than a theoretical one: every app onboarded via the `argocd-apps`
ApplicationSet gets its own `AppProject` —

- `sourceRepos`: `idp-service-catalog` + that app's own `gitops-<app-name>` only.
- `destinations`: that app's own `app-<name>-<env>` namespace(s) on this cluster only.

Reuses the exact boundary-enforcement pattern already live-tested in `platform-cicd`
(a throwaway `Application` pointed outside an `AppProject`'s `sourceRepos` was created at
the API level but ArgoCD refused to reconcile it — real rejection, confirmed live, not
just scoped-looking YAML).

This is the **upper-env** `AppProject`. §10 defines a second, separately-scoped
`AppProject` per app for lower/ephemeral environments on the dev cluster — same
enforcement mechanism, deliberately different `sourceRepos`/`destinations`.

## 7. Where `platform-cicd` fits

`platform-cicd`'s own control plane (Tekton, Pipelines-as-Code, the CDEvents broker)
keeps running on the dev cluster, as `50-platform-cicd/` inside `gitops-cluster-dev`'s
own logical groups — it becomes cluster config like everything else, GitOps-managed by
`argocd-platform` rather than the ad hoc `helm upgrade` steps `hack/bootstrap.sh` uses
today. Its own tenant-onboarding ApplicationSet (`tenants/*/identity.yaml`, driving
`argocd-apps` on the dev cluster specifically) is the direct ancestor of §5's pattern —
this doc generalizes it to N clusters, it doesn't replace its mechanism.

The multi-cluster release/outcome loop already built there (sync hooks → `argocd-outcome-
relay` → CDEvents broker, `docs/multi-cluster.md` §"the feedback relay") is the same
event-driven backbone this doc assumes for every cluster, not just upper-env releases —
see "Forward-looking, not built here" below.

## 8. Chart delivery: git source, not OCI, for now

`platform-cicd` already investigated packaging its own charts as OCI images for ArgoCD
to pull (`platform_cicd_session_oci_chart_investigation`) and found a real, confirmed
ArgoCD limitation: semver-*range* `targetRevision` resolution doesn't work against OCI
sources at all (argoproj/argo-cd#9528 — the resolver needs an `index.yaml`, which OCI
registries don't have). Exact-version pins work fine over OCI; floating ranges don't.
`idp-service-catalog` and `idp-cluster-baseline` both use **git source, pinned to a tag**,
for the same reason `platform-cicd` reverted to git: the property actually wanted here
("publish, ArgoCD tracks it, no manual re-pin for active development") is what a git
source already gives natively. Revisit OCI + exact pins together with `platform-cicd`'s
already-flagged follow-up ("a real, proper, versioned/tested/promoted release workflow")
rather than solving it twice.

### `idp-cluster-baseline` specifically: Helm chart, not a Kustomize remote base (resolved)

This is the direct replacement for the "common cluster repo pulled in as a Kustomize
remote resource" piece of your prior GitOps strategy — same role (one shared source for
common cluster config, patched per cluster), different delivery mechanism. Worth
comparing directly, since the DRY property that motivated the original design matters
here too.

**What a Kustomize remote base gets you**
(`resources: [https://github.com/org/idp-cluster-baseline//base?ref=v1.2.3]`): no
packaging or publish step at all — any commit, tag, or branch is directly addressable —
and a patch can override *any* field in the rendered output, regardless of whether the
upstream base exposed a values-style hook for it. That flexibility is real, and Helm
doesn't fully match it: a Helm chart consumer is limited to whatever the chart author
exposed in `values.yaml`.

**What it costs — matching what you already ran into**: `kustomize build` resolving a
remote base does a fresh clone/fetch of the remote repo on every render, which is
genuinely slow at ArgoCD's reconciliation cadence, and gets worse as more Applications
reference it (every cluster's `argocd-platform`, on every poll/webhook cycle). There's a
second, structural problem beyond raw speed: ArgoCD's repo-server polls/webhooks the
*Application's own* `source.repoURL` — it has no visibility into a Kustomize base's
transitively-referenced remote repo, so a change to `idp-cluster-baseline` pinned by
branch (not tag) may not get picked up until ArgoCD's own periodic full cache refresh,
not immediately the way a direct repo change is. (I haven't verified this specific
behavior live against a real ArgoCD install — flagging it as "matches known ArgoCD/
Kustomize behavior, worth a quick live check before fully committing," the same way the
sync-hook-vs-Notifications question in `platform-cicd` got settled with a real test
rather than an assumption.)

**The reason to prefer Helm here isn't just speed**: this whole design already leans on
one specific Helm property as a structural safety mechanism —
`helm.valuesObject` (operator) beating `helm.valueFiles` (developer) for overlapping
keys, live-attack-tested in `platform-cicd`, and reused for §5 (app delivery). Kustomize's
patch model doesn't give you that guarantee for free — override-safety has to be reasoned
about per patch, per field, rather than coming structurally from the tool. Standardizing
on one common-config-plus-overlay mechanism for the whole platform (apps and clusters
both) is worth more than Kustomize's extra override flexibility, especially since that
flexibility gap has a cheap answer: give `idp-cluster-baseline`'s chart(s) a deliberate
escape-hatch value (an `extraManifests:`-style list — a common Helm-chart pattern) for
the rare case a cluster needs something the values schema doesn't expose, rather than
reaching for a second templating tool to solve it.

**Recommendation, confirmed**: Helm chart, git-source pinned tag, each cluster repo
supplies its own `values.yaml` override. If a real per-cluster need for
patch-arbitrary-field flexibility shows up in practice, a *local* (non-remote, no
network fetch, no staleness risk) Kustomize overlay inside that one cluster's own repo,
patching the already-rendered output, is a reasonable escape hatch — the problem with
the prior setup was specifically the *remote* fetch, not Kustomize as a tool.

### CI gates for cluster config: lint/syntax only, human review carries the real weight (resolved)

`idp-cluster-baseline` and `gitops-cluster-<name>` both get PR review as the actual gate,
plus lint/syntax CI — not a governance-check suite like `platform-cicd`'s release stage
(SAST/image-scan/policy-check/SBOM don't have an equivalent meaning for cluster config
the way they do for an app image). Concretely: `helm lint`, `yamllint`, `helm template`
rendered against each cluster's real values as a dry run (catches render errors before
merge, not just syntax), and `kubeconform`/`kubeval` against the rendered output for
schema validity. Required reviewers + branch protection on both repos, same as
everywhere else in this design — the review is the real gate, CI just keeps obviously-
broken YAML from reaching that review.

## 9. Credentials

Two rules carried forward, both already established preferences, not new ones:

- **No standing cross-cluster credential, ever** (§ "Guiding constraint" above).
- **Never-persisted beats cached-and-refreshed, even when refreshing would be simpler**
  ([[feedback_credential_persistence_preference]]) — `platform-cicd` deferred wiring ESO
  for its outcome-relay tokens specifically because the right home for that
  `ExternalSecret` is "whenever this app's k8s delivery becomes a proper Helm chart."
  `idp-service-catalog` (§1) *is* that chart. Wiring ESO-based relay-token distribution
  there, once the chart exists, closes that deferred item as a side effect of this work
  rather than a separately-scheduled task.

## 10. Lower vs. upper environments — a real security boundary, not just a naming split

**Built and live-verified 2026-08-16**, prompted by a real bug: a `NodeJSApplication`
(`nodejs-demo-app`) onboarded with `cicd.yaml` declaring
`deploy.lowerEnvironments: [dev]`, and nothing ever provisioned that namespace -
`ApplicationEnvironment` structurally refuses `kind-dev` targets by design (this
section's whole point), so `platform-cicd`'s chart-rendered `Role`/`RoleBinding` for
that env sat permanently `OutOfSync` (`namespaces "app-nodejs-demo-app-dev" not
found`). One real correction to how this section is phrased below: "`argocd-apps`...
gets a second `ApplicationSet`" reads as one platform-wide ApplicationSet - not
achievable, since a `git` `files` generator only ever has one static `repoURL`, and
each app's `platform/envs/` lives in a *different* repo. What's actually built is
**one `ApplicationSet` per app**, generated dynamically by
`gitops-cluster-dev/02-argocd-apps/tenant-appprojects`'s own chart (it already fires
once per app) alongside the second, narrower `AppProject` this section calls for -
same "render a resource ApplicationSet can't generate directly via an Application +
chart" trick already used for the per-app `AppProject`, extended one level further.
Live-verified: both new objects (`<app>-lower` `AppProject`,
`<app>-lower-envs` `ApplicationSet`) sync healthy per app; the `AppProject`
rejection boundary was proven with two throwaway `Application`s (one pointed at
`gitopsRepoUrl`, deliberately excluded from `sourceRepos`; one pointed at a
non-matching `destination.namespace`) - both hard-rejected with a clear
`InvalidSpecError`, not just shaped like the right YAML. **Caveat, not fully
tested**: the *cross-cluster* half of this boundary (pointing a lower-env
`Application` at a prod namespace) is currently structurally unreachable rather than
`AppProject`-tested - neither ArgoCD instance in this fleet has any other cluster
registered as a destination (`argocd.argoproj.io/secret-type: cluster`) today, so
there's nothing to actually attempt this against yet.

A real `platform/envs/dev.yaml` committed to `nodejs-demo-app`'s own repo (playing
the "developer self-serves" role this section describes) closed the original bug
end to end: `app-nodejs-demo-app-dev` got provisioned for real (`ServiceAccount` +
`NetworkPolicy`, `Synced`/`Healthy`), and the previously-stuck `Role`/`RoleBinding`
in that Application's *sibling* CICD-onboarding `Application` (a separate ArgoCD
project entirely, `platform-cicd`'s own `tenant-onboarding`) now has a real
namespace to sync into - confirmed by re-checking it directly, though by the time
this landed, an unrelated edit to that app's own `cicd.yaml` had already dropped
`dev` from `lowerEnvironments`, which independently cleared the original symptom
too. The mechanism itself was still verified for real, not inferred from that
coincidence: namespace creation, `AppProject`/`ApplicationSet` health, and the
rejection tests above all confirm it end to end.

Three real bugs found live getting the `platform/envs/*.yaml` → `idp-application`
plumbing working, none obvious from ArgoCD's docs alone:

1. **The git `files` generator's `path` param is a map, not a string.**
   `{{.path}}` alone (a first attempt at "the matched file's directory") renders Go's
   default map stringification (`map[basename:... filename:dev.yaml
   path:platform/envs segments:[platform envs]]`) into the `Application` spec, not a
   clean path - real, live `ComparisonError`. `{{.path.path}}` is the map's own
   directory-string field; concatenated with `{{.path.filename}}` (the bare matched
   filename) gives the real full path a `valueFiles` `$ref` needs
   (`$appsrc/{{.path.path}}/{{.path.filename}}`) - there's no single convenience
   field for the whole thing.
2. **`env:` collides with `idp-application`'s own reserved `env:` key.** The
   self-describing field this tier's own files use to name themselves (mirroring
   `<env>/identity.yaml`'s `env:` convention) can't be called `env:` here, because
   unlike `identity.yaml` this file *also* gets fed straight into
   `idp-application`'s Helm values as a `valueFiles` entry - where `env:` already
   means the chart's Embedded-tier container env-var list. A real
   `env: dev` produced a live `range can't iterate over dev` Helm error inside that
   chart's `workloadEnv` helper. `envName:` (matching the chart's own field name -
   its `values.yaml` already documents choosing that name for exactly this reason)
   is correct instead.
3. **The chart's own `rollout:` default isn't `null`.** Leaving `rollout:` out of
   `platform/envs/dev.yaml` entirely (intending the same "no image yet" bootstrap
   stub `ApplicationEnvironment` uses) let the chart's own default `rollout:` value
   (non-null, empty `image.repository`/`tag`) render a real `Rollout` with two
   `InvalidImageName` pods. `rollout: null` has to be explicit.

Your instinct here is right, and it's a direct extension of a boundary this doc already
draws for a different reason: `gitops-<app-name>` (§5) is the reviewed, release-gated
path to real users — it should carry **upper environments only**. Lower environments (a
persistent `dev`, plus short-lived ad hoc test envs) don't belong there; they're a
developer's own sandbox and shouldn't share a repo, a review bar, or an `AppProject` with
what actually reaches staging/prod.

### Where lower envs live: the app's own `/platform` folder, live-read

`platform-cicd`'s own design already anticipated this exact scenario — the tracked-copy
→ live-read redesign for `cicd.yaml` (`platform_cicd_session_argocd_onboarding`) was
explicitly justified by "a planned self-service folder in each app repo (developer-
authored Crossplane XRs/CRs) means ArgoCD needs broad app-repo read access anyway." This
is that folder doing exactly the job it was justified for. Concretely: `argocd-apps` on
the **dev cluster only** gets a second `ApplicationSet`, generating one `Application` per
app that live-reads `<app-name>/platform/envs/*.yaml` directly from the app's own src
repo — no gitops repo involved for this tier at all. A developer adds or edits a file
under `platform/envs/` and pushes; no PR-based release pipeline, no governance checks, no
`gitops-<app-name>` commit.

This isn't a new mechanism bolted on — it's the same shape as the PR-based ephemeral
environments already built and live in `platform-cicd`
(`docs/ephemeral-environments.md`: an ArgoCD ApplicationSet `pullRequest` generator that
already watches the **app's own repo's PRs directly**, not a gitops repo, to spin up a
preview namespace per open PR). That generator is a special case of this tier, not a
separate thing: a PR-triggered ephemeral env and a developer-defined ad hoc ephemeral env
both land in the same lower-env `AppProject` below — one triggered by a PR event, the
other by a direct commit to `platform/envs/`.

### The enforced boundary: a separate AppProject, not just a separate folder

The split has to be structural, the same way §6 makes upper-env scoping structural
rather than conventional. A **second `AppProject` per app**, scoped narrower on
`sourceRepos` but destined only for the dev cluster:

- `sourceRepos`: that app's own **src** repo (not `gitops-<app-name>`) + `idp-service-catalog`.
- `destinations`: **dev cluster only**, and only that app's own `-dev`/`-pr-<n>`/ad hoc
  ephemeral namespaces (naming convention unchanged — `platform-cicd`'s
  `docs/naming-conventions.md`) — never a staging/prod namespace, on any cluster, even
  by mistake. This is the one property worth stress-testing live once this is built, the
  same way the AppProject-rejection boundary was stress-tested in `platform-cicd`: try to
  point a lower-env `Application` at a prod namespace and confirm ArgoCD refuses it.

A developer committing straight to `platform/envs/` can create or modify their own
lower-env definitions freely — the safety property isn't "developers can't self-serve,"
it's "self-service is fenced to a namespace class that can structurally never be
mistaken for, or escalated into, a real environment."

### What this doc doesn't decide (service-catalog scope, goal 8)

Two follow-on questions surfaced by this split belong to the XRD/Composition design, not
this doc:

- Whether a lower-env Claim (e.g. "give me a database") should resolve to a cheaper/
  lighter Composition than the same Claim kind would in an upper env — likely yes, but
  that's a Composition-authoring decision, not a GitOps-topology one.
- TTL/cleanup for a developer-triggered ad hoc env that isn't tied to a PR's lifecycle.
  `platform-cicd` already has a marker for this class of problem
  (`platform.io/ephemeral-env`, currently swept only for PR-namespace TTL per
  `docs/naming-conventions.md`) — extending that same sweep to cover ad hoc envs looks
  like the natural fit, not a new mechanism, but isn't designed here.

## Forward-looking, not designed here

Two places this doc deliberately stops short, both flagged so the GitOps layer doesn't
paint them into a corner later:

- **Where Crossplane itself runs — resolved 2026-08-15**, see
  `service-catalog-design.md` §0 "Where Crossplane runs across a multi-cluster fleet"
  for the full reasoning. Neither hub-and-spoke nor uniform per-cluster: it's
  tier-dependent. Bootstrap-tier XRDs (`NodeJSApplication`/`ApplicationEnvironment`)
  centralize on one dev cluster permanently, regardless of fleet size, since they only
  ever write git commits (`provider-github`), never touch any cluster's K8s API.
  Attached-tier XRDs (`SLO` and friends) genuinely need `10-crds-operators/`'s original
  per-cluster assumption, since they compose native in-cluster resources directly — no
  meaning without Crossplane actually running where the resource lands. A new
  cluster-admin-owned cluster registry (a labeled `ConfigMap` per cluster, `type:
  dev|upper` + readiness flags) is what makes a per-app cluster choice
  (`NodeJSApplication.spec.devCluster`, `ApplicationEnvironment.spec.cluster`)
  checkable against real fleet state.
- **AI-triage hooks (goal 9) — partially resolved 2026-08-15.** The sync-hook → relay →
  CDEvents pattern proven for release outcomes (§7) remains the right structural fit,
  still not built. Separately, `service-catalog-design.md` §0 resolves *where* the
  existing `function-rollout-watcher`/HolmesGPT mechanism (already built,
  [[idp_session_phase2_holmesgpt]]) needs to run in a multi-cluster fleet — same
  per-cluster requirement as Attached-tier XRDs, plus a real redesign (extra-resources
  watch of an already-Helm-created Rollout, not same-XR composition) needed regardless
  of cluster count. Whether Holmes itself runs per-cluster or stays a shared instance
  with per-cluster-scoped credentials is flagged there as its own still-open follow-on.

## Open questions for discussion

**Resolved in review round 1 (2026-08-12)** — kept here as a changelog, not because
they're still open: label namespace stays `platform.io/*` for the whole IDP; ArgoCD
instance names (`argocd-platform`/`argocd-apps`) approved; cluster-config repo shape is
per-cluster, not a fleet monorepo (§1); `idp-cluster-baseline` is a Helm chart, not a
Kustomize remote base (§8); CI gates for cluster config are lint/syntax + human review,
not a governance suite (§8); `gitops-<app-name>` carries upper environments only, with
lower/ephemeral envs moved to a separately-scoped path (§10); tenants repo is
per-cluster, not fleet-wide (§1).

**Still open:**

1. Lower-env Composition tiering and ad hoc-env TTL sweep (§10) — parked under
   "Forward-looking," belongs to the service-catalog design (goal 8), not this doc.

All structural questions from the first draft are now resolved. Next up: the service
catalog / Crossplane XRD design (goal 8) is the natural next piece — see README.md's
status section.
