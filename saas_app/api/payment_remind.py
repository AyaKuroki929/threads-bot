"""
とうこさん 支払い失敗 → LINEリマインド（うらかたさんの Code.js と同じ流れをとうこさん側に移植・2026-09-22）

  1通目: 失敗を受けたら（webhook or 毎朝の見回り）→ 彩さんに「承認して送信」URL付きLINE → タップで本人へ送信
  2通目: 1通目から3日たっても未払い → 同じく承認 → 送信
  3通目: 2通目から3日 → 同じく承認 → 送信
  それでも3日未払い → 彩さんに「手動フォロー必要」

状態は Stripe の請求書 metadata に持つ（新しいテーブルを作らない）:
  tk_remind   = pending1 / sent1 / pending2 / sent2 / pending3 / sent3 / escalated / no_line
  tk_sentN_at = N通目を本人へ送った日時（JST）
支払われた請求書は Stripe の「未払い一覧」から消えるので、それが「解決」の印。

エンドポイント（/api/payment-remind）:
  ?mode=poll&token=…[&dry=1]     毎朝の見回り（GitHub Actions が呼ぶ。dry=1 は送らず一覧だけ返す）
  ?mode=approve&invoice=…&attempt=N&token=…   彩さんがタップする承認ページ
  ?mode=execute&invoice=…&attempt=N&token=…   承認ページのボタンが呼ぶ（本人へ送信）
  ?mode=start&invoice=…&token=…   webhook から呼ばれる想定の「1通目を起こす」（内部用・poll と同じ合言葉）

合言葉（新しい秘密を増やさない）:
  承認URL: HMAC(STRIPE_WEBHOOK_SECRET, "approve:<invoice>:<attempt>")
  見回り : HMAC(SUPABASE_SERVICE_KEY,  "poll:<YYYY-MM-DD>")  ← GitHub Actions 側も同じ鍵を持っている
"""
from http.server import BaseHTTPRequestHandler
import base64, datetime, hashlib, hmac, html, json, os, sys, urllib.parse, urllib.request

STRIPE_SECRET_KEY     = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
SUPABASE_URL          = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY          = os.environ.get("SUPABASE_SERVICE_KEY", "")
ADMIN_LINE_TOKEN      = os.environ.get("ADMIN_NOTIFY_LINE_TOKEN", "")    # Claude通知Bot（彩さん宛）
CLIENT_LINE_TOKEN     = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")  # とうこさんOA（お客様宛）
TOUKOSAN_PRODUCT_ID   = "prod_UWa5BZv291uQts"
BASE_URL              = os.environ.get("PUBLIC_BASE_URL", "https://saas.shikisai.work")
# うらかたさんと同じく承認制ON。false にすると営業時間内（9〜19時JST）に直接送る
APPROVAL_REQUIRED     = os.environ.get("TK_REMIND_APPROVAL", "true").lower() != "false"
BUSINESS_HOURS        = (9, 19)
REMIND_INTERVAL_DAYS  = 3
JST = datetime.timezone(datetime.timedelta(hours=9))


def _log(msg):
    print(f"[payment_remind] {msg}", file=sys.stderr, flush=True)


def _now():
    return datetime.datetime.now(JST)


# ── 文面（うらかたさんの3通と同じ形。サービス名だけ「とうこさん」）──────────
def build_message(attempt: int, url: str) -> str:
    if attempt == 1:
        body = ("お世話になります。\n\n"
                "今月のとうこさんのカード決済ができていなかったようで、有効期限などご確認いただけますでしょうか🙇🏻‍♀️\n\n"
                "下記リンクより、宜しくお願いいたします。")
    elif attempt == 2:
        body = ("お世話になります。\n\n"
                "先日ご連絡いたしました今月のとうこさんのカード決済の件、まだ確認ができておりませんでした🙇🏻‍♀️\n\n"
                "ご多用のところ恐れ入りますが、下記よりお手続きをお願いいたします。")
    else:
        body = ("お世話になります。\n\n"
                "たびたびのご連絡失礼いたします。\n"
                "今月のとうこさんのカード決済が、まだ確認できていない状況です🙇🏻‍♀️\n\n"
                "下記よりご対応お願いいたします。")
    return body + ("\n" + url if url else "")


