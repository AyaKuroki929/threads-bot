# -*- coding: utf-8 -*-
"""payment_remind.py を、Stripe / Supabase / LINE を偽物に差し替えて「わざと壊して」確かめる。"""
import os, sys, json, io, threading, urllib.request, urllib.error, importlib
os.environ.update({"STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x", "SUPABASE_URL": "https://sb.test",
                   "SUPABASE_SERVICE_KEY": "sbkey", "ADMIN_NOTIFY_LINE_TOKEN": "admin_tok", "LINE_CHANNEL_ACCESS_TOKEN": "client_tok"})
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
pr = importlib.import_module("payment_remind")
PROD = pr.TOUKOSAN_PRODUCT_ID

class World:
    def __init__(self):
        self.invoices = {}; self.admin = []; self.client = []; self.fail_admin = False; self.fail_client = False
        self.line_users = {"cus_A": {"line_user_id": "U_A", "display_name": "杉村真理子"}}
        self.salons = {"cus_A": {"salon_name": "okaosori.piccolo"}}
        self.on_get = None   # 読み取り時に差し込む（競合の再現用）
        self.seen_keys = set()
    def add(self, iid, cust="cus_A", product=PROD, status="open", attempts=1, remaining=2750, meta=None):
        self.invoices[iid] = {"id": iid, "customer": cust, "status": status, "attempt_count": attempts, "amount_due": 2750,
                              "amount_remaining": remaining, "collection_method": "charge_automatically", "hosted_invoice_url": "https://inv/" + iid,
                              "lines": {"data": [{"pricing": {"price_details": {"product": product}}}], "has_more": False}, "metadata": dict(meta or {})}
W = World()
class R:
    def __init__(s, b, status=200): s.b = b; s.status = status
    def __enter__(s): return s
    def __exit__(s, *a): pass
    def read(s): return s.b
def fake_urlopen(req, timeout=20):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    method = getattr(req, "method", "GET") or "GET"
    if url.startswith("https://api.stripe.com/v1/invoices"):
        path = url.split("/v1/invoices", 1)[1]
        if path.startswith("?"):
            data = [v for v in W.invoices.values() if v["status"] == "open"]
            return R(json.dumps({"data": data, "has_more": False}).encode())
        iid = path.strip("/").split("/")[0].split("?")[0]
        inv = W.invoices[iid]
        if method == "POST":
            form = dict(urllib.parse.parse_qsl(req.data.decode()))
            for k, v in form.items():
                key = k[len("metadata["):-1]
                if v == "": inv["metadata"].pop(key, None)
                else: inv["metadata"][key] = v
            return R(json.dumps(inv).encode())
        if W.on_get: W.on_get(inv)
        return R(json.dumps(inv).encode())
    if url.startswith("https://sb.test/rest/v1/line_users"):
        cus = urllib.parse.unquote(url.split("eq.")[1].split("&")[0]); u = W.line_users.get(cus)
        return R(json.dumps([u] if u else []).encode())
    if url.startswith("https://sb.test/rest/v1/salons"):
        cus = urllib.parse.unquote(url.split("eq.")[1].split("&")[0]); s_ = W.salons.get(cus)
        return R(json.dumps([s_] if s_ else []).encode())
    if url.endswith("/broadcast"):
        if W.fail_admin: raise urllib.error.HTTPError(url, 500, "x", {}, io.BytesIO(b""))
        W.admin.append(json.loads(req.data)["messages"][0]["text"]); return R(b"{}")
    if url.endswith("/push"):
        if W.fail_client: raise urllib.error.HTTPError(url, 500, "x", {}, io.BytesIO(b""))
        key = req.get_header("X-line-retry-key")
        if key and key in W.seen_keys:    # 本物のLINEと同じく、同じ retry key の2通目は 409＋受理ID
            import email.message; m = email.message.Message(); m["x-line-accepted-request-id"] = "dup"
            raise urllib.error.HTTPError(url, 409, "x", m, io.BytesIO(b""))
        W.seen_keys.add(key)
        W.client.append((json.loads(req.data)["to"], json.loads(req.data)["messages"][0]["text"], key)); return R(b"{}")
    raise RuntimeError("unexpected url " + url)
