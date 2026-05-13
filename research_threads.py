"""
Threads 競合リサーチ & 分析スクリプト
毎週月曜に実行し、伸びている投稿のパターンをClaude APIで分析してレポートを生成する。
"""
import os, json, time, sys, re
from datetime import datetime
from anthropic import Anthropic
from playwright.sync_api import sync_playwright

SESSION_FILE = os.environ.get("SESSION_FILE", "session_personal.json")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

RESEARCH_TOPICS = {
    "bemolle": [
        "美容 ダイエット", "痩身サロン", "エステ 大阪",
        "肌荒れ 改善", "40代 ダイエット", "リバウンドしない",
    ],
    "personal": [
        "サロン経営 自動化", "美容サロン AI", "一人サロン 効率化",
        "サロンオーナー 起業", "業務効率化 サロン",
    ],
}

def load_session():
    with open(SESSION_FILE) as f:
        return json.load(f)

def fetch_posts_for_keyword(page, keyword, max_scroll=3):
    """指定キーワードで検索し、投稿テキストを収集する。"""
    posts = []
    try:
        encoded = keyword.replace(" ", "%20")
        page.goto(
            f"https://www.threads.com/search?q={encoded}&serp_type=default",
            wait_until="domcontentloaded",
            timeout=15000,
        )
        page.wait_for_timeout(3000)

        for _ in range(max_scroll):
            page.evaluate("window.scrollBy(0, 800)")
            page.wait_for_timeout(1200)

        raw_text = page.inner_text("body")

        lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
        block = []
        for line in lines:
            if len(line) > 30 and not line.startswith("http"):
                block.append(line)
            elif block:
                joined = " ".join(block)
                if len(joined) > 50:
                    posts.append({"keyword": keyword, "text": joined[:400]})
                block = []
        if len(posts) == 0:
            print(f"[research] '{keyword}': テキスト取得できず")
    except Exception as e:
        print(f"[research] '{keyword}' 失敗: {e}")
    return posts[:8]


def analyze_with_claude(posts, account_type):
    """収集した投稿をClaude Haikuで分析し、マークダウンのインサイトを返す。"""
    if not posts:
        return "（投稿収集できず）\n"

    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    samples = "\n\n---\n\n".join(
        f"[{p['keyword']}]\n{p['text']}" for p in posts[:25]
    )

    if account_type == "bemolle":
        context = "美容・ダイエット・エステサロン系（大阪・谷町九丁目のサロン @bemolle_diet 参考用）"
        rules_focus = "来店・予約・インスタフォローにつながる投稿パターン"
    else:
        context = "美容サロンオーナー×AI自動化系（@aya_kuroki_0929 参考用）"
        rules_focus = "同業サロンオーナーのフォロー・保存・コメントを獲得する投稿パターン"

    prompt = f"""あなたはSNSマーケティングの専門家です。
以下はThreadsで「{context}」のキーワードで収集した投稿サンプルです。

## 投稿サンプル
{samples}

## 分析してほしいこと
目的：{rules_focus}

以下の観点で分析し、マークダウン形式で出力してください：

### 1. 刺さっているフックパターン（冒頭1行目）
よく使われている書き出しのパターンを5つ、具体例付きで

### 2. 投稿構造のパターン
どんな流れで書かれているか（起承転結、問題提起→解決、など）

### 3. 反応されそうな感情のツボ
どんな感情（共感・驚き・危機感・安心感など）に訴えているか

### 4. CTA・誘導のパターン
末尾の行動喚起・インスタ誘導・フォロー促進の書き方

### 5. 今の投稿ルール（GENERATE_RULES）への具体的な改善提案（3〜5個）
「こう変えると反響が増えそう」という具体的な提案を箇条書きで
"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"[research] 開始: {today}")

    session_data = load_session()
    all_posts = {"bemolle": [], "personal": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        ctx.add_cookies(session_data["cookies"])
        page = ctx.new_page()

        for account_type, keywords in RESEARCH_TOPICS.items():
            print(f"[research] === {account_type} リサーチ開始 ===")
            for kw in keywords:
                posts = fetch_posts_for_keyword(page, kw)
                all_posts[account_type].extend(posts)
                print(f"[research] '{kw}': {len(posts)}件")
                time.sleep(2)

        browser.close()

    os.makedirs("research_reports", exist_ok=True)
    report_path = f"research_reports/report_{today}.md"

    lines = [
        f"# Threads リサーチレポート {today}\n",
        f"生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M JST')}\n",
        "---\n",
    ]

    for account_type in ["bemolle", "personal"]:
        posts = all_posts[account_type]
        label = (
            "ベモーレ（美容・ダイエット・エステ）"
            if account_type == "bemolle"
            else "個人（サロンオーナー×AI自動化）"
        )
        print(f"[research] {label} 分析中... ({len(posts)}件)")

        analysis = analyze_with_claude(posts, account_type)

        lines.append(f"## {label}\n")
        lines.append(f"収集投稿数: {len(posts)}件\n")
        lines.append(analysis)
        lines.append("\n---\n")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[research] レポート保存: {report_path}")


if __name__ == "__main__":
    main()
