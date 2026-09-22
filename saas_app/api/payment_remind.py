"""
とうこさん 支払い失敗 → LINEリマインド（うらかたさんの Code.js と同じ流れをとうこさん側に移植・2026-09-22）

  1通目: 失敗を受けたら（webhook or 毎朝の見回り）→ 彩さんに「承認して送信」URL付きLINE → タップで本人へ送信
  2通目: 1通目から3日たっても未払い → 同じく承認 → 送信
  3通目: 2通目から3日 → 同じく承認 → 送信
  それでも3日未払い → 彩さんに「手動フォロー必要」
  ※本人へ届く経路は「彩さんの承認ボタン」だけ。承認なしで送る分岐は持たない（Sol指摘#10）

状態は Stripe の請求書 metadata に持つ（新しいテーブルを作らない）:
  tk_remind    = pending1 / sending1 / sent1 / pending2 / … / sent3 / escalated / no_line / paid_skip
  tk_nonce     = 承認URLの一回限りの合言葉の元（pendingのときだけ存在。送ったら消す＝古いURLは死ぬ）
  tk_lock      = 送信権の印（同時タップの片方だけが進む。Stripeに比較更新が無いので書いて読み直す）
  tk_sentN_at  = N通目を本人へ送った日時（JST）
  tk_at        = 最後に状態を変えた日時
支払われた請求書は Stripe の「未払い一覧」から消えるので、それが「解決」の印。
状態は前にしか進めない（sent→pending へ戻さない。古いwebhookや見回りの取得結果で巻き戻さない・Sol指摘#3）。

エンドポイント（/api/payment-remind）:
  GET  ?mode=poll[&dry=1]      毎朝の見回り。Authorization: Bearer <HMAC(SUPABASE_SERVICE_KEY,"poll:<日付>")>
  POST ?mode=start&invoice=…  webhookからの「1通目を起こす」。同じBearer
  POST ?mode=mark&invoice=…&attempt=N   彩さんが手で送ったときの印。Bearer は "mark:<日付>"
  GET  ?mode=approve&invoice=…&attempt=N&token=…   彩さんがタップする承認ページ
  POST ?mode=execute&invoice=…&attempt=N&token=…   承認ページのボタンが呼ぶ（本人へ送信）
承認token = HMAC(STRIPE_WEBHOOK_SECRET, "approve:<invoice>:<attempt>:<nonce>")[:32]
"""
from http.server import BaseHTTPRequestHandler
import base64, datetime, hashlib, hmac, html, json, os, secrets, sys, time, urllib.parse, urllib.request, uuid

STRIPE_SECRET_KEY     = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
SUPABASE_URL          = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY          = os.environ.get("SUPABASE_SERVICE_KEY", "")
ADMIN_LINE_TOKEN      = os.environ.get("ADMIN_NOTIFY_LINE_TOKEN", "")    # Claude通知Bot（彩さん宛）
CLIENT_LINE_TOKEN     = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")  # とうこさんOA（お客様宛）
TOUKOSAN_PRODUCT_ID   = "prod_UWa5BZv291uQts"
BASE_URL              = os.environ.get("PUBLIC_BASE_URL", "https://saas.shikisai.work")
REMIND_INTERVAL_DAYS  = 3
LOCK_SETTLE_SEC       = float(os.environ.get("TK_LOCK_SETTLE_SEC", "1.5"))
JST = datetime.timezone(datetime.timedelta(hours=9))


class ConfigError(Exception):
    pass


def _require_config():
    """鍵が空のまま動くと、空の鍵から作った合言葉で誰でも送れてしまう（Sol指摘#4）"""
    missing = [k for k, v in (("STRIPE_SECRET_KEY", STRIPE_SECRET_KEY), ("STRIPE_WEBHOOK_SECRET", STRIPE_WEBHOOK_SECRET),
                              ("SUPABASE_URL", SUPABASE_URL), ("SUPABASE_SERVICE_KEY", SUPABASE_KEY),
                              ("ADMIN_NOTIFY_LINE_TOKEN", ADMIN_LINE_TOKEN), ("LINE_CHANNEL_ACCESS_TOKEN", CLIENT_LINE_TOKEN)) if not v]
    if missing:
        raise ConfigError("設定が足りません: " + ", ".join(missing))


