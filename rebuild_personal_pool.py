#!/usr/bin/env python3
"""個人アカウント（@aya_kuroki_0929）の投稿ストックを、新しいコンセプトで作り直す。

2026-11-01にブランディングが「時間とお金の削減」へ変わった。既存の在庫は前のコンセプト
（AI・業務効率化）で作ったものなので、新しいルールで作り直す。

作り方の順番がポイント：**先に新しい投稿を足してから、古いものを落とす**。
先に空にすると、途中で生成が失敗したときに投稿する在庫がゼロになる（朝7時半に何も出ない）。

  1. 今の本数を覚えておく
  2. generate_posts.py を ROUNDS 回まわして新しい投稿を後ろに足す（POOL_THRESHOLD で補充のしきい値を上げる）
  3. 新しい分が MIN_NEW 本以上あることを確かめてから、古い分（最初の N 本）を落とす
  4. 手動投稿用の posts_personal.json にも同じ中身を写す（配信用と中身が食い違わないように）

⚠️ 実際に配信されるのは posts_saas/posts_aya_kuroki_0929.json。2026-06-22のSaaS統合で
個人アカウントも post_saas.py 経由になったため、posts_personal.json は緊急手動投稿
（post_personal.yml / manual_post_personal.yml）専用になっている。ここを間違えると
「作り直したのに古い投稿が流れ続ける」（2026-11-02に実際に起きた）。

古い在庫は git の履歴に残っているので、戻したくなったら取り出せる。
"""
import json
import os
import subprocess
import sys

import post_concept

_BASE = os.path.dirname(os.path.abspath(__file__))

# generate_posts.py が補充するアカウント（クライアントの rebuild_saas_pools.py とは別経路）。
# ACCOUNT で切り替える。既定は個人アカウント。
ACCOUNT = os.environ.get("ACCOUNT") or "saas_personal"
_PRESETS = {
    "saas_personal": {
        "pool": os.path.join("posts_saas", "posts_aya_kuroki_0929.json"),
        "manual": "posts_personal.json",          # 緊急手動投稿用（同じ中身を写す）
        "rules": "GENERATE_RULES_personal.md",
    },
    "saas_bemolle": {
        "pool": os.path.join("posts_saas", "posts_bemolle_diet.json"),
        "manual": "",                             # 手動用の控えは無い
        "rules": "GENERATE_RULES.md",
    },
}
if ACCOUNT not in _PRESETS:
    raise SystemExit(f"ACCOUNT が不正です: {ACCOUNT}（使えるのは {list(_PRESETS)}）")
_P = _PRESETS[ACCOUNT]
POOL = os.path.join(_BASE, _P["pool"])                                   # 実際に配信されるファイル
MANUAL = os.path.join(_BASE, _P["manual"]) if _P["manual"] else ""
RULES_FILE = _P["rules"]
ROUNDS = int(os.environ.get("ROUNDS") or 6)
MIN_NEW = int(os.environ.get("MIN_NEW") or 20)      # 1枠あたり、これ未満しか作れなければ古い分を落とさない
POOL_THRESHOLD = os.environ.get("POOL_THRESHOLD") or "500"


def load(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def main():
    posts = load(POOL)
    if not posts:
        print(f"{POOL} を読めません", file=sys.stderr)
        return 1
    orig = {slot: len(v) for slot, v in post_concept.slots_only(posts).items()}
    print(f"対象: {ACCOUNT} / {POOL}")
    print(f"作り直し前: { {k: v for k, v in orig.items()} }")

    env = {**os.environ, "POOL_THRESHOLD": POOL_THRESHOLD}
    ok_rounds = 0
    for i in range(1, ROUNDS + 1):
        print(f"\n===== {i}/{ROUNDS} 回目の生成 =====")
        r = subprocess.run([sys.executable, os.path.join(_BASE, "generate_posts.py"), ACCOUNT], env=env)
        if r.returncode == 0:
            ok_rounds += 1
        now = {slot: len(v) for slot, v in post_concept.slots_only(load(POOL) or {}).items()}
        print(f"[{i}/{ROUNDS}] 終了コード {r.returncode} / 現在 {now}")

    posts = load(POOL)
    new_counts = {slot: len(posts.get(slot, [])) - orig.get(slot, 0) for slot in orig}
    print(f"\n新しく作れた本数: {new_counts}")

    # 中身を目で見られるように、新しく作った分を各枠3本ずつ出す
    print("\n===== 新しく作った投稿の見本 =====")
    for slot in orig:
        for post in posts.get(slot, [])[orig.get(slot, 0):][:3]:
            parts = post if isinstance(post, list) else [post]
            print(f"\n--- {slot} ---")
            for part in parts:
                print(part)

    if ok_rounds == 0:
        print("\n生成が1回も成功しませんでした（APIキー切れ等）", file=sys.stderr)
        return 1

    thin = [s for s, n in new_counts.items() if n < MIN_NEW]
    if thin:
        # 生成自体は動いているので失敗扱いにしない（🚨LINEを無駄に飛ばさない）。
        # 足りない分は回数を増やしてもう一度まわす
        print(f"::warning::{thin} の新しい分が {MIN_NEW} 本に届かないので、古い在庫は落とさずそのままにしました。"
              f"回数を増やしてもう一度実行してください")
        return 0

    for slot in orig:
        posts[slot] = posts[slot][orig[slot]:]
    post_concept.stamp(posts, RULES_FILE)
    json.dump(posts, open(POOL, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    if MANUAL:
        json.dump(posts, open(MANUAL, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"作り直し後: { {k: len(v) for k, v in post_concept.slots_only(posts).items()} }"
          f"（古い分は落とし、手動投稿用の posts_personal.json にも同じ中身を写しました）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
