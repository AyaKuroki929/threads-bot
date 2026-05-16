from http.server import BaseHTTPRequestHandler
import hashlib, hmac, base64, json, os, urllib.request, urllib.parse, secrets

LINE_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_TOKEN  = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
ADMIN_UID   = os.environ.get("LINE_ADMIN_USER_ID", "")
NGROK_BASE  = os.environ.get("NGROK_BASE_URL", "https://squealer-goofiness-undying.ngrok-free.dev")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def verify_sig(body: bytes, sig: str) -> bool:
    h = hmac.new(LINE_SECRET.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(h).decode() == sig


def push(to: str, messages: list):
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push",
        data=json.dumps({"to": to, "messages": messages}).encode(),
        headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status
    except Exception as e:
        return str(e)


def reply(token: str, messages: list):
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/reply",
        data=json.dumps({"replyToken": token, "messages": messages}).encode(),
        headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status
    except Exception as e:
        return str(e)


def get_display_name(uid: str) -> str:
    req = urllib.request.Request(
        f"https://api.line.me/v2/bot/profile/{uid}",
        headers={"Authorization": f"Bearer {LINE_TOKEN}"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read()).get("displayName", "不明")
    except:
        return "不明"


def generate_setup_url(salon_name: str, threads_username: str) -> str:
    """ngrokサーバーにセッション作成をリクエストしてURLを返す"""
    params = urllib.parse.urlencode({"salon_name": salon_name, "threads_username": threads_username})
    try:
        req = urllib.request.Request(
            f"{NGROK_BASE}/create?{params}",
            headers={"ngrok-skip-browser-warning": "1"},
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read())
            token = data.get("token", "")
            if token:
                return f"{NGROK_BASE}/setup/{token}"
    except Exception as e:
        return ""
    return ""


def save_line_user(uid: str, display_name: str):
    if not SUPABASE_URL:
        return
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/line_users",
        data=json.dumps({"line_user_id": uid, "display_name": display_name}).encode(),
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req):
            pass
    except:
        pass


def notify_admin_new_follower(uid: str, display_name: str):
    if not ADMIN_UID:
        return
    push(ADMIN_UID, [{
        "type": "flex",
        "altText": f"新フォロワー：{display_name}",
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box", "layout": "vertical",
                "contents": [
                    {"type": "text", "text": "👤 新しいフォロワー", "weight": "bold", "size": "lg"},
                    {"type": "text", "text": display_name, "size": "md", "margin": "sm"},
                    {"type": "text", "text": f"ID: {uid}", "size": "xs", "color": "#888888", "wrap": True, "margin": "sm"},
                    {"type": "separator", "margin": "md"},
                    {"type": "text", "text": "⚠️ URLコマンドを送る前に：", "size": "xs", "color": "#555555", "margin": "md", "weight": "bold"},
                    {"type": "text", "text": "MacでTerminalを開き server.py + ngrok を起動してください", "size": "xs", "color": "#888888", "wrap": True},
                    {"type": "separator", "margin": "md"},
                    {"type": "text", "text": "URLを送る場合は以下を送信：", "size": "xs", "color": "#555555", "margin": "md"},
                    {"type": "text", "text": f"URL {uid} サロン名 @username", "size": "xs", "color": "#FF6B35", "wrap": True},
                ]
            }
        }
    }])


