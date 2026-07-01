"""Playwright で Threads にログインしてセッションCookieを取得する。

使い方:
  python3 playwright_login.py bemolle   → session.json 保存
  python3 playwright_login.py           → session_personal.json 保存

⭐ Chrome専用プロファイルを使う（永続ログイン済み）:
    bemolle → Profile 4 (@bemolle_diet 専用)
    個人    → Profile 3 (@aya_kuroki_0929 専用)
   一時ディレクトリにコピーして使うので、Chrome起動中でもOK。元プロファイルは無傷。
"""
import sys
import os
import shutil
import tempfile
from playwright.sync_api import sync_playwright

account = sys.argv[1] if len(sys.argv) > 1 else "personal"

# アカウントごとの設定
if account == "bemolle":
    session_file = "session.json"
    chrome_profile = "Profile 4"  # bemolle_diet 専用プロファイル
    expected_user = "bemolle_diet"
    label = "ベモーレ"
else:
    session_file = "session_personal.json"
    chrome_profile = "Profile 3"  # aya_kuroki_0929 専用プロファイル
    expected_user = "aya_kuroki_0929"
    label = "個人"

# 元プロファイルをコピー（Chrome起動中でもロックされないように）
src_profile = os.path.expanduser(f"~/Library/Application Support/Google/Chrome/{chrome_profile}")
if not os.path.exists(src_profile):
    print(f"❌ Chromeプロファイルが見つかりません: {src_profile}")
    sys.exit(1)

tmp_root = tempfile.mkdtemp(prefix="playwright-chrome-")
dest_profile = os.path.join(tmp_root, "Default")

print(f"📋 {label} 用に {chrome_profile} (@{expected_user}) を一時コピー中...")
shutil.copytree(src_profile, dest_profile, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns('Cache*', 'Code Cache*', 'GPUCache',
                                              'Service Worker', 'Application Cache'))

print(f"🌐 Chrome (persistent context) を起動中...")

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=tmp_root,
        channel="chrome",
        headless=False,
        locale="ja-JP",
        timezone_id="Asia/Tokyo",
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://www.threads.com")

    print("\n" + "=" * 60)
    print(f"📱 ブラウザが開きました（{label}: @{expected_user}）")
    print(f"   専用プロファイルなので、そのままフィードが表示されるはずです。")
    print(f"   もしログイン画面が出たら @{expected_user} でログインしてください。")
    print(f"   フィード表示を確認したらターミナルに戻って Enter を押してください。")
    print("=" * 60 + "\n")
    input("フィード表示を確認したら Enter を押してください: ")

    print("Cookie生成を待機中（15秒）...")
    page.wait_for_timeout(15000)

    print("activityページでCookieを取得中...")
    try:
        page.goto("https://www.threads.com/activity", timeout=20000, wait_until="networkidle")
        page.wait_for_timeout(5000)
    except Exception as e:
        print(f"activity取得失敗（続行）: {e}")

    try:
        page.goto("https://www.threads.com/", timeout=20000, wait_until="networkidle")
        page.wait_for_timeout(3000)
    except Exception:
        pass

    ctx.storage_state(path=session_file)

    import json
    data = json.load(open(session_file))
    names = [f"{c['domain']}|{c['name']}" for c in data.get('cookies', [])]
    print(f"\n✅ 保存したCookie ({len(names)}件):")
    for n in names:
        print(f"  {n}")
    print(f"\n💾 {session_file} を保存しました")
    print(f"\n次のステップ：")
    print(f"  python3 slim_session.py {session_file} > session_slim.json")
    if account == "bemolle":
        print(f"  gh secret set THREADS_SESSION < session_slim.json")
    else:
        print(f"  gh secret set THREADS_SESSION_PERSONAL < session_slim.json")
    ctx.close()

try:
    shutil.rmtree(tmp_root, ignore_errors=True)
except Exception:
    pass