# ── 合言葉 ─────────────────────────────────────────────────────
def approve_token(invoice_id: str, attempt: int) -> str:
    return hmac.new(STRIPE_WEBHOOK_SECRET.encode(), f"approve:{invoice_id}:{attempt}".encode(), hashlib.sha256).hexdigest()[:32]


def poll_token(day: str) -> str:
    return hmac.new(SUPABASE_KEY.encode(), f"poll:{day}".encode(), hashlib.sha256).hexdigest()[:32]


def poll_token_ok(token: str) -> bool:
    if not token or not SUPABASE_KEY:
        return False
    today = _now().date()
    for d in (today, today - datetime.timedelta(days=1), today + datetime.timedelta(days=1)):
        if hmac.compare_digest(poll_token(d.isoformat()), token):
            return True
    return False


# ── Stripe ──────────────────────────────────────────────────────
def _stripe(method: str, path: str, form: dict = None) -> dict:
    data = urllib.parse.urlencode(form).encode() if form else None
    req = urllib.request.Request("https://api.stripe.com/v1/" + path, data=data, method=method,
                                 headers={"Authorization": "Basic " + base64.b64encode((STRIPE_SECRET_KEY + ":").encode()).decode()})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def stripe_get_invoice(invoice_id: str) -> dict:
    return _stripe("GET", f"invoices/{urllib.parse.quote(invoice_id)}")


def stripe_open_invoices() -> list:
    out, starting_after = [], None
    while True:
        q = "invoices?status=open&limit=100" + (f"&starting_after={starting_after}" if starting_after else "")
        page = _stripe("GET", q)
        out += page.get("data", [])
        if not page.get("has_more") or not out:
            return out
        starting_after = out[-1]["id"]


def set_meta(invoice_id: str, **kv) -> None:
    form = {f"metadata[{k}]": v for k, v in kv.items()}
    _stripe("POST", f"invoices/{urllib.parse.quote(invoice_id)}", form)


def _line_product_ids(line: dict) -> list:
    """請求書の明細から商品IDを取り出す。Stripeは版によって置き場所が違う:
    旧: line.price.product / line.plan.product  新(2025〜): line.pricing.price_details.product
    （2026-09-22 新しい置き場所を見ていなかったため、とうこさんの失敗通知を取りこぼしていた）"""
    out = []
    for key in ("price", "plan"):
        v = line.get(key)
        if isinstance(v, dict) and v.get("product"):
            p = v["product"]; out.append(p.get("id") if isinstance(p, dict) else p)
    pd = (line.get("pricing") or {}).get("price_details") or {}
    if pd.get("product"):
        p = pd["product"]; out.append(p.get("id") if isinstance(p, dict) else p)
    return [x for x in out if x]


def is_toukosan(inv: dict) -> bool:
    return any(TOUKOSAN_PRODUCT_ID in _line_product_ids(l) for l in inv.get("lines", {}).get("data", []))


