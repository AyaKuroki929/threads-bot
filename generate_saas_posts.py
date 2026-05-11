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
from pathlib import Path

import anthropic
import gspread
from google.oauth2.service_account import Credentials

SPREADSHEET_ID = "1Af6ZnH7Ghzn1APpVrFy5nFlftNaIX5YOeSvfFjSdU-U"
SERVICE_ACCOUNT_FILE = os.path.join(os.path.dirname(__file__), "google_service_account.json")
POSTS_DIR = os.path.join(os.path.dirname(__file__), "posts_saas")
GENERATE_COUNT = 15
THRESHOLD = 5

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
    location = salon.get("所在地（最寄り駅・徒歩時間）", "")
    hours = salon.get("営業時間", "")
    holiday = salon.get("定休日", "")
    threads_id = salon.get("Threadsのアカウント名（@から始まるID）", "")
    target = salon.get("メインターゲット（年代・性別・どんな悩みを持つ人）", "")
    menu = salon.get("提供メニューと価格帯（箇条書きでOK）", "")
    best_menu = salon.get("一番の売りメニュー・最も結果が出やすい施術", "")
    results = salon.get("お客様の具体的な変化・実績（数字があれば）", "")
    owner_fail = salon.get("過去の失敗・コンプレックス（「実はこんなだった」という意外な過去）", "")
    turning_point = salon.get("転換点・気づき（何をきっかけに変わったか）", "")
    own_result = salon.get("自分自身で出た成果・結果", "")
    why_salon = salon.get("なぜこのサロンを作ったか", "")
    stance = salon.get("お客様への向き合い方・こだわり", "")
    ng_words = salon.get("サロンとして「言いたくないこと」「NGワード」", "")
    catchcopy = salon.get("サロンを一言で表すキャッチコピー（あれば）", "")
    tone = tone_map.get(salon.get("投稿のトーン", ""), "丁寧・落ち着いたですます調。")
    emoji = emoji_map.get(salon.get("絵文字を使いますか？", ""), "絵文字は使わない。")
    booking_status = salon.get("現在の予約状況", "")
    booking_url = salon.get("予約先のURL（投稿末尾に誘導するリンク）", "")
    notes = salon.get("その他・自由記入（伝えておきたいこと）", "")

    rules = f"""# {salon_name} Threads投稿生成ルール

## 発信者プロフィール
- **サロン名**：{salon_name}
- **オーナー名**：{owner_name}
- **場所**：{location}
- **営業時間**：{hours}（定休日：{holiday}）
- **アカウント**：{threads_id}

## Threadsの目的
ターゲット層（{target}）に響く投稿でフォロワーを増やし、予約・来店につなげる。
エンゲージメント（いいね・保存・返信）が増える投稿を最優先する。

## ターゲット
{target}

## メニュー・サービス
{menu}

一番の売り：{best_menu}

お客様の実績：{results}

## オーナーのストーリー（投稿の核・最重要）

### 過去の失敗・コンプレックス（ギャップ素材）
{owner_fail}

### 転換点・気づき
{turning_point}

### 自分自身の成果
{own_result}

### なぜこのサロンを作ったか
{why_salon}

## サロンの理念・お客様への向き合い方
{stance}

## キャッチコピー
{catchcopy if catchcopy else "（未設定）"}

## NGワード・避けるべき表現
{ng_words if ng_words else "特になし"}

## 現在の予約状況
{booking_status}
{"→ 空きがあるので積極的に来店誘導するCTAを入れる" if "空き" in booking_status else "→ 満席に近いため「少数限定」「今すぐ」の希少性を出す"}

## 予約先
{booking_url if booking_url else "プロフィールのリンクから"}

## トーン・スタイル
{tone}
{emoji}
ハッシュタグは付けない。
短い文の改行を多用しThreadsで読みやすくする。

## 投稿の構造（ギャップ技法・常時適用）
- タイトル行で「え？」と思わせるズレを作る：「〇〇なのに△△」「〇〇じゃない、実は△△」
- オーナーの過去の失敗を素材にしたギャップを積極的に使う
- 失敗→転換点→成果のW型ストーリーを複数本に使う

## その他メモ
{notes if notes else "なし"}
"""
    return rules


def _remaining(posts, used, slot):
    total = len(posts.get(slot, []))
    used_count = len(used.get(slot, []))
    return total - used_count


def generate_for_salon(salon: dict):
    salon_name = salon.get("サロン名", "unknown")
    safe_name = re.sub(r'[^\w\-]', '_', salon_name)

    Path(POSTS_DIR).mkdir(exist_ok=True)
    posts_path = os.path.join(POSTS_DIR, f"posts_{safe_name}.json")
    used_path = os.path.join(POSTS_DIR, f"used_{safe_name}.json")

    if os.path.exists(posts_path):
        posts = json.load(open(posts_path, encoding="utf-8"))
    else:
        posts = {"morning": [], "evening": []}

    used = json.load(open(used_path, encoding="utf-8")) if os.path.exists(used_path) else {}

    rules = salon_to_rules(salon)
    try:
        client = anthropic.Anthropic()
    except Exception as e:
        print(f"[saas] Anthropic APIキーが取得できません → {e}")
        return False
    generated_any = False

    for slot in ["morning", "evening"]:
        remaining = _remaining(posts, used, slot)
        if remaining > THRESHOLD:
            print(f"[saas] {salon_name} {slot}: 残{remaining}本 → 生成不要")
            continue

        print(f"[saas] {salon_name} {slot}: 残{remaining}本 → {GENERATE_COUNT}本生成開始")

        slot_hint = {
            "morning": "朝投稿（7〜8時頃）。1日の始まりに読む人向け。前向きな気づき・軽い問いかけ・背中を押す内容。",
            "evening": "夜投稿（21時頃）。1日の終わりに読む人向け。内省・本音・今日の気づき・静かな共感。",
        }[slot]

        existing = "\n".join([str(posts[slot][i])[:80] for i in range(min(3, len(posts[slot])))])

        system_prompt = f"""あなたはSNS投稿の専門家です。
以下のサロン情報・ルールに従って、Threads用の投稿文を生成してください。

=== サロン情報・投稿ルール ===
{rules}

=== 時間帯の特性 ===
{slot_hint}

=== 出力形式（厳守）===
JSON配列だけを返してください。各要素は単発投稿の文字列です。
改行は \\n で表現してください。

出力例:
[
  "1行目フック\\n\\n本文の続き。\\n\\nCTA（予約はプロフのリンクから）",
  "別の投稿のフック\\n\\n本文。"
]

JSON配列以外の文字は一切出力しないでください。"""

        user_prompt = f"""{GENERATE_COUNT}本の{slot}投稿を生成してください。

必ず守ること：
- 1行目は必ず「スクロールが止まる」強いフックから始める
- ギャップ投稿（「〇〇なのに△△」「実は〇〇だった」）を半数以上に使う
- オーナーの実体験・失敗談を素材にした投稿を複数本入れる
- 全{GENERATE_COUNT}本が違う切り口・違う素材
- ハッシュタグ禁止
- {GENERATE_COUNT}本すべてJSON配列に含める

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