def _log(msg):
    print(f"[payment_remind] {msg}", file=sys.stderr, flush=True)


def _now():
    return datetime.datetime.now(JST)


def _iso():
    return _now().isoformat(timespec="seconds")


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
def _hmac(key: str, msg: str) -> str:
    return hmac.new(key.encode(), msg.encode(), hashlib.sha256).hexdigest()[:32]


def approve_token(invoice_id: str, attempt: int, nonce: str) -> str:
    return _hmac(STRIPE_WEBHOOK_SECRET, f"approve:{invoice_id}:{attempt}:{nonce}")


def bearer_ok(header: str, purpose: str) -> bool:
    """GitHub Actions / webhook からの呼び出し用。日付入りなので前後1日だけ通す。"""
    if not header or not header.startswith("Bearer ") or not SUPABASE_KEY:
        return False
    token = header[len("Bearer "):].strip()
    today = _now().date()
    return any(hmac.compare_digest(_hmac(SUPABASE_KEY, f"{purpose}:{(today + datetime.timedelta(days=d)).isoformat()}"), token)
               for d in (-1, 0, 1))


# ── Stripe ──────────────────────────────────────────────────────
def _stripe(method: str, path: str, form: dict = None) -> dict:
    data = urllib.parse.urlencode(form).encode() if form else None
    req = urllib.request.Request("https://api.stripe.com/v1/" + path, data=data, method=method,
                                 headers={"Authorization": "Basic " + base64.b64encode((STRIPE_SECRET_KEY + ":").encode()).decode()})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def stripe_get_invoice(invoice_id: str) -> dict:
    inv = _stripe("GET", f"invoices/{urllib.parse.quote(invoice_id)}")
    lines = inv.get("lines") or {}
    if lines.get("has_more"):    # 明細が2ページ目にある請求書で対象商品を取りこぼさない（Sol指摘）
        inv["lines"] = {"data": _stripe("GET", f"invoices/{urllib.parse.quote(invoice_id)}/lines?limit=100").get("data", [])}
    return inv


def stripe_open_invoices() -> list:
    out, starting_after = [], None
    while True:
        q = "invoices?status=open&limit=100" + (f"&starting_after={starting_after}" if starting_after else "")
        page = _stripe("GET", q)
        out += page.get("data", [])
        if not page.get("has_more") or not page.get("data"):
            return out
        starting_after = page["data"][-1]["id"]


def set_meta(invoice_id: str, **kv) -> dict:
    """metadata を書く。値が "" のキーは削除される（Stripeの仕様）。書いた後の請求書を返す。"""
    form = {f"metadata[{k}]": v for k, v in kv.items()}
    return _stripe("POST", f"invoices/{urllib.parse.quote(invoice_id)}", form)


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
    return any(TOUKOSAN_PRODUCT_ID in _line_product_ids(l) for l in (inv.get("lines") or {}).get("data", []))


def is_failed_open(inv: dict) -> bool:
    """「カード決済に失敗した未払い」だけを対象にする。発行直後・未試行・期限前・残額0は対象外（Sol指摘#8）"""
    return (inv.get("status") == "open"
            and int(inv.get("attempt_count") or 0) >= 1
            and int(inv.get("amount_remaining") or 0) > 0
            and inv.get("collection_method", "charge_automatically") == "charge_automatically")


