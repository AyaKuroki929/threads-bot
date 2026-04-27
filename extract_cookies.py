"""
ChromeのThreadsログイン情報をPlaywright用に変換して保存する
"""
import json
import browser_cookie3
import time

def extract_and_save():
    print("ChromeからThreadsのクッキーを取得中...")

    cookies_list = []

    # threads.com と instagram.com のクッキーを取得
    for domain in ["threads.com", "instagram.com"]:
        try:
            cookies = browser_cookie3.chrome(domain_name=domain)
            for c in cookies:
                cookie_dict = {
                    "name": c.name,
                    "value": c.value,
                    "domain": c.domain if c.domain.startswith(".") else f".{c.domain}",
                    "path": c.path or "/",
                    "expires": c.expires if c.expires else int(time.time()) + 86400 * 30,
                    "httpOnly": bool(c.has_nonstandard_attr("HttpOnly")),
                    "secure": bool(c.secure),
                    "sameSite": "Lax"
                }
                cookies_list.append(cookie_dict)
            print(f"  {domain}: {sum(1 for c in browser_cookie3.chrome(domain_name=domain))}件取得")
        except Exception as e:
            print(f"  {domain}: エラー - {e}")

    session = {"cookies": cookies_list, "origins": []}
    with open("session.json", "w") as f:
        json.dump(session, f, indent=2)

    print(f"\n合計 {len(cookies_list)} 件のクッキーを session.json に保存しました。")

if __name__ == "__main__":
    extract_and_save()
