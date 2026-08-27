# Backstage design (upstream, kind-man)

**Status: DRAFT — plan, not yet built. Revised once already** (first pass targeted
Red Hat Developer Hub; corrected to plain upstream Backstage after the user flagged
RHDH isn't a no-cost path — see "Dynamic plugins, revisited" below for what survived
that correction). Answers where Backstage runs, how it reaches `kind-dev`/`kind-prod`,
how the plugin set actually gets built and delivered, and what changes in
`gitops-cluster-template`. Two decisions below were made explicitly with the user
rather than assumed — flagged as such, since both revise or extend prior design in
`service-catalog-design.md` §0.

## Decisions made this round

1. **§0's "zero Kubernetes credentials of any kind" is revised to "zero *write*
   credentials."** Backstage gets a **read-only** ServiceAccount token per cluster
   (`kind-dev`, `kind-prod`, and its own `kind-man`) so live-status plugins (Kubernetes,
   ArgoCD, pod logs) work. Every *mutation* still goes through the existing GitOps-commit
   path — `token-review-interceptor`'s `/github-installation-token` endpoint for
   scaffolder actions, exactly as §0 already designed. This is a scoped exception, not a
   reversal: no cluster still ever holds another cluster's *write* credential.
2. **Revised from the first pass: upstream Backstage, not Red Hat Developer Hub.**
   RHDH is a licensed Red Hat product (the polished pre-packaged-plugin catalog isn't a
   no-cost path) — user's call, correcting the first draft's assumption. Plan now
   targets a self-built upstream Backstage app (`@backstage/create-app`), with plugins
   added one at a time by hand rather than pulled from RHDH's curated catalog. See
   "Dynamic plugins, revisited" below for what's actually still free/upstream from the
   dynamic-loading idea, and what isn't.

## Dynamic plugins, revisited

Checked what's actually free/upstream vs. RHDH-only before dropping the idea entirely
(don't guess licensing/tooling boundaries, verify — same discipline this project
already applies to chart configs):

- **Backend dynamic plugin loading is real, upstream, and free** —
  `@backstage/backend-dynamic-feature-service` merged into Backstage core itself (the
  RFC/BEP for dynamic *backend* plugins is implemented and merged), no RHDH needed. It
  scans a configured directory for pre-built plugin bundles and loads them at startup
  without a Backstage rebuild.
- **Frontend dynamic plugin loading is NOT the same story** — BEP-0002 (dynamic
  *frontend* plugins, the Module-Federation/Scalprum mechanism RHDH's UI plugin catalog
  actually depends on) was still maturing/under review upstream as of the last check.
  Most of what people mean by "install a plugin" is the frontend half, so this is the
  piece that doesn't get a turnkey free win the way the first draft assumed.
- **Net effect on the plan**: default to the standard, well-trodden model — plugins
  (frontend and backend) added as npm dependencies directly into a self-built
  `packages/app`/`packages/backend`, compiled into one custom Backstage image, redeployed
  on each plugin-set change. This is normal Backstage operation, not a workaround, and
  matches "add all the plugins manually." Revisit frontend dynamic loading as a later
  optimization once its upstream status is confirmed current at implementation time —
  don't build against it now on the strength of an August-2026 web search alone.

## Where it runs

`kind-man` — already scaffolded from `gitops-cluster-template` and, it turns out,
already bootstrapped live (ArgoCD, cert-manager, Crossplane, External Secrets,
Infisical remote-consumer, observability stack, Contour, Sloth all present as of this
session's implementation start — the design doc's earlier "not yet live" note was
based on a transient podman VM wedge, not an actual unbootstrapped cluster; see
implementation log below). Still registered in the cluster registry as
`cicdReady: false`/`crossplaneReady: false` pending live verification of those.
Backstage
is a **singleton platform component**, same category as Infisical (`infisicalHost`) or
platform-cicd's control plane (`platformCicd`) — one instance for the whole fleet, not
something every cluster runs. `kind-man`'s `type: upper` doesn't conflict with either
existing invariant the template enforces (`providerGithub`/`platformCicd` must be
false on `upper`) — Backstage is neither.

## Directory layout — new `60-backstage/` tier

Follows the existing numbered-tier convention
(`00-bootstrap` → `01-argocd-platform` → `02-argocd-apps` → `10-crds-operators` →
`20-service-catalog` → `30-policy` → `40-observability` → `50-platform-cicd`):

