#!/usr/bin/env python3
"""在庫の配線の見張り（scripts/check_post_pipeline.py）をわざと壊して、検知するか確かめる。

通るだけの点検は無意味なので、「壊したら落ちる」ことまで機械で確認する。
2026-11-02の事故：作り直した在庫 posts_personal.json と、配信が読む
posts_saas/posts_aya_kuroki_0929.json が別物で、古いコンセプトの投稿が流れ続けた。
ここが素通りになったら、その事故が黙って戻れる状態に戻っている。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK = os.path.join("scripts", "check_post_pipeline.py")

# 配線チェックが読むものだけを写す（リポジトリ全部をコピーしない）
NEEDED = [
    "post_concept.py", "post_saas.py", "post_state.py", "botlib.py",
    "generate_posts.py", "generate_saas_posts.py", "rebuild_personal_pool.py",
    "GENERATE_RULES_personal.md",
    "posts_saas/posts_aya_kuroki_0929.json",
    ".github/workflows/personal_regen.yml",
    CHECK,
]


def make_copy(dst):
    for rel in NEEDED:
        src = os.path.join(BASE, rel)
        out = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if os.path.exists(src):
            shutil.copy2(src, out)
    return dst


def run(root):
    env = {**os.environ, "PYTHONPATH": root}
    r = subprocess.run([sys.executable, CHECK], cwd=root, env=env,
                       capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)


def edit(root, rel, old, new):
    path = os.path.join(root, rel)
    s = open(path, encoding="utf-8").read()
    if old not in s:
        raise AssertionError(f"{rel} に書き換え対象が見つかりません: {old[:60]}")
    open(path, "w", encoding="utf-8").write(s.replace(old, new, 1))


def stale_concept(root):
    """在庫の印を古いコンセプトにする＝作り直し忘れ"""
    p = os.path.join(root, "posts_saas", "posts_aya_kuroki_0929.json")
    d = json.load(open(p, encoding="utf-8"))
    d["_concept"] = "2026-07-04-推し"
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False)


def no_concept(root):
    """印を消す＝どのルールで作ったか分からない在庫"""
    p = os.path.join(root, "posts_saas", "posts_aya_kuroki_0929.json")
    d = json.load(open(p, encoding="utf-8"))
    d.pop("_concept", None)
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False)


def rebuild_elsewhere(root):
    """作り直しの書き先を、配信されないファイルに戻す＝2026-11-02の事故そのもの"""
    edit(root, "rebuild_personal_pool.py",
         '"pool": os.path.join("posts_saas", "posts_aya_kuroki_0929.json"),',
         '"pool": "posts_personal.json",')


def refill_elsewhere(root):
    """補充の書き先だけが別ファイルになる＝配信プールが枯れていく（2026-07-11に実際に起きた型）"""
    edit(root, "generate_posts.py",
         'generate_for_saas("aya_kuroki_0929", "posts_saas/posts_aya_kuroki_0929.json"',
         'generate_for_saas("aya_kuroki_0929", "posts_saas/posts_aya_0929_private.json"')


def workflow_forgets_commit(root):
    """作り直してもGitHubに残らない（commit対象から配信ファイルが抜ける）"""
    edit(root, ".github/workflows/personal_regen.yml",
         "git add posts_saas/posts_aya_kuroki_0929.json posts_saas/posts_bemolle_diet.json posts_personal.json",
         "git add posts_personal.json")


def empty_slot(root):
    """配信ファイルの1枠が空＝その時間の投稿が出ない"""
    p = os.path.join(root, "posts_saas", "posts_aya_kuroki_0929.json")
    d = json.load(open(p, encoding="utf-8"))
    d["morning"] = []
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False)


def signature_removed(root):
    """名乗りの設定を消す＝名乗り無しで投稿され続ける"""
    edit(root, "post_concept.py",
         '"aya_kuroki_0929": "サロンオーナーを早く帰らせたい黒木です。",',
         "")


def signature_not_applied(root):
    """投稿経路から名乗りの付与が抜ける"""
    edit(root, "post_saas.py",
         "texts = post_concept.add_signature(salon_name, texts)",
         "pass  # 名乗りを付けない")


def signature_check_removed(root):
    """付与できたかの確認が抜ける（壊れても気づけない）"""
    edit(root, "post_saas.py",
         "if not post_concept.has_signature(salon_name, texts):",
         "if False:")


def signature_baked_in(root):
    """在庫の本文に名乗りを書き込む＝配信時の付与と二重になる"""
    p = os.path.join(root, "posts_saas", "posts_aya_kuroki_0929.json")
    d = json.load(open(p, encoding="utf-8"))
    d["morning"][0][0] = "サロンオーナーを早く帰らせたい黒木です。\n" + d["morning"][0][0]
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False)


def second_refill_path(root):
    """個人アカウントの補充経路が2本に戻る＝古いルールの在庫が混ざる"""
    edit(root, "generate_saas_posts.py",
         '_REFILLED_ELSEWHERE = {"aya_kuroki_0929"}',
         "_REFILLED_ELSEWHERE = set()")


def custom_rules_revived(root):
    """rules_custom に個人アカウント用の別ルールが復活する"""
    os.makedirs(os.path.join(root, "rules_custom"), exist_ok=True)
    with open(os.path.join(root, "rules_custom", "aya_kuroki_0929.md"), "w", encoding="utf-8") as f:
        f.write("# 旧ルール\n「推しと結婚した美容サロンオーナー、黒木です。」\n")


MUTATIONS = [
    ("在庫が古いコンセプトのまま", stale_concept),
    ("在庫にコンセプトの印が無い", no_concept),
    ("作り直しの書き先が配信されないファイル", rebuild_elsewhere),
    ("補充の書き先が配信されないファイル", refill_elsewhere),
    ("workflowが配信ファイルをcommitしない", workflow_forgets_commit),
    ("配信ファイルの朝が0本", empty_slot),
    ("名乗りの設定が消えた", signature_removed),
    ("投稿経路で名乗りを付けていない", signature_not_applied),
    ("名乗りが付いたかの確認が消えた", signature_check_removed),
    ("在庫の本文に名乗りが焼き込まれた", signature_baked_in),
    ("補充経路が2本に戻った", second_refill_path),
    ("rules_custom に別ルールが復活した", custom_rules_revived),
]


def main():
    failures = []

    with tempfile.TemporaryDirectory() as tmp:
        root = make_copy(os.path.join(tmp, "clean"))
        code, out = run(root)
        if code != 0:
            print("壊していない状態で落ちました。見張り自体がおかしいです:\n" + out, file=sys.stderr)
            return 1
        print("[基準] 壊していない状態では通る → OK")

    for name, mutate in MUTATIONS:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_copy(os.path.join(tmp, "broken"))
            mutate(root)
            code, out = run(root)
            if code == 0:
                failures.append(name)
                print(f"❌ 素通り: {name}")
            else:
                print(f"✅ 検知: {name}")

    if failures:
        print(f"\n{len(failures)}件が素通りしました: {failures}", file=sys.stderr)
        return 1
    print(f"\n{len(MUTATIONS)}件すべて検知しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
