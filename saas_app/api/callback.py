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
LINE_TOKEN = os.environ.get("ADMIN_NOTIFY_LINE_TOKEN", "")  # Claude通知bot


def line_notify_oauth_complete(username: str):
    if not LINE_TOKEN:
        return
    text = (
        f"✅ とうこさん OAuth完了！\n\n"
        f"Threads ID：@{username}\n\n"
        f"▼ 次のステップ\n"
        f"GitHub Actions「SaaS - サロン別投稿生成」を手動実行\n"
        f"https://github.com/AyaKuroki929/threads-bot/actions/workflows/saas_generate.yml"
    )
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/broadcast",
        data=json.dumps({"messages": [{"type": "text", "text": text}]}).encode(),
        headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


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
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise Exception(f"exchange_code HTTP {e.code}: {body} | redirect_uri={CALLBACK_URL!r} | app_id={APP_ID!r}")


def get_long_lived_token(short_token):
    params = urllib.parse.urlencode({
        "grant_type": "th_exchange_token",
        "client_secret": APP_SECRET,
        "access_token": short_token,
    })
    url = f"https://graph.threads.net/access_token?{params}"
    try:
        with urllib.request.urlopen(url) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise Exception(f"get_long_lived_token HTTP {e.code}: {body}")


def get_user_info(token):
    url = f"https://graph.threads.net/me?fields=id,username&access_token={urllib.parse.quote(token)}"
    try:
        with urllib.request.urlopen(url) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise Exception(f"get_user_info HTTP {e.code}: {body}")


def save_to_supabase(user_id, username, access_token, stripe_customer_id=""):
    # upsert by threads_user_id
    payload = {
        "threads_user_id": user_id,
        "salon_name": username,
        "access_token": access_token,
        "is_active": True,
    }
    if stripe_customer_id:
        payload["stripe_customer_id"] = stripe_customer_id
    data = json.dumps(payload).encode()

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
初回の投稿は設定完了後に自動的に開始されます。</p>
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
            # state に customer_id が入っている
            customer_id = query.get("state", [""])[0]

            token_data = exchange_code(code)
            short_token = token_data["access_token"]

            long_token_data = get_long_lived_token(short_token)
            access_token = long_token_data["access_token"]

            user_info = get_user_info(access_token)
            username = user_info.get("username", "")
            save_to_supabase(
                user_id=user_info["id"],
                username=username,
                access_token=access_token,
                stripe_customer_id=customer_id,
            )
            line_notify_oauth_complete(username)

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(SUCCESS_HTML.encode())

        except Exception as e:
            import logging, traceback
            logging.error("callback error: %s", e)
            self.send_response(500)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            detail = f"{type(e).__name__}: {e}"
            self.wfile.write(ERROR_HTML.format(message=f"認証処理中にエラーが発生しました。<br><br><code>{detail}</code>").encode())
