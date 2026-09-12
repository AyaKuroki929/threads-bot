"""投稿の「実行権」と「結果」を1スロット1行で残す台帳（Supabase: post_attempts）。

なぜ必要か（2026-09-12 Sol指摘①②③の土台）:
  Threadsの publish 応答が失われたとき（504など）、「公開できたか分からない」状態になる。
  これを記録せずに終わると、次の実行は「未投稿」と見なして同じ本文をもう一度出す＝二重投稿。
  逆に「失敗」として扱うと、実は公開済みの投稿が記録されず、以後ずっと欠落が残る。
  分からないものは分からないまま次の実行へ引き継ぐ。それを可能にするのがこの台帳。

設計の要点:
  ・1スロット1行（op_id = "<salon_id>:<JST日付>:<slot>"）。op_id は UNIQUE なので
    INSERT の成否がそのまま「実行権を取れたか」になる（予備実行との同時起動対策）。
  ・更新は必ず rev 一致を条件にした PATCH（楽観ロック）。取り違え更新を起こさない。
  ・parts に各パートの creation_id / post_id / 状態を残す。親だけ公開できた場合に
    親から作り直さず、残りのパートだけ再開できる。
  ・表が無い環境（未適用・別プロジェクト）では available() が False を返し、
    呼び出し側は従来動作にフォールバックする（この台帳の導入自体で投稿を止めない）。
"""
from __future__ import annotations

import hashlib
import json
import os
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
TABLE = "post_attempts"

# 残り時間を返す関数。post_saas が締切を持っているときに差し込む。
# 台帳の通信だけ固定20秒だと、ジョブ締切を食い破る（2026-09-12 Sol指摘#4）
time_left_fn = None


def _http_timeout(default=20):
    if time_left_fn is None:
        return default
    left = time_left_fn()
    if left is None:
        return default
    return max(1, min(default, int(left)))

STATUS_RUNNING = "running"      # 実行権を取得。公開処理中
STATUS_UNKNOWN = "unknown"      # 公開できたか確定できない。人の確認待ち＝再送禁止
STATUS_PUBLISHED = "published"  # 公開済み（post_logs への記録は未完）
STATUS_LOGGED = "logged"        # 公開＋記録まで完了
STATUS_FAILED = "failed"        # 未公開を確定。再試行してよい
STATUS_ATTENTION = "attention"    # 人の確認が要る。自動では二度と触らない
STATUS_HOLD_REPAIR = "hold_repair"  # 投稿は止めるが、記録の修復だけは自動で続ける

# 「他の実行が処理中かもしれない」と見なす時間。updated_at は工程ごとに進むので
# ＝「最後に進捗があってから」の秒数。これを超えたら死んだ実行とみなして引き継ぐ。
#  running … まだ何も公開していない枠。取り違えの害が大きいので長め
#  resume  … 既に一部公開済み／未確定の枠。予備実行（12分後）に続きを任せたいので短め
STALE_SEC = int(os.environ.get("POST_STATE_STALE_SEC", "600"))
RESUME_STALE_SEC = int(os.environ.get("POST_STATE_RESUME_STALE_SEC", "120"))

PART_PENDING = "pending"
PART_CONTAINER = "container"    # コンテナ作成済み・公開要求前/中
PART_UNKNOWN = "unknown"
PART_PUBLISHED = "published"


class StateError(Exception):
    """台帳そのものが操作できない。投稿を進めてよいか判断できないので停止させる。"""


def norm_text(t: str) -> str:
    """本文比較用の正規化。

    ⚠️ 空白を「削除」してはいけない（Sol指摘#5）。"ab c" と "a bc" が同一になり、
    別の投稿を今回のものと誤認する。空白は1つに畳むだけにし、
    全角/半角・合成文字の違いは NFKC で吸収する。
    """
    s = unicodedata.normalize("NFKC", t or "")
    return " ".join(s.split())


def part_hash(t: str) -> str:
    return hashlib.sha256(norm_text(t).encode("utf-8")).hexdigest()[:32]


