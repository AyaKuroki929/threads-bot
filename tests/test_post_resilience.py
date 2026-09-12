import os, sys, json, importlib, io, contextlib, urllib.error, shutil
from datetime import datetime as _dt0, timezone as _tz0, timedelta as _td0
from datetime import datetime, timezone, timedelta
os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake")
os.environ.setdefault("SLOT", "noon")
# テストは同じ枠を連続で実行するので、「他の実行が処理中」判定は無効化する
os.environ["POST_STATE_STALE_SEC"] = "0"
os.environ["POST_STATE_RESUME_STALE_SEC"] = "0"
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "")
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import fake_threads_supabase as fakeapi
fakeapi.install()
W = fakeapi.W

import post_state, post_saas
NOTIFY_OK = [True]      # False にすると「送信に失敗した」状況を作れる


def _fake_broadcast(text, token=None, **kw):
    """⚠️ 差し替えるのは**送信そのもの**だけ。_notify_line() は本物を通す。
    _notify_line ごと差し替えると、戻り値の扱いの壊れを検知できない（Sol指摘）。"""
    NOTIFY.append(text)
    return NOTIFY_OK[0]


post_saas.line_broadcast = _fake_broadcast
post_saas.LINE_TOKEN = "dummy"

# 宣伝の使用済みファイルも、本物の読み書きをテスト用の一時ファイルで通す
import tempfile as _tf
_promo_dir = _tf.mkdtemp()
post_saas.PROMO_USED_FILE = os.path.join(_promo_dir, "promo_used.json")
post_saas.PROMO_POOL_FILE = os.path.join(_promo_dir, "promo_pool.json")


class _Clock:
    """待った分だけ時間が進む仮想時計。実時間で待たずに「持ち時間切れ」を再現する。"""
    def __init__(self):
        self.offset = 0.0
        self.slept = 0.0
        self.frozen = None      # 値を入れると時計が止まる（境界の検査用）
    def sleep(self, sec):
        self.offset += sec
        self.slept += sec
    def time(self):
        if self.frozen is not None:
            return self.frozen + self.offset
        return _real_time.time() + self.offset
    def reset(self):
        self.offset = 0.0
        self.slept = 0.0
        self.frozen = None


import time as _real_time
CLOCK = _Clock()
post_saas.time = CLOCK
fakeapi.W.on_latency = CLOCK.sleep
post_state_time = CLOCK
post_state.SUPABASE_URL = os.environ["SUPABASE_URL"]; post_state.SUPABASE_KEY = "fake"
NOTIFY = []
SYNCED = []


def _record_sync_write(salon_name, slot):
    """last_run への**書き込みだけ**を差し替える。日付の判定（過去日は書かない）は
    本物の _sync_last_run() を通す。無処理に差し替えると同期漏れも日付ガードの
    破損も検知できない（2026-09-12 Sol指摘#5）。"""
    SYNCED.append((salon_name, slot))


post_saas._sync_last_run_now = _record_sync_write

SALON = "11111111-1111-1111-1111-111111111111"
# ⚠️ 日付を固定するとCIが翌日に誤警報を出す（2026-09-12 Sol指摘#6）
JST_DATE = _dt0.now(_tz0(_td0(hours=9))).strftime("%Y-%m-%d")
YESTERDAY = (_dt0.now(_tz0(_td0(hours=9))) - _td0(days=1)).strftime("%Y-%m-%d")
TEXTS1 = ["これはテスト本文の1部目です。"]
TEXTS2 = ["これはテスト本文の1部目です。", "これは2部目の返信本文です。"]

def seed_history(salons=None, days=3, include_today_earlier=True):
    """過去の投稿実績を偽DBに入れる（本番の post_logs には履歴があるため）。
    これが無いと、点検が過去日を全部「未投稿」と判定してしまう。"""
    from datetime import datetime as _d, timezone as _z, timedelta as _t
    JSTz = _z(_t(hours=9))
    earlier = {"morning": [], "noon": ["morning"], "evening": ["morning", "noon"]}
    for s_ in (salons or [{"id": SALON}]):
        if include_today_earlier:
            today = _d.now(JSTz)
            for slot in earlier.get(post_saas.SLOT, []):
                W.post_logs.append({
                    "salon_id": s_["id"], "slot": slot, "post_content": f"今日の{slot}",
                    "posted_at": today.replace(hour=7).astimezone(_z.utc).isoformat(),
                    "op_id": f'{s_["id"]}:{today:%Y-%m-%d}:{slot}'})
        for n in range(1, days + 1):
            day = _d.now(JSTz) - _t(days=n)
            for slot, hour in (("morning", 7), ("noon", 12), ("evening", 21)):
                W.post_logs.append({
                    "salon_id": s_["id"], "slot": slot, "post_content": f"過去{n}{slot}",
                    "posted_at": day.replace(hour=hour).astimezone(_z.utc).isoformat(),
                    "op_id": f'{s_["id"]}:{day:%Y-%m-%d}:{slot}'})


def reset(**kw):
    global NOTIFY
    NOTIFY = []
    NOTIFY_OK[0] = True
    SYNCED.clear()
    W.__init__()
    CLOCK.reset()
    W.on_latency = CLOCK.sleep      # __init__ で消えるので毎回つなぎ直す
    W.now_fn = CLOCK.time
    # 取りこぼしの穴埋めは (105) で個別に検証する。ほかのシナリオでは邪魔なので止める
    post_saas.check_previous_slot = lambda salons: 0
    post_saas._run_jst_date = None
    post_saas._deadline = None
    post_state._available = None
    post_state._MEM.clear()
    post_saas._retry_spent = 0.0
    for k, v in kw.items():
        setattr(W, k, v)

_real_check_prev = post_saas.check_previous_slot
_real_ig_cta = post_saas._maybe_add_instagram_cta_saas
_real_pick_post = post_saas.pick_post
_real_get_used = post_saas.get_used_posts

SALON_ROW = {"id": SALON, "salon_name": "テストサロン", "access_token": "TOK",
             "threads_user_id": "USER1", "instagram_url": "", "is_active": True}


def run(texts):
    """1回分の投稿実行。**本物の _run_slot() を通す**（通知・記録・台帳の後始末まで同じ道）。
    返り値 (res, row, exc)。res は _run_slot の (status, detail) を dict にしたもの。"""
    action, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
    if action in ("hold", "skip"):
        return {"action": action, "complete": action == "skip"}, row, None
    if action == "go":
        post_saas.pick_post = lambda name, slot, used, **kw: list(texts)
        post_saas.is_promo_time = lambda name, slot: False
        post_saas.get_used_posts = lambda sid, slot=None: set()
        post_saas._maybe_add_instagram_cta_saas = lambda t, u: t
        post_saas._enforce_threads_limit = lambda t: t
        post_saas._select_topic = lambda t, n: None
    try:
        status, detail, _k = post_saas._run_slot(row, action, SALON_ROW, "USER1", "TOK",
                                             "noon", "@testsalon")
    except Exception as e:
        return {"action": action, "exc": f"{type(e).__name__}: {e}"}, row, e
    cur = post_state.fetch(row["op_id"]) or row
    return {"action": action, "status": status, "note": detail,
            "complete": status == "ok", "logged": bool(cur.get("logged"))}, cur, None


FAILS = []
def check(name, cond, detail=""):
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"  << {detail}"))
    if not cond: FAILS.append(name)

def quiet(fn):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn()
    return r, buf.getvalue()

# ── 1. 公開応答喪失＋状態不明＋一覧が空 → コンテナを増やさない ─────────
print("\n① 公開タイムアウト・状態不明・一覧が空")
reset()
W.publish_behavior = lambda cid, n: "timeout"
W.status_override = "__fail__"
W.list_behavior = "empty"
(res, row, _), out = quiet(lambda: run(TEXTS1))
check("コンテナ作成は1回だけ", W.calls["create"] == 1, f"実測 {W.calls['create']}回")
check("投稿は0件", len(W.posts) == 0, f"実測 {len(W.posts)}")
check("枠は unknown で残る", W.attempts[f"{SALON}:{JST_DATE}:noon"]["status"] == "unknown",
      W.attempts[f"{SALON}:{JST_DATE}:noon"]["status"])
check("記録はしない", len(W.post_logs) == 0)

print("\n②-a 同じ枠を再実行 → 同じコンテナで解決（新規コンテナを作らない）")
W.publish_behavior = lambda cid, n: "ok"
W.status_override = None
W.list_behavior = "normal"
(res2, row2, _), out2 = quiet(lambda: run(TEXTS1))
check("再実行でもコンテナは通算1個", W.calls["create"] == 1, f"実測 {W.calls['create']}")
check("投稿は1件だけ", len(W.posts) == 1, f"実測 {len(W.posts)}")
check("完了扱いになる", W.attempts[f"{SALON}:{JST_DATE}:noon"]["status"] == "logged",
      W.attempts[f"{SALON}:{JST_DATE}:noon"]["status"])
check("post_logs 1件", len(W.post_logs) == 1, f"実測 {len(W.post_logs)}")

print("\n②-b さらに再実行 → 何も起きない")
before = (W.calls["create"], len(W.posts), len(W.post_logs))
(res3, _, _), _ = quiet(lambda: run(TEXTS1))
check("skip される", res3.get("action") == "skip", res3.get("action"))
check("投稿も記録も増えない", (W.calls["create"], len(W.posts), len(W.post_logs)) == before)

# ── 3. FINISHED＋同文の古い投稿が一覧にある → 誤って成功にしない ────────
print("\n③ コンテナはFINISHED・同文の別投稿が一覧にある")
reset()
old_ts = (datetime.now(timezone.utc) - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%S+0000")
W.posts.insert(0, {"id": "OLD_POST", "text": TEXTS1[0], "timestamp": old_ts})
W.publish_behavior = lambda cid, n: "timeout"
W.status_override = "FINISHED"
(res, row, _), out = quiet(lambda: run(TEXTS1))
check("OLD_POST を成功として採用しない", "OLD_POST" not in json.dumps(W.attempts, ensure_ascii=False),
      json.dumps(W.attempts, ensure_ascii=False)[:200])
check("コンテナは1個のまま", W.calls["create"] == 1, W.calls["create"])
check("unknown で次へ引き継ぐ", W.attempts[f"{SALON}:{JST_DATE}:noon"]["status"] == "unknown")

# ── 4. 同文の別投稿があっても、投稿IDを推測しない ──────────────────
print("\n④ 同文の別投稿があるところで公開応答が失われる")
from datetime import datetime as _dt, timezone as _tz, timedelta as _td
reset()
W.posts.insert(0, {"id": "OTHER", "text": TEXTS2[0],
                   "timestamp": (_dt.now(_tz.utc) - _td(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S+0000")})
W.publish_behavior = lambda cid, n: "published_then_timeout"
W.status_override = lambda cid: W.containers[cid]["status"]
(res, row, _), out = quiet(lambda: run(TEXTS2))
parts = W.attempts[f"{SALON}:{JST_DATE}:noon"]["parts"]
check("OTHER を返信先にしない", all(p.get("post_id") != "OTHER" for p in parts),
      json.dumps(parts, ensure_ascii=False))
check("2部目は出さない", W.calls["create"] == 1, W.calls["create"])
check("要対応で止まる",
      W.attempts[f"{SALON}:{JST_DATE}:noon"]["status"] in ("attention", "hold_repair"),
      W.attempts[f"{SALON}:{JST_DATE}:noon"]["status"])
check("1部目は記録される", len(W.post_logs) == 1, len(W.post_logs))
print("  → 再実行しても自動では触らない")
before = (len(W.posts), len(W.post_logs), W.calls["create"])
a, _ = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
check("自動では触らない", a == "hold", a)
check("何も増えない", (len(W.posts), len(W.post_logs), W.calls["create"]) == before)

# ── 5. 返信本文と同じ本文の別投稿がある → 別IDを採用しない ────────────
print("\n⑤ 返信パートの取り違え")
reset()
W.posts.insert(0, {"id": "OTHER_SAME_TEXT", "text": TEXTS2[1],
                   "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")})
seq = {"n": 0}
def pub(cid, n):
    # 1部目は成功、2部目は応答喪失（サーバ側では公開済み）
    return "ok" if cid == "CONTAINER_1" else "published_then_timeout"
W.publish_behavior = pub
W.status_override = lambda cid: W.containers[cid]["status"]
(res, row, _), out = quiet(lambda: run(TEXTS2))
parts = W.attempts[f"{SALON}:{JST_DATE}:noon"]["parts"]
check("2部目に OTHER_SAME_TEXT を採用しない",
      all(p.get("post_id") != "OTHER_SAME_TEXT" for p in parts), json.dumps(parts, ensure_ascii=False))
check("コンテナは2個（作り直していない）", W.calls["create"] == 2, W.calls["create"])
check("1部目は記録される", len(W.post_logs) == 1, len(W.post_logs))

# ── 6. PUBLISHED だが投稿IDが取れない → コンテナIDを返信先にしない ──────
print("\n⑥ 1部目PUBLISHED・投稿ID回収不可")
reset()
W.publish_behavior = lambda cid, n: "published_then_timeout"
W.status_override = lambda cid: W.containers[cid]["status"]
W.list_behavior = "empty"
(res, row, _), out = quiet(lambda: run(TEXTS2))
parts = W.attempts[f"{SALON}:{JST_DATE}:noon"]["parts"]
check("CONTAINER_1 を返信先に使っていない", "CONTAINER_1" not in [p.get("post_id") for p in parts],
      json.dumps(parts, ensure_ascii=False))
check("2部目は出していない", W.calls["create"] == 1, W.calls["create"])
check("未完として残る", not res.get("complete"), res)
check("枠は止まる（続きは自動で出さない）",
      W.attempts[f"{SALON}:{JST_DATE}:noon"]["status"] in ("attention", "hold_repair"),
      W.attempts[f"{SALON}:{JST_DATE}:noon"]["status"])
check("1部目は記録される", len(W.post_logs) == 1, len(W.post_logs))

# ── 7. 記録が5回とも失敗 → 次の実行が再投稿しない ─────────────────
print("\n⑦ post_logs への記録が全滅")
reset()
W.log_insert_behavior = lambda n: "fail"
(res, row, _), out = quiet(lambda: run(TEXTS1))
check("投稿は1件", len(W.posts) == 1, len(W.posts))
check("記録は0件", len(W.post_logs) == 0)
check("枠は published（logged にしない）",
      W.attempts[f"{SALON}:{JST_DATE}:noon"]["status"] == "published",
      W.attempts[f"{SALON}:{JST_DATE}:noon"]["status"])
print("  → 再実行")
W.log_insert_behavior = lambda n: "ok"
(res2, _, _), out2 = quiet(lambda: run(TEXTS1))
check("再実行で投稿は増えない", len(W.posts) == 1, len(W.posts))
check("記録だけが復旧する", len(W.post_logs) == 1, len(W.post_logs))
check("完了になる", W.attempts[f"{SALON}:{JST_DATE}:noon"]["status"] == "logged")

# ── 8. INSERT応答だけ失われた → 二重記録しない ────────────────────
print("\n⑧ 記録の応答だけ失われる")
reset()
W.log_insert_behavior = lambda n: "fail_but_saved" if n == 1 else "ok"
(res, _, _), out = quiet(lambda: run(TEXTS1))
check("post_logs は1件のまま", len(W.post_logs) == 1, len(W.post_logs))
check("完了になる", W.attempts[f"{SALON}:{JST_DATE}:noon"]["status"] == "logged")

# ── 9. 同時実行（予備実行）→ 片方だけが投稿する ───────────────────
print("\n⑨ 同時に2つ走る")
reset()
post_state.STALE_SEC = 600
a1, r1 = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
a2, r2 = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
check("2つ目は hold", (a1, a2) == ("go", "hold"), f"{a1}/{a2}")
# 公開途中(unknown)の枠も、直前まで動いていれば触らない
reset()
post_state.RESUME_STALE_SEC = 120
W.publish_behavior = lambda cid, n: "timeout"
W.status_override = "__fail__"
W.list_behavior = "empty"
quiet(lambda: run(TEXTS1))
a3, _ = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
check("直後の予備実行は hold", a3 == "hold", a3)
post_state.RESUME_STALE_SEC = 0
a4, _ = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
check("時間が経てば resume で引き継ぐ", a4 == "resume", a4)
post_state.STALE_SEC = 0

# ── 10. 表が無い環境 → 黙って従来動作に落ちず、止まって気づかせる ──────
print("\n⑩ post_attempts がまだ無い")
reset()
orig_sb = W.sb
def no_table(method, table, query, body):
    if table.startswith("post_attempts"):
        import urllib.error, io as _io
        raise urllib.error.HTTPError("u", 404, "no table", {}, _io.BytesIO(b'{}'))
    return orig_sb(method, table, query, body)
W.sb = no_table
err = None
try:
    (res, _, _), out = quiet(lambda: run(TEXTS1))
except Exception as e:
    err = e
check("止まる（黙って従来動作に落ちない）", err is not None, "例外が出なかった")
check("投稿していない", len(W.posts) == 0, len(W.posts))
check("理由が分かるメッセージ", "post_attempts" in str(err), str(err)[:120])
W.sb = orig_sb

# ── 11. 2部構成を3回実行しても記録は1件（resume時の二重記録）──────────
print("\n⑪ 2部構成・公開応答喪失で3回実行")
reset()
W.publish_behavior = lambda cid, n: "published_then_timeout"
W.status_override = lambda cid: W.containers[cid]["status"]
for _ in range(3):
    quiet(lambda: run(TEXTS2))
check("post_logs は1件", len(W.post_logs) == 1, len(W.post_logs))
check("投稿は増えない", len(W.posts) <= 2, len(W.posts))

# ── 12. Threadsの時刻形式(+0000)を読める ────────────────────────
print("\n⑫ timestamp が +0000 形式")
check("パースできる", post_saas._parse_ts("2026-09-12T03:10:41+0000") is not None)
check("Z形式も読める", post_saas._parse_ts("2026-09-12T03:10:41Z") is not None)
check("壊れた値は None", post_saas._parse_ts("なんだこれ") is None)


# ── 13. 再開の実行権も原子的か（2つが同時に resume しない）──────────
print("\n⑬ 未確定の枠に2つが同時に来る")
reset()
post_state.RESUME_STALE_SEC = 0   # 1回目は「時間が経った」ことにして未確定の枠を作る
W.publish_behavior = lambda cid, n: "timeout"
W.status_override = "__fail__"
W.list_behavior = "empty"
quiet(lambda: run(TEXTS1))
W.publish_behavior = lambda cid, n: "ok"; W.status_override = None; W.list_behavior = "normal"
op = f"{SALON}:{JST_DATE}:noon"
from datetime import datetime as _d2, timezone as _z2, timedelta as _t2
W.attempts[op]["updated_at"] = (_d2.now(_z2.utc) - _t2(minutes=10)).isoformat()
post_state.RESUME_STALE_SEC = 120   # ここからは本番と同じ値
# 2つの実行が「同じ古い行」を同時に読んでから、両方が所有権を取りにいく
snap_a = post_state.fetch(op)
snap_b = post_state.fetch(op)
a1, _ = post_state._take("resume", snap_a)
a2, _ = post_state._take("resume", snap_b)
check("片方だけが resume を取る", sorted([a1, a2]) == ["hold", "resume"], f"{a1}/{a2}")
# 直後に来た3つ目は、古さの確認で止まる（runningにパートがあっても迂回しない）
a3, _ = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
check("直後の3つ目は hold", a3 == "hold", a3)
post_state.RESUME_STALE_SEC = 0

# ── 14. 記録の重複はDBの一意制約で止まる ─────────────────────
print("\n⑭ 記録が保存済みなのに応答だけ失われる（照合GETも失敗）")
reset()
W.log_insert_behavior = lambda n: "fail_but_saved" if n == 1 else "ok"
quiet(lambda: run(TEXTS1))
check("post_logs は1件", len(W.post_logs) == 1, len(W.post_logs))
print("  → 7時間後にもう一度同じ枠を実行")
W.post_logs[0]["posted_at"] = "2020-01-01T00:00:00+00:00"   # 検索窓の外へ
before = len(W.post_logs)
post_state._MEM.clear()
W.attempts[f"{SALON}:{JST_DATE}:noon"]["status"] = "published"
W.attempts[f"{SALON}:{JST_DATE}:noon"]["logged"] = False
quiet(lambda: run(TEXTS1))
check("窓の外でも二重にならない", len(W.post_logs) == before, len(W.post_logs))
check("完了になる", W.attempts[f"{SALON}:{JST_DATE}:noon"]["status"] == "logged",
      W.attempts[f"{SALON}:{JST_DATE}:noon"]["status"])

# ── 15. 台帳のPATCHが一度失敗しても、受け取った投稿IDを捨てない ────────
print("\n⑮ 公開成功・台帳保存だけ一度失敗")
reset()
state = {"n": 0}
def patch_once_fail(op, body):
    if body.get("parts") and state["n"] == 0:
        state["n"] += 1
        return "fail"
    return "ok"
W.attempts_patch_behavior = patch_once_fail
(res, row, _), out = quiet(lambda: run(TEXTS2))
parts = W.attempts[f"{SALON}:{JST_DATE}:noon"]["parts"]
check("投稿IDが台帳に残る", all(p.get("post_id") for p in parts), json.dumps(parts, ensure_ascii=False))
check("2部とも出る", len(W.posts) == 2, len(W.posts))
W.attempts_patch_behavior = None

# ── 16. 過去の未完の枠を回収する ─────────────────────────
print("\n⑯ 前日の未完の枠")
reset()
W.salons = [{"id": SALON, "salon_name": "テストサロン", "access_token": "TOK",
             "threads_user_id": "USER1", "instagram_url": ""}]
op = f"{SALON}:{YESTERDAY}:evening"
W.publish_behavior = lambda cid, n: "timeout"; W.status_override = "__fail__"
a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "evening")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
with contextlib.redirect_stdout(io.StringIO()):
    r = post_saas.threads_post(row, "USER1", "TOK", TEXTS1, original_first=TEXTS1[0])
    post_saas._state_finish(r["row"], r["slot_status"], note=r["note"])
check("未確定として残る", W.attempts[op]["status"] == "unknown", W.attempts[op]["status"])
W.publish_behavior = lambda cid, n: "ok"; W.status_override = None
with contextlib.redirect_stdout(io.StringIO()):
    n = post_saas.recover_open_attempts(W.salons)
check("回収された", n == 1, n)
check("投稿が出た", len(W.posts) == 1, len(W.posts))
check("記録された", len(W.post_logs) == 1, len(W.post_logs))
check("完了になる", W.attempts[op]["status"] == "logged", W.attempts[op]["status"])
check("新しいコンテナを作っていない", W.calls["create"] == 1, W.calls["create"])

# ── 17. DRY_RUN は本番に何も書かない ────────────────────────
print("\n⑰ DRY_RUN の境界")
reset()
check("salons へのPATCHが0件", W.calls.get("salons_patch", 0) == 0)
check("DRY_RUNでは生成workflowを起動しない",
      post_saas.DRY_RUN is False or post_saas._trigger_generate("x") is False)


# ── 19. failed 行に2つが割り込んでも、公開は1件 ─────────────────
print("\n⑲ failed の枠に2つが同時に来る")
reset()
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
post_state.update(row, status=post_state.STATUS_FAILED)
from datetime import datetime as _d3, timezone as _z3, timedelta as _t3
W.attempts[op]["updated_at"] = (_d3.now(_z3.utc) - _t3(minutes=30)).isoformat()
snap_a = post_state.fetch(op)
snap_b = post_state.fetch(op)
r1, _ = post_state._take("go", snap_a)
r2, _ = post_state._take("go", snap_b)
check("片方だけが実行権を取る", sorted([r1, r2]) == ["go", "hold"], f"{r1}/{r2}")

# ── 20. 409 が重複以外（外部キー違反）なら成功扱いしない ───────────────
print("\n⑳ 409 が外部キー違反")
reset()
W.log_fk_violation = True
ok = None
try:
    post_saas.log_post_with_retry(SALON, "noon", "本文", "op-fk", attempts=2)
    ok = True
except Exception:
    ok = False
check("失敗として扱う", ok is False, ok)
check("記録は0件", len(W.post_logs) == 0, len(W.post_logs))
W.log_fk_violation = False

# ── 21. 途中パートでトークン切れ → 1部目の記録は残る ──────────────
print("\n㉑ 2部目でトークン切れ")
reset()
def pub_ok(cid, n): return "ok"
W.publish_behavior = pub_ok
orig_create = W.th_create
def create_fail_second(params):
    if W.calls["create"] >= 1:
        raise urllib.error.HTTPError("u", 401, "token", {},
                                     io.BytesIO(b'{"error":{"message":"expired token"}}'))
    return orig_create(params)
W.th_create = create_fail_second
raised = None
try:
    (res, row, _), out = quiet(lambda: run(TEXTS2))
except Exception as e:
    raised = e
check("1部目は公開された", len(W.posts) == 1, len(W.posts))
check("1部目が記録される（記録を飛ばさない）", len(W.post_logs) == 1, len(W.post_logs))
W.th_create = orig_create

# ── 22. 本物の main() を通す：前日の回収が当日の投稿を消さない ──────────
print("\n㉒ main() で「前日の回収 → 当日の投稿」")
reset()
post_saas.SLOT_JST_WINDOWS = {}      # 時間帯ガードはテストでは無効化
post_saas.SLOT = "noon"
post_saas.SALON_FILTER = ""
post_saas.DRY_RUN = False
W.salons = [{"id": SALON, "salon_name": "テストサロン", "access_token": "TOK",
             "threads_user_id": "USER1", "instagram_url": "", "is_active": True}]
post_saas.pick_post = lambda name, slot, used, **kw: ["本日の投稿本文"]
post_saas.is_promo_time = lambda name, slot: False
# 前日の noon を「公開済み・記録未完」にしておく
yday = YESTERDAY
a, row = post_saas._acquire_with_retry(SALON, yday, "noon")
row = post_state.update(row, payload={"texts": ["前日の本文"], "original_first": "前日の本文",
                                      "topic_tag": None, "image_url": "", "promo": False})
W.publish_behavior = lambda cid, n: "ok"
with contextlib.redirect_stdout(io.StringIO()):
    r = post_saas.threads_post(row, "USER1", "TOK", ["前日の本文"],
                               original_first="前日の本文")
post_state.update(r["row"], status=post_state.STATUS_PUBLISHED, note="記録未完")
W.attempts[f"{SALON}:{yday}:noon"]["updated_at"] = (_d3.now(_z3.utc) - _t3(hours=12)).isoformat()
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit:
    pass
texts_out = [p["text"] for p in W.posts]
check("前日分が記録された", any(l.get("post_content") == "前日の本文" for l in W.post_logs),
      json.dumps(W.post_logs, ensure_ascii=False)[:200])
check("当日分も投稿された", "本日の投稿本文" in texts_out, texts_out)
check("当日分も記録された", any(l.get("post_content") == "本日の投稿本文" for l in W.post_logs),
      json.dumps(W.post_logs, ensure_ascii=False)[:200])
check("投稿は2件だけ", len(W.posts) == 2, len(W.posts))

print("  → もう一度 main() を回しても増えない")
before = (len(W.posts), len(W.post_logs))
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit:
    pass
check("投稿も記録も増えない", (len(W.posts), len(W.post_logs)) == before,
      f"{(len(W.posts), len(W.post_logs))} != {before}")


# ── 23. 回収の持ち時間を、処理の途中でも守る ────────────────────
print("\n㉓ 回収が長引く")
reset()
W.salons = [{"id": SALON, "salon_name": "テストサロン", "access_token": "TOK",
             "threads_user_id": "USER1", "instagram_url": ""}]
post_saas.RECOVER_BUDGET_SEC = 60
for d in ("2026-09-05", "2026-09-06", "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10"):
    a, row = post_saas._acquire_with_retry(SALON, d, "noon")
    row = post_state.update(row, payload={"texts": ["古い本文"], "original_first": "古い本文",
                                          "topic_tag": None, "image_url": "", "promo": False})
    post_state.set_part(row, 0, hash=post_state.part_hash("古い本文"),
                    original_hash=post_state.part_hash("古い本文"),
                        creation_id=f"OLD_C_{d}", status=post_state.PART_UNKNOWN)
    post_state.update(post_state.fetch(row["op_id"]), status=post_state.STATUS_UNKNOWN)
    W.containers[f"OLD_C_{d}"] = {"status": "FINISHED", "text": "古い本文", "reply_to": None}
    W.attempts[row["op_id"]]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=5)).isoformat()
W.publish_behavior = lambda cid, n: "timeout"
W.status_override = "FINISHED"
with contextlib.redirect_stdout(io.StringIO()):
    n = post_saas.recover_open_attempts(W.salons)
check("待機の合計が持ち時間を超えない", CLOCK.slept <= 60, f"待機{CLOCK.slept}秒")
check("残りは次の実行へ回す", n < 6, n)
check("通常投稿用の待機予算を食い潰さない", post_saas._retry_spent == 0.0, post_saas._retry_spent)
check("締切は元に戻る", post_saas._deadline is None, post_saas._deadline)
post_saas.RECOVER_BUDGET_SEC = 120

# ── 24. 公開に成功した後の台帳保存だけが落ちる ────────────────────
print("\n㉔ 公開成功後のPATCHだけ失敗")
reset()
W.publish_behavior = lambda cid, n: "ok"
def fail_after_publish(op, body):
    parts = (body or {}).get("parts")
    if parts and any(p.get("status") == "published" for p in parts):
        return "fail"
    return "ok"
W.attempts_patch_behavior = fail_after_publish
(res, row, _), out = quiet(lambda: run(TEXTS1))
check("投稿は1件", len(W.posts) == 1, len(W.posts))
check("投稿IDを載せて通知する", any("post_id=POST_" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:200])
W.attempts_patch_behavior = None

# ── 25. 停止状態(attention)でも、記録だけは復旧する ──────────────
print("\n㉕ attention＋記録なし")
reset()
W.salons = [{"id": SALON, "salon_name": "テストサロン", "access_token": "TOK",
             "threads_user_id": "USER1", "instagram_url": ""}]
W.publish_behavior = lambda cid, n: "published_then_timeout"
W.status_override = lambda cid: W.containers[cid]["status"]
W.log_insert_behavior = lambda n: "fail"
(res, row, _), out = quiet(lambda: run(TEXTS2))
op = f"{SALON}:{JST_DATE}:noon"
check("止まる（自動では続けない）",
      W.attempts[op]["status"] in ("attention", "hold_repair"), W.attempts[op]["status"])
check("記録はまだ無い", len(W.post_logs) == 0, len(W.post_logs))
W.log_insert_behavior = lambda n: "ok"
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(minutes=10)).isoformat()
before_posts = len(W.posts)
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("記録だけ復旧する", len(W.post_logs) == 1, len(W.post_logs))
check("投稿は増えない", len(W.posts) == before_posts, len(W.posts))
check("停止状態のまま", W.attempts[op]["status"] == "attention", W.attempts[op]["status"])

