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
# スクール版（晶子サロンアカデミー等）の回答シート。createSchoolForm 実行後の SPREADSHEET_ID をここに入れる。
# 空文字のままなら従来どおりサロンのみ処理（スクールはスキップ）。
SCHOOL_SPREADSHEET_ID = "1sX130Vbe0UBrCjkLuAM68UbfbLv4wVpAQmee9Od5kps"
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


def normalize_threads_id(raw: str) -> str:
    """Threads IDを正規化（全角＠・半角@・大文字・前後スペース・全角英数を吸収）"""
    if not raw:
        return ""
    s = str(raw).strip()
    # 全角英数記号 → 半角に変換
    s = s.translate(str.maketrans(
        "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ．＿－",
        "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz._-"
    ))
    # 先頭の @ または ＠ を全部除去（連続していても）
    while s and s[0] in ("@", "＠"):
        s = s[1:]
    # 末尾スペース除去・小文字化（Threads usernameは大文字小文字を区別しない）
    return s.strip().lower()


def load_sheet_data():
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws = sh.get_worksheet(0)
    records = ws.get_all_records()
    return records


def load_school_data():
    """スクール版の回答シートを読み込み、共通パイプラインが使うキーへエイリアスして返す。"""
    if not SCHOOL_SPREADSHEET_ID:
        return []
    try:
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SCHOOL_SPREADSHEET_ID)
        ws = sh.get_worksheet(0)
        records = ws.get_all_records()
    except Exception as e:
        print(f"[saas] スクールシート読み込みスキップ（共有設定/IDを確認）: {e}")
        return []
    out = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        rec["_type"] = "school"
        # 共通の識別キー（名前・Threads ID）へエイリアス
        if not rec.get("サロン名"):
            rec["サロン名"] = rec.get("スクール・アカデミー名", "")
        if not rec.get("Threadsのアカウント名（@から始まるID）"):
            rec["Threadsのアカウント名（@から始まるID）"] = rec.get("ThreadsのアカウントID（@から始まるID）", "")
        out.append(rec)
    return out


def load_local_salons():
    """フォーム回答シートを使わず、リポジトリ内の定義からサロンを読み込む。
    B2B（うらかたさん等）のように、シートではなくインタビュー内容を直接登録するアカウント用。
    各エントリは "_type" を持ち（例 "b2b"）、generate_for_salon でルート分岐される。"""
    path = os.path.join(os.path.dirname(__file__), "saas_local_salons.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[saas] ローカルサロン読み込みスキップ（{path}）: {e}")
        return []
    return data if isinstance(data, list) else []


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
    threads_id = normalize_threads_id(salon.get("Threadsのアカウント名（@から始まるID）", ""))
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
    churn_pattern = (salon.get("途中で来なくなるお客様に多いパターン・理由（わかる範囲でOK）", "")
                     or salon.get("途中で来なくなるお客様に多いパターン・理由", ""))
    seasonal_focus = salon.get("特に集客を強化したい季節・月（あれば）", "")
    competitor_diff = salon.get("お客様がよく比較検討する他サービスや選択肢（ジム・通販・他サロン等）", "")
    stance = salon.get("お客様への向き合い方・こだわり", "")
    ng_words = salon.get("サロンとして「言いたくないこと」「NGワード」", "")
    sns_ng_style = salon.get("自分の発信スタイルのNGライン（やりたくない表現・雰囲気・言葉づかい）", "")
    catchcopy = salon.get("サロンを一言で表すキャッチコピー（あれば）", "")
    owner_words = salon.get("オーナーの口癖・よく使う言葉・フレーズ", "")
    concept_words = salon.get("サロン独自のキーワード・コンセプトワード", "")
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

## このオーナー固有の言葉・文体の個性（差別化の核・必ず反映する）
{f"口癖・よく使う言葉：{owner_words}" if owner_words else "口癖：（未記入）"}
{f"独自コンセプトワード：{concept_words}" if concept_words else "独自コンセプトワード：（未記入）"}
→ 上記の言葉・表現を自然な形で投稿に織り込み、このオーナーにしか書けない文体を作ること。
→ 他のどのサロンとも被らない個性を出すことが最優先。一般的・テンプレート的な表現は避ける。

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
- **このサロン情報に「過去の失敗・コンプレックス」が具体的に書かれている場合のみ、それを軸に「失敗→転換点→成果」のW型ストーリーを複数本入れ、失敗ベースの投稿比率を高める（共感されやすく伸びやすい）**
- **書かれていない・内容が薄い場合は、失敗やコンプレックスを創作しない。お客様の声・実績・こだわり・よくある間違いを主軸にする**
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

