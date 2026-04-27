"""
既存のChromeプロファイルを使ってThreadsにログインし、セッションを保存する
"""
import shutil
import os
from playwright.sync_api import sync_playwright

CHROME_PROFILE = os.path.expanduser(
    "~/Library/Application Support/Google/Chrome/Default"
)
TEMP_PROFILE = "/tmp/threads_chrome_profile"

def save_login():
    # Chromeプロファイルをコピー（Chromeが使用中でも読めるように）
    if os.path.exists(TEMP_PROFILE):
        shutil.rmtree(TEMP_PROFILE)
    print("Chromeプロファイルをコピー中...")
    shutil.copytree(CHROME_PROFILE, TEMP_PROFILE, ignore_dangling_symlinks=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=TEMP_PROFILE,
            channel="chrome",
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        page = context.new_page()
        print("Threadsを開いています...")
        page.goto("https://www.threads.com")
        page.wait_for_timeout(5000)

        # ログイン状態の確認
        if "threads.com" in page.url and "login" not in page.url:
            print("ログイン確認OK")
            context.storage_state(path="session.json")
            print("セッションを保存しました！")
        else:
            print(f"ログインできていません: {page.url}")

        context.close()

if __name__ == "__main__":
    save_login()