def make_op_id(salon_id: str, jst_date: str, slot: str) -> str:
    return f"{salon_id}:{jst_date}:{slot}"


# ── HTTP（post_saas に依存しない最小実装。循環importを避ける）────────────
def _req(method: str, path: str, *, params=None, body=None, prefer=None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=_http_timeout()) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else []


_available = None


def force_memory():
    """この実行では表を使わない（手動実行・DRY_RUN用）。本番の枠を汚さない。"""
    global _available
    _available = False


def available() -> bool:
    """post_attempts が使える状態かを1回だけ確認する。"""
    global _available
    if _available is not None:
        return _available
    if not (SUPABASE_URL and SUPABASE_KEY):
        _available = False
        return False
    try:
        _req("GET", TABLE, params={"select": "op_id", "limit": 1})
        _available = True
    except urllib.error.HTTPError as e:
        # ⚠️ 表が無いからといって黙って従来動作に落ちない（2026-09-12 Sol指摘）。
        # 落ちると「未確定の公開を次の実行へ引き継げない」＝二重投稿の経路が復活する。
        # 表が無いのは設定漏れなので、止めて気づかせる。テスト・手動実行は force_memory()。
        raise StateError(
            f"投稿台帳 post_attempts が使えません（HTTP {e.code}）。"
            f"sql/post_attempts.sql をSupabaseのSQL Editorで実行してください") from e
    except Exception as e:
        raise StateError(f"台帳の確認に失敗: {type(e).__name__}: {e}") from e
    return _available


# force_memory() を呼んだときだけ、同じAPIのままプロセス内メモリで動かす
# （テスト・DRY_RUN・手動実行用。呼び出し側を分岐させないため）。
# ⚠️ メモリ台帳は実行をまたいで残らない＝次回への引き継ぎができない。本番では使わない。
_MEM: dict = {}


def fetch(op_id: str):
    if not available():
        row = _MEM.get(op_id)
        return dict(row) if row else None
    rows = _req("GET", TABLE, params={"select": "*", "op_id": f"eq.{op_id}"})
    return rows[0] if rows else None


def _age_sec(row) -> float:
    """最後の進捗からの経過秒。読めなければ -1（＝古さを判断できない）。"""
    from datetime import datetime, timezone
    try:
        upd = datetime.fromisoformat(str(row.get("updated_at")).replace("Z", "+00:00"))
        if upd.tzinfo is None:
            upd = upd.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - upd).total_seconds()
        # 未来の時刻＝時計がずれている。古いとも新しいとも判断できない（Sol指摘#3）
        return age if age >= 0 else -1.0
    except Exception:
        return -1.0