### ホームケアに関する必須スタンス（全サロン絶対遵守）
- 「ホームケア商品は必要ない」「商品を買わなくても結果が出る」など、ホームケアを否定・軽視する表現は一切使わない
- サロンに来ていない日の方が圧倒的に多い。その日々の習慣・ホームケアこそがすべての結果を作るという考え方を基本とする
- 「サロンでの施術＋自宅でのホームケア」が一体となって結果につながるという表現をすること
- ホームケアで何をすればいいかをしっかりお伝えするサロンというスタンスで投稿する
- ✅ 例：「サロンに来ていない日の過ごし方が、実は一番大切です。」
- ❌ 禁止：「商品を買わなくても結果が出ます」「ホームケアは不要です」「余計なものは買わせません」

### 禁則（全サロン絶対遵守）
- 医療的な断定・病気が治るなどの表現禁止
- 「絶対」「100%」などの誇大表現は避ける（「ほとんど」「多くの場合」等に言い換える）
- 政治・宗教・占いの断定は避ける
- ハッシュタグ禁止
"""
    return rules


def b2b_to_rules(salon: dict) -> str:
    """B2B（サロンオーナー向けに商品・システムを売る）アカウントのルールに変換する。
    読者は美容消費者ではなく『サロンオーナー／これから起業する人』。
    美容サロン版（salon_to_rules）の前提（ホームケア・当サロン・営業時間・施術など）は一切適用しない。"""

    tone_map = {
        "丁寧・落ち着いた（ですます調）": "丁寧・落ち着いたですます調。断言は控えめに。",
        "親しみやすい・フレンドリー": "親しみやすく温かみがある敬語。馴れ合いにはしない。",
        "プロとして言い切る・強め": "自信を持って言い切る。ただし文末は敬語で着地。",
        "柔らかく共感ベース": "まず共感から入る。「わかります」「私もそうでした」の温度感。",
    }
    emoji_map = {
        "使わない（または最小限）": "絵文字は使わない。または最小限（1投稿に0〜2個まで）。",
        "適度に使う": "絵文字を適度に使う（投稿あたり2〜4個程度）。",
        "たくさん使う": "絵文字を積極的に使う。",
    }

    brand = salon.get("サロン名", "")
    self_pronoun = salon.get("投稿で自分を何と呼びますか？", "私") or "私"
    threads_id = normalize_threads_id(salon.get("Threadsのアカウント名（@から始まるID）", ""))
    homepage = salon.get("ホームページURL", "")
    line_url = salon.get("LINE公式アカウントのURL（あれば）", "")
    career = salon.get("業界経歴・年数", "")
    target = salon.get("メインターゲット（年代・性別・どんな悩みを持つ人）", "")
    new_concern = salon.get("新規のお客様が最初に来る一番多い理由・悩みは何ですか？", "")
    menu = salon.get("提供メニューと価格帯（箇条書きでOK）", "")
    price_ok = salon.get("価格を投稿に記載してもOKですか？", "")
    best = salon.get("一番の売りメニュー・最も結果が出やすい施術", "")
    diff = salon.get("他サロンとの違い・このサロンならではの施術やこだわり", "")
    results = salon.get("お客様の具体的な変化・実績（数字があれば）", "")
    testimonials = salon.get("印象的なお客様の声・体験談（名前不要・1〜3件）", "")
    testimonials_ok = salon.get("お客様の声・体験談を投稿に使ってもOKですか？", "")
    timeline = salon.get("結果が出るまでの一般的な目安", "")
    owner_fail = salon.get("過去の失敗・コンプレックス（「実はこんなだった」という意外な過去）", "")
    turning = salon.get("転換点・気づき（何をきっかけに変わったか）", "")
    own_result = salon.get("自分自身で出た成果・結果（数字・具体エピソード）", "")
    why = salon.get("なぜこのサロンを作ったか", "")
    mistakes = salon.get("お客様がよくやりがちな間違い・勘違い（3つ程度）", "")
    misunderstanding = salon.get("サービスについてお客様に誤解されやすいこと", "")
    allowed = salon.get("投稿に含めてよい成分名・施術名・商品名（ここに書いたもの以外は使いません）", "")
    style = salon.get("特に力を入れてほしい投稿スタイル（当てはまるものをすべて記入）", "")
    goal = salon.get("投稿の最終ゴールとして最も重視すること（1つ）", "")
    competitor = salon.get("お客様がよく比較検討する他サービスや選択肢（ジム・通販・他サロン等）", "")
    stance = salon.get("お客様への向き合い方・こだわり", "")
    ng = salon.get("サロンとして「言いたくないこと」「NGワード」", "")
    ng_style = salon.get("自分の発信スタイルのNGライン（やりたくない表現・雰囲気・言葉づかい）", "")
    catch = salon.get("サロンを一言で表すキャッチコピー（あれば）", "")
    habits = salon.get("オーナーの口癖・よく使う言葉・フレーズ", "")
    concept = salon.get("サロン独自のキーワード・コンセプトワード", "")
    tone = tone_map.get(salon.get("投稿のトーン", ""), "柔らかく共感ベース。")
    emoji = emoji_map.get(salon.get("絵文字を使いますか？", ""), "絵文字は使わない。または最小限。")
    notes = salon.get("その他・自由記入（伝えておきたいこと）", "")

    rules = f"""# {brand} Threads投稿生成ルール（B2B：サロンオーナー向け）

