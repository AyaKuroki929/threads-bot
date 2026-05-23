#!/usr/bin/env python3
"""
自動コメント専用スクリプト（GH Actions用）
使い方: python3 comment.py bemolle | python3 comment.py personal
"""
import os
import sys
import json

_BASE = os.path.dirname(os.path.abspath(__file__))

account = sys.argv[1] if len(sys.argv) > 1 else "bemolle"
dry_run = "--dry-run" in sys.argv

if account == "personal":
    os.environ.setdefault("THREADS_USERNAME", "aya_kuroki_0929")
    os.environ.setdefault("SESSION_FILE", os.path.join(_BASE, "session_personal.json"))
    os.environ.setdefault("COMMENT_TARGETS_FILE", os.path.join(_BASE, "comment_targets_personal.json"))
    os.environ.setdefault("COMMENTED_FILE", os.path.join(_BASE, "commented_posts_personal.json"))
    os.environ.setdefault("COMMENT_KEYWORDS_FILE", os.path.join(_BASE, "comment_search_keywords_personal.json"))
else:
    os.environ.setdefault("THREADS_USERNAME", "bemolle_diet")
    os.environ.setdefault("SESSION_FILE", os.path.join(_BASE, "session.json"))
    os.environ.setdefault("COMMENT_TARGETS_FILE", os.path.join(_BASE, "comment_targets.json"))
    os.environ.setdefault("COMMENTED_FILE", os.path.join(_BASE, "commented_posts.json"))

# セッションをenv varから復元（GH Actions）
session_env_key = "THREADS_SESSION_PERSONAL" if account == "personal" else "THREADS_SESSION"
session_data = os.environ.get(session_env_key, "")
session_file = os.environ["SESSION_FILE"]

if session_data:
    with open(session_file, "w", encoding="utf-8") as f:
        f.write(session_data)
    print(f"[comment] {session_env_key} からセッションを復元しました")
elif not os.path.exists(session_file):
    print(f"[comment] セッションファイルが見つかりません: {session_file}")
    sys.exit(1)

# post.pyのモジュール変数はimport時に確定するため、env var設定後にimport
import post  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402


def _send_line(token, msg):
    import urllib.request as _req
    body = json.dumps({"messages": [{"type": "text", "text": msg}]}).encode()
    req = _req.Request(
        "https://api.line.me/v2/bot/message/broadcast",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        _req.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[comment] LINE通知失敗: {e}")


def _is_logged_in(page):
    """Threadsのフィードを開いてログイン状態を確認する"""
    try:
        page.goto("https://www.threads.com/", timeout=20000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        # ログイン済みならフィードまたはプロフィールアイコンが存在する
        # ログアウト状態なら「Log in」ボタンが出る
        login_btn = page.query_selector('a[href*="login"], a[href*="instagram.com"]')
        if login_btn:
            return False
        # フィードのコンテンツが存在すれば OK
        feed = page.query_selector('div[role="main"], article, [data-pressable-container]')
        return feed is not None
    except Exception:
        return False


def _auto_login(page, username, password, line_token=""):
    """資格情報を使って自動ログインを試みる。2FAが必要な場合はFalseを返す"""
    print("[comment] セッション切れ検知 → 自動ログインを試みます")
    try:
        page.goto("https://www.threads.com/", timeout=20000, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # 「Log in」または「Continue with Instagram」ボタンをクリック
        for sel in ['a[href*="login"]', 'button:has-text("Log in")', 'a:has-text("Log in")']:
            btn = page.query_selector(sel)
            if btn:
                btn.click()
                page.wait_for_timeout(2000)
                break

        # Instagram ログインフォームに入力
        user_input = page.query_selector('input[name="username"], input[type="text"]')
        pass_input = page.query_selector('input[name="password"], input[type="password"]')
        if not user_input or not pass_input:
            print("[comment] ログインフォームが見つかりません")
            return False

        user_input.fill(username)
        pass_input.fill(password)
        page.keyboard.press("Enter")
        page.wait_for_timeout(5000)

        # 2FAチェック（認証コード入力欄が出たら失敗）
        twofa = page.query_selector('input[name="verificationCode"], input[aria-label*="code"], input[aria-label*="コード"]')
        if twofa:
            print("[comment] 2FA が必要です → 自動化できません")
            if line_token:
                _send_line(line_token, "⚠️ 個人アカウント セッション切れ\n2FA認証が必要なため自動ログイン不可。\npython3 playwright_login.py を実行してください。")
            return False

        # フィードが表示されれば成功
        page.wait_for_timeout(3000)
        feed = page.query_selector('div[role="main"], article, [data-pressable-container]')
        if feed:
            print("[comment] 自動ログイン成功 ✅")
            return True

        print("[comment] ログイン後フィードが確認できませんでした")
        return False
    except Exception as e:
        print(f"[comment] 自動ログインエラー: {e}")
        return False


print(f"[comment] アカウント: {os.environ.get('THREADS_USERNAME')} / dry_run={dry_run}")

ig_username = os.environ.get("IG_USERNAME_PERSONAL", "") if account == "personal" else ""
ig_password = os.environ.get("IG_PASSWORD_PERSONAL", "") if account == "personal" else ""
line_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        storage_state=session_file,
        locale="ja-JP",
        timezone_id="Asia/Tokyo",
    )
    page = context.new_page()

    # セッション有効性チェック（personalのみ）
    session_ok = True
    if account == "personal":
        session_ok = _is_logged_in(page)
        if not session_ok:
            if ig_username and ig_password:
                session_ok = _auto_login(page, ig_username, ig_password, line_token)
            else:
                print("[comment] IG_USERNAME_PERSONAL / IG_PASSWORD_PERSONAL 未設定 → 自動ログイン不可")
                if line_token:
                    _send_line(line_token, "⚠️ 個人アカウント セッション切れ\npython3 playwright_login.py を実行してください。")

    results = []
    if session_ok:
        results = post._do_auto_comments(page, dry_run=dry_run)
    else:
        print("[comment] セッション復旧失敗 → スキップ")

    ok = sum(1 for r in results if r.get("status") == "ok")
    skipped = sum(1 for r in results if r.get("status") == "skipped")
    errors = sum(1 for r in results if r.get("status") == "error")
    print(f"[comment] 完了: ok={ok} skipped={skipped} error={errors}")

    # 実行後に更新済みCookieを保存（GH Secretsへの書き戻しに使う）
    if not dry_run and session_ok:
        context.storage_state(path=session_file)
        print(f"[comment] セッション更新を保存: {session_file}")

    browser.close()

# 0件コメントの場合はLINE通知（dry-runは除く）
if not dry_run and ok == 0 and len(results) > 0:
    account_label = "個人" if account == "personal" else "ベモーレ"
    if line_token:
        restricted = sum(1 for r in results if r.get("status") == "error" and "制限" in r.get("error", ""))
        no_new_post = sum(1 for r in results if r.get("status") == "skipped" and "新投稿なし" in str(r.get("reason", "")))
        filtered = sum(1 for r in results if r.get("status") == "skipped") - no_new_post
        msg = (
            f"⚠️ {account_label} 自動コメント 0件\n"
            f"処理:{len(results)}件 制限:{restricted} フィルター:{filtered} エラー:{errors}\n"
            f"https://github.com/AyaKuroki929/threads-bot/actions"
        )
        _send_line(line_token, msg)
        print(f"[comment] LINE通知送信: 0件アラート")
