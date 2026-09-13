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
# ツリー1部目の長さ。ここがタイムラインに出る部分で、短いほど見られる（2026-09-13 実測）
FIRST_PART_MIN = 25
FIRST_PART_MAX = 160


def _valid_tree(p) -> bool:
    """保存してよい形か。ちょうど2部・1部目が短い・各部が長すぎない。"""
    if not isinstance(p, list) or len(p) != 2:
        return False
    if not all(isinstance(x, str) and x.strip() for x in p):
        return False
    return FIRST_PART_MIN <= len(p[0]) <= FIRST_PART_MAX and all(len(x) <= 400 for x in p)
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
    needed_slots, filled_slots = [], []   # 枠ごとに「要る」「足せた」を数える

    all_slots = [s for s in ["morning", "morning2", "noon", "evening2", "evening"] if s in posts]
    for slot in all_slots:
        remaining = _remaining(posts, used, slot)
        if remaining > THRESHOLD:
            print(f"[generate] {account} {slot}: 残{remaining}本 → 生成不要")
            continue
        needed_slots.append(slot)

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

        # ⚠️ 朝・昼・夜すべてツリー。本文が短いほど見られるのが実測で分かったため
        # （2026-09-13。〜100字は200字〜の6〜10倍。朝・昼・夜すべてで一致）。
        # 以前は「ツリーは昼だけ」で、それが朝夜の表示回数を落としていた。
        output_format = """=== 出力形式（厳守）===
必ずJSON配列だけを返してください。各要素は **ちょうど2要素の配列**（ツリー投稿）です。
["1部目の本文", "2部目の本文"]
※1部目末尾を「予告文」で終えること絶対禁止。「〜を書きます」「〜を話します」
  「〜をお伝えします」「〜を紹介します」「正直に書きます」など、内容を宣言する
  メタ発言は全てNG。話を始めてしまい、一番気になるところで止めること。
※**1部目は60〜100字**。ここがタイムラインに出る部分で、短いほど読まれる（実測）。
※2部目は120〜250字。

出力例:
[
  ["1部目フック。改行は\nで表現。", "2部目は答えから。"],
  ["別の1部目。", "別の2部目。"]
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
- 黒木さんの実体験を素材にする：素材は上記ルール内の「黒木さん自身のストーリー」「ギャップ一覧」に書かれているものだけを使う（このアカウントのテーマに無関係な素材は使わない）。時期・経緯・程度を盛らない・変えない（ルール内の⚠️厳守注記に従う）
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

            # ⚠️ ここで配列を1本につなげると、せっかくの短い1部目が200字超の
            # 単発に戻る（2026-09-13 Sol指摘）。つなげず、形が違う物を落とす。
            kept = [x for x in new_posts if _valid_tree(x)]
            if len(kept) < len(new_posts):
                print(f"[generate] {slot}: 形か長さが基準外 {len(new_posts) - len(kept)}本を除外"
                      f"（ちょうど2部・1部目{FIRST_PART_MIN}〜{FIRST_PART_MAX}字）")
            new_posts = kept
            if not new_posts:
                print(f"[generate] {slot}: 追加なし")
                continue

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
            filled_slots.append(slot)

        except Exception as e:
            print(f"[generate] {account} {slot}: 生成エラー → スキップ: {e}")
            continue

    if generated_any:
        with open(posts_path, "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)
        print(f"[generate] {posts_file} を更新しました")

    # 補充が必要だったのに1本も生成できなかった＝このままではプールが枯渇して
    # 投稿が止まる。exit 0 で静かに終わらず、異常終了してワークフローの通知に乗せる。
    # ⚠️ 全体で1枠でも成功すると通ってしまうと、朝夜の失敗に気づけない（Sol指摘）。
    # 補充が要る枠ごとに見る。
    missed = [s_ for s_ in needed_slots if s_ not in filled_slots]
    if missed:
        print(f"[generate] {account}: 補充できなかった枠 {', '.join(missed)} → 異常終了",
              file=sys.stderr)
        sys.exit(1)

    return generated_any


def _supabase_used_texts(salon_name: str) -> set:
    """このサロンで投稿済みの本文をすべて集める（SaaSモード用）。

    ⚠️ 件数だけで数えると、12本中12本使用済みでも
    `total - used % total` が 12 になり「残12本→補充不要」と誤判定する
    （2026-09-13 Sol指摘）。本文そのもので突き合わせる。"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return set()
    h = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    try:
        url = (f"{SUPABASE_URL}/rest/v1/salons"
               f"?salon_name={urllib.parse.quote('eq.' + salon_name)}&select=id&limit=1")
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=15) as r:
            rows = json.loads(r.read())
        if not rows:
            return set()
        sid = rows[0]["id"]
        used, page, offset = set(), 1000, 0
        while True:
            url = (f"{SUPABASE_URL}/rest/v1/post_logs?salon_id=eq.{sid}"
                   f"&select=post_content&order=id.asc&limit={page}&offset={offset}")
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=20) as r:
                chunk = json.loads(r.read())
            used.update(x.get("post_content") or "" for x in chunk)
            if len(chunk) < page:
                return used
            offset += page
    except Exception as e:
        print(f"[generate/saas] Supabase使用済み取得エラー ({salon_name}): {e}")
        return used if "used" in dir() else set()