## 大前提（最優先）
- このアカウントの読者は**美容消費者ではなく、サロンのオーナー／これから開業する人**です。一般のお客様向け美容投稿は書きません。
- {brand}は美容サロンではなく、**サロンオーナーに提供するサービス／システム**です。施術・ホームケア・営業時間・予約の話は書きません。
- 発信者は「現役サロンオーナー」の立場。自分のことは「{self_pronoun}」と呼ぶ。**本名・自店名は出さない**。

## 発信者プロフィール
- ブランド名：{brand}
- 立場・経歴：{career}
- Threads：@{threads_id}
- ホームページ：{homepage}

## 何を届けるか（商品）
- 一番の売り：{best}
- 内容・料金：{menu}
- 価格の記載：{price_ok}
- 他との違い・こだわり：{diff}
- よく比較される選択肢：{competitor}

## 誰に届けるか（ターゲット）
- メインターゲット：{target}
- 見込み客が抱える悩み・きっかけ：{new_concern}

## 実績・お客様の声（数字は下記のもののみ使用可）
- 具体的な変化・実績：{results}
- お客様の声：{testimonials}（投稿利用：{testimonials_ok}・名前は出さない）
- 効果が出るまでの目安：{timeline}

## オーナーのストーリー（W型・最も伸びる素材）
- 過去の失敗・コンプレックス：{owner_fail}
- 転換点・気づき：{turning}
- 自分で出た成果：{own_result}
- なぜ作ったか：{why}

## 見込み客の勘違い・誤解（保存型・あるある投稿の素材）
- よくある勘違い：{mistakes}
- 誤解されやすいこと：{misunderstanding}

## トーン・文体
- トーン：{tone}
- 絵文字：{emoji}
- 口癖・よく使うフレーズ：{habits}
- コンセプトワード：{concept}
- キャッチコピー：{catch}
- 向き合い方：{stance}
- 力を入れる投稿スタイル：{style}

## 投稿の構造（ギャップ技法・常時適用）
- 1行目で「え？」と思わせるズレを作る（「〇〇なのに△△」「実は〇〇だった」）
- お客様の声を冒頭フックに使う投稿を複数本

## 失敗ストーリー重視（このアカウントの主軸）
- **全体の6〜7割を「失敗エピソード」起点の投稿にする**。残り3〜4割でお客様の声・気づき・保存型まとめを入れてリズムを作る。
- 形は必ず**W型（失敗→転換・気づき→今／解決）で1投稿に着地**させる。ただ落ち込むだけ・暗いだけで終わらせない。
- 失敗は抽象NG。**固有の場面・数字・感情**を入れて生々しく（「月末2〜3時間電卓」「通帳を見て青ざめた」など）。
- 失敗の**種類を散らす**（同じ失敗の言い換えを続けない）：①お金 ②時間 ③人（スタッフ）④思い込み ⑤道具（多機能で使えない）。
- 信用を毀損する失敗は書かない（売り物＝経営の目利き／システムの信頼を下げる話はNG）。

