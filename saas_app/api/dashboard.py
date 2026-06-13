"""
Threads Auto Post — English demo dashboard (for Meta App Review screencast).

Purpose: demonstrate the end-to-end use of the requested permissions *inside the app*:
  - threads_basic          : show the connected Threads account (GET /me)
  - threads_content_publish : publish a post and view the result in the app
  - threads_manage_replies  : create a reply to that post and view it in the app

Routes (see vercel.json):
  GET  /dashboard?account=<salon_name|customer_id>            -> English HTML page
  GET  /dashboard?action=account&account=<...>                -> JSON {username, posts:[...]}
  POST /dashboard?action=publish&account=<...>                -> JSON {post, reply}

Safety: live publishing is restricted to the demo account only (DEMO_SALON,
default "aya_0929_private") so a real client account is never posted to from here.
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import os
import time
import urllib.request
import urllib.parse
import urllib.error

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
GRAPH = "https://graph.threads.net"
# 実クライアントに誤投稿しないよう、ライブ投稿はデモ用アカウントだけ許可する
DEMO_SALON = os.environ.get("DEMO_SALON", "aya_0929_private")


def _supabase_get(params):
    url = f"{SUPABASE_URL}/rest/v1/salons?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _get_salon(identifier):
    """salon_name か stripe_customer_id でサロンを引く。token/user_id/name を返す。"""
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    select = "salon_name,threads_user_id,access_token,is_active"
    for col in ("salon_name", "stripe_customer_id"):
        try:
            rows = _supabase_get({col: f"eq.{identifier}", "select": select, "limit": 1})
        except Exception:
            rows = []
        if rows:
            return rows[0]
    return None


def _graph_get(path, token, extra=None):
    params = {"access_token": token}
    if extra:
        params.update(extra)
    url = f"{GRAPH}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=20) as resp:
        return json.loads(resp.read())


def _graph_post(path, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(f"{GRAPH}/{path}", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _publish_thread(user_id, token, text, reply_to_id=None):
    """コンテナ作成 → 待機 → 公開。{id, permalink} を返す。"""
    payload = {"media_type": "TEXT", "text": text, "access_token": token}
    if reply_to_id:
        payload["reply_to_id"] = reply_to_id
    creation_id = _graph_post(f"{user_id}/threads", payload)["id"]
    time.sleep(5)
    post_id = _graph_post(f"{user_id}/threads_publish",
                          {"creation_id": creation_id, "access_token": token})["id"]
    permalink = ""
    try:
        permalink = _graph_get(str(post_id), token, {"fields": "permalink"}).get("permalink", "")
    except Exception:
        pass
    return {"id": post_id, "permalink": permalink, "text": text}


def _account_payload(salon):
    """連携アカウント情報＋最近の投稿（返信含む）を返す。"""
    token = salon["access_token"]
    me = _graph_get("me", token, {"fields": "id,username"})
    posts = []
    try:
        data = _graph_get("me/threads", token,
                          {"fields": "id,text,permalink,timestamp", "limit": "8"})
        posts = data.get("data", [])
    except Exception:
        pass
    return {
        "username": me.get("username", ""),
        "user_id": me.get("id", ""),
        "salon_name": salon.get("salon_name", ""),
        "posts": posts,
    }


PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Threads Auto Post — Dashboard</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif; max-width: 720px;
         margin: 0 auto; padding: 24px; color: #1c1e21; background: #f5f6f7; }}
  h1 {{ font-size: 22px; }}
  .card {{ background: #fff; border: 1px solid #e4e6eb; border-radius: 12px;
          padding: 18px; margin: 16px 0; }}
  .label {{ color: #65676b; font-size: 13px; }}
  .account {{ font-size: 20px; font-weight: 600; }}
  .perm {{ display: inline-block; background: #e7f3ff; color: #0064d1; border-radius: 6px;
           padding: 2px 8px; font-size: 12px; margin: 2px 4px 2px 0; }}
  button {{ background: #0064d1; color: #fff; border: none; border-radius: 8px;
           padding: 12px 18px; font-size: 15px; cursor: pointer; }}
  button:disabled {{ background: #9db7d6; cursor: default; }}
  .post {{ border-top: 1px solid #eee; padding: 10px 0; font-size: 14px; }}
  .reply {{ margin-left: 18px; border-left: 3px solid #0064d1; padding-left: 10px; }}
  a {{ color: #0064d1; }}
  .hint {{ color: #65676b; font-size: 12px; margin-top: 4px; }}
  .status {{ font-size: 13px; color: #65676b; margin-top: 8px; min-height: 18px; }}
</style>
</head>
<body>
  <h1>Threads Auto Post — Dashboard</h1>

  <div class="card">
    <div class="label">Connected Threads account</div>
    <div class="account" id="account">Loading…</div>
    <div class="hint">This account is linked to our app through the Threads OAuth login.
      We read the account profile using the <b>threads_basic</b> permission.</div>
    <div style="margin-top:8px;">
      <span class="perm">threads_basic</span>
      <span class="perm">threads_content_publish</span>
      <span class="perm">threads_manage_replies</span>
    </div>
  </div>

  <div class="card">
    <div class="label">Publish a post and a reply</div>
    <div class="hint">Clicking the button publishes a new post to the connected Threads profile
      (<b>threads_content_publish</b>), then creates a reply to that same post
      (<b>threads_manage_replies</b>). The results appear below and on the native Threads app.</div>
    <p><button id="pub" onclick="publish()">Publish demo post + reply</button></p>
    <div class="status" id="status"></div>
  </div>

  <div class="card">
    <div class="label">Recent posts on this account</div>
    <div id="posts">Loading…</div>
  </div>

<script>
const ACCOUNT = {account_json};

async function loadAccount() {{
  try {{
    const r = await fetch('/dashboard?action=account&account=' + encodeURIComponent(ACCOUNT));
    const d = await r.json();
    if (d.error) {{ document.getElementById('account').textContent = 'Error: ' + d.error; return; }}
    document.getElementById('account').textContent = '@' + d.username;
    renderPosts(d.posts || []);
  }} catch (e) {{
    document.getElementById('account').textContent = 'Error loading account';
  }}
}}

function renderPosts(posts) {{
  const box = document.getElementById('posts');
  if (!posts.length) {{ box.textContent = 'No posts yet.'; return; }}
  box.innerHTML = posts.map(p => {{
    const cls = p.is_reply ? 'post reply' : 'post';
    const tag = p.is_reply ? '↳ Reply' : 'Post';
    const text = (p.text || '(no text)').replace(/</g,'&lt;');
    const link = p.permalink ? ' — <a href="'+p.permalink+'" target="_blank">View on Threads</a>' : '';
    return '<div class="'+cls+'"><b>'+tag+'</b> · '+(p.timestamp||'')+'<br>'+text+link+'</div>';
  }}).join('');
}}

async function publish() {{
  const btn = document.getElementById('pub');
  const st = document.getElementById('status');
  btn.disabled = true; st.textContent = 'Publishing post and reply… (this takes ~15 seconds)';
  try {{
    const r = await fetch('/dashboard?action=publish&account=' + encodeURIComponent(ACCOUNT), {{ method: 'POST' }});
    const d = await r.json();
    if (d.error) {{ st.textContent = 'Error: ' + d.error; btn.disabled = false; return; }}
    const esc = s => (s||'').replace(/</g,'&lt;');
    st.innerHTML =
      '<div style="color:#1c1e21;margin-bottom:6px;">✅ Published successfully.</div>'
      + '<div class="post"><b>Post</b> · created with threads_content_publish<br>'
      + esc(d.post.text) + '<br><a href="'+d.post.permalink+'" target="_blank">View on Threads</a></div>'
      + '<div class="post reply"><b>↳ Reply</b> · created with threads_manage_replies<br>'
      + esc(d.reply.text) + '<br><a href="'+d.reply.permalink+'" target="_blank">View on Threads</a></div>';
    btn.disabled = false;
    loadAccount();
  }} catch (e) {{
    st.textContent = 'Error while publishing.'; btn.disabled = false;
  }}
}}

loadAccount();
</script>
</body>
</html>"""


