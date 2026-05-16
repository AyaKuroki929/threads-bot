from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import os
import urllib.request
import urllib.parse

APP_ID = os.environ.get("THREADS_APP_ID", "985270787180212")
APP_SECRET = os.environ.get("META_APP_SECRET", "")
CALLBACK_URL = os.environ.get("CALLBACK_URL", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def exchange_code(code):
    data = urllib.parse.urlencode({
        "client_id": APP_ID,
        "client_secret": APP_SECRET,
        "redirect_uri": CALLBACK_URL,
        "code": code,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(
        "https://graph.threads.net/oauth/access_token",
        data=data,
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def get_long_lived_token(short_token):
    url = (
        f"https://graph.threads.net/access_token"
        f"?grant_type=th_exchange_token"
        f"&client_secret={APP_SECRET}"
        f"&access_token={short_token}"
    )
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read())


def get_user_info(token):
    url = f"https://graph.threads.net/me?fields=id,username&access_token={token}"
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read())


def save_to_supabase(user_id, username, access_token, expires_in):
    from datetime import datetime, timedelta
    expires_at = (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat() + "Z"

    # upsert by threads_user_id
    data = json.dumps({
        "threads_user_id": user_id,
        "salon_name": username,
        "access_token": access_token,
        "token_expires_at": expires_at,
        "is_active": True,
    }).encode()

    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/salons",
        data=data,
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        },
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status


SUCCESS_HTML = """<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"><title>接続完了</title>
<style>body{font-family:sans-serif;text-align:center;padding:60px;background:#f9f9f9;}
h1{color:#333;}p{color:#666;}</style>
</head>
<body>
<h1>✅ 接続が完了しました</h1>
<p>Threadsアカウントの自動投稿設定が完了しました。<br>
初回の投稿は翌朝から自動的に開始されます。</p>
</body></html>"""

ERROR_HTML = """<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"><title>エラー</title></head>
<body><h1>❌ エラーが発生しました</h1><p>{message}</p></body></html>"""


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        code = query.get("code", [None])[0]

        if not code:
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(ERROR_HTML.format(message="認証コードがありません").encode())
            return

        try:
            token_data = exchange_code(code)
            short_token = token_data["access_token"]

            long_token_data = get_long_lived_token(short_token)
            access_token = long_token_data["access_token"]
            expires_in = long_token_data.get("expires_in", 5183944)

            user_info = get_user_info(access_token)
            save_to_supabase(
                user_id=user_info["id"],
                username=user_info.get("username", ""),
                access_token=access_token,
                expires_in=expires_in,
            )

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(SUCCESS_HTML.encode())

        except Exception as e:
            import logging
            logging.error("callback error: %s", e)
            self.send_response(500)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(ERROR_HTML.format(message="認証処理中にエラーが発生しました。しばらく経ってから再試行してください。").encode())
