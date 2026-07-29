"""widget-api: a tiny order-total calculator, used as the demo app for
AI-diagnosed canary rollbacks.

Deliberately has two independent ways to break it, matching two different
classes of real incident:

1. CONFIG break: MAX_ITEMS_PER_ORDER (etc.) come from environment variables
   sourced from a ConfigMap. They're read and parsed as int/float at import
   time. Put a non-numeric value in the ConfigMap and the process crashes on
   startup with a clear ValueError -- a config problem, fixed by reverting
   the config value, not by touching this file.

2. SOURCE break: /readyz calls calculate_total() with a fixed self-check
   input as a "does my own core logic still work" smoke test before
   declaring the pod ready. A bug introduced into calculate_total() (see
   break-demo-src-bug.sh) can make that self-check fail without breaking
   the config at all -- a genuine source-code problem, fixed by editing
   this file, not the ConfigMap or the Rollout manifest.
"""

import os

from flask import Flask, jsonify, request

app = Flask(__name__)


def _load_config():
    # Intentionally read as plain strings from the environment and parsed
    # here -- an invalid value (e.g. "lots" instead of a number) raises at
    # import time and crashes the process. See break-demo-config.sh.
    max_items = int(os.environ.get("MAX_ITEMS_PER_ORDER", "100"))
    discount_rate = float(os.environ.get("DISCOUNT_RATE", "0.1"))
    bulk_discount_enabled = os.environ.get("FEATURE_BULK_DISCOUNT", "true").strip().lower() == "true"
    return max_items, discount_rate, bulk_discount_enabled


MAX_ITEMS_PER_ORDER, DISCOUNT_RATE, FEATURE_BULK_DISCOUNT = _load_config()

# Fixed inputs for the /readyz self-check -- deliberately equal to
# MAX_ITEMS_PER_ORDER so an off-by-one bug in calculate_total()'s boundary
# check (> vs >=) trips the self-check specifically, without affecting
# every other request. See break-demo-src-bug.sh.
SELF_CHECK_ITEM_COUNT = MAX_ITEMS_PER_ORDER
SELF_CHECK_UNIT_PRICE = 1.0


def calculate_total(item_count, unit_price):
    """Core business logic: total price for an order, with a bulk discount
    applied above 10 items. This is THE function break-demo-src-bug.sh
    introduces a bug into."""
    if item_count >= MAX_ITEMS_PER_ORDER:
        raise ValueError(f"order of {item_count} exceeds max items ({MAX_ITEMS_PER_ORDER})")
    subtotal = item_count * unit_price
    if FEATURE_BULK_DISCOUNT and item_count >= 10:
        subtotal = subtotal * (1 - DISCOUNT_RATE)
    return round(subtotal, 2)


@app.get("/healthz")
def healthz():
    return jsonify(status="ok"), 200


@app.get("/readyz")
def readyz():
    # Self-check: exercise our own core logic with known-good inputs before
    # declaring ready. A bug in calculate_total() shows up here, not just
    # on arbitrary user requests -- matching how Argo Rollouts' canary
    # analysis in this project reacts to readiness/restarts, not to
    # per-request error rates.
    try:
        calculate_total(SELF_CHECK_ITEM_COUNT, SELF_CHECK_UNIT_PRICE)
    except Exception as exc:  # noqa: BLE001
        return jsonify(status="not ready", reason=str(exc)), 503
    return jsonify(status="ready"), 200


@app.get("/")
def info():
    return jsonify(
        service="widget-api",
        max_items_per_order=MAX_ITEMS_PER_ORDER,
        discount_rate=DISCOUNT_RATE,
        bulk_discount_enabled=FEATURE_BULK_DISCOUNT,
    )


@app.get("/quote")
def quote():
    item_count = int(request.args.get("items", "1"))
    unit_price = float(request.args.get("price", "10.0"))
    total = calculate_total(item_count, unit_price)
    return jsonify(item_count=item_count, unit_price=unit_price, total=total)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
