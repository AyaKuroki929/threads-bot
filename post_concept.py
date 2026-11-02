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
