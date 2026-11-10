"""本番と同じルールで投稿を試し作りして、ログに出すだけのプレビュー。

ファイルへ書き込まず・コミットせず・投稿もしない。本番プールは汚さない。

  python3 preview_gen.py                         # 既定 urakata_san_official
  python3 preview_gen.py syoko_happy_salon       # Threads ID か サロン名
  python3 preview_gen.py all                     # 運用中の全アカウントを1本ずつ

⚠️ ルール（build_rules）とプロンプト（build_prompts）は本番の generate_saas_posts.py と
同じ関数を呼ぶ。ここで見た形が、そのまま本番で作られる形になる。
B2B（うらかたさん）はシート不要。シート由来（サロン・スクール）は
GOOGLE_SERVICE_ACCOUNT_JSON が要る。
"""
import os
import re
import sys

import anthropic

from generate_saas_posts import (
    build_prompts,
    build_rules,
    load_local_salons,
    load_napori_data,
    load_school_data,
    load_sheet_data,
    normalize_threads_id,
    _ask_model,
    _keep_valid_shape,
)
import post_concept

COUNT = int(os.environ.get("PREVIEW_COUNT") or 2)
SLOTS = (os.environ.get("PREVIEW_SLOTS") or "morning,noon,evening").split(",")


def collect_salons():
    salons = []
    try:
        salons += load_sheet_data() + load_school_data() + load_napori_data()
    except Exception as e:
        print(f"[preview] シートが読めません（ローカル定義だけで続けます）: {e}")
    salons += load_local_salons()
    return salons


def key_of(salon):
    tid = normalize_threads_id(salon.get("Threadsのアカウント名（@から始まるID）", ""))
    return tid or salon.get("サロン名", "")


def preview(salon, client):
    file_key = key_of(salon)
    safe_name = re.sub(r"[^\w\-]", "_", file_key)
    rules, _ = build_rules(salon, safe_name, file_key)
    print("\n" + "#" * 70)
    print(f"# {salon.get('サロン名', '')}（@{file_key}） 種別={salon.get('_type') or 'salon'}")
    print("#" * 70)
    for slot in SLOTS:
        slot = slot.strip()
        if not slot:
            continue
        system_prompt, user_prompt = build_prompts(rules, slot, COUNT)
        posts, err = _ask_model(client, system_prompt, user_prompt,
                                f"preview {file_key} {slot}", keep_fn=_keep_valid_shape)
        print(f"\n========== {slot} ==========")
        if not posts:
            print(f"（生成できませんでした: {err}）")
            continue
        for i, p in enumerate(posts, 1):
            parts = p if isinstance(p, list) else [p]
            # 配信されるときの形（名乗りが入るアカウントは入れて見せる）
            parts = post_concept.add_signature(file_key, parts)
            print(f"\n--- {i}本目 ---")
            for j, part in enumerate(parts, 1):
                print(f"【{j}部目】{len(part)}字")
                print(part)


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "urakata_san_official"
    salons = collect_salons()
    if not salons:
        print("[preview] アカウントが1件も読めませんでした")
        sys.exit(1)
    if target == "all":
        chosen = [s for s in salons if s.get("サロン名")]
    else:
        chosen = [s for s in salons
                  if key_of(s) == target or s.get("サロン名") == target]
    if not chosen:
        print(f"[preview] 見つかりません: {target}")
        print("[preview] 候補: " + ", ".join(sorted({key_of(s) for s in salons if key_of(s)})))
        sys.exit(1)
    client = anthropic.Anthropic()
    seen = set()
    for salon in chosen:
        k = key_of(salon)
        if k in seen:
            continue
        seen.add(k)
        preview(salon, client)
    print("\n################ END PREVIEW ################")


if __name__ == "__main__":
    main()