# ── 26. 記録未完の古い枠は、何日経っても回収対象に残る ────────────────
print("\n㉖ 5日前の記録未完")
reset()
a, row = post_saas._acquire_with_retry(SALON, "2026-09-07", "noon")
post_state.update(row, status=post_state.STATUS_PUBLISHED, logged=False)
rows = post_state.open_issues(since_days=3, salon_ids=[SALON])
check("古くても対象に残る", any(r["op_id"].endswith("2026-09-07:noon") for r in rows),
      [r["op_id"] for r in rows])

# ── 27. 停止済みサロンの古い行で埋まっても、稼働中サロンに届く ────────────
print("\n㉗ 回収一覧の飢餓")
reset()
DEAD = "22222222-2222-2222-2222-222222222222"
for i in range(60):
    op = f"{DEAD}:2026-09-0{i%9+1}:noon"
    W.attempts[op] = {"op_id": op, "salon_id": DEAD, "jst_date": f"2026-09-0{i%9+1}",
                      "slot": "noon", "status": "unknown", "parts": [], "rev": 0,
                      "payload": None, "note": None, "logged": False,
                      "updated_at": (_dt.now(_tz.utc) - _td(days=9)).isoformat()}
live_op = f"{SALON}:{YESTERDAY}:noon"
W.attempts[live_op] = {"op_id": live_op, "salon_id": SALON, "jst_date": YESTERDAY,
                       "slot": "noon", "status": "published", "parts": [], "rev": 0,
                       "payload": None, "note": None, "logged": False,
                       "updated_at": (_dt.now(_tz.utc) - _td(hours=1)).isoformat()}
rows = post_state.open_issues(since_days=3, salon_ids=[SALON])
check("稼働中サロンの行が取れる", any(r["op_id"] == live_op for r in rows), len(rows))
check("停止済みサロンの行は入らない", all(r["salon_id"] == SALON for r in rows))

# ── 28. DRY_RUN の main() は本番に何も書かない ─────────────────
print("\n㉘ DRY_RUN で main()")
reset()
W.salons = [{"id": SALON, "salon_name": "テストサロン", "access_token": "TOK",
             "threads_user_id": "", "instagram_url": "", "is_active": True}]
post_saas.SLOT_JST_WINDOWS = {}
post_saas.SLOT = "noon"
post_saas.SALON_FILTER = ""
post_saas.DRY_RUN = True
post_saas.pick_post = lambda name, slot, used, **kw: ["DRY本文"]
post_saas.is_promo_time = lambda name, slot: False
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit:
    pass
check("投稿しない", len(W.posts) == 0, len(W.posts))
check("記録しない", len(W.post_logs) == 0, len(W.post_logs))
check("台帳に書かない", len(W.attempts) == 0, list(W.attempts))
check("salons を書き換えない", W.calls.get("salons_patch", 0) == 0, W.calls.get("salons_patch"))
post_saas.DRY_RUN = False


# ── 29. 総当たり：どの障害の組み合わせでも二重投稿・二重記録・誤った返信先を出さない ──
# 「公開応答」「コンテナ状態」「記録」の失敗パターンを掛け合わせ、
#   ・回復モード…2回目以降は障害が直る
#   ・継続モード…障害が最後まで続く（Sol指摘#7：直してしまうと実行をまたぐ穴が隠れる）
# の両方で4回実行し、投稿数・記録数・パートごとの公開回数・返信先の正しさを数える。
print("\n㉙ 障害の組み合わせ総当たり")
PUB = {"ok": lambda c, n: "ok",
       "応答喪失": lambda c, n: "timeout",
       "公開済みだが応答喪失": lambda c, n: "published_then_timeout",
       "500": lambda c, n: "http500",
       "400（明確な拒否）": lambda c, n: "http400"}
STA = {"実状態": lambda c: W.containers[c]["status"], "取得失敗": "__fail__",
       "FINISHED": "FINISHED", "ERROR": "ERROR", "EXPIRED": "EXPIRED", "不明": None}
LOG = {"ok": lambda n: "ok", "全滅": lambda n: "fail",
       "応答喪失": lambda n: "fail_but_saved" if n == 1 else "ok"}
dup = 0; none_posted = 0; bad_reply = 0; held = 0; total = 0
for mode in ("回復", "継続"):
    for nparts in (1, 2, 3):
        for pk, pv in PUB.items():
            for sk, sv in STA.items():
                for lk, lv in LOG.items():
                    total += 1
                    reset()
                    texts = [f"総当たり本文{i}" for i in range(nparts)]
                    W.publish_behavior, W.status_override, W.log_insert_behavior = pv, sv, lv
                    W.salons = [{"id": SALON, "salon_name": "テストサロン",
                                 "access_token": "TOK", "threads_user_id": "USER1",
                                 "instagram_url": ""}]
                    with contextlib.redirect_stdout(io.StringIO()):
                        for _ in range(4):
                            try:
                                run(texts)
                            except Exception:
                                pass
                            if mode == "回復":
                                W.publish_behavior = PUB["ok"]
                                W.status_override = None
                                W.log_insert_behavior = LOG["ok"]
                                # 本番と同じく、未完の枠は回収処理が片づけにいく
                                for r in W.attempts.values():
                                    r["updated_at"] = (_dt.now(_tz.utc) - _td(minutes=30)).isoformat()
                                try:
                                    post_saas.recover_open_attempts(W.salons)
                                except Exception:
                                    pass
                    tag = f"{mode} / {nparts}部 / 公開={pk} / 状態={sk} / 記録={lk}"
                    # ① 同じ本文が2回以上出ていないか（パート単位で数える）
                    for t in texts:
                        if sum(1 for p in W.posts if p["text"] == t) > 1:
                            dup += 1
                            print(f"    🚨 {tag} → 「{t}」が{sum(1 for p in W.posts if p['text'] == t)}回出た")
                            break
                    else:
                        if len(W.post_logs) > 1:
                            dup += 1
                            print(f"    🚨 {tag} → 記録{len(W.post_logs)}件")
                    # ② 返信先が、今回出した投稿のIDになっているか
                    ids = {p["id"] for p in W.posts}
                    for p in W.posts:
                        if p.get("reply_to") and p["reply_to"] not in ids:
                            bad_reply += 1
                            print(f"    🚨 {tag} → 返信先 {p['reply_to']} が実在しない")
                            break
                    # ③ 回復モードなら「全部出し切る」か「人待ち(attention)として通知して止まる」か。
                    #    黙って中途半端に終わるのだけは許さない。
                    if mode == "回復":
                        st = list(W.attempts.values())[0]["status"] if W.attempts else "?"
                        missing = [t for t in texts if not any(p["text"] == t for p in W.posts)]
                        complete = (not missing) and len(W.post_logs) == 1
                        if complete:
                            pass
                        elif st in ("attention", "hold_repair") and len(W.post_logs) == 1 and any(
                                "止まりました" in m or "🚨" in m for m in NOTIFY):
                            held += 1     # 投稿IDを推測しない設計上、ここで止まるのは想定内
                        elif st in ("attention", "hold_repair") and len(W.post_logs) == 1:
                            none_posted += 1
                            print(f"    ⚠️ {tag} → attention なのに通知が無い")
                        else:
                            none_posted += 1
                            print(f"    ⚠️ {tag} → 未投稿{len(missing)}部・記録{len(W.post_logs)}件・状態{st}")
                    # ④ 返信先が「1つ前のパート」になっているか
                    by_text = {p["text"]: p for p in W.posts}
                    for n in range(1, len(texts)):
                        child = by_text.get(texts[n])
                        parent = by_text.get(texts[n - 1])
                        if child and parent and child.get("reply_to") != parent["id"]:
                            bad_reply += 1
                            print(f"    🚨 {tag} → {n+1}部目の返信先が1つ前ではない")
                            break
check(f"{total}通りで二重投稿・二重記録が0件", dup == 0, f"{dup}件")
check(f"{total}通りで誤った返信先が0件", bad_reply == 0, f"{bad_reply}件")
check("黙って中途半端に終わる組み合わせが0件", none_posted == 0, f"{none_posted}件")
print(f"    （うち{held}通りは「1部目は公開・記録済み、続きは人待ちで通知」で停止＝設計どおり）")


# ── 30. 記録済みattentionが50件あっても、後ろの行に届く ────────────────
print("\n㉚ 人待ちの行で回収枠が埋まる")
reset()
for i in range(200):
    op = f"{SALON}:2026-08-{i%28+1:02d}:noon{i}"
    W.attempts[op] = {"op_id": op, "salon_id": SALON, "jst_date": f"2026-08-{i%28+1:02d}",
                      "slot": "noon", "status": "attention", "parts": [], "rev": 0,
                      "payload": None, "note": "人待ち", "logged": True,
                      "updated_at": (_dt.now(_tz.utc) - _td(days=20)).isoformat()}
live = f"{SALON}:{YESTERDAY}:evening"
W.attempts[live] = {"op_id": live, "salon_id": SALON, "jst_date": YESTERDAY,
                    "slot": "evening", "status": "published", "parts": [], "rev": 0,
                    "payload": None, "note": None, "logged": False,
                    "updated_at": (_dt.now(_tz.utc) - _td(hours=2)).isoformat()}
rows = post_state.open_issues(limit=50, salon_ids=[SALON])
check("200件そろっている（テスト自体の確認）",
      sum(1 for k in W.attempts if ":noon" in k and k[-1].isdigit()) == 200,
      sum(1 for k in W.attempts if ":noon" in k and k[-1].isdigit()))
check("記録済みの人待ちは対象外", all(r.get("status") != "attention" for r in rows), len(rows))
check("後ろの行に届く", any(r["op_id"] == live for r in rows), [r["op_id"] for r in rows][:3])

# ── 31. 公開途中で死んだ古い running も回収対象に残る ──────────────────
print("\n㉛ 5日前の running（コンテナ保存済み）")
reset()
op = f"{SALON}:2026-09-07:noon"
W.attempts[op] = {"op_id": op, "salon_id": SALON, "jst_date": "2026-09-07", "slot": "noon",
                  "status": "running", "parts": [{"i": 0, "status": "container",
                                                  "creation_id": "C_OLD"}], "rev": 0,
                  "payload": None, "note": None, "logged": False,
                  "updated_at": (_dt.now(_tz.utc) - _td(days=5)).isoformat()}
rows = post_state.open_issues(salon_ids=[SALON])
check("日付で切り捨てない", any(r["op_id"] == op for r in rows), [r["op_id"] for r in rows])

# ── 32. 記録の再試行も回収の持ち時間を守る ──────────────────────────
print("\n㉜ 記録APIが遅くて失敗し続ける")
reset()
post_saas._deadline = CLOCK.time() + 60
W.log_insert_behavior = lambda n: "fail"
ok = None
with contextlib.redirect_stdout(io.StringIO()):
    try:
        post_saas.log_post_with_retry(SALON, "noon", "本文", "op-slow")
    except Exception:
        ok = False
check("持ち時間を超えて待ち続けない", CLOCK.slept <= 60, f"待機{CLOCK.slept}秒")
post_saas._deadline = None


# ── 33. 公開直後に台帳保存が全滅しても、次回に作り直さない ─────────────
print("\n㉝ 公開成功 → 台帳保存が3回とも失敗 → 再実行")
reset()
W.publish_behavior = lambda cid, n: "ok"
def fail_published_patch(op, body):
    parts = (body or {}).get("parts")
    if parts and any(p.get("status") == "published" for p in parts):
        return "fail"
    return "ok"
W.attempts_patch_behavior = fail_published_patch
(res, row, _), out = quiet(lambda: run(TEXTS1))
check("投稿は1件", len(W.posts) == 1, len(W.posts))
check("公開したことは台帳に残る（応答喪失の印）",
      any(p.get("lost_response") for p in W.attempts[f"{SALON}:{JST_DATE}:noon"]["parts"]),
      json.dumps(W.attempts[f"{SALON}:{JST_DATE}:noon"]["parts"], ensure_ascii=False))
print("  → 旧コンテナは400＋EXPIRED、新コンテナなら成功する状況で再実行")
W.attempts_patch_behavior = None
W.publish_behavior = lambda cid, n: "http400" if cid == "CONTAINER_1" else "ok"
W.status_override = lambda cid: "EXPIRED" if cid == "CONTAINER_1" else W.containers[cid]["status"]
W.attempts[f"{SALON}:{JST_DATE}:noon"]["updated_at"] = (_dt.now(_tz.utc) - _td(minutes=30)).isoformat()
before = len(W.posts)
quiet(lambda: run(TEXTS1))
check("同じ本文を2回出さない", len(W.posts) == before, len(W.posts))
check("コンテナを作り直さない", W.calls["create"] == 1, W.calls["create"])
W.status_override = None

