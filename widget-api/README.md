# widget-api

The demo app for this project — a tiny Flask order-total calculator,
replacing the earlier `podinfo`-based demo. Unlike `podinfo` (a prebuilt
binary image with no source we control), this is source code we own, which
is the whole point: it lets the diagnosis agent open a fix PR against
*actual application code*, not just YAML.

## Build and load into kind

Same pattern as `diagnosis-job` — no registry, `kind load` straight into
containerd:

```bash
docker build -t widget-api:1.0.0 .
kind load docker-image widget-api:1.0.0 --name <your-kind-cluster-name>
```

`demo-app/application.yaml` already references `image: {repository:
widget-api, tag: "1.0.0"}` with the Rollout template's `imagePullPolicy:
IfNotPresent`, so no further config needed.

## Three ways to break this demo

Each one is a genuinely different root-cause class with a genuinely
different correct fix target — the point is exercising the diagnosis
agent's ability to tell them apart, not just detect "something's wrong."

| Scenario | Script | Root cause | Correct fix target |
|---|---|---|---|
| Image tag | `demo-app/break-demo-via-xr.sh` | Nonexistent image tag → ImagePullBackOff | GitOps manifest (`image.tag`) |
| Config | `demo-app/break-demo-config.sh` | Invalid ConfigMap value → crash on startup | GitOps manifest (`config.*`) |
| Source code | this directory's steps below | Logic bug → readiness self-check fails | App source repo (`app.py`) |

The first two are one-line `kubectl patch`es — see their own scripts. The
third is more involved, since it requires an actual code change, a real
image rebuild, and a real git commit for the agent to find via
`gh__list_commits`/`gh__get_commit` (the same pattern that worked well in
earlier testing against the image-tag scenario).

### Source-code break, step by step

This repo ships `bug-off-by-one.patch` — a real `git diff` (not
hand-written) that changes `calculate_total()`'s boundary check from `>` to
`>=`. Combined with `/readyz`'s self-check (which deliberately calls
`calculate_total()` with `item_count == MAX_ITEMS_PER_ORDER`), this makes
the self-check fail without touching config or the image tag at all.

```bash
# 1. In your actual GitOps repo checkout (where you pushed gitops-repo/widget-api/):
cd my-gitops-repo
git apply /path/to/argo-ai-canary/widget-api/bug-off-by-one.patch
git add widget-api/app.py
git commit -m "widget-api: adjust order limit boundary check"
git push

# 2. Build the buggy image and load it
cd widget-api
docker build -t widget-api:1.0.1-buggy .
kind load docker-image widget-api:1.0.1-buggy --name <your-kind-cluster-name>

# 3. Point the XR at the new tag (this itself should go through git too, for
#    consistency with how a real bad deploy reaches production — edit
#    demo-app/application.yaml's image.tag to "1.0.1-buggy", commit, push.
#    Or, for fast local iteration, patch directly:)
kubectl patch application widget-api -n demo-apps --type=merge -p '{
  "spec": {"parameters": {"image": {"tag": "1.0.1-buggy"}}}
}'
```

What should happen: canary pods come up and stay **Running**, but never
**Ready** (`/readyz` returns 503 because the self-check's
`calculate_total(100, 1.0)` now raises `ValueError` — 100 >= 100 under the
buggy `>=`). Same `progressDeadlineSeconds` → `Degraded` → diagnosis Job
path as the other two scenarios, but the evidence trail looks different:
no crash, no restart count climbing — just a pod stuck `0/1 Running` with a
specific 503 reason in its own `/readyz` response, which the agent would
need to correlate with the source code (not the config, not the image tag)
to diagnose correctly.

To revert: point the XR's `image.tag` back at `"1.0.0"`, the same way
`demo-app/fix-demo-via-xr.sh` does for the image-tag scenario.