### 失敗エピソードバンク（実話・ここから素材を取る／創作しない）
- 【時間】営業後に毎晩レジ締め・売上集計・在庫確認で1時間。本来は接客に使いたい時間を計算に奪われていた。
- 【時間】現状把握のため夜な夜な電卓を叩いていた。
- 【人】スタッフによって締め作業の時間にバラつき。「締めはオーナーがやるもの」と思い込み、店を空けられなかった＝全部自分に属人化していた。
- 【お金】売上は悪くないのに通帳の残高が思ったより少なく青ざめた。原因は把握しきれていない在庫と細かい経費。
- 【思い込み】「小規模だから数字管理はまだいい」と後回しにしていた。その"なんとなく"が後で効いた。
- 【道具】高機能なPOSレジを入れたのに、機能が多すぎて結局ほとんど使えていなかった。
- 【時間・人】自動化・仕組み化の前は全部手作業で、スタッフの残業が増えていた。
- 【お金・機会損失】お客様が欲しがっていた商品の発注を忘れて欠品。さらにフォローも忘れているうちに、そのお客様は同じ商品を楽天で買っていた。気づかないうちに取りこぼしていた売上。
  - （※「スタッフが一気に辞めた」事件は個人アカウントの素材。うらかたでは固有の離職エピソードは扱わない。うらかたで使うのは「現場・数字が見えていなかった／属人化していた」という教訓の核のみ＝上の締め属人化バンクで担保。）

## NG・禁止（絶対遵守）
- ブランド名「{brand}」は**必ずこの表記のまま**使う（ひらがな指定があれば漢字化しない・呼び捨てにしない）
- 投稿本文にURL・リンクを直接貼らない。誘導は「プロフィールのLINEから」と書く（参考LINE：{line_url}）
- 他社サービス・他の方法・Excel等を露骨に批判・否定しない。「選択肢のひとつ」として触れ、人や選択を否定しない
- 数字・実績は上記「実績」に書かれたもののみ使用。創作・盛りは禁止
- 「絶対」「100%」など誇大表現は避ける（景品表示法。「多くの場合」「〜という声が多い」に言い換える）
- 投稿に含めてよい固有名詞：{allowed}（これ以外の商品・機能名を創作しない）
- NGワード・避けたい雰囲気：{ng}／{ng_style}
- 上から目線・マウント・過度な煽り・キラキラ営業トーンは禁止
- ハッシュタグ禁止。AI臭（「〜が大切です」「いかがでしょうか」「ポイントは3つ」等）禁止
- タメ口禁止・文末は敬語で着地

## ゴール
- {goal}
- 毎投稿をCTAにしない。価値提供・共感が主で、LINE誘導は文脈をつけて時々入れる。新規アカウントなので売り込み感を出さない。

