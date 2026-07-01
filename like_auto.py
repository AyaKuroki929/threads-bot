#!/usr/bin/env python3
"""Threads 自動いいねスクリプト（bemolle / personal 両対応）

ターゲットリスト（comment_targets.json）から投稿を探していいねする。
いいね済みは liked_posts.json に記録して重複防止。
残り50件以下になったらキーワード検索で新規アカウントを自動補充。
"""
import os, sys, json, time, random
import urllib.request
from datetime import datetime, timedelta

_BASE = os.path.dirname(os.path.abspath(__file__))


def _line_alert(text: str):
    """いいね機能の重要トラブル時のみ管理者LINEに通知（Claude通知Bot経由）。
    コメント/suggest系の通知は停止中でも、この関数は動作する。"""
    token = os.environ.get("ADMIN_NOTIFY_LINE_TOKEN") or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        print(f"[like_alert] LINEトークン無し: {text}")
        return
    try:
        body = json.dumps({"messages": [{"type": "text", "text": text}]}).encode()
        req = urllib.request.Request(
            "https://api.line.me/v2/bot/message/broadcast",
            data=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[like_alert] LINE送信失敗: {e}")

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in ("bemolle", "personal") else "bemolle"

if ACCOUNT == "personal":
    SESSION_ENV   = "THREADS_SESSION_PERSONAL"
    SESSION_FILE  = os.path.join(_BASE, "session_personal.json")
    TARGETS_FILE  = os.path.join(_BASE, "comment_targets_personal.json")
    LIKED_FILE    = os.path.join(_BASE, "liked_posts_personal.json")
    KEYWORDS_FILE = os.path.join(_BASE, "comment_search_keywords_personal.json")
    USERNAME      = "aya_kuroki_0929"
    LABEL         = "個人"
else:
    SESSION_ENV   = "THREADS_SESSION"
    SESSION_FILE  = os.path.join(_BASE, "session.json")
    TARGETS_FILE  = os.path.join(_BASE, "comment_targets.json")
    LIKED_FILE    = os.path.join(_BASE, "liked_posts.json")
    KEYWORDS_FILE = os.path.join(_BASE, "comment_search_keywords.json")
    USERNAME      = "bemolle_diet"
    LABEL         = "ベモーレ"

LIKES_PER_RUN      = 5    # 1回あたりのいいね上限
LIKE_COOLDOWN_DAYS = 30   # 同アカに再いいねしない日数
REPLENISH_THRESHOLD = 50  # ターゲット残りがこの件数以下で補充発動
DISCOVER_COUNT      = 15  # 1回の補充で追加する最大件数
MAX_FAIL            = 8   # 連続失敗でセッション異常と判断し中断


def _load_liked():
    try:
        return json.load(open(LIKED_FILE, encoding="utf-8"))
    except Exception:
        return {}


def _save_liked(data):
    json.dump(data, open(LIKED_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def _already_liked(liked, account):
    ts = liked.get(account)
    if not ts:
        return False
    try:
        return (datetime.utcnow() - datetime.fromisoformat(ts)).days < LIKE_COOLDOWN_DAYS
    except Exception:
        return False


def _load_targets():
    try:
        return json.load(open(TARGETS_FILE, encoding="utf-8"))
    except Exception:
        return []


def _save_targets(data):
    json.dump(data, open(TARGETS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def _try_like(page, acc):
    """プロフィールページの最初の投稿をいいねする。成功でTrue。"""
    try:
        page.goto(f"https://www.threads.com/@{acc}", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(random.randint(2500, 4000))

        # 最初の投稿カードのいいねボタンを探す
        # Threads の like ボタンは aria-label に "Like" または "いいね" を含む
        like_btns = page.locator(
            '[aria-label*="Like"], [aria-label*="いいね"], [aria-label*="like"]'
        )
        if like_btns.count() == 0:
            print(f"[like] @{acc} いいねボタンが見つからない → スキップ")
            return False

        btn = like_btns.first
        label = (btn.get_attribute("aria-label") or "").lower()

        # 既にいいね済みの場合はスキップ（aria-label に "unlike" が含まれる）
        if "unlike" in label:
            print(f"[like] @{acc} 既にいいね済み → スキップ（記録して次へ）")
            return None  # None = 既いいね（カウントせず記録だけ）

        btn.scroll_into_view_if_needed()
        btn.click()
        page.wait_for_timeout(random.randint(3000, 8000))  # Bot検知回避ランダム待機
        print(f"[like] @{acc} ❤️ いいね完了")
        return True

    except Exception as e:
        print(f"[like] @{acc} エラー: {e}")
        return False


def main():
    # セッションファイル書き出し（GH Actions環境変数から）
    session_val = os.environ.get(SESSION_ENV, "")
    if session_val:
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            f.write(session_val)

    if not os.path.exists(SESSION_FILE):
        print(f"[like] {LABEL} セッションファイルなし → スキップ")
        return

    targets = _load_targets()
    liked   = _load_liked()

    eligible = [t for t in targets if not _already_liked(liked, t.get("account", ""))]
    random.shuffle(eligible)

    liked_count    = 0
    consecutive_fail = 0

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            storage_state=SESSION_FILE,
            locale="ja-JP",
            timezone_id="Asia/Tokyo"
        )
        page = ctx.new_page()

        for t in eligible:
            if liked_count >= LIKES_PER_RUN:
                break
            acc = t.get("account", "")
            if not acc:
                continue

            result = _try_like(page, acc)
            if result is True:
                liked[acc] = datetime.utcnow().isoformat()
                liked_count += 1
                consecutive_fail = 0
            elif result is None:
                # 既いいね済み → 記録して次へ
                liked[acc] = datetime.utcnow().isoformat()
                consecutive_fail = 0
            else:
                consecutive_fail += 1
                if consecutive_fail >= MAX_FAIL:
                    print(f"[like] {LABEL} 連続{MAX_FAIL}件失敗 → セッション異常の可能性。中断")
                    _line_alert(
                        f"🚨 【{LABEL}】自動いいねが停止しました\n\n"
                        f"連続{MAX_FAIL}件で「いいねボタンが見つからない」→ 中断\n"
                        f"セッション(Cookie)期限切れの可能性が高いです。\n\n"
                        f"対処：playwright_login.py で {LABEL} を再ログイン\n"
                        f"→ GitHub Secret {SESSION_ENV} を更新"
                    )
                    break

        # ターゲット補充（残りが REPLENISH_THRESHOLD 以下）
        remaining = [t for t in targets if not _already_liked(liked, t.get("account", ""))]
        if len(remaining) < REPLENISH_THRESHOLD:
            print(f"[like] {LABEL} ターゲット残り{len(remaining)}件 → 新規発掘開始")
            import post as _post
            _post.USERNAME             = USERNAME
            _post.COMMENT_KEYWORDS_FILE = KEYWORDS_FILE
            already_known = {t["account"] for t in targets}
            new_accounts = _post._discover_new_accounts(page, already_known, max_new=DISCOVER_COUNT)
            if new_accounts:
                targets.extend(new_accounts)
                _save_targets(targets)
                print(f"[like] {len(new_accounts)}件追加 → 合計{len(targets)}件")

        browser.close()

    _save_liked(liked)
    print(f"[like] {LABEL} 完了: {liked_count}件いいね")


if __name__ == "__main__":
    main()
