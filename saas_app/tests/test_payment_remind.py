# -*- coding: utf-8 -*-
"""payment_remind.py を、Stripe / Supabase台帳 / LINE を偽物に差し替えて「わざと壊して」確かめる。"""
import os, sys, json, io, threading, urllib.request, urllib.error, urllib.parse, importlib, email.message
os.environ.update({"STRIPE_SECRET_KEY": "sk_test_x", "STRIPE_WEBHOOK_SECRET": "whsec_x", "SUPABASE_URL": "https://sb.test",
                   "SUPABASE_SERVICE_KEY": "sbkey", "ADMIN_NOTIFY_LINE_TOKEN": "admin_tok", "LINE_CHANNEL_ACCESS_TOKEN": "client_tok"})
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
pr = importlib.import_module("payment_remind")
PROD = pr.TOUKOSAN_PRODUCT_ID
LOCK = threading.Lock()

class World:
    def __init__(self):
        self.invoices = {}; self.ledger = {}; self.admin = []; self.client = []; self.seen_keys = set()
        self.fail_admin = False; self.client_mode = "ok"; self.ledger_missing = False
        self.line_users = {"cus_A": {"line_user_id": "U_A", "display_name": "杉村真理子"}}
        self.salons = {"cus_A": {"salon_name": "okaosori.piccolo"}}
        self.before_client_send = None; self.notify_hook = None; self.admin_calls = 0
    def add(self, iid, cust="cus_A", product=PROD, status="open", attempts=1, remaining=2750):
        self.invoices[iid] = {"id": iid, "customer": cust, "status": status, "attempt_count": attempts, "amount_due": 2750,
                              "amount_remaining": remaining, "collection_method": "charge_automatically", "hosted_invoice_url": "https://inv/" + iid,
                              "lines": {"data": [{"pricing": {"price_details": {"product": product}}}], "has_more": False}}
W = World()
class R:
    def __init__(s, b, status=200): s.b = b; s.status = status
    def __enter__(s): return s
    def __exit__(s, *a): pass
    def read(s): return s.b
def _err(url, code, headers=None, body=b""):
    m = email.message.Message()
    for k, v in (headers or {}).items(): m[k] = v
    return urllib.error.HTTPError(url, code, "x", m, io.BytesIO(body))
def fake_urlopen(req, timeout=20):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    method = getattr(req, "method", "GET") or "GET"
    if url.startswith("https://api.stripe.com/v1/invoices"):
        path = url.split("/v1/invoices", 1)[1]
        if path.startswith("?"):
            data = [v for v in W.invoices.values() if v["status"] == "open"]
            return R(json.dumps({"data": data, "has_more": False}).encode())
        iid = path.strip("/").split("/")[0].split("?")[0]
        return R(json.dumps(W.invoices[iid]).encode())
    if url.startswith("https://sb.test/rest/v1/payment_reminder_attempts"):
        if W.ledger_missing: raise _err(url, 404, body=b'{"code":"PGRST205","message":"Could not find the table"}')
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        def match(row):
            for k, v in q.items():
                if k in ("order", "select", "limit"): continue
                if v == "is.null":
                    if row.get(k) is not None: return False
                elif v == "not.is.null":
                    if row.get(k) is None: return False
                elif v.startswith("lt."):
                    val = row.get(k)
                    if k.endswith("_at"):
                        if val is None or not (str(val) < urllib.parse.unquote(v[3:])): return False
                    elif not (int(val or 0) < int(v[3:])): return False
                elif str(row.get(k)) != v[len("eq."):]: return False
            return True
        if W.notify_hook and method == "PATCH":
            body = json.loads(req.data or b"{}")
            if body.get("notify_lock") and "notified_at" not in body:   # 送信権を取りに来た瞬間だけ
                W.notify_hook()
        with LOCK:
            if method == "POST":
                row = json.loads(req.data); key = (row["invoice_id"], int(row["attempt"]))
                if key in W.ledger: raise _err(url, 409, body=b'{"code":"23505"}')
                W.ledger[key] = {"notified_at": None, "notify_lock": None, "notify_lock_at": None, "notify_tries": 0, "updated_at": pr._iso(), **row}; return R(b"")
            if method == "PATCH":
                patch = json.loads(req.data); hit = [r for r in W.ledger.values() if match(r)]
                for r in hit: r.update(patch)
                return R(json.dumps(hit).encode())
            rows = sorted([r for r in W.ledger.values() if match(r)], key=lambda r: int(r["attempt"]))
            return R(json.dumps(rows).encode())
    if url.startswith("https://sb.test/rest/v1/line_users"):
        cus = urllib.parse.unquote(url.split("eq.")[1].split("&")[0]); u = W.line_users.get(cus); return R(json.dumps([u] if u else []).encode())
    if url.startswith("https://sb.test/rest/v1/salons"):
        cus = urllib.parse.unquote(url.split("eq.")[1].split("&")[0]); s_ = W.salons.get(cus); return R(json.dumps([s_] if s_ else []).encode())
    if url.endswith("/broadcast"):
        W.admin_calls += 1
        if W.fail_admin == "lost":      # LINEは受理したのに応答だけ失われる
            key = req.get_header("X-line-retry-key")
            with LOCK:
                if key not in W.seen_keys: W.seen_keys.add(key); W.admin.append(json.loads(req.data)["messages"][0]["text"])
            raise TimeoutError("lost")
        if W.fail_admin: raise _err(url, 500)
        key = req.get_header("X-line-retry-key")
        with LOCK:
            if key and key in W.seen_keys: raise _err(url, 409, {"x-line-accepted-request-id": "dup"})
            if key: W.seen_keys.add(key)
            W.admin.append(json.loads(req.data)["messages"][0]["text"])
        return R(b"{}")
    if url.endswith("/push"):
        if W.before_client_send: W.before_client_send()
        key = req.get_header("X-line-retry-key")
        if W.client_mode == "timeout": raise TimeoutError("timed out")
        if W.client_mode == "4xx": raise _err(url, 400)
        with LOCK:
            if key and key in W.seen_keys: raise _err(url, 409, {"x-line-accepted-request-id": "dup"})
            W.seen_keys.add(key); W.client.append((json.loads(req.data)["to"], json.loads(req.data)["messages"][0]["text"], key))
        return R(b"{}")
    raise RuntimeError("unexpected url " + url)
