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
        # SPA対策: networkidleまで待ち、ログイン/未ログインのサインの描画も待つ。
        # 早読みするとDOM一次判定が毎回失敗し、弱いURLフォールバックだけで
        # 「有効」と誤判定した死にCookieをSecretにアップロードしてしまう。
        try:
            page.goto("https://www.threads.com", wait_until="networkidle", timeout=30000)
        except Exception:
            page.goto("https://www.threads.com", wait_until="domcontentloaded")
        try:
            page.wait_for_selector(
                '[aria-label*="Create"], [aria-label*="作成"], [aria-label*="新規スレッド"], a[href*="/login"]',
                timeout=12000)
        except Exception:
            pass
        page.wait_for_timeout(2000)

        url = page.url
        title = page.title()
        print(f"URL: {url}")
        print(f"Title: {title}")

        # パスワード変更後に出る再認証モーダルを最優先で検出
        reauth_selectors = [
            'button:has-text("Continue with Instagram")',
            'a:has-text("Continue with Instagram")',
            'div[role="button"]:has-text("Continue with Instagram")',
        ]
        for sel in reauth_selectors:
            if page.locator(sel).first.count() > 0:
                print("❌ 再認証モーダル検出（Say more with Threads）")
                print("   → Chromeでthreads.comを開き「Continue with Instagram」をクリックして")
                print("     新しいパスワードでログインし直してから、もう一度extract_cookies2.pyを実行してください")
                page.screenshot(path="verify.png", full_page=False)
                print("スクリーンショット保存: verify.png")
                browser.close()
                return False

        # ログイン済みなら投稿ボタンが見える
        logged_in_selectors = [
            '[aria-label*="Create"]',
            '[aria-label*="作成"]',
            '[aria-label*="新しい"]',
            '[aria-label*="新規スレッド"]',
            'a[href="/intent/post"]',
            'a[href^="/@"]',
        ]
        found = None
        for sel in logged_in_selectors:
            if page.locator(sel).first.is_visible():
                found = sel
                break

        if found:
            print(f"✅ ログイン状態を確認：{found}")
            page.screenshot(path="verify.png", full_page=False)
            print("スクリーンショット保存: verify.png")
            browser.close()
            return True

        # DOMセレクタが見つからない場合はURL確認で二次判定。
        # ただしURLだけの合格は弱い（ログイン壁がSPA内表示だとURLは変わらない）ため、
        # activityページで「loginリンクが無い」ことまで確認して初めて合格とする。
        if "threads.com" in url and "login" not in url and "instagram.com" not in url:
            page.goto("https://www.threads.com/activity", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(3000)
            activity_url = page.url
            has_login_link = page.locator('a[href*="/login"]').count() > 0
            if ("threads.com" in activity_url and "login" not in activity_url
                    and "instagram.com" not in activity_url and not has_login_link):
                print(f"✅ ログイン状態を確認（URL+loginリンク不在のフォールバック）")
                page.screenshot(path="verify.png", full_page=False)
                print("スクリーンショット保存: verify.png")
                browser.close()
                return True

        print("❌ ログインできていません。")
        if page.locator('text=/ログイン|Log in/').first.is_visible():
            print("   → ログインボタンが見えています（セッション切れ）")
        page.screenshot(path="verify.png", full_page=False)
        print("スクリーンショット保存: verify.png")
        browser.close()
        return False

if __name__ == "__main__":
    verify()
