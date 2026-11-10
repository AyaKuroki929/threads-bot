#!/usr/bin/env python3
"""投稿ストックが「今の発信コンセプト」で作られた物かを機械が見分けるための印。

2026-11-02の事故：個人アカウント（@aya_kuroki_0929）のコンセプトを「削減」に変えて
在庫を作り直したのに、作り直した先が posts_personal.json、実際に配信されるのは
posts_saas/posts_aya_kuroki_0929.json だったため、古い名乗り（推しと結婚した…）の
投稿が流れ続けた。名前の似たファイルが2つあると人は間違える。

そこで「ルールの版」と「在庫の版」を突き合わせる。
  ・ルール側：GENERATE_RULES_*.md の冒頭に `<!-- concept: 2026-11-01-削減 -->`
  ・在庫側  ：生成時に posts_*.json の "_concept" に同じ値を書き込む
  ・配信側  ：食い違っていたら投稿しない（古い物を流すより、止めて知らせる方が良い）

ルールMDに concept の行が無いアカウントは、何も検査しない（従来どおり動く）。
"""
import os
import re

_BASE = os.path.dirname(os.path.abspath(__file__))

# サロン名 → 生成ルールのファイル（generate_posts.py の割り当てと合わせる）
RULES_FOR_SALON = {
    "aya_kuroki_0929": "GENERATE_RULES_personal.md",
}

KEY = "_concept"


def rules_concept(rules_file):
    """ルールMDに書かれたコンセプトの版。無ければ None。"""
    if not rules_file:
        return None
    try:
        with open(os.path.join(_BASE, rules_file), encoding="utf-8") as f:
            head = f.read(4000)
    except OSError:
        return None
    m = re.search(r"<!--\s*concept:\s*(.+?)\s*-->", head)
    return m.group(1).strip() if m else None


def concept_for_salon(salon_name):
    return rules_concept(RULES_FOR_SALON.get(salon_name))


def stamp(posts, rules_file):
    """生成・作り直しのあと、在庫に今の版を押す。辞書をそのまま返す。"""
    concept = rules_concept(rules_file)
    if concept and isinstance(posts, dict):
        posts[KEY] = concept
    return posts


def mismatch(salon_name, posts):
    """配信前の照合。問題なければ None、食い違っていれば理由の文を返す。"""
    expected = concept_for_salon(salon_name)
    if not expected:
        return None
    found = posts.get(KEY) if isinstance(posts, dict) else None
    if found == expected:
        return None
    if not found:
        return (f"{salon_name}: 投稿ストックに発信コンセプトの印がありません"
                f"（ルールは「{expected}」）。作り直した在庫が配信ファイルに入っていない"
                f"恐れがあります。posts_saas/posts_{salon_name}.json を確認してください")
    return (f"{salon_name}: 投稿ストックのコンセプトが「{found}」で、"
            f"今のルール「{expected}」と違います。古いコンセプトの投稿を出さないよう止めました。"
            f"在庫を作り直してください（personal_regen.yml）")


SLOTS = ("morning", "noon", "evening")


def slots_only(posts):
    """枠（morning/noon/evening）だけを取り出す。_concept などの印を数に混ぜないため。"""
    return {k: v for k, v in posts.items() if k in SLOTS}


# ── 名乗り（シグネチャ）────────────────────────────────────────────────
# 投稿の1部目の冒頭に毎回付ける名乗り。在庫の本文には書かず、配信するときに付ける。
# そうしておけば、名乗りを変えたくなったときに在庫を全部作り直さなくて済む
# （2026-11-02 コンセプトを変えたら211本を作り直すことになった反省）。
SIGNATURE_FOR_SALON = {
    # 2026-11-02 彩さん決定。旧「推しと結婚した美容サロンオーナー、黒木です。」からの変更
    "aya_kuroki_0929": "サロンオーナーを早く帰らせたい黒木です。",
}


def signature_for(salon_name):
    return SIGNATURE_FOR_SALON.get(salon_name)


def add_signature(salon_name, texts):
    """1部目の「呼びかけ」の次の行に名乗りを入れる。

    2026-11-10 彩さん指示で位置を変更（旧：1部目のいちばん先頭）。
    名乗りだけが入り口だと毎回同じ出だしになってAIっぽい。
    「今日も遅くまでサロンに残ってる方、聞いてほしくて。」のような呼びかけを
    先に置き、その次に名乗る（鷺さんの指摘）。
    在庫の1部目は1行目が呼びかけになるよう生成している（GENERATE_RULES_conversation.md）。
    対象外・既に名乗っている本文はそのまま返す。
    """
    sig = signature_for(salon_name)
    if not sig or not texts:
        return texts
    first = str(texts[0] or "")
    # すでに名乗りがある本文に二度付けない。表記が少し違っても拾えるよう
    # 「黒木です」を目印にする（1部目の頭の方に出ていれば名乗りと見なす）
    if sig in first or "黒木です" in first[:120]:
        return texts
    greeting, _, rest = first.partition("\n")
    result = list(texts)
    result[0] = f"{greeting}\n{sig}\n{rest}" if rest else f"{greeting}\n{sig}"
    return result


def has_signature(salon_name, texts):
    """投稿直前の確認。名乗りが要るアカウントで、1部目に名乗りがあるか。"""
    sig = signature_for(salon_name)
    if not sig:
        return True
    # 呼びかけ1行のあとに名乗りが入るので、頭から80字では届かないことがある
    first = str((texts or [""])[0] or "")
    return sig in first[:200] or "黒木です" in first[:200]
