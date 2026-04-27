"""
最新投稿をクリックしてスレッド詳細を確認
"""
import os
from playwright.sync_api import sync_playwright

SESSION_FILE = os.path.join(os.path.dirname(__file__), "session.json")

def check():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=SESSION_FILE)
        page = context.new_page()
        page.goto("https://www.threads.com/@bemolle_diet", wait_until="domcontentloaded")
        page.wait_for_timeout(6000)

        # 最新のthreads(投稿)をクリック
        first_post = page.locator('[data-pressable-container="true"]').first
        first_post.click()
        page.wait_for_timeout(4000)
        page.screenshot(path="latest_detail.png", full_page=True)
        print("スクリーンショット: latest_detail.png")
        browser.close()

if __name__ == "__main__":
    check()
