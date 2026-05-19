#!/usr/bin/env python3
"""
Threadsサブスクシステム - サロン別投稿生成スクリプト

Googleスプレッドシートからサロン情報を読み込み、
Claude APIでそのサロン専用の投稿文を生成してJSONに保存する。

使い方:
  python3 generate_saas_posts.py               # 全サロン処理
  python3 generate_saas_posts.py "ベモーレ"    # 指定サロンのみ
"""

import json
import os
import sys
import re
import urllib.request
import urllib.parse
from pathlib import Path

import anthropic
import gspread
from google.oauth2.service_account import Credentials

SPREADSHEET_ID = "1Af6ZnH7Ghzn1APpVrFy5nFlftNaIX5YOeSvfFjSdU-U"
SERVICE_ACCOUNT_FILE = os.path.join(os.path.dirname(__file__), "google_service_account.json")
POSTS_DIR = os.path.join(os.path.dirname(__file__), "posts_saas")
GENERATE_COUNT = 15
THRESHOLD = 5

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def load_sheet_data():
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws = sh.get_worksheet(0)
    records = ws.get_all_records()
    return records


def salon_to_rules(salon: dict) -> str:
    """スプレッドシートの1行をGENERATE_RULES形式のテキストに変換する"""

    tone_map = {
        "丁寧・落ち着いた（ですます調）": "丁寧・落ち着いたですます調。断言は控えめに。",
        "親しみやすい・フレンドリー": "親しみやすく、温かみがある。でも馴れ合いにならない程度の敬語。",
        "プロとして言い切る・強め": "自信を持って言い切る。「ハッキリ言います」「正直に書きます」などの強い導入も使う。",
        "柔らかく共感ベース": "まず共感から入る。「わかります」「私もそうでした」の温度感。",
    }
    emoji_map = {
        "使わない（または最小限）": "絵文字は使わない。または最小限（1〜2個まで）。",
        "適度に使う": "絵文字を適度に使う（投稿あたり2〜4個程度）。",
        "たくさん使う": "絵文字を積極的に使う（投稿あたり5個以上OK）。",
    }

    salon_name = salon.get("サロン名", "")
    owner_name = salon.get("オーナー名（投稿で使うお名前）", "")
    self_pronoun = salon.get("投稿で自分を何と呼びますか？", "私") or "私"
    location = salon.get("所在地（最寄り駅・徒歩時間）", "")
    hours = salon.get("営業時間", "")
    holiday = salon.get("定休日", "")
    threads_id = salon.get("Threadsのアカウント名（@から始まるID）", "")
    homepage_url = salon.get("ホームページURL", "")
    instagram_id = salon.get("InstagramアカウントID（@から・あれば）", "") or salon.get("インスタグラムのURL", "")
    hotpepper_url = salon.get("ホットペッパービューティーのURL", "")
    reservation_source = salon.get("予約はどこから受けていますか？（メインの予約先）", "")
    line_url = salon.get("LINE公式アカウントのURL（あれば）", "")
    career = salon.get("業界経歴・年数", "")
    open_year = salon.get("サロンのオープン年（何年目ですか）", "")
    staff_info = salon.get("スタッフ人数と特徴（一人運営の場合は一人と記入）", "")
    target = salon.get("メインターゲット（年代・性別・どんな悩みを持つ人）", "")
    new_visitor_concern = salon.get("新規のお客様が最初に来る一番多い理由・悩みは何ですか？", "")
    menu = salon.get("提供メニューと価格帯（箇条書きでOK）", "")
    price_ok = salon.get("価格を投稿に記載してもOKですか？", "")
    best_menu = salon.get("一番の売りメニュー・最も結果が出やすい施術", "")
    diff = salon.get("他サロンとの違い・このサロンならではの施術やこだわり", "")
    results = salon.get("お客様の具体的な変化・実績（数字があれば）", "")
    testimonials = (salon.get("印象的なお客様の声・体験談（名前不要・1～3件）", "")
                    or salon.get("印象的なお客様の声・体験談（名前不要・1〜3件）", ""))
    testimonials_ok = salon.get("お客様の声・体験談を投稿に使ってもOKですか？", "")
    result_timeline = (salon.get("結果が出るまでの一般的な目安（例：「○回・○ヶ月で変化を感じる方が多い」）", "")
                       or salon.get("結果が出るまでの一般的な目安（例：〇回・〇ヶ月で変化を感じる方が多い）", ""))
    owner_fail = salon.get("過去の失敗・コンプレックス（「実はこんなだった」という意外な過去）", "")
    turning_point = salon.get("転換点・気づき（何をきっかけに変わったか）", "")
    own_result = salon.get("自分自身で出た成果・結果（数字・具体エピソード）", "") or salon.get("自分自身で出た成果・結果", "")
    why_salon = salon.get("なぜこのサロンを作ったか", "")
    qualifications = salon.get("取得した資格・受講した研修・認定など（あれば）", "")
    owner_habits = salon.get("オーナー自身が毎日続けているケア・習慣（業種に関連するもの）", "")
    owner_lifestyle = salon.get("趣味・ライフスタイル（オーナー自身・業種と無関係でもOK）", "")
    common_mistakes = salon.get("お客様がよくやりがちな間違い・勘違い（3つ程度）", "")
    owner_privacy = salon.get("オーナー自身について投稿に出してOKな情報・出してほしくない情報", "")
    misunderstanding = salon.get("サービスについてお客様に誤解されやすいこと・過去にクレームや問い合わせでこじれた表現", "") or salon.get("過去にクレームになったこと・誤解されやすいこと", "")
    allowed_terms = salon.get("投稿に含めてよい成分名・施術名・商品名（ここに書いたもの以外は使いません）", "")
    post_style = salon.get("特に力を入れてほしい投稿スタイル（当てはまるものをすべて記入）", "")
    post_goal = salon.get("投稿の最終ゴールとして最も重視すること（1つ）", "")
    churn_pattern = salon.get("途中で来なくなるお客様に多いパターン・理由", "")
    seasonal_focus = salon.get("特に集客を強化したい季節・月（あれば）", "")
    competitor_diff = salon.get("お客様がよく比較検討する他サービスや選択肢（ジム・通販・他サロン等）", "")
    stance = salon.get("お客様への向き合い方・こだわり", "")
    ng_words = salon.get("サロンとして「言いたくないこと」「NGワード」", "")
    sns_ng_style = salon.get("自分の発信スタイルのNGライン（やりたくない表現・雰囲気・言葉づかい）", "")
    catchcopy = salon.get("サロンを一言で表すキャッチコピー（あれば）", "")
    tone = tone_map.get(salon.get("投稿のトーン", ""), "丁寧・落ち着いたですます調。")
    emoji = emoji_map.get(salon.get("絵文字を使いますか？", ""), "絵文字は使わない。")
    booking_url = salon.get("予約先のURL（投稿末尾に誘導するリンク）", "")
    notes = salon.get("その他・自由記入（伝えておきたいこと）", "")

    rules = f"""# {salon_name} Threads投稿生成ルール

## 発信者プロフィール
- **サロン名**：{salon_name}
- **オーナー名**：{owner_name}
- **場所**：{location}
- **営業時間**：{hours}（定休日：{holiday}）
- **Threads**：{threads_id}
{f"- **Instagram**：{instagram_id}" if instagram_id else ""}
{f"- **LINE公式**：{line_url}" if line_url else ""}
{f"- **ホームページ**：{homepage_url}" if homepage_url else ""}
{f"- **ホットペッパービューティー**：{hotpepper_url}" if hotpepper_url else ""}
{f"- **経歴**：{career}" if career else ""}
{f"- **開業**：{open_year}" if open_year else ""}
{f"- **スタッフ**：{staff_info}" if staff_info else ""}

## Threadsの目的
ターゲット層（{target}）に響く投稿でフォロワーを増やし、予約・来店につなげる。
エンゲージメント（いいね・保存・返信）が増える投稿を最優先する。

## ターゲット
{target}

{f"新規来店の主な理由・悩み：{new_visitor_concern}" if new_visitor_concern else ""}

## メニュー・サービス
{menu}

一番の売り：{best_menu}

{f"他サロンとの違い・こだわり：{diff}" if diff else ""}

## お客様の実績・体験談

### 変化・実績（数字）
{results}

{f"### 印象的なお客様の声（そのまま引用可）{chr(10)}{testimonials}" if testimonials else ""}

{f"### 結果が出るまでの目安{chr(10)}{result_timeline}" if result_timeline else ""}

## オーナーのストーリー（投稿の核・最重要）

### 過去の失敗・コンプレックス（ギャップ素材）
{owner_fail}

### 転換点・気づき
{turning_point}

### 自分自身で出た成果（具体的数字・エピソード）
{own_result}

### なぜこのサロンを作ったか
{why_salon}

{f"### 取得資格・研修（権威性・信頼訴求の素材）{chr(10)}{qualifications}" if qualifications else ""}

{f"### オーナー自身が毎日続けているケア・習慣（ライフスタイル投稿の素材）{chr(10)}{owner_habits}" if owner_habits else ""}

{f"### 趣味・ライフスタイル（人柄投稿の素材）{chr(10)}{owner_lifestyle}" if owner_lifestyle else ""}

## よくある間違い・業界の誤解（刺す投稿・保存型投稿の素材）
{common_mistakes if common_mistakes else "（未記入）"}

{f"## 競合・比較検討される選択肢（差別化投稿の素材）{chr(10)}お客様がよく比較するもの：{competitor_diff}{chr(10)}→ これらと比較したときのベネフィットを投稿素材として活用する。" if competitor_diff else ""}

{f"## 来なくなるパターン・離脱防止（継続訴求投稿の素材）{chr(10)}{churn_pattern}{chr(10)}→ このパターンを先回りして解消する投稿を定期的に入れる。" if churn_pattern else ""}

{f"## 集客強化時期（季節性投稿の素材）{chr(10)}{seasonal_focus}の時期は特に来店誘導を強化する投稿を増やす。" if seasonal_focus else ""}

## サロンの理念・お客様への向き合い方
{stance}

## キャッチコピー
{catchcopy if catchcopy else "（未設定）"}

## 価格・体験談の投稿使用ルール（クライアント指定）
{f"価格記載：{price_ok}" if price_ok else "価格記載ルール：未指定"}
{f"体験談使用：{testimonials_ok}" if testimonials_ok else "体験談使用ルール：未指定"}

## NGワード・避けるべき表現（クライアント指定）
{ng_words if ng_words else "特になし"}

{f"## 発信スタイルのNGライン（クライアント指定）{chr(10)}{sns_ng_style}" if sns_ng_style else ""}

{f"## 誤解を招く表現・クレーム防止（必ず守る）{chr(10)}{misunderstanding}" if misunderstanding else ""}

{f"## 使ってよい成分名・施術名・商品名（これ以外は創作しない）{chr(10)}{allowed_terms}" if allowed_terms else ""}

{f"## オーナー自身についてのOK/NG情報{chr(10)}{owner_privacy}" if owner_privacy else ""}

{f"## 投稿スタイルの優先度（クライアント指定）{chr(10)}{post_style}" if post_style else ""}

{f"## 投稿の最終ゴール（CTAの方向性）{chr(10)}{post_goal}に向けてCTAを組み立てる。" if post_goal else ""}

## 予約先
{f"予約受付：{reservation_source}" if reservation_source else ""}
{booking_url if booking_url else "プロフィールのリンクから"}

{f"## Instagram誘導（20本に3〜4本の割合で末尾に自然に入れる）{chr(10)}「{instagram_id} では施術写真・ビフォーアフターを載せています。」など自然な形で添える。{chr(10)}「フォローしてください」と直接書かない。" if instagram_id else ""}

{f"## LINE誘導（月2〜3本の割合で末尾に入れる）{chr(10)}{line_url} への誘導を自然に入れる。" if line_url else ""}

{f"## ホットペッパービューティー誘導（月1〜2本の割合で末尾に自然に入れる）{chr(10)}{hotpepper_url} への誘導を自然に入れる。「口コミ・メニュー詳細はこちら」など。" if hotpepper_url else ""}

## トーン・スタイル
{tone}
{emoji}
ハッシュタグは付けない。
短い文の改行を多用しThreadsで読みやすくする。

## 投稿の構造（ギャップ技法・常時適用）
- タイトル行で「え？」と思わせるズレを作る：「〇〇なのに△△」「〇〇じゃない、実は△△」
- オーナーの過去の失敗を素材にしたギャップを積極的に使う
- 失敗→転換点→成果のW型ストーリーを複数本に使う
- 「よくある間違い」を使って保存型まとめ投稿（〇選、チェックリスト）を定期的に入れる
- お客様の声を冒頭フックに使う投稿を複数本入れる

## その他メモ
{notes if notes else "なし"}

---

## 【全サロン共通・必ず守るルール】（クライアントの要望より優先）

### 一人称・サロンの呼び方
- 「うち」「うちの」「うちに」は一切使わない
- サロンを指すときは「{salon_name}」または「当サロン」を使う
  - ✅「{salon_name}に来ていただけたら」「{salon_name}のお客様」「当サロンでは」
  - ❌「うちに来てくれたら」「うちのお客様」「うちでは」
- 自分（オーナー）を指すときは「{self_pronoun}」を使う

### 敬語ルール
- 基本は「です・ます」調。文末は「〜です」「〜ます」「〜ました」「〜でしょうか」で統一
- お客様の行動には必ず敬語・謙譲語を使う
  - ✅「来ていただけたら」「お越しください」「通ってくださっている方」「ご相談ください」
  - ❌「来てくれたら」「通ってくれてる人」「相談してね」
- 断言・攻めた導入はOKだが、文末は敬語で着地させる
- タメ口・ぞんざいな語尾は絶対禁止
  - ❌「〜だよ」「〜してね」「〜じゃん」「〜なんだ」
  - ✅「〜なんですよ」「〜してください」「〜ですよね」「〜なんです」

### ネガティブ表現の禁止
- 他サロン・他業者・他の方法を批判・否定しない
- 読み手が不安になるだけで終わる表現を使わない（必ず希望・解決策で着地させる）
- 人格否定・見下し・悪口になる表現は一切使わない
- 方法・やり方を否定することはOK。「人」や「選択」を否定しない

### AI臭の排除
- 形式ワード禁止：「〜することが大切です」「〜していきましょう」「ぜひ〜してみてください」「いかがでしょうか」「ポイントは3つ」「まずは〜から」「日々の積み重ね」
- 整いすぎた見出し→3項目→まとめ構造を使わない
- 「自分を大切に」「無理せず」「自分らしく」など誰でも書ける抽象フレーズは禁止
- 同じ語尾を3文以上繰り返さない
- 文の長さにバラつきをつける（短文と長文を混在させる）

### 営業時間・定休日（絶対遵守）
- 営業時間（{hours}）・定休日（{holiday}）と矛盾する表現は絶対に使わない
  - ❌「夜でも」「深夜でもOK」「週末も」「定休日でも」「24時間」など
- 営業時間外への予約受付・来店誘導は一切書かない

### 禁則（全サロン絶対遵守）
- 医療的な断定・病気が治るなどの表現禁止
- 「絶対」「100%」などの誇大表現は避ける（「ほとんど」「多くの場合」等に言い換える）
- 政治・宗教・占いの断定は避ける
- ハッシュタグ禁止
"""
    return rules


