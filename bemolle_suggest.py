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
# 引数でアカウント切替（bemolle / personal）。両アカとも自動送信が制限/不安定なため手動支援。
ACCOUNT = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in ("bemolle", "personal") else "bemolle"
if ACCOUNT == "personal":
    os.environ.setdefault("THREADS_USERNAME", "aya_kuroki_0929")
    os.environ.setdefault("SESSION_FILE", os.path.join(_BASE, "session_personal.json"))
    os.environ.setdefault("COMMENT_TARGETS_FILE", os.path.join(_BASE, "comment_targets_personal.json"))
    os.environ.setdefault("COMMENTED_FILE", os.path.join(_BASE, "commented_posts_personal.json"))
    os.environ.setdefault("COMMENT_KEYWORDS_FILE", os.path.join(_BASE, "comment_search_keywords_personal.json"))
    _SESSION_ENV = "THREADS_SESSION_PERSONAL"
    ACCOUNT_LABEL = "個人"
else:
    os.environ.setdefault("THREADS_USERNAME", "bemolle_diet")
    os.environ.setdefault("SESSION_FILE", os.path.join(_BASE, "session.json"))
    os.environ.setdefault("COMMENT_TARGETS_FILE", os.path.join(_BASE, "comment_targets.json"))
    os.environ.setdefault("COMMENTED_FILE", os.path.join(_BASE, "commented_posts.json"))
    os.environ.setdefault("COMMENT_KEYWORDS_FILE", os.path.join(_BASE, "comment_search_keywords.json"))
    _SESSION_ENV = "THREADS_SESSION"
    ACCOUNT_LABEL = "ベモーレ"
USERNAME = os.environ["THREADS_USERNAME"]

# 読み取り用セッションは常に bemolle の健全なセッション(THREADS_SESSION)を使う。
# suggestは投稿を「読む」だけ（コメント送信は人間が手動）なので、個人の候補生成でも
# 読み取りセッションは共有できる。個人セッションは書き込み制限で死にやすく読み取りにも
# 不安定なため使わない（＝個人のためにセッションを取り直す必要がなくなる）。
_reading_session = os.environ.get("THREADS_SESSION", "")
_session_file = os.path.join(_BASE, "session.json")
if _reading_session:
    with open(_session_file, "w", encoding="utf-8") as f:
        f.write(_reading_session)

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


def _load_exclude():
    """二度と提案しないアカウントの除外リスト（@なし小文字）。アカウント別ファイル。"""
    path = os.path.join(_BASE, f"comment_exclude_{ACCOUNT}.json")
    try:
        return {str(a).lstrip("@").lower() for a in json.load(open(path, encoding="utf-8"))}
    except Exception:
        return set()


def main():
    targets = json.load(open(post.COMMENT_TARGETS_FILE, encoding="utf-8"))
    commented = post._load_commented()
    exclude = _load_exclude()
    eligible = [
        t for t in targets
        if not post._already_commented_recently(commented, t.get("account", ""))
        and t.get("account", "").lstrip("@").lower() not in exclude
    ]
    random.shuffle(eligible)

    suggestions = []
    consecutive_fail = 0
    MAX_FAIL = 8  # セッション異常時にプール全件を空回りしてタイムアウトするのを防ぐ
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
                consecutive_fail += 1
                print(f"[suggest] @{acc} 投稿取得失敗 (連続{consecutive_fail}件)")
                if consecutive_fail >= MAX_FAIL:
                    print(f"[suggest] {ACCOUNT_LABEL} 連続{MAX_FAIL}件 投稿取得失敗 → セッション異常の可能性。中断")
                    break
                continue
            consecutive_fail = 0
            post_id = url.rstrip("/").split("/")[-1]
            # 【二重投稿防止①】この投稿IDに既にコメント記録があれば絶対に提案しない
            if f"/{acc}/{post_id}" in commented or any(k.endswith("/" + post_id) for k in commented):
                print(f"[suggest] @{acc} {post_id} は既にコメント記録あり → スキップ（二重防止）")
                continue
            if dt and dt < datetime.utcnow() - timedelta(days=post.POST_AGE_LIMIT_DAYS):
                print(f"[suggest] @{acc} 投稿が古すぎ ({dt}) → スキップ")
                continue
            if any(w in text for w in post._NEGATIVE_IMPRESSION_SKIP_WORDS):
                print(f"[suggest] @{acc} NGワード → スキップ")
                continue
            if any(w in text for w in post._ILLNESS_DEATH_WORDS):
                print(f"[suggest] @{acc} 疾病ワード → スキップ")
                continue
            # 【二重投稿防止②】投稿ページに既に自分(USERNAME)の返信があればスキップ（手動分も検知）
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(3500)
                if USERNAME in page.locator("body").inner_text():
                    print(f"[suggest] @{acc} {post_id} に既に{USERNAME}の返信あり → スキップ（二重防止）")
                    commented[f"/{acc}/{post_id}"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    continue
            except Exception:
                pass
            comment = post._generate_comment(text, t.get("note", ""))
            if not comment:
                continue
            suggestions.append((acc, url, comment))
            # 提案した投稿を記録（再提案・二重投稿の防止）
            commented[f"/{acc}/{post_id}"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[suggest] @{acc} 候補生成: {comment}")
        browser.close()

    post._save_commented(commented)

    if not suggestions:
        print(f"[suggest] {ACCOUNT_LABEL} 候補0件（LINE配信節約のため通知なし）")
        return

    # ヘッダーは送らない（配信数の節約）。各候補を「URL1通 + コメント文1通」で送る。
    # どのアカウントで送るか分かるよう【ベモーレ】【個人】を明記。
    for i, (acc, url, comment) in enumerate(suggestions, 1):
        send_line(f"📍【{ACCOUNT_LABEL}】スレッズ投稿{i}件目（@{acc}）👇\n{url}")
        send_line(comment)  # ← これ単独。丸ごとコピーできる
    print(f"[suggest] {ACCOUNT_LABEL} {len(suggestions)}件をLINE送信完了")


if __name__ == "__main__":
    main()
