"""
Threads 公式API - OAuthトークン取得スクリプト
Usage:
  export META_APP_SECRET=ここにシークレットを貼る
  python3 get_threads_token.py bemolle    # bemolle_diet アカウント用
  python3 get_threads_token.py personal   # aya_kuroki_0929 アカウント用
"""
import urllib.parse
import urllib.request
import urllib.error
import json
import sys
import os

APP_ID = "1497479218824264"
APP_SECRET = os.environ.get("META_APP_SECRET", "")
REDIRECT_URI = "https://shikisai.work"
SCOPE = "threads_basic,threads_content_publish"

ACCOUNT_MAP = {
    "bemolle":  {"username": "bemolle_diet",    "user_id": "73523451930", "secret_name": "THREADS_API_TOKEN_BEMOLLE"},
    "personal": {"username": "aya_kuroki_0929", "user_id": "63084943935", "secret_name": "THREADS_API_TOKEN_PERSONAL"},
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ACCOUNT_MAP:
        print("Usage: python3 get_threads_token.py bemolle|personal")
        sys.exit(1)

    account_key = sys.argv[1]
    account_info = ACCOUNT_MAP[account_key]

    if not APP_SECRET:
        print("エラー: META_APP_SECRET 環境変数を設定してください")
        sys.exit(1)

    print(f"=== {account_info['username']} のトークン取得 ===")
    print()

    auth_url = (
        "https://threads.net/oauth/authorize"
        f"?client_id={APP_ID}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
        f"&scope={SCOPE}"
        f"&response_type=code"
    )

    print("【手順】")
    print(f"1. @{account_info['username']} でログインしているChromeプロファイルで以下のURLを開く")
    print()
    print(auth_url)
    print()
    print("2. 「許可」をクリックする")
    print("3. ブラウザが shikisai.work にリダイレクトされる（ページが開く or エラーになる）→ それで OK")
    print("4. そのときのアドレスバーの URL をコピーして、ここに貼り付ける")
    print("   (https://shikisai.work?code=XXXXX... という形式)")
    print()

    raw = input("アドレスバーの URL を貼り付け（https://localhost?code=... の形）: ").strip()
    parsed = urllib.parse.urlparse(raw)
    params = urllib.parse.parse_qs(parsed.query)

    if "code" not in params:
        print(f"エラー: URL に code が見つかりません: {raw[:100]}")
        sys.exit(1)

    auth_code = params["code"][0]
    print(f"認証コード取得: {auth_code[:10]}...")

    # 短期トークン取得
    try:
        data = urllib.parse.urlencode({
            "client_id": APP_ID,
            "client_secret": APP_SECRET,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
            "code": auth_code,
        }).encode()
        req = urllib.request.Request(
            "https://graph.threads.net/oauth/access_token",
            data=data, method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            short_data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"短期トークン取得失敗 HTTP {e.code}: {body}")
        sys.exit(1)

    short_token = short_data["access_token"]
    got_user_id = str(short_data.get("user_id", ""))
    expected_uid = account_info["user_id"]

    if got_user_id and got_user_id != expected_uid:
        print(f"アカウント不一致！")
        print(f"  期待: {expected_uid} (@{account_info['username']})")
        print(f"  実際: {got_user_id}")
        print(f"  @{account_info['username']} でログインしているプロファイルを使ってください")
        sys.exit(1)

    print(f"user_id 確認: {got_user_id}")

    # 長期トークン取得（60日有効）
    try:
        url = (
            "https://graph.threads.net/access_token"
            f"?grant_type=th_exchange_token"
            f"&client_secret={APP_SECRET}"
            f"&access_token={short_token}"
        )
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            long_data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"長期トークン取得失敗 HTTP {e.code}: {body}")
        sys.exit(1)

    long_token = long_data["access_token"]
    expires_days = long_data.get("expires_in", 5184000) // 86400

    print()
    print("=" * 60)
    print(f"長期トークン取得成功！（有効期間: {expires_days}日）")
    print("=" * 60)
    print()
    print("以下のコマンドをターミナルで実行してください:")
    print()
    secret_name = account_info["secret_name"]
    print(f'printf "%s" "{long_token}" | gh secret set {secret_name} --repo AyaKuroki929/threads-bot')
