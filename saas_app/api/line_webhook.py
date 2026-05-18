from http.server import BaseHTTPRequestHandler
import hashlib, hmac, base64, json, os, urllib.request

LINE_SECRET    = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_TOKEN     = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
ADMIN_UID      = os.environ.get("LINE_ADMIN_USER_ID", "")
ADMIN_LINE_TOKEN = os.environ.get("ADMIN_NOTIFY_LINE_TOKEN", "")  # Claude通知bot
SUPABASE_URL   = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY   = os.environ.get("SUPABASE_SERVICE_KEY", "")


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


def broadcast_admin(text: str):
    if not ADMIN_LINE_TOKEN:
        return
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/broadcast",
        data=json.dumps({"messages": [{"type": "text", "text": text}]}).encode(),
        headers={"Authorization": f"Bearer {ADMIN_LINE_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception:
        pass


def notify_admin_new_follower(uid: str, display_name: str):
    broadcast_admin(
        f"👤 とうこさん LINE友達追加\n\n"
        f"名前：{display_name}\n"
        f"LINE ID：{uid}\n\n"
        f"Stripeで決済が完了すると自動でフォームURL・connect URLが届きます。"
    )


def handle_admin_command(text: str, reply_token: str, uid: str):
    """Ayaからのコマンドを処理"""
    parts = text.strip().split()

    # LIST → Supabaseのサロン一覧
    if parts[0].upper() == "LIST":
        if SUPABASE_URL:
            req = urllib.request.Request(
                f"{SUPABASE_URL}/rest/v1/salons?select=salon_name,threads_user_id,is_active&order=created_at.desc&limit=10",
                headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            )
            try:
                with urllib.request.urlopen(req) as r:
                    salons = json.loads(r.read())
                lines = [f"{'🟢' if s['is_active'] else '🔴'} {s['salon_name']} ({s.get('threads_user_id','未設定')})" for s in salons]
                reply(reply_token, [{"type": "text", "text": "📋 登録サロン一覧\n\n" + "\n".join(lines) if lines else "まだ登録がありません"}])
            except Exception as e:
                reply(reply_token, [{"type": "text", "text": f"取得に失敗しました\n{e}"}])
        return

    reply(reply_token, [{
        "type": "text",
        "text": (
            "📖 コマンド一覧\n\n"
            "LIST\n→ 登録サロン一覧を表示\n\n"
            "myid\n→ 自分のLINE IDを確認\n\n"
            "新規登録は register_saas_user.py を使用してください"
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
            stripe_url = f"https://buy.stripe.com/14AeV64rV9zFcr8f9P5wI00?client_reference_id={uid}"
            reply(reply_tok, [{
                "type": "text",
                "text": (
                    f"こんにちは！とうこさんへようこそ🎉\n\n"
                    f"▼ お申込みはこちら\n{stripe_url}\n\n"
                    f"お申込み後、担当者よりサロン情報フォームをお送りします。"
                )
            }])
            notify_admin_new_follower(uid, name)

        elif etype == "message" and event.get("message", {}).get("type") == "text":
            text = event["message"]["text"].strip()

            if text == "myid":
                reply(reply_tok, [{"type": "text", "text": f"あなたのLINE User ID:\n{uid}"}])
            elif uid == ADMIN_UID:
                handle_admin_command(text, reply_tok, uid)