pr.urllib.request.urlopen = fake_urlopen
fails = []
def check(name, cond, detail=""):
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  << {detail}")); cond or fails.append(name)
def tok(iid, attempt): return pr.approve_token(iid, attempt, W.ledger[(iid, attempt)]["nonce"])
def st(iid, attempt): return W.ledger.get((iid, attempt), {}).get("status")

print("(1) 承認ページが表示できる・依頼は彩さん宛1通・本人にはまだ送らない")
W.add("in_1"); r = pr.request_attempt("in_1", 1)
code, page = pr.render_approve_page("in_1", 1, tok("in_1", 1))
check("HTTP 200", code == 200, code); check("プレビューと宛先", "今月のとうこさんのカード決済" in page and "杉村真理子" in page)
check("彩さん宛1通", len(W.admin) == 1 and "要承認 1通目" in W.admin[0], W.admin); check("本人0通", W.client == [])
check("台帳 pending", st("in_1", 1) == "pending")

print("(2) 承認→送信は一度だけ。古いURLは死ぬ。完了LINEは送らない")
t1 = tok("in_1", 1); ok, text = pr.execute("in_1", 1, t1)
check("送信できる", ok, text); check("本人へ1通・retry key付き", len(W.client) == 1 and W.client[0][2]); check("台帳 sent", st("in_1", 1) == "sent")
ok2, text2 = pr.execute("in_1", 1, t1); check("同じURLで2回目は拒否", not ok2 and len(W.client) == 1, text2)
code, page = pr.render_approve_page("in_1", 1, t1); check("処理済みページは客情報を出さない", code == 403 and "杉村" not in page)
check("彩さん宛は1通のまま", len(W.admin) == 1)

print("(3) 巻き戻し禁止：送った後に古い失敗通知（start）が来ても1通目を作り直さない")
r = pr.request_attempt("in_1", 1); check("start が拒否", "進めない" in r, r); check("台帳は sent のまま", st("in_1", 1) == "sent")
check("見回りも待機", "待機中" in pr.process_invoice(W.invoices["in_1"]))

print("(4) 同時に2つの処理が1通目を起こしても承認依頼は1通")
W.add("in_2"); W.admin.clear(); barrier = threading.Barrier(2); res = []
def go(): barrier.wait(); res.append(pr.request_attempt("in_2", 1))
ts = [threading.Thread(target=go) for _ in range(2)]; [t.start() for t in ts]; [t.join() for t in ts]
check("彩さん宛1通", len(W.admin) == 1, W.admin); check("台帳の行は1つ", sum(1 for k in W.ledger if k[0] == "in_2") == 1)

