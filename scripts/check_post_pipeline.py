#!/usr/bin/env python3
"""「作り直したファイル」と「実際に配信されるファイル」が同じかを機械で確かめる。

2026-11-02の事故：個人アカウントの在庫を作り直したが、書いた先は posts_personal.json、
配信が読むのは posts_saas/posts_aya_kuroki_0929.json だった。名前が似ているので人の
目視では見分けられず、古いコンセプトの投稿が流れ続けた。

ここで見るのは4つ：
  ① 配信（post_saas.py）が読むパス
  ② 補充（generate_posts.py の saas_personal）が書くパス
  ③ 作り直し（rebuild_personal_pool.py）が書くパス
  ④ 作り直しワークフローが commit するパス
全部が ① と一致していなければ異常終了する。

加えて、配信される在庫に発信コンセプトの印（_concept）があり、ルールの版と
一致しているかも見る（古い在庫が残っていないか）。
"""
import json
import os
import re
import sys

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE)
import post_concept  # noqa: E402

SALON = "aya_kuroki_0929"
problems = []


def read(path):
    with open(os.path.join(_BASE, path), encoding="utf-8") as f:
        return f.read()


# ① 配信が読むパス（post_saas.py の組み立て方をそのまま使う）
import post_saas  # noqa: E402
delivered = os.path.relpath(
    os.path.join(post_saas.POSTS_DIR, f"posts_{post_saas._safe_name(SALON)}.json"), _BASE)
print(f"① 配信が読む     : {delivered}")

# ② 補充が書くパス
gen = read("generate_posts.py")
m = re.search(r'generate_for_saas\(\s*"%s"\s*,\s*"([^"]+)"' % SALON, gen)
refill = m.group(1) if m else None
print(f"② 補充が書く     : {refill}")
if refill != delivered:
    problems.append(f"補充（generate_posts.py saas_personal）の書き先 {refill} が配信 {delivered} と違う")

# ③ 作り直しが書くパス
reb = read("rebuild_personal_pool.py")
m = re.search(r'POOL\s*=\s*os\.path\.join\(_BASE,\s*(.+?)\)\s*(?:#|$)', reb, re.M)
parts = re.findall(r'"([^"]+)"', m.group(1)) if m else []
rebuild = "/".join(parts) if parts else None
print(f"③ 作り直しが書く : {rebuild}")
if rebuild != delivered:
    problems.append(f"作り直し（rebuild_personal_pool.py の POOL）{rebuild} が配信 {delivered} と違う")

# ④ 作り直しワークフローが commit するパス
wf = read(".github/workflows/personal_regen.yml")
m = re.search(r'git add ([^\n]+)', wf)
committed = m.group(1).split() if m else []
print(f"④ workflowがcommit: {' '.join(committed)}")
if delivered not in committed:
    problems.append(f"personal_regen.yml の git add に配信ファイル {delivered} が入っていない"
                    f"（作り直してもGitHubに残らない）")

# ⑤ 配信される在庫のコンセプトが今のルールと合っているか
try:
    data = json.load(open(os.path.join(_BASE, delivered), encoding="utf-8"))
except (OSError, ValueError) as e:
    problems.append(f"配信ファイルを読めない: {e}")
else:
    stale = post_concept.mismatch(SALON, data)
    counts = {k: len(v) for k, v in post_concept.slots_only(data).items()}
    print(f"⑤ 在庫のコンセプト: {data.get(post_concept.KEY)!r} / 本数 {counts}")
    if stale:
        problems.append(stale)
    for slot, n in counts.items():
        if n == 0:
            problems.append(f"配信ファイルの {slot} が0本")

# ⑥ 名乗りが設定され、配信時にちゃんと付くか（在庫には焼き込まない方式のため）
sig = post_concept.signature_for(SALON)
print(f"⑥ 名乗り          : {sig!r}")
if not sig:
    problems.append(f"{SALON} の名乗りが post_concept.SIGNATURE_FOR_SALON に無い")
else:
    sample = ["本文のはじまり", "2部目"]
    got = post_concept.add_signature(SALON, sample)
    if not got[0].startswith(sig):
        problems.append("post_concept.add_signature が1部目の冒頭に名乗りを付けていない")
    if post_concept.add_signature(SALON, got)[0] != got[0]:
        problems.append("post_concept.add_signature が名乗りを二重に付けている")
    if not post_concept.has_signature(SALON, got):
        problems.append("post_concept.has_signature が付いた名乗りを認識しない")
    if post_concept.has_signature(SALON, sample):
        problems.append("post_concept.has_signature が名乗りの無い本文を通してしまう")
    # 投稿側がその付与と確認を実際に通しているか（消えたら名乗り無しで流れ続ける）
    ps = read("post_saas.py")
    for call in ("post_concept.add_signature(salon_name, texts)",
                 "post_concept.has_signature(salon_name, texts)"):
        if call not in ps:
            problems.append(f"post_saas.py の投稿経路に {call} が無い")
    # 在庫の本文に名乗りを焼き込んでいないか（焼き込むと二重になり、変更時に作り直しになる）
    try:
        pool = json.load(open(os.path.join(_BASE, delivered), encoding="utf-8"))
    except (OSError, ValueError):
        pool = {}
    baked = [p for v in post_concept.slots_only(pool).values() for p in v
             if sig in str(p[0] if isinstance(p, list) else p)]
    if baked:
        problems.append(f"在庫の本文に名乗りが書き込まれている（{len(baked)}本）。"
                        f"配信時に付くので本文には書かない")

# ⑦ 個人アカウントの補充経路が1本か（2本あると片方が古いルールで在庫を上書きする）
gs = read("generate_saas_posts.py")
if f'"{SALON}"' not in gs.split("_REFILLED_ELSEWHERE = ")[-1][:120]:
    problems.append("generate_saas_posts.py の _REFILLED_ELSEWHERE に "
                    f"{SALON} が入っていない（補充経路が2本になり、古いルールの在庫が戻る）")
if os.path.exists(os.path.join(_BASE, "rules_custom", f"{SALON}.md")):
    problems.append(f"rules_custom/{SALON}.md が復活している（GENERATE_RULES_personal.md と"
                    "ルールが二重管理になる。古い名乗りが戻った原因）")
print(f"⑦ 補充経路        : generate_posts.py saas_personal の1本のみ")

if problems:
    print("\n".join(f"::error::{p}" for p in problems))
    print(f"\n配線に問題が {len(problems)} 件あります", file=sys.stderr)
    sys.exit(1)
print("\n配線OK：作り直す先・補充する先・配信が読む先が全部同じで、在庫のコンセプトも今のルール通りです")
