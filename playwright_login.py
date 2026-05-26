import sys
from playwright.sync_api import sync_playwright

account = sys.argv[1] if len(sys.argv) > 1 else "personal"
session_file = "session_personal.json" if account == "personal" else "session.json"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto("https://www.threads.com")
    print("ブラウザが開きました。")
    print("「Continue with Instagram」をクリックして新しいパスワードでログインしてください。")
    print("Threadsのフィードが表示されたらEnterを押してください。")
    input()

    # フィード上でしばらく待機してJSによるCookie生成を待つ
    print("Cookie生成を待機中（15秒）...")
    page.wait_for_timeout(15000)

    # activityページに移動してセッションCookieを確実に取得
    print("activityページでCookieを取得中...")
    try:
        page.goto("https://www.threads.com/activity", timeout=20000, wait_until="networkidle")
        page.wait_for_timeout(5000)
    except Exception as e:
        print(f"activity取得失敗（続行）: {e}")

    # メインページに戻る
    try:
        page.goto("https://www.threads.com/", timeout=20000, wait_until="networkidle")
        page.wait_for_timeout(3000)
    except Exception:
        pass

    ctx.storage_state(path=session_file)

    # 取得できたCookieを表示
    import json
    data = json.load(open(session_file))
    names = [f"{c['domain']}|{c['name']}" for c in data.get('cookies', [])]
    print(f"\n保存したCookie ({len(names)}件):")
    for n in names:
        print(f"  {n}")
    print(f"\n{session_file} を保存しました")
    browser.close()
