#!/usr/bin/env python3
"""個人アカウント（@aya_kuroki_0929）の投稿ストックを、新しいコンセプトで作り直す。

2026-11-01にブランディングが「時間とお金の削減」へ変わった。既存の在庫は前のコンセプト
（AI・業務効率化）で作ったものなので、新しいルールで作り直す。

作り方の順番がポイント：**先に新しい投稿を足してから、古いものを落とす**。
先に空にすると、途中で生成が失敗したときに投稿する在庫がゼロになる（朝7時半に何も出ない）。

  1. 今の本数を覚えておく
  2. generate_posts.py を ROUNDS 回まわして新しい投稿を後ろに足す（POOL_THRESHOLD で補充のしきい値を上げる）
  3. 新しい分が MIN_NEW 本以上あることを確かめてから、古い分（最初の N 本）を落とす
  4. used_posts_personal.json を空にする（中身が全部入れ替わるので、使用済みの番号は意味が無くなる）

古い在庫は git の履歴に残っているので、戻したくなったら取り出せる。
"""
import json
import os
import subprocess
import sys

_BASE = os.path.dirname(os.path.abspath(__file__))
POOL = os.path.join(_BASE, "posts_personal.json")
USED = os.path.join(_BASE, "used_posts_personal.json")
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
        print("posts_personal.json を読めません", file=sys.stderr)
        return 1
    orig = {slot: len(v) for slot, v in posts.items()}
    print(f"作り直し前: { {k: v for k, v in orig.items()} }")

    env = {**os.environ, "POOL_THRESHOLD": POOL_THRESHOLD}
    ok_rounds = 0
    for i in range(1, ROUNDS + 1):
        print(f"\n===== {i}/{ROUNDS} 回目の生成 =====")
        r = subprocess.run([sys.executable, os.path.join(_BASE, "generate_posts.py"), "personal"], env=env)
        if r.returncode == 0:
            ok_rounds += 1
        now = {slot: len(v) for slot, v in (load(POOL) or {}).items()}
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

    for slot in list(posts.keys()):
        posts[slot] = posts[slot][orig.get(slot, 0):]
    json.dump(posts, open(POOL, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump({}, open(USED, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"作り直し後: { {k: len(v) for k, v in posts.items()} }（古い分は落とし、使用済みの記録も空にしました）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
