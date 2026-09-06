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
CUSTOMER_ID_ENTRY    = "entry.1831716486"

# ── スクール版フォーム（晶子サロンアカデミー等）──────────────
# createSchoolForm 実行後に PUBLISHED_URL と CUSTOMER_ID_ENTRY を入れる。
# 空のままなら全員サロン用フォームが送られる（従来動作）。
SCHOOL_FORM_URL          = "https://docs.google.com/forms/d/e/1FAIpQLScl761NYd6YWj3EfRyr91BcZwUxZpBTt3oVxnerOnoMdlsfFg/viewform"
SCHOOL_CUSTOMER_ID_ENTRY = "entry.860086005"
# このメールアドレスで決済した顧客にはスクール用フォームを送る（小文字で比較）
SCHOOL_EMAILS = {"syokokumamoto0205@gmail.com"}

# ── ナポリ版フォーム（napori_mark・弱者のマーケティング・コンサル）──────────
# createNaporiForm 実行後の PUBLISHED_URL と CUSTOMER_ID_ENTRY。
NAPORI_FORM_URL          = "https://docs.google.com/forms/d/e/1FAIpQLSeRiqCth4_QqOBVbiDyWwNNMr5xo_K34jsk1jGuhATmeWtClA/viewform"
NAPORI_CUSTOMER_ID_ENTRY = "entry.1469119100"
# ナポリさんがStripe決済で使うメールアドレス（小文字で比較）
NAPORI_EMAILS = {"youandcooooo@gmail.com"}


def build_prefilled_form(email: str, customer_id: str) -> tuple:
    """メールから送るフォームを出し分けて (URL, 種別) を返す。
    種別は "napori" / "school" / "salon"。該当URLが未設定なら常にサロン用。"""
    e = (email or "").strip().lower()
    if bool(NAPORI_FORM_URL) and e in NAPORI_EMAILS:
        return f"{NAPORI_FORM_URL}?{NAPORI_CUSTOMER_ID_ENTRY}={urllib.parse.quote(customer_id)}", "napori"
    if bool(SCHOOL_FORM_URL) and e in SCHOOL_EMAILS:
        return f"{SCHOOL_FORM_URL}?{SCHOOL_CUSTOMER_ID_ENTRY}={urllib.parse.quote(customer_id)}", "school"
    return f"{GOOGLE_FORM_URL}?{CUSTOMER_ID_ENTRY}={urllib.parse.quote(customer_id)}", "salon"


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


