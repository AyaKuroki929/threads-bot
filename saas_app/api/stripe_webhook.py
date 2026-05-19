"""
Stripe Webhook ハンドラ（とうこさん SaaS）
- checkout.session.completed   → クライアントにフォームURL自動送信 + 管理者通知
- customer.subscription.deleted → is_active=false + LINE通知
- invoice.payment_failed        → LINE警告通知（即停止はしない）
"""
import sys
from http.server import BaseHTTPRequestHandler
import hashlib, hmac, json, os, time, urllib.request, urllib.parse

STRIPE_WEBHOOK_SECRET  = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_SECRET_KEY      = os.environ.get("STRIPE_SECRET_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
LINE_TOKEN         = os.environ.get("ADMIN_NOTIFY_LINE_TOKEN", "")   # Claude通知bot（管理者用）
TOUKOSAN_LINE_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "") # とうこさんLINE bot（クライアント用）

TOUKOSAN_PRODUCT_ID  = "prod_UWa5BZv291uQts"
GOOGLE_FORM_URL      = "https://docs.google.com/forms/d/e/1FAIpQLSc4RAj_6O1nP6_9Ehm5FyLp_tFv4qgO3mQTUf2FHs9hsvz1cw/viewform"
CUSTOMER_ID_ENTRY    = "entry.1047073828"


def _log(msg: str):
    print(f"[stripe_webhook] {msg}", file=sys.stderr, flush=True)


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
            _log(f"signature timestamp too old: {ts} vs {time.time()}")
            return False
        signed = f"{ts}.{payload}"
        expected = hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig)
    except Exception as e:
        _log(f"verify_stripe_sig error: {e}")
        return False


# ── Supabase ─────────────────────────────────────────────────
def supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def get_salon_by_subscription(subscription_id: str, customer_id: str):
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
        except Exception as e:
            _log(f"get_salon_by_subscription error ({col}={val}): {e}")
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


# ── LINE ──────────────────────────────────────────────────────
def line_push_admin(text: str):
    """管理者（Claude通知bot）にbroadcast通知"""
    if not LINE_TOKEN:
        _log("line_push_admin: LINE_TOKEN not set")
        return -1
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/broadcast",
        data=json.dumps({"messages": [{"type": "text", "text": text}]}).encode(),
        headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            _log(f"line_push_admin: status={r.status}")
            return r.status
    except Exception as e:
        _log(f"line_push_admin error: {e}")
        return -1