print("(5) 同時タップは片方だけ送る（条件付き更新）")
t2 = tok("in_2", 1); W.client.clear(); res = []; barrier = threading.Barrier(2)
def go2(): barrier.wait(); res.append(pr.execute("in_2", 1, t2))
ts = [threading.Thread(target=go2) for _ in range(2)]; [t.start() for t in ts]; [t.join() for t in ts]
check("本人へ1通", len(W.client) == 1, W.client); check("成功1・拒否1", sorted(r_[0] for r_ in res) == [False, True], res)

print("(6) 宛先を取っている間に払われても送らない")
W.add("in_3"); pr.request_attempt("in_3", 1); t3 = tok("in_3", 1); W.client.clear()
_ci = pr.customer_info
def ci_then_paid(c): r_ = _ci(c); W.invoices["in_3"]["status"] = "paid"; W.invoices["in_3"]["amount_remaining"] = 0; return r_
pr.customer_info = ci_then_paid; ok, text = pr.execute("in_3", 1, t3); pr.customer_info = _ci
check("送らない", not ok and W.client == [], text); check("台帳は送らずに閉じる", st("in_3", 1) == "sent" and "送らず" in W.ledger[("in_3", 1)]["note"])

print("(7) 彩さん宛LINEが失敗したら翌朝やり直す（行は残る・二重には作らない）")
W.add("in_4"); W.fail_admin = True
try: pr.request_attempt("in_4", 1); check("例外", False)
except Exception: check("例外", True)
W.fail_admin = False; check("行は pending_notify", st("in_4", 1) == "pending_notify" and W.ledger[("in_4",1)]["notify_tries"] == 1); W.admin.clear()
r = pr.process_invoice(W.invoices["in_4"]); check("翌朝は依頼を再送", "再送" in r and len(W.admin) == 1 and st("in_4", 1) == "pending", r)
check("届いた印", W.ledger[("in_4",1)]["notified_at"] is not None)

print("(8) 送れたか分からない（タイムアウト）は unknown。再送しない。彩さんに1通")
W.add("in_5"); pr.request_attempt("in_5", 1); t5 = tok("in_5", 1); W.client.clear(); W.admin.clear(); W.client_mode = "timeout"
ok, text = pr.execute("in_5", 1, t5); W.client_mode = "ok"
check("unknown", not ok and st("in_5", 1) == "unknown", text); check("彩さんに『送れたか不明』1通", len(W.admin) == 1 and "送れたか不明" in W.admin[0])
ok, text = pr.execute("in_5", 1, t5); check("同じURLをもう一度押しても送らない", not ok and W.client == [], text)
W.admin.clear(); r = pr.process_invoice(W.invoices["in_5"]); check("見回りも触らない・LINE 0", "人の確認待ち" in r and W.admin == [], r)

print("(9) LINEの4xx（受け取っていない）は承認待ちに戻り、同じURLで押し直せる")
W.add("in_6"); pr.request_attempt("in_6", 1); t6 = tok("in_6", 1); W.client.clear(); W.client_mode = "4xx"
ok, text = pr.execute("in_6", 1, t6); W.client_mode = "ok"
check("pending に戻る", not ok and st("in_6", 1) == "pending", text)
ok, text = pr.execute("in_6", 1, t6); check("押し直しで送れる", ok and len(W.client) == 1, text)

print("(10) 未試行・他社商品・残額0は対象外。dryはLINE 0")
W.add("in_7", attempts=0); W.add("in_8", product="prod_other"); W.add("in_9", remaining=0); W.admin.clear()
out = pr.poll(dry=True)
check("未試行は対象外", any("in_7" in x and "対象外" in x for x in out["results"]), out["results"])
check("他社商品は無視", not any("in_8" in x for x in out["results"])); check("残額0は対象外", any("in_9" in x and "対象外" in x for x in out["results"]))
check("dryでLINE 0", W.admin == [])