# ── Supabase ─────────────────────────────────────────────────────
def _sb(path: str) -> list:
    req = urllib.request.Request(f"{SUPABASE_URL}/rest/v1/{path}",
                                 headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def customer_info(customer_id: str) -> dict:
    q = urllib.parse.quote(customer_id or "")
    users = _sb(f"line_users?stripe_customer_id=eq.{q}&select=line_user_id,display_name&limit=1") if q else []
    salons = _sb(f"salons?stripe_customer_id=eq.{q}&select=salon_name&limit=1") if q else []
    u = users[0] if users else {}
    s = salons[0] if salons else {}
    return {"line_user_id": u.get("line_user_id") or "", "name": u.get("display_name") or "", "salon_name": s.get("salon_name") or ""}


def _who(info: dict, inv: dict) -> str:
    return f"{info.get('name') or inv.get('customer_name') or '(名前なし)'} / @{info.get('salon_name') or '?'}"


# ── LINE ─────────────────────────────────────────────────────────
def _line_post(path: str, token: str, body: dict, retry_key: str = "") -> int:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if retry_key:
        headers["X-Line-Retry-Key"] = retry_key
    req = urllib.request.Request("https://api.line.me/v2/bot/message/" + path, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        # 同じ retry key の再送で 409＋受理IDが返る＝すでに届いている
        if e.code == 409 and retry_key and e.headers is not None and e.headers.get("x-line-accepted-request-id"):
            return 200
        raise


def line_admin(text: str) -> None:
    """彩さん宛。失敗したら例外＝呼び出し側は状態を進めない（承認依頼が黙って消えない・Sol指摘#5）"""
    _line_post("broadcast", ADMIN_LINE_TOKEN, {"messages": [{"type": "text", "text": text}]})


def line_client(uid: str, text: str, retry_key: str) -> None:
    """お客様宛（とうこさんOA）。同じ送信には同じ retry key＝24時間以内の再送で二重に届かない"""
    if not uid:
        raise RuntimeError("宛先のLINE IDがありません")
    _line_post("push", CLIENT_LINE_TOKEN, {"to": uid, "messages": [{"type": "text", "text": text}]}, retry_key)


# ── 状態遷移 ───────────────────────────────────────────────────────
def _meta(inv: dict) -> dict:
    return inv.get("metadata") or {}


def _prev_state_for(attempt: int) -> str:
    return "" if attempt == 1 else f"sent{attempt - 1}"


def request_attempt(invoice_id: str, attempt: int, dry: bool = False) -> str:
    """N通目の承認依頼を彩さんへ出す。最新の請求書を取り直し、期待した状態のときだけ進める（巻き戻し禁止）。
    順序＝①LINEを送る ②metadataに pendingN＋nonce を書く。②が失敗しても①は届いているので翌朝もう一度依頼が出る（消えるよりまし）。"""
    inv = stripe_get_invoice(invoice_id)
    if not is_toukosan(inv) or not is_failed_open(inv):
        return f"対象外（状態 {inv.get('status')} / 失敗{inv.get('attempt_count')}回）: {invoice_id}"
    state = _meta(inv).get("tk_remind", "")
    if state != _prev_state_for(attempt):
        return f"進めない（今の状態 {state or 'なし'} / 期待 {_prev_state_for(attempt) or 'なし'}）: {invoice_id}"
    info = customer_info(inv.get("customer", ""))
    who = _who(info, inv)
    if not info["line_user_id"]:
        if not dry:
            line_admin(f"⚠️ とうこさん 支払い失敗（LINEの紐付けなし）\n\n{who}\ncustomer: {inv.get('customer')}\n"
                       f"名簿にLINE IDが無いため自動リマインドできません。手動でご連絡ください。\n{inv.get('hosted_invoice_url', '')}")
            set_meta(invoice_id, tk_remind="no_line", tk_at=_iso())
        return f"紐付けなし: {who}"
    if dry:
        return f"[dry] {attempt}通目の承認依頼を出す: {who} [{invoice_id}]"
    nonce = secrets.token_hex(8)
    url = inv.get("hosted_invoice_url", "") or ""
    approve_url = (f"{BASE_URL}/api/payment-remind?mode=approve&invoice={urllib.parse.quote(invoice_id)}"
                   f"&attempt={attempt}&token={approve_token(invoice_id, attempt, nonce)}")
    line_admin(f"[とうこさん要承認 {attempt}通目]\n"
               f"👤 {who}\n"
               f"金額: ¥{int(inv.get('amount_due', 0) or 0):,}（失敗{inv.get('attempt_count')}回）\n"
               f"invoice: {invoice_id}\n\n"
               f"--- 文面プレビュー ---\n{build_message(attempt, url)}\n\n"
               f"✅ 承認して送信:\n{approve_url}\n\n"
               f"（送らない場合は何もせず無視してください）")
    set_meta(invoice_id, tk_remind=f"pending{attempt}", tk_nonce=nonce, tk_lock="", tk_at=_iso())
    return f"承認依頼を出した: {attempt}通目 {who} [{invoice_id}]"


def _days_since(iso: str) -> float:
    try:
        return (_now() - datetime.datetime.fromisoformat(iso)).total_seconds() / 86400
    except Exception:
        return 0.0


def process_invoice(inv: dict, dry: bool = False) -> str:
    if not is_toukosan(inv):
        return ""
    if not is_failed_open(inv):
        return f"未試行・期限前などのため対象外: [{inv['id']}]"
    m = _meta(inv); st = m.get("tk_remind", "")
    label = f"[{inv['id']}]"
    if st.startswith("pending"):
        return f"承認待ち（{st}）{label}"
    if st.startswith("sending"):
        # 送信権を取って送ったあと、記録の書き込みだけ失敗した形。二度と送らず、送った扱いで記録を直す（Sol指摘#2）
        n = st[-1]
        if not dry:
            set_meta(inv["id"], tk_remind=f"sent{n}", tk_nonce="", tk_lock="", **{f"tk_sent{n}_at": m.get("tk_at") or _iso()}, tk_at=_iso())
        return f"送信済みとして記録を修復（{st}→sent{n}）{label}"
    if st in ("escalated", "no_line", "paid_skip"):
        return f"対応済み（{st}）{label}"
    if not st:
        return request_attempt(inv["id"], 1, dry)
    for n in (1, 2):
        if st == f"sent{n}" and _days_since(m.get(f"tk_sent{n}_at", "")) >= REMIND_INTERVAL_DAYS:
            return request_attempt(inv["id"], n + 1, dry)
    if st == "sent3" and _days_since(m.get("tk_sent3_at", "")) >= REMIND_INTERVAL_DAYS:
        info = customer_info(inv.get("customer", ""))
        if not dry:
            line_admin(f"⚠️【手動フォロー必要】\n{_who(info, inv)} さんが3通目のリマインドから3日経っても未払いです。\n"
                       f"直接連絡またはサービス停止を検討してください。\ncustomer: {inv.get('customer')}\ninvoice: {inv['id']}")
            set_meta(inv["id"], tk_remind="escalated", tk_at=_iso())
        return f"手動フォロー通知 {label}"
    return f"待機中（{st}）{label}"


def poll(dry: bool = False) -> dict:
    _require_config()
    results, errors, scanned = [], [], []
    for inv in stripe_open_invoices():
        try:
            if dry:
                scanned.append({"invoice": inv.get("id"), "amount": inv.get("amount_due"), "attempt_count": inv.get("attempt_count"),
                                "toukosan": is_toukosan(inv), "failed_open": is_failed_open(inv), "meta": _meta(inv).get("tk_remind", "")})
            r = process_invoice(inv, dry)
            if r:
                results.append(r)
        except Exception as e:  # noqa: BLE001 1件の失敗で他を止めない。失敗は返して見回り側（GitHub）を赤にする
            errors.append(f"{inv.get('id')}: {type(e).__name__}: {e}")
    _log(f"poll dry={dry} results={results} errors={errors}")
    out = {"ok": not errors, "dry": dry, "results": results, "errors": errors}
    if dry:
        out["scanned"] = scanned
    return out


def mark_sent(invoice_id: str, attempt: int) -> str:
    """彩さんが自分の手で送ったとき用。前に進める方向にしか書かない。"""
    inv = stripe_get_invoice(invoice_id)
    if not is_toukosan(inv):
        return "NG: とうこさんの請求書ではありません"
    st = _meta(inv).get("tk_remind", "")
    if st not in (_prev_state_for(attempt), f"pending{attempt}"):
        return f"NG: 今の状態 {st or 'なし'} からは {attempt}通目の印を付けられません"
    set_meta(invoice_id, tk_remind=f"sent{attempt}", tk_nonce="", tk_lock="", **{f"tk_sent{attempt}_at": _iso()}, tk_at=_iso())
    return f"OK: {invoice_id} を {attempt}通目送付済みにしました（次は{REMIND_INTERVAL_DAYS}日後に{attempt + 1}通目の承認依頼）"


def execute(invoice_id: str, attempt: int, token: str) -> tuple:
    """承認ボタンから。(ok, text)。送信権は「tk_lock に自分の印を書き、読み直して自分の印が残っていたら進む」で1人だけ取る。"""
    _require_config()
    inv = stripe_get_invoice(invoice_id)
    m = _meta(inv)
    if m.get("tk_remind") != f"pending{attempt}" or not m.get("tk_nonce") \
            or not hmac.compare_digest(approve_token(invoice_id, attempt, m["tk_nonce"]), token or ""):
        return False, f"NG: この依頼は処理済みか、承認待ちの状態ではありません（今: {m.get('tk_remind') or 'なし'}）。"
    my_lock = secrets.token_hex(8)
    set_meta(invoice_id, tk_remind=f"sending{attempt}", tk_lock=my_lock, tk_at=_iso())
    # Stripeには「前の値がXなら書く」が無い。同時タップは両方が自分の印を書けるので、
    # 少し待ってから読み直し、最後に書かれた印を持つ1人だけが進む。
    # それでも抜ける幅（1.5秒より遅く2人目が来る）は、同じ retry key でLINE側が二重配信を捨てる
    time.sleep(LOCK_SETTLE_SEC)
    inv = stripe_get_invoice(invoice_id)
    m = _meta(inv)
    if m.get("tk_lock") != my_lock or m.get("tk_remind") != f"sending{attempt}":
        return False, "NG: 同時に別の操作が進んでいます。もう一方の結果をお待ちください。"
    if not is_failed_open(inv):                                # 送る直前にも最新の状態を見る（Sol指摘#9）
        set_meta(invoice_id, tk_remind="paid_skip", tk_nonce="", tk_lock="", tk_at=_iso())
        return False, f"NG: この請求書はもう「{inv.get('status')}」（支払い済みなど）です。送信しません。"
    info = customer_info(inv.get("customer", ""))
    if not info["line_user_id"]:
        set_meta(invoice_id, tk_remind=f"pending{attempt}", tk_lock="", tk_at=_iso())
        return False, "NG: 名簿にLINE IDがありません。"
    retry_key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"toukosan-remind:{invoice_id}:{attempt}"))
    try:
        line_client(info["line_user_id"], build_message(attempt, inv.get("hosted_invoice_url", "") or ""), retry_key)
    except Exception as e:  # noqa: BLE001 送れなかったので承認待ちに戻す（同じURLでやり直せる）
        set_meta(invoice_id, tk_remind=f"pending{attempt}", tk_lock="", tk_at=_iso())
        return False, f"NG: LINEの送信に失敗しました（{type(e).__name__}）。少し待ってもう一度押してください。"
    set_meta(invoice_id, tk_remind=f"sent{attempt}", tk_nonce="", tk_lock="", **{f"tk_sent{attempt}_at": _iso()}, tk_at=_iso())
    return True, f"✅ 送信しました\n\n{attempt}通目を {info['name'] or '本人'} さんへ送りました。\nこのページは閉じて大丈夫です。"


