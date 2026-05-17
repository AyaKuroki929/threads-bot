"""
Stripe Webhook ハンドラ（とうこさん SaaS）
- customer.subscription.deleted → is_active=false + LINE通知
- invoice.payment_failed       → LINE警告通知（即停止はしない）
"""
from http.server import BaseHTTPRequestHandler
import hashlib, hmac, json, os, time, urllib.request, urllib.parse

STRIPE_WEBHOOK_SECRET  = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
LINE_TOKEN   = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")

TOUKOSAN_PRODUCT_ID = "prod_UWa5BZv291uQts"  # とうこさん専用商品ID


# ── Stripe 署名検証 ───────────────────────────────────────────
def verify_stripe_sig(payload: str, sig_header: str, secret: str) -> bool:
    try:
        pairs = {}
        for item in sig_header.split(','):
            k, _, v = item.partition('=')
            pairs[k] = v
        ts  = int(pairs.get('t', 0))
        sig = pairs.get('v1', '')
        if not ts or not sig:
            return False
        if abs(time.time() - ts) > 300:
            return False
        signed = f"{ts}.{payload}"
        expected = hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig)
    except Exception:
        return False


# ── Supabase ─────────────────────────────────────────────────
def supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def get_salon_by_subscription(subscription_id: str, customer_id: str):
    """subscription_id で照合（サービスをまたいだ誤検知を防ぐ）。
    未登録なら customer_id でフォールバック。"""
    for col, val in [("stripe_subscription_id", subscription_id), ("stripe_customer_id", customer_id)]:
        if not val:
            continue
        url = (f"{SUPABASE_URL}/rest/v1/salons"
               f"?{col}=eq.{urllib.parse.quote(val)}"
               f"&select=id,salon_name,is_active&limit=1")
        req = urllib.request.Request(url, headers=supabase_headers())
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                rows = json.loads(r.read())
                if rows:
                    return rows[0]
        except Exception:
            pass
    return None


def deactivate_salon(salon_id: str):
    url = f"{SUPABASE_URL}/rest/v1/salons?id=eq.{salon_id}"
    data = json.dumps({"is_active": False}).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={**supabase_headers(), "Prefer": "return=minimal"},
        method="PATCH"
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


# ── LINE broadcast ────────────────────────────────────────────
def line_broadcast(text: str):
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/broadcast",
        data=json.dumps({"messages": [{"type": "text", "text": text}]}).encode(),
        headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except Exception:
        return -1


# ── イベント処理 ──────────────────────────────────────────────
def _is_toukosan_product(items_data: list) -> bool:
    for item in items_data:
        if item.get("price", {}).get("product") == TOUKOSAN_PRODUCT_ID:
            return True
    return False


def handle_subscription_deleted(obj: dict):
    # とうこさん商品以外は無視（うらかたさん等の混入を防ぐ）
    if not _is_toukosan_product(obj.get("items", {}).get("data", [])):
        return
    subscription_id = obj.get("id", "")
    customer_id = obj.get("customer", "")
    salon = get_salon_by_subscription(subscription_id, customer_id)
    if not salon:
        return
    deactivate_salon(salon["id"])
    line_broadcast(
        f"🔴 とうこさん サービス停止\n\n"
        f"サロン: {salon['salon_name']}\n"
        f"Stripe: {customer_id}\n\n"
        f"Stripeのサブスクリプションが削除されたため、Supabaseの is_active を自動で false にしました。"
    )


def handle_payment_failed(obj: dict):
    # invoiceのline itemsからとうこさん商品か確認
    lines = obj.get("lines", {}).get("data", [])
    if not _is_toukosan_product(lines):
        return  # とうこさん以外（うらかたさん等）は無視
    subscription_id = obj.get("subscription", "")
    customer_id = obj.get("customer", "")
    salon = get_salon_by_subscription(subscription_id, customer_id)
    if not salon:
        return
    attempt = obj.get("attempt_count", "?")
    line_broadcast(
        f"⚠️ とうこさん 支払い失敗\n\n"
        f"サロン: {salon['salon_name']}\n"
        f"試行回数: {attempt}回目\n\n"
        f"Stripeが自動リトライします。全て失敗するとサービスが自動停止します。\n\n"
        f"https://dashboard.stripe.com"
    )


# ── HTTP ハンドラ ─────────────────────────────────────────────
class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Stripe Webhook OK")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        sig = self.headers.get("Stripe-Signature", "")

        if STRIPE_WEBHOOK_SECRET and not verify_stripe_sig(raw, sig, STRIPE_WEBHOOK_SECRET):
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Invalid signature")
            return

        self.send_response(200)
        self.end_headers()

        try:
            event = json.loads(raw)
        except Exception:
            return

        etype = event.get("type", "")
        obj   = event.get("data", {}).get("object", {})

        if etype == "customer.subscription.deleted":
            handle_subscription_deleted(obj)
        elif etype == "invoice.payment_failed":
            handle_payment_failed(obj)
