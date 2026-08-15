# idp — Dream IDP

An API-centric, event-driven Internal Developer Platform: Crossplane as the custom
control plane, ArgoCD/GitOps for delivery, Backstage as the single pane of glass,
`platform-cicd` (`../platform-cicd`) as the CI/CD component underneath it. AI-assisted at
two levels — API clients driving self-service provisioning, and workflows embedded at
the control-plane layer for things like automated triage of a degraded deployment.

Core goals, in full: [[project_dream_idp]] in `platform-cicd`'s memory has the running
history; this repo is where the design and implementation actually live.

## Status

Design resolved (`docs/gitops-strategy.md`, `docs/service-catalog-design.md`), build
started 2026-08-12/13.

**Phase 1 done and live-verified**: `gitops-cluster-dev` built per `gitops-strategy.md`
§3's group structure and fully rebuilt from scratch under its own GitOps management
(Calico CNI, replacing kind's default `kindnet` — confirmed live that it silently
ignores NetworkPolicy entirely). `10-crds-operators/` (Crossplane + providers/functions,
cert-manager, external-secrets, Argo Rollouts, Contour, Sloth) and a full
`40-observability/` stack (Prometheus/Grafana/Thanos/Loki/Tempo/MinIO/otel-collector,
mirroring `kind-observe`'s own design) are real and ArgoCD-managed — see that repo's
own README for exactly what's adopted vs. documented-only.

**Naming note**: `gitops-cluster-dev` targets the `kind-dev` kubectl context — kept
deliberately separate from `kind-observe`, `platform-cicd`'s own live Tekton dev
cluster, to avoid touching that running pipeline while this project's own cluster gets
wiped/rebuilt repeatedly.

Before Phase 1 started, discovered `ai-rollout` — a real, previously-unrecorded
AI-diagnosed-canary-rollback prototype already live on `kind-observe`, a working
implementation of goal 9's AI-triage idea. Folded into this plan (original left
untouched) — see [[idp_session_build_phase1]] in memory.

**Phase 2 — done and live-verified 2026-08-13**:
- `ai-rollout`'s AI-triage mechanism moved into `idp-service-catalog` for real,
  redesigned so the Composition Function hands investigation off to a shared HolmesGPT
  service instead of running a bespoke per-app Claude agent — `diagnosis-holmes-dispatch`
  holds no credentials at all. Proven end-to-end: a real broken canary rollout on
  `kind-dev` produced a real diagnosis and a real fix PR,
  [jfillman/idp#8](https://github.com/jfillman/idp/pull/8). kagent (an alternative AI
  backend) evaluation tabled for later.
- The real `idp-application` Helm chart, then `widget-api` migrated onto it for real
  (off `ai-rollout`'s own standalone Composition) — full live test pass (NetworkPolicy
  enforcement, ServiceMonitor scraping, a real canary + Prometheus-backed AnalysisRun,
  checksum-triggered revisions). See
  `idp-service-catalog/charts/idp-application/README.md` for the two real bugs found
  and fixed along the way.
- The `SLO` XRD + Composition (item 4) — first Attached-tier XRD in the catalog, wraps
  Sloth (sloth.dev) rather than hand-rolling multi-window-multi-burn-rate PromQL. Found
  a real Crossplane v2.3.4 bug doing it (a second Function pointing at an
  already-installed package corrupts the package-manager's dependency-lock graph
  cluster-wide) — see `idp-service-catalog/README.md`.
- `gitops-cluster-dev/20-service-catalog` wired for real: `idp-service-catalog` tagged
  `v0.1.0`, pinned+synced via a real ArgoCD Application (same pattern already proven for
  `10-crds-operators`/`40-observability`) — closes the last "hand `kubectl apply` only"
  gap, live-verified end-to-end (a real SLO XR → Sloth → a real `PrometheusRule`).

**Phase 3, starting now**: rest of the v1 service catalog. Tracing `NodeJSApplication`'s
own dependencies first surfaced a real prerequisite gap: `ApplicationEnvironment` needs
somewhere real to commit an onboarding entry (`gitops-strategy.md` §5/§6's
tenant-onboarding `ApplicationSet` + per-app `AppProject`), and neither existed. That
plumbing is now real and live-verified 2026-08-13 —
`gitops-cluster-dev/02-argocd-apps/` (`tenant-appprojects` + `tenant-onboarding`
`ApplicationSet`s, reading the newly-bootstrapped `gitops-cluster-dev-tenants` repo),
built inside the existing single `01-argocd` instance rather than standing up a real
second `argocd-apps` instance (that split stays deferred to its own Phase 4 task).
Verified with a throwaway tenant, including a live `AppProject` boundary rejection
(`InvalidSpecError`, same mechanism already proven in `platform-cicd`) — see
`gitops-cluster-dev`'s own README for the real deletion-ordering bug this surfaced
(pruning an `AppProject` before its dependent `Application`'s own finalizer finishes
permanently stuck that `Application`). **Fixed and live-verified 2026-08-15**: a
`protection.crossplane.io` `Usage`, composed by `ApplicationEnvironment`'s own
Composition, blocks `NodeJSApplication` deletion (via a real, already-installed
admission webhook, not a finalizer hang) while any referencing env still exists — see
`docs/service-catalog-design.md` §0 for the full mechanism, `idp-service-catalog`
`v0.3.2`.

First XRD up next — `NodeJSApplication`, the first Bootstrap-tier XRD (§ Framework's
`provider-github` mechanism, §1/§2 of `service-catalog-design.md`). Its own
CICD-onboarding-commit step (giving a new app a real dev-cluster Tekton pipeline) stays
explicitly stubbed/deferred — that depends on migrating `platform-cicd`'s control plane
onto `kind-dev`, investigated this same session and found to be a genuine multi-day
effort (~300+ objects, a hardcoded per-cluster Fulcio CA, an undocumented secret, a
replace-vs-coexist decision against `kind-observe`) — its own separate future task, not
bundled into the XRD build. `SpringBootApplication`, `ApplicationEnvironment`, and the
Component XRDs (Redis, `OAuthServer`, Database, Queue, `SecretStore`) follow.

**Phase 4**: the two-ArgoCD-instance split + ArgoCD self-management (deliberately last
— both touch the instance currently running every live `platform-cicd` pipeline).
Backstage integration (goal 7) not started.

## Repos

```
idp                        this repo — docs + running status
gitops-cluster-dev          kind-dev's cluster config (Phase 1 done, live-verified)
gitops-cluster-dev-tenants  kind-dev's app-onboarding requests (not started — needed for Phase 3's Bootstrap-tier XRDs)
idp-cluster-baseline        shared cluster-config chart(s) (not started)
idp-service-catalog         Crossplane XRDs/Compositions + the idp-application chart, tagged v0.1.0, pinned+synced via ArgoCD
```

## Repo layout (so far)

```
docs/   design docs, written as decisions land — same convention as platform-cicd/docs/
```

## Design language

Shares `platform-cicd`'s conventions rather than inventing new ones: `platform.io/*`
label namespace, `<type>-<app-name>-<env>` namespace pattern, kebab-case docs, the
"never a live cross-cluster API call, only a git commit" credential posture. Deviations
get called out explicitly where they happen, not silently.