## その他メモ
{notes if notes else "なし"}
"""
    return rules


def school_to_rules(school: dict) -> str:
    """スクール（サロン経営スクール）の1行をThreads投稿生成ルールに変換する。
    読者はエステサロンのオーナー。一般の美容客向けではない点がサロン版と決定的に異なる。"""

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

    def v(key, *alts):
        s = str(school.get(key, "") or "").strip()
        if not s:
            for a in alts:
                s = str(school.get(a, "") or "").strip()
                if s:
                    break
        if s in ("特になし", "なし", "ない", "特に無し", "無し"):
            return ""
        return s

    school_name = str(school.get("スクール・アカデミー名", "") or "").strip()
    teacher = str(school.get("講師名（投稿で使うお名前）", "") or "").strip()
    self_pronoun = (str(school.get("投稿で自分を何と呼びますか？", "") or "").strip() or "私")
    threads_id = normalize_threads_id(school.get("ThreadsのアカウントID（@から始まるID）", "")
                                      or school.get("Threadsのアカウント名（@から始まるID）", ""))
    other_sns = v("その他のSNS・HPのURL")

    goal_multi = v("このアカウントで一番達成したいこと（複数選択可）", "このアカウントで一番達成したいこと")
    goal_detail = v("目的の具体的な中身")
    reader_outcome = v("投稿を読んだサロンオーナーに最終的にどうなってほしいか")
    success_3m = v("3ヶ月後にこうなっていたら成功という状態")

    cta_actions = v("投稿の最後で読者にしてほしい行動（複数選択可）", "投稿の最後で読者にしてほしい行動")
    cta_detail = v("CTAの誘導先と具体的な文言・キーワード")
    dm_ok = v("DM・コメントでの相談は受け付けてOKですか？")
    cta_freq = v("CTAを出す頻度")

    target = v("どんなエステサロンオーナーに届けたいか")
    pain = v("そのオーナーが今一番抱えている悩み・痛み")
    not_target = v("こういう人には来てほしくないという人物像")

    why = v("なぜこのスクールを作ったか")
    failure = v("一番の失敗・どん底と、そこからどう立て直したか")
    highprice = v("「低単価で満席を目指すサロン」との違い（高単価で本当に結果を出すサロンとは）")
    retail_reason = v("物販・ホームケア教育を必須にする理由")
    menu_combo = v("メニューの組み合わせ方で大事にしている考え方")
    worklife = v("サロンオーナーが働き詰めにならず、時間とお金の余裕を持って働くために必要なこと")
    misbelief = v("サロンオーナーがよく持っている思い込み・勘違い")
    one_message = v("サロンオーナーに一番気づいてほしいことを一言で")

    courses = v("提供している講座・コースと価格")
    best_course = v("一番おすすめ・一番成果が出る講座とその理由")
    mechanism = v("投稿で触れてOKな講座の特徴的な仕組み")
    price_ok = v("価格を投稿に出してもOKですか？")

    testi_ok = v("受講生・卒業生の成果や体験談を投稿に使ってもOKですか？")
    results = v("印象的な受講生の成果・変化")
    voices = v("受講生からよく言われる感想・変化")

    diff = v("他のサロン経営スクール・コンサルとの違い・強み")
    competitor = v("受講を迷う人がよく比較する他の選択肢")
    churn = v("途中で挫折・離脱しやすい人のパターン")
    words = v("先生の口癖・よく使う言葉・フレーズ")
    concept = v("スクール独自のキーワード・コンセプトワード")
    tone = tone_map.get(school.get("投稿のトーン", ""), "丁寧・落ち着いたですます調。")
    emoji = emoji_map.get(school.get("絵文字をどうしますか？", ""), "絵文字は使わない。")
    ng_line = v("発信スタイルのNGライン")
    privacy = v("先生自身について投稿に出してOKな情報・出してほしくない情報")
    ng_words = v("スクールとして言いたくないこと・NGワード")

    rules = f"""# {school_name} Threads投稿生成ルール（スクール／サロン経営塾）

## 発信者プロフィール
- **スクール名**：{school_name}
- **講師名**：{teacher}
- **Threads**：{threads_id}
{f"- **その他SNS・HP**：{other_sns}" if other_sns else ""}

## このアカウントの読者（ターゲット）
読者は「エステサロンのオーナー（経営者）」です。一般の美容のお客様ではありません。
施術を受けに来てもらうための投稿ではなく、サロン経営に悩むオーナーの心を動かす投稿を書きます。
{f"届けたい相手：{target}" if target else ""}
{f"その人が今抱えている悩み・痛み：{pain}" if pain else ""}
{f"逆に来てほしくない人：{not_target}" if not_target else ""}

## このThreadsアカウントの目的
{f"一番達成したいこと：{goal_multi}" if goal_multi else ""}
{f"具体的には：{goal_detail}" if goal_detail else ""}
{f"読者に最終的にどうなってほしいか：{reader_outcome}" if reader_outcome else ""}
{f"成功の状態（3ヶ月後）：{success_3m}" if success_3m else ""}
→ 読者であるサロンオーナーの心を動かし、この目的につながる投稿を最優先する。
エンゲージメント（いいね・保存・返信）が増える投稿を最優先する。

## 講師の想い・哲学（投稿の核・最重要）
### なぜこのスクールを作ったか
{why if why else "（未記入）"}

{f"### 一番の失敗・どん底とそこからの立て直し（ギャップ素材・最強の発信材料）{chr(10)}{failure}" if failure else ""}

{f"### 「低単価で満席」ではなく「高単価で本当に結果を出すサロン」を教える理由{chr(10)}{highprice}{chr(10)}→ この信念を投稿の軸に据える。安売り・低単価を勧める内容は書かない。" if highprice else ""}