# ── 承認ページ（.format は使わない。CSSの {} と衝突して500になる・Sol指摘#1）──
_PAGE = """<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>とうこさん 承認確認</title>
<style>body{font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans",sans-serif;margin:0;padding:24px;background:#f5f5f5;color:#222;line-height:1.7}
.card{max-width:480px;margin:0 auto;background:#fff;border-radius:12px;padding:24px;box-shadow:0 2px 8px rgba(0,0,0,.06)}
h2{margin:0 0 16px;font-size:18px}.info p{margin:6px 0}.info b{color:#555;min-width:80px;display:inline-block}
pre{white-space:pre-wrap;background:#fafafa;border:1px solid #eee;border-radius:8px;padding:12px;font-size:14px}
.btn{display:block;width:100%;padding:16px;background:#06c755;color:#fff;border:none;border-radius:8px;font-size:18px;font-weight:bold;cursor:pointer;margin-top:24px}
.btn:disabled{background:#999}.result{margin-top:16px;padding:12px;border-radius:8px;background:#f0f9f4;color:#06682a;white-space:pre-wrap;display:none}
.result.error{background:#fee;color:#c00}.result.show{display:block}</style></head><body><div class="card">
<h2>とうこさん __ATTEMPT__通目 承認確認</h2>
<div class="info"><p><b>宛先:</b> __WHO__</p><p><b>金額:</b> ¥__AMOUNT__（失敗__ATTEMPTS__回）</p><p><b>invoice:</b> __INVOICE__</p></div>
<pre>__PREVIEW__</pre>
<button class="btn" id="btn">✅ 承認して送信</button>
<div class="result" id="result"></div></div>
<script>
const btn=document.getElementById('btn'),res=document.getElementById('result');
btn.addEventListener('click',async()=>{btn.disabled=true;btn.textContent='送信中...';
 try{const r=await fetch(__EXECUTE_URL__,{method:'POST'});const t=await r.text();res.textContent=t;
  const ng=t.indexOf('NG')===0;res.className='result show'+(ng?' error':'');
  if(!ng){btn.style.display='none';}else{btn.disabled=false;btn.textContent='✅ 承認して送信';}}
 catch(e){res.textContent='通信エラー: '+e;res.className='result show error';btn.disabled=false;btn.textContent='✅ 承認して送信';}});
</script></body></html>"""