# ── 34. 登録アカウントとトークンの実アカウントが違えば投稿しない ────────────
print("\n㉞ アカウント不一致")
reset()
post_saas.SLOT_JST_WINDOWS = {}
post_saas.SLOT = "noon"
post_saas.SALON_FILTER = ""
post_saas.DRY_RUN = False
post_saas.pick_post = lambda name, slot, used, **kw: ["不一致テスト本文"]
post_saas.is_promo_time = lambda name, slot: False
W.salons = [{"id": SALON, "salon_name": "テストサロン", "access_token": "TOK",
             "threads_user_id": "BETSU_NO_ID", "instagram_url": "", "is_active": True}]
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit:
    pass
check("投稿しない", len(W.posts) == 0, len(W.posts))
check("理由を通知する", any("一致しません" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:200])

# ── 35. ジョブ全体の持ち時間を超えたら、残りは次の実行へ回す ────────────────
print("\n㉟ ジョブの持ち時間切れ")
reset()
post_saas.JOB_BUDGET_SEC = 30
post_saas.SLOT_JST_WINDOWS = {}
post_saas.SLOT = "noon"
post_saas.DRY_RUN = False
post_saas.pick_post = lambda name, slot, used, **kw: ["時間切れテスト"]
post_saas.is_promo_time = lambda name, slot: False
W.salons = [{"id": SALON, "salon_name": f"サロン{i}", "access_token": "TOK",
             "threads_user_id": "USER1", "instagram_url": "", "is_active": True}
            for i in range(1, 4)]
for i, sl in enumerate(W.salons):
    sl["id"] = f"{i+1}1111111-1111-1111-1111-111111111111"
W.latency = 20      # 通信1回で20秒進む
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit:
    pass
check("通信時間も時計を進める（テスト自体の確認）", CLOCK.slept >= 20, f"{CLOCK.slept}秒")
check("時間切れを通知する", any("時間切れ" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:200])
# ⚠️ 件数ではなく「送った時刻」で見る。件数だけだと締切確認を外しても合格してしまう
late = [t for t in W.publish_times if t > post_saas._job_deadline]
check("締切を過ぎてから公開要求を送っていない", not late,
      f"{len(late)}回が締切後（{[round(t - post_saas._job_deadline, 1) for t in late][:3]}秒超過）")
W.latency = 0
post_saas.JOB_BUDGET_SEC = 480


# ── 36. 回収でもアカウント不一致を止める ────────────────────────
print("\n㊱ 回収時のアカウント不一致")
reset()
W.salons = [{"id": SALON, "salon_name": "テストサロン", "access_token": "TOK",
             "threads_user_id": "OTHER_ACCOUNT", "instagram_url": ""}]
op = f"{SALON}:{YESTERDAY}:noon"
a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "noon")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(row, 0, hash=post_state.part_hash(TEXTS1[0]),
                    original_hash=post_state.part_hash(TEXTS1[0]), creation_id="C_X",
                    status=post_state.PART_UNKNOWN, lost_response=True)
post_state.update(post_state.fetch(op), status=post_state.STATUS_UNKNOWN)
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
W.containers["C_X"] = {"status": "FINISHED", "text": TEXTS1[0], "reply_to": None}
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("投稿しない", len(W.posts) == 0, len(W.posts))
check("要対応で止める", W.attempts[op]["status"] in ("attention", "hold_repair"),
      W.attempts[op]["status"])
check("理由を通知する", any("一致しません" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:200])

# ── 37. 公開未確定のまま記録も失敗 → 回収対象から外さない ─────────────
print("\n㊲ 公開未確定＋記録失敗の複合")
reset()
W.salons = [{"id": SALON, "salon_name": "テストサロン", "access_token": "TOK",
             "threads_user_id": "USER1", "instagram_url": ""}]
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(row, 0, hash=post_state.part_hash(TEXTS1[0]),
                    original_hash=post_state.part_hash(TEXTS1[0]), creation_id="C_Y",
                    status=post_state.PART_UNKNOWN, lost_response=True)
post_state.update(post_state.fetch(op), status=post_state.STATUS_HOLD_REPAIR,
                  note="人の確認待ち")
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("logged を立てて消さない", not W.attempts[op].get("logged"), W.attempts[op].get("logged"))
rows = post_state.open_issues(salon_ids=[SALON])
check("次回も回収対象に残る", any(r["op_id"] == op for r in rows),
      f'status={W.attempts[op]["status"]} rows={[r["op_id"][-12:] for r in rows]}')
check("投稿はしない", len(W.posts) == 0, len(W.posts))

# ── 38. 予備経路は設定が無ければローカルでも止まる ────────────────
print("\n㊳ 予備経路の締め出し")
import post_api
saved = dict(os.environ)
for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "GITHUB_ACTIONS"):
    os.environ.pop(k, None)
check("設定が無ければ止める", post_api._saas_owns("USER1") is not None, post_api._saas_owns("USER1"))
os.environ["SUPABASE_URL"] = "https://fake.supabase.co"
os.environ["SUPABASE_SERVICE_KEY"] = "fake"
check("実IDが無ければ止める", post_api._saas_owns("") is not None, post_api._saas_owns(""))
reset()
W.salons = [{"id": SALON, "salon_name": "テストサロン", "is_active": False}]
check("停止中の登録でも止める", post_api._saas_owns("USER1") is not None, post_api._saas_owns("USER1"))
os.environ.clear(); os.environ.update(saved)


# ── 39. 送信直前に締切を跨いだら、送らない・印も残さない ────────────────
print("\n㊴ 送信前の台帳保存中に締切を跨ぐ")
reset()
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.set_part(row, 0, hash=post_state.part_hash(TEXTS1[0]),
                    original_hash=post_state.part_hash(TEXTS1[0]),
                          creation_id="C_Z", status=post_state.PART_CONTAINER)
W.containers["C_Z"] = {"status": "FINISHED", "text": TEXTS1[0], "reply_to": None}
# 送信前の台帳保存に5秒かかり、その間に締切を跨ぐ状況を作る
orig_set = post_saas._ledger_set_part
def slow_set(r, i, **f):
    out = orig_set(r, i, **f)
    CLOCK.sleep(5)
    return out
post_saas._ledger_set_part = slow_set
post_saas._deadline = CLOCK.time() + 3
with contextlib.redirect_stdout(io.StringIO()):
    row2, pid, outcome = post_saas._finalize_part(row, 0, "USER1", "TOK", "C_Z", "part 1/1")
post_saas._ledger_set_part = orig_set
check("公開要求を送らない", W.calls["publish"] == 0, W.calls["publish"])
part = post_state.get_part(post_state.fetch(row["op_id"]), 0) or {}
check("送っていないのに『結果不明』を残さない", not part.get("lost_response"),
      json.dumps(part, ensure_ascii=False))
check("コンテナは作り直さない", W.calls["create"] == 0, W.calls["create"])
check("未確定として持ち越す", outcome == "unknown", outcome)
post_saas._deadline = None

# ── 40. 未確定の attention は、コンテナが公開済みなら記録だけ戻す ────────────
print("\n㊵ 未確定 attention の解決")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(row, 0, hash=post_state.part_hash(TEXTS1[0]),
                    original_hash=post_state.part_hash(TEXTS1[0]), creation_id="C_W",
                    status=post_state.PART_UNKNOWN, lost_response=True)
post_state.update(post_state.fetch(op), status=post_state.STATUS_HOLD_REPAIR, note="人の確認待ち")
W.containers["C_W"] = {"status": "PUBLISHED", "text": TEXTS1[0], "reply_to": None}
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("記録が戻る", len(W.post_logs) == 1, len(W.post_logs))
check("投稿はしない", len(W.posts) == 0, len(W.posts))
check("停止状態は維持", W.attempts[op]["status"] in ("attention", "hold_repair"),
      W.attempts[op]["status"])
check("回収対象から外れる", not any(r["op_id"] == op for r in post_state.open_issues(salon_ids=[SALON])),
      [r["op_id"] for r in post_state.open_issues(salon_ids=[SALON])])

# ── 41. 公開済みだが本文が復元できない → 記録不要にしない ──────────────
print("\n㊶ 公開済み・本文欠損")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
post_state.set_part(row, 0, hash="x", creation_id="C_V", post_id="POST_X",
                    status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_HOLD_REPAIR, note="人の確認待ち")
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("記録不要にしない", not W.attempts[op].get("logged"), W.attempts[op].get("logged"))
check("理由が事実に合っている", "本文" in (W.attempts[op].get("note") or ""),
      W.attempts[op].get("note"))

# ── 42. /me を確認できなければ、通常経路も投稿しない ──────────────────
print("\n㊷ /me 失敗")
reset()
post_saas.SLOT_JST_WINDOWS = {}
post_saas.SLOT = "noon"
post_saas.SALON_FILTER = ""
post_saas.DRY_RUN = False
post_saas.pick_post = lambda name, slot, used, **kw: ["me失敗テスト"]
post_saas.is_promo_time = lambda name, slot: False
W.salons = [dict(SALON_ROW)]
W.me_behavior = lambda n: "timeout"
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit:
    pass
check("投稿しない", len(W.posts) == 0, len(W.posts))
check("理由を通知する", any("実アカウント" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:200])
W.me_behavior = None


# ── 43. 持ち越した未確定は、締切中断でも消さない ─────────────────────
print("\n㊸ 持ち越しあり＋締切中断")
reset()
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.set_part(row, 0, hash=post_state.part_hash(TEXTS1[0]),
                    original_hash=post_state.part_hash(TEXTS1[0]), creation_id="C_P",
                          status=post_state.PART_UNKNOWN, lost_response=True)  # 前回からの持ち越し
W.containers["C_P"] = {"status": "FINISHED", "text": TEXTS1[0], "reply_to": None}
# 「ループ先頭では時間内、送信直前で締切超過」を再現する
orig_oot = post_saas._out_of_time
seen = {"n": 0}
def oot_once(label=""):
    seen["n"] += 1
    return seen["n"] > 1
post_saas._out_of_time = oot_once
with contextlib.redirect_stdout(io.StringIO()):
    post_saas._finalize_part(row, 0, "USER1", "TOK", "C_P", "part 1/1", lost_before=True)
post_saas._out_of_time = orig_oot
check("公開要求は送らない", W.calls["publish"] == 0, W.calls["publish"])
part = post_state.get_part(post_state.fetch(op), 0) or {}
check("持ち越しの『結果不明』は消えない", part.get("lost_response") is True,
      json.dumps(part, ensure_ascii=False))
print("  → 旧コンテナ400＋EXPIREDで再実行しても作り直さない")
W.publish_behavior = lambda cid, n: "http400" if cid == "C_P" else "ok"
W.status_override = lambda cid: "EXPIRED" if cid == "C_P" else W.containers[cid]["status"]
with contextlib.redirect_stdout(io.StringIO()):
    # 本物の投稿処理を通す（ここを通さないと「作り直し」の判定を検査できない）
    post_saas.threads_post(post_state.fetch(op), "USER1", "TOK", TEXTS1,
                           original_first=TEXTS1[0])
check("コンテナを作り直さない", W.calls["create"] == 0, W.calls["create"])
check("投稿もしない", len(W.posts) == 0, len(W.posts))
W.status_override = None

# ── 44. FINISHED を「公開済み」として記録しない ─────────────────────
print("\n㊹ FINISHED は公開済みではない")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(row, 0, hash=post_state.part_hash(TEXTS1[0]),
                    original_hash=post_state.part_hash(TEXTS1[0]), creation_id="C_F",
                    status=post_state.PART_UNKNOWN, lost_response=True)
post_state.update(post_state.fetch(op), status=post_state.STATUS_HOLD_REPAIR, note="人の確認待ち")
W.containers["C_F"] = {"status": "FINISHED", "text": TEXTS1[0], "reply_to": None}
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("記録しない", len(W.post_logs) == 0, len(W.post_logs))
check("投稿もしない", len(W.posts) == 0, len(W.posts))
check("回収対象に残す", any(r["op_id"] == op for r in post_state.open_issues(salon_ids=[SALON])),
      [r["op_id"] for r in post_state.open_issues(salon_ids=[SALON])])

# ── 45. 再連携で登録もトークンも入れ替わっても、旧枠の続きは出さない ────────
print("\n㊺ 再連携をまたぐ続き")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": TEXTS2, "original_first": TEXTS2[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="ACCOUNT_A")   # 旧アカウントで始めた枠
post_state.set_part(row, 0, hash=post_state.part_hash(TEXTS2[0]),
                    original_hash=post_state.part_hash(TEXTS2[0]), creation_id="C_A",
                    post_id="ROOT_OF_A", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_PUBLISHED)
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
# 登録もトークンも入れ替わった状況。登録＝トークン（USER1）の照合は通ってしまうので、
# 台帳に固定した「投稿を始めたアカウント(ACCOUNT_A)」だけが最後の砦になる
W.salons = [dict(SALON_ROW, threads_user_id="USER1")]
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("別アカウントから続きを出さない", W.calls["create"] == 0, W.calls["create"])
check("要対応で止める", W.attempts[op]["status"] in ("attention", "hold_repair"),
      W.attempts[op]["status"])

# ── 46. /me が一度こけただけでは全滅しない ──────────────────────
print("\n㊻ /me の一時障害")
reset()
post_saas.SLOT_JST_WINDOWS = {}
post_saas.SLOT = "noon"
post_saas.SALON_FILTER = ""
post_saas.DRY_RUN = False
post_saas.pick_post = lambda name, slot, used, **kw: ["一時障害テスト"]
post_saas.is_promo_time = lambda name, slot: False
W.salons = [dict(SALON_ROW)]
W.me_behavior = lambda n: "timeout" if n == 1 else "ok"
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit:
    pass
check("やり直して投稿できる", len(W.posts) == 1, len(W.posts))
check("記録も残る", len(W.post_logs) == 1, len(W.post_logs))
W.me_behavior = None

# ── 47. 記録だけの復旧は /me の障害に巻き込まれない ───────────────────
print("\n㊼ 全パート公開済み・記録だけ未完で /me が失敗")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{YESTERDAY}:noon"
a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "noon")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(row, 0, hash=post_state.part_hash(TEXTS1[0]),
                    original_hash=post_state.part_hash(TEXTS1[0]), creation_id="C_L",
                    post_id="POST_L", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_PUBLISHED, logged=False)
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
W.me_behavior = lambda n: "timeout"
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
W.me_behavior = None
check("記録は戻る", len(W.post_logs) == 1, len(W.post_logs))
check("投稿はしない", len(W.posts) == 0, len(W.posts))
check("完了になる", W.attempts[op]["status"] == "logged", W.attempts[op]["status"])


# ── 48. 初回実行が「投稿を始めたアカウント」を台帳に残す ────────────────
print("\n㊽ 初回投稿→停止→再連携→再開")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{JST_DATE}:noon"
W.publish_behavior = lambda cid, n: "ok" if cid == "CONTAINER_1" else "timeout"
W.status_override = lambda cid: W.containers[cid]["status"]
(res, row, _), out = quiet(lambda: run(TEXTS2))
check("投稿者が台帳に残る", W.attempts[op].get("publisher_user_id") == "USER1",
      W.attempts[op].get("publisher_user_id"))
print("  → 別アカウントに再連携して再開")
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
W.publish_behavior = lambda cid, n: "ok"
W.status_override = None
before_create = W.calls["create"]
W.me_behavior = lambda n: "ACCOUNT_B"
W.salons = [dict(SALON_ROW, threads_user_id="ACCOUNT_B")]
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
W.me_behavior = None
check("別アカウントでは続きを出さない", W.calls["create"] == before_create, W.calls["create"])
check("要対応で止める", W.attempts[op]["status"] in ("attention", "hold_repair"),
      W.attempts[op]["status"])

# ── 49. 台帳の本文と今回の本文が違えば、公開済み扱いしない ────────────────
print("\n㊾ 台帳と本文が食い違う")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": ["別の本文"], "original_first": "別の本文",
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(row, 0, hash=post_state.part_hash("むかしの本文"),
                    original_hash=post_state.part_hash("むかしの本文"), creation_id="C_M",
                    post_id="POST_M", status=post_state.PART_PUBLISHED)
check("全公開済みと誤判定しない",
      not post_saas._all_parts_published(post_state.fetch(op)), "誤判定した")
row2 = post_state.fetch(op)
row2["payload"] = {"texts": ["A", "B"], "original_first": "A"}
check("部数が違えば誤判定しない", not post_saas._all_parts_published(row2), "誤判定した")

# ── 50. 記録の復旧に失敗したら「完了」にしない ───────────────────
print("\n㊿ 記録の復旧が失敗")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{YESTERDAY}:noon"
a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "noon")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(row, 0, hash=post_state.part_hash(TEXTS1[0]),
                    original_hash=post_state.part_hash(TEXTS1[0]), creation_id="C_R",
                    post_id="POST_R", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_PUBLISHED, logged=False)
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
W.log_insert_behavior = lambda n: "fail"
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("完了にしない", W.attempts[op]["status"] != "logged", W.attempts[op]["status"])
check("次回も回収対象に残る",
      any(r["op_id"] == op for r in post_state.open_issues(salon_ids=[SALON])),
      [r["op_id"] for r in post_state.open_issues(salon_ids=[SALON])])

# ── 51. 当日枠でも、記録だけの復旧は /me を待たない ──────────────────
print("\n(51) 当日枠・全パート公開済み・/me 失敗")
reset()
post_saas.SLOT_JST_WINDOWS = {}
post_saas.SLOT = "noon"
post_saas.SALON_FILTER = ""
post_saas.DRY_RUN = False
post_saas.is_promo_time = lambda name, slot: False
W.salons = [dict(SALON_ROW)]
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(row, 0, hash=post_state.part_hash(TEXTS1[0]),
                    original_hash=post_state.part_hash(TEXTS1[0]), creation_id="C_T",
                    post_id="POST_T", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_PUBLISHED, logged=False)
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(minutes=30)).isoformat()
W.me_behavior = lambda n: "timeout"
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit:
    pass
W.me_behavior = None
check("記録が戻る", len(W.post_logs) == 1, len(W.post_logs))
check("投稿はしない", len(W.posts) == 0, len(W.posts))

# ── 52. /me が401ならトークン切れとして扱う ─────────────────────
print("\n(52) /me が401")
reset()
post_saas.SLOT_JST_WINDOWS = {}
post_saas.SLOT = "noon"
post_saas.DRY_RUN = False
post_saas.pick_post = lambda name, slot, used, **kw: ["401テスト"]
post_saas.is_promo_time = lambda name, slot: False
W.salons = [dict(SALON_ROW)]
W.me_behavior = lambda n: "401"
code = None
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit as e:
    code = e.code
check("トークン切れ専用の終了コード3", code == 3, code)
check("再連携の通知が出る", any("トークン切れ" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:150])
check("401は再試行しない", W.calls.get("me", 0) == 1, W.calls.get("me"))
W.me_behavior = None


# ── 53. 投稿者不明の古い台帳行は、勝手に引き継がない ──────────────────
print("\n(53) 投稿者が記録されていない古い行")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:2026-09-10:noon"
a, row = post_saas._acquire_with_retry(SALON, "2026-09-10", "noon")
row = post_state.update(row, payload={"texts": TEXTS2, "original_first": TEXTS2[0],
                                      "topic_tag": None, "image_url": "", "promo": False})
post_state.set_part(row, 0, hash=post_state.part_hash(TEXTS2[0]),
                    original_hash=post_state.part_hash(TEXTS2[0]), creation_id="C_OLD2",
                    post_id="ROOT_OLD", status=post_state.PART_PUBLISHED)   # publisher なし
post_state.update(post_state.fetch(op), status=post_state.STATUS_PUBLISHED)
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=6)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("続きを出さない", W.calls["create"] == 0, W.calls["create"])
check("要対応で止める", W.attempts[op]["status"] in ("attention", "hold_repair"),
      W.attempts[op]["status"])
check("今のアカウントを後付けしない", not W.attempts[op].get("publisher_user_id"),
      W.attempts[op].get("publisher_user_id"))

# ── 54. 台帳のパートと本文が違えば、公開済みとして飛ばさない ──────────────
print("\n(54) 別の本文のパートを使い回さない")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, publisher_user_id="USER1")
# パート0には「昔の本文」の公開済み記録が入っている
row = post_state.set_part(row, 0, hash=post_state.part_hash("むかしの本文"),
                    original_hash=post_state.part_hash("むかしの本文"),
                          creation_id="C_OLD3", post_id="POST_OLD3",
                          status=post_state.PART_PUBLISHED)
W.publish_behavior = lambda cid, n: "ok"
with contextlib.redirect_stdout(io.StringIO()):
    res = post_saas.threads_post(post_state.fetch(op), "USER1", "TOK", ["いまの本文"])
check("追加で公開しない", len(W.posts) == 0, [p["text"] for p in W.posts])
check("古い投稿IDを使い回さない", res["first_post_id"] != "POST_OLD3", res["first_post_id"])
check("要対応で止める", res["slot_status"] in ("attention", "hold_repair"),
      res["slot_status"])
check("台帳の履歴は消さない",
      (post_state.get_part(post_state.fetch(op), 0) or {}).get("post_id") == "POST_OLD3",
      post_state.get_part(post_state.fetch(op), 0))


# ── 55. ハッシュが無いパートは「全公開済み」と認めない ─────────────────
print("\n(55) ハッシュ欠落・添字重複")
reset()
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": ["本文X"], "original_first": "本文X",
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(row, 0, creation_id="C_NH", post_id="POST_NH",
                    status=post_state.PART_PUBLISHED)      # hash なし
