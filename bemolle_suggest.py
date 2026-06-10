#!/usr/bin/env python3
"""bemolle 手動コメント支援。
重度アクション制限中のbemolleは自動「送信」が弾かれるため、Botは「探す・書く」だけ行い、
候補（投稿URL＋おすすめコメント文）をLINEで送る。送信は人間が手動で行う＝制限を受けない。
投稿の"読み取り"と"コメント生成"は制限対象外なのでクラウド(GH Actions)でも動く。

LINEは「URL=1通」「コメント文=独立した1通」を候補ごとに交互送信する。
→ コメント文の通知を丸ごとコピーしてThreadsに貼り付けられる（部分選択不要）。
"""
import os, sys, json, time, random, urllib.request
from datetime import datetime, timedelta

_BASE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("THREADS_USERNAME", "bemolle_diet")
os.environ.setdefault("SESSION_FILE", os.path.join(_BASE, "session.json"))
os.environ.setdefault("COMMENT_TARGETS_FILE", os.path.join(_BASE, "comment_targets.json"))
os.environ.setdefault("COMMENTED_FILE", os.path.join(_BASE, "commented_posts.json"))
os.environ.setdefault("COMMENT_KEYWORDS_FILE", os.path.join(_BASE, "comment_search_keywords.json"))

# GH Actions: THREADS_SESSION からセッション復元
_session_data = os.environ.get("THREADS_SESSION", "")
_session_file = os.environ["SESSION_FILE"]
if _session_data:
    with open(_session_file, "w", encoding="utf-8") as f:
        f.write(_session_data)

import post  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

N = int(os.environ.get("SUGGEST_COUNT", "3"))
LINE_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")


def send_line(text):
    """1メッセージを単独でLINE送信（丸ごとコピーできるように1通=1テキスト）。"""
    if not LINE_TOKEN:
        print("[suggest] LINEトークン無し（表示のみ）:\n" + text)
        return
    body = json.dumps({"messages": [{"type": "text", "text": text}]}).encode()
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/broadcast",
        data=body,
        headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req)
    except Exception as e:
        print(f"[suggest] LINE送信失敗: {e}")
    time.sleep(0.6)  # 順序を保つため少し待つ


def main():
    targets = json.load(open(post.COMMENT_TARGETS_FILE, encoding="utf-8"))
    commented = post._load_commented()
    eligible = [t for t in targets if not post._already_commented_recently(commented, t.get("account", ""))]
    random.shuffle(eligible)

    suggestions = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=_session_file, locale="ja-JP", timezone_id="Asia/Tokyo")
        page = ctx.new_page()
        for t in eligible:
            if len(suggestions) >= N:
                break
            acc = t.get("account", "")
            if not acc:
                continue
            url, text, dt = post._get_target_latest_post(page, acc)
            if not url or not text:
                continue
            if dt and dt < datetime.utcnow() - timedelta(days=post.POST_AGE_LIMIT_DAYS):
                continue
            if any(w in text for w in post._NEGATIVE_IMPRESSION_SKIP_WORDS):
                continue
            if any(w in text for w in post._ILLNESS_DEATH_WORDS):
                continue
            comment = post._generate_comment(text, t.get("note", ""))
            if not comment:
                continue
            suggestions.append((acc, url, comment))
            # 重複提案を防ぐため記録（手動送信前提で通常のクールダウン対象にする）
            post_id = url.rstrip("/").split("/")[-1]
            commented[f"/{acc}/{post_id}"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[suggest] @{acc} 候補生成: {comment}")
        browser.close()

    post._save_commented(commented)

    if not suggestions:
        send_line("☕ bemolle 今日のコメント候補：今日は適切な候補が見つかりませんでした。")
        print("[suggest] 候補0件")
        return

    # ヘッダー → 各候補（URL1通 + コメント文1通）
    send_line(f"☕ bemolle 今日のコメント候補 {len(suggestions)}件\n「投稿リンク」を開いて、その下の「コメント文」だけの通知を丸ごとコピーして貼り付けて送ってね🙏")
    for i, (acc, url, comment) in enumerate(suggestions, 1):
        send_line(f"📍 {i}件目（@{acc}）投稿はこちら👇\n{url}")
        send_line(comment)  # ← これ単独。丸ごとコピーできる
    print(f"[suggest] {len(suggestions)}件をLINE送信完了")


if __name__ == "__main__":
    main()