def line_push_client(uid: str, text: str):
    """クライアント（とうこさんLINE bot）にpush通知"""
    if not TOUKOSAN_LINE_TOKEN or not uid:
        _log(f"line_push_client: token={bool(TOUKOSAN_LINE_TOKEN)}, uid={uid!r}")
        return -1
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push",
        data=json.dumps({"to": uid, "messages": [{"type": "text", "text": text}]}).encode(),
        headers={"Authorization": f"Bearer {TOUKOSAN_LINE_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            _log(f"line_push_client: uid={uid}, status={r.status}")
            return r.status
    except Exception as e:
        _log(f"line_push_client error: {e}")
        return -1


# ── Stripe API ───────────────────────────────────────────────
def get_stripe_customer(customer_id: str) -> dict:
    if not STRIPE_SECRET_KEY or not customer_id:
        return {}
    url = f"https://api.stripe.com/v1/customers/{customer_id}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {STRIPE_SECRET_KEY}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            _log(f"get_stripe_customer: name={data.get('name')!r}, email={data.get('email')!r}")
            return data
    except Exception as e:
        _log(f"get_stripe_customer error: {e}")
        return {}


# ── 商品チェック ─────────────────────────────────────────────
def _is_toukosan_product(items: list) -> bool:
    for item in items:
        for key in ("price", "plan"):
            val = item.get(key)
            if not isinstance(val, dict):
                continue
            product = val.get("product")
            # string IDの場合
            if isinstance(product, str) and product == TOUKOSAN_PRODUCT_ID:
                return True
            # 展開されたオブジェクトの場合
            if isinstance(product, dict) and product.get("id") == TOUKOSAN_PRODUCT_ID:
                return True
    return False


# ── イベント処理 ──────────────────────────────────────────────
def handle_checkout_session(obj: dict):
    """
    checkout.session.completed:
    - client_reference_id にLINE user IDが入っている場合、クライアントにフォームURLを自動送信
    - 管理者にも新規登録通知を送る（名前・メール・connect URLを含む）
    """
    line_uid    = obj.get("client_reference_id", "") or ""
    customer_id = obj.get("customer", "") or ""
    details     = obj.get("customer_details") or {}
    name        = details.get("name") or obj.get("customer_name") or ""
    email       = details.get("email") or obj.get("customer_email") or ""
    amount      = obj.get("amount_total", 0)

    _log(f"handle_checkout_session: line_uid={line_uid!r}, customer_id={customer_id!r}, name={name!r}, email={email!r}")

    # 名前・メールがなければStripe APIから取得
    if (not name or not email) and customer_id:
        cust  = get_stripe_customer(customer_id)
        name  = name  or cust.get("name",  "")
        email = email or cust.get("email", "")

    name  = name  or "不明"
    email = email or "不明"

    # クライアントにフォームURLを自動送信（customer_idをpre-fill）
    prefilled_form = f"{GOOGLE_FORM_URL}?{CUSTOMER_ID_ENTRY}={urllib.parse.quote(customer_id)}"
    if line_uid:
        client_msg = (
            f"サロン情報のご入力をお願いします📋\n\n"
            f"{prefilled_form}\n\n"
            f"ご記入後、担当者よりThreads連携URLをお送りします。"
        )
        line_push_client(line_uid, client_msg)
    else:
        _log("handle_checkout_session: LINE user ID not set (client used static payment link)")

    # 管理者通知（シンプル版 — フォーム回答時に詳細手順が届く）
    line_push_admin(
        f"🎉 とうこさん 新規登録！\n\n"
        f"名前: {name}\n"
        f"メール: {email}\n"
        f"金額: ¥{amount:,}/月\n\n"
        f"{'✅ フォームはLINEで自動送信済み' if line_uid else '⚠️ LINE ID不明 — 手動でフォームを送ってください：' + chr(10) + prefilled_form}"
    )


def handle_subscription_deleted(obj: dict):
    if not _is_toukosan_product(obj.get("items", {}).get("data", [])):
        return
    subscription_id = obj.get("id", "")
    customer_id = obj.get("customer", "")
    salon = get_salon_by_subscription(subscription_id, customer_id)
    if not salon:
        line_push_admin(
            f"⚠️ とうこさん サブスク解約（未登録ユーザー）\n\n"
            f"Stripe顧客ID: {customer_id}\n\n"
            f"Supabaseに対応するサロンが見つかりませんでした。\n"
            f"Threads認証を完了する前に解約した可能性があります。"
        )
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
    """invoice.paid: 既存サロンの支払い再開のみ処理。新規登録は checkout.session.completed で処理。"""
    lines = obj.get("lines", {}).get("data", [])
    _log(f"handle_invoice_paid: lines_count={len(lines)}, subscription={obj.get('subscription')!r}")

    if lines and not _is_toukosan_product(lines):
        _log("handle_invoice_paid: product not matched, skipping")
        return

    subscription_id = obj.get("subscription", "")
    customer_id     = obj.get("customer", "")
    salon = get_salon_by_subscription(subscription_id, customer_id)

    if not salon:
        _log("handle_invoice_paid: no salon found in Supabase (new registration — handled by checkout.session)")
        # checkout.session.completed が先に届いていない場合のフォールバック通知
        name  = obj.get("customer_name")  or ""
        email = obj.get("customer_email") or ""
        if (not name or not email) and customer_id:
            cust  = get_stripe_customer(customer_id)
            name  = name  or cust.get("name",  "")
            email = email or cust.get("email", "")
        name  = name  or "不明"
        email = email or "不明"
        amount = obj.get("amount_paid", 0)
        _log(f"handle_invoice_paid fallback notify: name={name!r}, email={email!r}")
        prefilled = f"{GOOGLE_FORM_URL}?{CUSTOMER_ID_ENTRY}={urllib.parse.quote(customer_id)}"
        line_push_admin(
            f"🎉 とうこさん 新規登録！（invoice.paid）\n\n"
            f"名前: {name}\n"
            f"メール: {email}\n"
            f"金額: ¥{amount:,}/月\n\n"
            f"⚠️ LINE ID不明 — 手動でフォームを送ってください：\n"
            f"{prefilled}"
        )
        return

    # 既存サロンの支払い再開
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
        """診断用エンドポイント: 環境変数の設定状態を返す"""
        self.send_response(200)
        self.end_headers()

        # とうこさんトークンのbot情報を取得して正しいチャンネルか確認
        toukosan_bot_info = {}
        if TOUKOSAN_LINE_TOKEN:
            try:
                req = urllib.request.Request(
                    "https://api.line.me/v2/bot/info",
                    headers={"Authorization": f"Bearer {TOUKOSAN_LINE_TOKEN}"},
                )
                with urllib.request.urlopen(req, timeout=5) as r:
                    toukosan_bot_info = json.loads(r.read())
            except Exception as e:
                toukosan_bot_info = {"error": str(e)}

        # Stripe webhook endpoint URLを確認
        stripe_webhooks = []
        if STRIPE_SECRET_KEY:
            try:
                req = urllib.request.Request("https://api.stripe.com/v1/webhook_endpoints?limit=10")
                req.add_header("Authorization", f"Bearer {STRIPE_SECRET_KEY}")
                with urllib.request.urlopen(req, timeout=5) as r:
                    data = json.loads(r.read())
                    stripe_webhooks = [
                        {"url": ep.get("url"), "status": ep.get("status"), "enabled_events": ep.get("enabled_events", [])}
                        for ep in data.get("data", [])
                    ]
            except Exception as e:
                stripe_webhooks = [{"error": str(e)}]

        # ?test=1 でサンプル管理者通知を送信
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        if qs.get("test") == ["1"]:
            sample_msg = (
                f"🎉 とうこさん 新規登録！\n\n"
                f"名前: テスト太郎\n"
                f"メール: test@example.com\n"
                f"金額: ¥2,750/月\n\n"
                f"✅ フォームはLINEで自動送信済み"
            )
            result = line_push_admin(sample_msg)
            self.wfile.write(json.dumps({"test_sent": True, "line_status": result}, ensure_ascii=False).encode())
            return

        status = {
            "ok": True,
            "stripe_webhook_secret": bool(STRIPE_WEBHOOK_SECRET),
            "stripe_secret_key": bool(STRIPE_SECRET_KEY),
            "stripe_webhooks": stripe_webhooks,
            "admin_line_token": bool(LINE_TOKEN),
            "toukosan_line_token": bool(TOUKOSAN_LINE_TOKEN),
            "toukosan_bot_basicId": toukosan_bot_info.get("basicId", ""),
            "toukosan_bot_displayName": toukosan_bot_info.get("displayName", ""),
            "supabase_url": bool(SUPABASE_URL),
            "supabase_key": bool(SUPABASE_KEY),
        }
        self.wfile.write(json.dumps(status, ensure_ascii=False, indent=2).encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        sig = self.headers.get("Stripe-Signature", "")

        if STRIPE_WEBHOOK_SECRET and not verify_stripe_sig(raw, sig, STRIPE_WEBHOOK_SECRET):
            _log("signature verification failed")
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Invalid signature")
            return

        self.send_response(200)
        self.end_headers()

        try:
            event = json.loads(raw)
        except Exception as e:
            _log(f"json parse error: {e}")
            return

        etype = event.get("type", "")
        obj   = event.get("data", {}).get("object", {})
        _log(f"received event: {etype}")

        if etype == "checkout.session.completed":
            handle_checkout_session(obj)
        elif etype == "customer.subscription.deleted":
            handle_subscription_deleted(obj)
        elif etype == "invoice.payment_failed":
            handle_payment_failed(obj)
        elif etype == "invoice.paid":
            handle_invoice_paid(obj)
        else:
            _log(f"unhandled event type: {etype}")