print("(11) 3日後に2通目、6日後に3通目、9日後に手動フォロー（1回だけ）")
W.add("in_10"); W.ledger[("in_10", 1)] = {"invoice_id": "in_10", "attempt": 1, "status": "sent", "sent_at": "2026-09-01T09:00:00+09:00", "customer_id": "cus_A", "line_user_id": "U_A"}; W.admin.clear()
r = pr.process_invoice(W.invoices["in_10"]); check("2通目の承認依頼", "2通目" in r and "要承認 2通目" in W.admin[-1], r)
W.ledger[("in_10", 2)].update({"status": "sent", "sent_at": "2026-09-04T09:00:00+09:00"})
r = pr.process_invoice(W.invoices["in_10"]); check("3通目の承認依頼", "3通目" in r, r)
W.ledger[("in_10", 3)].update({"status": "sent", "sent_at": "2026-09-07T09:00:00+09:00"}); W.admin.clear()
r = pr.process_invoice(W.invoices["in_10"]); check("手動フォロー通知", "手動フォロー" in r and "手動フォロー必要" in W.admin[-1], r)
r = pr.process_invoice(W.invoices["in_10"]); check("翌日は再通知しない", len(W.admin) == 1 and "対応済み" in r, r)

print("(12) 合言葉・設定・台帳なし")
d = pr._now().date().isoformat(); good = pr._hmac("sbkey", f"poll:{d}")
check("正しいBearerは通る", pr.bearer_ok("Bearer " + good, "poll")); check("用途違い(start)は拒否", not pr.bearer_ok("Bearer " + good, "start"))
check("鍵違いは拒否", not pr.bearer_ok("Bearer " + pr._hmac("other", f"poll:{d}"), "poll"))
pr.STRIPE_WEBHOOK_SECRET = ""
try: pr.execute("in_1", 1, "x"); check("鍵が空なら動かない", False)
except pr.ConfigError: check("鍵が空なら動かない", True)
pr.STRIPE_WEBHOOK_SECRET = "whsec_x"
W.ledger_missing = True; out = pr.poll(dry=True); W.ledger_missing = False
check("台帳が無ければ失敗として返す（黙らない）", not out["ok"] and any("supabase_tables.sql" in e for e in out["errors"]), out["errors"])

print("(14) 彩さん宛の依頼：受理されたのに応答だけ失われても、翌朝2通目にならない")
W.add("in_13"); W.admin.clear(); W.fail_admin = "lost"
try: pr.request_attempt("in_13", 1)
except Exception: pass
W.fail_admin = False
check("1通は届いている・行は pending_notify", len(W.admin) == 1 and st("in_13", 1) == "pending_notify")
r = pr.process_invoice(W.invoices["in_13"]); check("翌朝の再送はLINE側で重複排除→2通にならない", len(W.admin) == 1 and st("in_13", 1) == "pending", (r, W.admin))

print("(15) pending_notify の再送が同時に走っても依頼は1通")
W.add("in_14"); W.ledger[("in_14", 1)] = {"invoice_id": "in_14", "attempt": 1, "status": "pending_notify", "nonce": "n", "customer_id": "cus_A", "line_user_id": "U_A", "notified_at": None, "notify_lock": None, "notify_tries": 0, "updated_at": pr._iso()}
W.admin.clear(); W.admin_calls = 0; barrier = threading.Barrier(2)
_hits = [0]
def _hook():
    _hits[0] += 1
    if _hits[0] <= 2: barrier.wait(timeout=2)
W.notify_hook = _hook
ts = [threading.Thread(target=lambda: pr.process_invoice(W.invoices["in_14"])) for _ in range(2)]; [t.start() for t in ts]; [t.join() for t in ts]
W.notify_hook = None
check("LINE呼び出し1回・届いた1通", W.admin_calls == 1 and len(W.admin) == 1, (W.admin_calls, len(W.admin)))

print("(16) 3回失敗したら諦める（毎朝増えない）／手動フォロー通知が失敗しても翌朝やり直す")
W.add("in_15"); W.ledger[("in_15", 4)] = {"invoice_id": "in_15", "attempt": 4, "status": "escalated", "customer_id": "cus_A", "notified_at": None, "notify_lock": None, "notify_tries": 0, "updated_at": pr._iso()}
W.fail_admin = True; W.admin.clear()
for _ in range(5):
    try: pr.process_invoice(W.invoices["in_15"])
    except Exception: pass