check("ハッシュが無ければ認めない",
      not post_saas._all_parts_published(post_state.fetch(op)), "誤判定した")
dup = dict(post_state.fetch(op))
h = post_state.part_hash("本文X")
dup["parts"] = [{"i": 0, "status": "published", "hash": h},
                {"i": 0, "status": "published", "hash": h}]
dup["payload"] = {"texts": ["本文X", "本文X"]}
try:
    verdict = post_saas._all_parts_published(dup)
except Exception as e:
    verdict = f"例外になった: {type(e).__name__}: {e}"
check("添字が重複していれば認めない", verdict is False, verdict)

# ── 56. 全パート公開・記録済みなら、/me を待たず完了にする ────────────────
print("\n(56) 全公開・記録済み・/me 失敗")
reset()
post_saas.SLOT_JST_WINDOWS = {}
post_saas.SLOT = "noon"
post_saas.SALON_FILTER = ""
post_saas.DRY_RUN = False
post_saas.is_promo_time = lambda name, slot: False
W.salons = [dict(SALON_ROW)]
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1", logged=True)
post_state.set_part(row, 0, hash=post_state.part_hash(TEXTS1[0]),
                    original_hash=post_state.part_hash(TEXTS1[0]), creation_id="C_D",
                    status=post_state.PART_PUBLISHED)     # 投稿IDは欠落
post_state.update(post_state.fetch(op), status=post_state.STATUS_PUBLISHED)
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(minutes=30)).isoformat()
W.me_behavior = lambda n: "timeout"
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit:
    pass
W.me_behavior = None
check("完了になる", W.attempts[op]["status"] == "logged", W.attempts[op]["status"])
check("投稿しない", len(W.posts) == 0, len(W.posts))
check("記録を増やさない", len(W.post_logs) == 0, len(W.post_logs))
check("last_run を同期する", any(x[1] == "noon" for x in SYNCED), SYNCED)

# ── 57. 回収中のトークン切れは再連携の通知になる ────────────────────
print("\n(57) 回収時の401")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{YESTERDAY}:noon"
a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "noon")
row = post_state.update(row, payload={"texts": TEXTS2, "original_first": TEXTS2[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(row, 0, hash=post_state.part_hash(TEXTS2[0]),
                    original_hash=post_state.part_hash(TEXTS2[0]), creation_id="C_401",
                    post_id="P401", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_PUBLISHED)
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
W.me_behavior = lambda n: "401"
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
W.me_behavior = None
check("再連携が必要だと分かる通知", any("連携が切れて" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:200])
check("投稿しない", len(W.posts) == 0, len(W.posts))

# ── 58. 残り0秒では通信を始めない ──────────────────────────
print("\n(58) 締切ちょうど")
reset()
CLOCK.frozen = 1000.0                       # 時計を止めて境界ちょうどを再現する
post_saas._deadline = CLOCK.time()          # 残り0秒
check("残り0秒は時間切れ扱い", post_saas._out_of_time("境界") is True, "時間内と判定した")
post_saas._deadline = CLOCK.time() + 1
check("残り1秒は時間内", post_saas._out_of_time("境界") is False, "時間切れと判定した")
post_saas._deadline = None
CLOCK.frozen = None


# ── 59. 本文不一致で止めた枠は、回収でも間違った本文を記録しない ────────────
print("\n(59) 不一致で停止 → 回収")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": ["あたらしい本文"], "original_first": "あたらしい本文",
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(row, 0, hash=post_state.part_hash("ふるい本文"),
                    original_hash=post_state.part_hash("ふるい本文"), creation_id="C_MM",
                    post_id="POST_MM", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_HOLD_REPAIR, note="本文不一致")
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("違う本文を記録しない", len(W.post_logs) == 0, W.post_logs)
check("完了にしない", W.attempts[op]["status"] != "logged", W.attempts[op]["status"])
check("理由を通知する", any("食い違" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:200])

# ── 60. 台帳に余分な／重複したパートがあれば投稿しない ────────────────
print("\n(60) 台帳の不整合")
for label, parts in (("2部目だけ履歴",
                      [{"i": 0, "status": "published", "hash": None, "post_id": "P0"}]),
                     ("未処理なのに公開履歴",
                      [{"i": 0, "status": "pending", "hash": None, "post_id": "P0"}]),
                     ("余分なパート",
                      [{"i": 0, "status": "published", "hash": None, "post_id": "P0"},
                       {"i": 1, "status": "unknown", "creation_id": "C_EX"}]),
                     ("番号の重複",
                      [{"i": 0, "status": "published", "hash": None, "post_id": "P0"},
                       {"i": 0, "status": "unknown", "creation_id": "C_DUP"}])):
    reset()
    op = f"{SALON}:{JST_DATE}:noon"
    a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
    texts = ["ひとつだけの本文"] if label != "2部目だけ履歴" else ["1部目", "2部目"]
    if label == "2部目だけ履歴":
        parts = [{"i": 1, "status": "published", "hash": post_state.part_hash("2部目"),
                  "post_id": "P1"}]
    for p in parts:
        if p.get("hash") is None:
            p["hash"] = post_state.part_hash(texts[0])
    row = post_state.update(row, payload={"texts": texts, "original_first": texts[0],
                                          "topic_tag": None, "image_url": "", "promo": False},
                            publisher_user_id="USER1", parts=parts)
    with contextlib.redirect_stdout(io.StringIO()):
        res = post_saas.threads_post(post_state.fetch(op), "USER1", "TOK", texts)
    check(f"{label}：投稿しない", W.calls["create"] == 0, W.calls["create"])
    check(f"{label}：要対応で止める", res["slot_status"] in ("attention", "hold_repair"),
          res["slot_status"])

# ── 61. 記録専用の回収でも、401は再連携の通知になる ──────────────────
print("\n(61) 状態照会が401")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(row, 0, hash=post_state.part_hash(TEXTS1[0]),
                    original_hash=post_state.part_hash(TEXTS1[0]), creation_id="C_A401",
                    status=post_state.PART_UNKNOWN, lost_response=True)
post_state.update(post_state.fetch(op), status=post_state.STATUS_HOLD_REPAIR, note="人待ち")
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
W.status_override = "__401__"
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
W.status_override = None
check("再連携の通知が出る", any("連携が切れて" in m or "トークン" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:200])
check("記録しない", len(W.post_logs) == 0, len(W.post_logs))

# ── 62. 回収で片づいたら last_run も同期する ─────────────────────
print("\n(62) 回収後の last_run 同期")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1", logged=True)
post_state.set_part(row, 0, hash=post_state.part_hash(TEXTS1[0]),
                    original_hash=post_state.part_hash(TEXTS1[0]), creation_id="C_S",
                    post_id="P_S", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_PUBLISHED)
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("完了になる", W.attempts[op]["status"] == "logged", W.attempts[op]["status"])
check("last_run を同期する", any(x[1] == "noon" for x in SYNCED), SYNCED)
print("  → 前日の枠なら同期しない")
reset()
W.salons = [SALON_ROW]
op2 = f"{SALON}:{YESTERDAY}:noon"
a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "noon")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1", logged=True)
post_state.set_part(row, 0, hash=post_state.part_hash(TEXTS1[0]),
                    original_hash=post_state.part_hash(TEXTS1[0]), creation_id="C_S2",
                    post_id="P_S2", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op2), status=post_state.STATUS_PUBLISHED)
W.attempts[op2]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("前日分は同期しない", not SYNCED, SYNCED)


# ── 63. 原文だけ差し替えても、出していない本文を記録しない ─────────────
print("\n(63) original_first の差し替え")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": ["公開した本文"], "original_first": "公開した本文",
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(row, 0, hash=post_state.part_hash("公開した本文"),
                    original_hash=post_state.part_hash("公開した本文"),
                    creation_id="C_OF", post_id="P_OF", status=post_state.PART_PUBLISHED)
# payload の original_first だけを、出していない本文に差し替える
cur = post_state.fetch(op)
pl = dict(cur["payload"]); pl["original_first"] = "出していない本文"
post_state.update(cur, payload=pl, status=post_state.STATUS_HOLD_REPAIR, note="人待ち")
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("出していない本文を記録しない",
      not any(l["post_content"] == "出していない本文" for l in W.post_logs), W.post_logs)
check("完了にしない", W.attempts[op]["status"] != "logged", W.attempts[op]["status"])

# ── 64. 回収中に1件こけても、残りは処理して通知する ────────────────
print("\n(64) 回収の1件が例外")
reset()
S2 = "22222222-2222-2222-2222-222222222222"
W.salons = [SALON_ROW, {"id": S2, "salon_name": "テストサロン2", "access_token": "TOK",
                        "threads_user_id": "USER1", "instagram_url": ""}]
for sid in (SALON, S2):
    o = f"{sid}:{YESTERDAY}:noon"
    a, r0 = post_saas._acquire_with_retry(sid, YESTERDAY, "noon")
    r0 = post_state.update(r0, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                        "topic_tag": None, "image_url": "", "promo": False},
                           publisher_user_id="USER1")
    post_state.set_part(r0, 0, hash=post_state.part_hash(TEXTS1[0]),
                        original_hash=post_state.part_hash(TEXTS1[0]),
                        creation_id=f"C_{sid[:4]}", post_id=f"P_{sid[:4]}",
                        status=post_state.PART_PUBLISHED)
    post_state.update(post_state.fetch(o), status=post_state.STATUS_PUBLISHED, logged=False)
    W.attempts[o]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
orig_repair = post_saas._repair_log_only
calls = {"n": 0}
def boom(row, salon, slot, jst_date, finish_status=None, quiet=False):
    calls["n"] += 1
    if calls["n"] == 1:
        raise TimeoutError("timed out")
    return orig_repair(row, salon, slot, jst_date, finish_status=finish_status, quiet=quiet)
post_saas._repair_log_only = boom
escaped = None
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.recover_open_attempts(W.salons)
except Exception as e:
    escaped = f"{type(e).__name__}: {e}"
post_saas._repair_log_only = orig_repair
check("例外が回収全体を止めない", escaped is None, escaped)
check("2件目も処理される", calls["n"] == 2, calls["n"])
check("失敗を通知する", any("片づけられなかった" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:200])
check("通知は1通にまとまる", sum(1 for m in NOTIFY if "片づけられなかった" in m) == 1,
      [m[:40] for m in NOTIFY])

# ── 65. 過去日の回収では last_run を更新しない（本物のガードを通す）──────────
print("\n(65) 過去日ガード")
reset()
post_saas._sync_last_run("テストサロン", "noon", jst_date=YESTERDAY)
check("前日分は書かない", not SYNCED, SYNCED)
post_saas._sync_last_run("テストサロン", "noon", jst_date=JST_DATE)
check("当日分は書く", len(SYNCED) == 1, SYNCED)


# ── 66. 記録の復旧に失敗したら、ログだけで終わらせず通知する ──────────────
print("\n(66) 回収の失敗を通知する")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{YESTERDAY}:noon"
a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "noon")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(row, 0, hash=post_state.part_hash(TEXTS1[0]),
                    original_hash=post_state.part_hash(TEXTS1[0]),
                    creation_id="C_NF", post_id="P_NF", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_PUBLISHED, logged=False)
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
W.log_insert_behavior = lambda n: "fail"
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("記録は戻らない（前提）", len(W.post_logs) == 0, len(W.post_logs))
check("片づかなかったことを通知する",
      any("片づけられなかった" in m for m in NOTIFY), json.dumps(NOTIFY, ensure_ascii=False)[:200])

# ── 67. 通常の投稿が成功したら last_run を同期する ──────────────────
print("\n(67) 通常投稿後の last_run 同期")
reset()
W.publish_behavior = lambda cid, n: "ok"
(res, row, _), out = quiet(lambda: run(TEXTS1))
check("投稿できる", len(W.posts) == 1, len(W.posts))
check("last_run を同期する", any(x[1] == "noon" for x in SYNCED), SYNCED)


# ── 68. ツリー続行の経路でも、原文の差し替えを記録しない ─────────────────
print("\n(68) 続行経路での原文差し替え")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": TEXTS2, "original_first": TEXTS2[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
# 親Aだけ公開済み（原文Aで固定）。子はこれから
post_state.set_part(row, 0, hash=post_state.part_hash(TEXTS2[0]),
                    original_hash=post_state.part_hash(TEXTS2[0]),
                    creation_id="C_TA", post_id="P_TA", status=post_state.PART_PUBLISHED)
cur = post_state.fetch(op)
pl = dict(cur["payload"]); pl["original_first"] = "出していない原文"
post_state.update(cur, payload=pl, status=post_state.STATUS_PUBLISHED, logged=False)
W.publish_behavior = lambda cid, n: "ok"
with contextlib.redirect_stdout(io.StringIO()):
    status, detail, _k = post_saas._run_slot(post_state.fetch(op), "resume", SALON_ROW,
                                         "USER1", "TOK", "noon", "@testsalon")
check("出していない原文を記録しない",
      not any(l["post_content"] == "出していない原文" for l in W.post_logs), W.post_logs)
check("完了にしない", W.attempts[op]["status"] != "logged", W.attempts[op]["status"])


# ── 69. 同じ理由の回収失敗を毎回通知しない ─────────────────────
print("\n(69) 通知の連発")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{YESTERDAY}:noon"
a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "noon")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(row, 0, hash=post_state.part_hash(TEXTS1[0]),
                    original_hash=post_state.part_hash(TEXTS1[0]),
                    creation_id="C_RP", post_id="P_RP", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_PUBLISHED, logged=False)
W.log_insert_behavior = lambda n: "fail"
counts = []
for _ in range(3):
    NOTIFY.clear()
    W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.recover_open_attempts(W.salons)
    counts.append(len(NOTIFY))
check("1回目は通知する", counts[0] >= 1, counts)
check("2回目以降は鳴らさない", counts[1] == 0 and counts[2] == 0, counts)

# ── 70. 宣伝枠は、記録の復旧で「使用済み」も戻す ─────────────────
print("\n(70) 宣伝の使用済み記録の復旧")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{YESTERDAY}:evening"
import json as _json
open(post_saas.PROMO_USED_FILE, "w").write("[]")
a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "evening")
row = post_state.update(row, payload={"texts": ["宣伝本文"], "original_first": "宣伝本文",
                                      "topic_tag": None, "image_url": "img", "promo": True},
                        publisher_user_id="USER1")
post_state.set_part(row, 0, hash=post_state.part_hash("宣伝本文"),
                    original_hash=post_state.part_hash("宣伝本文"),
                    creation_id="C_PR", post_id="P_PR", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_PUBLISHED, logged=False)
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("記録が戻る", len(W.post_logs) == 1, len(W.post_logs))
check("宣伝の使用済みも戻る（本物のファイルに書かれる）",
      _json.load(open(post_saas.PROMO_USED_FILE)) == ["宣伝本文"],
      open(post_saas.PROMO_USED_FILE).read()[:80])

# ── 71. 原文が差し替わっていたら、宣伝の使用済みにもしない ─────────────
print("\n(71) 宣伝の使用済みと原文照合")
reset()
op = f"{SALON}:{JST_DATE}:noon"
import json as _json
open(post_saas.PROMO_USED_FILE, "w").write("[]")
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": ["宣伝A", "続き"], "original_first": "宣伝A",
                                      "topic_tag": None, "image_url": "", "promo": True},
                        publisher_user_id="USER1", logged=True)
post_state.set_part(row, 0, hash=post_state.part_hash("宣伝A"),
                    original_hash=post_state.part_hash("宣伝A"),
                    creation_id="C_PA", post_id="P_PA", status=post_state.PART_PUBLISHED)
cur = post_state.fetch(op)
pl = dict(cur["payload"]); pl["original_first"] = "宣伝B（未投稿）"
post_state.update(cur, payload=pl, status=post_state.STATUS_PUBLISHED)
W.publish_behavior = lambda cid, n: "ok"
with contextlib.redirect_stdout(io.StringIO()):
    post_saas._run_slot(post_state.fetch(op), "resume", SALON_ROW, "USER1", "TOK",
                        "noon", "@testsalon")
check("未投稿の宣伝文を使用済みにしない",
      "宣伝B（未投稿）" not in _json.load(open(post_saas.PROMO_USED_FILE)),
      open(post_saas.PROMO_USED_FILE).read()[:80])


# ── 72. コンテナを作り直しても、原文ハッシュは最初のものを保つ ───────────
print("\n(72) 原文ハッシュの上書き防止")
reset()
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": ["本文A"], "original_first": "原文A",
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
# 1回目：コンテナは作るが、公開を明確に断られる（＝未公開が確定・作り直してよい）
W.publish_behavior = lambda cid, n: "http400"
W.status_override = "ERROR"
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.threads_post(post_state.fetch(op), "USER1", "TOK", ["本文A"],
                               original_first="原文A")
except Exception:
    pass    # 4回とも断られて失敗するのが正しい（未公開が確定）
check("1回目は公開されない", len(W.posts) == 0, len(W.posts))
# 2回目：原文だけ差し替えて再開する（本文は同じ）
W.publish_behavior = lambda cid, n: "ok"
W.status_override = None
cur = post_state.fetch(op)
pl = dict(cur["payload"]); pl["original_first"] = "原文B（未投稿）"
row2 = post_state.update(cur, payload=pl)
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.threads_post(post_state.fetch(op), "USER1", "TOK", ["本文A"],
                           original_first="原文B（未投稿）")
first = post_state.get_part(post_state.fetch(op), 0) or {}
check("作り直しても原文ハッシュは最初のまま",
      first.get("original_hash") == post_state.part_hash("原文A"),
      first.get("original_hash"))
check("差し替えを見抜く",
      post_saas._original_mismatch(post_state.fetch(op), "原文B（未投稿）") is not None,
      "見抜けなかった")


# ── 73. 未公開で失敗した枠を選び直したとき、公開した本文を記録できる ────────
print("\n(73) 選び直し（改ざんなしの通常運用）")
reset()
op = f"{SALON}:{JST_DATE}:noon"
# 1回目：本文Aで始めるが、公開を明確に断られて未公開が確定する
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
W.publish_behavior = lambda cid, n: "http400"
W.status_override = "ERROR"
post_saas.pick_post = lambda name, slot, used, **kw: ["本文A"]
post_saas.is_promo_time = lambda name, slot: False
post_saas.get_used_posts = lambda sid, slot=None: set()
post_saas._maybe_add_instagram_cta_saas = lambda t, u: t
post_saas._enforce_threads_limit = lambda t: t
post_saas._select_topic = lambda t, n: None
with contextlib.redirect_stdout(io.StringIO()):
    try:
        post_saas._run_slot(row, "go", SALON_ROW, "USER1", "TOK", "noon", "@testsalon")
    except Exception:
        pass
check("1回目は公開されない", len(W.posts) == 0, len(W.posts))
print("  → 次の実行で別の本文Bを選び直す")
W.publish_behavior = lambda cid, n: "ok"
W.status_override = None
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=1)).isoformat()
post_saas.pick_post = lambda name, slot, used, **kw: ["本文B"]
a2, row2 = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
with contextlib.redirect_stdout(io.StringIO()):
    st2, d2, _k = post_saas._run_slot(row2, a2, SALON_ROW, "USER1", "TOK", "noon", "@testsalon")
check("本文Bが公開される", any(p["text"] == "本文B" for p in W.posts), [p["text"] for p in W.posts])
check("本文Bが記録される", any(l["post_content"] == "本文B" for l in W.post_logs), W.post_logs)
check("完了になる", W.attempts[op]["status"] == "logged", W.attempts[op]["status"])


# ── 74. 通知が送れなかったら「通知済み」にしない ────────────────────
print("\n(74) 通知の送信に失敗")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{YESTERDAY}:noon"
a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "noon")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(row, 0, hash=post_state.part_hash(TEXTS1[0]),
                    original_hash=post_state.part_hash(TEXTS1[0]),
                    creation_id="C_NS", post_id="P_NS", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_PUBLISHED, logged=False)
W.log_insert_behavior = lambda n: "fail"
NOTIFY_OK[0] = False        # LINEが送れない状況
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("送れなかったら通知済みにしない",
      "通知済み" not in (W.attempts[op].get("note") or ""), W.attempts[op].get("note"))
print("  → LINEが復旧したら、ちゃんと知らせる")
NOTIFY_OK[0] = True
NOTIFY.clear()
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("復旧後に通知が届く", len(NOTIFY) >= 1, NOTIFY)

# ── 75. 失敗の原因が変わったら、もう一度知らせる ────────────────────
print("\n(75) 原因が変わったとき")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{YESTERDAY}:noon"
a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "noon")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(row, 0, hash=post_state.part_hash(TEXTS1[0]),
                    original_hash=post_state.part_hash(TEXTS1[0]),
                    creation_id="C_CH", post_id="P_CH", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_PUBLISHED, logged=False)
W.log_insert_behavior = lambda n: "fail"
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
first_n = len(NOTIFY)
print("  → 原因がトークン切れに変わる")
NOTIFY.clear()
W.me_behavior = lambda n: "401"
W.status_override = "__401__"
post_state.set_part(post_state.fetch(op), 0, status=post_state.PART_UNKNOWN)
post_state.update(post_state.fetch(op), status=post_state.STATUS_HOLD_REPAIR)
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
W.me_behavior = None
W.status_override = None
check("1回目は知らせる", first_n >= 1, first_n)
check("原因が変わったら改めて知らせる", len(NOTIFY) >= 1,
      f'{NOTIFY} / note={W.attempts[op].get("note")}')
print("  → 最初の原因に戻っても、もう鳴らさない")
NOTIFY.clear()
W.me_behavior = None
W.status_override = None
W.log_insert_behavior = lambda n: "fail"
post_state.set_part(post_state.fetch(op), 0, status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_HOLD_REPAIR, logged=False)
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("知らせた種類は覚えている", len(NOTIFY) == 0,
      f'{NOTIFY} / note={W.attempts[op].get("note")}')