```
gitops-cluster-template/60-backstage/
  README.md
  backstage/
    application.yaml          # ArgoCD Application - Deployment/Service for our own
                               # custom-built image (ghcr.io/jfillman/backstage),
                               # not a published Backstage Helm chart (no single
                               # official, strongly-maintained one exists - verify
                               # at implementation time rather than assume; several
                               # self-hosted installs just hand-roll manifests)
  postgres/                   # bundled, matching the infisical-standalone precedent
    application.yaml
```

Backstage source itself (the `@backstage/create-app` project, `packages/app` +
`packages/backend`, plugin dependencies added by hand) is a **new source repo**, not
part of `gitops-cluster-template` — that template only carries the deploy-time
manifests, same split every other component here already follows (e.g.
`function-rollout-watcher`'s own repo vs. its `functions.yaml` reference in
`10-crds-operators/crossplane/`).

Add a `components.backstage` toggle to `cluster.yaml.example`'s schema (same pattern as
`components.secrets.infisicalHost` / `components.platformCicd`) and to
`hack/customize-cluster.sh` — `false` by default, deletes `60-backstage/` when unset,
`true` only ever set on `kind-man`. No new hard invariant needed (Backstage isn't
mutually exclusive with anything `type: upper` already forbids), just a normal optional
directory like `contour`/`sloth`.

## Cross-cluster reachability — reuse a known-fragile pattern, flag it up front

Reaching `kind-dev`'s and `kind-prod`'s API servers from `kind-man` (separate
podman-network containers, no shared cluster network) is the same class of problem
already solved once for Infisical: expose via **NodePort on the API server's own host
podman IP**. That existing solution has broken **three times already**
([[platform_cicd_infisical_hardcoded_ip_todo]]) because the podman-network IP isn't
stable across a cluster restart/rebuild and drifts non-monotonically. Adding a second
and third consumer of the same fragile-IP pattern (Backstage → kind-dev,
Backstage → kind-prod) makes this worse, not better, unless the structural fix lands
first or alongside.

**Recommendation: do the structural fix (or at minimum a live-verified lookup, not a
hardcoded literal) before wiring Backstage's Kubernetes/ArgoCD plugins to real
endpoints** — either a cluster-registry-driven lookup (same `ExtraResources` mechanism
`infisicalHost` already uses) or fronting each cluster's API server with something
host-network-stable. Wiring three more hardcoded IPs into RHDH's `valuesObject` now is
building on the exact debt already flagged as "worth prioritizing." If the user wants
to proceed without the structural fix first, the plan still works — it just inherits
the same "re-verify the IP against the live cluster, don't trust the committed value"
discipline every existing consumer already needs.

## Credentials

- **Read-only K8s ServiceAccount token per cluster** (`kind-dev`, `kind-prod`,
  `kind-man`) — `get`/`list`/`watch` on the resource kinds each plugin actually reads
  (Pods, Deployments, Rollouts, PipelineRuns, ...), no write verbs, no `secrets` read.
  Delivered to `kind-man` via ESO `ExternalSecret`s pointing at Infisical, same as every
  other cross-cluster credential in this platform — never pasted to an assistant, never
  committed in the clear.
- **ArgoCD**: each cluster runs its own ArgoCD instance (self-managing, per
  [[idp_session_gitops_strategy]]) — Backstage's ArgoCD plugin needs one **read-only
  API token per instance** (kind-dev's ArgoCD, kind-prod's ArgoCD), not one shared
  credential.
- **GitHub**: reuse the existing GitHub App used for `provider-github`/repo creds as
  Backstage's sign-in + catalog-discovery identity — add Backstage's OAuth callback URL
  to that App's config rather than standing up a second App. Confirm at implementation
  time whether its current permission scope (repo contents, already used for XR/catalog
  commits) is sufficient for catalog discovery read, or needs a scope addition.
- **Writes stay git-only**: scaffolder actions (new `NodeJSApplication`,
  `ApplicationEnvironment`, Attached-tier components) call
  `token-review-interceptor`'s `/github-installation-token` endpoint exactly as §0
  designed, authenticated by a K8s ServiceAccount token that carries **no** resource-write
  RBAC — unchanged by this plan.
