"""
WebNX store monitor.

Fetches a WebNX (WHMCS) store category page, parses the listed products
(name, stock qty, spec string, price), diffs against the last known
snapshot, and posts a Discord/Slack-compatible webhook message for any
change: new product listed, product removed, stock qty changed, or price
changed.

Usage:
    python monitor.py            # run once (intended to be scheduled)
    python monitor.py --loop     # run forever, sleeping interval_minutes between checks
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
SNAPSHOT_PATH = BASE_DIR / "snapshot.json"
LOG_PATH = BASE_DIR / "monitor.log"

SITE_ORIGIN = "https://clients.webnx.com"
SITE_LOGO = f"{SITE_ORIGIN}/assets/img/logo.png"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Discord brand colors, reused as embed side-stripe colors.
COLOR_NEW = 0x57F287       # green
COLOR_CHANGE = 0xFEE75C    # yellow
COLOR_REMOVED = 0xED4245   # red
COLOR_NEUTRAL = 0x5865F2   # blurple

CHANGE_STYLE = {
    "new": ("\U0001F195", "New listing"),
    "stock": ("\U0001F4E6", "Stock change"),
    "price": ("\U0001F4B0", "Price change"),
    "removed": ("❌", "Removed / sold out"),
}


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    # Allow overriding secrets/settings via env vars (e.g. GitHub Actions
    # secrets) instead of committing them into config.json.
    if os.environ.get("WEBHOOK_URL"):
        config["webhook_url"] = os.environ["WEBHOOK_URL"]
    if os.environ.get("STORE_URL"):
        config["url"] = os.environ["STORE_URL"]
    if os.environ.get("DISCORD_PING_USER_ID"):
        config["discord_ping_user_id"] = os.environ["DISCORD_PING_USER_ID"]
    return config


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    line = f"[{stamp}] {message}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def fetch_products(url: str) -> dict:
    """Return {product_id: {name, qty_text, qty, price, specs, order_url}}."""
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    products = {}
    for node in soup.select("div.product.clearfix"):
        pid = node.get("id", "").replace("product", "").strip()
        name_el = node.select_one("header span[id$='-name']")
        qty_el = node.select_one("header span.qty")
        price_el = node.select_one(".product-pricing .price")
        order_el = node.select_one("a.btn-order-now")
        spec_el = node.select_one(".product-desc span[title]")

        name = name_el.get_text(strip=True) if name_el else None
        qty_text = qty_el.get_text(strip=True) if qty_el else None
        qty_match = re.search(r"\d+", qty_text or "")
        qty = int(qty_match.group()) if qty_match else None
        price = price_el.get_text(strip=True) if price_el else None
        order_url = order_el.get("href") if order_el else None
        specs = spec_el.get("title") if spec_el else None

        if not pid or not name:
            continue

        products[pid] = {
            "name": name,
            "qty_text": qty_text,
            "qty": qty,
            "price": price,
            "specs": specs,
            "order_url": order_url,
        }
    return products


def load_snapshot() -> dict:
    if SNAPSHOT_PATH.exists():
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_snapshot(products: dict) -> None:
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2)


def diff_products(old: dict, new: dict) -> list:
    """Return a list of {type, name, detail, url} change records."""
    changes = []

    for pid, new_p in new.items():
        if pid not in old:
            changes.append({
                "type": "new",
                "name": new_p["name"],
                "detail": f"{new_p['qty_text'] or 'stock unknown'} — {new_p['price'] or 'price unknown'}",
                "url": new_p.get("order_url"),
            })
            continue

        old_p = old[pid]
        if old_p.get("qty") != new_p.get("qty"):
            changes.append({
                "type": "stock",
                "name": new_p["name"],
                "detail": f"{old_p.get('qty_text') or 'unknown'} → {new_p.get('qty_text') or 'unknown'}",
                "url": new_p.get("order_url"),
            })
        if old_p.get("price") != new_p.get("price"):
            changes.append({
                "type": "price",
                "name": new_p["name"],
                "detail": f"{old_p.get('price') or 'unknown'} → {new_p.get('price') or 'unknown'}",
                "url": new_p.get("order_url"),
            })

    for pid, old_p in old.items():
        if pid not in new:
            changes.append({
                "type": "removed",
                "name": old_p["name"],
                "detail": "No longer listed",
                "url": None,
            })

    return changes


def absolute_url(url: str) -> str:
    if not url:
        return None
    return url if url.startswith("http") else SITE_ORIGIN + url


def post_webhook(webhook_url: str, embed: dict, content: str = "", slack_text: str = None) -> None:
    if slack_text is None:
        slack_text = embed.get("description") or embed.get("title", "")
    if not webhook_url or "REPLACE_ME" in webhook_url:
        log("Webhook URL not configured — skipping notification: " + slack_text.replace("\n", " "))
        return

    # Discord renders "embeds"; Slack ignores it and falls back to "text".
    payload = {"content": content, "embeds": [embed], "text": slack_text}
    resp = requests.post(webhook_url, json=payload, timeout=15)
    if resp.status_code >= 300:
        log(f"Webhook post failed ({resp.status_code}): {resp.text[:300]}")


def send_webhook(webhook_url: str, category_label: str, changes: list, ping_user_id: str = None) -> None:
    has_new = any(c["type"] == "new" for c in changes)
    has_removed = any(c["type"] == "removed" for c in changes)
    if has_new:
        color = COLOR_NEW
    elif has_removed and all(c["type"] == "removed" for c in changes):
        color = COLOR_REMOVED
    else:
        color = COLOR_CHANGE

    fields = []
    for c in changes[:25]:
        emoji, label = CHANGE_STYLE[c["type"]]
        value = c["detail"]
        url = absolute_url(c.get("url"))
        if url:
            value += f"\n[Order now]({url})"
        fields.append({"name": f"{emoji} {label} — {c['name']}", "value": value, "inline": False})

    embed = {
        "title": f"WebNX Stock Update — {category_label}",
        "color": color,
        "fields": fields,
        "thumbnail": {"url": SITE_LOGO},
        "footer": {"text": "WebNX Stock Monitor"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    slack_text = f"WebNX Stock Update — {category_label}\n" + "\n".join(
        f"[{CHANGE_STYLE[c['type']][1]}] {c['name']}: {c['detail']}" for c in changes
    )
    content = f"<@{ping_user_id}>" if ping_user_id else ""
    post_webhook(webhook_url, embed, content=content, slack_text=slack_text)


def send_scan_complete(webhook_url: str, category_label: str, product_count: int) -> None:
    embed = {
        "title": f"Scan complete — {category_label}",
        "description": f"No stock identified. {product_count} listing(s) currently up.",
        "color": COLOR_NEUTRAL,
        "thumbnail": {"url": SITE_LOGO},
        "footer": {"text": "WebNX Stock Monitor"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    post_webhook(webhook_url, embed)


def run_once(config: dict) -> None:
    url = config["url"]
    label = config.get("label", url)

    try:
        new_products = fetch_products(url)
    except requests.RequestException as e:
        log(f"Fetch failed: {e}")
        return

    old_products = load_snapshot()
    changes = diff_products(old_products, new_products)

    if changes:
        log(f"{len(changes)} change(s) detected for {label}:")
        for c in changes:
            log(f"  [{c['type']}] {c['name']}: {c['detail']}")
        send_webhook(config.get("webhook_url", ""), label, changes, config.get("discord_ping_user_id"))
    else:
        log(f"No changes ({len(new_products)} product(s) currently listed).")
        if not new_products:
            send_scan_complete(config.get("webhook_url", ""), label, len(new_products))

    save_snapshot(new_products)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true", help="Run continuously, sleeping between checks.")
    args = parser.parse_args()

    config = load_config()

    if args.loop:
        interval = max(1, int(config.get("interval_minutes", 5)))
        log(f"Starting loop, checking every {interval} minute(s). Ctrl+C to stop.")
        while True:
            run_once(config)
            time.sleep(interval * 60)
    else:
        run_once(config)


if __name__ == "__main__":
    main()