{f"### 物販・ホームケア教育を必須にする理由（重要な指導テーマ）{chr(10)}{retail_reason}{chr(10)}→ 「物販やホームケア教育こそがお客様に結果を出させ、サロンの売上も安定させる」という指導内容として発信する。物販を“押し売り”ではなく“結果を出すための必須要素”として伝える。" if retail_reason else ""}

{f"### メニューの組み合わせ方の考え方（指導テーマ）{chr(10)}{menu_combo}" if menu_combo else ""}

{f"### オーナーが働き詰めにならず、時間とお金の余裕を持って幸せに働くために必要なこと（指導テーマ）{chr(10)}{worklife}{chr(10)}→ 「頑張って働き続ける」ではなく「仕組みで余裕を作る」という価値観を伝える。" if worklife else ""}

{f"### サロンオーナーに一番気づいてほしいこと{chr(10)}{one_message}" if one_message else ""}

## サロンオーナーがよく持つ思い込み・勘違い（刺さる投稿・保存型投稿の素材）
{misbelief if misbelief else "（未記入）"}

## 講座・サービス
{f"提供講座・コースと価格：{courses}" if courses else "（未記入）"}
{f"一番おすすめ・成果が出る講座：{best_course}" if best_course else ""}
{f"投稿で触れてよい講座の仕組み：{mechanism}" if mechanism else ""}

## 受講生の成果・声
{f"成果・変化（数字含む）：{results}" if results else "（未記入）"}
{f"受講生からよく言われる感想：{voices}" if voices else ""}

{f"## 他スクール・コンサルとの違い（差別化の核）{chr(10)}{diff}" if diff else ""}

{f"## 受講を迷う人が比較する選択肢（差別化投稿の素材）{chr(10)}{competitor}{chr(10)}→ これらと比べたときの価値を投稿素材にする。" if competitor else ""}

{f"## 挫折・離脱しやすい人のパターン（先回り投稿の素材）{chr(10)}{churn}" if churn else ""}

## この講師固有の言葉・文体の個性（差別化の核・必ず反映する）
{f"口癖・よく使う言葉：{words}" if words else "口癖：（未記入）"}
{f"独自コンセプトワード：{concept}" if concept else "独自コンセプトワード：（未記入）"}
→ 上記の言葉・表現を自然に投稿へ織り込み、この講師にしか書けない文体を作る。一般的・テンプレ的な表現は避ける。

## CTA（投稿末尾の誘導）
{f"してほしい行動：{cta_actions}" if cta_actions else ""}
{f"誘導先・文言：{cta_detail}" if cta_detail else "プロフィールのリンクへ自然に誘導する"}
{f"DM・コメント相談：{dm_ok}" if dm_ok else ""}
{f"CTAの頻度：{cta_freq}（押し売り感を出さない）" if cta_freq else ""}

## 価格・体験談の投稿使用ルール（クライアント指定）
{f"価格記載：{price_ok}" if price_ok else "価格記載ルール：未指定"}
{f"受講生の成果・体験談の使用：{testi_ok}" if testi_ok else "体験談使用ルール：未指定"}

## NGワード・避けるべき表現（クライアント指定）
{ng_words if ng_words else "特になし"}

{f"## 発信スタイルのNGライン（クライアント指定）{chr(10)}{ng_line}" if ng_line else ""}

{f"## 講師自身についてのOK/NG情報{chr(10)}{privacy}" if privacy else ""}

## トーン・スタイル
{tone}
{emoji}
ハッシュタグは付けない。
短い文の改行を多用しThreadsで読みやすくする。

## 投稿の構造（ギャップ技法・常時適用）
- タイトル行で「え？」と思わせるズレを作る：「〇〇なのに△△」「〇〇じゃない、実は△△」
- 講師自身の過去の失敗（廃業・赤字など）を素材にしたギャップを積極的に使う
- 失敗→転換点→成果のW型ストーリーを複数本に使う
- サロンオーナーの「よくある思い込み」を正す保存型まとめ投稿（〇選、チェックリスト）を定期的に入れる
- 受講生の成果を冒頭フックに使う投稿を複数本入れる

---

## 【スクール版・必ず守るルール】（クライアントの要望より優先）