# ── Supabase ─────────────────────────────────────────────────────
def _sb(path: str) -> list:
    req = urllib.request.Request(f"{SUPABASE_URL}/rest/v1/{path}",
                                 headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def customer_info(customer_id: str) -> dict:
    """line_user_id / 表示名 / サロン名。紐付けが無ければ line_user_id は空"""
    q = urllib.parse.quote(customer_id)
    users = _sb(f"line_users?stripe_customer_id=eq.{q}&select=line_user_id,display_name&limit=1")
    salons = _sb(f"salons?stripe_customer_id=eq.{q}&select=salon_name,is_active&limit=1")
    u = users[0] if users else {}
    s = salons[0] if salons else {}
    return {"line_user_id": u.get("line_user_id", "") or "", "name": u.get("display_name", "") or "",
            "salon_name": s.get("salon_name", "") or "", "is_active": s.get("is_active", True)}


# ── LINE ─────────────────────────────────────────────────────────
def _line_post(path: str, token: str, body: dict) -> int:
    req = urllib.request.Request("https://api.line.me/v2/bot/message/" + path, data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


def line_admin(text: str) -> None:
    if not ADMIN_LINE_TOKEN:
        _log("ADMIN token missing; admin message skipped: " + text[:60]); return
    _line_post("broadcast", ADMIN_LINE_TOKEN, {"messages": [{"type": "text", "text": text}]})


def line_client(uid: str, text: str) -> int:
    if not CLIENT_LINE_TOKEN or not uid:
        raise RuntimeError("とうこさんOAのトークンか宛先IDがありません")
    return _line_post("push", CLIENT_LINE_TOKEN, {"to": uid, "messages": [{"type": "text", "text": text}]})


# ── 進め方 ───────────────────────────────────────────────────────
def _in_business_hours() -> bool:
    return BUSINESS_HOURS[0] <= _now().hour < BUSINESS_HOURS[1]


def _days_since(iso: str) -> float:
    try:
        return (_now() - datetime.datetime.fromisoformat(iso)).total_seconds() / 86400
    except Exception:
        return 0.0


def request_attempt(inv: dict, attempt: int, info: dict, dry: bool = False) -> str:
    """N通目を起こす。承認制なら彩さんへ承認依頼、そうでなければ営業時間内に本人へ直接送る。"""
    invoice_id = inv["id"]; url = inv.get("hosted_invoice_url", "") or ""
    who = f"{info['name'] or '(名前なし)'} / @{info['salon_name'] or '?'}"
    if dry:
        return f"[dry] {attempt}通目を起こす: {who}"
    if APPROVAL_REQUIRED:
        set_meta(invoice_id, tk_remind=f"pending{attempt}", tk_at=_now().isoformat(timespec="seconds"))
        approve_url = (f"{BASE_URL}/api/payment-remind?mode=approve&invoice={urllib.parse.quote(invoice_id)}"
                       f"&attempt={attempt}&token={approve_token(invoice_id, attempt)}")
        line_admin(f"[とうこさん要承認 {attempt}通目]\n"
                   f"👤 {who}\n"
                   f"customer: {inv.get('customer')}\ninvoice: {invoice_id}\n"
                   f"金額: ¥{int(inv.get('amount_due', 0) or 0):,}\n\n"
                   f"--- 文面プレビュー ---\n{build_message(attempt, url)}\n\n"
                   f"✅ 承認して送信:\n{approve_url}\n\n"
                   f"（送らない場合は何もせず無視してください）")
        return f"承認依頼を送った: {attempt}通目 {who}"
    if not _in_business_hours():
        return f"営業時間外のため次回に持ち越し: {attempt}通目 {who}"
    line_client(info["line_user_id"], build_message(attempt, url))
    set_meta(invoice_id, tk_remind=f"sent{attempt}", **{f"tk_sent{attempt}_at": _now().isoformat(timespec="seconds")})
    return f"本人へ送信: {attempt}通目 {who}"


def process_invoice(inv: dict, dry: bool = False) -> str:
    if not is_toukosan(inv):
        return ""
    meta = inv.get("metadata", {}) or {}
    st = meta.get("tk_remind", "")
    info = customer_info(inv.get("customer", ""))
    who = f"{info['name'] or inv.get('customer_name') or '(名前なし)'} / @{info['salon_name'] or '?'} [{inv['id']}]"
    if not info["line_user_id"]:
        if st != "no_line" and not dry:
            set_meta(inv["id"], tk_remind="no_line")
            line_admin(f"⚠️ とうこさん 支払い失敗（LINEの紐付けなし）\n\n{who}\ncustomer: {inv.get('customer')}\n"
                       f"名簿にLINE IDが無いため自動リマインドできません。手動でご連絡ください。\n{inv.get('hosted_invoice_url','')}")
        return f"紐付けなし: {who}"
    if st.startswith("pending"):
        return f"承認待ち（{st}）: {who}"
    if st in ("escalated", "no_line"):
        return f"対応済み（{st}）: {who}"
    if not st:
        return request_attempt(inv, 1, info, dry)
    if st == "sent1" and _days_since(meta.get("tk_sent1_at", "")) >= REMIND_INTERVAL_DAYS:
        return request_attempt(inv, 2, info, dry)
    if st == "sent2" and _days_since(meta.get("tk_sent2_at", "")) >= REMIND_INTERVAL_DAYS:
        return request_attempt(inv, 3, info, dry)
    if st == "sent3" and _days_since(meta.get("tk_sent3_at", "")) >= REMIND_INTERVAL_DAYS:
        if not dry:
            set_meta(inv["id"], tk_remind="escalated")
            line_admin(f"⚠️【手動フォロー必要】\n{who} さんが3通目のリマインドから3日経っても未払いです。\n"
                       f"直接連絡またはサービス停止を検討してください。\ncustomer: {inv.get('customer')}\ninvoice: {inv['id']}")
        return f"手動フォロー通知: {who}"
    return f"待機中（{st}）: {who}"


def _products_of(inv: dict) -> list:
    out = []
    for line in inv.get("lines", {}).get("data", []):
        out += _line_product_ids(line)
    return out


def poll(dry: bool = False) -> dict:
    results, errors, scanned = [], [], []
    for inv in stripe_open_invoices():
        try:
            if dry:   # 試運転では、見た請求書を全部（対象外も）並べて判断の根拠を残す
                scanned.append({"invoice": inv.get("id"), "customer": inv.get("customer"),
                                "amount": inv.get("amount_due"), "products": _products_of(inv),
                                "toukosan": is_toukosan(inv), "meta": (inv.get("metadata") or {}).get("tk_remind", "")})
            r = process_invoice(inv, dry)
            if r:
                results.append(r)
        except Exception as e:  # noqa: BLE001 1件の失敗で他を止めない
            errors.append(f"{inv.get('id')}: {type(e).__name__}: {e}")
    _log(f"poll dry={dry} results={results} errors={errors}")
    out = {"ok": not errors, "dry": dry, "approval_mode": APPROVAL_REQUIRED, "results": results, "errors": errors}
    if dry:
        out["scanned"] = scanned
    return out


def mark_sent(invoice_id: str, attempt: int) -> str:
    """彩さんが自分の手で送ったときに「N通目送付済み」の印だけ付ける（本人へは送らない）。"""
    inv = stripe_get_invoice(invoice_id)
    if not is_toukosan(inv):
        return "NG: とうこさんの請求書ではありません"
    set_meta(invoice_id, tk_remind=f"sent{attempt}", **{f"tk_sent{attempt}_at": _now().isoformat(timespec="seconds")})
    return f"OK: {invoice_id} を {attempt}通目送付済みにしました（次は{REMIND_INTERVAL_DAYS}日後に{attempt+1}通目の承認依頼）"


def execute(invoice_id: str, attempt: int) -> tuple:
    """承認ボタンから。 (ok, text)"""
    inv = stripe_get_invoice(invoice_id)
    if inv.get("status") != "open":
        return False, f"NG: この請求書は「{inv.get('status')}」です（支払い済みなど）。送信しません。"
    meta = inv.get("metadata", {}) or {}
    if meta.get("tk_remind") != f"pending{attempt}":
        return False, f"NG: 承認待ちの状態ではありません（今: {meta.get('tk_remind') or 'なし'}）。既に送信済みか、別の通番です。"
    info = customer_info(inv.get("customer", ""))
    if not info["line_user_id"]:
        return False, "NG: 名簿にLINE IDがありません。"
    line_client(info["line_user_id"], build_message(attempt, inv.get("hosted_invoice_url", "") or ""))
    set_meta(invoice_id, tk_remind=f"sent{attempt}", **{f"tk_sent{attempt}_at": _now().isoformat(timespec="seconds")})
    who = f"{info['name'] or '(名前なし)'} / @{info['salon_name'] or '?'}"
    try:
        line_admin(f"[とうこさん] ✅ 送信完了 ({attempt}通目): {who}\ninvoice: {invoice_id}")
    except Exception as e:  # noqa: BLE001
        _log(f"admin done-notify failed: {e}")
    return True, f"✅ 承認完了\n\n{attempt}通目を{info['name'] or '本人'}さんへ送信しました。\nこのページは閉じて大丈夫です。"


APPROVE_HTML = """<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>とうこさん 承認確認</title>
<style>body{font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans",sans-serif;margin:0;padding:24px;background:#f5f5f5;color:#222;line-height:1.7}
.card{max-width:480px;margin:0 auto;background:#fff;border-radius:12px;padding:24px;box-shadow:0 2px 8px rgba(0,0,0,.06)}
h2{margin:0 0 16px;font-size:18px}.info p{margin:6px 0}.info b{color:#555;min-width:80px;display:inline-block}
pre{white-space:pre-wrap;background:#fafafa;border:1px solid #eee;border-radius:8px;padding:12px;font-size:14px}
.btn{display:block;width:100%;padding:16px;background:#06c755;color:#fff;border:none;border-radius:8px;font-size:18px;font-weight:bold;cursor:pointer;margin-top:24px}
.btn:disabled{background:#999}.result{margin-top:16px;padding:12px;border-radius:8px;background:#f0f9f4;color:#06682a;white-space:pre-wrap;display:none}
.result.error{background:#fee;color:#c00}.result.show{display:block}</style></head><body><div class="card">
<h2>とうこさん {attempt}通目 承認確認</h2>
<div class="info"><p><b>宛先:</b> {who}</p><p><b>invoice:</b> {invoice}</p><p><b>金額:</b> ¥{amount}</p></div>
<pre>{preview}</pre>
<button class="btn" id="btn">✅ 承認して送信</button>
<div class="result" id="result"></div></div>
<script>
const btn=document.getElementById('btn'),res=document.getElementById('result');
btn.addEventListener('click',async()=>{{btn.disabled=true;btn.textContent='送信中...';
 try{{const r=await fetch({execute_url},{{method:'POST'}});const t=await r.text();res.textContent=t;
  res.className='result show'+(t.indexOf('NG')===0?' error':'');if(t.indexOf('NG')!==0)btn.style.display='none';else{{btn.disabled=false;btn.textContent='✅ 承認して送信';}}}}
 catch(e){{res.textContent='通信エラー: '+e;res.className='result show error';btn.disabled=false;btn.textContent='✅ 承認して送信';}}}});
</script></body></html>"""


class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: str, ctype: str = "text/plain; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _handle(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        g = lambda k: (qs.get(k, [""])[0] or "").strip()
        mode = g("mode")
        try:
            if mode == "poll":
                if not poll_token_ok(g("token")):
                    return self._send(403, "NG: token")
                out = poll(dry=g("dry") == "1")
                return self._send(200 if out["ok"] else 500, json.dumps(out, ensure_ascii=False, indent=1), "application/json; charset=utf-8")
            invoice_id, attempt = g("invoice"), int(g("attempt") or "0")
            if mode == "mark":
                if not poll_token_ok(g("token")) or attempt not in (1, 2, 3) or not invoice_id:
                    return self._send(403, "NG: token/引数")
                return self._send(200, mark_sent(invoice_id, attempt))
            if mode in ("approve", "execute"):
                if not invoice_id or attempt not in (1, 2, 3) or not hmac.compare_digest(approve_token(invoice_id, attempt), g("token")):
                    return self._send(403, "NG: このURLは無効です（合言葉が違います）。")
                if mode == "execute":
                    ok, text = execute(invoice_id, attempt)
                    return self._send(200, text)
                inv = stripe_get_invoice(invoice_id)
                info = customer_info(inv.get("customer", ""))
                who = f"{info['name'] or '(名前なし)'} / @{info['salon_name'] or '?'}"
                execute_url = json.dumps(f"{BASE_URL}/api/payment-remind?mode=execute&invoice={urllib.parse.quote(invoice_id)}&attempt={attempt}&token={g('token')}")
                page = APPROVE_HTML.format(attempt=attempt, who=html.escape(who), invoice=html.escape(invoice_id),
                                           amount=f"{int(inv.get('amount_due', 0) or 0):,}",
                                           preview=html.escape(build_message(attempt, inv.get("hosted_invoice_url", "") or "")),
                                           execute_url=execute_url)
                return self._send(200, page, "text/html; charset=utf-8")
            return self._send(404, "NG: mode")
        except Exception as e:  # noqa: BLE001
            _log(f"error mode={mode}: {type(e).__name__}: {e}")
            return self._send(500, f"NG: {type(e).__name__}: {e}")

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()