# ── 76. 宣伝の使用済み記録が失敗したら完了にしない ──────────────────
print("\n(76) 宣伝の使用済み記録が失敗")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{YESTERDAY}:evening"
orig_mark = post_saas.mark_promo_used
post_saas.mark_promo_used = lambda t: (_ for _ in ()).throw(OSError("書き込めない"))
a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "evening")
row = post_state.update(row, payload={"texts": ["宣伝X"], "original_first": "宣伝X",
                                      "topic_tag": None, "image_url": "i", "promo": True},
                        publisher_user_id="USER1")
post_state.set_part(row, 0, hash=post_state.part_hash("宣伝X"),
                    original_hash=post_state.part_hash("宣伝X"),
                    creation_id="C_PX", post_id="P_PX", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_PUBLISHED, logged=False)
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("完了にしない", W.attempts[op]["status"] != "logged", W.attempts[op]["status"])
check("回収対象に残る",
      any(r["op_id"] == op for r in post_state.open_issues(salon_ids=[SALON])),
      [r["op_id"] for r in post_state.open_issues(salon_ids=[SALON])])
print("  → 書き込めるようになったら片づく")
post_saas.mark_promo_used = orig_mark
open(post_saas.PROMO_USED_FILE, "w").write("[]")
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("宣伝の使用済みが戻る（本物のファイル）",
      _json.load(open(post_saas.PROMO_USED_FILE)) == ["宣伝X"],
      open(post_saas.PROMO_USED_FILE).read()[:80])
check("完了になる", W.attempts[op]["status"] == "logged", W.attempts[op]["status"])


# ── 77. 通常投稿でも、宣伝の使用済みが残っていれば完了にしない ──────────────
print("\n(77) 通常投稿＋宣伝の使用済み失敗")
reset()
op = f"{SALON}:{JST_DATE}:noon"
orig_mark = post_saas.mark_promo_used
post_saas.mark_promo_used = lambda t: (_ for _ in ()).throw(OSError("書き込めない"))
orig_promo_time = post_saas.is_promo_time
orig_pick_promo = post_saas.pick_promo
post_saas.is_promo_time = lambda name, slot: True
post_saas.pick_promo = lambda sid=None: {"text": "宣伝Z", "image_url": "img"}
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
post_saas.get_used_posts = lambda sid, slot=None: set()
post_saas._maybe_add_instagram_cta_saas = lambda t, u: t
post_saas._enforce_threads_limit = lambda t: t
post_saas._select_topic = lambda t, n: None
W.publish_behavior = lambda cid, n: "ok"
with contextlib.redirect_stdout(io.StringIO()):
    st, dt, _k = post_saas._run_slot(row, "go", SALON_ROW, "USER1", "TOK", "noon", "@testsalon")
post_saas.mark_promo_used = orig_mark
post_saas.is_promo_time = orig_promo_time
post_saas.pick_promo = orig_pick_promo
check("投稿はされる", len(W.posts) == 1, len(W.posts))
check("記録はされる", len(W.post_logs) == 1, len(W.post_logs))
check("完了にしない", W.attempts[op]["status"] != "logged", W.attempts[op]["status"])
check("回収対象に残る",
      any(r["op_id"] == op for r in post_state.open_issues(salon_ids=[SALON])),
      [r["op_id"] for r in post_state.open_issues(salon_ids=[SALON])])


# ── 78. 全公開済みの回収でも、原文が差し替わっていれば使用済みにしない ────────
print("\n(78) 全公開済み経路＋原文差し替え")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{YESTERDAY}:evening"
open(post_saas.PROMO_USED_FILE, "w").write("[]")
a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "evening")
row = post_state.update(row, payload={"texts": ["宣伝A"], "original_first": "宣伝A",
                                      "topic_tag": None, "image_url": "i", "promo": True},
                        publisher_user_id="USER1", logged=True)
post_state.set_part(row, 0, hash=post_state.part_hash("宣伝A"),
                    original_hash=post_state.part_hash("宣伝A"),
                    creation_id="C_PZ", post_id="P_PZ", status=post_state.PART_PUBLISHED)
cur = post_state.fetch(op)
pl = dict(cur["payload"]); pl["original_first"] = "宣伝B（未投稿）"
post_state.update(cur, payload=pl, status=post_state.STATUS_PUBLISHED)
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
saved = _json.load(open(post_saas.PROMO_USED_FILE))
check("未投稿の宣伝文を使用済みにしない", "宣伝B（未投稿）" not in saved, saved)
check("完了にしない", W.attempts[op]["status"] != "logged", W.attempts[op]["status"])


# ── 79. 使用済みを確認できないときは、宣伝を出さない ──────────────────
print("\n(79) 使用済みの確認ができない")
reset()
_json.dump({"posts": ["宣伝A", "宣伝B"], "image_url": "img"},
           open(post_saas.PROMO_POOL_FILE, "w"), ensure_ascii=False)
open(post_saas.PROMO_USED_FILE, "w").write("[]")
W.post_logs.append({"salon_id": SALON, "slot": "evening", "post_content": "宣伝A",
                    "posted_at": _dt.now(_tz.utc).isoformat(), "op_id": "past"})
got = post_saas.pick_promo(SALON)
check("DBが見えれば、出した宣伝Aは避ける", got and got["text"] == "宣伝B", got)
orig_get = post_saas.supabase_get
post_saas.supabase_get = lambda path, params=None: (_ for _ in ()).throw(TimeoutError("timed out"))
failed = None
try:
    post_saas.pick_promo(SALON)
except post_saas.PromoCheckFailed as e:
    failed = str(e)
post_saas.supabase_get = orig_get
check("確認できないときは選ばない（例外で止める）", failed is not None, "選んでしまった")

# ── 80. 記録済みの人待ちが200件あっても、後ろの未完行に届く ────────────────
print("\n(80) 除外対象が大量にある")
reset()
for i in range(200):
    o = f"{SALON}:2026-07-{i%28+1:02d}:noon{i}"
    W.attempts[o] = {"op_id": o, "salon_id": SALON, "jst_date": f"2026-07-{i%28+1:02d}",
                     "slot": "noon", "status": "attention", "parts": [], "rev": 0,
                     "payload": None, "note": "人待ち", "logged": True,
                     "updated_at": (_dt.now(_tz.utc) - _td(days=40)).isoformat()}
tail_op = f"{SALON}:{YESTERDAY}:evening"
W.attempts[tail_op] = {"op_id": tail_op, "salon_id": SALON, "jst_date": YESTERDAY,
                       "slot": "evening", "status": "unknown", "parts": [], "rev": 0,
                       "payload": None, "note": None, "logged": False,
                       "updated_at": (_dt.now(_tz.utc) - _td(hours=1)).isoformat()}
rows = post_state.open_issues(salon_ids=[SALON])
check("後ろの未完行に届く", any(r["op_id"] == tail_op for r in rows),
      f"{len(rows)}件: {[r['op_id'][-18:] for r in rows][:3]}")

# ── 81. 宣伝の使用済みが戻せないときは、必ず知らせる ────────────────────
print("\n(81) 宣伝の使用済みが戻せない（記録は済んでいる）")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{YESTERDAY}:evening"
a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "evening")
row = post_state.update(row, payload={"texts": ["宣伝W"], "original_first": "宣伝W",
                                      "topic_tag": None, "image_url": "i", "promo": True},
                        publisher_user_id="USER1", logged=True)
post_state.set_part(row, 0, hash=post_state.part_hash("宣伝W"),
                    original_hash=post_state.part_hash("宣伝W"),
                    creation_id="C_PW", post_id="P_PW", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_HOLD_REPAIR, note="人待ち")
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
orig_mark = post_saas.mark_promo_used
post_saas.mark_promo_used = lambda t: (_ for _ in ()).throw(OSError("書き込めない"))
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
post_saas.mark_promo_used = orig_mark
check("知らせる", any("片づけられなかった" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:200])
check("人待ちのまま", W.attempts[op]["status"] in ("attention", "hold_repair"),
      W.attempts[op]["status"])


# ── 82. 通知に載せていない枠は「通知済み」にしない ────────────────────
print("\n(82) 6件以上の失敗")
reset()
W.salons = [SALON_ROW]
ops = []
for i in range(6):
    o = f"{SALON}:{YESTERDAY}:noon{i}"
    W.attempts[o] = {"op_id": o, "salon_id": SALON, "jst_date": YESTERDAY,
                     "slot": f"noon{i}", "status": "published", "parts": [], "rev": 0,
                     "payload": None, "note": None, "logged": False,
                     "updated_at": (_dt.now(_tz.utc) - _td(hours=5 - i * 0.1)).isoformat()}
    ops.append(o)
post_saas.RECOVER_MAX = 10
orig_acquire = post_state.acquire
post_state.acquire = lambda *a, **k: (_ for _ in ()).throw(TimeoutError("timed out"))
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
post_state.acquire = orig_acquire
post_saas.RECOVER_MAX = 5
msg = "\n".join(NOTIFY)
listed = [o for o in ops if o in msg]
unlisted = [o for o in ops if o not in msg]
check("5件だけ本文に載る", len(listed) == 5, len(listed))
check("載った枠は通知済みになる",
      all("通知済み" in (W.attempts[o].get("note") or "") for o in listed),
      [W.attempts[o].get("note") for o in listed])
check("載らなかった枠は通知済みにしない",
      all("通知済み" not in (W.attempts[o].get("note") or "") for o in unlisted),
      [W.attempts[o].get("note") for o in unlisted])


# ── 83. 前週の未確定な宣伝を、翌週に選び直さない ────────────────────
print("\n(83) 週をまたぐ宣伝の未確定")
reset()
_json.dump({"posts": ["宣伝A", "宣伝B"], "image_url": "img"},
           open(post_saas.PROMO_POOL_FILE, "w"), ensure_ascii=False)
open(post_saas.PROMO_USED_FILE, "w").write("[]")
# 先週：宣伝Aを出したが応答を失い、記録も使用済みも残っていない
last_week = (_dt.now(_tz.utc) - _td(days=7)).strftime("%Y-%m-%d")
op = f"{SALON}:{last_week}:evening"
W.attempts[op] = {"op_id": op, "salon_id": SALON, "jst_date": last_week, "slot": "evening",
                  "status": "unknown", "rev": 0, "logged": False, "note": None,
                  "parts": [{"i": 0, "status": "unknown", "hash": post_state.part_hash("宣伝A"),
                             "creation_id": "C_LW", "lost_response": True}],
                  "payload": {"texts": ["宣伝A"], "original_first": "宣伝A",
                              "topic_tag": None, "image_url": "img", "promo": True},
                  "updated_at": (_dt.now(_tz.utc) - _td(days=7)).isoformat()}
got = post_saas.pick_promo(SALON)
check("未確定の宣伝Aは選ばない", got and got["text"] == "宣伝B", got)

# ── 84. 補充側も、投稿側と同じ使用済みを見る ───────────────────────
print("\n(84) 補充側の使用済み判定")
reset()
W.post_logs.append({"salon_id": SALON, "slot": "evening", "post_content": "宣伝A",
                    "posted_at": _dt.now(_tz.utc).isoformat(), "op_id": "p1"})
W.post_logs.append({"salon_id": SALON, "slot": "evening", "post_content": "宣伝B",
                    "posted_at": _dt.now(_tz.utc).isoformat(), "op_id": "p2"})
used_db = post_saas._promo_used_from_db(SALON)
check("DBから2本とも使用済みと分かる",
      {post_state.norm_text("宣伝A"), post_state.norm_text("宣伝B")} <= used_db, used_db)
open(post_saas.PROMO_USED_FILE, "w").write("[]")   # ローカルJSONは失われている
_json.dump({"posts": ["宣伝A", "宣伝B"], "image_url": "img"},
           open(post_saas.PROMO_POOL_FILE, "w"), ensure_ascii=False)
check("投稿側は「選べる宣伝なし」", post_saas.pick_promo(SALON) is None, post_saas.pick_promo(SALON))


# ── 85. 使用済みを確認できないときは、通常投稿に切り替えて投稿を止めない ────────
print("\n(85) 確認不能から通常投稿へ")
reset()
post_saas.SLOT = "noon"
post_saas.SALON_FILTER = ""
post_saas.DRY_RUN = False
post_saas.SLOT_JST_WINDOWS = {}
W.salons = [dict(SALON_ROW)]
_json.dump({"posts": ["宣伝A"], "image_url": "img"},
           open(post_saas.PROMO_POOL_FILE, "w"), ensure_ascii=False)
open(post_saas.PROMO_USED_FILE, "w").write("[]")
orig_promo_time = post_saas.is_promo_time
post_saas.is_promo_time = lambda name, slot: True
post_saas.pick_post = lambda name, slot, used, **kw: ["通常の本文"]
orig_get = post_saas.supabase_get
def flaky_get(path, params=None):
    if path == "post_attempts" and (params or {}).get("select", "").startswith("payload"):
        raise TimeoutError("timed out")
    return orig_get(path, params)
post_saas.supabase_get = flaky_get
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit:
    pass
post_saas.supabase_get = orig_get
post_saas.is_promo_time = orig_promo_time
check("宣伝は出さない", not any(p["text"] == "宣伝A" for p in W.posts), [p["text"] for p in W.posts])
check("通常の投稿は出す", any(p["text"] == "通常の本文" for p in W.posts), [p["text"] for p in W.posts])
check("理由を知らせる", any("確認できなかった" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:200])
check("在庫切れとは言わない", not any("在庫が空" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:200])

# ── 86. 例外処理の中でDBが落ちても、まとめ通知まで届く ────────────────
print("\n(86) 例外処理中のDB障害")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{YESTERDAY}:noon"
a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "noon")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(row, 0, hash=post_state.part_hash(TEXTS1[0]),
                    original_hash=post_state.part_hash(TEXTS1[0]),
                    creation_id="C_SF", post_id="P_SF", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_PUBLISHED, logged=False)
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
W.log_insert_behavior = lambda n: "fail"
orig_fetch = post_state.fetch
calls = {"n": 0}
def flaky_fetch(op_id):
    calls["n"] += 1
    if calls["n"] > 3:          # 記録復旧のあとの取り直しから落とす
        raise TimeoutError("timed out")
    return orig_fetch(op_id)
post_state.fetch = flaky_fetch
escaped = None
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.recover_open_attempts(W.salons)
except Exception as e:
    escaped = f"{type(e).__name__}: {e}"
post_state.fetch = orig_fetch
check("例外が外へ出ない", escaped is None, escaped)
check("まとめ通知は届く", any("片づけられなかった" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:200])


# ── 87. 公開後に台帳が書けなくても、記録の修復は自動で続く ────────────────
print("\n(87) 公開成功→台帳保存失敗→次回に記録が戻る")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{JST_DATE}:noon"
W.publish_behavior = lambda cid, n: "ok"
def fail_published_patch2(o, body):
    parts = (body or {}).get("parts")
    if parts and any(p.get("status") == "published" for p in parts):
        return "fail"
    return "ok"
W.attempts_patch_behavior = fail_published_patch2
W.log_insert_behavior = lambda n: "fail"
(res, row, _), out = quiet(lambda: run(TEXTS1))
check("投稿は出る", len(W.posts) == 1, len(W.posts))
check("記録の修復が続く状態で残る",
      W.attempts[op]["status"] == "hold_repair", W.attempts[op]["status"])
check("回収対象に残る",
      any(r["op_id"] == op for r in post_state.open_issues(salon_ids=[SALON])),
      [r["op_id"] for r in post_state.open_issues(salon_ids=[SALON])])

# ── 88. 人の確認待ち(attention)は、自動処理を一切通さない ──────────────
print("\n(88) attention は自動で触らない")
reset()
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.update(post_state.fetch(op), status=post_state.STATUS_ATTENTION, note="人の確認待ち")
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(days=2)).isoformat()
for allow in (False, True):
    act, _r = post_state.acquire(SALON, JST_DATE, "noon", allow_attention=allow)
    check(f"allow_attention={allow} でも hold", act == "hold", act)
check("回収対象に入らない",
      not any(r["op_id"] == op for r in post_state.open_issues(salon_ids=[SALON])),
      [r["op_id"] for r in post_state.open_issues(salon_ids=[SALON])])
check("状態が変わらない", W.attempts[op]["status"] == "attention", W.attempts[op]["status"])
check("公開要求は0回", W.calls["publish"] == 0, W.calls["publish"])


# ── 89. 別の実行が「人待ち」にした枠を、古い結果で戻さない ────────────────
print("\n(89) 古い結末で停止を解除しない")
reset()
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
stale = dict(row)                      # 手元に古い行を持ったまま
post_state.update(post_state.fetch(op), status=post_state.STATUS_ATTENTION, note="人の確認待ち")
ok = post_saas._state_finish(stale, post_state.STATUS_PUBLISHED, note="古い結末")
check("上書きしない", ok is False, ok)
check("人待ちのまま", W.attempts[op]["status"] == "attention", W.attempts[op]["status"])

# ── 90. 使用済み印が立っていても、記録が未完なら再選択しない ───────────────
print("\n(90) promo_used だけ立っている枠")
reset()
_json.dump({"posts": ["宣伝A", "宣伝B"], "image_url": "img"},
           open(post_saas.PROMO_POOL_FILE, "w"), ensure_ascii=False)
open(post_saas.PROMO_USED_FILE, "w").write("[]")
op = f"{SALON}:{YESTERDAY}:evening"
W.attempts[op] = {"op_id": op, "salon_id": SALON, "jst_date": YESTERDAY, "slot": "evening",
                  "status": "published", "rev": 0, "logged": False, "note": None,
                  "parts": [{"i": 0, "status": "published", "post_id": "P1",
                             "hash": post_state.part_hash("宣伝A"),
                             "original_hash": post_state.part_hash("宣伝A")}],
                  "payload": {"texts": ["宣伝A"], "original_first": "宣伝A", "promo": True,
                              "promo_used": True, "topic_tag": None, "image_url": "img"},
                  "updated_at": (_dt.now(_tz.utc) - _td(days=7)).isoformat()}
got = post_saas.pick_promo(SALON)
check("記録未完の宣伝Aは選ばない", got and got["text"] == "宣伝B", got)

# ── 91. 人待ちへ移す知らせが送れなければ、回収対象に残す ────────────────
print("\n(91) 人待ち移行の通知が失敗")
reset()
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="ACCOUNT_A")
NOTIFY_OK[0] = False
with contextlib.redirect_stdout(io.StringIO()):
    st, dt, kd = post_saas._run_slot(post_state.fetch(op), "resume", SALON_ROW,
                                     "USER1", "TOK", "noon", "@testsalon")
check("投稿しない", len(W.posts) == 0, len(W.posts))
check("人待ちにしない（回収対象に残す）",
      W.attempts[op]["status"] == "hold_repair", W.attempts[op]["status"])
check("回収対象に残る",
      any(r["op_id"] == op for r in post_state.open_issues(salon_ids=[SALON])),
      [r["op_id"] for r in post_state.open_issues(salon_ids=[SALON])])
NOTIFY_OK[0] = True
with contextlib.redirect_stdout(io.StringIO()):
    post_saas._run_slot(post_state.fetch(op), "resume", SALON_ROW, "USER1", "TOK",
                        "noon", "@testsalon")
check("送れたら人待ちにする", W.attempts[op]["status"] == "attention", W.attempts[op]["status"])


