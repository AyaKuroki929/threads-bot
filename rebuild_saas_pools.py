#!/usr/bin/env python3
"""クライアント全アカウントの投稿ストックを、今のルールで作り直す。

2026-11-10、鷺さんの指摘を受けて投稿の書き方を「説明」から「会話」に変えた
（GENERATE_RULES_conversation.md）。既存の在庫は前の書き方で作ったものなので、
新しいルールで作り直す。個人アカウントの rebuild_personal_pool.py のクライアント版。

作り方の順番がポイント：**先に新しい投稿を足してから、古いものを落とす**。
先に空にすると、途中で生成が失敗したときに投稿する在庫がゼロになる（朝7時半に何も出ない）。

  1. 稼働中のアカウントを集める（停止中・解約済みは generate_saas_posts 側がスキップする）
  2. アカウントごとに、今の本数を覚えてから生成を ROUNDS 回まわす
     （POOL_THRESHOLD を大きくして「在庫が足りているから作らない」を外す）
  3. 新しい分が各枠 MIN_NEW 本以上あることを確かめてから、古い分（最初のN本）を落とす
  4. 足りない枠があるアカウントは、古い在庫をそのまま残す（投稿を止めないため）

環境変数：
  ROUNDS       1アカウントあたり生成を何回まわすか（既定3。1回＝1枠15本）
  MIN_NEW      1枠あたりこの本数を作れなければ古い在庫を落とさない（既定20）
  ONLY         カンマ区切りで対象を絞る（Threads ID かサロン名。既定は全件）
  SKIP         カンマ区切りで対象から外す
"""
import json
import os
import sys

# ⚠️ generate_saas_posts を import する前に設定する。THRESHOLD はモジュール読み込み時に
# 環境変数から決まるので、あとから変えても効かない。
os.environ.setdefault("POOL_THRESHOLD", "500")

import generate_saas_posts as g  # noqa: E402

_BASE = os.path.dirname(os.path.abspath(__file__))
SLOTS = ("morning", "noon", "evening")
ROUNDS = int(os.environ.get("ROUNDS") or 3)
MIN_NEW = int(os.environ.get("MIN_NEW") or 20)
ONLY = [s.strip() for s in (os.environ.get("ONLY") or "").split(",") if s.strip()]
SKIP = [s.strip() for s in (os.environ.get("SKIP") or "").split(",") if s.strip()]

# 個人アカウントはここでは作り直さない（rebuild_personal_pool.py が担当・ルールが別）
SKIP_ALWAYS = set(getattr(g, "_REFILLED_ELSEWHERE", ()) or ())


def pool_path(salon):
    file_key = g.normalize_threads_id(
        salon.get("Threadsのアカウント名（@から始まるID）", "")) or salon.get("サロン名", "")
    safe = g.re.sub(r"[^\w\-]", "_", file_key)
    return file_key, os.path.join(g.POSTS_DIR, f"posts_{safe}.json")


def counts(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None, {}
    return d, {s: len(d.get(s, [])) for s in SLOTS}


def collect():
    salons = []
    for label, fn in (("サロン", g.load_sheet_data), ("スクール", g.load_school_data),
                      ("ナポリ", g.load_napori_data)):
        try:
            got = fn()
        except Exception as e:
            print(f"[rebuild] {label}のシートが読めません: {str(e)[:120]}")
            continue
        print(f"[rebuild] {label}のシート: {len(got)}件")
        salons += got
    local = g.load_local_salons()
    print(f"[rebuild] ローカル定義: {len(local)}件")
    salons += local
    out, seen = [], set()
    for s in salons:
        key = g.normalize_threads_id(s.get("Threadsのアカウント名（@から始まるID）", "")) \
            or s.get("サロン名", "")
        if not key or key in seen:
            continue
        seen.add(key)
        if key in SKIP_ALWAYS:
            print(f"[rebuild] {key}: ここでは作り直さない（別のスクリプトが担当）")
            continue
        if ONLY and key not in ONLY and s.get("サロン名") not in ONLY:
            continue
        if key in SKIP or s.get("サロン名") in SKIP:
            print(f"[rebuild] {key}: SKIP 指定のため飛ばします")
            continue
        out.append(s)
    return out


def rebuild_one(salon):
    """1アカウント分。(結果の文字列, 作り直せたか) を返す。"""
    file_key, path = pool_path(salon)
    data, before = counts(path)
    if data is None:
        return f"{file_key}: 在庫ファイルが読めないので飛ばしました（{path}）", False
    if not any(before.values()):
        return f"{file_key}: 在庫が0本なので作り直しの対象外（通常の補充にまかせます）", False

    print(f"\n===== {file_key} 作り直し開始（前: {before}）=====")
    ok_rounds = 0
    for i in range(1, ROUNDS + 1):
        try:
            g.generate_for_salon(salon)
            ok_rounds += 1
        except Exception as e:
            # 1アカウントの失敗で全体を止めない。古い在庫は残るので投稿は続く
            print(f"[rebuild] {file_key} {i}/{ROUNDS}回目で失敗: {str(e)[:150]}")
        _, now = counts(path)
        print(f"[rebuild] {file_key} {i}/{ROUNDS} 終了 → 今 {now}")

    data, after = counts(path)
    if data is None:
        return f"{file_key}: 生成後に在庫ファイルが読めません（要確認）", False
    new = {s: after.get(s, 0) - before.get(s, 0) for s in SLOTS}
    print(f"[rebuild] {file_key} 新しく作れた本数: {new}（生成成功 {ok_rounds}/{ROUNDS}回）")

    thin = [s for s in SLOTS if before.get(s, 0) > 0 and new.get(s, 0) < MIN_NEW]
    if thin:
        return (f"{file_key}: {thin} の新しい分が{MIN_NEW}本に届かないので、"
                f"古い在庫は落とさずそのままにしました（新 {new}）"), False

    for s in SLOTS:
        if before.get(s, 0) and len(data.get(s, [])) > before[s]:
            data[s] = data[s][before[s]:]
    g._save_pool(path, data)
    _, final = counts(path)
    return f"{file_key}: 作り直し完了（前 {before} → 後 {final}）", True


def main():
    salons = collect()
    if not salons:
        print("[rebuild] 対象のアカウントがありません", file=sys.stderr)
        return 1
    print(f"\n[rebuild] 対象 {len(salons)}件 / ROUNDS={ROUNDS} / MIN_NEW={MIN_NEW}")

    results, done = [], 0
    for salon in salons:
        try:
            msg, ok = rebuild_one(salon)
        except Exception as e:
            msg, ok = f"{salon.get('サロン名','?')}: 想定外の失敗 {str(e)[:150]}", False
        print(f"[rebuild] {msg}")
        results.append(msg)
        done += 1 if ok else 0

    print("\n================ まとめ ================")
    for r in results:
        print(" ・" + r)
    print(f"\n作り直せたアカウント: {done}/{len(salons)}")
    # 1件でも作り直せていれば成功扱い。落とせなかった分は古い在庫が残っているだけで、
    # 投稿は止まらない（ここで失敗にすると無用な🚨が飛ぶ）
    return 0


if __name__ == "__main__":
    sys.exit(main())