def acquire(salon_id: str, jst_date: str, slot: str, *, stale_sec: int = None,
            allow_attention: bool = False):
    """実行権を取る。返り値は (action, row)。

      "go"      … このスロットはまだ何も公開していない。投稿して良い
      "resume"  … 途中まで公開済み。payload の本文で残りのパートだけ続ける
      "hold"    … 公開できたか未確定 or 他の実行が処理中。投稿してはいけない
      "skip"    … 公開＋記録まで完了済み
    """
    stale_sec = STALE_SEC if stale_sec is None else stale_sec
    op_id = make_op_id(salon_id, jst_date, slot)
    row = fetch(op_id)
    if row is None and not available():
        _MEM[op_id] = {"op_id": op_id, "salon_id": salon_id, "jst_date": jst_date,
                       "slot": slot, "status": STATUS_RUNNING, "parts": [], "rev": 0,
                       "payload": None, "note": None, "updated_at": _now_iso()}
        return ("go", dict(_MEM[op_id]))
    if row is None:
        try:
            created = _req("POST", TABLE, body={
                "op_id": op_id, "salon_id": salon_id, "jst_date": jst_date,
                "slot": slot, "status": STATUS_RUNNING, "parts": [], "rev": 0,
            }, prefer="return=representation")
            return ("go", created[0] if created else fetch(op_id))
        except urllib.error.HTTPError as e:
            if e.code != 409:   # 409 = 同時に誰かが取った
                raise StateError(f"実行権の取得に失敗 HTTP {e.code}") from e
            row = fetch(op_id)
            if row is None:
                raise StateError("実行権の取得直後に行が消えた") from e

    st = row.get("status")
    if st == STATUS_LOGGED:
        return ("skip", row)
    if st == STATUS_HOLD_REPAIR:
        # 投稿は止めるが、記録の修復だけは自動で続ける状態
        if not allow_attention:
            return ("hold", row)
        age = _age_sec(row)
        if age < 0 or age < RESUME_STALE_SEC:
            return ("hold", row)
        return _take("repair", row)
    if st == STATUS_ATTENTION:
        # 人が見るまで自動では触らない停止状態（自動でできることは残っていない）
        print(f"[state] {op_id}: 要対応のため自動処理しません（{(row.get('note') or '')[:60]}）")
        return ("hold", row)
    if st in (STATUS_UNKNOWN, STATUS_PUBLISHED):
        # 未確定でも「同じコンテナで公開をやり直す」のは二重投稿にならない。
        # 止めるのではなく、前回の続きとして解決させる（新しいコンテナは作らせない）。
        # ただし直前まで動いていたなら、その実行に任せる（同時に2つが同じ行を触ると
        # rev不一致で片方が落ち、通知だけが増える）。
        age = _age_sec(row)
        if age < 0:
            print(f"[state] {op_id}: 最終更新時刻が読めない → 触りません")
            return ("hold", row)
        if age < RESUME_STALE_SEC:
            print(f"[state] {op_id}: 直前まで進行中（{int(age)}秒前）→ その実行に任せます")
            return ("hold", row)
        return _take("resume", row)
    if st == STATUS_FAILED:
        # ⚠️ ここも所有権を取ってから返す。取らないと、同じ failed 行を2つの実行が
        # 掴んで別々の本文を公開できる（2026-09-12 Sol指摘#1）
        return _take("go", row)

    # running：前回が途中で死んだか、いま別の実行が動いている
    age = _age_sec(row)
    if age < 0:
        # 時刻が読めない（または未来）＝古いかどうか判断できない。触らない側に倒す
        print(f"[state] {op_id}: 最終更新時刻が読めない → 触りません")
        return ("hold", row)
    parts = row.get("parts") or []
    # ⚠️ 状態名だけを見ると、状態が巻き戻った台帳（pending なのに creation_id や
    # post_id が残っている）を「まだ何もしていない」と誤読して出し直してしまう
    def _has_history(p):
        return bool(p.get("creation_id") or p.get("post_id") or p.get("lost_response")) \
            or p.get("status") in (PART_CONTAINER, PART_UNKNOWN, PART_PUBLISHED)

    if any(_has_history(p) for p in parts):
        # ⚠️ 途中まで進んでいても、古さの確認を飛ばさない。飛ばすと2つの実行が
        # 同じ行を同時に resume して両方が公開要求へ進む（2026-09-12 Sol指摘#3）
        if age < RESUME_STALE_SEC:
            print(f"[state] {op_id}: 直前まで進行中（{int(age)}秒前）→ その実行に任せます")
            return ("hold", row)
        return _take("resume", row)
    if age < stale_sec:
        print(f"[state] {op_id}: 別の実行が処理中の可能性（{int(age)}秒前に更新）→ 投稿しません")
        return ("hold", row)
    return _take("go", row)