def save_line_customer_mapping(line_uid: str, customer_id: str):
    """line_users に stripe_customer_id を紐づけて保存（send-step で使用）"""
    data = json.dumps({
        "line_user_id": line_uid,
        "stripe_customer_id": customer_id,
    }).encode()
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/line_users",
        data=data,
        headers={**supabase_headers(), "Prefer": "resolution=merge-duplicates"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            _log(f"save_line_customer_mapping: status={r.status}")
    except Exception as e:
        _log(f"save_line_customer_mapping error: {e}")


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
def get_session_line_items(session_id: str) -> list:
    """Fetch line items for a checkout session (with price.product expanded)"""
    if not STRIPE_SECRET_KEY or not session_id:
        return []
    url = f"https://api.stripe.com/v1/checkout/sessions/{session_id}/line_items?expand[]=data.price.product"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {STRIPE_SECRET_KEY}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            return data.get("data", [])
    except Exception as e:
        _log(f"get_session_line_items error: {e}")
        return []


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
    session_id  = obj.get("id", "")
    line_uid    = obj.get("client_reference_id", "") or ""
    customer_id = obj.get("customer", "") or ""

    # とうこさんの商品か確認（他サービスのStripe決済を除外）
    if session_id:
        items = get_session_line_items(session_id)
        if items and not _is_toukosan_product(items):
            _log(f"handle_checkout_session: product not matched, skipping (session={session_id})")
            return
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

    # line_uid と customer_id のマッピングを保存（send-step エンドポイントで使用）
    if line_uid and customer_id:
        save_line_customer_mapping(line_uid, customer_id)

    # クライアントにフォームURLを自動送信（customer_idをpre-fill）
    prefilled_form, form_kind = build_prefilled_form(email, customer_id)
    if line_uid:
        if form_kind == "napori":
            client_msg = (
                f"ご登録ありがとうございます😊\n\n"
                f"さっそく、ヒアリングシートをお送りします📋\n"
                f"↓ こちらにご回答ください\n\n"
                f"{prefilled_form}\n\n"
                f"上手く書こうとしなくて大丈夫です。\n"
                f"友達に話すように、思っていることをそのまま書いてください❣️\n\n"
                f"正直に・具体的に・感情そのままで書いていただくほど、\n"
                f"あなたにしか書けない、刺さる投稿になります✨\n\n"
                f"ご記入後、担当者より次の手順をご連絡いたします。"
            )
        else:
            client_msg = (
                f"ご決済ありがとうございます😊\n\n"
                f"さっそく、サロン情報のアンケートフォームをお送りします📋\n"
                f"↓ こちらにご回答ください\n\n"
                f"{prefilled_form}\n\n"
                f"なるべく詳しくご回答いただくのがポイントです❣️\n"
                f"実は、ここで投稿の質が大きく変わります✨\n\n"
                f"いただいた内容をもとに、AIがあなたのサロン“専用”の投稿を作ります。\n"
                f"書いていただくほど「そのサロンらしさ」が伝わる投稿になり、\n"
                f"あっさりだと、どこにでもある投稿になりがちです💦\n\n"
                f"「システムにサロンのことをたっぷり教える」つもりで、\n"
                f"ぜひ丁寧にご記入くださいね😊\n\n"
                f"ご記入後、担当者より次の手順をご連絡いたします。"
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

    # 再決済URL（この請求書専用の支払いページ）。webhookペイロードに入っているのを
    # 通知とログに残す。以前は捨てていて、後から本人へリマインドを送りたいときに
    # 彩さんがダッシュボードでコピーする手間が発生していた（2026-07-27改修）。
    invoice_url = obj.get("hosted_invoice_url", "") or ""
    _log(f"payment_failed: salon={salon['salon_name']} attempt={attempt} hosted_invoice_url={invoice_url}")
    url_block = f"\n\n💳 再決済URL（本人にリマインドを送る時にこのまま使えます）:\n{invoice_url}" if invoice_url else ""

    if isinstance(attempt, int) and attempt >= 3:
        deactivate_salon(salon["id"])
        line_push_admin(
            f"🔴 とうこさん 自動停止（支払い失敗 {attempt}回）\n\n"
            f"サロン: {salon['salon_name']}\n\n"
            f"Stripeのサブスクは継続中のため、入金されれば自動で投稿再開します。{url_block}\n\n"
            f"https://dashboard.stripe.com"
        )
    else:
        line_push_admin(
            f"⚠️ とうこさん 支払い失敗（{attempt}回目）\n\n"
            f"サロン: {salon['salon_name']}\n\n"
            f"Stripeが自動リトライします。3回失敗でサービス自動停止。{url_block}\n\n"
            f"https://dashboard.stripe.com"
        )


# ── 解約待ちリスト（2026-09-06 追加・うらかたさんの cancel watch と同じ考え方）─────
# 解約が決まった方をここに登録しておくと、Stripeが「最終課金が通った」と通知してきた瞬間に
# 彩さんへ「期間終了時にキャンセルを設定してください」とLINEが飛ぶ。
# 日付を決め打ちしたリマインダーと違い、課金がずれても失敗しても正しいタイミングで届く。
# 設定を忘れたまま次の請求が走った場合も、その課金でもう一度通知が飛ぶ（取りこぼさない）。
# 解約設定が済めば以降の請求が無くなるので、通知も自然に止まる。
CANCEL_WATCH = {
    # customer_id: {名前, 満了日, メール, 備考}
    "cus_V0wKgFmqaF2CC1": {
        "name": "長町 孝彰",
        "until": "2026-11-05",
        "email": "youandcooooo@gmail.com",
        "note": "ナポリさん。2026-08-05契約・最低利用期間3ヶ月（8/5・9/5・10/5の3回課金）。"
                "Threads連携が未完了のため投稿停止の操作は不要。",
    },
}


def handle_cancel_watch(obj: dict) -> bool:
    """解約待ちの方の課金が通ったら、彩さんへ解約設定のリマインドを送る。送ったらTrue。"""
    customer_id = obj.get("customer", "") or ""
    w = CANCEL_WATCH.get(customer_id)
    if not w:
        return False
    # 定期課金の請求だけを対象にする（初回登録や手動請求では鳴らさない）
    if obj.get("billing_reason") not in (None, "subscription_cycle", "subscription_create"):
        _log(f"cancel_watch: billing_reason={obj.get('billing_reason')} のためスキップ")
        return False
    amount = obj.get("amount_paid", 0)
    line_push_admin(
        f"💳 {w['name']}さんの課金が通りました\n\n"
        f"✅ Stripeで「期間終了時にキャンセル」を設定してください。\n"
        f"https://dashboard.stripe.com/customers/{customer_id}\n\n"
        f"対象：{w['name']}さん\n"
        f"メールアドレス：{w['email']}\n"
        f"満了日：{w['until']}（この日をもって契約終了）\n"
        f"今回の課金：¥{amount:,}\n\n"
        f"※設定しないと満了日に次の請求が走ります。\n"
        f"{w['note']}"
    )
    _log(f"cancel_watch: {w['name']} へのリマインドを送信（invoice={obj.get('id')}）")
    return True


def handle_invoice_paid(obj: dict):
    """invoice.paid: 解約待ちの通知／既存サロンの支払い再開。新規登録は checkout.session.completed で処理。"""
    # 解約待ちの方は、この課金が「最終課金」なので最優先で彩さんへ知らせる
    if handle_cancel_watch(obj):
        return
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
        prefilled, _ = build_prefilled_form(email, customer_id)
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

        if qs.get("test") == ["checkout"]:
            test_cus = "cus_TEST123"
            test_prefilled = f"{GOOGLE_FORM_URL}?{CUSTOMER_ID_ENTRY}={urllib.parse.quote(test_cus)}"
            test_msg = (
                f"サロン情報のご入力をお願いします📋\n\n"
                f"{test_prefilled}\n\n"
                f"ご記入後、担当者よりThreads連携URLをお送りします。"
            )
            result = line_push_admin(test_msg)
            self.wfile.write(json.dumps({"test_sent": True, "url": test_prefilled, "line_status": result}, ensure_ascii=False).encode())
            return

        if qs.get("test") == ["form"]:
            connect_url = "https://saas.shikisai.work/api/connect?customer_id=cus_TEST123"
            form_msg = (
                f"📋 とうこさん フォーム回答あり！\n\n"
                f"サロン名：テストサロン\n"
                f"オーナー名：テスト太郎\n"
                f"Threads ID：@test_account\n\n"
                f"【やること】\n"
                f"下記をLINEでコピペ送信 ↓\n"
                f"──────────────\n"
                f"Threadsとの連携手順をお送りします📱\n\n"
                f"下記URLを開いて、Instagramアカウントでログインし\n"
                f"連携を完了してください。\n\n"
                f"{connect_url}\n\n"
                f"ご不明な点はいつでもお気軽にご連絡ください😊\n"
                f"──────────────"
            )
            result = line_push_admin(form_msg)
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