def handle_admin_command(text: str, reply_token: str, uid: str):
    """Ayaからのコマンドを処理"""
    parts = text.strip().split()

    # URL {line_user_id} {salon_name（スペース可）} @threads_username
    if parts[0].upper() == "URL" and len(parts) >= 4:
        client_uid  = parts[1]
        # @usernameは必ず末尾（サロン名にスペースが含まれる場合も対応）
        if not parts[-1].startswith("@"):
            reply(reply_token, [{"type": "text", "text": (
                "⚠️ 形式が違います。ユーザー名は@から始めてください。\n\n"
                "例：URL Uxxxxxxxx サロン名 @username"
            )}])
            return
        threads_un  = parts[-1].lstrip("@")
        salon_name  = " ".join(parts[2:-1])

        setup_url = generate_setup_url(salon_name, threads_un)
        if setup_url:
            push(client_uid, [{
                "type": "text",
                "text": (
                    f"とうこさんへようこそ！🎉\n"
                    f"以下のURLをブラウザで開いてThreadsにログインしてください👇\n\n"
                    f"{setup_url}\n\n"
                    f"【注意①】最初に英語の画面が出たら「Visit Site」ボタンを押してください。\n\n"
                    f"【注意②】ログイン前にInstagramアプリも開いておいてください。「新しいログインがありました」という通知が届いたら「はい、私です」を押してください。\n\n"
                    f"📱 スマートフォンの手順：\n"
                    f"① 画面の「Continue with Instagram」をタップ\n"
                    f"② 画面下の「Abc テキスト」が選ばれた状態で入力欄をタップしてID（メールアドレス等）を入力\n"
                    f"③「🔐 パスワード」ボタンを押してからパスワードを入力（文字が隠れます）\n"
                    f"④「セットアップ完了」が表示されたらページを閉じてOKです\n\n"
                    f"※ボタンが小さい場合は2本指でピンチして画面を拡大できます。\n"
                    f"※URLは1回限り有効です。"
                )
            }])
            reply(reply_token, [{"type": "text", "text": f"✅ {salon_name}（{threads_un}）さんにURLを送りました"}])
        else:
            reply(reply_token, [{
                "type": "text",
                "text": "⚠️ サーバーに接続できません。\nターミナルで server.py と ngrok を起動してから再度お試しください。"
            }])
        return

    # LIST → Supabaseのサロン一覧
    if parts[0].upper() == "LIST":
        if SUPABASE_URL:
            req = urllib.request.Request(
                f"{SUPABASE_URL}/rest/v1/salons?select=salon_name,threads_username,is_active&order=created_at.desc&limit=10",
                headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            )
            try:
                with urllib.request.urlopen(req) as r:
                    salons = json.loads(r.read())
                lines = [f"{'🟢' if s['is_active'] else '🔴'} {s['salon_name']} @{s.get('threads_username','?')}" for s in salons]
                reply(reply_token, [{"type": "text", "text": "📋 登録サロン一覧\n\n" + "\n".join(lines) if lines else "まだ登録がありません"}])
            except Exception as e:
                reply(reply_token, [{"type": "text", "text": f"取得に失敗しました\n{e}"}])
        return

    reply(reply_token, [{
        "type": "text",
        "text": (
            "📖 コマンド一覧\n\n"
            "URL {line_id} {サロン名} {@username}\n→ セットアップURLをクライアントに送る\n\n"
            "LIST\n→ 登録サロン一覧を表示\n\n"
            "myid\n→ 自分のLINE IDを確認"
        )
    }])


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"LINE Webhook OK")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        sig  = self.headers.get("X-Line-Signature", "")

        if LINE_SECRET and not verify_sig(body, sig):
            self.send_response(400)
            self.end_headers()
            return

        self.send_response(200)
        self.end_headers()

        try:
            events = json.loads(body).get("events", [])
        except:
            return

        for event in events:
            try:
                self._dispatch(event)
            except:
                pass

    def _dispatch(self, event):
        etype      = event.get("type")
        uid        = event.get("source", {}).get("userId", "")
        reply_tok  = event.get("replyToken", "")

        if etype == "follow":
            name = get_display_name(uid)
            save_line_user(uid, name)
            reply(reply_tok, [{"type": "text", "text": f"こんにちは！とうこさんへようこそ🎉\nまもなく担当者からご連絡いたします。しばらくお待ちください。"}])
            notify_admin_new_follower(uid, name)

        elif etype == "message" and event.get("message", {}).get("type") == "text":
            text = event["message"]["text"].strip()

            if text == "myid":
                reply(reply_tok, [{"type": "text", "text": f"あなたのLINE User ID:\n{uid}"}])
            elif uid == ADMIN_UID:
                handle_admin_command(text, reply_tok, uid)
