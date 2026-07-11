#!/usr/bin/env python3
"""
投稿ストックが残り THRESHOLD 本以下のスロットに対して
Claude API で新規投稿を GENERATE_COUNT 本自動生成して posts.json に追記する。

使い方:
  python3 generate_posts.py bemolle            # posts.json / used_posts.json
  python3 generate_posts.py personal           # posts_personal.json / used_posts_personal.json
  python3 generate_posts.py saas_bemolle       # posts_saas/posts_bemolle_diet.json / Supabase
  python3 generate_posts.py saas_personal      # posts_saas/posts_aya_0929_private.json / Supabase
"""

import json
import os
import sys
import urllib.request
import urllib.parse

THRESHOLD = 5
GENERATE_COUNT = 12
_BASE = os.path.dirname(os.path.abspath(__file__))

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


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
    needed_any = False  # 補充が必要なスロットが1つでもあったか（全滅exit判定用）

    all_slots = [s for s in ["morning", "morning2", "noon", "evening2", "evening"] if s in posts]
    for slot in all_slots:
        remaining = _remaining(posts, used, slot)
        if remaining > THRESHOLD:
            print(f"[generate] {account} {slot}: 残{remaining}本 → 生成不要")
            continue
        needed_any = True

        print(f"[generate] {account} {slot}: 残{remaining}本 ≤ {THRESHOLD} → {GENERATE_COUNT}本生成開始")

        slot_hint = {
            "morning": "朝投稿（7:30頃配信）。1日の始まりに読む人向け。前向きな気づき・軽い問いかけ・背中を押す内容が向く。",
            "morning2": "朝2本目投稿（9:00頃配信）。朝イチより少し落ち着いた時間。具体的なTips・保存型・チェックリスト系が向く。",
            "noon": "昼投稿（12:00頃配信）。移動中・休憩中に読む人向け。共感しやすい体験談・サロンあるある・具体的な失敗談が向く。",
            "evening2": "夜1本目投稿（21:00前後配信）。帰宅後・夕食後の時間。今日起きた気づき・お客様エピソード・共感系が向く。",
            "evening": "夜投稿（21:00頃配信）。1日の終わりに読む人向け。内省・本音・静かな気づき・今日学んだことが向く。",
        }[slot]

        existing_samples = "\n".join([
            str(posts[slot][i])[:80] for i in range(min(5, len(posts[slot])))
        ])

        if slot == "noon":
            output_format = """=== 出力形式（厳守）===
必ずJSON配列だけを返してください。各要素は以下のいずれか：
- 単発投稿: "投稿本文"（文字列）
- ツリー投稿: ["1部目の本文", "2部目の本文"]（文字列の配列）
  ※ツリー投稿の1部目末尾を「予告文」で終えること絶対禁止。予告文とは「これから何を話すか」を宣言するメタ発言全般：
    ❌「続きに読んでください」「続きを書きます」「〜を書きます」「〜を話します」「〜をお伝えします」「〜を紹介します」「私の考えを書きます」「正直に書きます」など、文末が「書きます/話します/伝えます/紹介します」で終わる文は全てNG。
    ✅ 正しいクリフハンガー＝話を始めてしまい、一番気になるところで止める。
    例：❌「その時、何より先に考えたことを書きます。」
    　　✅「正直、お店の存続を考えるべき場面でした。でも私の頭に最初に浮かんだのは、売上でも採用でもありませんでした。」

出力例:
[
  "単発投稿の本文。改行は\\nで表現。",
  ["ツリー1部目。", "ツリー2部目。"],
  "別の単発投稿。"
]

JSON配列以外の文字は一切出力しないでください。説明文も不要です。"""
        else:
            output_format = """=== 出力形式（厳守）===
必ずJSON配列だけを返してください。各要素は「単発投稿の本文（文字列）」のみ。
★この時間帯（昼以外）はツリー投稿（配列）を絶対に作らないこと。必ず単発投稿にすること。ツリーは昼(noon)だけ。

出力例:
[
  "単発投稿の本文。改行は\\nで表現。",
  "別の単発投稿。"
]

JSON配列以外の文字は一切出力しないでください。説明文も不要です。"""

        system_cached = f"""あなたはThreads投稿の専門家です。
以下のルールに厳密に従って、{account}アカウントの投稿を生成してください。

=== 投稿生成ルール ===
{rules}"""

        system_dynamic = f"""=== 時間帯の特性（{slot}） ===
{slot_hint}

=== 医療・成分・学術名称の取り扱い（最優先ルール）===
成分名（ビタミン・ミネラル等）・学術名称・数値データは、上記のサロン情報に明記されているもののみ使用すること。
サロン情報に記載のない成分名・効能・数値・学術名称を創作・推測して書くことは絶対禁止。
週次インサイトに「成分名を列挙せよ」「学術名称を強調せよ」等のルールがあっても、サロン情報に根拠がない場合は無視すること。

{output_format}"""

        system_prompt = [
            {"type": "text", "text": system_cached, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": system_dynamic},
        ]

        user_prompt = f"""{GENERATE_COUNT}本の投稿を生成してください。

必ず守ること：
- ギャップ投稿（期待と現実のズレ）を半数以上に入れる（「〇〇なのに△△」「〇〇じゃない、実は△△」）
- W型ストーリー（失敗→成功→失敗→成功）を複数本に使う
- 黒木さんの実体験を素材にする：
  ・ピギー（子豚）というあだ名を10代の頃からつけられていた・彼氏（今の主人）と体重が同じになるほど太っていた
  ・20年以上ダイエットを続けて失敗し続けた
  ・病院でも原因不明と言われ続けたイボ・ニキビ
  ・スタッフが一気に辞めて仕事が回らなくなった経験
  ・計画なし・お金の知識なしで起業した
  ・美容業界でAI活用している人が他にいなかったから自分で作った
- 同じ素材・同じ角度の投稿を繰り返さない（{GENERATE_COUNT}本全部違う切り口にする）
- **保存型投稿を1〜2本必ず含める**：「〇〇の3つの間違い」「チェックリスト」「〇〇する人としない人の違い」など。末尾に「スクショ保存しておくと便利です」を入れる
- **あるある共感型を1〜2本必ず含める**：「40代ダイエットあるある」「こんな経験ありませんか？」など。ターゲットが「私のことだ」と感じる具体的な描写にする
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

            # 昼(noon)以外はツリー禁止：万一配列で来たら結合して単発化（保険）
            if slot != "noon":
                new_posts = [
                    "\n\n".join(str(x) for x in p) if isinstance(p, list) else p
                    for p in new_posts
                ]

            # 最終バリデーション: 空/短すぎ/薬機法NG語/プロンプト漏れ/ハングルを
            # プールに入れる前に落とす（プロンプト任せにしない最後の防波堤）
            from botlib import validate_post_content
            valid_posts = []
            for p in new_posts:
                reason = validate_post_content(p)
                if reason:
                    print(f"[generate] {slot}: 検証NGで除外 → {reason}: {str(p)[:60]}")
                else:
                    valid_posts.append(p)
            new_posts = valid_posts
            if not new_posts:
                print(f"[generate] {slot}: 全件検証NG → このスロットは追加なし")
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

    # 補充が必要だったのに1本も生成できなかった＝このままではプールが枯渇して
    # 投稿が止まる。exit 0 で静かに終わらず、異常終了してワークフローの通知に乗せる。
    if needed_any and not generated_any:
        print(f"[generate] {account}: 補充が必要なスロットに1本も生成できませんでした → 異常終了", file=sys.stderr)
        sys.exit(1)

    return generated_any


def _supabase_used_count(salon_name: str, slot: str) -> int:
    """Supabaseのpost_logsから使用済み投稿数を取得（SaaSモード用）"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return 0
    try:
        h = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        url = (f"{SUPABASE_URL}/rest/v1/salons"
               f"?salon_name=eq.{urllib.parse.quote(salon_name)}&select=id&limit=1")
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=10) as r:
            rows = json.loads(r.read())
        if not rows:
            return 0
        salon_id = rows[0]["id"]
        url = (f"{SUPABASE_URL}/rest/v1/post_logs"
               f"?salon_id=eq.{salon_id}&slot=eq.{slot}&select=id")
        req = urllib.request.Request(url, headers={
            **h, "Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            cr = r.headers.get("Content-Range", "")
            return int(cr.split("/")[1]) if "/" in cr else 0
    except Exception as e:
        print(f"[generate/saas] Supabase使用数取得エラー ({salon_name}/{slot}): {e}")
        return 0


def generate_for_saas(salon_name: str, posts_file: str, rules_file: str):
    """SaaSモード: posts_saas/から読んでSupabaseで残数確認、生成してposts_saas/に書き戻す"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("[generate/saas] ANTHROPIC_API_KEY が未設定 → スキップ")
        return False

    posts_path = os.path.join(_BASE, posts_file)
    if not os.path.exists(posts_path):
        print(f"[generate/saas] {posts_file} が見つかりません → スキップ")
        return False

    posts = json.load(open(posts_path, encoding="utf-8"))
    rules = _load_rules(rules_file)

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    generated_any = False

    for slot in ["morning", "noon", "evening"]:
        if slot not in posts:
            continue
        total = len(posts[slot])
        used_count = _supabase_used_count(salon_name, slot)
        remaining = total - (used_count % total) if total > 0 else 0
        if remaining > THRESHOLD:
            print(f"[generate/saas] {salon_name} {slot}: 残{remaining}本 → 生成不要")
            continue

        print(f"[generate/saas] {salon_name} {slot}: 残{remaining}本 ≤ {THRESHOLD} → {GENERATE_COUNT}本生成開始")

        slot_hint = {
            "morning": "朝投稿（7:30頃配信）。1日の始まりに読む人向け。前向きな気づき・軽い問いかけ・背中を押す内容が向く。",
            "noon": "昼投稿（12:00頃配信）。移動中・休憩中に読む人向け。共感しやすい体験談・サロンあるある・具体的な失敗談が向く。",
            "evening": "夜投稿（21:00頃配信）。1日の終わりに読む人向け。内省・本音・静かな気づき・今日学んだことが向く。",
        }[slot]

        existing_samples = "\n".join([
            str(posts[slot][i])[:80] for i in range(min(5, len(posts[slot])))
        ])

        if slot == "noon":
            output_format = """=== 出力形式（厳守）===
必ずJSON配列だけを返してください。各要素は以下のいずれか：
- 単発投稿: "投稿本文"（文字列）
- ツリー投稿: ["1部目の本文", "2部目の本文"]（文字列の配列）
  ※ツリー投稿の1部目末尾を「予告文」で終えること絶対禁止。「〜を書きます」「〜を話します」「〜をお伝えします」「〜を紹介します」「正直に書きます」など、内容を宣言するメタ発言は全てNG。話を始めてしまい、一番気になるところで止めること。

出力例:
[
  "単発投稿の本文。改行は\\nで表現。",
  ["ツリー1部目。", "ツリー2部目。"],
  "別の単発投稿。"
]

JSON配列以外の文字は一切出力しないでください。説明文も不要です。"""
        else:
            output_format = """=== 出力形式（厳守）===
必ずJSON配列だけを返してください。各要素は「単発投稿の本文（文字列）」のみ。
★この時間帯（昼以外）はツリー投稿（配列）を絶対に作らないこと。

出力例:
[
  "単発投稿の本文。改行は\\nで表現。",
  "別の単発投稿。"
]

JSON配列以外の文字は一切出力しないでください。説明文も不要です。"""

        system_prompt = [
            {"type": "text", "text": f"""あなたはThreads投稿の専門家です。
以下のルールに厳密に従って、{salon_name}アカウントの投稿を生成してください。

=== 投稿生成ルール ===
{rules}""", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": f"""=== 時間帯の特性（{slot}） ===
{slot_hint}

=== 医療・成分・学術名称の取り扱い（最優先ルール）===
成分名・学術名称・数値データは、上記のサロン情報に明記されているもののみ使用すること。
サロン情報に記載のない成分名・効能・数値・学術名称を創作・推測して書くことは絶対禁止。

{output_format}"""},
        ]

        user_prompt = f"""{GENERATE_COUNT}本の投稿を生成してください。

必ず守ること：
- ギャップ投稿（期待と現実のズレ）を半数以上に入れる（「〇〇なのに△△」「〇〇じゃない、実は△△」）
- W型ストーリー（失敗→成功→失敗→成功）を複数本に使う
- 黒木さんの実体験を素材にする：
  ・ピギー（子豚）というあだ名を10代の頃からつけられていた・彼氏（今の主人）と体重が同じになるほど太っていた
  ・20年以上ダイエットを続けて失敗し続けた
  ・病院でも原因不明と言われ続けたイボ・ニキビ
  ・スタッフが一気に辞めて仕事が回らなくなった経験
  ・計画なし・お金の知識なしで起業した
  ・美容業界でAI活用している人が他にいなかったから自分で作った
- 同じ素材・同じ角度の投稿を繰り返さない（{GENERATE_COUNT}本全部違う切り口にする）
- **保存型投稿を1〜2本必ず含める**：「〇〇の3つの間違い」「チェックリスト」など。末尾に「スクショ保存しておくと便利です」を入れる
- **あるある共感型を1〜2本必ず含める**：ターゲットが「私のことだ」と感じる具体的な描写
- **返信誘導投稿を1〜2本必ず含める**：末尾に二択・共感確認・悩み募集のどれかを入れる
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
                print(f"[generate/saas] {slot}: JSONが見つからない → スキップ")
                continue
            new_posts = json.loads(raw[start:end])
            if not isinstance(new_posts, list) or len(new_posts) == 0:
                print(f"[generate/saas] {slot}: 不正な形式 → スキップ")
                continue
            if slot != "noon":
                new_posts = [
                    "\n\n".join(str(x) for x in p) if isinstance(p, list) else p
                    for p in new_posts
                ]
            posts[slot].extend(new_posts)
            print(f"[generate/saas] {salon_name} {slot}: {len(new_posts)}本追加（合計{len(posts[slot])}本）")
            generated_any = True
        except Exception as e:
            print(f"[generate/saas] {salon_name} {slot}: 生成エラー → スキップ: {e}")
            continue

    if generated_any:
        with open(posts_path, "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)
        print(f"[generate/saas] {posts_file} を更新しました")

    return generated_any


if __name__ == "__main__":
    account = sys.argv[1] if len(sys.argv) > 1 else "bemolle"

    if account == "bemolle":
        generate_for_account("bemolle", "posts.json", "used_posts.json", "GENERATE_RULES.md")
    elif account == "personal":
        generate_for_account("personal", "posts_personal.json", "used_posts_personal.json", "GENERATE_RULES_personal.md")
    elif account == "saas_bemolle":
        generate_for_saas("bemolle_diet", "posts_saas/posts_bemolle_diet.json", "GENERATE_RULES.md")
    elif account == "saas_personal":
        # Supabaseの実サロン名は aya_kuroki_0929。旧名 aya_0929_private のファイルに
        # 補充し続けて実プール(posts_aya_kuroki_0929.json)が枯渇していく事故の修正（2026-07-11）
        generate_for_saas("aya_kuroki_0929", "posts_saas/posts_aya_kuroki_0929.json", "GENERATE_RULES_personal.md")
    else:
        print(f"[generate] 不明なアカウント: {account}")
        sys.exit(1)
