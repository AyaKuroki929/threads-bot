"""
ChromeのCookieをKeychain経由で復号化してPlaywright用session.jsonに変換する
"""
import sqlite3
import subprocess
import json
import shutil
import os
import time
import argparse

try:
    from Crypto.Cipher import AES
    from Crypto.Protocol.KDF import PBKDF2
except ImportError:
    os.system("pip3 install pycryptodome 2>/dev/null")
    from Crypto.Cipher import AES
    from Crypto.Protocol.KDF import PBKDF2

parser = argparse.ArgumentParser()
parser.add_argument('--profile', default='1', help='Chrome profile number (1=personal, 3=bemolle_diet)')
args = parser.parse_args()

CHROME_COOKIES_PATH = os.path.expanduser(
    f"~/Library/Application Support/Google/Chrome/Profile {args.profile}/Cookies"
)
TEMP_COOKIES = "/tmp/Cookies_copy"

def get_chrome_key():
    """macOS KeychainからChromeの暗号化キーを取得"""
    result = subprocess.run(
        ["security", "find-generic-password", "-a", "Chrome", "-s", "Chrome Safe Storage", "-w"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", "Chromium", "-s", "Chromium Safe Storage", "-w"],
            capture_output=True, text=True
        )
    password = result.stdout.strip().encode()
    salt = b"saltysalt"
    key = PBKDF2(password, salt, dkLen=16, count=1003)
    return key

def decrypt_cookie(encrypted_value, key):
    """AES-128-CBC復号化。Chrome v120+はSHA256(eTLD+1)32バイトのプレフィックス付き。

    macOSのChromeでは、復号後の先頭16バイトがランダムなpad、次に
    SHA256ハッシュ(32バイト)が続くのではなく、v10形式では先頭に32バイトの
    SHA256(host)ハッシュがそのまま付く。古い形式はプレフィックスなし。
    """
    try:
        if len(encrypted_value) < 3:
            return encrypted_value.decode("utf-8", errors="ignore")
        if encrypted_value[:3] != b"v10":
            return encrypted_value.decode("utf-8", errors="ignore")
        iv = b" " * 16  # 古いmacOS Chromeは固定IV(スペース16個)
        # v10(3バイト) + 暗号文
        ciphertext = encrypted_value[3:]
        cipher = AES.new(key, AES.MODE_CBC, IV=iv)
        decrypted = cipher.decrypt(ciphertext)
        # PKCS7パディング除去
        padding = decrypted[-1]
        if 1 <= padding <= 16:
            decrypted = decrypted[:-padding]
        # Chrome v120+ はSHA256(32バイト)プレフィックス付き
        # 先頭32バイトがASCIIでなければハッシュと見なして除去
        if len(decrypted) > 32 and not all(32 <= b < 127 or b in (9, 10, 13) for b in decrypted[:32]):
            decrypted = decrypted[32:]
        return decrypted.decode("utf-8", errors="ignore")
    except Exception:
        return ""

def extract_cookies():
    key = get_chrome_key()
    shutil.copy2(CHROME_COOKIES_PATH, TEMP_COOKIES)
    conn = sqlite3.connect(TEMP_COOKIES)
    cursor = conn.cursor()

    target_domains = ["threads.com", "threads.net", "instagram.com"]
    cookies_list = []

    for domain in target_domains:
        cursor.execute(
            "SELECT name, encrypted_value, host_key, path, expires_utc, is_secure, is_httponly FROM cookies WHERE host_key LIKE ?",
            (f"%{domain}%",)
        )
        rows = cursor.fetchall()
        for name, encrypted_value, host_key, path, expires_utc, is_secure, is_httponly in rows:
            value = decrypt_cookie(encrypted_value, key)
            if not value:
                continue
            # Chrome時刻（1601年基準マイクロ秒）をUnixタイムに変換
            if expires_utc and expires_utc > 0:
                expires = int(expires_utc / 1000000) - 11644473600
            else:
                expires = int(time.time()) + 86400 * 30
            # 過去の日時や不正な値を除外
            if expires < int(time.time()):
                expires = int(time.time()) + 86400 * 30
            cookie = {
                "name": name,
                "value": value,
                "domain": host_key if host_key.startswith(".") else f".{host_key}",
                "path": path or "/",
                "expires": expires,
                "httpOnly": bool(is_httponly),
                "secure": bool(is_secure),
                "sameSite": "None" if bool(is_secure) else "Lax"
            }
            cookies_list.append(cookie)

    conn.close()
    os.remove(TEMP_COOKIES)

    session = {"cookies": cookies_list, "origins": []}
    with open("session.json", "w") as f:
        json.dump(session, f, indent=2)

    print(f"{len(cookies_list)}件のクッキーを保存しました")
    for d in target_domains:
        count = sum(1 for c in cookies_list if d in c["domain"])
        print(f"  {d}: {count}件")

if __name__ == "__main__":
    extract_cookies()
