# Service catalog design (Crossplane XRDs)

**Status: DRAFT — first pass, for discussion.** Goal 8 from the project's core goals:
the service catalog is what a Backstage-driven Crossplane plugin turns into templates
(per [[project_dream_idp]]'s memory — "the Backstage service catalog is *generated
from* Crossplane's CRDs, not hand-authored separately"). This doc also resolves the
one thing `docs/gitops-strategy.md` deliberately deferred to this design: where
Crossplane actually runs.

**Revised same session**: dependency-ordering questions (can an env exist without an
app, does a ConfigMap need a link back to one) exposed a real gap in the first pass's
Attached-tier mechanism — fixed by routing Attached-tier resources through one shared
chart (§3) instead of separately-committed files, which resolves those questions
structurally rather than by validation. Components (Redis, OAuth servers, ...) and a
scoped-down look at a secret vault were added the same round. **Crossplane v2 confirmed**
as the target — Claim/XR terminology from the first pass updated throughout; see
Terminology section for what that actually changes, not just renames.

**Revised again same session**: the rendering mechanism for every list-shaped
`values.yaml` field (§3) is now explicit — generic `range`-loop templates, not Helm
sub-charts (a real mechanical limitation, not a style call), fixing an actual gap in
how multiple ConfigMaps would've worked. **Argo Rollouts confirmed as the default
deployment resource** — `rollout:` replaces `image`/Deployment in §3's schema, and
`AnalysisTemplate` joins the Embedded tier (app-specific) alongside a new
platform-curated `ClusterAnalysisTemplate` library (cluster config, not the service
catalog) — same curated-default-plus-escape-hatch pattern already used for Components
and `idp-cluster-baseline`.

**Revised a third time same session**: confirmed Backstage holds **zero Kubernetes
credentials at all** — even the one remaining live API call from the previous round
(Bootstrap-tier XR creation) is now a GitOps commit, closing the last exception §0 had
carved out. `Database`/`Queue` joined the catalog; secrets settled on a self-hosted
Infisical backend (new `SecretStore` XRD, item 8); ArgoCD's `Rollout` health check
verified for real, with a concrete caveat. See "Open questions" for the full changelog.

**Revised once more**: `SecretStore` scope narrowed to one per **(app, cluster)** —
shared across that app's envs on the same cluster, never across clusters — which also
caught and reverted a wrong call from the previous round (a namespaced `SecretStore`
doesn't survive cross-namespace sharing; back to `ClusterSecretStore`, namespace-scoped
via `spec.conditions`). See item 8.

**Revised 2026-08-13** (separate session, after Phase 2's first slice — see
[[idp_session_phase2_holmesgpt]] — was built and live-verified): three additions to
§3's schema, confirmed before implementation started. **`rollout.podSpec: {}`** — a raw
map, deep-merged (Sprig `mergeOverwrite`) onto the rendered `.spec.template.spec` after
every curated/generated field — is the actual "any pod-spec field" escape hatch this doc
had only gestured at before. Deliberately excludes `containers` itself (the curated
fields above it cover the main container; `extraContainers: []` covers sidecars) —
`mergeOverwrite` replaces whole arrays rather than merging by index, so letting this
escape hatch touch `containers` would silently clobber the chart's own rendered
container list instead of extending it. **`networkPolicy`** joins the Embedded tier,
default `enabled: true`: deny cross-namespace ingress except from the ingress controller
(only relevant when `ingress.enabled`), leave egress open by default — an ingress-only
default was a deliberate choice, not an oversight; defaulting egress closed too would
break DNS/external-API calls in a much more confusing way than ingress isolation does,
so egress-tightening is the escape hatch (`extraEgressRules`), not the default.
**`volumes` (PVC support)** joins the Embedded tier, using the exact same list-of-objects
+ generic range-loop pattern already established for `configMaps` — one PVC + one
`volumeMount` per entry, no new rendering mechanism needed. Chart implementation was
deferred to a later session at the time this paragraph was written; see the revision
note below — it's since been built.

**Revised 2026-08-13** (third session of the day): the `idp-application` chart
itself is now built (`idp-service-catalog/charts/idp-application`), `helm
lint`/`helm template` verified against three fixtures (minimal, full-featured
including `blueGreen`, and an `appType: infra` standalone-component release
with no workload). §3 below is unchanged as the schema source of truth: the
chart's own `values.yaml` documents every field from that schema plus a small
number of fields §3 left genuinely unspecified (an app-facing `secrets:`
entry's Infisical path selection, `configMaps:` mount paths, and similar) -
see `charts/idp-application/README.md` for the concrete list, not repeated
here since it's implementation detail, not design. One real naming collision
worth recording here rather than just in the chart README, since it's a trap
this doc's own schema shape invites: **`cluster`/env-identity fields needed
for `spec.environmentRef` (§ Framework) cannot be named `env`** - §3's schema
already uses the top-level key `env` for the Embedded-tier container env-var
list (`env: [{name: FOO, value: bar}]`); a same-named identity field silently
collides with it in one flat values map. Caught live by `helm lint` during
this build (`range .Values.env` failed with "can't iterate over dev") - named
`envName` instead. Two things stayed genuinely unresolved, not implementation
gaps but real open questions this pass didn't answer: the actual Crossplane
API group for `components:`/`slos:` XRs (none of those XRDs exist yet - the
chart uses a placeholder, `catalog.idp.io/v1alpha1`) and the platform's
default canary step sequence (§ "Still open" item 3, below - the chart renders
a deliberately inert single-step placeholder, not a real default).

**Revised 2026-08-13** (fourth session of the day, immediately after the above):
a deliberate v1 resource-coverage pass on the now-built chart, prompted by "what
else should be in v1 so we don't have to keep changing this chart" - **entirely
beyond §3's schema**, not a gap in it, so not folded into §3 itself; recorded here
only as a pointer, full detail in `charts/idp-application/README.md`. Added: a
dedicated ServiceAccount (the identity every pod now runs as, instead of the
namespace's implicit `default` - the one thing on this list that's genuinely hard
to retrofit once real deployments exist), `jobs:`/`cronJobs:` batch tasks sharing
the main workload's env/secrets/config automatically, a ServiceMonitor
(`kube-prometheus-stack` is already installed cluster-side), and a raw
`extraManifests:` escape hatch matching `idp-cluster-baseline`'s own pattern. One
more real bug caught live, general enough to be worth a line here too: **Sprig's
`default` function treats an explicit `false`/`0` exactly like "unset" and
silently substitutes the default anyway** - `hook: false` on a `jobs:` entry still
rendered as `hook: true` until fixed with an explicit `hasKey` check instead. Any
future schema field on this chart where the Go zero value is a legitimate,
meaningful setting (not just "not configured") needs the same treatment, not a
bare `| default`.

**Revised 2026-08-13** (fifth session of the day): code review of the built
chart surfaced three more real gaps in §3's `configMaps:`/`secrets:` schema
itself (not the resource-coverage pass above, and unlike that pass, folded
directly into §3's own schema block below, since these are genuine additions
to fields §3 already specifies, not new resource kinds outside it) - full
detail in `charts/idp-application/README.md`. `secrets:` could only ever become an env
var, never a mounted file; `configMaps:` could only ever be volume-mounted,
never `envFrom`'d; and `configMaps:` could only ever be chart-owned via
`data:`, with no way to reference one created outside this chart - the last
one a real, concrete need (a Kustomize `configMapGenerator`'s output). Both
lists gained an `as: env | volume | both` field; `configMaps:` gained
`existingConfigMap:` as a `data:` alternative. **Your call on the Kustomize
case specifically**: a fixed name (`disableNameSuffixHash: true`), not a
hash-suffixed one requiring external sync - the accepted tradeoff is that
ConfigMap loses Kustomize's own automatic-rollout-on-content-change property.
Closed a related, adjacent gap at the same time: `configMaps[].data`/secret
edits previously didn't change the Rollout's pod template at all (same
name/keys), so Argo Rollouts never started a new revision - fixed with
`checksum/configmaps`/`checksum/secrets` pod-template annotations, though the
secrets one only catches a *declaration* change, not a value rotated in
Infisical without touching `values.yaml` (a different, harder problem, not
solved here).

**Revised 2026-08-13** (sixth session of the day): `networkPolicy:`'s
`extraIngressRules`/`extraEgressRules` escape hatch works but requires knowing
the real K8s `NetworkPolicyPeer`/`NetworkPolicyPort` shape - not simple for the
dominant real case, "let this other namespace (optionally narrowed to some
pods) or this CIDR reach me on this port." `allowIngressFrom:`/`allowEgressTo:`
(folded directly into §3's schema block below, same reasoning as the
`configMaps:`/`secrets:` revision above - a genuine addition to a field §3
already specifies) cover that flatly: `{namespace, podLabels?, ports?}` or
`{cidr, ports?}`, ports as bare TCP port numbers. `namespace:` resolves via the
same `kubernetes.io/metadata.name`-label mechanism already used for
`ingressControllerNamespaceSelector`, not a new idiom. Both raw escape hatches
stay, unchanged, for UDP/SCTP or multiple ORed peers in one rule - full detail
in `charts/idp-application/README.md`.

## Terminology (Crossplane v2 primitives, for reference)

**Confirmed: this design targets Crossplane v2.** v2's real, load-bearing change from
v1: an XR can be **namespaced directly** — a developer creates the XR itself, in a real
namespace, with no separate cluster-scoped XR hidden behind a namespaced Claim proxy.
"Claim" language from the first draft (written before this was confirmed) is replaced
below — flagging here rather than silently, since it's a genuine simplification, not
just a rename: the Attached tier (§ Framework) no longer has a hidden indirection layer
to explain — the XR the chart renders *is* the real resource, sitting directly in the
app's own namespace.

- **XRD** (`CompositeResourceDefinition`) — defines a new custom API type: the schema
  for the **XR** (Composite Resource) a developer creates directly — namespaced by
  default in v2, cluster-scoped only where an XRD deliberately declares it (none of
  this catalog's XRDs need cluster scope; see below).
- **Composition** — the implementation: how an XR's spec becomes real managed resources
  (or other XRs). Multiple Compositions can implement one XRD, selected by label — this
  is how "same XRD, different backing resource per env tier" works, flagged as a real
  future need in §5 of `gitops-strategy.md` (a lower-env XR resolving to a cheaper
  Composition than the same XR kind in an upper env).
- **Composition Function** — a real code pipeline (Go, KCL, or a templating function)
  that computes a Composition's output, the modern replacement for pure declarative
  patch-and-transform. Needed here — see §2.
- **Provider** / **managed resource** — a Crossplane-managed integration with an
  external API (cloud provider, GitHub, a Helm release, another Kubernetes API) and the
  CRD-shaped resources it manages.

## §0. Where Crossplane runs, and how Backstage reaches it — with zero K8s write credentials

**Revised this round — Backstage never holds any Kubernetes credential, of any kind, on
any cluster.** The first pass had one exception (Backstage calling the dev cluster's K8s
API directly to create Bootstrap-tier XRs) — your call: no k8s write creds on Backstage
at all, every XR follows the GitOps pattern. This turns out to be a real simplification,
not just a constraint satisfied grudgingly — it removes the one special case §0 used to
carve out, and makes the answer to "does an Attached-tier XRD have to go through
Backstage-calls-K8s-directly, or can it be a template too" (your question 1) simply:
**everything is a git commit, so yes, absolutely** — see below.

**Crossplane still runs per-cluster** (confirms `gitops-strategy.md`'s tentative
default). Every XR, Bootstrap or Attached, is created the same fundamental way — commit
a manifest into a GitOps-synced location, let that cluster's own pull-based ArgoCD and
Crossplane do the rest — the only variable is *which* location:

1. **Bootstrap-tier** (`NodeJSApplication`, `SpringBootApplication`,
   `ApplicationEnvironment`): commit into `gitops-cluster-<cluster>-tenants/<app>/` —
   the same directory that already carries `identity.yaml` and already drives the
   tenant-onboarding `ApplicationSet`. A new `xr-requests/` subdirectory carries the XR
   manifest itself. **This closes the "namespace must pre-exist" wrinkle from the first
   pass for free**, rather than needing Backstage to make a separate namespace-creation
   call: the SAME per-app `Application` that already renders `app-<name>-cicd`'s
   namespace/RBAC/Triggers now *also* renders whatever's in `xr-requests/`, in one sync
   — a namespace at a lower `sync-wave` than the XR that lands inside it, both applied
   by ArgoCD in the same operation. For a brand-new app, Backstage's action is exactly
   one commit (`identity.yaml` + the first `xr-requests/` file, together) — there's no
   longer a live API call anywhere in this path, at any point in an app's life.
2. **Attached-tier** (`SLO`, `Redis`, `OAuthServer`, ...): unchanged from the first pass
   — a block in that env's own `values.yaml` in `gitops-<app-name>`, rendered by the
   `idp-application` chart. This tier never needed a K8s credential in the first place.

**What Backstage actually needs, then: a scoped GitHub write, not a Kubernetes one** —
and not even a new mechanism for that. `platform-cicd`'s `token-review-interceptor`
already mints per-repo-scoped GitHub installation tokens on demand
(`/github-installation-token`), TokenReview-authenticated, never persisting the GitHub
App's own private key outside `platform-system`. Backstage's scaffolder actions call
that *same* endpoint rather than holding a standing GitHub credential of its own —
directly the pattern [[feedback_credential_persistence_preference]] already established
("never saved anywhere" beats "cached/refreshed"), reused instead of re-solved.

**One nuance worth being precise about, so "zero K8s creds" is verifiably true and not
just asserted**: Backstage still needs *something* to authenticate to that endpoint —
a Kubernetes ServiceAccount token, checked via TokenReview, the same mechanism every
other caller of that endpoint already uses. This is not a contradiction of "no k8s write
creds": that SA token carries **no RBAC grants to create, patch, or delete any
Kubernetes resource** — it's an identity credential for one HTTP call, not a write
credential for the K8s API. Worth stating explicitly rather than eliding, since the
distinction is exactly the kind of thing worth getting precise rather than hand-waved.

This keeps the guiding constraint from `gitops-strategy.md` ("no cluster ever holds
credentials for another cluster's API") intact, and extends it one layer further than
the first pass did: now it's not just "no cross-cluster credential," it's "no live
write credential of any kind, anywhere, for anything" — every mutation, at every layer
of this whole design, is a reviewed or self-service git commit, synced by the cluster
that owns the result.

**Resolved: `app-<name>-cicd`, uniformly, for all three Bootstrap-tier XRDs — no
separate shared `platform-catalog` namespace.** Unchanged conclusion from the first
pass, reached by a cleaner mechanism now (above) instead of a two-call workaround.

**Does `-cicd` still fit, now that it also hosts provisioning objects, not just pipeline
execution? Recommend keeping the name — the boundary it draws didn't actually move.**
`naming-conventions.md` already defined `-cicd` as conceptually distinct from `dev`/
`staging`/`pr-<n>` before any of this: "the Application's own pipeline-execution
namespace," explicitly *not* a deploy target. That distinction was always really
*control-plane namespace* vs. *deploy-target namespace* — pipeline execution just
happened to be the only control-plane resident that existed yet. Crossplane's Bootstrap-
tier XRs are a second resident of the same role (control-plane objects that act *on
behalf of* the app, never receive live traffic), not a new category the existing
boundary has to stretch to cover. So the name is arguably always been a slight
under-description of its own role, not a description that's now wrong.

**Weighed against an actual rename**: `-cicd` is baked into a live, working,
two-real-tenant system — the `envNamespace` helper in `platform-cicd-app`, every
existing namespace, RBAC, broker CEL filters, docs. A rename means a real namespace
migration (Kubernetes can't rename a namespace in place), not a find-and-replace, for a
label-precision gain that doesn't change any actual behavior. Not worth it. If a broader
naming pass ever happens for independent reasons, "control" or "platform" would be a
more literal name for the role this namespace has always actually played — worth
remembering then, not a reason to act now.

### Where Crossplane runs across a multi-cluster fleet (resolved 2026-08-15)

Closes the doc's own opening claim ("resolves the one thing `gitops-strategy.md`
deliberately deferred... where Crossplane actually runs") for real, once a fleet with
more than one dev cluster and real upper-env clusters is on the table (a second, real
`kind-prod` cluster now exists for testing this). Reasoning worked through live in
conversation, not asserted — kept here so it isn't lost:

**The two XRD tiers have different locality requirements, and that difference is the
whole answer.** Bootstrap-tier (`NodeJSApplication`, `ApplicationEnvironment`) composes
*only* `provider-github` resources — every mutation is a GitHub API call, never a
Kubernetes API call to any cluster. Attached-tier (`SLO` today; `Redis`/`OAuthServer`/
`Database`/`Queue` once built) composes *native, in-cluster* resources directly (`SLO`'s
Composition renders a real `PrometheusServiceLevel` into whichever cluster it's
reconciled on) — that has no meaning unless Crossplane is actually running there.

- **Bootstrap-tier stays centralized on one dev cluster, permanently, regardless of
  fleet size.** There is nothing about creating a GitHub repo or committing a file that
  benefits from running per-cluster, and running N copies of the same Composition
  against N clusters would just create N controllers racing to own the same repo.
- **Attached-tier (and, see below, AI-triage) must run per-cluster** — on every cluster
  that hosts real app deployments, dev and upper-env alike. This was always implied by
  how `SLO` already works; it just had nothing to contradict it while only one cluster
  existed.

**A cluster registry is the missing piece that makes any of this checkable**, once
there's more than one cluster of either kind. A small, cluster-admin-authored,
PR-reviewed object per cluster — a labeled `ConfigMap` is enough, no new CRD needed
(matches the low-ceremony, operator-authored-data pattern this doc already uses for
`tenants/*/app.yaml`):

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: kind-prod          # cluster name
  namespace: crossplane-system
  labels: {platform.io/cluster-registry: "true"}
data:
  type: upper              # dev | upper
  cicdReady: "false"       # dev only - flips once that cluster's CICD control plane is live
  crossplaneReady: "false" # both types - flips once Crossplane + the Attached-tier
                            # catalog subset is installed and healthy there
```

Both readiness flags are **manual, PR-reviewed attestations**, not automated health
probes — same "CI gates are lint/syntax, human review carries the real weight"
philosophy `gitops-strategy.md` §8 already applies to cluster config generally, and
consistent with [[feedback_live_verification]]'s standing caution against trusting a
passing check without confirming the thing it's gating is actually on. An automated
probe could report *into* this as a second opinion later; it shouldn't be the sole gate.
Registering a cluster (adding its entry) and bringing it fully online (§4's bootstrap
sequence, extended to also install the Attached-tier catalog once `10-crds-operators`
includes Crossplane) stay the same cluster-admin action — the registry doesn't invent
new toil, it just makes the fact checkable.

**`NodeJSApplication` gains a required `devCluster` field**, validated via an
extra-resources lookup against this registry (must resolve to `type: dev` +
`cicdReady: "true"`, or the Composition creates nothing and reports a blocking
condition — same "structural backstop, refuse rather than partially succeed"
instinct already used for `WorkloadDeployed`/`CicdOnboarded`). **Immutable once set** —
a CEL transition rule (`self.devCluster == oldSelf.devCluster`, rejected after
creation), not a mutable field Crossplane would try to reconcile toward. `devCluster`
isn't just "which tenants repo `app.yaml` lands in" — it's which cluster's CICD control
plane owns the app's pipeline history/secrets/webhooks, and which cluster's own
lower-env (§10) `ApplicationSet` live-reads this app's `platform/envs/`. None of that is
something a declarative reconcile loop can safely migrate; a spec-field change would at
best orphan everything on the old cluster while partially standing up a new one, not
actually move anything. Moving an app to a different dev cluster is a deliberate
decommission-and-re-onboard (delete `NodeJSApplication`, which per the fix below already
can't happen until every `ApplicationEnvironment` child is gone — then create a new one),
not a field edit.

**`ApplicationEnvironment`'s `cluster` field stops being a hardcoded Composition
constant and becomes a real, required spec field**, gated the same way: extra-resources
lookup against the registry, must resolve to `type: upper` + `crossplaneReady: "true"`.
**Explicitly rejecting `type: dev` targets is a real, load-bearing enforcement, not just
tidiness** — §10 of `gitops-strategy.md` is explicit that `gitops-<app-name>` (which
`ApplicationEnvironment` writes into) carries upper environments *only*; a dev cluster's
environments belong to the separately-scoped `platform/envs/`-live-read mechanism
instead, with its own narrower `AppProject`. Nothing currently stops `ApplicationEnvironment`
from targeting a dev cluster — it's only ever pointed at `kind-dev` today because that's
the sole cluster that exists, not because anything enforces the boundary. This closes
that gap once the registry exists to check against.

One more real gap the registry surfaces: **the first `ApplicationEnvironment` for a
given (app, cluster) pair must also seed that cluster's own `app.yaml`-equivalent
tenant entry, not just `identity.yaml`.** §6 scopes `AppProject` per cluster — an app
deployed to three clusters gets three independently-generated `AppProject`s, each built
by that cluster's own `tenant-appprojects` from that cluster's own `app.yaml`.
`NodeJSApplication` only ever writes the `devCluster`'s copy; every other cluster an app
gets deployed to needs its own, and `ApplicationEnvironment` is the only thing that ever
learns about a new cluster for an app, so it's the natural (idempotent,
`overwriteOnCreate: true`, same as everywhere else) place to seed it.

**`AppProject`/`Application` ownership stays with ArgoCD's `ApplicationSet`s
(cluster-admin-templated), not moved into direct Crossplane composition** — considered
and rejected, for two compounding reasons. First, it's structurally incompatible with
Bootstrap-tier staying centralized: `ApplicationEnvironment` targeting an upper-env
cluster runs on the dev cluster's Crossplane, which has no credential to that upper-env
cluster's API and structurally shouldn't get one — direct composition would require
exactly the cross-cluster credential the guiding constraint forbids, or force
Bootstrap-tier to un-centralize after all. Second, `AppProject.sourceRepos`/
`destinations` is the actual security enforcement boundary (§6) — its shape is
deliberately authored in `gitops-cluster-<name>`, a repo `gitops-strategy.md` keeps
"close to read-only... rare, high-stakes... real review weight," owned by cluster
admins. Letting a Composition generate that shape directly would move the security
boundary's definition into `idp-service-catalog`'s own release cadence instead — a
different, less rigorous bar than the one `gitops-strategy.md` deliberately wants for
anything that shapes an `AppProject`.

The same reasoning extends to a related idea considered and set aside: having
`ApplicationEnvironment` itself trigger installation of the Attached-tier catalog onto a
new cluster (e.g. by committing into that cluster's `gitops-cluster-<name>`) the first
time an app targets it. Appealing (removes a manual step), but it's the same
ownership-boundary cross one level further down the stack — an app-owner-facing XRD
would be expanding what's installed on a cluster, which `gitops-strategy.md`'s own
terminology section assigns to cluster admins exclusively ("app owner... never touches
cluster config"). `crossplaneReady` in the registry is the alternative that gets most of
the practical value (a single checkable fact `ApplicationEnvironment` gates on) without
crossing it — if the real goal is reducing cluster-admin toil rather than shifting who
controls cluster infrastructure, the lever is a more turnkey admin-run bootstrap script
(extending the existing `hack/bootstrap-upper-cluster.sh` precedent), not moving the
trigger to the app side.

**Fixes the real, twice-confirmed `AppProject`-deletion-ordering bug — built and
live-verified 2026-08-15** (found live building `ApplicationEnvironment`, see
`idp_session_applicationenvironment_xrd` — the two `ApplicationSet`s prune
independently with no ordering between them, so deleting an `AppProject` before its
dependent `Application` finishes its own finalizer cleanup permanently stuck that
`Application`) — at the Crossplane layer, not the ArgoCD one, since ArgoCD's
`ApplicationSet` doesn't expose an ordering primitive for this and teaching it one
isn't obviously possible. Originally planned as a homegrown extra-resources lookup on
`NodeJSApplication`'s own Composition (query for remaining `ApplicationEnvironment`
XRs, refuse deletion if any exist) — superseded before building it once `kubectl
api-resources` on `kind-dev` confirmed Crossplane itself already ships a real
primitive for exactly this: `protection.crossplane.io/v1beta1` `Usage` ("defines a
deletion blocking relationship between two resources"), enforced by a live
`crossplane-no-usages` admission webhook already installed with this cluster's
Crossplane — nothing new to deploy. `ApplicationEnvironment`'s own Composition now
composes one, unconditionally (not gated by the `$clusterOk` cluster-registry check
elsewhere in the same template — the app/env relationship holds regardless of
deployment-gate status): `spec.of` = the parent `NodeJSApplication` (by
`spec.appName`), `spec.by` = the `ApplicationEnvironment` XR itself. `
NodeJSApplication`'s own Composition needed **zero** changes — a real simplification
versus the original design, since the webhook and Usage controller do all the
blocking purely by watching `Usage` objects, regardless of what composed them.
Live-verified end-to-end on `kind-dev`: a real `NodeJSApplication` + referencing
`ApplicationEnvironment`, confirmed the `Usage` object and the `crossplane.io/in-use`
label it drives, confirmed `kubectl delete` on the app is cleanly rejected at
admission time (not a finalizer hang) while the env exists, confirmed the `Usage` is
garbage-collected the moment the env is deleted, confirmed app deletion then succeeds
— see `idp-service-catalog`'s own README (`v0.3.2`) for the full pass.

**The `xr-requests/` mechanism itself (point 1 above) — built and live-verified
2026-08-15.** `gitops-cluster-dev/02-argocd-apps/xr-requests/` adds a dedicated
`ApplicationSet` (a git `directories` generator on `tenants/*`, not a `files` generator
on `app.yaml` — gating XR creation on a file the XR itself produces would be circular)
and a narrowly-scoped `idp-onboarding` `AppProject` (not `default`, not the per-app one
— both are circular too for a brand-new app; scoped to just
`NodeJSApplication`/`ApplicationEnvironment` from `gitops-cluster-dev-tenants` only,
same shape as `platform-cicd`'s own `platform-onboarding` `AppProject`). Live-verified
end-to-end, twice, with a real throwaway app (`xr-onboarding-verify`): a git commit
into `tenants/<app>/xr-requests/nodejsapplication.yaml` created the `app-<app>-cicd`
namespace and the XR, which provisioned real GitHub repos and committed `app.yaml`
back, which the pre-existing `tenant-appprojects` `ApplicationSet` then turned into a
real per-app `AppProject` — closing the loop with zero manual `kubectl apply` anywhere.
A second commit (`applicationenvironment.yaml`, targeting `kind-prod`) deployed a real
env on the second cluster the same way. The `idp-onboarding` `AppProject` boundary was
attack-tested, not just asserted: a committed `Secret` was rejected with `resource
:Secret is not permitted in project idp-onboarding`.

Two real bugs found live during this build:

- **Fixed**: a directory-type `Application` source pointed straight at
  `xr-requests/` errors manifest generation entirely (`app path does not exist`) the
  moment its last file is removed, since git doesn't track empty directories — exactly
  the moment a real deletion needs a clean diff to zero resources instead. Fixed by
  pointing the source at the always-present `tenants/<app>` directory (guaranteed to
  exist — it's what the generator just matched) with `directory: {recurse: true,
  include: "xr-requests/*.yaml"}` instead of the subfolder directly.
- **Suspected resolved, not proven — downgraded 2026-08-15 after 3 clean live
  reproductions**: deleting an `ApplicationEnvironment` xr-request through this
  `Application` was confirmed twice (2026-08-15 morning session) to deadlock against
  the `Usage` it composes — the `Usage`'s own controller refuses to release its
  finalizer until the `ApplicationEnvironment` is actually gone (`WaitingUsingDeleted`),
  while the `ApplicationEnvironment` itself won't finish going away until that same
  `Usage` (an owned, `blockOwnerDeletion: true` dependent) is gone first — circular.
  `PrunePropagationPolicy=background` was tried as a fix and did not resolve it on a
  same-day retest. A later same-day pass (afternoon) live-reproduced the same deletion
  path **3 times, including one attempt matching the original failure's timing and
  target cluster almost exactly** (`kind-prod`, ~13 minutes dwell before deletion, same
  git-commit-removal path) — all 3 tore down cleanly with no manual finalizer-clearing.
  No code change was made to the `Usage`/finalizer mechanism itself between the
  confirmed failures and the clean runs. The one relevant thing that *did* change: a
  real, separate ArgoCD Redis-cache staleness bug (see `gitops-cluster-dev`'s
  `01-argocd/README.md`) was found and fixed immediately before the clean runs, and the
  original failures happened during a session doing many rapid onboard/
  teardown cycles — exactly the load that would build up stale ArgoCD cache state. This
  is a plausible link, not a proven one; forcing a stale-cache condition deliberately
  and re-testing would be needed to confirm causation. Recovery, if this ever recurs:
  manually clear the `Usage` object's own finalizer (`kubectl patch usage <name> -n
  <ns> --type=merge -p '{"metadata":{"finalizers":[]}}'`). Treat env deletion through
  `xr-requests/` as usable but **monitor** rather than fully routine until this has more
  soak time.

**AI-triage (`function-rollout-watcher`/`diagnosis-holmes-dispatch`,
[[idp_session_phase2_holmesgpt]]) needs a real redesign here, not just more clusters to
run on.** Confirmed by reading the function's actual code: it currently watches
`req.observed.resources["rollout"]` — the Rollout composed by *step 1 of its own
pipeline* (the old `ai-rollout`-derived `Application` XRD, which renders the Rollout
directly). But the deployment mechanism that actually got built, `idp-application`
rendered by Helm via ArgoCD, never gives Crossplane a hand in creating the Rollout at
all — there's no live XR for this function to attach to in the real model. (The
function's own README already names this as a known gap; it isn't new here, just newly
relevant.) Fix: switch from same-XR composition to an **extra-resources lookup** — a
small, always-on Attached-tier-shaped resource (`RolloutWatch`) `idp-application`
renders unconditionally alongside any release with `rollout:` set (same treatment as
`ServiceMonitor` — not a developer-selected `components:` entry), whose Composition
observes the *already-existing* Rollout Helm created (by name/namespace convention) and
composes the diagnosis `Job` only, which it legitimately owns creating. Carries
`environmentRef` like every other Attached-tier resource; gets its gitops/src repo
coordinates from `NodeJSApplication`'s own `appRepoUrl` field (already committed to
`app.yaml`) instead of the current per-XR annotation scheme. Runs per-cluster, riding
the same Attached-tier catalog install as everything else in this section — no separate
mechanism needed. **Not resolved here, flagged as real follow-on work**: Holmes itself
needs live in-cluster access to diagnose anything (pod logs, events), so a single shared
Holmes instance has the same locality problem one level further out — whether that
means Holmes runs per-cluster too, or stays shared with per-cluster-scoped credentials,
needs its own pass.

**Upper-env half built and live-verified 2026-08-15**: the registry,
`ApplicationEnvironment.spec.cluster` becoming real, the `type: upper`/`crossplaneReady`
gating (both the rejection and success paths), and the first-time `app.yaml` seeding
are all real and proven end-to-end against `kind-prod` — see Item 3's own "Built and
live-verified for real" note below for the detail, including one real bug found and
fixed (`managementPolicies`, not `deletionPolicy`). Still not buildable: a second
*dev* cluster (`NodeJSApplication.spec.devCluster` and its own registry gate) — no
second dev cluster exists yet, and that field was explicitly out of scope for this
pass. AI-triage's own redesign (this section, above) also remains unbuilt — a
separate pass.

## §1. `provider-github` is the mechanism behind every "create/commit to a repo" step

Category-1 XRDs (§0) need to create a GitHub repo and commit files into it, without a
publish/package step and without a full clone-commit-push flow. The real package
(confirmed live building `NodeJSApplication`, not just this doc's original shorthand) is
`crossplane-contrib/provider-upjet-github` — `provider-github` was a guess at the name,
the actual xpkg reference is `xpkg.upbound.io/crossplane-contrib/provider-upjet-github`.
It wraps the Terraform GitHub provider as plain managed resources: `Repository` (create
the repo), `RepositoryFile` (create/update a single file via GitHub's Contents API — this
is what writes boilerplate files, `cicd.yaml`, a tenant `identity.yaml`-equivalent, or an
env's `values.yaml`), `BranchProtection` (required checks/reviewers, matching what
`platform-cicd` already enforces on `gitops-<app-name>` today — **not built in
`NodeJSApplication`'s first pass**, since it would need to reference status-check names
from a CICD pipeline that doesn't exist yet on this cluster, see Item 1/2's status note).
**Credential: not the GitHub App after all — corrected live, this was the doc's biggest
wrong assumption.** The original plan ("one GitHub App credential, same shape as
`token-review-interceptor` already mints from — no new credential class") turned out to
be structurally impossible for this catalog's actual GitHub account: `jfillman` is a
personal **User** account, not an Organization, and GitHub Apps are unconditionally
blocked from `POST /user/repos` (`403 Resource not accessible by integration`) — a
documented platform restriction, not a permissions/scope gap on the App. GitHub Apps can
only create repositories inside an Organization they're installed on. Confirmed live
building `NodeJSApplication`: the App credential 403'd on every `Repository` create,
with zero App-permission configuration able to fix it. **Live fix: a classic PAT
(`repo` + `delete_repo` scopes), stored the same way** (`source: Secret`, JSON
`{"owner": "jfillman", "token": "..."}` — the provider's `githubConfig.Token` field,
plain PAT auth alongside its `app_auth` field, not instead of it as a provider
limitation — this catalog just doesn't use `app_auth` now). This **is** a new credential
class, contradicting the original plan — tracked here as a known, deliberate deviation,
not silently reconciled. Revisit if `jfillman` ever becomes/moves under an Organization,
which would reopen the GitHub-App path. `owner` stays a **ProviderConfig-wide** setting
either way (required, confirmed live against the provider's own credential-parsing
source) — not a per-`Repository` field, so no XRD in this catalog takes an owner as a
spec field regardless of which credential type backs it.

**Managed-resource family: the namespaced one (`repo.github.m.upbound.io`), not the
Cluster-scoped one — also corrected live, a second wrong first guess.** The provider
ships both a Cluster-scoped family (`repo.github.upbound.io`) and a namespaced one
(`repo.github.m.upbound.io`); the Cluster-scoped one looked like the safer choice at
first because it has real, complete examples in the provider's own repo (the namespaced
example tree uses a stale, never-filled-in placeholder apiVersion,
`template.m.crossplane.io/v1beta1`, that reads like unfinished scaffolding). That
first guess broke immediately on a real cluster: Crossplane v2 hard-rejects a namespaced
XR composing a Cluster-scoped managed resource at all (`cannot apply cluster scoped
composed resource ... for a namespaced composite resource`) — not a permissions issue,
a structural one. The namespaced family's CRDs are genuinely installed and working
despite its misleading examples; `NodeJSApplication` composes those instead, via a
`ClusterProviderConfig` (`github.m.upbound.io/v1beta1`, not the legacy Cluster-scoped
`ProviderConfig`) so the one credential stays referenceable from every namespace without
duplicating the Secret per app. Lesson for the next Bootstrap-tier XRD
(`ApplicationEnvironment`) or any future provider adoption: check the actual installed
CRD scope (`kubectl api-resources`) before trusting which variant a provider's example
directory happens to document best.

## §2. Composition authoring: function-go-templating, not patch-and-transform + KCL

**Revised after actually building `NodeJSApplication`.** This section originally
recommended a pipeline of `function-patch-and-transform` for field-mapping plus a
dedicated KCL-or-Go-templating function for file-rendering, shared across
`NodeJSApplication` and `SpringBootApplication`. What got built instead, matching the
real convention the `SLO` Composition already established: **pure
`function-go-templating`, `source: Inline`, generated from `templates/*.yaml` via a
`build-composition.sh` script** (`idp-service-catalog/compositions/slo/`, now also
`compositions/nodejsapplication/`) — no `function-patch-and-transform` step, no second
Function registration. A second `Function` object pointing at a package reference
already installed (e.g. a dedicated file-rendering function alongside the shared
`function-go-templating`) corrupted Crossplane's shared dependency-lock graph
cluster-wide, a real bug hit live building the `SLO` Composition (see that Composition's
`build-composition.sh` header) — reusing the one already-installed `function-go-templating`
Function via `Inline` templates avoids the problem entirely rather than working around it.
The "shared function, parameterized by stack" idea for `NodeJSApplication`/
`SpringBootApplication` code reuse is deferred until `SpringBootApplication` actually
gets built — nothing to share yet with only one stack implemented.

**Confirmed: Crossplane v2 target.** See the Terminology section above for what that
changes — namespaced XRs directly, no separate Claim type. Composition Functions
(this section) are orthogonal to the v1/v2 split (a 1.14+ change, still how v2
Compositions work) — nothing here needed revising because of the version confirmation,
only the Claim/XR terminology used throughout the rest of this doc did.

## Framework: identity/bootstrap resources vs. attached resources vs. embedded fields

Three tiers, not a blanket policy — this is the direct answer to your "should we
separate these out" questions (items 5, 6). **Revised from the first pass** after your
dependency-ordering questions below — Attached-tier resources no longer commit their
own separate file; they're values blocks inside the *same* `values.yaml` the Deployment
already comes from, rendered by one shared chart (§3). This isn't just simpler, it's
what makes "can this exist without an app/env" unaskable rather than merely validated —
see "Dependency ordering" below.

| Tier | Examples | Mechanism | Lifecycle |
|---|---|---|---|
| **Bootstrap** | `NodeJSApplication`, `SpringBootApplication`, `ApplicationEnvironment` | A commit into `gitops-cluster-<cluster>-tenants/<app>/xr-requests/`, synced by the app's own onboarding `Application`, reconciled via `provider-github` (§0) — no live API call, no k8s credential | Creates a new addressable git location that didn't exist before — a repo, or a new `<cluster>/<env>/values.yaml` |
| **Attached** | `SLO`, `Redis`, `OAuthServer` (§ Components) | A block inside that env's `values.yaml` (`slos:`, `components:`); the `idp-application` chart (§3) renders it into an XR, directly in the app's own namespace, auto-stamped with which app/env it came from | Independent provisioning lifecycle, but only expressible *inside* an existing env's file |
| **Embedded** | config, secrets, HPA, PodDisruptionBudget, `AnalysisTemplate`, resource limits | Plain fields on the same `values.yaml`, rendered directly (no XR at all) | 1:1 with the single workload (Argo `Rollout` — §3) the Application already owns |

**Linking mechanism, revised**: because Attached-tier blocks live inside the *same*
`values.yaml` file the chart already renders the Deployment from, the chart already
knows `appName`/`cluster`/`env` from its own release context — it stamps
`spec.environmentRef: {name: <app>-<cluster>-<env>}` and `platform.io/app: <app>` onto
every XR/resource it renders from a `components:`/`slos:` block automatically. A
developer adding Redis to their app never types an app reference by hand; it's implicit
in which file they're editing. `environmentRef` names the specific `ApplicationEnvironment`
XR this resource depends on (deterministic name `<app>-<cluster>-<env>`, not three
separate fields) — see "Dependency ordering" for what that reference is actually for.
On the Backstage side, the Crossplane plugin still needs to translate these refs into
`dependsOn`/`dependencyOf` catalog-info.yaml relations — unchanged from the first draft,
still not built.

### Dependency ordering — what can and can't exist without what

Direct answers to your questions, in order:

**"Can an environment be added without specifying an application?"** No, structurally.
`ApplicationEnvironment.spec.environmentRef`'s app-name component is a required XRD
schema field — the Kubernetes API server itself rejects an XR missing it, before any
Composition runs. Semantically it couldn't work anyway: the Composition's only job is to
commit into `gitops-<app-name>`, so without an app name there's no repo to commit into.

**"Does it need to be required to link a ConfigMap to an application? Does an env need
to exist? Where would it deploy with no env defined?"** These questions dissolve under
the revised mechanism rather than needing a runtime check to catch them: a config field
(Embedded tier) isn't an independently-Claimable resource at all, it's a key inside a
specific env's `values.yaml`. There is no "orphan ConfigMap" state to guard against,
because there's no path to creating one that isn't "edit a file that, by definition,
already belongs to one app and one env." Same answer for Attached-tier blocks
(`components:`, `slos:`) — they're keys in that same file.

**The one place a real dependency-ordering gap remains**: `environmentRef` proves an
app name was *supplied*, not that the named `ApplicationEnvironment` XR actually
*exists yet* — OpenAPI schema validation can't express cross-resource existence checks.
Two layers, not one:

1. **Structural backstop**: the chart only ever renders Attached-tier blocks as part of
   a Helm release that's already scoped to one specific, already-provisioned env's own
   `Application`/`AppProject` (§0.2) — so in practice this can't actually be reached
   through the normal self-service path at all. It only becomes reachable if something
   hand-constructs the XR directly, bypassing the chart.
2. **For that edge case**: the Attached XRD's Composition Function does an "extra
   resources" lookup of the referenced `ApplicationEnvironment` XR and reports
   `Ready: False` with a clear reason if it's not found yet, rather than erroring
   opaquely — Crossplane's native way to express "this waits on that," visible via
   `kubectl describe` and, once built, on the resource's own Backstage catalog page.

## §3. The `idp-application` Helm chart — the actual center of this design

Everything above funnels into one chart (`idp-service-catalog/charts/idp-application`),
because every env is exactly one Helm release: one `values.yaml`, one `Application`,
one namespace. Worth designing this concretely rather than leaving "plain Helm values"
abstract, since it's what items 5/6 and the new Components/SLO mechanism above actually
resolve to.

```yaml
# gitops-<app-name>/<cluster>/<env>/values.yaml — one file, one release, one namespace
appName: my-app
appType: app              # app | infra (naming-conventions.md's existing distinction —
                           # see §"Components" below for why infra-type reuses this, not a new concept)
rollout:                   # Embedded — Argo Rollouts is the default workload, see § below.
  image: {repository: ..., tag: ...}   # omit `rollout` entirely for an infra-type release with no custom workload
  replicas: 2
  resources: {...}
  strategy: canary          # canary | blueGreen — platform supplies default steps unless overridden
  # Curated, safe-default escape hatches for the main container/pod — common enough to
  # deserve real fields rather than forcing every app through the raw podSpec escape
  # hatch below. Each is toYaml'd straight in when set; the chart supplies a sane
  # default (e.g. an httpGet probe on the first port) when omitted.
  command: []
  args: []
  ports: [{name: http, containerPort: 8080}]     # first entry doubles as the Service/ingress target
  livenessProbe: {}
  readinessProbe: {}
  podSecurityContext: {}
  containerSecurityContext: {}
  extraContainers: []       # full container specs, appended as-is (sidecars) — kept
                             # separate from podSpec.containers, see below
  podSpec: {}                # the actual "any pod-spec field" escape hatch — a raw map,
                             # deep-merged (Sprig mergeOverwrite) onto the rendered
                             # .spec.template.spec AFTER every curated/generated field
                             # (main container, volumes from configMaps/secrets/volumes
                             # below, extraContainers). Anything not already covered by a
                             # curated field goes here: tolerations, affinity,
                             # nodeSelector, topologySpreadConstraints, dnsPolicy,
                             # hostAliases, terminationGracePeriodSeconds, etc.
                             # Deliberately NOT for containers[0] (use the curated fields
                             # above) or additional containers (use extraContainers) —
                             # mergeOverwrite replaces whole arrays rather than merging by
                             # index, so mixing container edits into this escape hatch
                             # would silently clobber the chart-rendered container list.
analysisTemplates:          # Embedded — app-specific custom AnalysisTemplates, see § below.
  - name: checkout-conversion-rate
    metrics: [...]
env: [{name: FOO, value: bar}]              # Embedded
configMaps:                 # Embedded — revised this round, see "Rendering mechanism" below.
                             # `as: env|volume|both` (default volume) and
                             # `existingConfigMap:` (a data: alternative, for one this
                             # chart doesn't own — e.g. a Kustomize configMapGenerator's
                             # output) added 2026-08-13, see revision note above.
  - name: app-settings
    data: {app-config.yaml: "...", logging.yaml: "..."}
  - name: feature-flags
    data: {flags.json: "..."}
secrets: [{name: db-password, key: DB_PASSWORD}]        # Embedded — same appSecretStores/ESO
                                                          # mechanism platform-cicd already built,
                                                          # not reinvented here — unlike ConfigMap,
                                                          # NOT revised to a multi-object shape, see below.
                                                          # `as: env|volume|both` (default env)
                                                          # added 2026-08-13, see revision note above —
                                                          # volume mode mounts one key at an exact
                                                          # file path, not a directory.
volumes:                   # Embedded — PVC support, added 2026-08-13. Same list-of-objects
                             # + generic range-loop pattern as configMaps: one PVC + one
                             # volumeMount per entry.
  - name: uploads
    size: 10Gi
    storageClassName: standard      # omit for cluster default
    accessModes: [ReadWriteOnce]    # default if omitted
    mountPath: /data/uploads
autoscaling: {enabled: false, min: 2, max: 10, targetCPUPercent: 70}  # Embedded — scaleTargetRef.kind: Rollout
podDisruptionBudget: {enabled: false}                                 # Embedded
ingress: {enabled: true, host: my-app.example.com}                    # Embedded
networkPolicy:              # Embedded — added 2026-08-13. Default reflects "isolate
                             # traffic to its own namespace": deny cross-namespace ingress
                             # except from the ingress controller (only relevant when
                             # ingress.enabled) and other pods in this same namespace;
                             # egress left open by default — see revision note above for why.
  enabled: true
  allowIngressFromIngressController: true
  allowIngressFrom:         # simplified peers, added 2026-08-13 — {namespace, podLabels?,
                             # ports?} or {cidr, ports?}; namespace resolves via the
                             # auto-populated kubernetes.io/metadata.name label, same
                             # mechanism as ingressControllerNamespaceSelector. See
                             # revision note above and idp-application's own README for
                             # why extraIngressRules alone wasn't simple enough.
    - namespace: app-payments-prod
      ports: [8080]
  allowEgressTo: []          # same shape as allowIngressFrom, for egress
  extraIngressRules: []      # raw NetworkPolicyIngressRule list — escape hatch for
                             # whatever allowIngressFrom can't express (UDP/SCTP,
                             # multiple ORed peers in one rule)
  extraEgressRules: []
components:               # Attached — rendered as Component XRs, see below
  - type: redis
    name: cache
    spec: {size: small}
slos:                      # Attached — rendered as SLO XRs
  - name: availability
    objective: 99.9
    indicator: {...}
```

### Rendering mechanism: one generic pattern, not sub-charts, applied to every list field

**Not Helm sub-charts** — this is a mechanical Helm limitation, not a preference. A
sub-chart is a statically-declared, 0-or-1 unit in `Chart.yaml` — Helm's dependency
model has no way to say "instantiate this sub-chart once per entry in a values list,
with different values each time." Every field above that needs *N* instances
(`components`, `slos`, `configMaps`, `analysisTemplates`) genuinely can't be expressed
as a sub-chart at all, regardless of style preference — a sub-chart could give you one
optional Redis, never "however many components a developer lists."

**The actual mechanism: one `range` loop per field, in the main chart's own
templates** — the standard Helm idiom for "N instances driven by a values list."
Concretely, for `components:`/`slos:` (Attached tier), the loop is *generic*, not
type-specific: each entry carries a `type` (`redis`, `oauth-server`, `slo`) mapped to a
`kind` via a small lookup table, and its `spec:` block is passed straight through to the
rendered XR untouched — the chart doesn't validate or branch on component-specific
fields at all, Crossplane's own XRD schema does that when the XR lands. **This is what
makes the catalog extensible without editing this chart**: adding a new Component XRD
next month (Kafka, say) needs one new row in the lookup table, never new template logic.
Backstage's own form curation is still the primary UX guardrail (§ Dependency ordering) —
the generic passthrough is what happens underneath a curated form, not a replacement for
one.

**ConfigMap, revised to answer "what if a user needs several"**: the first draft's flat
`configFiles: [...]` never actually said whether multiple entries meant multiple keys in
one ConfigMap or multiple ConfigMap objects — a real gap. Fixed with a two-level shape:
`configMaps:` is a list of *objects* (`name`, `data: {key: content, ...}`), rendered by
the same generic range-loop. This answers both cases at once — multiple keys in one
logical ConfigMap (nest more entries in one `data:` block) and multiple separate
ConfigMap objects (add another list entry) — without needing to pick one shape over the
other. Real reasons an app might want the split (separate mount paths, separate
restart-on-change semantics via a checksum annotation scoped to just one object, the
1MiB per-object size limit) all fall out naturally once it's list-of-objects rather than
list-of-files.

**Secrets stays as it was, deliberately not given the same two-level treatment**:
`platform-cicd`'s existing ESO pattern is already one `ExternalSecret` per app with
multiple keys — a working, live-verified mechanism, not something this doc is revising.
Only ConfigMap had the gap; secrets never did.

### Argo Rollouts as the default deployment resource

New decision this round, replacing plain `Deployment` as `idp-application`'s core
workload — the `rollout:` block above renders an Argo `Rollout`, not a `Deployment`.
Downstream effects worth being explicit about:

- **`autoscaling`'s HPA now targets `scaleTargetRef: {kind: Rollout, ...}`** instead of
  `Deployment` — Argo Rollouts supports this natively, no different mechanism needed,
  just a different `kind` in the same field.
- **`AnalysisTemplate`: same curated-default-plus-self-service-escape-hatch pattern
  already used for Components (§ item 7) and `idp-cluster-baseline` (§8 of
  `gitops-strategy.md`)**, applied a third time — worth naming as a real, repeating
  pattern in this design, not a coincidence:
  - **Platform-curated, shared, reusable across every app**: `ClusterAnalysisTemplate`
    resources (Argo Rollouts' own cluster-scoped variant, referenceable from any
    namespace) — a small golden-path library (`error-rate-check`,
    `success-rate-check`) living in `idp-cluster-baseline`, installed alongside the
    Argo Rollouts controller itself in cluster config, **not** a service-catalog XRD —
    developers reference these by name, they never Claim or create one.
  - **App-specific, custom**: the `analysisTemplates:` Embedded-tier block above,
    rendered as namespaced `AnalysisTemplate` resources scoped to that app's own
    namespace — for genuinely app-specific metrics a shared library wouldn't cover
    (e.g. a business KPI unique to this app). Argo Rollouts lets a single `Rollout`'s
    canary `analysis.templates[]` mix references to both cluster-scoped and
    namespace-scoped templates in the same step, so an app can use the platform
    defaults *and* its own custom one together without any special wiring.
- **A real integration dependency worth flagging, not verified yet**: the multi-cluster
  release-outcome mechanism from `gitops-strategy.md` (`PostSync`/`SyncFail` hooks) only
  fires correctly once ArgoCD's own `status.health.status` reaches `Healthy` — which
  means ArgoCD has to understand a `Rollout`'s health correctly, not just "resource
  applied." ArgoCD does ship a built-in health check for `argoproj.io/Rollout` in
  reasonably current versions, but this doc hasn't confirmed it against the actual
  ArgoCD version in use — worth a real live check (same instinct as the hook-timing test
  in `platform_cicd_session_multicluster_argocd`) before relying on it, not an assumption.

**What's deliberately NOT in this chart**: anything cluster-scoped, or anything a
per-app `AppProject` (§6 of `gitops-strategy.md`) shouldn't be allowed to create outside
its own namespace — `ClusterSecretStore`, the `Application`/`AppProject` resource
itself (can't render its own container), platform-shared infra (ingress controller,
Vault server if one exists — see the Component/Vault discussion below). Those live in
cluster config, referenced by name, never templated here.

**Resolved 2026-08-13, when the chart was built**: `rollout:` is genuinely optional
(set to `null`/omitted) rather than needing a separate, lighter sibling chart — the
"optional in the same chart" leaning below, confirmed. Implemented and fixture-tested
(an `appType: infra` release with only a `components:` block renders cleanly, no
Rollout/Service/HPA/PDB/AnalysisTemplate, just the NetworkPolicy and the Component XR).

---

## Item 1/2: `NodeJSApplication` / `SpringBootApplication`

**Separate XRDs, not one generic `Application` XRD with a language field.** A discriminated-
union schema would technically work, but goal 8's own framing — one XRD becomes one
Backstage template — argues for it directly: a developer picking "New Service" wants two
distinct, clearly-labeled template cards, not a generic form with a language dropdown
buried inside. Matches the existing memory note on this (favor XRD designs that "read
cleanly as a Backstage template input" over internally-convenient ones). The repo-
creation/CICD-onboarding logic that's ~90% identical across languages would live in a
shared Composition Function per §2's original plan — deferred until `SpringBootApplication`
actually gets built (see §2's revision note); `NodeJSApplication`'s own Composition isn't
factored for sharing yet, nothing to share with only one stack implemented.

**Scope, deliberately narrow**: src repo + boilerplate + an *empty, scaffolded*
`gitops-<app-name>` repo + a `tenants/<app-name>/app.yaml` commit into
`gitops-cluster-dev-tenants` (corrected from this section's original `identity.yaml`-
equivalent guess — `app.yaml` is the real, already-built file this catalog's tenant
`ApplicationSet`s read for the app-level `AppProject`, see
`gitops-cluster-dev-tenants/README.md`). **Not** upper-env provisioning — that's item 3.
This split maps directly onto the lower/upper security boundary already designed:
everything these two XRDs do is inherently dev-cluster, self-service, no-review-gate-needed
territory; promoting to a real environment is a deliberately separate, higher-trust action.

**Status: `NodeJSApplication` built 2026-08-13**, live-verified on `kind-dev`
(`idp-service-catalog/xrds/nodejsapplication.yaml`, `compositions/nodejsapplication/`).
At build time, the "CICD onboarding" half of this scope genuinely couldn't complete
yet — `platform-cicd`'s control plane wasn't running on `kind-dev` — so the Composition
surfaced the gap as an explicit custom condition (`CicdOnboarded: False`, reason
`CicdOnboardingPending`) rather than silently succeeding or blocking. **Real as of
2026-08-15**: `platform-cicd`'s control plane now runs as a second, independent instance
on `kind-dev` (see `platform-cicd/docs/bootstrap.md`'s own note), and the Composition
gained a real step committing `tenants/<app-name>/identity.yaml` into
`platform-cicd-kind-dev-tenants` — `platform-cicd`'s own tenant-onboarding
`ApplicationSet` picks it up and stands up the app's actual CICD pipeline. **Redirected
2026-08-16** (`idp-service-catalog` v0.3.5): that dedicated repo was eliminated once
live history showed it only ever held throwaway apps and `kind-dev`'s platform-cicd
instance was confirmed idp-exclusive - the same commit now lands in
`gitops-cluster-dev-tenants` instead, alongside `app.yaml`. See that repo's own README
and `cicd-identity-yaml.yaml`'s own header comment for the full reasoning.
`CicdOnboarded` now reflects the real observed status of that commit (`True` once it's
Ready), not a hardcoded `False` — live-verified end-to-end with a throwaway app,
including a real signed build (genuine `.att` OCI attestation in the registry, Fulcio
cert chained to `kind-dev`'s own independently-generated root CA — not just the
`chains.tekton.dev/signed: "true"` annotation, which has lied on this platform before).
Custom condition, not an override of the standard `Ready` condition, which
`function-go-templating` reserves and errors on if a Composition tries to set it
directly (confirmed live; the framework's own custom-condition mechanism, target
`CompositeAndClaim`, is the supported way to surface this). `SpringBootApplication` isn't
built. `BranchProtection` was also deliberately left out of this pass — it would need to
reference status-check names from a CICD pipeline, and while one now exists, wiring
`BranchProtection` itself to it is still separate, unstarted work.

Live verification (real `kubectl apply`, throwaway `nodejsapp-verify-test`) produced two
real corrections, not assumed in the original design — see §1 for the full detail: the
GitHub App credential can't create repos under `jfillman`'s personal account at all (a
PAT backs the `ProviderConfig` instead, for now), and the Composition composes
`repo.github.m.upbound.io` (namespaced), not `repo.github.upbound.io` (Cluster-scoped) —
Crossplane v2 rejects the latter for a namespaced XR outright. Both real repos, all five
boilerplate files, and the `tenants/nodejsapp-verify-test/app.yaml` commit were confirmed
via the GitHub API before teardown. One more live footnote worth recording: the PAT
needs `delete_repo` alongside `repo` — without it, `Repository` deletion 403s and the
composed resource gets stuck `Terminating` (hit live during cleanup; not a
`NodeJSApplication` bug, but relevant to anyone deprovisioning through this provider).

**Why `gitops-<app-name>` gets created here, empty, rather than by item 3**: its
lifecycle is app-level (create once), not env-level (create per cluster×env) — creating
it alongside the src repo, at the one moment both are being bootstrapped together, avoids
a "which of possibly-several env Claims owns the shared repo" ownership question with no
clean answer under Crossplane's per-Claim resource ownership model.

**Still not built**: a required, creation-immutable `devCluster` field (gated against
the new cluster registry — `type: dev` + `cicdReady`), designed 2026-08-15 — see
"Where Crossplane runs across a multi-cluster fleet" in §0 for the full reasoning.

**The `AppProject`-deletion-ordering bug fix, also designed 2026-08-15, is built and
live-verified** — but it doesn't touch this XRD/Composition at all, contrary to the
original plan recorded here. It's a `protection.crossplane.io` `Usage` composed by
`ApplicationEnvironment`'s own Composition instead (§0 has the full mechanism and
live-verification detail) — `NodeJSApplication` needed zero changes.

## Item 3: `ApplicationEnvironment` (renamed from `UpperEnv`)

Naming and mechanism, both addressed:

**Granularity — one XR per (app, cluster, env), not one XR per app covering all its
envs.** Matches `gitops-<app-name>`'s own directory structure 1:1 (`<cluster>/<env>/
values.yaml`), and avoids the failure mode of a list-shaped spec where adding a new env
means editing (and risking corrupting) an existing resource rather than creating a new,
independent one.

**Mechanism — pure `provider-github`, no direct access to the target cluster at all.**
The Composition commits two things: the env's `<cluster>/<env>/values.yaml` into
`gitops-<app-name>`, and an onboarding entry into `gitops-cluster-<cluster>-tenants`
(creating that entry if this is the app's first env on that cluster). The target
cluster's own `argocd-apps` ApplicationSet — already watching its own tenants repo, per
`gitops-strategy.md` §1 — picks up the new entry entirely on its own and creates the
namespace/`Application`/`AppProject` as part of its normal sync. This is *why* a remote
`provider-kubernetes` credential is never needed here, and why this XRD is Bootstrap-tier
(§0.1), not Attached-tier: at XR-creation time, nothing on the target cluster exists
yet for it to commit *into*.

**Naming**: `ApplicationEnvironment` over the placeholder `UpperEnv` — names what it
does (deploys one environment for one application) rather than where it sits in a
lower/upper taxonomy that's really a property of *which repo* the request lands in
(gitops-<app-name> vs. `/platform/envs/`), not of the XRD itself. Easy to bikeshed
further, low cost to rename later.

**Built and live-verified 2026-08-15** (`idp-service-catalog/xrds/
applicationenvironment.yaml`, `compositions/applicationenvironment/`). Two design
calls resolved concretely, both confirmed against already-live code before deciding,
not guessed:

- ~~**`cluster` stays a fixed Composition constant (`"kind-dev"`), not a spec field** —
  confirmed the already-built `tenant-onboarding` ApplicationSet
  (`gitops-cluster-dev/02-argocd-apps/tenant-onboarding/applicationset.yaml`) already
  hardcodes the same literal in two places (`valuesObject.cluster` and its
  `valueFiles` path); there's no real multi-cluster wiring anywhere downstream yet to
  make a spec field meaningful. Matches `NodeJSApplication`'s own precedent
  (`platformOwner`/`tenantsRepo` as fixed constants).~~ **Superseded 2026-08-15**, now
  that a second, real upper-env cluster (`kind-prod`) exists to design and test
  against: `cluster` becomes a required spec field, gated via the new cluster registry
  (`type: upper` + `crossplaneReady`) — explicitly rejecting `type: dev` targets, since
  §10 scopes `gitops-<app-name>` to upper environments only. See "Where Crossplane runs
  across a multi-cluster fleet" in §0 for the full reasoning (also covers the
  first-time-on-a-cluster `app.yaml`-seeding gap this surfaces).
- **Initial `values.yaml` is an identity-only stub, `rollout: null`** — no real image
  exists to deploy at XR-creation time regardless of CICD control-plane availability
  (`platform-cicd` now runs on `kind-dev` as of 2026-08-15, but a real image only
  exists once a developer's own push actually completes a real release through it).
  Rather than seed a placeholder image that would sit in permanent `ImagePullBackOff`,
  the Composition reports a sibling `WorkloadDeployed: False` custom condition — same
  mechanism `NodeJSApplication`'s own `CicdOnboarded` condition used before that one
  went real (see Item 1/2's own status note), same accepted limitation (doesn't
  auto-clear once a developer's own follow-up PR sets a real `rollout.image`).

**`env` opened up from a closed enum to team-chosen names, 2026-08-15.** Was
`enum: ["dev", "staging", "prod"]` since the field's introduction — traced every real
consumer (this Composition's own template, the `tenant-onboarding` `ApplicationSet`,
the `AppProject`'s own `destinations` wildcard `app-<appName>-*`) and confirmed
nothing branches on the specific value; it was always pure path/name interpolation
(the k8s namespace `app-<appName>-<env>`, git paths `<cluster>/<env>/values.yaml` and
`tenants/<appName>/<env>/identity.yaml`), never encoded business logic. Replaced the
enum with a `pattern` matching Kubernetes' own DNS-1123 namespace-label rule plus
`maxLength: 20`, so a value Kubernetes would reject still fails at XR admission with a
clear message rather than downstream as an ArgoCD sync failure. Live-verified on
`kind-dev`: a custom name (`perf-test`, previously impossible) reconciles end-to-end
for real; an invalid one (`Staging!`) is rejected at admission with the expected
pattern-mismatch error.

**Built and live-verified for real 2026-08-15** (same day as the design above,
different session): `cluster` is now a real required field, the cluster registry
exists (`gitops-cluster-dev/00-bootstrap/cluster-registry/`), and `kind-prod` was
bootstrapped as a real second cluster (`gitops-cluster-kind-prod`, reusing its
pre-existing ArgoCD instance) specifically to prove both the rejection path
(`crossplaneReady: "false"` → `ClusterReady: False`, zero resources created) and the
success path (real commits, `kind-prod`'s own ArgoCD picking up the new tenant on
its own, a real namespace/`ServiceAccount`/`NetworkPolicy`) end-to-end, not just in
design. One real bug found live and fixed: the cluster's shared `app.yaml` can't use
`spec.deletionPolicy: Orphan` as originally planned — `provider-upjet-github`
v0.19.1's `RepositoryFile` CRD has no such field, confirmed via a real
`ReconcileError` (`.spec.deletionPolicy: field not declared in schema`) plus
`kubectl explain`. Fixed with `managementPolicies` excluding `"Delete"` instead, same
intent, correct field for the actually-installed CRD schema. See
`idp-service-catalog`'s own README and [[idp_session_applicationenvironment_xrd]]
follow-on memory for the full detail.

Also confirmed before building: `SLO`'s own Composition doesn't actually implement
the "extra resources lookup" dependency-ordering mechanism described below — it's
aspirational text, not built anywhere in this catalog yet. `ApplicationEnvironment`
doesn't need to expose anything for that lookup as a result; it's purely the write
side of the `environmentRef` contract `SLO`'s XRD and the chart's own
`environmentRef` helper already fix as `<appName>-<cluster>-<envName>`.

## Item 4: `SLO` (narrowed from SLA/SLO/SLI)

**Start with one XRD, `SLO`, not three.** SLI (the measured indicator — e.g. p99
latency) is naturally just a field *within* an SLO spec (which query defines success),
not an independent concept worth its own XRD. SLA (an external, often contractual
commitment, sometimes aggregating multiple SLOs with consequences attached) is a
reporting/business layer that can be built later, on top of SLOs that already exist —
nothing about building `SLO` first forecloses adding an `SLAReport` XRD afterward.

**Superseded again, same day: switched to wrapping Sloth after all.** The hand-rolled
revision immediately below was built and live-verified first (own PromQL/burn-rate
math, no Sloth dependency) - then, discussing it further, the user asked for a
pros/cons comparison and was persuaded back to wrapping Sloth, for two concrete
reasons: the hand-rolled version only implemented a 2-tier simplification of the SRE
workbook's real 4-window pattern (page + ticket tiers each have a fast AND a slow
alert; the hand-rolled version only had one per severity), and "wrap an existing tool"
is this project's convention everywhere else (Argo Rollouts, ESO, component charts) -
the hand-rolled version was the outlier, not the house style. Also built and
live-verified on `kind-dev`, on a second pass after this: the XRD got SIMPLER, not
more complex, switching to Sloth - `spec.window` and `spec.alerting.burnRates` are
both gone, since Sloth computes the compliance period (a controller-wide default, not
per-SLO - confirmed against Sloth's own CRD schema) and the full canonical burn-rate
pattern automatically from just `objective`. The Composition generates a Sloth
`sloth.slok.dev/v1 PrometheusServiceLevel`, not a `PrometheusRule` directly - Sloth's
own controller (`gitops-cluster-dev/10-crds-operators/sloth/`) does that translation.
See `idp-service-catalog/README.md`'s Status section for the real bugs hit switching
(most notably: a second `function-go-templating` Function registration, added to keep
the SLO Composition's templates isolated, corrupted Crossplane's package-manager lock
graph for every OTHER Function on the cluster - fixed by using `source: Inline`
instead, which also meant no separate Function/mount was needed at all).

**Superseded 2026-08-13: built hand-rolled, not wrapping Sloth** (first pass, later
superseded again above). The reasoning below ("don't reinvent SLO-to-alerting-rule
translation") was the original lean, but the user explicitly chose the hand-rolled
approach instead (inspired by a
[kube-slo-style article](https://dpacgdm.medium.com/your-slos-should-be-kubernetes-resources-not-grafana-dashboards-8d94820e2b32)
proposing exactly that), trading Sloth's battle-tested math for one dependency-free
artifact with no extra in-cluster controller. Original text kept below for the record:

~~**Don't reinvent SLO-to-alerting-rule translation — wrap an existing tool.**
[Sloth](https://sloth.dev) (or OpenSLO/Pyrra, same space) already solves "SLO spec →
multiwindow-multi-burn-rate Prometheus recording/alerting rules" correctly; the `SLO`
XRD's Composition should generate whatever CR that tool consumes (or generate
`PrometheusRule` directly if going dependency-free), not hand-roll the burn-rate math in
a Composition Function.~~

**Attached-tier, per the revised mechanism (§ Framework)**: a `slos:` block in that
env's own `values.yaml` (an app can have different SLOs for staging vs. prod, since each
env has its own file) — the `idp-application` chart renders one `SLO` Claim per entry,
auto-stamped with `environmentRef`. Same namespace, same AppProject, no new credential,
no separately-committed file. Structurally upper-env-only in practice (nothing stops a
lower-env `slos:` entry from existing, but it's a meaningless concept on an ephemeral
test namespace) — worth a Kyverno policy in the cluster-config policy layer rejecting it
there, rather than teaching the XRD itself about environment tiers.

## Item 5: config/secrets — recommend no standalone XRD

**Static, developer-authored config: plain Helm values on the Application chart, not a
separate XR.** A ConfigMap has no independent provisioning lifecycle — it's data
1:1-coupled to the Deployment that reads it, and it churns with ordinary app iteration
far more often than infrastructure topology does. Routing every config edit through a
full Crossplane reconcile, a separate Backstage catalog entry, and the linking
machinery is ceremony with no matching payoff. Mechanism: the existing per-env
`values.yaml` (upper) / `platform/envs/*.yaml` (lower) carries config fields directly,
rendered into a ConfigMap by the same chart that renders the Deployment.

**Secrets: reuse the ESO/`ExternalSecret` consumption pattern `platform-cicd` already
built, with Infisical replacing the backend** — confirmed this round; see the revised
Item 8 below for the backend/`SecretStore` design. The developer-facing half is
unchanged: the `secrets:` Embedded-tier list in §3, one `ExternalSecret` per app,
consumed by the workload exactly as before. Only what's *behind* the `SecretStore`
object changes.

**Dynamic/derived config (e.g. a database's connection string) is not a ConfigMap
concern at all** — it's an output of whatever XRD provisions the *underlying* dynamic
resource (a future `Database` XRD's own Composition writes its own connection-info
Secret as one of its managed resources). No separate `ConfigMap` XRD is needed even for
this case.

## Item 6: HPA — mostly embedded, but names the real dividing line

**Recommend: an optional field block on the Application XR's spec** (`spec.autoscaling:
{enabled, min, max, targetCPUPercent}`), same mechanism as ConfigMap — not a separate
XRD. Reasoning: HPA is still 1:1 with the single Deployment the Application XR
already owns; there's no independent lifecycle or cross-app sharing that a separate
XR would buy. The "genuinely optional, added later, real operational stakes" concern
you raised is fully addressed by it being an optional field with a safe default
(disabled) that a developer turns on via a normal PR to their own values file — same
review bar as any other config change in that repo, no new XR needed. Concrete
schema: `autoscaling`/`podDisruptionBudget` in §3.

**The actual dividing line this surfaces, worth stating explicitly** since it'll keep
coming up as the catalog grows: *"1:1-coupled, in-namespace, no independent
provisioning lifecycle" → embed as a field (ConfigMap, HPA, PodDisruptionBudget,
resource limits). "Independent provisioning lifecycle, real external footprint" →
separate Attached-tier XRD (SLO; Components — Redis, OAuth server — immediately below).*

## Item 7: Components — Redis, an OAuth server, and others (new, from this round)

**Real infrastructure with its own provisioning lifecycle, not embeddable** — squarely
Attached-tier, using the mechanism above: a `components:` block entry in some env's
`values.yaml`, rendered by the `idp-application` chart into an XR (`Redis`,
`OAuthServer`, ...), reconciled locally by that cluster's Crossplane.

**Yes to your question 1 — these are real, independent XRDs, so they're independently
selectable Backstage templates, not just a hand-edited values.yaml block.** Nothing about
being Attached-tier prevents that; it only determines the *mechanism* behind the
template action. "Add Redis" in Backstage means the same thing "New NodeJS Application"
now means (§0): the action commits — here, appending an entry to the target env's
`components:` list in `gitops-<app-name>`, via the same scoped GitHub token mechanism —
rather than a live API call either way. Bootstrap and Attached tiers were never
different in *whether* Backstage can offer them as templates, only in *which* git
location the resulting commit lands in.

**Each wraps a real upstream Helm chart via `provider-helm`'s `Release` resource,
confirmed as the right mechanism** — not hand-assembled Deployment/Service/PVC
manifests in a Composition. Recommend the platform maintain a **thin wrapper chart per
component type** (`idp-service-catalog/charts/component-redis`, wrapping Bitnami's Redis
chart or similar), not a bare pass-through to an arbitrary upstream chart — same
curated-golden-path reasoning that already won for #1/#2 over a generic
discriminated-union XRD: a `Redis` XRD with a real schema (`size: small|medium|large`,
`persistence: bool`) gives a legible Backstage form and platform-controlled defaults; a
generic `HelmComponent` XRD (`chartRepo`/`chartName`/arbitrary `values:` map) is more
flexible but gives up both the guardrails and the self-service simplicity goal 1/6 both
ask for. Worth having the generic escape hatch too, later, for the case the curated
catalog doesn't cover yet — not designed now, same pattern as `idp-cluster-baseline`'s
`extraManifests:` escape hatch in `gitops-strategy.md` §8.

**Standalone deployment ("its own namespace") reuses `appType: infra` — not a new
concept.** `platform-cicd`'s `docs/naming-conventions.md` already defines `infra` as
"a shared/platform-adjacent service onboarded with its own pipeline" alongside `app` —
a standalone Redis or OAuth server *is* exactly that, unchanged. Mechanically: it goes
through the same `ApplicationEnvironment` Bootstrap flow as any real app (its own
`gitops-infra-<name>` repo, its own env directory), but that env's `values.yaml`
contains *only* a `components:` block with itself as the sole entry — no `rollout:`
block, no custom workload. This is the open question flagged in §3: whether the chart
needs to tolerate an absent `rollout:` block. If yes (recommended, for mechanism reuse), "attached to
an app" and "standalone" are the *same* XRD, the *same* chart, the *same* Bootstrap
flow — the only variable is which env's `values.yaml` the `components:` entry ends up
in, an app's own or a dedicated infra one.

## Item 8: `SecretStore` — Infisical-backed, self-hosted (resolved this round)

**Same platform-infra-vs-per-app-capability split the Vault discussion in the first
pass already reasoned through — Infisical just answers which concrete backend.**
Infisical itself (the community edition, self-hosted on-prem) is shared **platform
infrastructure**, not a service-catalog item — installed once, cluster-admin-owned, in
`idp-cluster-baseline`'s `10-crds-operators/` group alongside ESO's own controller, the
same way the earlier hypothetical Vault install would have been. A `SecretStore` XRD
provisions per-app *isolation within* that already-running shared instance — it does
not stand up Infisical itself.

**Scope: one `SecretStore` per (app, cluster) — not per (app, cluster, env) — sharing
secrets across envs on the same cluster, never across clusters.** Your addition this
round, and it's a real, deliberate narrowing, not just an implementation detail: the
store itself is what draws the sharing boundary, and that boundary can't cross a
cluster line for a structural reason, not a policy one — a `SecretStore`-family object
is always local to one cluster's API, so "shared across envs, scoped to one cluster" was
always the widest this could go.

**Correction to the first pass, caught by this narrowing**: I'd recommended a
*namespaced* `SecretStore` last round, reasoning that Infisical's own project isolation
made the cluster-scoped `ClusterSecretStore` (`platform-cicd`'s current pattern)
unnecessary. That reasoning silently assumed one store per namespace — **it breaks under
cross-env sharing**: ESO's namespaced `SecretStore` can only be referenced by an
`ExternalSecret` in the *same* namespace; sharing across `app-<name>-dev`/
`-staging`/`-prod` (different namespaces, same cluster) needs `ClusterSecretStore`,
full stop, regardless of backend isolation. **Reverted**: `ClusterSecretStore`, matching
what `platform-cicd` already does — restricted to exactly this app's own namespaces on
this cluster via ESO's `spec.conditions` namespace selector, so "cluster-scoped" doesn't
mean "visible to every app," just "referenceable from more than one of *this* app's own
namespaces." Worth flagging plainly: this is me catching my own earlier recommendation
being wrong once new information changed the tradeoff, the same thing this platform's
own history already has a habit of doing openly rather than quietly — see
`platform_cicd_session_argocd_onboarding`'s tracked-copy → live-read reversal for the
precedent.

**Infisical-side structure that operationalizes "shared, scoped to a cluster"**: one
Infisical project per (app, cluster), with sub-paths inside it — `/dev/*`,
`/staging/*`, `/prod/*` for env-specific secrets, plus a `/shared/*` path for the
ones meant to be reused. The `ClusterSecretStore` connects to the *project*; which
path a given env actually reads is controlled by that env's own `ExternalSecret`
(`remoteRef`), not by the store — so "shared" is opt-in per secret, not automatic
exposure of everything to every env.

**Ownership: the first `ApplicationEnvironment` for a given (app, cluster) pair creates
it; later ones for the same pair reference the existing one.** This is the same
"multiple Claims can't cleanly co-own one shared resource" problem already reasoned
through for who creates `gitops-<app-name>` (§ Item 1/2) — resolved the same way: an
"extra resources" existence lookup (the identical mechanism §"Dependency ordering"
already uses for `environmentRef` waits) checks whether a `ClusterSecretStore` named
`<app>-<cluster>` already exists before deciding whether this env's
`ApplicationEnvironment` Composition also needs to create one.

**The XRD's remaining jobs, matching what you described**:

1. **Configure the Infisical backend** — create the per-(app,cluster) project.
   Mechanism not yet confirmed: a native Crossplane `provider-infisical` may or may not
   exist with the maturity this needs — worth verifying before committing, rather than
   assuming. If it doesn't, `provider-terraform` wrapping Infisical's own (real, actively
   maintained) Terraform provider is the fallback, same "wrap a real tool, don't
   reinvent it" instinct already applied to Sloth for SLOs.
2. **Create the `ClusterSecretStore`** pointing at that project, `spec.conditions`
   restricted to this app's own namespaces on this cluster.
3. **`ExternalSecret` creation stays with the existing Embedded-tier `secrets:`
   mechanism (§3), not this XRD** — unchanged reasoning from last round: which keys an
   env actually pulls (and from which path) is ordinary, fast-churning config, not this
   XRD's concern.

**Linking field deliberately different from every other Attached-tier resource**: not
`environmentRef` (which names one specific env) — `spec.appRef: {name: <app>}` +
`spec.cluster: <cluster-name>`, since this resource explicitly must *not* pin to one
env. A second, now-explicit exception to the general Attached-tier pattern, alongside
"auto-provisioned rather than developer-selected" from last round — both worth keeping
visible as named exceptions rather than quietly special-cased.

---

## Proposed v1 catalog

| XRD | Tier | XR scope | Creates |
|---|---|---|---|
| `NodeJSApplication` | Bootstrap | one per app | src repo, boilerplate, empty `gitops-<app-name>`, dev-cluster CICD onboarding |
| `SpringBootApplication` | Bootstrap | one per app | same, Java/Spring-specific |
| `ApplicationEnvironment` | Bootstrap | one per (app, cluster, env) | env's `values.yaml` in `gitops-<app-name>`, onboarding entry in `gitops-cluster-<cluster>-tenants` |
| `SLO` | Attached | one `slos:` entry per (app, cluster, env) | Sloth/PrometheusRule, rendered by the chart from that env's `values.yaml` |
| `Redis` | Attached | one `components:` entry, in an app's own env or a dedicated `infra`-type one | `provider-helm` `Release` of a platform-wrapped Redis chart |
| `OAuthServer` | Attached | same as `Redis` | `provider-helm` `Release` of a platform-wrapped OAuth/identity chart |
| `Database` | Attached | same as `Redis` | `provider-helm` `Release` of a platform-wrapped Postgres (or similar) chart |
| `Queue` | Attached | same as `Redis` | `provider-helm` `Release` of a platform-wrapped queue (e.g. RabbitMQ) chart |
| `SecretStore` | Attached, auto-provisioned | one per **(app, cluster)** — shared across that app's envs on the same cluster — created by the first `ApplicationEnvironment` for that pair, never developer-selected | Infisical project + a `ClusterSecretStore` scoped to this app's namespaces — see item 8 |

ConfigMap and HPA are deliberately *not* on this list — they're `values.yaml` fields,
§3 has the schema.

## Open questions for discussion

**Resolved this round**:

- Crossplane v2 confirmed as the target (Terminology section) — namespaced XRs
  directly, no separate Claim type.
- **Zero K8s write credentials for Backstage, anywhere** (§0, revised) — every XR,
  Bootstrap or Attached, is a git commit; Bootstrap-tier lands in
  `gitops-cluster-<cluster>-tenants/<app>/xr-requests/`, synced into `app-<name>-cicd`
  by the same Application that already renders that namespace (sync-wave ordering, not
  a live namespace-creation call). Backstage authenticates only to
  `token-review-interceptor`'s existing `/github-installation-token` endpoint via a K8s
  ServiceAccount token that carries no resource-write RBAC at all.
- Attached-tier XRDs (Redis, `OAuthServer`, `SLO`) are independently selectable
  Backstage templates, same as Bootstrap-tier — confirmed, see item 7.
- `Database`/`Queue` added to the v1 catalog, same Component pattern as `Redis`.
- Secret vault question resolved to Infisical, self-hosted — see the revised item 8.
- ArgoCD's `Rollout` health check verified (below) — real, but not guaranteed bundled;
  needs explicit `argocd-cm` configuration, not an assumption.
- `ClusterAnalysisTemplate` confirmed as the primary path — custom app-specific
  `analysisTemplates:` entries should be the rare exception, not a co-equal option.

**ArgoCD `Rollout` health check — verified, with a real caveat**: ArgoCD's own repo
ships a Lua health script for `argoproj.io/Rollout`
(`resource_customizations/argoproj.io/Rollout/health.lua`) that correctly distinguishes
`Healthy`/`Progressing`/`Degraded`/`Suspended` — confirmed by fetching it directly.
**But this is not necessarily compiled into every ArgoCD version's binary the way
Deployment/StatefulSet/DaemonSet health checks are** — Argo Rollouts is a separate
project from Argo CD itself, and current guidance is to explicitly add this script to
`argocd-cm` under `resource.customizations.health.argoproj.io_Rollout` rather than
assume it's already active. Concrete action, not just a caveat: `idp-cluster-baseline`
should carry this `argocd-cm` entry explicitly, alongside installing the Argo Rollouts
controller itself — belongs in cluster config, not something to leave to chance per
cluster.
Sources: [Argo CD Resource Health docs](https://argo-cd.readthedocs.io/en/latest/operator-manual/health/), [How to Configure Health Checks for Argo Rollouts in ArgoCD](https://oneuptime.com/blog/post/2026-02-26-argocd-health-checks-argo-rollouts/view)

**Resolved 2026-08-13**: `idp-application`'s `rollout:` block is genuinely optional
(§3, chart built) — a standalone `infra`-type component (§ Components) reuses the exact
same chart, no second sibling chart needed.

**Still open:**

1. **Does a mature native Crossplane `provider-infisical` exist** (§ item 8), or does
   the `SecretStore` XRD's backend-configuration step need to go through
   `provider-terraform` wrapping Infisical's own Terraform provider instead? Not
   verified yet.
2. **What the platform's default canary step sequence should be** (§ Argo Rollouts) —
   `idp-application` should ship a sensible default (weights, pauses, which
   `ClusterAnalysisTemplate`(s) attach automatically) so most apps never need to specify
   `rollout.steps` at all, matching the "golden path, fully automated" goal, now
   sharpened by your confirmation that custom `analysisTemplates:` should be rare — not
   designed here. The chart (built 2026-08-13) ships a deliberately inert single-step
   placeholder in the meantime — see `charts/idp-application/README.md`.
3. The chart-architecture question raised this round (one `idp-application` chart vs.
   splitting pieces like ConfigMap out) — addressed in chat, not yet folded into a doc
   revision pending your read. (Built as one chart, per the original leaning — worth
   confirming this is still the intended resolution now that it's real code, not just
   the leaning.)
4. **The actual Crossplane API group for `components:`/`slos:` XRs** — not fixed
   anywhere yet (none of those XRDs exist). The chart uses a placeholder,
   `catalog.idp.io/v1alpha1`, one value to update once this is decided.
5. **The real namespace/labels that identify the ingress controller** (§3's
   `networkPolicy.allowIngressFromIngressController`) — no ingress controller has been
   installed or named anywhere in idp's docs yet. The chart defaults to the
   `ingress-nginx` project's conventional namespace label as a placeholder.