# ── 92. 回収で人待ちへ移すのは、まとめ通知が届いてから ──────────────────
print("\n(92) 回収での人待ち移行")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{YESTERDAY}:noon"
a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "noon")
# 2部構成で、1部目だけ公開済み（続きが残っている＝投稿処理に入る枠）
row = post_state.update(row, payload={"texts": TEXTS2, "original_first": TEXTS2[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="ACCOUNT_A", logged=True)   # 別アカウントで始めた枠
post_state.update(post_state.fetch(op), status=post_state.STATUS_PUBLISHED)
post_state.set_part(post_state.fetch(op), 0, hash=post_state.part_hash(TEXTS2[0]),
                    original_hash=post_state.part_hash(TEXTS2[0]),
                    creation_id="C_HM", post_id="P_HM", status=post_state.PART_PUBLISHED)
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
NOTIFY_OK[0] = False
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("届かないうちは人待ちにしない",
      W.attempts[op]["status"] == "hold_repair", W.attempts[op]["status"])
check("回収対象に残る",
      any(r["op_id"] == op for r in post_state.open_issues(salon_ids=[SALON])),
      [r["op_id"] for r in post_state.open_issues(salon_ids=[SALON])])
print("  → LINEが復旧したら知らせて、人待ちへ移す")
NOTIFY_OK[0] = True
NOTIFY.clear()
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("知らせが届く", any("片づけられなかった" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:200])
check("人待ちへ移る", W.attempts[op]["status"] == "attention", W.attempts[op]["status"])
check("以後は回収対象から外れる",
      not any(r["op_id"] == op for r in post_state.open_issues(salon_ids=[SALON])),
      [r["op_id"] for r in post_state.open_issues(salon_ids=[SALON])])


# ── 93. 通知が届いても、記録が戻せるうちは回収対象に残す ────────────────
print("\n(93) 通知後も記録の復旧は続ける（logged=False から）")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{YESTERDAY}:noon"
a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "noon")
# 2部構成・親だけ公開済み・記録は未完・台帳の投稿者が今のアカウントと違う
row = post_state.update(row, payload={"texts": TEXTS2, "original_first": TEXTS2[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="ACCOUNT_A", logged=False)
post_state.set_part(post_state.fetch(op), 0, hash=post_state.part_hash(TEXTS2[0]),
                    original_hash=post_state.part_hash(TEXTS2[0]),
                    creation_id="C_R1", post_id="P_R1", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_PUBLISHED)
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("知らせは届く", any("片づけられなかった" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:160])
check("1部目の記録は戻る", len(W.post_logs) == 1, W.post_logs)
check("記録が戻るまで回収対象から外さない",
      W.attempts[op]["logged"] or any(r["op_id"] == op
                                      for r in post_state.open_issues(salon_ids=[SALON])),
      f'logged={W.attempts[op]["logged"]} status={W.attempts[op]["status"]}')
print("  → 記録がまだ戻せないうちは、通知が届いても人待ちにしない")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{YESTERDAY}:noon"
a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "noon")
row = post_state.update(row, payload={"texts": TEXTS2, "original_first": TEXTS2[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="ACCOUNT_A", logged=False)
post_state.set_part(post_state.fetch(op), 0, hash=post_state.part_hash(TEXTS2[0]),
                    original_hash=post_state.part_hash(TEXTS2[0]),
                    creation_id="C_R2", post_id="P_R2", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_PUBLISHED)
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
W.log_insert_behavior = lambda n: "fail"      # 記録がまだ戻せない
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("記録が戻っていない（前提）", not W.attempts[op]["logged"], W.attempts[op]["logged"])
check("人待ちにしない", W.attempts[op]["status"] == "hold_repair", W.attempts[op]["status"])
check("回収対象に残る",
      any(r["op_id"] == op for r in post_state.open_issues(salon_ids=[SALON])),
      [r["op_id"] for r in post_state.open_issues(salon_ids=[SALON])])

# ── 94. コンテナが消えた枠は、いつまでも回し続けない ────────────────────
print("\n(94) コンテナが EXPIRED のまま")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{YESTERDAY}:noon"
a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "noon")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(post_state.fetch(op), 0, hash=post_state.part_hash(TEXTS1[0]),
                    original_hash=post_state.part_hash(TEXTS1[0]),
                    creation_id="C_EX2", status=post_state.PART_UNKNOWN, lost_response=True)
post_state.update(post_state.fetch(op), status=post_state.STATUS_HOLD_REPAIR, note="未確定")
W.status_override = "EXPIRED"
seen = []
for _ in range(3):
    W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.recover_open_attempts(W.salons)
    seen.append(W.attempts[op]["status"])
W.status_override = None
check("投稿はしない", len(W.posts) == 0, len(W.posts))
check("知らせる", any("片づけられなかった" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:160])
check("最後は人待ちになって回り続けない", seen[-1] == "attention", seen)

# ── 95. 完了の印を残せなければ、成功と言わない ─────────────────────
print("\n(95) 完了印の保存が失敗")
reset()
op = f"{SALON}:{JST_DATE}:noon"
W.publish_behavior = lambda cid, n: "ok"
def block_logged(o, body):
    return "fail" if body.get("status") == "logged" else "ok"
W.attempts_patch_behavior = block_logged
(res, row, _), out = quiet(lambda: run(TEXTS1))
W.attempts_patch_behavior = None
check("投稿は出る", len(W.posts) == 1, len(W.posts))
check("記録も残る", len(W.post_logs) == 1, len(W.post_logs))
check("成功とは言わない", res.get("status") != "ok", res.get("status"))


# ── 96. 通常経路でも、記録が戻せるうちは人待ちにしない ─────────────────
print("\n(96) 通常経路の人待ち判定")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": TEXTS2, "original_first": TEXTS2[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="ACCOUNT_A", logged=False)
post_state.set_part(post_state.fetch(op), 0, hash=post_state.part_hash(TEXTS2[0]),
                    original_hash=post_state.part_hash(TEXTS2[0]),
                    creation_id="C_N1", post_id="P_N1", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_PUBLISHED)
with contextlib.redirect_stdout(io.StringIO()):
    st, dt, kd = post_saas._run_slot(post_state.fetch(op), "resume", SALON_ROW,
                                     "USER1", "TOK", "noon", "@testsalon")   # quiet=False
check("投稿しない", len(W.posts) == 0, len(W.posts))
check("知らせは出る", len(NOTIFY) >= 1, NOTIFY)
check("人待ちにしない（記録が戻せる）",
      W.attempts[op]["status"] == "hold_repair", W.attempts[op]["status"])
print("  → 続けて回収すると記録が戻る")
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("記録が戻る", len(W.post_logs) == 1, W.post_logs)

# ── 97. 本文がそろわない枠は「戻せる」と判定しない ─────────────────
print("\n(97) 記録に必要な本文がそろっていない")
reset()
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": ["いまの本文"], "original_first": "いまの本文",
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(post_state.fetch(op), 0, hash=post_state.part_hash("むかしの本文"),
                    original_hash=post_state.part_hash("むかしの本文"),
                    creation_id="C_N2", post_id="P_N2", status=post_state.PART_PUBLISHED)
check("本文が食い違えば戻せないと判定",
      not post_saas._repairable(post_state.fetch(op)), "戻せると判定した")
print("  → 知らせたあとは人待ちへ移り、回り続けない")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{YESTERDAY}:noon"
a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "noon")
row = post_state.update(row, payload={"texts": ["いまの本文"], "original_first": "いまの本文",
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(post_state.fetch(op), 0, hash=post_state.part_hash("むかしの本文"),
                    original_hash=post_state.part_hash("むかしの本文"),
                    creation_id="C_N3", post_id="P_N3", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_HOLD_REPAIR, note="不一致")
seen = []
for _ in range(3):
    W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.recover_open_attempts(W.salons)
    seen.append(W.attempts[op]["status"])
check("間違った記録はしない", len(W.post_logs) == 0, W.post_logs)
check("最後は人待ちになる", seen[-1] == "attention", seen)

# ── 98. 宣伝の復旧でも、完了印を残せなければ成功にしない ────────────────
print("\n(98) 宣伝の復旧＋完了印の失敗")
reset()
post_saas.SLOT = "noon"
post_saas.SALON_FILTER = ""
post_saas.DRY_RUN = False
post_saas.SLOT_JST_WINDOWS = {}
post_saas.is_promo_time = lambda name, slot: False
W.salons = [dict(SALON_ROW)]
op = f"{SALON}:{JST_DATE}:noon"
open(post_saas.PROMO_USED_FILE, "w").write("[]")
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": ["宣伝V"], "original_first": "宣伝V",
                                      "topic_tag": None, "image_url": "i", "promo": True},
                        publisher_user_id="USER1", logged=True)
post_state.set_part(post_state.fetch(op), 0, hash=post_state.part_hash("宣伝V"),
                    original_hash=post_state.part_hash("宣伝V"),
                    creation_id="C_PV", post_id="P_PV", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_PUBLISHED)
def block_logged2(o, body):
    return "fail" if body.get("status") == "logged" else "ok"
W.attempts_patch_behavior = block_logged2
code = None
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit as e:
    code = e.code
W.attempts_patch_behavior = None
check("成功として終わらない", code == 1, code)
check("完了になっていない", W.attempts[op]["status"] != "logged", W.attempts[op]["status"])


# ── 99. コンテナ照会の403も「連携切れ」として知らせる ────────────────
print("\n(99) 状態照会が403")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(post_state.fetch(op), 0, hash=post_state.part_hash(TEXTS1[0]),
                    original_hash=post_state.part_hash(TEXTS1[0]),
                    creation_id="C_403", status=post_state.PART_UNKNOWN, lost_response=True)
post_state.update(post_state.fetch(op), status=post_state.STATUS_HOLD_REPAIR, note="未確定")
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
W.status_override = "__403__"
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
W.status_override = None
check("再連携が必要だと分かる知らせ",
      any("連携が切れて" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:250])
check("記録しない", len(W.post_logs) == 0, len(W.post_logs))

# ── 100. 公開後に台帳が書けなくても、次の実行で記録が戻る ──────────────
print("\n(100) 公開→台帳保存失敗→回収で記録が戻る")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{JST_DATE}:noon"
W.publish_behavior = lambda cid, n: "ok"
def fail_pub_patch(o, body):
    parts = (body or {}).get("parts")
    if parts and any(p.get("status") == "published" for p in parts):
        return "fail"
    return "ok"
W.attempts_patch_behavior = fail_pub_patch
(res, row, _), out = quiet(lambda: run(TEXTS1))
W.attempts_patch_behavior = None
check("記録の修復が続く状態", W.attempts[op]["status"] == "hold_repair", W.attempts[op]["status"])
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("投稿は増えない", len(W.posts) == 1, len(W.posts))
check("記録が戻る", len(W.post_logs) == 1, W.post_logs)


# ── 101. 本文一覧が欠けていても、原文が一致するなら記録は戻す ──────────────
print("\n(101) texts 欠損でも記録は戻す")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{YESTERDAY}:noon"
a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "noon")
row = post_state.update(row, payload={"original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")     # texts が無い
post_state.set_part(post_state.fetch(op), 0, hash=post_state.part_hash(TEXTS1[0]),
                    original_hash=post_state.part_hash(TEXTS1[0]),
                    creation_id="C_TX", post_id="P_TX", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_PUBLISHED, logged=False)
check("戻せると判定する", post_saas._repairable(post_state.fetch(op)), "戻せないと判定した")
W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("記録が戻る", len(W.post_logs) == 1, W.post_logs)
check("投稿はしない", len(W.posts) == 0, len(W.posts))

# ── 102. 本文ハッシュ・原文ハッシュは、それぞれ単独で効く ────────────────
print("\n(102) ハッシュ確認の効き目")
for label, part_hash_text, orig_hash_text in (
        ("本文ハッシュだけ不一致", "ちがう本文", TEXTS1[0]),
        ("原文ハッシュだけ不一致", TEXTS1[0], "ちがう原文")):
    reset()
    W.salons = [SALON_ROW]
    op = f"{SALON}:{YESTERDAY}:noon"
    a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "noon")
    row = post_state.update(row, payload={"texts": [TEXTS1[0]], "original_first": TEXTS1[0],
                                          "topic_tag": None, "image_url": "", "promo": False},
                            publisher_user_id="USER1")
    post_state.set_part(post_state.fetch(op), 0,
                        hash=post_state.part_hash(part_hash_text),
                        original_hash=post_state.part_hash(orig_hash_text),
                        creation_id="C_HH", post_id="P_HH",
                        status=post_state.PART_PUBLISHED)
    post_state.update(post_state.fetch(op), status=post_state.STATUS_HOLD_REPAIR, note="不一致")
    check(f"{label}：戻せないと判定", not post_saas._repairable(post_state.fetch(op)),
          "戻せると判定した")
    seen = []
    for _ in range(2):
        W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
        with contextlib.redirect_stdout(io.StringIO()):
            post_saas.recover_open_attempts(W.salons)
        seen.append(W.attempts[op]["status"])
    check(f"{label}：記録しない", len(W.post_logs) == 0, W.post_logs)
    check(f"{label}：人待ちへ移って回り続けない", seen[-1] == "attention", seen)

# ── 103. 記録復旧の追記で、通知済みの印を壊さない ──────────────────
print("\n(103) メモの追記と通知済みの印")
reset()
note = "なにかの理由" + post_saas.RECOVER_NOTE_MARK + "hold"
check("印より前に足す",
      post_saas._append_note(note, "／記録は復旧済み").endswith(post_saas.RECOVER_NOTE_MARK + "hold"),
      post_saas._append_note(note, "／記録は復旧済み"))
check("種類が読み取れる",
      post_saas._notified_kinds({"note": post_saas._append_note(note, "／記録は復旧済み")}) == {"hold"},
      post_saas._notified_kinds({"note": post_saas._append_note(note, "／記録は復旧済み")}))


# ── 104. 知らせ済み＋復旧済みの枠は、回収枠を使い続けない ────────────────
print("\n(104) 復旧後に回収から外れる")
reset()
W.salons = [SALON_ROW]
op = f"{SALON}:{YESTERDAY}:noon"
a, row = post_saas._acquire_with_retry(SALON, YESTERDAY, "noon")
# 2部構成・親だけ公開済み・記録未完（＝知らせが要る／記録は戻せる）
row = post_state.update(row, payload={"texts": TEXTS2, "original_first": TEXTS2[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1", logged=False)
post_state.set_part(post_state.fetch(op), 0, hash=post_state.part_hash(TEXTS2[0]),
                    original_hash=post_state.part_hash(TEXTS2[0]),
                    creation_id="C_ST", post_id="P_ST", status=post_state.PART_PUBLISHED)
post_state.update(post_state.fetch(op), status=post_state.STATUS_HOLD_REPAIR, note="続きが出せない")
W.log_insert_behavior = lambda n: "fail"    # 1回目は記録を戻せない（＝知らせるが片づかない）
states, notified = [], []
for i in range(4):
    NOTIFY.clear()
    if i == 1:
        W.log_insert_behavior = lambda n: "ok"   # 2回目から記録が戻せるようになる
    W.attempts[op]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.recover_open_attempts(W.salons)
    states.append(W.attempts[op]["status"])
    notified.append(len(NOTIFY))
check("1回目で知らせる", notified[0] >= 1, notified)
check("記録は戻る", len(W.post_logs) == 1, W.post_logs)
check("片づいたあとは知らせない", sum(notified[2:]) == 0, notified)
check("2回目で片づく（回り続けない）", states[1] == "attention", states)
check("最後は人待ちになって回収から外れる", states[-1] == "attention", states)
check("回収対象から消える",
      not any(r["op_id"] == op for r in post_state.open_issues(salon_ids=[SALON])),
      [r["op_id"] for r in post_state.open_issues(salon_ids=[SALON])])


# ── 105. 1つ前のスロットの取りこぼしを埋める ─────────────────────
print("\n(105) 前のスロットの穴埋め")
reset()
post_saas.check_previous_slot = _real_check_prev      # ここだけ本物を使う
post_saas.SLOT = "noon"
post_saas.SALON_FILTER = ""
post_saas.DRY_RUN = False
post_saas.SLOT_JST_WINDOWS = {}
post_saas.is_promo_time = lambda name, slot: False
post_saas._sync_last_run = _record_sync_write and (lambda *a, **k: None)
W.salons = [dict(SALON_ROW)]
texts_by_slot = {"morning": ["朝の本文"], "noon": ["昼の本文"]}
post_saas.pick_post = lambda name, slot, used, **kw: list(texts_by_slot[slot])
W.publish_behavior = lambda cid, n: "ok"
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit:
    pass
out = [p["text"] for p in W.posts]
check("抜けていた朝の分を出す", "朝の本文" in out, out)
check("今回の昼も出す", "昼の本文" in out, out)
check("知らせる", any("出ていない投稿がありました" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:160])
print("  → もう一度回しても増えない")
before = len(W.posts)
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit:
    pass
check("増えない", len(W.posts) == before, [p["text"] for p in W.posts])
post_saas.check_previous_slot = lambda salons: 0


# ── 106. 時間帯を過ぎた実行は「その日の穴埋め」として通す ────────────────
print("\n(106) 時間帯ガードの向き")
reset()
post_saas.SLOT = "noon"
post_saas.SALON_FILTER = ""
post_saas.DRY_RUN = False
post_saas.is_promo_time = lambda name, slot: False
post_saas.pick_post = lambda name, slot, used, **kw: ["昼の本文"]
W.salons = [dict(SALON_ROW)]
W.publish_behavior = lambda cid, n: "ok"
post_saas.SLOT_JST_WINDOWS = {"noon": range(11, 17)}
_now = post_saas.datetime


class _FakeNow:
    """JSTの現在時刻だけ差し替える（時間帯ガードの向きを確かめるため）"""
    def __init__(self, hour):
        self.hour = hour

    def now(self, tz=None):
        real = _now.now(tz)
        return real.replace(hour=self.hour)

    def __getattr__(self, name):
        return getattr(_now, name)


post_saas.datetime = _FakeNow(17)      # 昼の時間帯を過ぎている
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit:
    pass
check("時間帯を過ぎていても穴を埋める", len(W.posts) == 1, [p["text"] for p in W.posts])
print("  → まだ時間前なら先出ししない")
reset()
W.salons = [dict(SALON_ROW)]
W.publish_behavior = lambda cid, n: "ok"
post_saas.datetime = _FakeNow(9)       # 昼の時間前
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit:
    pass
check("時間前は出さない", len(W.posts) == 0, [p["text"] for p in W.posts])
post_saas.datetime = _now
post_saas.SLOT_JST_WINDOWS = {}


# ── 107. 朝の実行が「昨夜の取りこぼし」を知らせる ─────────────────────
print("\n(107) 昨夜の取りこぼし")
reset()
post_saas.check_previous_slot = _real_check_prev
post_saas.SLOT = "morning"
post_saas.SALON_FILTER = ""
post_saas.DRY_RUN = False
post_saas.SLOT_JST_WINDOWS = {}
post_saas.is_promo_time = lambda name, slot: False
post_saas.pick_post = lambda name, slot, used, **kw: ["朝の本文"]
W.salons = [dict(SALON_ROW)]
W.publish_behavior = lambda cid, n: "ok"
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit:
    pass
check("昨夜の抜けを知らせる", any("日をまたいだ未投稿" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:200])
check("昨夜の分は勝手に出さない", [p["text"] for p in W.posts] == ["朝の本文"],
      [p["text"] for p in W.posts])
post_saas.check_previous_slot = lambda salons: 0


# ── 108. 実行中に日付が変わったら、新しい投稿はしない ───────────────────
print("\n(108) 日付を跨いだ実行")
reset()
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_saas._run_jst_date = "2026-01-01"      # 実行開始時の日付が今日と違う＝日付が変わった状態
W.publish_behavior = lambda cid, n: "ok"
with contextlib.redirect_stdout(io.StringIO()):
    res = post_saas.threads_post(post_state.fetch(op), "USER1", "TOK", TEXTS1,
                                 original_first=TEXTS1[0])
post_saas._run_jst_date = None
check("投稿しない", len(W.posts) == 0, len(W.posts))
check("コンテナも作らない", W.calls["create"] == 0, W.calls["create"])
check("未完として残す", not res["complete"], res["note"])

# ── 109. 穴埋めは本来の枠のあと・専用の持ち時間で ─────────────────────
print("\n(109) 穴埋めの順番と持ち時間")
reset()
post_saas.check_previous_slot = _real_check_prev
post_saas.SLOT = "noon"
post_saas.SALON_FILTER = ""
post_saas.DRY_RUN = False
post_saas.SLOT_JST_WINDOWS = {}
post_saas.is_promo_time = lambda name, slot: False
post_saas.GAPFILL_BUDGET_SEC = 30
W.salons = [dict(SALON_ROW, id=f"{i}1111111-1111-1111-1111-111111111111",
                 salon_name=f"サロン{i}") for i in range(1, 4)]
texts_by_slot = {"morning": ["朝の本文"], "noon": ["昼の本文"]}
post_saas.pick_post = lambda name, slot, used, **kw: list(texts_by_slot[slot])
W.publish_behavior = lambda cid, n: "ok"
W.latency = 6          # 通信1回6秒（穴埋めの持ち時間をすぐ使い切る）
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit:
    pass
W.latency = 0
post_saas.GAPFILL_BUDGET_SEC = 150
noon_posts = [p for p in W.posts if p["text"] == "昼の本文"]
check("本来の昼枠は3件とも出る", len(noon_posts) == 3, [p["text"] for p in W.posts])
post_saas.check_previous_slot = lambda salons: 0


# ── 110. 昨夜の抜けは、通知が届かなくても次に持ち越す ────────────────────
print("\n(110) 昨夜の抜けの持ち越し")
reset()
post_saas.check_previous_slot = _real_check_prev
post_saas.SLOT = "morning"
post_saas.SALON_FILTER = ""
post_saas.DRY_RUN = False
post_saas.SLOT_JST_WINDOWS = {}
post_saas.is_promo_time = lambda name, slot: False
post_saas.pick_post = lambda name, slot, used, **kw: ["朝の本文"]
W.salons = [dict(SALON_ROW)]
W.publish_behavior = lambda cid, n: "ok"
seed_history(days=3)
W.post_logs[:] = [l for l in W.post_logs
                  if not l["op_id"].endswith(f"{YESTERDAY}:evening")]   # 昨夜だけ抜けた状態
NOTIFY_OK[0] = False       # LINEが送れない
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit:
    pass
yop = f"{SALON}:{YESTERDAY}:evening"
check("台帳に印が残る", yop in W.attempts, list(W.attempts))
check("回収対象に入る",
      any(r["op_id"] == yop for r in post_state.open_issues(salon_ids=[SALON])),
      [r["op_id"] for r in post_state.open_issues(salon_ids=[SALON])])
print("  → LINEが復旧したら知らせが届く")
NOTIFY_OK[0] = True
NOTIFY.clear()
W.attempts[yop]["updated_at"] = (_dt.now(_tz.utc) - _td(hours=3)).isoformat()
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.recover_open_attempts(W.salons)
check("知らせが届く", len(NOTIFY) >= 1, NOTIFY)
check("勝手に投稿しない", not any(p["text"] == "朝の本文" and i > 0
                                  for i, p in enumerate(W.posts)), len(W.posts))
post_saas.check_previous_slot = lambda salons: 0

# ── 111. 確認できなかったサロンを「抜けなし」と言わない ──────────────────
print("\n(111) 取りこぼしの確認ができない")
reset()
post_saas.check_previous_slot = _real_check_prev
post_saas.SLOT = "noon"
W.salons = [dict(SALON_ROW)]
orig_scan = post_saas._scan_gaps
post_saas._scan_gaps = lambda *a, **k: (_ for _ in ()).throw(TimeoutError("timed out"))
raised = None
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.check_previous_slot(W.salons)
except Exception as e:
    raised = type(e).__name__
post_saas._scan_gaps = orig_scan
check("確認できなかったと知らせる", any("調べられませんでした" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:200])
check("失敗として返す（成功で終わらせない）", raised is not None, raised)
post_saas.check_previous_slot = lambda salons: 0


# ── 112. 穴埋めでも、登録アカウントと実物が違えば投稿しない ────────────────
print("\n(112) 穴埋め時のアカウント不一致")
reset()
post_saas.check_previous_slot = _real_check_prev
post_saas.SLOT = "noon"
post_saas.SALON_FILTER = ""
post_saas.DRY_RUN = False
post_saas.SLOT_JST_WINDOWS = {}
post_saas.is_promo_time = lambda name, slot: False
post_saas.pick_post = lambda name, slot, used, **kw: ["朝の本文"]
W.salons = [dict(SALON_ROW, threads_user_id="BETSU_NO_ID")]
W.publish_behavior = lambda cid, n: "ok"
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.check_previous_slot(W.salons)
check("穴埋めでも投稿しない", len(W.posts) == 0, [p["text"] for p in W.posts])
check("理由を知らせる", any("アカウント不一致" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:200])
post_saas.check_previous_slot = lambda salons: 0


# ── 113. 夜の実行が、朝と昼の両方の取りこぼしを見る ───────────────────
print("\n(113) 未解決の過去枠をまとめて見る")
reset()
post_saas.check_previous_slot = _real_check_prev
post_saas.SLOT = "evening"
post_saas.SALON_FILTER = ""
post_saas.DRY_RUN = False
post_saas.SLOT_JST_WINDOWS = {}
post_saas.is_promo_time = lambda name, slot: False
W.salons = [dict(SALON_ROW)]
by_slot = {"morning": ["朝の本文"], "noon": ["昼の本文"], "evening": ["夜の本文"]}
post_saas.pick_post = lambda name, slot, used, **kw: list(by_slot[slot])
W.publish_behavior = lambda cid, n: "ok"
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit:
    pass
out = sorted(p["text"] for p in W.posts)
check("夜・昼・朝の3つとも出る", out == ["夜の本文", "昼の本文", "朝の本文"] or
      set(out) == {"朝の本文", "昼の本文", "夜の本文"}, out)
post_saas.check_previous_slot = lambda salons: 0

# ── 114. 昨夜の枠が failed で残っていても、知らせる対象にする ───────────────
print("\n(114) 既存の failed 行")
reset()
W.salons = [SALON_ROW]
yop = f"{SALON}:{YESTERDAY}:evening"
W.attempts[yop] = {"op_id": yop, "salon_id": SALON, "jst_date": YESTERDAY, "slot": "evening",
                   "status": "failed", "parts": [], "rev": 0, "payload": None,
                   "note": "未公開のまま期限切れ", "logged": False,
                   "updated_at": (_dt.now(_tz.utc) - _td(hours=10)).isoformat()}
post_saas.check_previous_slot = _real_check_prev
post_saas.SLOT = "morning"
NOTIFY_OK[0] = False
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.check_previous_slot(W.salons)
check("知らせる対象に移す", W.attempts[yop]["status"] == "hold_repair",
      W.attempts[yop]["status"])
check("回収対象に入る",
      any(r["op_id"] == yop for r in post_state.open_issues(salon_ids=[SALON])),
      [r["op_id"] for r in post_state.open_issues(salon_ids=[SALON])])
NOTIFY_OK[0] = True
post_saas.check_previous_slot = lambda salons: 0

# ── 115. 台帳にも残せなかったら、成功として終わらせない ──────────────────
print("\n(115) 台帳にも残せない")
reset()
W.salons = [SALON_ROW]
post_saas.check_previous_slot = _real_check_prev
post_saas.SLOT = "morning"
seed_history(days=3)
W.post_logs[:] = [l for l in W.post_logs
                  if not l["op_id"].endswith(f"{YESTERDAY}:evening")]   # 昨夜だけ抜けた
orig_flag = post_saas._flag_missing
post_saas._flag_missing = lambda *a, **k: False        # 台帳にも書けない
raised = None
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.check_previous_slot(W.salons)
except Exception as e:
    raised = f"{type(e).__name__}"
post_saas._flag_missing = orig_flag
check("失敗として返す", raised is not None, raised)
check("台帳にも残せないことを知らせる",
      any("台帳にも残せませんでした" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:220])
post_saas.check_previous_slot = lambda salons: 0

# ── 116. 実行中に日付が変わったら、翌日の枠を作らない ───────────────────
print("\n(116) 日付跨ぎで翌日枠を作らない")
reset()
post_saas.SLOT = "evening"
post_saas.SALON_FILTER = ""
post_saas.DRY_RUN = False
post_saas.SLOT_JST_WINDOWS = {}
post_saas.is_promo_time = lambda name, slot: False
post_saas.pick_post = lambda name, slot, used, **kw: ["夜の本文"]
W.salons = [dict(SALON_ROW)]
W.publish_behavior = lambda cid, n: "ok"
_orig_dt = post_saas.datetime


class _Rolling:
    """main() が始まったあとで日付が変わる状況を作る"""
    def __init__(self):
        self.calls = 0

    def now(self, tz=None):
        self.calls += 1
        d = _orig_dt.now(tz)
        return d if self.calls <= 1 else d + _td(days=1)

    def __getattr__(self, name):
        return getattr(_orig_dt, name)


post_saas.datetime = _Rolling()
_orig_rolled = post_saas._date_rolled_over
post_saas._date_rolled_over = lambda: False   # ← ループ離脱を外し、対象日の固定だけを検査する
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit:
    pass
post_saas._date_rolled_over = _orig_rolled
post_saas.datetime = _orig_dt
tomorrow = (_dt.now(_tz(_td(hours=9))) + _td(days=1)).strftime("%Y-%m-%d")
check("翌日の台帳を作らない", not any(tomorrow in k for k in W.attempts), list(W.attempts))
print("  → ループ離脱の方も単独で効く")
reset()
W.salons = [dict(SALON_ROW)]
W.publish_behavior = lambda cid, n: "ok"
post_saas.datetime = _Rolling()
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit:
    pass
post_saas.datetime = _orig_dt
check("投稿しない", len(W.posts) == 0, [p["text"] for p in W.posts])
check("知らせる", any("日付が変わった" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:150])


# ── 117. 取りこぼしの点検は、まとめて1回ずつの問い合わせで済ませる ────────────
print("\n(117) 点検の重さ")
reset()
W.salons = [dict(SALON_ROW, id=f"{i:02d}111111-1111-1111-1111-111111111111",
                 salon_name=f"サロン{i}") for i in range(1, 51)]   # 50サロン
post_saas.SLOT = "evening"
seed_history(W.salons, days=3)
W.latency = 1          # 通信1回につき1秒
before = CLOCK.slept
with contextlib.redirect_stdout(io.StringIO()):
    gaps = post_saas._scan_gaps(W.salons, 2)
spent = CLOCK.slept - before
W.latency = 0
check("50サロンでも問い合わせは数回で済む", spent <= 5, f"{spent}秒（通信{spent}回相当）")
check("抜けが無ければ0件", gaps == [], gaps[:3])

# ── 118. 点検・穴埋め全体が持ち時間を守る ────────────────────────
print("\n(118) 点検の持ち時間")
reset()
post_saas.check_previous_slot = _real_check_prev
post_saas.SLOT = "evening"
post_saas.GAPFILL_BUDGET_SEC = 30
W.salons = [dict(SALON_ROW, id=f"{i:02d}111111-1111-1111-1111-111111111111",
                 salon_name=f"サロン{i}") for i in range(1, 11)]
post_saas.is_promo_time = lambda name, slot: False
post_saas.pick_post = lambda name, slot, used, **kw: ["穴埋め本文"]
W.publish_behavior = lambda cid, n: "ok"
W.latency = 4
before = CLOCK.slept
with contextlib.redirect_stdout(io.StringIO()):
    try:
        post_saas.check_previous_slot(W.salons)
    except Exception:
        pass          # 持ち時間切れで台帳に残せないのは想定内（通知は出る）
spent = CLOCK.slept - before
W.latency = 0
post_saas.GAPFILL_BUDGET_SEC = 150
# 締切の確認はサロン単位なので、最後の1件ぶん（通信数回）は超える。
# ジョブ全体10分に対して十分小さいことを見る
check("持ち時間＋1サロンぶんに収まる", spent <= 30 + 40, f"{spent}秒")
check("締切は元に戻る", post_saas._deadline is None, post_saas._deadline)
post_saas.check_previous_slot = lambda salons: 0


# ── 119. 旧形式のログしか無くても、出ている枠は「抜け」と数えない ─────────────
print("\n(119) 旧形式ログの突き合わせ")
reset()
post_saas.SLOT = "evening"
S1 = dict(SALON_ROW)
W.salons = [S1]
from datetime import datetime as _d9, timezone as _z9, timedelta as _t9
JSTz9 = _z9(_t9(hours=9))
for n in (0, 1):
    day = _d9.now(JSTz9) - _t9(days=n)
    for slot, h in (("morning", 7), ("noon", 12), ("evening", 21)):
        if n == 0 and slot == "evening":
            continue
        W.post_logs.append({"salon_id": SALON, "slot": slot, "post_content": "旧",
                            "posted_at": day.replace(hour=h).astimezone(_z9.utc).isoformat(),
                            "op_id": None})        # ← op_id の無い旧形式
with contextlib.redirect_stdout(io.StringIO()):
    gaps = post_saas._scan_gaps(W.salons, 2)
check("旧形式でも出ていれば抜けにしない", gaps == [], [(g[1], g[2]) for g in gaps])

# ── 120. 稼働開始前の日を「未投稿」と数えない ─────────────────────
print("\n(120) 稼働開始前")
reset()
post_saas.SLOT = "evening"
today9 = _d9.now(JSTz9)
S2 = dict(SALON_ROW, created_at=today9.replace(hour=11).astimezone(_z9.utc).isoformat())
W.salons = [S2]
with contextlib.redirect_stdout(io.StringIO()):
    gaps = post_saas._scan_gaps(W.salons, 2)
days = {g[1] for g in gaps}
check("昨日は対象外", (today9 - _t9(days=1)).strftime("%Y-%m-%d") not in days, sorted(days))

# ── 121. 件数が多すぎて全部を確認できないときは「抜け」と決めない ──────────────
print("\n(121) ログが取り切れない")
reset()
post_saas.SLOT = "evening"
W.salons = [dict(SALON_ROW)]
orig_get = post_saas.supabase_get
def endless(path, params=None):
    if path == "post_logs":
        return [{"salon_id": SALON, "slot": "morning",
                 "posted_at": _d9.now(_z9.utc).isoformat(), "op_id": None}] * 1000
    return orig_get(path, params)
post_saas.supabase_get = endless
raised = None
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas._scan_gaps(W.salons, 2)
except Exception as e:
    raised = type(e).__name__
post_saas.supabase_get = orig_get
check("取り切れなければ止める", raised is not None, raised)

# ── 122. 点検が終わらなかった日は、次の実行で遡って見る ────────────────
print("\n(122) 点検日の持ち越し")
reset()
import tempfile as _tf2
post_saas.GAP_MARK_FILE = os.path.join(_tf2.mkdtemp(), "gap_checked.json")
post_saas._run_jst_date = JST_DATE
# 記録ファイルは実行のたびに消えるので、無いときは最大日数まで見る
check("記録が無ければ最大日数まで見る",
      post_saas._gap_days_to_scan() == post_saas.GAP_SCAN_MAX_DAYS,
      post_saas._gap_days_to_scan())
post_saas._mark_gap_checked((_d9.strptime(JST_DATE, "%Y-%m-%d") - _t9(days=1)).strftime("%Y-%m-%d"))
check("直近まで終わっていれば短くて済む",
      post_saas._gap_days_to_scan() <= post_saas.GAP_SCAN_MAX_DAYS,
      post_saas._gap_days_to_scan())
post_saas._run_jst_date = None

# ── 123. 点検・記録に失敗したら、ジョブも失敗にする ───────────────────
print("\n(123) 点検失敗はジョブの失敗")
reset()
post_saas.check_previous_slot = lambda salons: (_ for _ in ()).throw(RuntimeError("台帳に残せない"))
post_saas.SLOT = "noon"
post_saas.SALON_FILTER = ""
post_saas.DRY_RUN = False
post_saas.SLOT_JST_WINDOWS = {}
post_saas.is_promo_time = lambda name, slot: False
post_saas.pick_post = lambda name, slot, used, **kw: ["昼の本文"]
W.salons = [dict(SALON_ROW)]
W.publish_behavior = lambda cid, n: "ok"
code = None
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas.main()
except SystemExit as e:
    code = e.code
check("投稿はできている", len(W.posts) == 1, len(W.posts))
check("それでも失敗として終わる", code == 1, code)
check("知らせる", any("取りこぼし" in m for m in NOTIFY),
      json.dumps(NOTIFY, ensure_ascii=False)[:160])
post_saas.check_previous_slot = lambda salons: 0


# ── 124. まとめ取得が取りこぼしても、埋める直前の再確認で二重投稿しない ─────────
print("\n(124) まとめ取得の取りこぼし")
reset()
post_saas.check_previous_slot = _real_check_prev
post_saas.SLOT = "noon"
W.salons = [dict(SALON_ROW)]
seed_history(days=2)
# 今日の朝は実際には出ているのに、まとめ取得だけがそれを返さない状況を作る
orig_scan_get = post_saas.supabase_get
def hide_today_morning(path, params=None):
    rows = orig_scan_get(path, params)
    if path == "post_logs" and (params or {}).get("select", "").startswith("salon_id"):
        return [r for r in rows if not str(r.get("op_id", "")).endswith(":morning")]
    return rows
post_saas.supabase_get = hide_today_morning
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.check_previous_slot(W.salons)
post_saas.supabase_get = orig_scan_get
check("再確認して出し直さない", len(W.posts) == 0, [p["text"] for p in W.posts])
post_saas.check_previous_slot = lambda salons: 0


# ── 125. 状態が巻き戻った台帳から、入口経由でも出し直さない ─────────────────
print("\n(125) 壊れた台帳からの入口")
reset()
op = f"{SALON}:{JST_DATE}:noon"
W.attempts[op] = {"op_id": op, "salon_id": SALON, "jst_date": JST_DATE, "slot": "noon",
                  "status": "running", "rev": 0, "logged": False, "payload": None,
                  "note": None,
                  "parts": [{"i": 0, "status": "pending", "creation_id": "C_DIRTY",
                             "post_id": "P_DIRTY", "lost_response": True,
                             "hash": post_state.part_hash("むかしの本文")}],
                  "updated_at": (_dt.now(_tz.utc) - _td(hours=3)).isoformat()}
post_saas.pick_post = lambda name, slot, used, **kw: ["あたらしい本文"]
post_saas.is_promo_time = lambda name, slot: False
post_saas.get_used_posts = lambda sid, slot=None: set()
post_saas._maybe_add_instagram_cta_saas = lambda t, u: t
post_saas._enforce_threads_limit = lambda t: t
post_saas._select_topic = lambda t, n: None
W.publish_behavior = lambda cid, n: "ok"
action, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
check("『まだ何もしていない』とは扱わない", action != "go", action)
if action not in ("hold", "skip"):
    with contextlib.redirect_stdout(io.StringIO()):
        st, dt, kd = post_saas._run_slot(row, action, SALON_ROW, "USER1", "TOK",
                                         "noon", "@testsalon")
check("出し直さない", len(W.posts) == 0, [p["text"] for p in W.posts])
check("公開の記録を消さない",
      (post_state.get_part(post_state.fetch(op), 0) or {}).get("post_id") == "P_DIRTY",
      post_state.get_part(post_state.fetch(op), 0))

# ── 126. 2ページ目にしかない実績も「出ている」と数える ────────────────
print("\n(126) ページ送りの取り切り")
reset()
post_saas.SLOT = "evening"
W.salons = [dict(SALON_ROW)]
seed_history(days=2)
# 1ページ目を埋める古いログを1000件入れて、実績を2ページ目へ押し出す
from datetime import datetime as _dA, timezone as _zA, timedelta as _tA
oldest = _dA.now(_zA.utc) - _tA(days=1, hours=20)
for i in range(1000):
    W.post_logs.insert(0, {"salon_id": SALON, "slot": "noon", "post_content": "古い",
                           "posted_at": (oldest + _tA(seconds=i)).isoformat(),
                           "op_id": f"{SALON}:pad{i}"})
with contextlib.redirect_stdout(io.StringIO()):
    gaps = post_saas._scan_gaps(W.salons, 2)
check("2ページ目の実績も数える", gaps == [], [(g[1], g[2]) for g in gaps])

# ── 127. 当日枠の再確認が失敗したら、点検済みにしない ───────────────────
print("\n(127) 再確認が失敗")
reset()
post_saas.check_previous_slot = _real_check_prev
post_saas.SLOT = "noon"
W.salons = [dict(SALON_ROW)]
seed_history(days=2, include_today_earlier=False)
import tempfile as _tf3
post_saas.GAP_MARK_FILE = os.path.join(_tf3.mkdtemp(), "gap_checked.json")
orig_apt2 = post_saas.already_posted_today
post_saas.already_posted_today = lambda *a, **k: (_ for _ in ()).throw(TimeoutError("timed out"))
with contextlib.redirect_stdout(io.StringIO()):
    try:
        post_saas.check_previous_slot(W.salons)
    except Exception:
        pass
post_saas.already_posted_today = orig_apt2
check("投稿しない", len(W.posts) == 0, [p["text"] for p in W.posts])
check("点検済みにしない", not os.path.exists(post_saas.GAP_MARK_FILE),
      open(post_saas.GAP_MARK_FILE).read() if os.path.exists(post_saas.GAP_MARK_FILE) else "")
check("投稿を止める印は作らない（一時障害でその日を止めない）",
      not any(k.endswith(f"{JST_DATE}:morning") for k in W.attempts), list(W.attempts))
print("  → 通信が戻れば、その日のうちに埋められる")
W.publish_behavior = lambda cid, n: "ok"
post_saas.is_promo_time = lambda name, slot: False
post_saas.pick_post = lambda name, slot, used, **kw: ["朝の本文"]
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.check_previous_slot(W.salons)
check("復旧後に埋められる", any(p["text"] == "朝の本文" for p in W.posts),
      [p["text"] for p in W.posts])
post_saas.check_previous_slot = lambda salons: 0


# ── 128. 公開が不明なのに確認先が無い枠は、新しく作らない ────────────────
print("\n(128) 確認先(コンテナ)が消えた未確定")
reset()
op = f"{SALON}:{JST_DATE}:noon"
a, row = post_saas._acquire_with_retry(SALON, JST_DATE, "noon")
row = post_state.update(row, payload={"texts": TEXTS1, "original_first": TEXTS1[0],
                                      "topic_tag": None, "image_url": "", "promo": False},
                        publisher_user_id="USER1")
post_state.set_part(post_state.fetch(op), 0, hash=post_state.part_hash(TEXTS1[0]),
                    original_hash=post_state.part_hash(TEXTS1[0]),
                    status=post_state.PART_UNKNOWN, lost_response=True)   # creation_id なし
W.publish_behavior = lambda cid, n: "ok"
with contextlib.redirect_stdout(io.StringIO()):
    res = post_saas.threads_post(post_state.fetch(op), "USER1", "TOK", TEXTS1,
                                 original_first=TEXTS1[0])
check("新しく作らない", W.calls["create"] == 0, W.calls["create"])
check("投稿しない", len(W.posts) == 0, len(W.posts))
check("人へ渡す", res["slot_status"] == "attention", res["slot_status"])

# ── 129. 締切を過ぎていたら、点検の通信を始めない ────────────────────
print("\n(129) 点検の締切")
reset()
W.salons = [dict(SALON_ROW)]
post_saas._deadline = CLOCK.time() - 1        # すでに締切超過
before = W.calls.get("log_get", 0)
raised = None
try:
    with contextlib.redirect_stdout(io.StringIO()):
        post_saas._scan_gaps(W.salons, 2)
except Exception as e:
    raised = type(e).__name__
post_saas._deadline = None
check("止まる", raised is not None, raised)
check("通信を1回も始めない", W.calls.get("log_get", 0) == before,
      f'{W.calls.get("log_get", 0) - before}回')


# ── 130. Instagram誘導のリンクが壊れない ────────────────────────
print("\n(130) Instagram誘導のリンク")
reset()
cases = [
    ("https://www.instagram.com/nico.lymph/?r=nametag", "nico.lymph"),
    ("https://www.instagram.com/familie_suita_hiromi?igsh=abc&utm_source=qr",
     "familie_suita_hiromi"),
    ("https://www.instagram.com/tubamenosu_princess", "tubamenosu_princess"),
    ("https://instagram.com/aya_kuroki_0929/", "aya_kuroki_0929"),
    ("@handle_only", "handle_only"),
    ("https://www.instagram.com/", None),      # ユーザー名が無い
    ("", None),
]
ok = all(post_saas._instagram_handle(u) == want for u, want in cases)
check("登録の書き方がどれでも正しく取れる", ok,
      [(u, post_saas._instagram_handle(u), want) for u, want in cases
       if post_saas._instagram_handle(u) != want])
# 取れないときは誘導を付けない
orig_rand = post_saas.random.random
post_saas.random.random = lambda: 0.0          # 必ず付ける確率にする
post_saas._maybe_add_instagram_cta_saas = _real_ig_cta      # 本物を使う
out = post_saas._maybe_add_instagram_cta_saas(["本文"], "https://www.instagram.com/")
check("取れないときは誘導を付けない", out == ["本文"], out)
check("知らせる", any("Instagram誘導のリンクを作れません" in m for m in NOTIFY), NOTIFY)
out2 = post_saas._maybe_add_instagram_cta_saas(["本文"],
                                               "https://www.instagram.com/nico.lymph/?r=nametag")
post_saas.random.random = orig_rand
check("正しいリンクが入る", "instagram.com/nico.lymph" in out2[0], out2)
check("余計なパラメータが入らない", "?r=nametag" not in out2[0], out2)
check("確かめていない中身を断定しない",
      not any(w in out2[0] for w in ("BeforeAfter", "お客様の声", "施術写真")), out2)

# ── 131. 「このサロンを選ぶ判断材料」プール ─────────────────────
print("\n(131) 判断材料プール")
reset()
import tempfile as _tf
_tmp = _tf.mkdtemp()
_orig_dir = post_saas.POSTS_DIR
_orig_rate = post_saas.JUDGE_RATE
post_saas.POSTS_DIR = _tmp
name = "テストサロン"
safe = post_saas._safe_name(name)
json.dump({"morning": ["通常A"], "noon": ["通常B"], "evening": ["通常C"]},
          open(os.path.join(_tmp, f"posts_{safe}.json"), "w", encoding="utf-8"),
          ensure_ascii=False)

def _pick(used=None):
    """例外も結果として返す（壊れたときに落ちるのではなく❌で見えるようにする）"""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return _real_pick_post(name, "noon", set() if used is None else used)
    except Exception as e:      # noqa: BLE001 投稿が止まること自体が不合格
        return f"例外:{type(e).__name__}"

# プールが無いときは今までどおり通常プールから選ぶ
post_saas.JUDGE_RATE = 1.0
got = _pick()
check("判断材料プールが無くても投稿できる", got == ["通常B"], got)

# プールがあれば判断材料から選ぶ
judge_path = post_saas.judge_pool_path(name)
json.dump({"_salon": name, "morning": [], "noon": ["判断材料1", "判断材料2"], "evening": []},
          open(judge_path, "w", encoding="utf-8"), ensure_ascii=False)
got = _pick()
check("プールがあれば判断材料から選ぶ",
      isinstance(got, list) and got[0].startswith("判断材料"), got)

# ファイル名が他のサロンの通常プールと重ならない
check("判断材料のファイル名が通常プールと重ならない",
      os.path.basename(judge_path) == f"posts_{safe}.judge.json"
      and "." not in safe, os.path.basename(judge_path))

# 中身の持ち主が違うファイルは使わない
json.dump({"_salon": "よそのサロン", "noon": ["よその判断材料"]},
          open(judge_path, "w", encoding="utf-8"), ensure_ascii=False)
check("持ち主が違うプールは使わない", _pick() == ["通常B"], _pick())

# 持ち主が書かれていないファイルも使わない（誰の物か確かめられない）
for label, doc in [("_salonなし", {"noon": ["持ち主不明の本文"]}),
                   ("_salonがnull", {"_salon": None, "noon": ["持ち主不明の本文"]})]:
    json.dump(doc, open(judge_path, "w", encoding="utf-8"), ensure_ascii=False)
    check(f"持ち主不明のプールは使わない（{label}）", _pick() == ["通常B"], _pick())

# 形が壊れていても通常プールに落ちる（構文だけでなく型も）
for label, content in [("配列", "[]"), ("null", "null"), ("数値", "5"),
                       ("スロットがnull", '{"noon": null}'),
                       ("スロットが数値", '{"noon": 42}'),
                       ("スロットが文字列", '{"noon": "abc"}'),
                       ("空文字だけ", '{"noon": [""]}'),
                       ("要素が数値", '{"noon": [123]}')]:
    open(judge_path, "w", encoding="utf-8").write(content)
    check(f"形が壊れていても投稿できる（{label}）", _pick() == ["通常B"], _pick())

json.dump({"_salon": name, "morning": [], "noon": ["判断材料1", "判断材料2"], "evening": []},
          open(judge_path, "w", encoding="utf-8"), ensure_ascii=False)

# 使い切っていたら通常プールに落ちる（投稿を止めない）
got = _pick({"判断材料1", "判断材料2"})
check("使い切ったら通常プールに落ちる", got == ["通常B"], got)

# 壊れていても止まらない
open(judge_path, "w", encoding="utf-8").write("{壊れたJSON")
got = _pick()
check("壊れていても投稿は止まらない", got == ["通常B"], got)

# 割合0なら一度も選ばれない
json.dump({"morning": [], "noon": ["判断材料1"], "evening": []},
          open(judge_path, "w", encoding="utf-8"), ensure_ascii=False)
post_saas.JUDGE_RATE = 0.0
picks = [_pick() for _ in range(20)]
check("割合0なら判断材料は出ない", all(p == ["通常B"] for p in picks),
      [p for p in picks if p != ["通常B"]][:3])

post_saas.POSTS_DIR = _orig_dir
post_saas.JUDGE_RATE = _orig_rate
shutil.rmtree(_tmp, ignore_errors=True)


# ── 132. Instagram誘導を二重に付けない ──────────────────────────
print("\n(132) Instagram誘導の二重掲載")
reset()
post_saas._maybe_add_instagram_cta_saas = _real_ig_cta
_orig_rand = post_saas.random.random
post_saas.random.random = lambda: 0.0        # 必ず付ける確率
for body in ("本文\nInstagramにも載せています。",
             "本文\nインスタもご覧ください。",
             "本文\nhttps://www.instagram.com/nico.lymph"):
    with contextlib.redirect_stdout(io.StringIO()):
        out = post_saas._maybe_add_instagram_cta_saas([body], "https://www.instagram.com/nico.lymph/")
    check(f"本文に案内があれば足さない（{body[3:12]}）", out == [body], out)
with contextlib.redirect_stdout(io.StringIO()):
    out = post_saas._maybe_add_instagram_cta_saas(["本文だけ"], "https://www.instagram.com/nico.lymph/")
post_saas.random.random = _orig_rand
check("案内が無い本文には付く", "instagram.com/nico.lymph" in out[0], out)
# ツリー投稿は1部目に案内があっても二重にしない
post_saas.random.random = lambda: 0.0
with contextlib.redirect_stdout(io.StringIO()):
    out = post_saas._maybe_add_instagram_cta_saas(["1部目 Instagramに載せています", "2部目"],
                                                  "https://www.instagram.com/nico.lymph/")
post_saas.random.random = _orig_rand
check("ツリーでも二重にしない", out == ["1部目 Instagramに載せています", "2部目"], out)


# ── 133. 判断材料投稿の事実照合（金額・距離・営業時間）─────────────
print("\n(133) 判断材料の事実照合")
reset()
from botlib import judge_fact_violation

_PICCOLO = {"サロン名": "ピッコロ",
            "価格を投稿に記載してもOKですか？": "はい（具体的な金額を投稿に出してOK）",
            "提供メニューと価格帯（箇条書きでOK）":
                "生コラーゲンシェービング¥22,000（初回体験9,900円）"
                "プラズマ幹細胞美肌再生フェイシャル¥38,500（初回体験¥8,800）",
            "一番の売りメニュー・最も結果が出やすい施術": "お顔そりからフェイシャルへ",
            "所在地（最寄り駅・徒歩時間）": "アピタ長津田店より車で3分",
            "営業時間": "9:00〜17:00"}
_IRIS = {"サロン名": "アイリス",
         "価格を投稿に記載してもOKですか？": "体験・初回コースの価格のみOK",
         "提供メニューと価格帯（箇条書きでOK）":
             "生コラーゲンシェービング　90分¥14,850/初回体験価格¥8,800",
         "一番の売りメニュー・最も結果が出やすい施術": "プラズマ幹細胞フェイシャル",
         "所在地（最寄り駅・徒歩時間）": "服部天神徒歩3分", "営業時間": "10:00〜17:00"}
_NICO = {"サロン名": "nico",
         "価格を投稿に記載してもOKですか？": "いいえ（詳しくはDMまたはHPへ誘導する）",
         "提供メニューと価格帯（箇条書きでOK）": "フェイシャル¥15,000〜/全身リンパ¥6,500円〜",
         "一番の売りメニュー・最も結果が出やすい施術": "肌の土台再生",
         "所在地（最寄り駅・徒歩時間）": "大分駅　徒歩3分", "営業時間": "9:30〜16:00"}
_MAN = {"サロン名": "テスト", "価格を投稿に記載してもOKですか？": "はい（具体的な金額を投稿に出してOK）",
        "提供メニューと価格帯（箇条書きでOK）": "初回10,000円",
        "一番の売りメニュー・最も結果が出やすい施術": "",
        "所在地（最寄り駅・徒歩時間）": "東京都渋谷区", "営業時間": "9:30〜18:00"}
_IRIS2 = {"サロン名": "アイリス", "価格を投稿に記載してもOKですか？": "体験・初回コースの価格のみOK",
          "提供メニューと価格帯（箇条書きでOK）": "初回8,800円（2回目以降14,850円）",
          "一番の売りメニュー・最も結果が出やすい施術": "",
          "所在地（最寄り駅・徒歩時間）": "服部天神徒歩3分", "営業時間": "10:00〜17:00"}
_MAN2 = {"サロン名": "テスト", "価格を投稿に記載してもOKですか？": "はい（具体的な金額を投稿に出してOK）",
         "提供メニューと価格帯（箇条書きでOK）": "施術50,000円",
         "一番の売りメニュー・最も結果が出やすい施術": "",
         "所在地（最寄り駅・徒歩時間）": "東京都渋谷区1丁目", "営業時間": "9:00〜18:00"}
_ADDR = {"サロン名": "ピッコロ", "価格を投稿に記載してもOKですか？": "いいえ（詳しくはDMまたはHPへ誘導する）",
         "提供メニューと価格帯（箇条書きでOK）": "",
         "一番の売りメニュー・最も結果が出やすい施術": "",
         "所在地（最寄り駅・徒歩時間）": "緑区霧が丘5-12-18", "営業時間": "9:00〜17:00"}
_FACT_CASES = [
    ("金額禁止のサロンに金額を書かせない", _NICO, "料金は初回8,800円です。", False),
    ("金額禁止ならメニューに在る金額も書かせない", _NICO, "フェイシャルは15,000円です。", False),
    ("架空の営業時間を通さない", _NICO, "毎日23時まで営業しています。", False),
    ("金額に触れない本文は通す", _NICO, "料金はDMでお伝えしています。", True),
    ("許可サロンのメニューにある金額は通す", _PICCOLO, "初回体験は8,800円です。", True),
    ("メニューに無い金額は通さない", _PICCOLO, "初回体験は7,700円です。", False),
    ("ヒアリング通りの所要時間は通す", _PICCOLO, "アピタ長津田店より車で3分です。", True),
    ("書いていない徒歩分数は通さない", _PICCOLO, "駅から徒歩3分です。", False),
    ("体験のみ許可＝初回価格は通す", _IRIS, "初回体験価格は8,800円です。", True),
    ("体験のみ許可＝通常価格は通さない", _IRIS, "通常は¥14,850です。", False),
    ("営業時間内の時刻は通す", _IRIS, "17時まで営業しています。", True),
    ("営業時間外の時刻は通さない", _IRIS, "20時まで営業しています。", False),
    ("本文のInstagram誘導は通さない", _IRIS, "Instagramにも載せています。", False),
    ("回数の話を金額と誤判定しない", _IRIS, "3回目で変化を感じる方が多いです。", True),
    ("暮らしの時刻を営業時間と誤判定しない", _NICO, "朝7時に起きて白湯を飲みます。", True),
    # 2026-09-13 Sol 2巡目の反例（どれも「通ってしまっていた」書き方）
    ("「1万円」も金額として止める", _NICO, "初回料金は1万円です。", False),
    ("漢数字の金額も止める", _NICO, "初回料金は一万円です。", False),
    ("時間の範囲表記も照合する", _NICO, "営業時間は9:30〜23:00です。", False),
    ("分まで照合する", _NICO, "毎日15:59まで営業しています。", False),
    ("ヒアリング通りの営業時間は通す", _NICO, "営業は9:30から16:00までです。", True),
    ("架空の住所を止める", _NICO, "東京都渋谷区神宮前9丁目99番地にあります。", False),
    ("ヒアリングに無い駅名を止める", _NICO, "新宿駅から徒歩3分です。", False),
    ("体験のみ許可でも通常価格は止める", _IRIS, "通常コースは14,850円です。", False),
    ("地名に見える普通の言葉は落とさない", _NICO, "都市部の方からもご相談をいただきます。", True),
    ("「この地区」を地名と誤判定しない", _NICO, "この地区のお客様が多いです。", True),
    ("市販という言葉を地名と誤判定しない", _NICO, "市販のスキンケアでは届きません。", True),
    ("ヒアリングに無い市名は止める", _NICO, "大阪市内から通ってくださる方もいます。", False),
    # 2026-09-13 Sol 3巡目の反例（厳しすぎて正しい投稿まで落ちていた／抜けていた）
    ("許可サロンの「1万円」は通す", _MAN, "初回は1万円です。", True),
    ("漢数字の「一万円」は止める", _MAN, "初回は一万円です。", False),
    ("「午後6時まで営業」は通す", _MAN, "午後6時まで営業しています。", True),
    ("「9時半から営業」は通す", _MAN, "9時半から営業しています。", True),
    ("営業時間外の「午後8時」は止める", _MAN, "午後8時まで営業しています。", False),
    ("ひらがなの市名も止める", _MAN, "つくば市のサロンです。", False),
    ("所在地に在る区名は通す", _MAN, "渋谷区のサロンです。", True),
    ("「2回目以降」の価格は止める", _IRIS2, "2回目以降は14,850円です。", False),
    ("括弧の外の初回価格は通す", _IRIS2, "初回体験は8,800円です。", True),
    # 2026-09-13 Sol 4巡目：前置きが付いた正しい地名まで落としていた
    ("前置き付きの正しい地名は通す", _MAN, "当店は渋谷区にあります。", True),
    ("文の途中の正しい地名も通す", _MAN, "仕事帰りに渋谷区でケアしませんか。", True),
    ("前置きが付いても架空の地名は止める", _MAN, "当店はつくば市にあります。", False),
    # 2026-09-13 Sol 6巡目：数字と地名の一部だけで通っていた
    ("小数の万円を誤読しない", _MAN2, "施術は1.5万円です。", False),
    ("メニュー通りの万円は通す", _MAN2, "施術は5万円です。", True),
    ("ひらがな地名も止める", _MAN2, "ほのか市にあります。", False),
    ("ヒアリングに無い番地を止める", _MAN2, "渋谷区1丁目99-99にあります。", False),
    ("回数の範囲を番地と誤判定しない", _MAN2, "3-5回通うと変化を感じます。", True),
    ("期間の範囲を番地と誤判定しない", _MAN2, "1-2ヶ月で実感される方が多いです。", True),
    ("漢数字の営業時間を止める", _MAN2, "営業時間は朝七時から夜十時です。", False),
    ("所在地に在る丁目は通す", _MAN2, "渋谷区1丁目にあります。", True),
    ("地名の一部だけの一致で通さない", _MAN2, "架空谷区にあります。", False),
    ("前置き付きの正しい区名は通す", _MAN2, "当店は渋谷区にあります。", True),
    # 数字の住所は末尾一致を許さない（登録の一部と重なるだけで通さない）
    ("丁目の数字が違えば止める", _MAN2, "渋谷区11丁目にあります。", False),
    ("丁目の前の地名が違えば止める", _MAN2, "架空区1丁目にあります。", False),
    ("番地の一部が重なるだけでは通さない", _ADDR, "霧が丘99-12-18です。", False),
    ("登録どおりの番地は通す", _ADDR, "緑区霧が丘5-12-18です。", True),
    # 2026-09-13 Sol 7巡目
    ("全角の小数点も読む", _MAN2, "施術は１．５万円です。", False),
    ("円が付かない万も読む", _MAN2, "施術は1.5万です。", False),
    ("百円も読む", _MAN2, "施術は5百円です。", False),
    ("地名の頭が違えば止める", _MAN2, "東渋谷区にあります。", False),
    ("番地が続く住所も全部照合する", _MAN2, "渋谷区1丁目99番99号です。", False),
    ("営業と書かなくても時間の範囲は照合する", _MAN2, "朝7時から夜10時までお待ちしています。", False),
    ("営業時間内の範囲は通す", _MAN2, "朝9時から夜18時までお待ちしています。", True),
    ("時間の前後が逆なら止める", _MAN2, "営業時間は18時から9時です。", False),
    ("回数の範囲を住所と誤判定しない", _MAN2, "3-12回が目安です。", True),
    ("時間の範囲を住所と誤判定しない", _MAN2, "10-20分です。", True),
]
_bad = [(nm, judge_fact_violation(t, sal))
        for nm, sal, t, ok in _FACT_CASES
        if (judge_fact_violation(t, sal) is None) != ok]
check("金額・距離・営業時間の照合が全件正しい", not _bad, _bad)


# ── 134. 投稿済み本文を1000件で打ち切らない ────────────────────
print("\n(134) 投稿済み本文の取り切り")
reset()
W.post_logs = [{"salon_id": SALON, "slot": "noon", "id": i,
                "post_content": f"過去本文{i:05d}", "posted_at": "2026-01-01T00:00:00+00:00"}
               for i in range(1200)]
# 別スロットの本文も同じサロンなら「使用済み」に入る（取りこぼし穴埋めでの二重投稿を防ぐ）
W.post_logs.append({"salon_id": SALON, "slot": "morning", "id": 9999,
                    "post_content": "朝で使った本文", "posted_at": "2026-01-01T00:00:00+00:00"})
with contextlib.redirect_stdout(io.StringIO()):
    used = _real_get_used(SALON)
check("1000件を超えても全部そろう", len(used) >= 1201, len(used))
check("2ページ目の本文も入っている", "過去本文01100" in used, None)
check("別スロットの本文も使用済みに入る", "朝で使った本文" in used, None)


# ── 135. 台帳にだけ残る本文をもう一度選ばない ───────────────────
print("\n(135) 台帳にだけ残る本文")
reset()
W.post_logs = []          # 記録は残っていない
W.attempts["OLD:2026-09-12:noon"] = {
    "op_id": "OLD:2026-09-12:noon", "salon_id": SALON, "jst_date": "2026-09-12",
    "slot": "noon", "status": "published", "rev": 1, "parts": [], "logged": False,
    "payload": {"texts": ["公開できたのに記録できなかった本文"],
                "original_first": "公開できたのに記録できなかった本文"}}
with contextlib.redirect_stdout(io.StringIO()):
    pending, complete = post_saas.get_pending_texts(SALON)
check("台帳の本文を拾う", "公開できたのに記録できなかった本文" in pending, pending)
check("読み切れたと分かる", complete is True, complete)
with contextlib.redirect_stdout(io.StringIO()):
    used_all, used_ok = post_saas.used_texts_for(SALON)
check("投稿で使う除外集合にも入る",
      "公開できたのに記録できなかった本文" in used_all, used_all)

# 記録済み（logged=true）の行は取らない＝post_logs側にあるので二重に持たない
W.attempts["DONE:2026-09-12:noon"] = {
    "op_id": "DONE:2026-09-12:noon", "salon_id": SALON, "jst_date": "2026-09-12",
    "slot": "noon", "status": "logged", "rev": 1, "parts": [], "logged": True,
    "payload": {"texts": ["記録まで終わった本文"], "original_first": "記録まで終わった本文"}}
with contextlib.redirect_stdout(io.StringIO()):
    pending, _ = post_saas.get_pending_texts(SALON)
check("記録済みの本文は台帳から取らない", "記録まで終わった本文" not in pending, pending)

# 読めないときは3回試す（1回で諦めない）
_orig_sg = post_saas.supabase_get
_tries = [0]


def _always_fail(path, params=None):
    _tries[0] += 1
    raise TimeoutError("timed out")


post_saas.supabase_get = _always_fail
with contextlib.redirect_stdout(io.StringIO()):
    post_saas.get_pending_texts(SALON)
post_saas.supabase_get = _orig_sg
check("読めないときは3回試す", _tries[0] == 3, _tries[0])

# 台帳が読めなくても投稿は止めない
_orig_sg = post_saas.supabase_get
post_saas.supabase_get = lambda p, params=None: (_ for _ in ()).throw(TimeoutError("timed out"))
try:
    with contextlib.redirect_stdout(io.StringIO()):
        got, complete = post_saas.get_pending_texts(SALON)
    err = None
except Exception as e:      # noqa: BLE001
    got, complete, err = None, None, type(e).__name__
post_saas.supabase_get = _orig_sg
check("台帳が読めなくても落ちない", err is None and got == set(), err or got)
check("読み切れなかったと申告する", complete is False, complete)

# 読み切れないときは判断材料プールを使わない（少ない在庫ほど引き当てやすい）
import tempfile as _tf2
_tmp2 = _tf2.mkdtemp()
_od = post_saas.POSTS_DIR
post_saas.POSTS_DIR = _tmp2
_nm = "テストサロン"
_sf = post_saas._safe_name(_nm)
json.dump({"morning": ["通常A"], "noon": ["通常B"], "evening": ["通常C"]},
          open(os.path.join(_tmp2, f"posts_{_sf}.json"), "w", encoding="utf-8"),
          ensure_ascii=False)
json.dump({"_salon": _nm, "morning": [], "noon": ["判断材料X"], "evening": []},
          open(post_saas.judge_pool_path(_nm), "w", encoding="utf-8"), ensure_ascii=False)
_or = post_saas.JUDGE_RATE
post_saas.JUDGE_RATE = 1.0
with contextlib.redirect_stdout(io.StringIO()):
    got_ng = _real_pick_post(_nm, "noon", set(), allow_judge=False)
    got_ok = _real_pick_post(_nm, "noon", set(), allow_judge=True)
post_saas.JUDGE_RATE = _or
post_saas.POSTS_DIR = _od
shutil.rmtree(_tmp2, ignore_errors=True)
check("読み切れないときは判断材料を使わない", got_ng == ["通常B"], got_ng)
check("読み切れたときは判断材料を使う", got_ok == ["判断材料X"], got_ok)


print("\n" + ("🚨 失敗 " + ", ".join(FAILS) if FAILS else "✅ 全項目パス"))
sys.exit(1 if FAILS else 0)