import urllib.parse
pr.urllib.request.urlopen = fake_urlopen
fails = []
def check(name, cond, detail=""):
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  << {detail}")); fails.append(name) if not cond else None
def approve_url_token(iid, attempt):
    return pr.approve_token(iid, attempt, W.invoices[iid]["metadata"]["tk_nonce"])

print("(1) 承認ページが表示できる（CSSの{}で500にならない）")
W.add("in_1"); r = pr.request_attempt("in_1", 1)
code, page = pr.render_approve_page("in_1", 1, approve_url_token("in_1", 1))
check("HTTP 200", code == 200, code); check("本文プレビューが載る", "今月のとうこさんのカード決済" in page)
check("宛先名が載る", "杉村真理子" in page); check("JSが壊れていない", "addEventListener" in page and "__" not in page.replace("__proto__", ""))
check("承認依頼LINEは彩さん宛1通", len(W.admin) == 1 and "要承認 1通目" in W.admin[0], W.admin)
check("本人にはまだ送っていない", W.client == [])

print("(2) 承認→送信は一度だけ。古いURLは死ぬ")
ok, text = pr.execute("in_1", 1, approve_url_token("in_1", 1) if "tk_nonce" in W.invoices["in_1"]["metadata"] else "")
check("送信できる", ok, text); check("本人へ1通", len(W.client) == 1 and W.client[0][0] == "U_A")
check("retry key付き", bool(W.client[0][2])); check("状態 sent1", W.invoices["in_1"]["metadata"]["tk_remind"] == "sent1")
ok2, text2 = pr.execute("in_1", 1, "anything"); check("同じURLで2回目は拒否", not ok2 and len(W.client) == 1, text2)
code, page = pr.render_approve_page("in_1", 1, "anything"); check("処理済みの承認ページは客の情報を出さない", code == 403 and "杉村" not in page)
check("完了LINEは送らない（彩さんの枠を使わない）", len(W.admin) == 1)

print("(3) 巻き戻し禁止：送った後に古い失敗通知が来ても pending に戻らない")
r = pr.request_attempt("in_1", 1); check("start が拒否", "進めない" in r, r)
check("状態は sent1 のまま", W.invoices["in_1"]["metadata"]["tk_remind"] == "sent1")
r = pr.process_invoice(W.invoices["in_1"]); check("見回りも待機", "待機中" in r, r)

print("(4) 同時タップは片方だけ送る")
W.add("in_2"); pr.request_attempt("in_2", 1); tok = approve_url_token("in_2", 1); W.client.clear()
results = []; barrier = threading.Barrier(2)
def go():
    barrier.wait(); results.append(pr.execute("in_2", 1, tok))
ts = [threading.Thread(target=go) for _ in range(2)]; [t.start() for t in ts]; [t.join() for t in ts]
check("本人へは1通だけ", len(W.client) == 1, W.client)
check("少なくとも1人は送れている", any(r[0] for r in results), results)

print("(5) 送る直前に払われていたら送らない")
W.add("in_3"); pr.request_attempt("in_3", 1); tok = approve_url_token("in_3", 1); W.client.clear()
def paid_after_lock(inv):
    if inv["metadata"].get("tk_lock"): inv["status"] = "paid"; inv["amount_remaining"] = 0
W.on_get = paid_after_lock
ok, text = pr.execute("in_3", 1, tok); W.on_get = None
check("送らない", not ok and W.client == [], text); check("paid_skip", W.invoices["in_3"]["metadata"]["tk_remind"] == "paid_skip")

