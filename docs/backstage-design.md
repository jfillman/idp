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

**RESOLVED, differently, 2026-09-04** — RHDH is off the table (see "Decisions made
this round" above) so there's no `valuesObject` to wire IPs into any more; the actual
consumer became `app-config.yaml`'s `kubernetes.clusterLocatorMethods` +
`argocd.appLocatorMethods`. Neither "cluster-registry-driven lookup" nor
"host-network-stable fronting" panned out as options once the kiac migration landed
(kiac has no static-IP feature at all - confirmed upstream, `idp/docs/
local-clusters.md` - so there's no stable per-cluster address a registry could even
record). What shipped instead: `app-config.yaml` holds stable hostnames
(`kube-apiserver.{dev,prod}.kiac.local`, `argocd-apps.{dev,prod}.kiac.local`) that
never need editing again, and `gitops-cluster-kind-man/60-backstage/backstage/
deployment.yaml`'s `hostAliases` is the ONE place that still needs a live IP
re-verified after a kiac-dev/kiac-prod restart - centralizing the staleness this
section worried about into a single, scriptable spot (`refresh-kiac-hosts.sh` now
rewrites it) rather than eliminating the underlying VM-IP-churn problem, which isn't
actually fixable at this layer.

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

### Component type taxonomy (`spec.type`) — done 2026-09-04

Kubernetes Ingestor's own fallback for a Crossplane XR's `Component.spec.type` is the
hardcoded literal `crossplane-xr` (confirmed by reading the installed plugin's own
source, `EntityProvider.cjs.js`: `annotations[component-type] || xr.workloadType ||
"crossplane-xr"`) whenever the XR carries no `terasky.backstage.io/component-type`
annotation. Left alone, every ingested XR — an actual deployed app and an
`ApplicationEnvironment` alike — lands in one undifferentiated bucket, losing a useful
catalog facet. Backstage's own convention treats `spec.type` as a small curated
vocabulary describing the software's role, not its provisioning mechanism, so this
catalog now sets it explicitly:

| Type | XRDs |
|---|---|
| `service` | `NodeJSApplication`, `PythonApplication`, `GoApplication`, `SpringBootApplication` — real deployable apps a developer onboards |
| `environment` | `ApplicationEnvironment` — a deploy target, not a deployable service |
| `platform` | `TektonCICD`, `SecretStore`, `SLO`, `RolloutWatch` — auto-derived, never hand-created by a developer |

**Mechanism, and why it isn't a Composition patch**: the obvious-looking approach —
have each XRD's own Composition stamp the annotation onto the XR it produces — doesn't
actually work. Verified against `function-go-templating`'s own source
(`crossplane-contrib/function-go-templating`, `fn.go`): outputting a document matching
the composite's own `apiVersion`/`kind` (the documented way to "patch the XR itself")
only merges the **`status`** field back onto the composite — metadata/annotations are
never read from it. So instead:

- **8 of the 9 XRDs** (everything above except `RolloutWatch`) get the annotation from
  a Kyverno `ClusterPolicy`
  (`gitops-cluster-dev/30-policy/kyverno-policies/backstage-component-type-annotations.yaml`),
  which mutates matching `catalog.idp.io` kinds on every Create/Update admission
  review. Needed a supplemental read-only `ClusterRole` in the same file (Kyverno ships
  RBAC for built-in kinds only — same gap already hit for `testworkflows.testkube.io`,
  see `testkube-rbac.yaml` in the same directory). Kyverno only runs on `kiac-dev`
  today, which is fine — these 8 XRDs only exist there.
- **`RolloutWatch`** is the one exception: its instances come from `idp-application`'s
  own Helm chart (`charts/idp-application/templates/attached/rolloutwatch.yaml`), not a
  GitOps `xr-requests` commit, and it's the only one of the 9 that also needs to work on
  `kiac-prod`, which has no Kyverno installed. Set directly in that chart template
  instead (`terasky.backstage.io/component-type: platform`), shipped as
  `idp-service-catalog@v0.3.52`.

No manual backfill of already-live XRs was needed: Crossplane's own Composition
reconcile loop updates every XR's `status` constantly, and each of those updates is
itself an admission event Kyverno's mutate rule fires on — every pre-existing XR on
`kiac-dev` picked up the annotation within seconds of the policy going `Ready`, live-
verified across all 8 kinds (`NodeJSApplication`/`PythonApplication`/`GoApplication`/
`SpringBootApplication`/`ApplicationEnvironment`/`TektonCICD`/`SecretStore`/`SLO`).

## Plugin set

User-provided list (real target list, replacing the first draft's generic
placeholder), each landing as an npm dependency wired into `packages/app`/
`packages/backend`, committed to the new Backstage source repo, image rebuilt — no
shortcut around that with RHDH off the table. "There will likely be more" — this list
is a first slice, not final.

| # | Plugin | Package (verify exact name at implementation time) | New credential needed? |
|---|---|---|---|
| 1 | ArgoCD | **Code done 2026-09-04** (`@backstage-community/plugin-redhat-argocd` + `-backend` - switched from `@roadiehq/backstage-plugin-argo-cd` the same day, user's call, wanting the fuller read feature set: multi-app-per-entity, multi-instance display, Argo Rollouts visualization) | read-only API token per ArgoCD instance - **kiac-dev's and kiac-prod's `argocd-apps` instance only**, not `argocd` (platform) or kiac-man's own two - see below |
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

### Plugin #1 (ArgoCD) — code + GitOps done 2026-09-03/04, image not yet rebuilt

**Switched plugins 2026-09-04, same day as the section below** - started with
`@roadiehq/backstage-plugin-argo-cd(-backend)`, then the user asked for the fuller
read feature set once they saw the basic card live: multi-app-per-entity via
`argocd/app-selector`, multi-instance display via a comma-separated
`argocd/instance-name`, and Argo Rollouts visualization. Landed on
`@backstage-community/plugin-redhat-argocd(-backend)` for that - confirmed by
reading its `router.cjs.js` directly that it's entirely read-only (4 `GET` routes,
one Backstage permission `argocd.view.read`, no sync/rollback/delete anywhere) -
"full feature set minus some write options" turned out to need zero permission-
policy work, since this plugin never had write options to begin with. Same
`argocd.appLocatorMethods` config shape as Roadie's, so the credentials/
reachability work below (done for Roadie's plugin first) carried over unchanged.

**One real integration wrinkle Roadie's plugin didn't have**: this plugin hasn't
migrated to Backstage's new frontend system - its `/alpha` export is only
translation refs, not a `FrontendPlugin` (confirmed by reading `alpha.esm.js`
directly, not assumed from Roadie's own `/alpha` precedent). Bridged by hand in a
new `packages/app/src/modules/argocd/index.tsx`, using `core-compat-api`'s
`compatWrapper` around the plugin's two real components
(`ArgocdDeploymentSummary`, `ArgocdDeploymentLifecycle`) - same bridging pattern
this app already used for its sign-in page and theme overrides, just applied to a
genuinely new plugin surface instead of an `app`-pluginId override. Filtered on
`isArgocdConfigured` rather than a blanket `kind:component` (Roadie's own
default): this plugin's components return `JSX.Element | null` and silently
render nothing when unconfigured, with no `MissingAnnotationEmptyState`
placeholder the way Roadie's cards had - mounting them everywhere would leave a
bare layout gap instead.

**Argo Rollouts visualization** needed two more additions beyond what Roadie's
plugin needed: `kubernetes.customResources` in `app-config.yaml` (the `rollouts`/
`analysisruns` CRD kinds) and a new explicit `backstage-argo-rollouts-viewer`
ClusterRole + binding on kiac-dev/kiac-prod's own `backstage-ingestor-rbac`
(`view` doesn't cover `argoproj.io` CRDs, same reasoning as the existing CRD-viewer
and `crossplane-browse` grants) - not needed on kiac-man, which runs no Rollouts.

Also enabled `argocd.fullDeploymentHistory: true` (off/deduped by this plugin's
own default) per "full feature set."

**Scoped to `argocd-apps` only, kiac-dev + kiac-prod only** (user's explicit call,
not the default the plugin table above originally implied) - `argocd-apps` is the
instance that actually deploys each tenant's `Application` (gitops-strategy.md §2),
matching catalog Components 1:1; the `argocd` platform instance and kiac-man's own
two instances have no catalog entity to attach a card to.

**No per-entity annotation work needed** - `kubernetes-ingestor`'s `argoIntegration`
config defaults to `true` and already emits the exact `argocd/app-name` annotation
this plugin reads (confirmed by grepping the installed package's own compiled
source, not assumed from either plugin's docs), onto every entity it generates from
a resource owned by an ArgoCD Application. Once `kubernetesIngestor` catalog
ingestion is live against kiac-dev/kiac-prod (Phase 5, see "Rollout phases" below -
worth re-confirming this is actually still working post-kiac-migration, given the
IP-churn note next), the ArgoCD cards populate automatically.

**Reachability fixed via Gateway hostnames, not raw IPs** (user's explicit call,
over matching the existing `kubernetes.clusterLocatorMethods` fragile-IP pattern) -
`app-config.yaml`'s `argocd.appLocatorMethods` instance URLs are the stable
`argocd-apps.{dev,prod}.kiac.local` hostnames and never need editing again; only
`gitops-cluster-kind-man/60-backstage/backstage/deployment.yaml`'s `hostAliases`
(the Backstage pod's own DNS resolution for those two hostnames) needs re-pointing
at the clusters' current VM IPs after a kiac-dev/kiac-prod restart - same
live-reverify discipline as everything else kiac's no-static-IP limitation
touches (`idp/docs/local-clusters.md`). `refresh-kiac-hosts.sh` now also rewrites
`hostAliases` in the local `gitops-cluster-kind-man` checkout when it heals
`/etc/hosts` - one command, one source of truth (`container list`) for both. It
deliberately does NOT `kubectl patch` the live Deployment: that Application runs
`selfHeal: true`, so a live patch would just get reverted on ArgoCD's next
reconcile (confirmed by reading that Application's own sync policy) - the durable
fix has to go through git, same as everything else this platform manages. The
script stops at rewriting the file; committing/pushing is still a manual step.

**Credentials**: a dedicated `backstage` ArgoCD account (`apiKey` capability only,
bound to the built-in `role:readonly`) added to each cluster's `argocd-apps-install/
application.yaml` (`configs.cm`/`configs.rbac`). Token itself is manual-by-design,
same posture as every other credential here - see `gitops-cluster-kind-man/
60-backstage/backstage/argocd-{dev,prod}-apps-token-external-secret.yaml`'s own
header comments for the exact `argocd login`/`account generate-token` steps and
which Infisical key each one plants into.

**Not yet done**: the running Backstage image (`1.0.1-1e3c7fa`) predates this code -
needs a real CI build (push to the backstage repo) and `deployment.yaml`'s image tag
bumped before the plugin is actually live, plus the three manual steps (two ArgoCD
account tokens, `hostAliases` already has current-as-of-2026-09-03 IPs baked in but
should be re-verified live at build/deploy time).

### Two real bugs found live once the plugin actually rendered, 2026-09-04

Neither was config - both needed a code fix in the `backstage` repo:

1. **Routing crash on the ArgoCD tab** (`NotImplementedError`-adjacent: "Routable
   extension component... was not discovered in the app element tree"). Root cause:
   `ArgocdDeploymentSummary`/`ArgocdDeploymentLifecycle` are legacy
   `createRoutableExtension` components bound to the plugin's own absolute
   `rootRouteRef` - the new frontend system builds its route table by statically
   scanning the declared extension tree, so a routable extension hidden behind
   `compatWrapper` + a lazy loader needs that legacy routeRef explicitly re-bound via
   `routeRef: convertLegacyRouteRef(argocdPlugin.routes.root)` on the hosting
   `EntityContentBlueprint`. Fixed, then immediately hit...
2. **`NotImplementedError: No implementation available for
   apiRef{plugin.argo.cd.service}`**. The ArgoCD API client's `ApiFactory` is declared
   on the legacy `argocdPlugin` object's own `createPlugin({apis: [...]})` call, not on
   either component - `createFrontendPlugin` with just the two hand-picked extensions
   never registered it. Fixed by switching to `convertLegacyPlugin(argocdPlugin, {
   extensions: [...] })`, which bridges the legacy plugin's `apis`/`pluginId` while
   still using this module's own hand-built extensions.

### A third, bigger bug: `argocd/app-name` was wrong, and single-app anyway

User tested with real data (checkout-api: 3 Applications on kiac-dev, 4 on
kiac-prod, all legitimately part of the same app) and only 2 showed - one per
cluster. Traced live via a direct query against the catalog's own postgres DB
(`backstage_plugin_catalog.final_entities`, read through the pod's own
`POSTGRES_PASSWORD_FILE` so no credential was ever seen or transmitted): the
`checkout-api` entity's `argocd/app-name` annotation was `checkout-api-xr-requests`
- the Bootstrap-tier onboarding app, not any of the real workload Applications. That
exact app name happens to exist on both clusters, which is exactly "2 apps, one per
cluster."

Root cause, confirmed by reading `kubernetes-ingestor`'s `EntityProvider.cjs.js`
directly: it ingests **two different Kubernetes resources into the same catalog
entity ref** - the workload `Rollout` (tracked by `<app>-dev`/`-prod`) and the
`NodeJSApplication` XR claim (tracked by `<app>-xr-requests`) both become
`component:default/checkout-api`, and whichever resource this ingestion pass
processes last silently overwrites the other's annotation. Even the "correct" single
app-name would still only show one Application per cluster - `extractArgoAppName()`
is hardcoded to emit `argocd/app-name`, never the multi-app `argocd/app-selector`,
and there's no config knob for it.

**Fixed via a yarn patch** (`.yarn/patches/@terasky-backstage-plugin-kubernetes-
ingestor-*.patch`, same mechanism as the existing `terasky-utils` patch) - confirmed
first that `platform.io/app=<name>` is a label idp-application's chart applies
consistently to every workload AND every associated ArgoCD Application (checked live
across checkout-api, order-api, search-api). `extractArgoAppName()` now derives that
same `<name>` from the last path segment of ArgoCD's own `tracking-id` annotation
(format `<app-name>:<group>/<kind>:<namespace>/<resource-name>` - `<resource-name>`
is identical across every resource kind for a given app) and emits `argocd/app-
selector: platform.io/app=<resource-name>` instead. This is a **fleet-wide behavior
change** (user's explicit choice over a narrower "just fix the collision" patch) -
every catalog entity now links via label-selector instead of single app-name, so
every related Application shows, and the overwrite race is gone since every resource
kind for the same app now computes the identical selector.

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