def _supabase_used_count(salon_name: str, slot: str) -> int:
    """Supabaseのpost_logsから使用済み投稿数を取得（SaaSモード用・旧方式）"""
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

    used_texts = _supabase_used_texts(salon_name)

    def _key(p):
        return p if isinstance(p, str) else (p[0] if p else "")

    needed, filled = [], []
    for slot in ["morning", "noon", "evening"]:
        if slot not in posts:
            continue
        remaining = len([p for p in posts[slot] if _key(p) not in used_texts])
        if remaining > THRESHOLD:
            print(f"[generate/saas] {salon_name} {slot}: 残{remaining}本 → 生成不要")
            continue
        needed.append(slot)

        print(f"[generate/saas] {salon_name} {slot}: 残{remaining}本 ≤ {THRESHOLD} → {GENERATE_COUNT}本生成開始")

        slot_hint = {
            "morning": "朝投稿（7:30頃配信）。1日の始まりに読む人向け。前向きな気づき・軽い問いかけ・背中を押す内容が向く。",
            "noon": "昼投稿（12:00頃配信）。移動中・休憩中に読む人向け。共感しやすい体験談・サロンあるある・具体的な失敗談が向く。",
            "evening": "夜投稿（21:00頃配信）。1日の終わりに読む人向け。内省・本音・静かな気づき・今日学んだことが向く。",
        }[slot]

        existing_samples = "\n".join([
            str(posts[slot][i])[:80] for i in range(min(5, len(posts[slot])))
        ])

        # ⚠️ 朝・昼・夜すべてツリー。本文が短いほど見られるのが実測で分かったため
        # （2026-09-13。〜100字は200字〜の6〜10倍。朝・昼・夜すべてで一致）。
        # 以前は「ツリーは昼だけ」で、それが朝夜の表示回数を落としていた。
        output_format = """=== 出力形式（厳守）===
必ずJSON配列だけを返してください。各要素は **ちょうど2要素の配列**（ツリー投稿）です。
["1部目の本文", "2部目の本文"]
※1部目末尾を「予告文」で終えること絶対禁止。「〜を書きます」「〜を話します」
  「〜をお伝えします」「〜を紹介します」「正直に書きます」など、内容を宣言する
  メタ発言は全てNG。話を始めてしまい、一番気になるところで止めること。
※**1部目は60〜100字**。ここがタイムラインに出る部分で、短いほど読まれる（実測）。
※2部目は120〜250字。

出力例:
[
  ["1部目フック。改行は\nで表現。", "2部目は答えから。"],
  ["別の1部目。", "別の2部目。"]
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
- 黒木さんの実体験を素材にする：素材は上記ルール内の「黒木さん自身のストーリー」「ギャップ一覧」に書かれているものだけを使う（このアカウントのテーマに無関係な素材は使わない）。時期・経緯・程度を盛らない・変えない（ルール内の⚠️厳守注記に従う）
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
            # ⚠️ ここで配列を1本につなげると、せっかくの短い1部目が200字超の
            # 単発に戻る（2026-09-13 Sol指摘）。つなげず、形が違う物を落とす。
            kept = [x for x in new_posts if _valid_tree(x)]
            if len(kept) < len(new_posts):
                print(f"[generate] {slot}: 形か長さが基準外 {len(new_posts) - len(kept)}本を除外"
                      f"（ちょうど2部・1部目{FIRST_PART_MIN}〜{FIRST_PART_MAX}字）")
            new_posts = kept
            if not new_posts:
                print(f"[generate] {slot}: 追加なし")
                continue
            posts[slot].extend(new_posts)
            print(f"[generate/saas] {salon_name} {slot}: {len(new_posts)}本追加（合計{len(posts[slot])}本）")
            generated_any = True
            filled.append(slot)
        except Exception as e:
            print(f"[generate/saas] {salon_name} {slot}: 生成エラー → スキップ: {e}")
            continue

    if generated_any:
        with open(posts_path, "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)
        print(f"[generate/saas] {posts_file} を更新しました")

    # ⚠️ 1枠でも成功すると全体が成功に見えてしまうと、朝夜の失敗に誰も気づかない。
    # 補充が要る枠のうち1本も足せなかった物があれば異常終了して通知に乗せる（Sol指摘）
    missed = [s_ for s_ in needed if s_ not in filled]
    if missed:
        print(f"[generate/saas] {salon_name}: 補充できなかった枠 {', '.join(missed)} → 異常終了",
              file=sys.stderr)
        sys.exit(1)

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