def _take(action: str, row: dict):
    """条件付き更新（rev一致）で所有権を取る。取れなければ他の実行が持っている。

    ⚠️ これが無いと、2つの実行が同じ行を同時に resume して両方が公開要求へ進む
    （2026-09-12 Sol指摘#3）。INSERTだけが原子的では足りない。"""
    try:
        # repair（記録の修復だけ）は停止状態を解除しない。解除すると投稿経路が開いてしまう
        # ⚠️ note は「人が読む停止理由（投稿IDを含むことがある）」なので上書きしない。
        # 実行権の取得は rev が進むこと自体が印になる（2026-09-12 Sol指摘#2）
        new_status = row.get("status") if action == "repair" else STATUS_RUNNING
        taken = update(row, status=new_status)
    except StateError:
        print(f"[state] {row['op_id']}: 実行権を他の実行に取られました → 投稿しません")
        return ("hold", row)
    return (action, taken)


def update(row: dict, **fields) -> dict:
    """rev 一致を条件にした更新。取り違え更新を起こさない。"""
    rev = row.get("rev", 0)
    fields["rev"] = rev + 1
    # ⚠️ updated_at は送らない。DBのトリガが now() を打つ（実行側の時計を信じない・Sol指摘#3）
    fields.pop("updated_at", None)
    if not available():
        fields["updated_at"] = _now_iso()
        cur = _MEM.get(row["op_id"])
        if cur is None or cur.get("rev", 0) != rev:
            raise StateError(f"台帳が他の実行に更新されていました（op_id={row['op_id']}）")
        cur.update(fields)
        return dict(cur)
    res = _req("PATCH", TABLE,
               params={"op_id": f"eq.{row['op_id']}", "rev": f"eq.{rev}"},
               body=fields, prefer="return=representation")
    if not res:
        # 誰かが先に更新した＝自分の手元の row は古い。楽観ロックの正しい挙動
        raise StateError(f"台帳が他の実行に更新されていました（op_id={row['op_id']}）")
    return res[0]


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def set_part(row: dict, i: int, **fields) -> dict:
    """parts[i] を書き換える（無ければ作る）。行ごと rev 付きで更新する。"""
    parts = list(row.get("parts") or [])
    for p in parts:
        if p.get("i") == i:
            p.update(fields)
            break
    else:
        d = {"i": i, "status": PART_PENDING}
        d.update(fields)
        parts.append(d)
    parts.sort(key=lambda p: p.get("i", 0))
    return update(row, parts=parts)


def get_part(row: dict, i: int):
    for p in (row.get("parts") or []):
        if p.get("i") == i:
            return p
    return None


def open_issues(limit: int = 50, since_days: int = 3, salon_ids=None):
    """まだ片づいていない枠を、最後に触った順で返す。

    ⚠️ op_id には日付が入るので、翌日の実行は前日の行を「見に行かない限り」見ない。
    取りこぼしを拾うのはこの関数の役目。

    対象（すべてSQLだけで絞れる状態）:
      unknown / published / running … 公開の途中か、記録が未完
      hold_repair                   … 投稿は止めるが、記録の修復は自動で続ける
    対象外:
      logged / failed / attention   … 片づいている、または人の確認待ち

    ⚠️ 状態だけで絞り切ること。取ってからPythonで落とす作りにすると、
    除外対象が大量に並んだときに後続へ永久に届かない（2026-09-12 Sol指摘#3）。
    ⚠️ 日付では切らない。障害や再連携待ちが長引いた枠ほど回収が要る。"""
    live = (STATUS_UNKNOWN, STATUS_PUBLISHED, STATUS_RUNNING, STATUS_HOLD_REPAIR)
    if not available():
        rows = [r for r in _MEM.values()
                if r.get("status") in live
                and (salon_ids is None or r.get("salon_id") in salon_ids)]
        return sorted(rows, key=lambda r: str(r.get("updated_at") or ""))[:limit]
    params = {
        "select": "op_id,salon_id,jst_date,slot,status,note,parts,payload,logged,rev,updated_at",
        "status": f"in.({','.join(live)})",
        # 稼働中サロンで先に絞る。並びは「最後に触った順」＝失敗した行は後ろへ回る
        "order": "updated_at.asc,op_id.asc", "limit": limit,
    }
    if salon_ids:
        params["salon_id"] = "in.(" + ",".join(salon_ids) + ")"
    return _req("GET", TABLE, params=params)
