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


def reactivate_salon(salon_id: str):
    url = f"{SUPABASE_URL}/rest/v1/salons?id=eq.{salon_id}"
    data = json.dumps({"is_active": True}).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={**supabase_headers(), "Prefer": "return=minimal"},
        method="PATCH"
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


LINE_ADMIN_USER_ID = os.environ.get("LINE_ADMIN_USER_ID", "")

# ── LINE push（管理者のみ） ────────────────────────────────────
def line_push_admin(text: str):
    to = LINE_ADMIN_USER_ID
    if not to:
        return -1
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push",
        data=json.dumps({"to": to, "messages": [{"type": "text", "text": text}]}).encode(),
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


def handle_subscription_created(obj: dict):
    if not _is_toukosan_product(obj.get("items", {}).get("data", [])):
        return
    customer_id = obj.get("customer", "")
    subscription_id = obj.get("id", "")
    amount = obj.get("items", {}).get("data", [{}])[0].get("price", {}).get("unit_amount", 0)
    line_push_admin(
        f"🎉 とうこさん 新規サブスク！\n\n"
        f"Stripe顧客ID: {customer_id}\n"
        f"サブスクID: {subscription_id}\n"
        f"金額: ¥{amount:,}/月\n\n"
        f"Threadsの認証URLをクライアントに送ってください：\n"
        f"https://saas.shikisai.work/api/connect"
    )


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
    line_push_admin(
        f"🔴 とうこさん サービス停止\n\n"
        f"サロン: {salon['salon_name']}\n"
        f"Stripe: {customer_id}\n\n"
        f"Stripeのサブスクリプションが削除されたため、Supabaseの is_active を自動で false にしました。"
    )


def handle_payment_failed(obj: dict):
    lines = obj.get("lines", {}).get("data", [])
    if not _is_toukosan_product(lines):
        return
    subscription_id = obj.get("subscription", "")
    customer_id = obj.get("customer", "")
    salon = get_salon_by_subscription(subscription_id, customer_id)
    if not salon:
        return
    attempt = obj.get("attempt_count", 0)
    if isinstance(attempt, int) and attempt >= 3:
        deactivate_salon(salon["id"])
        line_push_admin(
            f"🔴 とうこさん 自動停止（支払い失敗 {attempt}回）\n\n"
            f"サロン: {salon['salon_name']}\n\n"
            f"Stripeのサブスクは継続中のため、入金されれば自動で投稿再開します。\n\n"
            f"https://dashboard.stripe.com"
        )
    else:
        line_push_admin(
            f"⚠️ とうこさん 支払い失敗（{attempt}回目）\n\n"
            f"サロン: {salon['salon_name']}\n\n"
            f"Stripeが自動リトライします。3回失敗でサービス自動停止。\n\n"
            f"https://dashboard.stripe.com"
        )


def handle_invoice_paid(obj: dict):
    lines = obj.get("lines", {}).get("data", [])
    if not _is_toukosan_product(lines):
        return
    subscription_id = obj.get("subscription", "")
    customer_id = obj.get("customer", "")
    salon = get_salon_by_subscription(subscription_id, customer_id)
    if not salon:
        return
    if not salon.get("is_active", True):
        reactivate_salon(salon["id"])
        line_push_admin(
            f"✅ とうこさん 自動再開\n\n"
            f"サロン: {salon['salon_name']}\n\n"
            f"支払いが確認されたため、投稿を自動再開しました。"
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

        if etype == "customer.subscription.created":
            handle_subscription_created(obj)
        elif etype == "customer.subscription.deleted":
            handle_subscription_deleted(obj)
        elif etype == "invoice.payment_failed":
            handle_payment_failed(obj)
        elif etype == "invoice.paid":
            handle_invoice_paid(obj)
