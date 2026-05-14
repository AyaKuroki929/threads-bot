"""
Threads リサーチ → GENERATE_RULES 自動更新パイプライン
毎週月曜 JST 10:00 実行

1. Threadsトップ検索で反響アカウントを自動発見
2. Claude が投稿パターンを分析
3. GENERATE_RULES 3ファイルの「週次インサイト」セクションを更新
4. LINE に「何を追加したか」を報告
"""
import os, json, time, re, sys
from datetime import datetime
from anthropic import Anthropic
from playwright.sync_api import sync_playwright

SESSION_FILE  = os.environ.get("SESSION_FILE", "session_personal.json")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
LINE_TOKEN    = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")

RULES_FILES = {
    "bemolle":   "GENERATE_RULES.md",
    "personal":  "GENERATE_RULES_personal.md",
    "saas":      "GENERATE_RULES_saas.md",
}

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

SECTION_HEADER = "## 🔄 週次インサイト（自動更新）"


# ──────────────────────────────────────────────
# Playwright: トップ検索 → アカウント発見 → 投稿収集
# ──────────────────────────────────────────────

def _search_top_posts(page, keyword, max_scroll=3):
    """キーワードでトップタブ検索し、投稿テキストとアカウント名を返す。"""
    posts = []
    try:
        encoded = keyword.replace(" ", "%20")
        page.goto(
            f"https://www.threads.com/search?q={encoded}&serp_type=default",
            wait_until="domcontentloaded",
            timeout=15000,
        )
        page.wait_for_timeout(3000)

        # 「トップ」タブをクリック
        for sel in ['[role="tab"]:has-text("トップ")', '[role="tab"]:has-text("Top")']:
            tab = page.locator(sel).first
            if tab.count() > 0:
                try:
                    tab.click()
                    page.wait_for_timeout(2000)
                except Exception:
                    pass
                break

        for _ in range(max_scroll):
            page.evaluate("window.scrollBy(0, 800)")
            page.wait_for_timeout(1200)

        containers = page.locator('div[data-pressable-container="true"]')
        for i in range(min(containers.count(), 20)):
            try:
                c = containers.nth(i)
                # アカウント名
                links = c.locator('a[href*="/@"]')
                username = ""
                for j in range(links.count()):
                    href = links.nth(j).get_attribute("href") or ""
                    if "/@" in href and "/post/" not in href:
                        username = href.strip("/").split("@")[-1].split("/")[0]
                        break
                # テキスト
                raw = c.inner_text()
                lines = [l.strip() for l in raw.splitlines()
                         if len(l.strip()) > 20 and not l.strip().startswith("http")]
                text = " ".join(lines)[:400]
                if username and len(text) > 50:
                    posts.append({"keyword": keyword, "username": username, "text": text})
            except Exception:
                pass
    except Exception as e:
        print(f"[research] '{keyword}' 検索失敗: {e}")
    return posts[:8]


def _fetch_account_posts(page, username, max_posts=5):
    """アカウントのプロフィールから最新投稿を収集する。"""
    posts = []
    try:
        page.goto(
            f"https://www.threads.com/@{username}",
            wait_until="domcontentloaded",
            timeout=15000,
        )
        page.wait_for_timeout(3000)
        containers = page.locator('div[data-pressable-container="true"]')
        for i in range(min(containers.count(), max_posts)):
            try:
                raw = containers.nth(i).inner_text()
                lines = [l.strip() for l in raw.splitlines()
                         if len(l.strip()) > 20 and not l.strip().startswith("http")]
                text = " ".join(lines)[:400]
                if len(text) > 50:
                    posts.append({"username": username, "text": text})
            except Exception:
                pass
    except Exception as e:
        print(f"[research] @{username} プロフィール取得失敗: {e}")
    return posts


def collect_posts(session_data):
    """全キーワードでトップ検索 → 上位アカウントの投稿を収集する。"""
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

        for acct_type, keywords in RESEARCH_TOPICS.items():
            seen_users = set()
            direct_posts = []

            # ① トップ検索で投稿＋アカウント名収集
            for kw in keywords:
                hits = _search_top_posts(page, kw)
                for h in hits:
                    direct_posts.append(h)
                    seen_users.add(h["username"])
                print(f"[research] {acct_type} '{kw}': {len(hits)}件")
                time.sleep(2)

            all_posts[acct_type].extend(direct_posts)

            # ② 上位アカウントのプロフィール投稿も追加取得（最大6アカウント）
            top_users = list(seen_users)[:6]
            for uname in top_users:
                profile_posts = _fetch_account_posts(page, uname)
                for p2 in profile_posts:
                    if p2["text"] not in [x["text"] for x in all_posts[acct_type]]:
                        all_posts[acct_type].append({
                            "keyword": f"@{uname}",
                            "username": uname,
                            "text": p2["text"],
                        })
                time.sleep(1)

        browser.close()

    return all_posts


# ──────────────────────────────────────────────
# Claude 分析 → ルール JSON 生成
# ──────────────────────────────────────────────