def _get_supabase_used_count(salon_name: str, slot: str) -> int:
    """Supabaseのpost_logsから実際の使用済み投稿数を取得する。"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return 0
    try:
        # サロンIDを取得
        url = (f"{SUPABASE_URL}/rest/v1/salons"
               f"?salon_name=eq.{urllib.parse.quote(salon_name)}&select=id&limit=1")
        req = urllib.request.Request(url, headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.loads(r.read())
        if not rows:
            return 0
        salon_id = rows[0]["id"]
        # 使用済み投稿数をContent-Rangeヘッダーで取得
        url = (f"{SUPABASE_URL}/rest/v1/post_logs"
               f"?salon_id=eq.{salon_id}&slot=eq.{slot}&select=id")
        req = urllib.request.Request(url, headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Prefer": "count=exact",
            "Range-Unit": "items",
            "Range": "0-0",
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            cr = r.headers.get("Content-Range", "")
            # "0-0/15" or "*/15" format
            if "/" in cr:
                return int(cr.split("/")[1])
        return 0
    except Exception as e:
        print(f"[saas] Supabase使用数取得エラー ({salon_name}/{slot}): {e}")
        return 0


def _remaining(posts, salon_name, slot):
    total = len(posts.get(slot, []))
    used_count = _get_supabase_used_count(salon_name, slot)
    return total - used_count


def generate_for_salon(salon: dict):
    salon_name = salon.get("サロン名", "unknown")
    safe_name = re.sub(r'[^\w\-]', '_', salon_name)

    Path(POSTS_DIR).mkdir(exist_ok=True)
    posts_path = os.path.join(POSTS_DIR, f"posts_{safe_name}.json")

    if os.path.exists(posts_path):
        posts = json.load(open(posts_path, encoding="utf-8"))
    else:
        posts = {"morning": [], "noon": [], "evening": []}

    rules = salon_to_rules(salon)

    # 週次リサーチから更新される共通インサイトを追加
    saas_rules_path = os.path.join(os.path.dirname(__file__), "GENERATE_RULES_saas.md")
    if os.path.exists(saas_rules_path):
        with open(saas_rules_path, encoding="utf-8") as f:
            weekly_insights = f.read()
        rules += f"\n\n{weekly_insights}"

    try:
        client = anthropic.Anthropic()
    except Exception as e:
        print(f"[saas] Anthropic APIキーが取得できません → {e}")
        return False
    generated_any = False

    for slot in ["morning", "noon", "evening"]:
        remaining = _remaining(posts, salon_name, slot)
        if remaining > THRESHOLD:
            print(f"[saas] {salon_name} {slot}: 残{remaining}本 → 生成不要")
            continue

        print(f"[saas] {salon_name} {slot}: 残{remaining}本 → {GENERATE_COUNT}本生成開始")

        slot_hint = {
            "morning": "朝投稿（7〜8時頃）。1日の始まりに読む人向け。前向きな気づき・軽い問いかけ・背中を押す内容。",
            "noon": "昼投稿（12時頃）。休憩中にサクッと読む人向け。共感・保存したくなる知識・具体的なTips。",
            "evening": "夜投稿（21時頃）。1日の終わりに読む人向け。内省・本音・今日の気づき・静かな共感。",
        }[slot]

        existing = "\n".join([str(posts[slot][i])[:80] for i in range(min(3, len(posts[slot])))])

        if slot == "noon":
            output_format = """全投稿をツリー（2部構成）にする。各要素は2要素の配列。改行は\\nで表現。