### 読者・呼び方
- 読者は「エステサロンのオーナー（経営者）」。一般の美容のお客様向けに書かない。
- 「うち」「うちの」は使わない。スクールを指すときは「{school_name}」または「当スクール」を使う。
- 自分（講師）を指すときは「{self_pronoun}」を使う。
- 一般客向けの「来店」「ご予約」「施術を受けに来てください」という誘導は書かない（読者は施術客ではなくサロンを経営するオーナー）。

### 敬語ルール
- 基本は「です・ます」調。読者（サロンオーナー）に敬意を持って語りかける。
- タメ口・ぞんざいな語尾は禁止（「〜だよ」「〜してね」「〜じゃん」）。「〜なんです」「〜ですよね」「〜してみてください」で着地。
- 断言・攻めた導入はOKだが、文末は敬語で着地させる。

### 物販・ホームケアの扱い（スクールとして重要）
- 物販・ホームケア教育は「サロンオーナーが取り入れるべき、結果を出すための必須要素」として前向きに発信する。
- 「物販は押し売り」「物を売るのは悪」という否定的な描き方はしない。
- 「お客様がサロンに来ていない日の習慣（ホームケア）こそが結果を作る。だからオーナーはホームケアを教えるべき」という考えを基本にする。

### ネガティブ表現の禁止
- 他のスクール・コンサル・他の手法を名指しで批判・否定しない（比較で自分の価値を語るのはOK）。
- 読み手が不安になるだけで終わる表現を使わない（必ず希望・解決策で着地）。
- 人格否定・見下しはしない。否定してよいのは「やり方・思い込み」であって「人」ではない。

### AI臭の排除
- 形式ワード禁止：「〜することが大切です」「〜していきましょう」「ぜひ〜してみてください」「いかがでしょうか」「ポイントは3つ」「まずは〜から」「日々の積み重ね」
- 整いすぎた見出し→3項目→まとめ構造を使わない。
- 「自分を大切に」「無理せず」など誰でも書ける抽象フレーズは禁止。
- 同じ語尾を3文以上繰り返さない。文の長さにバラつきをつける。

### 禁則（絶対遵守）
- 医療的な断定・病気が治る等の表現禁止。
- 「絶対」「100%」などの誇大表現は避ける（「ほとんど」「多くの場合」に言い換える）。
- 「必ず儲かる」「誰でも成功する」などの断定的な収益保証はしない。
- 政治・宗教・占いの断定は避ける。
- ハッシュタグ禁止。
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
    threads_id = normalize_threads_id(salon.get("Threadsのアカウント名（@から始まるID）", ""))
    file_key = threads_id if threads_id else salon_name
    safe_name = re.sub(r'[^\w\-]', '_', file_key)

    Path(POSTS_DIR).mkdir(exist_ok=True)
    posts_path = os.path.join(POSTS_DIR, f"posts_{safe_name}.json")

    if os.path.exists(posts_path):
        posts = json.load(open(posts_path, encoding="utf-8"))
        for slot in ["morning", "noon", "evening"]:
            posts.setdefault(slot, [])
    else:
        posts = {"morning": [], "noon": [], "evening": []}

    stype = salon.get("_type")
    if stype == "b2b":
        rules = b2b_to_rules(salon)
    elif stype == "school":
        rules = school_to_rules(salon)
    else:
        rules = salon_to_rules(salon)

    # 週次リサーチから更新される共通インサイト（美容寄り）を追加。B2Bは対象外なのでスキップ。
    if stype != "b2b":
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
        remaining = _remaining(posts, file_key, slot)
        if remaining > THRESHOLD:
            print(f"[saas] {file_key} {slot}: 残{remaining}本 → 生成不要")
            continue

        print(f"[saas] {file_key} {slot}: 残{remaining}本 → {GENERATE_COUNT}本生成開始")

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
            length_rule = "- 各部（1部目・2部目）は150〜300字。どの部も350字を超えないこと"
        else:
            output_format = """各要素は単発投稿の文字列。改行は\\nで表現。

出力例:
[
  "1行目フック\\n\\n本文。\\n\\nCTA",
  "別の投稿のフック\\n\\n本文。"
]"""
            tree_rule = "- 全投稿を単発（文字列）で生成する\n"
            length_rule = "- 各投稿は200〜300字を目安に、最長でも350字。長すぎる投稿は禁止（スクロールで一気に読み切れる短さが最も読まれる）"

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
- オーナーの実体験を素材にした投稿を複数本入れる（失敗談は、サロン情報に具体的な失敗・コンプレックスが書かれている場合のみ。無ければ創作しない）
- 全{GENERATE_COUNT}本が違う切り口・違う素材
- このサロン固有の口癖・言葉・コンセプトワードを文体に自然に反映させ、他のどのサロンとも被らない個性を出す
- ハッシュタグ禁止
{length_rule}
{tree_rule}- {GENERATE_COUNT}本すべてJSON配列に含める

