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

## §1. `provider-github` is the mechanism behind every "create/commit to a repo" step

Category-1 XRDs (§0) need to create a GitHub repo and commit files into it, without a
publish/package step and without a full clone-commit-push flow. Upbound's official
`provider-github` (wraps the Terraform GitHub provider) does this as plain managed
resources: `Repository` (create the repo), `RepositoryFile` (create/update a single
file via GitHub's Contents API — this is what writes boilerplate files, `cicd.yaml`, a
tenant `identity.yaml`-equivalent, or an env's `values.yaml`), `BranchProtection`
(required checks/reviewers, matching what `platform-cicd` already enforces on
`gitops-<app-name>` today). One GitHub App credential (same shape as the one
`platform-cicd`'s `token-review-interceptor` already mints per-repo tokens from) backs
the `ProviderConfig` — no new credential class, reuses the existing one.

## §2. Composition authoring: Composition Functions, not pure patch-and-transform

Rendering a language-specific boilerplate file set (multiple files, conditional
content based on `nodeVersion`/`packageManager`/etc.) is real logic, not a field-by-field
patch — patch-and-transform alone gets unwieldy fast for this. Recommend a pipeline of
`function-patch-and-transform` for the simple field-mapping parts (XR spec → managed
resource spec) plus a dedicated function (KCL or Go templating) for the file-rendering
parts, shared across `NodeJSApplication` and `SpringBootApplication` rather than
duplicated — one function, parameterized by stack, both XRDs' Compositions call into it.

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
analysisTemplates:          # Embedded — app-specific custom AnalysisTemplates, see § below.
  - name: checkout-conversion-rate
    metrics: [...]
env: [{name: FOO, value: bar}]              # Embedded
configMaps:                 # Embedded — revised this round, see "Rendering mechanism" below
  - name: app-settings
    data: {app-config.yaml: "...", logging.yaml: "..."}
  - name: feature-flags
    data: {flags.json: "..."}
secrets: [{name: db-password, key: DB_PASSWORD}]        # Embedded — same appSecretStores/ESO
                                                          # mechanism platform-cicd already built,
                                                          # not reinvented here — unlike ConfigMap,
                                                          # NOT revised to a multi-object shape, see below
autoscaling: {enabled: false, min: 2, max: 10, targetCPUPercent: 70}  # Embedded — scaleTargetRef.kind: Rollout
podDisruptionBudget: {enabled: false}                                 # Embedded
ingress: {enabled: true, host: my-app.example.com}                    # Embedded
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

**Open design question this doc doesn't resolve**: should `rollout:` (the workload
block) be genuinely optional (so an `appType: infra` release with only a `components:`
block — e.g. a standalone Redis with no app code at all — is a valid, normal use of this
same chart), or does infra-only deployment want a separate, lighter sibling chart with
no workload-shaped fields at all? Leaning toward "optional in the same chart" for
mechanism reuse, flagged for confirmation in Components below.

---

## Item 1/2: `NodeJSApplication` / `SpringBootApplication`

**Separate XRDs, not one generic `Application` XRD with a language field.** A discriminated-
union schema would technically work, but goal 8's own framing — one XRD becomes one
Backstage template — argues for it directly: a developer picking "New Service" wants two
distinct, clearly-labeled template cards, not a generic form with a language dropdown
buried inside. Matches the existing memory note on this (favor XRD designs that "read
cleanly as a Backstage template input" over internally-convenient ones). The repo-
creation/CICD-onboarding logic that's ~90% identical across languages lives in the
shared Composition Function from §2, not duplicated per XRD.

**Scope, deliberately narrow**: src repo + boilerplate + an *empty, scaffolded*
`gitops-<app-name>` repo + CICD onboarding (commits an `identity.yaml`-equivalent into
`gitops-cluster-dev-tenants`, giving the app its dev-cluster CI pipeline and, per §10 of
`gitops-strategy.md`, its lower-env self-service surface). **Not** upper-env
provisioning — that's item 3. This split maps directly onto the lower/upper security
boundary already designed: everything these two XRDs do is inherently dev-cluster,
self-service, no-review-gate-needed territory; promoting to a real environment is a
deliberately separate, higher-trust action.

**Why `gitops-<app-name>` gets created here, empty, rather than by item 3**: its
lifecycle is app-level (create once), not env-level (create per cluster×env) — creating
it alongside the src repo, at the one moment both are being bootstrapped together, avoids
a "which of possibly-several env Claims owns the shared repo" ownership question with no
clean answer under Crossplane's per-Claim resource ownership model.

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

## Item 4: `SLO` (narrowed from SLA/SLO/SLI)

**Start with one XRD, `SLO`, not three.** SLI (the measured indicator — e.g. p99
latency) is naturally just a field *within* an SLO spec (which query defines success),
not an independent concept worth its own XRD. SLA (an external, often contractual
commitment, sometimes aggregating multiple SLOs with consequences attached) is a
reporting/business layer that can be built later, on top of SLOs that already exist —
nothing about building `SLO` first forecloses adding an `SLAReport` XRD afterward.

**Don't reinvent SLO-to-alerting-rule translation — wrap an existing tool.**
[Sloth](https://sloth.dev) (or OpenSLO/Pyrra, same space) already solves "SLO spec →
multiwindow-multi-burn-rate Prometheus recording/alerting rules" correctly; the `SLO`
XRD's Composition should generate whatever CR that tool consumes (or generate
`PrometheusRule` directly if going dependency-free), not hand-roll the burn-rate math in
a Composition Function.

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

**Still open:**

1. **Does `idp-application`'s `rollout:` block need to be genuinely optional**
   (§3) — this is what lets a standalone `infra`-type component (§ Components) reuse the
   exact same chart instead of needing a second, workload-less sibling chart.
2. **Does a mature native Crossplane `provider-infisical` exist** (§ item 8), or does
   the `SecretStore` XRD's backend-configuration step need to go through
   `provider-terraform` wrapping Infisical's own Terraform provider instead? Not
   verified yet.
3. **What the platform's default canary step sequence should be** (§ Argo Rollouts) —
   `idp-application` should ship a sensible default (weights, pauses, which
   `ClusterAnalysisTemplate`(s) attach automatically) so most apps never need to specify
   `rollout.steps` at all, matching the "golden path, fully automated" goal, now
   sharpened by your confirmation that custom `analysisTemplates:` should be rare — not
   designed here.
4. The chart-architecture question raised this round (one `idp-application` chart vs.
   splitting pieces like ConfigMap out) — addressed in chat, not yet folded into a doc
   revision pending your read.