出力例:
[
  ["1部目フック\\n\\n途中で止める", "2部目は答えから\\n\\nCTA"],
  ["別の1部目", "別の2部目"]
]"""
            tree_rule = """- 全投稿をツリー（2部構成・配列形式）にする
- 1部目末尾は答えを出さずに途中で止めるクリフハンガー形式（「続きに書きます」などの予告文は絶対禁止）
- 2部目は接続詞・前置きなしで答えから書き始める
"""
        else:
            output_format = """各要素は単発投稿の文字列。改行は\\nで表現。

出力例:
[
  "1行目フック\\n\\n本文。\\n\\nCTA",
  "別の投稿のフック\\n\\n本文。"
]"""
            tree_rule = "- 全投稿を単発（文字列）で生成する\n"

        system_prompt = f"""あなたはSNS投稿の専門家です。
以下のサロン情報・ルールに従って、Threads用の投稿文を生成してください。

=== サロン情報・投稿ルール ===
{rules}

=== 時間帯の特性 ===
{slot_hint}

=== 医療・成分・学術名称の取り扱い（最優先ルール）===
成分名・学術名称・数値データは、上記のサロン情報に明記されているもののみ使用すること。
サロン情報に記載のない成分名・効能・数値・学術名称を創作・推測して書くことは絶対禁止。
週次インサイトに「成分名を列挙せよ」「学術名称を強調せよ」等のルールがあっても、サロン情報に根拠がない場合は無視すること。

