#!/usr/bin/env python3
"""
投稿ストックが残り THRESHOLD 本以下のスロットに対して
Claude API で新規投稿を GENERATE_COUNT 本自動生成して posts.json に追記する。

使い方:
  python3 generate_posts.py bemolle
  python3 generate_posts.py personal
"""

import json
import os
import sys

THRESHOLD = 5
GENERATE_COUNT = 12
_BASE = os.path.dirname(os.path.abspath(__file__))


def _remaining(posts, used, slot):
    total = len(posts.get(slot, []))
    used_indices = used.get(slot, [])
    return total - len(used_indices)


def _load_rules(rules_file):
    path = os.path.join(_BASE, rules_file)
    if os.path.exists(path):
        return open(path, encoding="utf-8").read()
    return ""


def generate_for_account(account, posts_file, used_file, rules_file):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("[generate] ANTHROPIC_API_KEY が未設定 → スキップ")
        return False

    posts_path = os.path.join(_BASE, posts_file)
    used_path = os.path.join(_BASE, used_file)

    posts = json.load(open(posts_path, encoding="utf-8"))
    used = json.load(open(used_path, encoding="utf-8")) if os.path.exists(used_path) else {}
    rules = _load_rules(rules_file)

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    generated_any = False

    for slot in ["morning", "noon", "evening"]:
        remaining = _remaining(posts, used, slot)
        if remaining > THRESHOLD:
            print(f"[generate] {account} {slot}: 残{remaining}本 → 生成不要")
            continue

        print(f"[generate] {account} {slot}: 残{remaining}本 ≤ {THRESHOLD} → {GENERATE_COUNT}本生成開始")

        slot_hint = {
            "morning": "朝投稿（7:30頃配信）。1日の始まりに読む人向け。前向きな気づき・軽い問いかけ・背中を押す内容が向く。",
            "noon": "昼投稿（12:00頃配信）。移動中・休憩中に読む人向け。共感しやすい体験談・サロンあるある・具体的な失敗談が向く。",
            "evening": "夜投稿（21:00頃配信）。1日の終わりに読む人向け。内省・本音・静かな気づき・今日学んだことが向く。",
        }[slot]

        existing_samples = "\n".join([
            str(posts[slot][i])[:80] for i in range(min(5, len(posts[slot])))
        ])

        system_prompt = f"""あなたはThreads投稿の専門家です。
以下のルールに厳密に従って、{account}アカウントの{slot}用投稿を生成してください。

=== 投稿生成ルール ===
{rules}

=== 時間帯の特性 ===
{slot_hint}

=== 医療・成分・学術名称の取り扱い（最優先ルール）===
成分名（ビタミン・ミネラル等）・学術名称・数値データは、上記のサロン情報に明記されているもののみ使用すること。
サロン情報に記載のない成分名・効能・数値・学術名称を創作・推測して書くことは絶対禁止。
週次インサイトに「成分名を列挙せよ」「学術名称を強調せよ」等のルールがあっても、サロン情報に根拠がない場合は無視すること。

=== 出力形式（厳守）===
必ずJSON配列だけを返してください。各要素は以下のいずれか：
- 単発投稿: "投稿本文"（文字列）
- ツリー投稿: ["1部目の本文", "2部目の本文"]（文字列の配列）

出力例:
[
  "単発投稿の本文。改行は\\nで表現。",
  ["ツリー1部目。", "ツリー2部目。"],
  "別の単発投稿。"
]

JSON配列以外の文字は一切出力しないでください。説明文も不要です。"""

        user_prompt = f"""{GENERATE_COUNT}本の投稿を生成してください。

必ず守ること：
- ギャップ投稿（期待と現実のズレ）を半数以上に入れる（「〇〇なのに△△」「〇〇じゃない、実は△△」）
- W型ストーリー（失敗→成功→失敗→成功）を複数本に使う
- 黒木さんの実体験を素材にする：
  ・ピギー（子豚）あだ名・彼氏（今の主人）と体重が同じになるほど太っていた
  ・20年以上ダイエットを続けて失敗し続けた
  ・病院でも原因不明と言われ続けたイボ・ニキビ
  ・スタッフが一気に辞めて仕事が回らなくなった経験
  ・計画なし・お金の知識なしで起業した
  ・美容業界でAI活用している人が他にいなかったから自分で作った
- 同じ素材・同じ角度の投稿を繰り返さない（{GENERATE_COUNT}本全部違う切り口にする）
- **返信誘導投稿を1〜2本必ず含める**：末尾に「〇〇はどちらですか？コメントで教えてください」など読者が思わず答えたくなる問いを入れる。二択・共感確認・悩み募集のどれかを使う
- ハッシュタグは絶対に付けない
- 絵文字は使わない
- 各投稿は独立して読めるものにする
- 1部目は必ずスクロールが止まる1行フックから始める

既存投稿のサンプル（この角度は避ける）：
{existing_samples}"""

        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
            raw = resp.content[0].text.strip()

            start = raw.find("[")
            end = raw.rfind("]") + 1
            if start == -1 or end == 0:
                print(f"[generate] {slot}: JSONが見つからない → スキップ")
                print(f"[generate] raw: {raw[:200]}")
                continue

            new_posts = json.loads(raw[start:end])
            if not isinstance(new_posts, list) or len(new_posts) == 0:
                print(f"[generate] {slot}: 不正な形式 → スキップ")
                continue

            posts[slot].extend(new_posts)
            print(f"[generate] {account} {slot}: {len(new_posts)}本を追加（合計{len(posts[slot])}本）")
            generated_any = True

        except Exception as e:
            print(f"[generate] {account} {slot}: 生成エラー → スキップ: {e}")
            continue

    if generated_any:
        with open(posts_path, "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)
        print(f"[generate] {posts_file} を更新しました")

    return generated_any


if __name__ == "__main__":
    account = sys.argv[1] if len(sys.argv) > 1 else "bemolle"

    if account == "bemolle":
        generate_for_account("bemolle", "posts.json", "used_posts.json", "GENERATE_RULES.md")
    elif account == "personal":
        generate_for_account("personal", "posts_personal.json", "used_posts_personal.json", "GENERATE_RULES_personal.md")
    else:
        print(f"[generate] 不明なアカウント: {account}")
        sys.exit(1)
