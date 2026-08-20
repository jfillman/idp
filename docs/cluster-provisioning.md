# Cluster provisioning: template repo + generator script

**Status: built and structurally verified 2026-08-19.** Closes the gap
`gitops-strategy.md` §1 flagged and left unsolved: "N repos to provision instead of
one — mitigate with a repo-scaffolding script once the logical-group directory shape
(§3) is finalized; not solved by this doc." The logical-group shape has been live for
both real clusters since, and had already visibly diverged between them by hand
(`gitops-cluster-kind-prod`'s own README documents deliberately omitting
`provider-github`, scoping `idp-service-catalog` to Attached-tier XRDs only, and
picking up Argo Rollouts/Contour/observability on different days than `gitops-cluster-
dev` did) — exactly the failure mode a scaffolding script exists to close off.

## What it is

A new repo, `gitops-cluster-template`, marked as a GitHub template repository.
Standing up cluster N is: use the template, copy `cluster.yaml.example` to
`cluster.yaml` and fill in a small declarative config, run
`hack/customize-cluster.sh`, then follow the printed next steps (commit/push, register
in `gitops-cluster-dev`'s cluster registry, run the real bootstrap sequence). See the
template repo's own `README.md` for the exact usage steps — this doc covers the
*design*, not the walkthrough.

Two inputs are now first-class, validated, part of that one config file, rather than
implicit or hand-diffed against the last cluster's repo:

- **`type: dev | upper`** — already existed as a concept (the `cluster-registry`
  `ConfigMap`'s own `type` field, `gitops-cluster-dev/00-bootstrap/cluster-registry/`),
  now gates real, enforced invariants at generation time instead of being something a
  human has to remember to keep consistent by hand.
- **`components:`** — a full accounting of every optional logical-group subtree
  (`crossplane.providerGithub`, `certManager`, `externalSecrets`, `secrets.
  infisicalHost`, `argoRollouts`, `contour`, `sloth`, `serviceCatalog.{enabled,scope}`,
  `observability`, `platformCicd`), replacing the prose "deliberately scoped, not a
  full mirror" sections both existing repos' READMEs carry with something the script
  itself enforces.

## Mechanism: literal-string substitution, not a templating engine

Deviates from the original plan's `__TOKEN__`-placeholder sketch in one respect,
discovered while building it: the template repo's files are direct, unedited copies
of the real `gitops-cluster-dev`/`gitops-cluster-kind-prod` manifests (every
`Application`'s own `repoURL` self-reference, the literal cluster name baked into
Infisical project slugs, etc.) — inserting synthetic tokens into dozens of files by
hand would have been more work and more error-prone than treating the *real* literal
values already in those files as the substitution targets. `hack/customize-cluster.sh`
does exactly that: a small, explicit table of known literal strings this specific
curated template happens to contain (`gitops-cluster-dev`/`gitops-cluster-kind-prod`
as repo self-references, `kind-dev`/`kind-prod` as cluster-name self-references,
`gitops-cluster-dev-tenants` as the tenants-repo self-reference) mapped to the new
cluster's real values, applied via `grep -F` + `sed` across every file except
`hack/` itself.

**That last exclusion is a real bug this caught, not a hypothetical one.** The first
working version of the script swept `hack/` too, which meant a still-running script
rewrote its own source file on disk mid-execution — confirmed live to actually
corrupt it (a second `substitute()` call matched text a first call had already
rewritten inside the script's own comments, e.g. `gitops-cluster-dev-tenants`
becoming `gitops-cluster-dev22-tenants` on a test run). Fixed by excluding `hack/`
from the sweep entirely and handling `hack/kind-config.yaml`'s one real per-cluster
field (the kind cluster's own `name:`, derived from `clusterName` by stripping its
`kind-` prefix) as a separate, explicit, non-self-referential substitution.

## SCM + registry host/owner — added after a gap the user caught

The first version left `https://github.com/jfillman/...` and
`ghcr.io/jfillman/function-rollout-watcher` hardcoded throughout — every repoURL
self-reference already got its *repo name* substituted, but not the GitHub owner or
host in front of it, and the one org-owned container image wasn't touched at all.
`cluster.yaml` now has `scm.{host,owner}` and `registry.{host,owner}`, substituted via
the same literal-string mechanism, scoped to the exact `github.com/jfillman` /
`ghcr.io/jfillman` literals (with and without the `https://` prefix, to also catch
prose mentions) — **not** a bare `github.com` replace, which would have wrongly
rewritten this template's own genuinely-third-party vendored repoURLs (`10-crds-
operators/sloth/application.yaml`'s real `https://github.com/slok/sloth.git`, plus
comment mentions of `github.com/argoproj/argo-cd` and `github.com/crossplane-contrib/*`
— confirmed live these exist and stay untouched). A bare `jfillman` catch-all runs
last, after the more specific rules have already consumed the URL forms, to pick up
what's left (`argocd-repo-creds-jfillman`'s own resource-name convention,
`provider-github-config.yaml`'s credential-JSON example comment). Re-verified with a
adversarial test run using a different host entirely (a fake `gitlab.example.com`) —
substituted correctly, `sloth`'s real upstream repoURL confirmed unchanged.

## Hard invariants — refused, not silently corrected

Two rules this catalog already treats as permanent architecture, not per-cluster
taste (`idp/docs/service-catalog-design.md` §0 "Where Crossplane runs across a
multi-cluster fleet"): Bootstrap-tier XRDs (`NodeJSApplication`/
`ApplicationEnvironment`, pure `provider-github` writers) and `platform-cicd`'s own
control plane stay centralized on the fleet's one dev cluster permanently, regardless
of fleet size. `hack/customize-cluster.sh` refuses to proceed — exits non-zero before
writing anything — if `type: upper` is combined with
`components.crossplane.providerGithub: true`, `components.platformCicd: true`, or
`components.serviceCatalog.scope: full`. Confirmed live: a deliberately-invalid config
was rejected before any file was touched, not partway through.

Lower/ephemeral environments (`gitops-strategy.md` §10, a real security boundary) are
the same category — the `tenant-appprojects` chart's `appproject-lower.yaml`/
`lower-envs-applicationset.yaml` templates are pruned outright on `type: upper`,
matching `gitops-cluster-kind-prod`'s own real absence of them, rather than shipped
inert.

## What's deliberately excluded from the template

- **Per-cluster secret/identity material that must never be reused across clusters** —
  caught during a secret-hygiene pass while building this: the template's first draft
  had literally copied `gitops-cluster-dev`'s own real, live `values-kind-dev.yaml`
  (its actual Fulcio root cert + API-server CA) and the real openssl-generated
  Infisical Postgres/Redis passwords straight from that cluster's committed config.
  Both removed. `values-<cluster>.yaml` is left for the operator to produce via
  `platform-cicd/hack/generate-cluster-values.sh` (ADR-0006's own tool, built for
  exactly this — refuses to reuse trust material across clusters). The Infisical
  Postgres/Redis passwords are generated fresh per run (`openssl rand -hex 20`,
  matching that file's own documented method) rather than reused from `kind-dev`'s
  real instance.
- **`idp-cluster-baseline`** — out of scope per your call; the template packages
  today's real per-component pattern, not the not-yet-built shared-chart layer
  `gitops-strategy.md` §8 designed.
- **AI-triage / HolmesGPT** (`gitops-cluster-kind-prod/30-ai-triage/`) — not modeled as
  a `components.*` toggle yet, since that mechanism is still only partially designed
  platform-wide. Real, known gap — see the template's own README.

## Verification

Structural: the script was run twice against fresh template checkouts — once with a
`type: dev`, all-components-on config, once with a `type: upper`,
`serviceCatalog.scope: attached-tier-only` config matching `gitops-cluster-kind-prod`'s
real settings — and the resulting file trees diffed against the two real repos. Both
diffs reduced to only expected, understood deltas (the `providers.yaml` → `provider-
kubernetes.yaml`/`provider-github.yaml` split, `values-<cluster>.yaml` deferred to
`generate-cluster-values.sh`, README files the real repos happen not to carry, the
known AI-triage gap, and `gitops-cluster-kind-prod` never having installed
`cert-manager` at all — a real gap in that live cluster, not a template flaw). The
hard-invariant guard was confirmed live to reject a deliberately-invalid config and
exit before writing anything.

**Not done this pass**: bootstrapping an actual third cluster from the template — the
structural diff against the two real repos is the verification target for the
template/script themselves; standing up a real `kind-staging` is a natural next step
but a separate, deliberately-not-automated action (real cluster creation).

## Cluster API on Crossplane — recommendation, not built

Raised alongside this work as a possible additional cluster-onboarding path (Claim a
cluster, get a real Kubernetes API back). Checked `crossplane-contrib/provider-capi`
live: real, but early-stage (20 GitHub stars, 13 commits total, 2 open issues, minimal
recent activity) — not something to build real infrastructure on yet.

If/when this becomes a real task, prefer wrapping upstream Cluster API CRDs
(`Cluster`, `KubeadmControlPlane`, `MachineDeployment`, an infra-provider object like
`DockerCluster` for CAPD) directly via `provider-kubernetes` + a Composition Function
— the same mechanism this catalog already uses for `SLO`/Sloth (native resources, no
bespoke thin provider in between) — rather than depending on `provider-capi`'s current
maturity level. A `KubernetesCluster` XRD built this way would be a natural
**producer** for this doc's own template+script: Claim a cluster, the Composition
stands up CAPI resources, and once the workload cluster's API is reachable,
`hack/customize-cluster.sh` + the bootstrap sequence take over. Worth designing as its
own follow-on task now that there's a real bootstrap target to hand off to — nothing
to hand off to before this template existed.
