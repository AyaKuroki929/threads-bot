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
        try:
            page.goto("https://www.threads.com/activity", timeout=12000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            activity_url = page.url
            if "login" in activity_url or "instagram.com" in activity_url:
                print("[comment] activityページがloginにリダイレクト → セッション無効")
                return False
            print("[comment] ログイン状態を確認（URLチェック + activityページ確認）")
            return "threads.com" in activity_url
        except Exception:
            # activityページ確認失敗 → メインページチェックのみで判断（フォールバック）
            print("[comment] ログイン状態を確認（URLチェックのみ、activityページ確認失敗）")
            return True
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

# ① 起動時config確認ログ（デフォルト使用中の変数を警告）
_CONFIG_CHECK = {
    "MAX_COMMENTS_PER_RUN":   (post.MAX_COMMENTS_PER_RUN,           6),
    "COMMENT_COOLDOWN_DAYS":  (post.COMMENT_COOLDOWN_DAYS,          30),
    "COMMENT_MIN_POOL":       (post.COMMENT_MIN_POOL,               100),
    "GLOBAL_RATELIMIT_HOURS": (post.COMMENT_GLOBAL_RATELIMIT_HOURS, 12),
    "COMMENT_WAIT_MIN_MS":    (post.COMMENT_WAIT_MIN_MS,            4000),
    "COMMENT_WAIT_MAX_MS":    (post.COMMENT_WAIT_MAX_MS,            8000),
}
_config_warn = []
for _k, (_v, _default) in _CONFIG_CHECK.items():
    if os.environ.get(_k) is None:
        _config_warn.append(f"  ⚠️ {_k}={_v}（未設定→デフォルト{_default}）")
    else:
        print(f"  [config] {_k}={_v}")

# ③ COMMENT_COOLDOWN_DAYS が未設定のときだけLINE通知（その他はログのみ）
_critical_warn = [w for w in _config_warn if "COMMENT_COOLDOWN_DAYS" in w]
if _config_warn:
    _warn_header = f"[config] ⚠️ {'個人' if account == 'personal' else 'ベモーレ'} デフォルト値使用中の変数:"
    print(_warn_header)
    for _w in _config_warn:
        print(_w)
    _config_warn_msg = _warn_header + "\n" + "\n".join(_critical_warn) if _critical_warn else None
    _line_token_early = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if _line_token_early and _config_warn_msg:
        import urllib.request as _ureq
        _body = json.dumps({"messages": [{"type": "text", "text": _config_warn_msg}]}).encode()
        _req = _ureq.Request(
            "https://api.line.me/v2/bot/message/broadcast",
            data=_body,
            headers={"Authorization": f"Bearer {_line_token_early}", "Content-Type": "application/json"},
        )
        try:
            _ureq.urlopen(_req, timeout=10)
        except Exception:
            pass

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

    # ② 実行前プールヘルスチェック（eligible が少ないうちに警告）
    if session_ok and not dry_run:
        try:
            with open(post.COMMENT_TARGETS_FILE, encoding="utf-8") as _f:
                _targets = json.load(_f)
            with open(post.COMMENTED_FILE, encoding="utf-8") as _f:
                _cmted = json.load(_f)
            _eligible = [t for t in _targets if not post._already_commented_recently(_cmted, t.get("account", ""))]
            _warn_th = post.MAX_COMMENTS_PER_RUN * 3  # 3回分を下回ったら警告
            print(f"[pool] 実行前 eligible={len(_eligible)} / pool={len(_targets)} / 警告閾値={_warn_th}")
            if len(_eligible) < _warn_th:
                _acct_label = "個人" if account == "personal" else "ベモーレ"
                _pool_warn = (
                    f"⚠️ {_acct_label} プール残り少\n"
                    f"eligible={len(_eligible)}件 / pool={len(_targets)}件\n"
                    f"自動発掘を実行しますが今後の運用要確認\n"
                    f"https://github.com/AyaKuroki929/threads-bot/actions"
                )
                if line_token:
                    _send_line(line_token, _pool_warn)
                    print("[pool] プール残り少 LINE通知送信")
        except FileNotFoundError:
            print("[pool] プールファイル未存在（初回実行）")
        except Exception as _e:
            print(f"[pool] ヘルスチェック失敗: {_e}")

    results = []
    if session_ok:
        results = post._do_auto_comments(page, dry_run=dry_run)
    else:
        print("[comment] セッション復旧失敗 → スキップ")

    ok = sum(1 for r in results if r.get("status") == "ok")
    skipped = sum(1 for r in results if r.get("status") == "skipped")
    errors = sum(1 for r in results if r.get("status") == "error")
    # 投稿後検証: 「送信できた」と「実際にThreadsへ反映された」は別物。
    # verified True=反映確認 / False=反映されず（アクション制限の疑い） / None=確認不能。
    verified_ok = sum(1 for r in results if r.get("status") == "ok" and r.get("verified") is True)
    unverified_fail = sum(1 for r in results if r.get("status") == "ok" and r.get("verified") is False)
    print(f"[comment] 完了: ok={ok}（反映確認={verified_ok} 反映されず={unverified_fail}） skipped={skipped} error={errors}")

    # 実行後に更新済みCookieを保存（GH Secretsへの書き戻しに使う）
    # dry_run でも auto-login が成功した場合はセッションを保存する（次回 2FA 再発防止）
    if session_ok:
        context.storage_state(path=session_file)
        print(f"[comment] セッション更新を保存: {session_file}")

    browser.close()

# 0件コメントの場合はLINE通知（dry-runは除く）
if not dry_run and ok == 0 and session_ok:
    account_label = "個人" if account == "personal" else "ベモーレ"
    if line_token:
        # エラー種別を細分化して原因を一目で判断できるようにする
        disabled = sum(1 for r in results if r.get("status") == "error" and "コメント制限アカウント" in r.get("error", ""))
        throttle_errors = sum(1 for r in results if r.get("status") == "error" and "コメント制限アカウント" not in r.get("error", ""))
        filtered = sum(1 for r in results if r.get("status") == "skipped")
        # グローバルスロットリング休止中かどうか確認
        commented_file = os.environ.get("COMMENTED_FILE", "")
        throttle_active = False
        if commented_file and os.path.exists(commented_file):
            try:
                with open(commented_file, encoding="utf-8") as _f:
                    _cd = json.load(_f)
                username = os.environ.get("THREADS_USERNAME", "")
                _gl_key = f"/{username}/_global_ratelimit"
                if _gl_key in _cd:
                    from datetime import datetime
                    _gl_ts = datetime.strptime(_cd[_gl_key], "%Y-%m-%d %H:%M:%S")
                    throttle_active = (datetime.now() - _gl_ts).total_seconds() < 6 * 3600
            except Exception:
                pass
        throttle_line = "\n🚫 スロットリング休止中（6h）" if throttle_active else ""
        # ok=0 のとき post.py の A ガードによりプールは温存される（恒久削除は ok>0 時のみ）。
        # 「返信ボタン欠落」が大量＝相手ではなく bot 自身のセッション切れ/アカウント制限の疑い。
        # その場合は犯人を取り違えないよう、再ログイン手順を添えて明示通知する。
        session_basename = os.path.basename(session_file)
        if disabled >= 5:
            msg = (
                f"⚠️ {account_label} 自動コメント 0件 — セッション切れ/アカウント制限の疑い{throttle_line}\n"
                f"全{disabled}件で返信ボタンが表示されません（プールは温存・削除なし）\n"
                f"→ パスワード変更直後などは再ログインが必要かも:\n"
                f"  python3 playwright_login.py {account}\n"
                f"  gh secret set {session_env_key} --repo AyaKuroki929/threads-bot < {session_basename}\n"
                f"https://github.com/AyaKuroki929/threads-bot/actions"
            )
        else:
            msg = (
                f"⚠️ {account_label} 自動コメント 0件{throttle_line}\n"
                f"処理:{len(results)}件\n"
                f"  返信無効アカウント:{disabled}件（プール温存）\n"
                f"  Playwrightエラー:{throttle_errors}件\n"
                f"  フィルタースキップ:{filtered}件\n"
                f"https://github.com/AyaKuroki929/threads-bot/actions"
            )
        _send_line(line_token, msg)
        print(f"[comment] LINE通知送信: 0件アラート")

# ok>0 なのに1件も反映確認できず、かつ明確に「反映されず」が出ている場合は
# シャドウ/アクション制限の疑い。従来は偽のok=Nで無音だった失敗を必ず通知する。
elif not dry_run and ok > 0 and verified_ok == 0 and unverified_fail >= 1 and session_ok:
    account_label = "個人" if account == "personal" else "ベモーレ"
    if line_token:
        msg = (
            f"⚠️ {account_label} コメント{ok}件送信したが、Threadsに1件も反映されていません\n"
            f"アクション制限/シャドウ制限の疑い → 数日アカウントを休ませるのが回復の近道です\n"
            f"https://github.com/AyaKuroki929/threads-bot/actions"
        )
        _send_line(line_token, msg)
        print(f"[comment] LINE通知送信: 反映ゼロ警告")