W.fail_admin = False
check("試行は3回で止まる", W.ledger[("in_15", 4)]["notify_tries"] == 3, W.ledger[("in_15", 4)]["notify_tries"])
W.ledger[("in_15", 4)]["notify_tries"] = 0
r = pr.process_invoice(W.invoices["in_15"]); check("復旧後は届く", len(W.admin) == 1 and "手動フォロー必要" in W.admin[0], r)

print("(17) 送信中のまま10分以上止まった行は結果不明にして人に渡す")
W.add("in_16"); W.ledger[("in_16", 1)] = {"invoice_id": "in_16", "attempt": 1, "status": "sending", "lock_id": "x", "nonce": "n", "customer_id": "cus_A", "line_user_id": "U_A", "notified_at": None, "notify_lock": None, "notify_tries": 0, "updated_at": "2026-09-01T09:00:00+09:00"}
W.admin.clear(); r = pr.process_invoice(W.invoices["in_16"])
check("unknown に", st("in_16", 1) == "unknown" and len(W.admin) == 1 and "送れたか不明" in W.admin[0], (r, st("in_16", 1)))
check("本人へは送らない", not any(c[0] == "U_A" and "in_16" in c[1] for c in W.client))

print("(18) 請求書0件の朝でも台帳が無ければ気づける")
saved = dict(W.invoices); W.invoices.clear(); W.ledger_missing = True; out = pr.poll(dry=True); W.ledger_missing = False; W.invoices.update(saved)
check("ok=false", not out["ok"] and any("台帳" in e for e in out["errors"]), out)

print("(19) 取ったまま10分以上止まった配送の印は回収して届ける")
W.add("in_17"); W.ledger[("in_17", 1)] = {"invoice_id": "in_17", "attempt": 1, "status": "pending_notify", "nonce": "n", "customer_id": "cus_A", "line_user_id": "U_A", "notified_at": None, "notify_lock": "dead", "notify_lock_at": "2026-09-01T09:00:00+09:00", "notify_tries": 0, "updated_at": pr._iso()}
W.admin.clear(); r = pr.process_invoice(W.invoices["in_17"])
check("回収して届く・pendingに", len(W.admin) == 1 and st("in_17", 1) == "pending", (r, st("in_17", 1)))
W.ledger[("in_17", 1)].update({"notified_at": None, "notify_lock": "alive", "notify_lock_at": pr._iso(), "status": "pending_notify"}); W.admin.clear()
r = pr.process_invoice(W.invoices["in_17"]); check("生きている印は奪わない", W.admin == [], (r, W.admin))

print("(20) 『届いた』と『承認待ちにする』は同じ更新（承認URLが永久403にならない）")
W.add("in_18"); W.ledger[("in_18", 1)] = {"invoice_id": "in_18", "attempt": 1, "status": "pending_notify", "nonce": "n", "customer_id": "cus_A", "line_user_id": "U_A", "notified_at": pr._iso(), "notify_lock": None, "notify_lock_at": None, "notify_tries": 0, "updated_at": pr._iso()}
r = pr.process_invoice(W.invoices["in_18"]); check("中間状態を修復", st("in_18", 1) == "pending", (r, st("in_18", 1)))

print("(21) 3回届かなかった知らせは正常扱いに戻さず失敗として返す")
W.add("in_19"); W.ledger[("in_19", 1)] = {"invoice_id": "in_19", "attempt": 1, "status": "unknown", "customer_id": "cus_A", "line_user_id": "U_A", "notified_at": None, "notify_lock": None, "notify_lock_at": None, "notify_tries": 3, "updated_at": pr._iso()}
out = pr.poll(); check("poll が ok=false", not out["ok"] and any("in_19" in e or "3回" in e for e in out["errors"]), out["errors"])

print("(13) 彩さんが手で送った印（mark）")
W.add("in_11"); check("1通目を手動送付済みに", pr.mark_sent("in_11", 1).startswith("OK") and st("in_11", 1) == "sent")
check("同じ通番は二重に付けない", pr.mark_sent("in_11", 1).startswith("NG"))
pr.request_attempt("in_12", 1) if W.add("in_12") is None else None
check("承認待ちの行を手動送付済みに", pr.mark_sent("in_12", 1).startswith("OK") and st("in_12", 1) == "sent")
check("結果不明も彩さんの確認で解決できる", pr.mark_sent("in_5", 1).startswith("OK") and st("in_5", 1) == "sent")

print("\n" + ("🚨 失敗: " + ", ".join(fails) if fails else "✅ 全項目パス"))
sys.exit(1 if fails else 0)