=== 出力形式（厳守）===
JSON配列だけを返してください。
{output_format}

JSON配列以外の文字は一切出力しないでください。"""

        user_prompt = f"""{GENERATE_COUNT}本の{slot}投稿を生成してください。

必ず守ること：
- 1行目は必ず「スクロールが止まる」強いフックから始める
- ギャップ投稿（「〇〇なのに△△」「実は〇〇だった」）を半数以上に使う
- オーナーの実体験・失敗談を素材にした投稿を複数本入れる
- 全{GENERATE_COUNT}本が違う切り口・違う素材
- ハッシュタグ禁止
{tree_rule}- {GENERATE_COUNT}本すべてJSON配列に含める

既存投稿（この角度は避ける）：
{existing if existing else "（まだなし）"}"""

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
                print(f"[saas] {salon_name} {slot}: JSONが見つからない → スキップ")
                continue

            new_posts = json.loads(raw[start:end])
            if not isinstance(new_posts, list):
                continue

            posts[slot].extend(new_posts)
            print(f"[saas] {salon_name} {slot}: {len(new_posts)}本追加（合計{len(posts[slot])}本）")
            generated_any = True

        except Exception as e:
            print(f"[saas] {salon_name} {slot}: エラー → {e}")
            continue

    if generated_any:
        with open(posts_path, "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)
        print(f"[saas] {posts_path} を更新しました")

    return generated_any


def main():
    target_salon = sys.argv[1] if len(sys.argv) > 1 else None

    print("[saas] スプレッドシートを読み込み中...")
    try:
        salons = load_sheet_data()
    except Exception as e:
        print(f"[saas] スプレッドシート読み込みエラー: {e}")
        print("[saas] google_service_account.json が設定されているか確認してください")
        sys.exit(1)

    print(f"[saas] {len(salons)}件のサロンを検出")

    for salon in salons:
        salon_name = salon.get("サロン名", "")
        if not salon_name:
            continue
        if target_salon and salon_name != target_salon:
            continue
        print(f"\n[saas] === {salon_name} の処理開始 ===")
        generate_for_salon(salon)

    print("\n[saas] 全処理完了")


if __name__ == "__main__":
    main()
