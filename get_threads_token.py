"""
Threads 公式API - OAuthトークン取得スクリプト
Usage:
  export META_APP_SECRET=ここにシークレットを貼る
  python3 get_threads_token.py bemolle    # bemolle_diet アカウント用
  python3 get_threads_token.py personal   # aya_kuroki_0929 アカウント用

完了すると GH Secrets 登録コマンドが表示されます。
"""
import http.server
import threading
import urllib.parse
import urllib.request
import urllib.error
import json
import sys
import os
import time

APP_ID = "985270787180212"
APP_SECRET = os.environ.get("META_APP_SECRET", "")
REDIRECT_URI = "http://localhost:3000/callback"
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
        print("  export META_APP_SECRET=ここにシークレットを貼る")
        print()
        print("Meta Developer Console での確認方法:")
        print("  1. https://developers.facebook.com/apps/ を開く")
        print("  2. 'Threads Auto Post' アプリを選択")
        print("  3. 設定 > ベーシック > アプリシークレット をコピー")
        sys.exit(1)

    print(f"=== {account_info['username']} のトークン取得を開始 ===")
    print()
    print("【重要】ブラウザでの操作:")
    print(f"  Chrome で @{account_info['username']} としてログインしているプロファイルを使ってください")
    print()

    auth_code = [None]

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if "code" in params:
                auth_code[0] = params["code"][0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"""
                <html><body style="font-family:sans-serif;text-align:center;margin-top:100px">
                <h1 style="color:green">&#10003; 認証完了！</h1>
                <p>このタブは閉じてターミナルに戻ってください</p>
                </body></html>
                """)
            else:
                error = params.get("error_description", ["不明なエラー"])[0]
                self.send_response(400)
                self.end_headers()
                self.wfile.write(f"<h1>エラー: {error}</h1>".encode())
        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("localhost", 3000), CallbackHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    auth_url = (
        "https://threads.net/oauth/authorize"
        f"?client_id={APP_ID}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
        f"&scope={SCOPE}"
        f"&response_type=code"
    )

    print("以下のURLをブラウザで開いてください（コピーして貼り付け）:")
    print()
    print(auth_url)
    print()
    print("認証後、自動でトークンが取得されます。最大90秒待ちます...")
    print()

    for _ in range(90):
        if auth_code[0]:
            break
        time.sleep(1)
    else:
        print("タイムアウト: 90秒以内に認証が完了しませんでした")
        sys.exit(1)

    server.shutdown()
    print(f"認証コード取得完了。トークン交換中...")

    # 短期トークン取得
    try:
        data = urllib.parse.urlencode({
            "client_id": APP_ID,
            "client_secret": APP_SECRET,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
            "code": auth_code[0],
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
        print(f"⚠️  アカウント不一致！")
        print(f"   期待: user_id={expected_uid} (@{account_info['username']})")
        print(f"   実際: user_id={got_user_id}")
        print(f"   @{account_info['username']} でログインしているChromeプロファイルを使ってください")
        sys.exit(1)

    print(f"✅ user_id 確認: {got_user_id} (@{account_info['username']})")

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
    print(f"✅ 長期トークン取得成功！（有効期間: {expires_days}日）")
    print("=" * 60)
    print()
    print("▼ 以下のコマンドをターミナルで実行してください:")
    print()
    secret_name = account_info["secret_name"]
    print(f'printf "%s" "{long_token}" | gh secret set {secret_name} --repo AyaKuroki929/threads-bot')
    print()
    print(f"（完了後、もう一方のアカウントも同様に取得してください）")
