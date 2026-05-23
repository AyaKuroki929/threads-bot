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
    """ログイン状態を2段階で確認: メインページURL + 要認証ページのリダイレクト"""
    try:
        page.goto("https://www.threads.com/", timeout=20000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        current_url = page.url
        if "login" in current_url or "instagram.com" in current_url:
            return False
        if "threads.com" not in current_url:
            return False
        # 2段階目: 通知ページ（要ログイン）にアクセスしてリダイレクト確認
        page.goto("https://www.threads.com/activity", timeout=15000, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        activity_url = page.url
        if "login" in activity_url or "instagram.com" in activity_url:
            print("[comment] activityページがloginにリダイレクト → セッション無効")
            return False
        print("[comment] ログイン状態を確認（URLチェック + activityページ確認）")
        return "threads.com" in activity_url
    except Exception:
        return False


def _auto_login(page, username, password, line_token=""):
    """資格情報を使って自動ログインを試みる。2FAが必要な場合はFalseを返す"""
    print("[comment] セッション切れ検知 → 自動ログインを試みます")
    try:
        # 直接ログインページに遷移
        page.goto("https://www.threads.com/login", timeout=20000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # ログインフォームが見つからない場合はInstagramログインページも試みる
        user_input = None
        pass_input = None
        for attempt_url in [None, "https://www.instagram.com/accounts/login/"]:
            if attempt_url:
                page.goto(attempt_url, timeout=20000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
            user_input = page.query_selector('input[name="username"], input[type="text"]')
            pass_input = page.query_selector('input[name="password"], input[type="password"]')
            if user_input and pass_input:
                break

        if not user_input or not pass_input:
            print("[comment] ログインフォームが見つかりません")
            account_label = "ベモーレアカウント" if username and "bemolle" in username else "個人アカウント"
            if line_token:
                _send_line(line_token, f"⚠️ {account_label} セッション切れ\nログインフォームが見つかりません。手動でpython3 playwright_login.py を実行してください。")
            return False

        user_input.fill(username)
        pass_input.fill(password)
        page.keyboard.press("Enter")
        page.wait_for_timeout(6000)

        # 2FAチェック（認証コード入力欄が出たら失敗）
        twofa = page.query_selector('input[name="verificationCode"], input[aria-label*="code"], input[aria-label*="コード"]')
        if twofa:
            print("[comment] 2FA が必要です → 自動化できません")
            if line_token:
                _send_line(line_token, "⚠️ セッション切れ＋2FA必要\npython3 playwright_login.py を実行してください。")
            return False

        # ログイン後のURL確認（threads.com に戻れば成功）
        page.wait_for_timeout(3000)
        current_url = page.url
        if "threads.com" in current_url and "login" not in current_url:
            print("[comment] 自動ログイン成功 ✅")
            return True

        # Threadsへの遷移を試みる
        page.goto("https://www.threads.com/", timeout=20000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        if "threads.com" in page.url and "login" not in page.url:
            print("[comment] 自動ログイン成功 ✅")
            return True

        print("[comment] ログイン後Threadsに遷移できませんでした")
        return False
    except Exception as e:
        print(f"[comment] 自動ログインエラー: {e}")
        return False


print(f"[comment] アカウント: {os.environ.get('THREADS_USERNAME')} / dry_run={dry_run}")

if account == "personal":
    ig_username = os.environ.get("IG_USERNAME_PERSONAL", "")
    ig_password = os.environ.get("IG_PASSWORD_PERSONAL", "")
elif account == "bemolle":
    ig_username = os.environ.get("IG_USERNAME_BEMOLLE", "")
    ig_password = os.environ.get("IG_PASSWORD_BEMOLLE", "")
else:
    ig_username = ""
    ig_password = ""
line_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        storage_state=session_file,
        locale="ja-JP",
        timezone_id="Asia/Tokyo",
    )
    page = context.new_page()

    # セッション有効性チェック（全アカウント共通）
    session_ok = _is_logged_in(page)
    if not session_ok:
        if ig_username and ig_password:
            session_ok = _auto_login(page, ig_username, ig_password, line_token)
        else:
            creds_env = "IG_USERNAME_PERSONAL / IG_PASSWORD_PERSONAL" if account == "personal" else "IG_USERNAME_BEMOLLE / IG_PASSWORD_BEMOLLE"
            account_label = "個人アカウント" if account == "personal" else "ベモーレアカウント"
            print(f"[comment] {creds_env} 未設定 → 自動ログイン不可")
            if line_token:
                _send_line(line_token, f"⚠️ {account_label} セッション切れ\npython3 playwright_login.py を実行してください。")

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