_DONE_PAGE = """<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>とうこさん 承認確認</title></head>
<body style="font-family:-apple-system,sans-serif;padding:24px"><p>__TEXT__</p></body></html>"""


def render_approve_page(invoice_id: str, attempt: int, token: str) -> tuple:
    """(HTTPコード, HTML)。処理済み・無効な依頼ではお客様の情報を出さない（Sol指摘#4）"""
    inv = stripe_get_invoice(invoice_id)
    m = _meta(inv)
    if m.get("tk_remind") != f"pending{attempt}" or not m.get("tk_nonce") \
            or not hmac.compare_digest(approve_token(invoice_id, attempt, m["tk_nonce"]), token or ""):
        return 403, _DONE_PAGE.replace("__TEXT__", "この依頼は処理済みか、無効なリンクです。")
    info = customer_info(inv.get("customer", ""))
    execute_url = json.dumps(f"{BASE_URL}/api/payment-remind?mode=execute&invoice={urllib.parse.quote(invoice_id)}&attempt={attempt}&token={token}")
    page = (_PAGE.replace("__ATTEMPT__", str(attempt)).replace("__WHO__", html.escape(_who(info, inv)))
            .replace("__AMOUNT__", f"{int(inv.get('amount_due', 0) or 0):,}").replace("__ATTEMPTS__", str(inv.get("attempt_count") or 0))
            .replace("__INVOICE__", html.escape(invoice_id))
            .replace("__PREVIEW__", html.escape(build_message(attempt, inv.get("hosted_invoice_url", "") or "")))
            .replace("__EXECUTE_URL__", execute_url))
    return 200, page


