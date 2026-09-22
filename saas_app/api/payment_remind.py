"""
とうこさん 支払い失敗 → LINEリマインド（うらかたさんの Code.js と同じ流れをとうこさん側に移植・2026-09-22）

  1通目: 失敗を受けたら（webhook or 毎朝の見回り）→ 彩さんに「承認して送信」URL付きLINE → タップで本人へ送信
  2通目: 1通目から3日たっても未払い → 同じく承認 → 送信
  3通目: 2通目から3日 → 同じく承認 → 送信
  それでも3日未払い → 彩さんに「手動フォロー必要」
  ※本人へ届く経路は「彩さんの承認ボタン」だけ。承認なしで送る分岐は持たない（Sol指摘#10）

状態は Supabase の台帳 payment_reminder_attempts に持つ（1行＝請求書×通番、主キーで二重作成不可）:
  pending_notify → pending（彩さんへ承認依頼を送れた）→ sending（送信権を取った）→ sent（本人へ送付済み）
  sending から LINE の応答が失われたら unknown（人の確認待ち・自動では二度と送らない）
  3通目から3日たっても未払いなら attempt=4 の行を escalated として作り、彩さんへ手動フォロー通知
  Stripe の metadata は使わない（同時更新の制御ができないため・Sol指摘#2,#3）。Stripeは読むだけ。
支払われた請求書は Stripe の「未払い一覧」から消えるので、それが「解決」の印。
送信権の取得は「status=pending かつ nonce=… の行だけを sending に更新して、更新できた行が返ったら自分」＝1つの
UPDATE文なので同時タップでも1人だけ進む。承認依頼の作成も INSERT の主キー衝突で1回だけ。

エンドポイント（/api/payment-remind）:
  GET  ?mode=poll[&dry=1]      毎朝の見回り。Authorization: Bearer <HMAC(SUPABASE_SERVICE_KEY,"poll:<日付>")>
  POST ?mode=start&invoice=…  webhookからの「1通目を起こす」。Bearer は "start:<日付>"（用途別・Sol指摘#4）
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


def _all_lines(invoice_id: str) -> list:
    out, after = [], None
    while True:
        page = _stripe("GET", f"invoices/{urllib.parse.quote(invoice_id)}/lines?limit=100" + (f"&starting_after={after}" if after else ""))
        out += page.get("data", [])
        if not page.get("has_more") or not page.get("data"):
            return out
        after = page["data"][-1]["id"]


def with_all_lines(inv: dict) -> dict:
    """明細が2ページ目以降にある請求書で対象商品を取りこぼさない（Sol指摘）。一覧で来た請求書にも使う"""
    if (inv.get("lines") or {}).get("has_more"):
        inv["lines"] = {"data": _all_lines(inv["id"]), "has_more": False}
    return inv


def stripe_get_invoice(invoice_id: str) -> dict:
    return with_all_lines(_stripe("GET", f"invoices/{urllib.parse.quote(invoice_id)}"))


def stripe_open_invoices() -> list:
    out, starting_after = [], None
    while True:
        q = "invoices?status=open&limit=100" + (f"&starting_after={starting_after}" if starting_after else "")
        page = _stripe("GET", q)
        out += page.get("data", [])
        if not page.get("has_more") or not page.get("data"):
            return out
        starting_after = page["data"][-1]["id"]


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
LEDGER = "payment_reminder_attempts"


class LedgerMissing(Exception):
    pass


def _sb(path: str, method: str = "GET", body: dict = None, prefer: str = "") -> list:
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    req = urllib.request.Request(f"{SUPABASE_URL}/rest/v1/{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return json.loads(raw) if raw else []
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        if path.startswith(LEDGER) and e.code in (404, 400) and ("PGRST205" in detail or "does not exist" in detail or "Could not find the table" in detail):
            raise LedgerMissing(f"台帳テーブル {LEDGER} がありません（supabase_tables.sql をSQL Editorで実行）") from e
        e.detail = detail
        raise


def ledger_rows(invoice_id: str) -> list:
    return _sb(f"{LEDGER}?invoice_id=eq.{urllib.parse.quote(invoice_id)}&order=attempt.asc")


def ledger_insert(row: dict) -> bool:
    """主キー衝突（同じ請求書×通番が既にある）なら False。それ以外の失敗は例外。"""
    try:
        _sb(LEDGER, "POST", row, prefer="return=minimal")
        return True
    except urllib.error.HTTPError as e:
        if e.code == 409:
            return False
        raise


def ledger_update(invoice_id: str, attempt: int, where: dict, patch: dict) -> bool:
    """条件付き更新。1つのUPDATE文なので同時実行でも更新できるのは1人。更新できた行が無ければ False。"""
    q = f"{LEDGER}?invoice_id=eq.{urllib.parse.quote(invoice_id)}&attempt=eq.{attempt}"
    for k, v in where.items():
        q += f"&{k}=eq.{urllib.parse.quote(str(v))}"
    rows = _sb(q, "PATCH", patch, prefer="return=representation")
    return len(rows) == 1


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


# ── 状態遷移（台帳ベース）───────────────────────────────────────
def _latest(rows: list) -> dict:
    return rows[-1] if rows else {}


def _notify_request(inv: dict, attempt: int, info: dict, nonce: str) -> None:
    """彩さんへ承認依頼を送る（失敗は例外）"""
    invoice_id = inv["id"]; url = inv.get("hosted_invoice_url", "") or ""
    approve_url = (f"{BASE_URL}/api/payment-remind?mode=approve&invoice={urllib.parse.quote(invoice_id)}"
                   f"&attempt={attempt}&token={approve_token(invoice_id, attempt, nonce)}")
    line_admin(f"[とうこさん要承認 {attempt}通目]\n"
               f"👤 {_who(info, inv)}\n"
               f"金額: ¥{int(inv.get('amount_due', 0) or 0):,}（失敗{inv.get('attempt_count')}回）\n"
               f"invoice: {invoice_id}\n\n"
               f"--- 文面プレビュー ---\n{build_message(attempt, url)}\n\n"
               f"✅ 承認して送信:\n{approve_url}\n\n"
               f"（送らない場合は何もせず無視してください）")


def request_attempt(invoice_id: str, attempt: int, dry: bool = False) -> str:
    """N通目の承認依頼を出す。台帳に行を INSERT できた1人だけが進む（主キー衝突＝他が先に進んでいる）。
    順序＝①行を pending_notify で作る ②彩さんへLINE ③pending に更新。②が失敗しても行は残り、翌朝の見回りが②からやり直す。"""
    inv = stripe_get_invoice(invoice_id)
    if not is_toukosan(inv) or not is_failed_open(inv):
        return f"対象外（状態 {inv.get('status')} / 失敗{inv.get('attempt_count')}回）: {invoice_id}"
    rows = ledger_rows(invoice_id)
    last = _latest(rows)
    if attempt == 1 and rows:
        return f"進めない（既に {last['attempt']}通目 {last['status']}）: {invoice_id}"
    if attempt > 1 and not (last.get("attempt") == attempt - 1 and last.get("status") == "sent"):
        return f"進めない（今 {last.get('attempt')}通目 {last.get('status')} / 期待 {attempt - 1}通目 sent）: {invoice_id}"
    info = customer_info(inv.get("customer", ""))
    who = _who(info, inv)
    if not info["line_user_id"]:
        if not dry and ledger_insert({"invoice_id": invoice_id, "attempt": attempt, "status": "no_line",
                                      "customer_id": inv.get("customer", ""), "note": "名簿にLINE IDなし"}):
            line_admin(f"⚠️ とうこさん 支払い失敗（LINEの紐付けなし）\n\n{who}\ncustomer: {inv.get('customer')}\n"
                       f"名簿にLINE IDが無いため自動リマインドできません。手動でご連絡ください。\n{inv.get('hosted_invoice_url', '')}")
        return f"紐付けなし: {who}"
    if dry:
        return f"[dry] {attempt}通目の承認依頼を出す: {who} [{invoice_id}]"
    nonce = secrets.token_hex(8)
    if not ledger_insert({"invoice_id": invoice_id, "attempt": attempt, "status": "pending_notify", "nonce": nonce,
                          "customer_id": inv.get("customer", ""), "line_user_id": info["line_user_id"]}):
        return f"進めない（同時に別の処理が先に作成）: {invoice_id}"
    _notify_request(inv, attempt, info, nonce)
    ledger_update(invoice_id, attempt, {"status": "pending_notify"}, {"status": "pending"})
    return f"承認依頼を出した: {attempt}通目 {who} [{invoice_id}]"


def _days_since(iso: str) -> float:
    try:
        return (_now() - datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))).total_seconds() / 86400
    except Exception:
        return 0.0


def process_invoice(inv: dict, dry: bool = False) -> str:
    if not is_toukosan(inv):
        return ""
    if not is_failed_open(inv):
        return f"未試行・期限前などのため対象外: [{inv['id']}]"
    rows = ledger_rows(inv["id"])
    last = _latest(rows)
    label = f"[{inv['id']}]"
    if not rows:
        return request_attempt(inv["id"], 1, dry)
    st, n = last["status"], int(last["attempt"])
    if st == "pending_notify":
        # 行はあるが彩さんへの依頼が届いていない（前回LINEが失敗）→ 依頼だけやり直す
        if not dry:
            info = customer_info(inv.get("customer", ""))
            _notify_request(inv, n, info, last.get("nonce") or "")
            ledger_update(inv["id"], n, {"status": "pending_notify"}, {"status": "pending"})
        return f"承認依頼を再送（{n}通目）{label}"
    if st in ("pending", "sending"):
        return f"承認待ち・送信中（{n}通目 {st}）{label}"
    if st in ("unknown", "escalated", "no_line"):
        return f"人の確認待ち・対応済み（{n}通目 {st}）{label}"
    if st == "sent" and n < 3 and _days_since(last.get("sent_at") or "") >= REMIND_INTERVAL_DAYS:
        return request_attempt(inv["id"], n + 1, dry)
    if st == "sent" and n == 3 and _days_since(last.get("sent_at") or "") >= REMIND_INTERVAL_DAYS:
        if not dry and ledger_insert({"invoice_id": inv["id"], "attempt": 4, "status": "escalated", "customer_id": inv.get("customer", "")}):
            info = customer_info(inv.get("customer", ""))
            line_admin(f"⚠️【手動フォロー必要】\n{_who(info, inv)} さんが3通目のリマインドから3日経っても未払いです。\n"
                       f"直接連絡またはサービス停止を検討してください。\ncustomer: {inv.get('customer')}\ninvoice: {inv['id']}")
        return f"手動フォロー通知 {label}"
    return f"待機中（{n}通目 {st}）{label}"


def poll(dry: bool = False) -> dict:
    _require_config()
    results, errors, scanned = [], [], []
    try:
        for inv in stripe_open_invoices():
            try:
                inv = with_all_lines(inv)
                if dry:
                    scanned.append({"invoice": inv.get("id"), "amount": inv.get("amount_due"), "attempt_count": inv.get("attempt_count"),
                                    "toukosan": is_toukosan(inv), "failed_open": is_failed_open(inv)})
                r = process_invoice(inv, dry)
                if r:
                    results.append(r)
            except LedgerMissing:
                raise
            except Exception as e:  # noqa: BLE001 1件の失敗で他を止めない。失敗は返して見回り側（GitHub）を赤にする
                errors.append(f"{inv.get('id')}: {type(e).__name__}: {e}")
    except LedgerMissing as e:
        errors.append(str(e))
    _log(f"poll dry={dry} results={results} errors={errors}")
    out = {"ok": not errors, "dry": dry, "results": results, "errors": errors}
    if dry:
        out["scanned"] = scanned
    return out


def mark_sent(invoice_id: str, attempt: int) -> str:
    """彩さんが自分の手で送ったとき用。"""
    inv = stripe_get_invoice(invoice_id)
    if not is_toukosan(inv):
        return "NG: とうこさんの請求書ではありません"
    rows = ledger_rows(invoice_id); last = _latest(rows)
    if last and last.get("attempt") == attempt and last.get("status") in ("pending_notify", "pending"):
        ok = ledger_update(invoice_id, attempt, {"status": last["status"]}, {"status": "sent", "sent_at": _iso(), "nonce": None, "note": "彩さんが手動で送付"})
        return "OK: 承認待ちだった行を送付済みにしました" if ok else "NG: 同時に別の処理が進みました"
    if (attempt == 1 and not rows) or (attempt > 1 and last.get("attempt") == attempt - 1 and last.get("status") == "sent"):
        ok = ledger_insert({"invoice_id": invoice_id, "attempt": attempt, "status": "sent", "sent_at": _iso(),
                            "customer_id": inv.get("customer", ""), "note": "彩さんが手動で送付"})
        return f"OK: {invoice_id} を {attempt}通目送付済みにしました（次は{REMIND_INTERVAL_DAYS}日後に{attempt + 1}通目の承認依頼）" if ok else "NG: 既に行があります"
    return f"NG: 今の状態（{last.get('attempt')}通目 {last.get('status')}）からは {attempt}通目の印を付けられません"


def execute(invoice_id: str, attempt: int, token: str) -> tuple:
    """承認ボタンから。(ok, text)。送信権＝「status=pending かつ nonce一致」の行だけを sending に更新できた1人。"""
    _require_config()
    rows = ledger_rows(invoice_id)
    row = next((r for r in rows if int(r["attempt"]) == attempt), None)
    if not row or row.get("status") != "pending" or not row.get("nonce") \
            or not hmac.compare_digest(approve_token(invoice_id, attempt, row["nonce"]), token or ""):
        return False, f"NG: この依頼は処理済みか、承認待ちの状態ではありません（今: {row.get('status') if row else 'なし'}）。"
    my_lock = secrets.token_hex(8)
    if not ledger_update(invoice_id, attempt, {"status": "pending", "nonce": row["nonce"]}, {"status": "sending", "lock_id": my_lock}):
        return False, "NG: 同時に別の操作が進んでいます。もう一方の結果をお待ちください。"
    info = customer_info(row.get("customer_id") or "")
    inv = stripe_get_invoice(invoice_id)                       # 宛先を取り終えた「送る直前」に最新の状態を見る
    if not is_failed_open(inv):
        ledger_update(invoice_id, attempt, {"status": "sending", "lock_id": my_lock}, {"status": "sent", "sent_at": _iso(), "nonce": None, "note": "送信前に支払い済み・送らず"})
        return False, f"NG: この請求書はもう「{inv.get('status')}」（支払い済みなど）です。送信しません。"
    uid = row.get("line_user_id") or info["line_user_id"]
    if not uid:
        ledger_update(invoice_id, attempt, {"status": "sending", "lock_id": my_lock}, {"status": "pending"})
        return False, "NG: 名簿にLINE IDがありません。"
    retry_key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"toukosan-remind:{invoice_id}:{attempt}"))
    try:
        line_client(uid, build_message(attempt, inv.get("hosted_invoice_url", "") or ""), retry_key)
    except urllib.error.HTTPError as e:
        if 400 <= e.code < 500:                                # LINEが受け取っていないと分かる失敗 → 承認待ちに戻す
            ledger_update(invoice_id, attempt, {"status": "sending", "lock_id": my_lock}, {"status": "pending"})
            return False, f"NG: LINEが受け付けませんでした（HTTP {e.code}）。宛先や設定を確認してください。"
        ledger_update(invoice_id, attempt, {"status": "sending", "lock_id": my_lock}, {"status": "unknown", "nonce": None, "note": f"LINE HTTP {e.code}"})
        _notify_unknown(invoice_id, attempt, info, inv)
        return False, "NG: 送れたかどうか確認できませんでした。本人に届いているか確認してください（自動では再送しません）。"
    except Exception as e:  # noqa: BLE001 タイムアウト・応答喪失＝届いたかもしれない → 結果不明として人に渡す（Sol指摘#2）
        ledger_update(invoice_id, attempt, {"status": "sending", "lock_id": my_lock}, {"status": "unknown", "nonce": None, "note": f"{type(e).__name__}"})
        _notify_unknown(invoice_id, attempt, info, inv)
        return False, "NG: 送れたかどうか確認できませんでした。本人に届いているか確認してください（自動では再送しません）。"
    ledger_update(invoice_id, attempt, {"status": "sending", "lock_id": my_lock}, {"status": "sent", "sent_at": _iso(), "nonce": None})
    return True, f"✅ 送信しました\n\n{attempt}通目を {info['name'] or '本人'} さんへ送りました。\nこのページは閉じて大丈夫です。"


def _notify_unknown(invoice_id: str, attempt: int, info: dict, inv: dict) -> None:
    try:
        line_admin(f"⚠️ とうこさん {attempt}通目：送れたか不明\n\n{_who(info, inv)}\ninvoice: {invoice_id}\n\n"
                   f"LINEの応答が確認できませんでした。本人に届いているかを確認し、届いていなければ手動で送ってください。自動では再送しません。")
    except Exception as e:  # noqa: BLE001
        _log(f"unknown-notify failed: {e}")


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
    row = next((r for r in ledger_rows(invoice_id) if int(r["attempt"]) == attempt), None)
    if not row or row.get("status") != "pending" or not row.get("nonce") \
            or not hmac.compare_digest(approve_token(invoice_id, attempt, row["nonce"]), token or ""):
        return 403, _DONE_PAGE.replace("__TEXT__", "この依頼は処理済みか、無効なリンクです。")
    inv = stripe_get_invoice(invoice_id)
    info = customer_info(row.get("customer_id") or inv.get("customer", ""))
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
                if not bearer_ok(auth, "start") or not invoice_id:
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