print("(6) 彩さん宛LINEが失敗したら状態を進めない（翌朝もう一度依頼が出る）")
W.add("in_4"); W.fail_admin = True
try: pr.request_attempt("in_4", 1); check("例外になる", False)
except Exception: check("例外になる", True)
W.fail_admin = False
check("metadata は空のまま", "tk_remind" not in W.invoices["in_4"]["metadata"])
r = pr.request_attempt("in_4", 1); check("翌朝は依頼が出る", "承認依頼を出した" in r, r)

print("(7) 未試行・他社商品・残額0は対象外")
W.add("in_5", attempts=0); W.add("in_6", product="prod_other"); W.add("in_7", remaining=0); W.admin.clear()
out = pr.poll(dry=True)
check("未試行は対象外", any("in_5" in x and "対象外" in x for x in out["results"]), out["results"])
check("他社商品は無視", not any("in_6" in x for x in out["results"]))
check("残額0は対象外", any("in_7" in x and "対象外" in x for x in out["results"]))
check("dryではLINE 0通", W.admin == [])

print("(8) 送信後の記録だけ失敗した形（sendingN）は、再送せず記録を直す")
W.add("in_8", meta={"tk_remind": "sending2", "tk_at": "2026-09-20T09:00:00+09:00"}); W.client.clear()
r = pr.process_invoice(W.invoices["in_8"]); check("修復", "修復" in r and W.invoices["in_8"]["metadata"]["tk_remind"] == "sent2", r)
check("本人に再送しない", W.client == [])

print("(9) 3日後に2通目、6日後に3通目、9日後に手動フォロー")
W.add("in_9", meta={"tk_remind": "sent1", "tk_sent1_at": "2026-09-01T09:00:00+09:00"}); W.admin.clear()
r = pr.process_invoice(W.invoices["in_9"]); check("2通目の承認依頼", "2通目" in r and "要承認 2通目" in W.admin[-1], r)
W.invoices["in_9"]["metadata"].update({"tk_remind": "sent3", "tk_sent3_at": "2026-09-01T09:00:00+09:00"}); W.admin.clear()
r = pr.process_invoice(W.invoices["in_9"]); check("手動フォロー通知", "手動フォロー" in r and "手動フォロー必要" in W.admin[-1], r)
check("escalated", W.invoices["in_9"]["metadata"]["tk_remind"] == "escalated")

print("(10) 合言葉")
import datetime
d = pr._now().date().isoformat(); good = pr._hmac("sbkey", f"poll:{d}")
check("正しいBearerは通る", pr.bearer_ok("Bearer " + good, "poll")); check("用途違いは拒否", not pr.bearer_ok("Bearer " + good, "mark"))
check("鍵が違えば拒否", not pr.bearer_ok("Bearer " + pr._hmac("other", f"poll:{d}"), "poll"))
pr.STRIPE_WEBHOOK_SECRET = ""
try: pr.execute("in_1", 1, "x"); check("鍵が空なら動かない", False)
except pr.ConfigError: check("鍵が空なら動かない", True)
pr.STRIPE_WEBHOOK_SECRET = "whsec_x"

print("(11) LINEの409（受理済み）は成功扱い、二重送信にしない")
def push_409(req, timeout=10):
    import email.message; m = email.message.Message(); m["x-line-accepted-request-id"] = "abc"
    raise urllib.error.HTTPError(req.full_url, 409, "x", m, io.BytesIO(b""))
orig = pr.urllib.request.urlopen
pr.urllib.request.urlopen = lambda req, timeout=20: push_409(req, timeout) if req.full_url.endswith("/push") else orig(req, timeout)
W.add("in_10"); pr.request_attempt("in_10", 1); ok, text = pr.execute("in_10", 1, approve_url_token("in_10", 1))
check("409は届いた扱い", ok and W.invoices["in_10"]["metadata"]["tk_remind"] == "sent1", text)
pr.urllib.request.urlopen = orig

print("\n" + ("🚨 失敗: " + ", ".join(fails) if fails else "✅ 全項目パス"))
sys.exit(1 if fails else 0)