class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: str, ctype: str = "text/plain; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self, method: str):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        g = lambda k: (qs.get(k, [""])[0] or "").strip()
        mode, invoice_id = g("mode"), g("invoice")
        attempt = int(g("attempt") or "0")
        auth = self.headers.get("Authorization", "")
        try:
            if mode == "poll" and method == "GET":
                if not bearer_ok(auth, "poll"):
                    return self._send(403, "NG: 認証")
                out = poll(dry=g("dry") == "1")
                return self._send(200 if out["ok"] else 500, json.dumps(out, ensure_ascii=False, indent=1), "application/json; charset=utf-8")
            if mode == "start" and method == "POST":
                if not bearer_ok(auth, "poll") or not invoice_id:
                    return self._send(403, "NG: 認証")
                _require_config()
                return self._send(200, request_attempt(invoice_id, 1))
            if mode == "mark" and method == "POST":
                if not bearer_ok(auth, "mark") or not invoice_id or attempt not in (1, 2, 3):
                    return self._send(403, "NG: 認証/引数")
                _require_config()
                return self._send(200, mark_sent(invoice_id, attempt))
            if mode == "approve" and method == "GET" and invoice_id and attempt in (1, 2, 3):
                _require_config()
                code, page = render_approve_page(invoice_id, attempt, g("token"))
                return self._send(code, page, "text/html; charset=utf-8")
            if mode == "execute" and method == "POST" and invoice_id and attempt in (1, 2, 3):
                ok, text = execute(invoice_id, attempt, g("token"))
                return self._send(200 if ok else 409, text)
            return self._send(404, "NG")
        except Exception as e:  # noqa: BLE001
            _log(f"error mode={mode} {method}: {type(e).__name__}: {e}")
            return self._send(500, f"NG: {type(e).__name__}: {e}")

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")