既存投稿（この角度は避ける）：
{existing if existing else "（まだなし）"}"""

        new_posts = None
        last_error = None
        for attempt in range(3):
            try:
                resp = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=8000,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}]
                )
                raw = resp.content[0].text.strip()
                start = raw.find("[")
                end = raw.rfind("]") + 1
                if start == -1 or end == 0:
                    last_error = "JSONが見つからない"
                    print(f"[saas] {salon_name} {slot} (試行{attempt+1}): {last_error}")
                    continue
                candidate = raw[start:end]
                try:
                    new_posts = json.loads(candidate)
                except json.JSONDecodeError:
                    import re as _re
                    candidate = _re.sub(r'(?<!\\)\n', r'\\n', candidate)
                    new_posts = json.loads(candidate)
                if not isinstance(new_posts, list):
                    last_error = "list以外が返された"
                    new_posts = None
                    continue
                break
            except json.JSONDecodeError as e:
                last_error = str(e)
                print(f"[saas] {salon_name} {slot} (試行{attempt+1}): JSONパースエラー → {e}")
                new_posts = None
            except Exception as e:
                last_error = str(e)
                print(f"[saas] {salon_name} {slot} (試行{attempt+1}): エラー → {e}")
                new_posts = None
                break

        if new_posts is None:
            print(f"[saas] {salon_name} {slot}: 3回試みて失敗 → {last_error}")
            continue

        # 長さ検証：基準を超える投稿は保存しない（単発350字・ツリー各部400字まで）
        def _within_length(p):
            if isinstance(p, list):
                return all(len(str(x)) <= 400 for x in p)
            return len(str(p)) <= 350
        kept = [p for p in new_posts if _within_length(p)]
        dropped = len(new_posts) - len(kept)
        if dropped:
            print(f"[saas] {salon_name} {slot}: 長すぎる投稿 {dropped}本を除外（基準: 単発350字/ツリー各部400字）")
        new_posts = kept

        posts[slot].extend(new_posts)
        print(f"[saas] {salon_name} {slot}: {len(new_posts)}本追加（合計{len(posts[slot])}本）")
        generated_any = True

    if generated_any:
        with open(posts_path, "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)
        print(f"[saas] {posts_path} を更新しました")

    return generated_any


def main():
    target_salon = sys.argv[1] if len(sys.argv) > 1 else None

    print("[saas] スプレッドシートを読み込み中...")
    try:
        salons = load_sheet_data() + load_school_data()
    except Exception as e:
        print(f"[saas] スプレッドシート読み込みエラー: {e}")
        print("[saas] google_service_account.json が設定されているか確認してください")
        # シートが読めなくても、ローカル定義（B2B等）だけは処理を続行する
        salons = []
    salons += load_local_salons()
    if not salons:
        sys.exit(1)

    print(f"[saas] {len(salons)}件のサロンを検出")

    # 投稿在庫を手動キュレーションしているアカウント（自動生成すると方針が上書きされるため除外）
    MANUAL_STOCK_IDS = {"aya_kuroki_0929"}

    for salon in salons:
        salon_name = salon.get("サロン名", "")
        threads_id = normalize_threads_id(salon.get("Threadsのアカウント名（@から始まるID）", ""))
        if not salon_name:
            continue
        if target_salon and threads_id != target_salon and salon_name != target_salon:
            continue
        if threads_id in MANUAL_STOCK_IDS:
            print(f"[saas] {salon_name} (@{threads_id}) は在庫手動管理のため自動生成をスキップ")
            continue
        print(f"\n[saas] === {salon_name} (@{threads_id}) の処理開始 ===")
        generate_for_salon(salon)

    print("\n[saas] 全処理完了")


if __name__ == "__main__":
    main()
