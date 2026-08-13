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
started 2026-08-12/13. **Phase 1 done and live-verified**: `gitops-cluster-dev` (the
cluster-config repo for `kind-observe`) built per `gitops-strategy.md` §3's group
structure; `10-crds-operators/` (Crossplane + its providers/functions, cert-manager,
external-secrets) adopted under ArgoCD non-destructively — see that repo's own README
for exactly what's adopted vs. documented-only so far, the split is real.

Before Phase 1 started, discovered `ai-rollout` — a real, previously-unrecorded
AI-diagnosed-canary-rollback prototype already live on `kind-observe`, a working
implementation of goal 9's AI-triage idea. Folded into this plan (original left
untouched) — see [[idp_session_build_phase1]] in memory.

**Phase 2 (next)**: fold `ai-rollout`'s mechanism in for real (generalize
`function-rollout-watcher`/`diagnosis-job` beyond its current single-repo demo), build
the `idp-application` Helm chart, prove the first Bootstrap-tier XRD end-to-end.
**Phase 3**: rest of the v1 service catalog. **Phase 4**: the two-ArgoCD-instance split
+ ArgoCD self-management (deliberately last — both touch the instance currently running
every live `platform-cicd` pipeline). Backstage integration (goal 7) not started.

## Repos

```
idp                        this repo — docs + running status
gitops-cluster-dev          kind-observe's cluster config (Phase 1, in progress)
gitops-cluster-dev-tenants  kind-observe's app-onboarding requests (Phase 2)
idp-cluster-baseline        shared cluster-config chart(s) (not started)
idp-service-catalog         Crossplane XRDs + the idp-application chart (Phase 2)
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