def analyze_and_generate_rules(all_posts):
    """Claude で分析し、ルール追加 JSON を返す。"""
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    def fmt(posts):
        return "\n\n---\n\n".join(
            f"[@{p.get('username','?')} / {p.get('keyword','')}]\n{p['text']}"
            for p in posts[:20]
        )

    bemolle_samples  = fmt(all_posts["bemolle"])
    personal_samples = fmt(all_posts["personal"])

    prompt = f"""あなたはSNSマーケティングの専門家です。
以下はThreadsで今週反響を得ているアカウントの投稿サンプルです（トップ検索で上位表示されたもの）。

## ベモーレ系（美容・ダイエット・エステサロン向け）
{bemolle_samples}

## 個人系（美容サロンオーナー×AI自動化向け）
{personal_samples}

---

以下の形式のJSONのみを返してください（前後に余計なテキスト不要）：

{{
  "both": [
    "【カテゴリ】内容（両アカウント共通ルール・最大5個）"
  ],
  "bemolle_only": [
    "【カテゴリ】内容（ベモーレのみ・最大3個）"
  ],
  "personal_only": [
    "【カテゴリ】内容（個人アカウントのみ・最大3個）"
  ],
  "summary": "今週の主な発見を2〜3行で（LINEに送る文章）"
}}

ルール作成のルール：
- カテゴリ例：フック・構造・感情・CTA・頻度・長さ・数字
- 必ず具体的・実行可能な形で書く（「〜を使う」「〜を週1本入れる」など）
- 既に一般的なことは書かない。今週のサンプルから読み取れる新しいパターンのみ
- 1ルールは50文字以内
"""

    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()

    # JSON 部分だけ抽出
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"JSON parse failed: {raw[:200]}")
    return json.loads(match.group())


# ──────────────────────────────────────────────
# GENERATE_RULES ファイル更新
# ──────────────────────────────────────────────

def _build_section(rules_list, today):
    lines = [
        f"{SECTION_HEADER}",
        f"最終更新：{today}（毎週月曜自動更新）",
        "",
    ]
    for rule in rules_list:
        lines.append(f"- {rule}")
    lines.append("")
    return "\n".join(lines)


def update_rules_file(filepath, rules_list, today):
    """ファイルの週次インサイトセクションを差し替える。"""
    if not os.path.exists(filepath):
        # 新規作成（SaaS用）
        content = f"# SaaS クライアント共通 Threads 投稿ルール（週次更新）\n\n"
        content += _build_section(rules_list, today)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[rules] {filepath} 新規作成")
        return True

    with open(filepath, encoding="utf-8") as f:
        content = f.read()

    new_section = _build_section(rules_list, today)

    if SECTION_HEADER in content:
        # 既存セクションを置換
        pattern = re.escape(SECTION_HEADER) + r".*?(?=\n## |\Z)"
        content = re.sub(pattern, new_section.rstrip(), content, flags=re.DOTALL)
    else:
        # 末尾に追加
        content = content.rstrip() + "\n\n" + new_section

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[rules] {filepath} 更新済み")
    return True


def apply_rules(rules_json, today):
    """3ファイルにルールを反映する。"""
    both    = rules_json.get("both", [])
    bemolle = both + rules_json.get("bemolle_only", [])
    personal = both + rules_json.get("personal_only", [])
    saas    = both  # SaaSは共通ルールのみ適用

    update_rules_file(RULES_FILES["bemolle"],  bemolle,  today)
    update_rules_file(RULES_FILES["personal"], personal, today)
    update_rules_file(RULES_FILES["saas"],     saas,     today)

    return {"bemolle": bemolle, "personal": personal, "saas": saas}


# ──────────────────────────────────────────────
# LINE 通知
# ──────────────────────────────────────────────

def send_line(message):
    if not LINE_TOKEN:
        return
    import urllib.request
    body = json.dumps({"messages": [{"type": "text", "text": message}]}).encode()
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/broadcast",
        data=body,
        headers={
            "Authorization": f"Bearer {LINE_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as r:
            pass
    except Exception as e:
        print(f"[LINE] 通知失敗: {e}")


# ──────────────────────────────────────────────
# main
# ──────────────────────────────────────────────

def main():
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"[research] 開始: {today}")

    with open(SESSION_FILE) as f:
        session_data = json.load(f)

    print("[research] 投稿収集中...")
    all_posts = collect_posts(session_data)
    print(f"[research] 収集完了: bemolle={len(all_posts['bemolle'])}件 personal={len(all_posts['personal'])}件")

    print("[research] Claude 分析中...")
    rules_json = analyze_and_generate_rules(all_posts)

    print("[research] GENERATE_RULES 更新中...")
    applied = apply_rules(rules_json, today)

    # LINE 通知
    summary = rules_json.get("summary", "（サマリーなし）")
    both_rules    = rules_json.get("both", [])
    bemolle_rules = rules_json.get("bemolle_only", [])
    personal_rules = rules_json.get("personal_only", [])

    msg_lines = [
        f"📊 週次ルール自動更新完了（{today}）",
        "",
        summary,
        "",
    ]
    if both_rules:
        msg_lines.append("【全アカウント共通】")
        for r in both_rules:
            msg_lines.append(f"・{r}")
        msg_lines.append("")
    if bemolle_rules:
        msg_lines.append("【ベモーレ追加】")
        for r in bemolle_rules:
            msg_lines.append(f"・{r}")
        msg_lines.append("")
    if personal_rules:
        msg_lines.append("【個人アカウント追加】")
        for r in personal_rules:
            msg_lines.append(f"・{r}")

    line_msg = "\n".join(msg_lines)
    print(f"[research] LINE 通知:\n{line_msg}")
    send_line(line_msg)

    print("[research] 完了")


if __name__ == "__main__":
    main()
