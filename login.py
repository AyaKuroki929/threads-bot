"""
初回のみ実行：Chromeでログインしてセッションを保存する
"""
from playwright.sync_api import sync_playwright

def save_login():
    with sync_playwright() as p:
        # 実際のChromeブラウザを使用（Chromeのウィンドウが開く）
        context = p.chromium.launch_persistent_context(
            user_data_dir="/tmp/threads_session",
            channel="chrome",
            headless=False,
        )
        page = context.new_page()
        page.goto("https://www.threads.com")
        print("Chromeが開きました。Threadsにログインしてください。")
        print("ログイン完了を自動検出して保存します...")

        # ログイン完了（投稿ボタンが表示されるまで待機）
        page.wait_for_selector('a[href="/intent/post"], [aria-label*="新しい"], [aria-label*="Create"], div[role="button"]', timeout=180000)
        page.wait_for_timeout(3000)

        context.storage_state(path="session.json")
        print("セッションを保存しました！")
        context.close()

if __name__ == "__main__":
    save_login()
