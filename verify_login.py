"""
session.jsonが有効かを確認（ヘッドレスでthreads.comを開いてログイン状態を見る）
"""
import os
import argparse
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--session', default=os.path.join(os.path.dirname(__file__), "session.json"))
args = parser.parse_args()
SESSION_FILE = args.session

def verify():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=SESSION_FILE)
        page = context.new_page()
        page.goto("https://www.threads.com", wait_until="domcontentloaded")
        page.wait_for_timeout(5000)

        url = page.url
        title = page.title()
        print(f"URL: {url}")
        print(f"Title: {title}")

        # ログイン中なら投稿ボタン or プロフィールアイコンが見える
        logged_in_selectors = [
            'a[href="/intent/post"]',
            '[aria-label*="新しい"]',
            '[aria-label*="Create"]',
            'a[href^="/@"]',
        ]
        found = None
        for sel in logged_in_selectors:
            if page.locator(sel).first.is_visible():
                found = sel
                break

        if found:
            print(f"✅ ログイン状態を確認：{found}")
        else:
            print("❌ ログインできていません。ログイン画面の可能性。")
            # ログインボタンがあるか
            if page.locator('text=/ログイン|Log in/').first.is_visible():
                print("   → ログインボタンが見えています")

        page.screenshot(path="verify.png", full_page=False)
        print("スクリーンショット保存: verify.png")
        browser.close()

if __name__ == "__main__":
    verify()