def _send_json(self, status, obj):
    body = json.dumps(obj).encode()
    self.send_response(status)
    self.send_header("Content-Type", "application/json")
    self.end_headers()
    self.wfile.write(body)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        action = qs.get("action", [""])[0]
        account = qs.get("account", [""])[0] or qs.get("customer_id", [""])[0]

        if action == "account":
            salon = _get_salon(account)
            if not salon:
                return _send_json(self, 404, {"error": "account not found"})
            try:
                return _send_json(self, 200, _account_payload(salon))
            except urllib.error.HTTPError as e:
                return _send_json(self, 502, {"error": f"Threads API HTTP {e.code}: {e.read().decode()[:200]}"})
            except Exception as e:
                return _send_json(self, 500, {"error": str(e)})

        # それ以外は英語ダッシュボードHTMLを返す
        html = PAGE_HTML.format(account_json=json.dumps(account))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def do_POST(self):
        qs = parse_qs(urlparse(self.path).query)
        action = qs.get("action", [""])[0]
        account = qs.get("account", [""])[0] or qs.get("customer_id", [""])[0]

        if action != "publish":
            return _send_json(self, 400, {"error": "unknown action"})

        salon = _get_salon(account)
        if not salon:
            return _send_json(self, 404, {"error": "account not found"})

        # 安全策：ライブ投稿はデモ用アカウントのみ（実クライアントへの誤投稿を防ぐ）
        if salon.get("salon_name") != DEMO_SALON:
            return _send_json(self, 403, {
                "error": f"Live publishing is restricted to the demo account ({DEMO_SALON})."})

        token = salon["access_token"]
        try:
            user_id = salon.get("threads_user_id") or _graph_get("me", token, {"fields": "id"})["id"]
            stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
            post = _publish_thread(
                user_id, token,
                f"Demo post published via the Threads Auto Post app. ({stamp})")
            reply = _publish_thread(
                user_id, token,
                "This reply was created via the API to demonstrate threads_manage_replies.",
                reply_to_id=post["id"])
            return _send_json(self, 200, {"post": post, "reply": reply})
        except urllib.error.HTTPError as e:
            return _send_json(self, 502, {"error": f"Threads API HTTP {e.code}: {e.read().decode()[:200]}"})
        except Exception as e:
            return _send_json(self, 500, {"error": str(e)})
