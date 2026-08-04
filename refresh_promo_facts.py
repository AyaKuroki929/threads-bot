#!/usr/bin/env python3
"""宣伝投稿に使う実測値を取り直して promo_facts.json に保存する（毎月1日に自動実行）。

なぜ必要か：
  宣伝文には「直近30日で32,020回表示」「Instagramが8日で+36人」のような実測値を本文に埋めている。
  この数字は書いた瞬間から古くなるため、放置すると古い数字を配信し続けることになる（＝嘘になる）。
  そこで毎月、数字を取り直し、古い数字が入ったままの未使用ストックを捨てて作り直させる。

やること：
  1. Threads API / Supabase / Meta API から現在値を取得
  2. promo_facts.json に保存（generate_promo_posts.py がこれを読んでプロンプトに差し込む）
  3. --purge を付けると、更新後の数字と食い違う未使用の宣伝文を在庫から削除する
     （削除後は在庫が減るので、後続の generate_promo_posts.py が新しい数字で作り直す）

環境変数: SUPABASE_URL / SUPABASE_SERVICE_KEY（必須）
          META_ACCESS_TOKEN（任意・無ければInstagramの数字だけ前回値を据え置き）
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
FACTS_FILE = os.path.join(BASE, "promo_facts.json")
POOL_FILE = os.path.join(BASE, "promo_posts_personal.json")
USED_FILE = os.path.join(BASE, "promo_used_personal.json")
JST = timezone(timedelta(hours=9))

PERSONAL_SALON = "aya_kuroki_0929"
PERSONAL_THREADS_UID = "26716216404727638"  # /me が返す実ID（salonsテーブルの値は古いので使わない）
BEMOLLE_IG_ID = "17841470478859455"


def _get(url: str, timeout: int = 25):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def _sb(path: str):
    """Supabase REST。1000件の上限で頭打ちになるため、全部取り切るまでページングする。
    （2026-08-04：上限で切れた1000件を『合計1000件』と誤って集計し、投稿文に誤った数字を書いた）"""
    base = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_KEY"]
    out, offset, page = [], 0, 1000
    while True:
        sep = "&" if "?" in path else "?"
        url = f"{base}/rest/v1/{path}{sep}limit={page}&offset={offset}"
        req = urllib.request.Request(url, headers={"apikey": key, "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            chunk = json.loads(r.read())
        out += chunk
        if len(chunk) < page:
            return out
        offset += page


def collect() -> dict:
    now = datetime.now(JST)
    facts = {"updated_at": now.strftime("%Y-%m-%d %H:%M JST")}

    # ── 稼働アカウント数 ──────────────────────────────
    salons = _sb("salons?select=salon_name,is_active,created_at&order=created_at")
    active = [s for s in salons if s["is_active"]]
    facts["active_accounts"] = len(active)
    facts["oldest_start"] = active[0]["created_at"][:10] if active else ""

    # ── 投稿実績（直近30日）──────────────────────────
    since = (now - timedelta(days=30)).astimezone(timezone.utc).isoformat()
    logs = _sb(f"post_logs?select=posted_at&posted_at=gte.{urllib.parse.quote(since)}")
    from collections import Counter
    def _jst_date(iso: str) -> str:
        # 小数秒の桁数が揃わない（Python3.9のfromisoformatが弾く）ため秒までに丸める
        import re as _re
        s2 = _re.sub(r"\.\d+", "", iso.replace("Z", "+00:00"))
        t = datetime.fromisoformat(s2)
        return t.astimezone(JST).strftime("%Y-%m-%d")
    days = Counter(_jst_date(l["posted_at"]) for l in logs)
    # 当日と最古日は途中集計（窓の端）なので除外して数える
    ordered = sorted(days)
    drop = {now.strftime("%Y-%m-%d")}
    if ordered:
        drop.add(ordered[0])
    full_days = {d: c for d, c in days.items() if d not in drop}
    facts["log_days"] = len(full_days)
    facts["log_total"] = sum(full_days.values())
    facts["log_min"] = min(full_days.values()) if full_days else 0
    facts["log_max"] = max(full_days.values()) if full_days else 0
    facts["zero_days"] = sum(1 for c in full_days.values() if c == 0)

    # ── 個人Threads（フォロワー・表示回数）───────────
    rows = _sb(f"salons?salon_name=eq.{PERSONAL_SALON}&select=access_token")
    tok = rows[0]["access_token"] if rows else ""
    if tok:
        q = urllib.parse.urlencode({"metric": "followers_count", "access_token": tok})
        d = _get(f"https://graph.threads.net/v1.0/{PERSONAL_THREADS_UID}/threads_insights?{q}")
        facts["threads_followers"] = d["data"][0]["total_value"]["value"]

        t = int(time.time())
        q = urllib.parse.urlencode({"metric": "views", "since": t - 29 * 86400,
                                    "until": t, "access_token": tok})
        d = _get(f"https://graph.threads.net/v1.0/{PERSONAL_THREADS_UID}/threads_insights?{q}")
        vals = [v["value"] for v in d["data"][0]["values"]]
        facts["views_total"] = sum(vals)
        facts["views_avg"] = sum(vals) // len(vals)
        facts["views_max"] = max(vals)

    # ── ベモーレ Instagram フォロワー ────────────────
    meta = os.environ.get("META_ACCESS_TOKEN", "")
    if meta:
        q = urllib.parse.urlencode({"fields": "followers_count", "access_token": meta})
        d = _get(f"https://graph.facebook.com/v21.0/{BEMOLLE_IG_ID}?{q}")
        facts["ig_followers"] = d.get("followers_count")
    return facts


# 常に書いてよい数字：サービス仕様の固定値・日付や時刻・過去の基準値（IGの起点1,113人など）。
# generate_promo_posts.py も promo_facts.json 経由でこの一覧を読むので、判定は必ず一致する。
FIXED_NUMS = ["2750", "2,750", "3", "7", "12", "9", "1", "2", "5", "8", "0",
              "30", "2026", "19", "450", "280", "10", "20", "1113", "1,113"]


def allowed_numbers(facts: dict) -> set:
    """本文に書いてよい数字（カンマ有無の両方）"""
    nums = set(facts.get("fixed_nums") or FIXED_NUMS)
    for v in facts.values():
        if isinstance(v, int):
            nums.add(str(v))
            nums.add(f"{v:,}")
    return nums


def purge_stale(facts: dict) -> int:
    """更新後の数字と食い違う未使用の宣伝文を在庫から削除し、削除数を返す"""
    import re
    pool = json.load(open(POOL_FILE, encoding="utf-8"))
    try:
        used = set(json.load(open(USED_FILE, encoding="utf-8")))
    except Exception:
        used = set()
    ok = allowed_numbers(facts)
    kept, removed = [], []
    for p in pool.get("posts", []):
        if p in used:
            kept.append(p)  # 使用済みは履歴として残す（重複生成の判定に使う）
            continue
        body = p.replace("https://lin.ee/88vrtbQ", "")
        bad = [n for n in re.findall(r"[0-9][0-9,]*", body) if n not in ok]
        (removed if bad else kept).append(p)
        if bad:
            print(f"[facts] 古い数字のため削除: {bad} … {p.splitlines()[0][:30]}")
    pool["posts"] = kept
    json.dump(pool, open(POOL_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return len(removed)


def main() -> int:
    facts = collect()
    prev = {}
    if os.path.exists(FACTS_FILE):
        prev = json.load(open(FACTS_FILE, encoding="utf-8"))
    # Instagramのトークンが無い月は前回値を据え置く（数字を消さない）
    if "ig_followers" not in facts and "ig_followers" in prev:
        facts["ig_followers"] = prev["ig_followers"]
        facts["ig_note"] = "前回値を据え置き（META_ACCESS_TOKEN未設定）"
    facts["fixed_nums"] = FIXED_NUMS
    facts["prev_ig_followers"] = prev.get("ig_followers")
    facts["prev_updated_at"] = prev.get("updated_at")

    json.dump(facts, open(FACTS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("[facts] 更新しました:")
    for k, v in facts.items():
        print(f"  {k}: {v}")

    if "--purge" in sys.argv:
        n = purge_stale(facts)
        print(f"[facts] 古い数字の宣伝文を{n}本削除しました（この後の生成で新しい数字のものが作られます）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