- **Grafana**: read-only API key/service-account token for kind-man's own
  `kube-prometheus-stack-grafana` (plugin #9 below) — new, not previously listed.

## Data stores

- **Postgres**: RHDH needs one. Follow the `infisical-standalone` precedent — a
  bundled Postgres (Bitnami-style subchart or RHDH's own optional dependency,
  whichever the actual chart ships) rather than waiting on the not-yet-built shared
  `Database` XRD (`service-catalog-design.md` Item 7). Single replica, kind-scoped,
  same "generated password, accepted plaintext-in-git tradeoff, flagged not hidden"
  precedent Infisical's own `application.yaml` documents, unless RHDH's chart supports
  `existingSecret` cleanly (verify — don't assume it does or doesn't without checking
  the actual chart, same trap Infisical's own header called out for its own chart).
- **TechDocs storage**: reuse `kind-man`'s existing MinIO instance
  (`40-observability/minio/`, already running for Thanos/Loki/Tempo) — add a fourth
  `techdocs` bucket rather than standing up separate object storage.

## Catalog ingestion

**Revised — two real candidate sources, not one, now that the actual plugin list names
both:**

- **GitHub org discovery** of `catalog-info.yaml` across tenant repos (`checkout-api`,
  `order-api`, `search-api`, `process-api`, ...) — hand-authored, developer-owned,
  standard Backstage pattern. Not yet built.
- **Kubernetes Ingestor** — **built, live, 2026-08-27** (Phase 1: `kind-man` only).
  Correction from the first pass: the real, current package is TeraSky-originated
  (`@terasky/backstage-plugin-kubernetes-ingestor` +
  `@terasky/backstage-plugin-scaffolder-backend-module-terasky-utils`), not
  `backstage-community/plugin-kubernetes-ingestor` — confirmed against the plugin's own
  source at implementation time, per this doc's own "verify exact name" flag on every
  plugin-table row. Generates `Component`/`API` catalog entities and scaffolder
  Templates directly from `idp-service-catalog`'s live XRDs on `kind-man` (which already
  runs the real service catalog, same as `kind-dev`/`kind-prod`) — no cross-cluster
  credential work needed for this first slice, since Backstage reads its own cluster's
  API server via a dedicated `backstage-ingestor` ServiceAccount (RBAC:
  `gitops-cluster-kind-man/60-backstage/backstage/rbac.yaml`) using the kubelet-
  projected, auto-rotating token — no ESO/Infisical hop, no long-lived Secret.
  Only the 5 "Bootstrap-tier" XRDs a developer actually creates directly
  (`NodeJSApplication`/`SpringBootApplication`/`PythonApplication`/`GoApplication`/
  `ApplicationEnvironment`) are annotated `terasky.backstage.io/add-to-catalog: "true"`
  and get scaffolder Templates that PR into `gitops-cluster-dev-tenants`'s
  `tenants/<app>/xr-requests/` (the real `xr-requests` mechanism, see
  `service-catalog-design.md` §0) — the other 4 (`TektonCICD`/`SecretStore`/`SLO`/
  `RolloutWatch`) are auto-derived by other XRDs' Compositions, so they're left
  unannotated (no template) but still surface as `Component` entities from their live
  instances, since that ingestion path isn't gated by the annotation. Extending to
  `kind-dev`/`kind-prod` is a deferred Phase 2, still gated on the cross-cluster
  NodePort/podman-IP reachability problem ([[platform_cicd_infisical_hardcoded_ip_todo]])
  exactly as this doc originally flagged.
- **Crossplane plugin** (`backstage-community/plugin-crossplane`) — shows live
  XR/Claim status and its own resource graph on a catalog entity page. This is a real,
  partial answer to `service-catalog-design.md` Goal 8 ("service catalog generated
  from Crossplane's CRDs") and to the `dependsOn`/`dependencyOf`-from-`componentRef`
  gap that doc flagged as "still not built" — worth re-checking that doc's own status
  note once this plugin is actually integrated, it may close the gap rather than just
  narrow it. Still not built (separate from Kubernetes Ingestor above, despite the
  similar name — see that plugin's own docs on the relationship).

## Plugin set

User-provided list (real target list, replacing the first draft's generic
placeholder), each landing as an npm dependency wired into `packages/app`/
`packages/backend`, committed to the new Backstage source repo, image rebuilt — no
shortcut around that with RHDH off the table. "There will likely be more" — this list
is a first slice, not final.

| # | Plugin | Package (verify exact name at implementation time) | New credential needed? |
|---|---|---|---|
| 1 | ArgoCD | `backstage-community`/`@roadiehq` ArgoCD plugin | read-only API token per ArgoCD instance (kind-dev, kind-prod) |
| 2 | Kubernetes topology | `@backstage/plugin-kubernetes` + `@backstage-community/plugin-topology` | read-only K8s creds per cluster (already decided) |
| 3 | GitHub pull requests | official `@backstage/plugin-github-pull-requests-board`-family | existing GitHub App (below) |
| 4 | GitHub Actions | official `@backstage/plugin-github-actions` | existing GitHub App — low first-pass value here (platform-cicd/Tekton is this fleet's real CI, not GH Actions; keep it, but don't prioritize) |
| 5 | Crossplane | `backstage-community/plugin-crossplane` | read-only K8s creds per cluster (already decided) — see Catalog ingestion above |
| 6 | Tekton pipelines | `backstage-community/plugin-tekton` | read-only K8s creds on kind-dev (where platform-cicd's control plane runs) |
| 7 | GitOps Manifest Updater | RHDH-originated scaffolder plugin, upstream availability TBD — **verify it isn't RHDH-only before committing to it**, this is exactly the mistake the RHDH-vs-upstream correction was about | none new — if it works upstream, it authenticates through the same `token-review-interceptor` GitHub token, not a standing credential |
| 8 | Kubernetes Ingestor | **DONE 2026-08-27** (Phase 1, `kind-man` only) — `@terasky/backstage-plugin-kubernetes-ingestor` (not `backstage-community/...`, corrected at implementation time) | read-only K8s creds per cluster — `kind-man` done via in-cluster ServiceAccount, `kind-dev`/`kind-prod` still deferred — see Catalog ingestion above |
| 9 | Grafana | `backstage-community/plugin-grafana` | **new**: Grafana read-only API key/service account token, against kind-man's own `kube-prometheus-stack-grafana` |

**Flag on #7 specifically**: "GitOps Manifest Updater" is a Red Hat/Janus-IDP-originated
scaffolder action for committing manifest changes as part of a template run — check at
implementation time whether it's published as a plain npm package usable outside RHDH,
or whether it's bundled only as an RHDH dynamic-plugin artifact. If the latter, the
existing plan (hand-write scaffolder actions calling `token-review-interceptor`,
per §0) is the fallback, not a blocker — just don't assume #7 is free to use the way
#1-6/#8/#9 are without checking.

Given "a large number of plugins" is the stated goal, worth sizing this explicitly
once phase 2 (below) is done: each plugin here is roughly a half-day-to-multi-day
integration+rebuild+verify cycle, not a values-file line.

## Image build — new platform-cicd surface

Dropping RHDH means Backstage needs its own container image, rebuilt whenever the
plugin set or app code changes. Natural fit: onboard the new Backstage source repo
onto `platform-cicd` as an `appType: infra` app (same category `naming-conventions.md`
already defines for "a shared/platform-adjacent service onboarded with its own
pipeline") — the IDP's own CI/CD platform builds the IDP's own portal, same as any
other tenant. Image lands in `ghcr.io/jfillman/backstage`, matching the
`registry.owner` convention `cluster.yaml.example` already templates. This is real new
scope versus the RHDH draft (which needed zero image-build work) — worth being
explicit that it's now part of the cost of this decision.

## Rollout phases

1. Bootstrap `kind-man` for real (it's registered but not live) — Calico, ArgoCD,
   root app-of-apps, same sequence every other cluster in the fleet already followed.
2. Stand up the Backstage source repo (`@backstage/create-app`, core plugins only:
   Catalog/Scaffolder/TechDocs/Search) and onboard it onto `platform-cicd` as an
   `appType: infra` app so it has a real build/publish pipeline from day one.
3. Land `60-backstage/` in `gitops-cluster-template` (+ the `components.backstage`
   toggle), instantiate on `gitops-cluster-kind-man` pointing at that image. Postgres +
   core plugins only — get one clean install healthy before adding cross-cluster
   surface area.
4. Wire GitHub auth + catalog discovery against real tenant repos.
5. Address cross-cluster reachability structurally (or explicitly accept the
   known-fragile NodePort pattern with live-verification discipline), then add
   read-only creds for `kind-dev`/`kind-prod` and integrate the Kubernetes + ArgoCD
   plugins (each its own commit + rebuild).
6. Layer in the rest of the plugin set incrementally, one integration+rebuild cycle
   at a time.

## Open questions

- Whether any published Backstage Helm chart is worth adopting vs. a hand-rolled
  Deployment/Service — verify against the real chart's current state at
  implementation time rather than assume either way.
- Postgres delivery: bundled subchart (Infisical precedent) vs. plain upstream
  `postgres` image + our own manifests, now that there's no RHDH chart pulling one in
  as a dependency by default.
- Whether the existing GitHub App's permission scope covers Backstage's needs as-is.
- Current upstream status of BEP-0002 (frontend dynamic plugins) — worth a fresh check
  before phase 6, not assumed unavailable forever.
- Whether to do the cluster-registry-driven reachability fix now (blocking phase 5) or
  defer it again — recommend not deferring a fourth time, but it's the user's call.
