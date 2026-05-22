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

print(f"[comment] アカウント: {os.environ.get('THREADS_USERNAME')} / dry_run={dry_run}")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        storage_state=session_file,
        locale="ja-JP",
        timezone_id="Asia/Tokyo",
    )
    page = context.new_page()

    results = post._do_auto_comments(page, dry_run=dry_run)

    ok = sum(1 for r in results if r.get("status") == "ok")
    skipped = sum(1 for r in results if r.get("status") == "skipped")
    errors = sum(1 for r in results if r.get("status") == "error")
    print(f"[comment] 完了: ok={ok} skipped={skipped} error={errors}")

    # 実行後に更新済みCookieを保存（GH Secretsへの書き戻しに使う）
    if not dry_run:
        context.storage_state(path=session_file)
        print(f"[comment] セッション更新を保存: {session_file}")

    browser.close()

# 0件コメントの場合はLINE通知（dry-runは除く）
if not dry_run and ok == 0 and len(results) > 0:
    line_token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    account_label = "個人" if account == "personal" else "ベモーレ"
    if line_token:
        import urllib.request as _req
        import urllib.parse as _parse
        msg = f"⚠️ {account_label} 自動コメント 0件\n処理:{len(results)}件すべてスキップまたはエラー\nAPIキー切れ・フィルター過多の可能性あり"
        body = json.dumps({"messages": [{"type": "text", "text": msg}]}).encode()
        req = _req.Request(
            "https://api.line.me/v2/bot/message/broadcast",
            data=body,
            headers={"Authorization": f"Bearer {line_token}", "Content-Type": "application/json"},
        )
        try:
            _req.urlopen(req, timeout=10)
            print(f"[comment] LINE通知送信: 0件アラート")
        except Exception as e:
            print(f"[comment] LINE通知失敗: {e}")
