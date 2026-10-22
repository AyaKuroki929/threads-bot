import json, subprocess, urllib.request, urllib.error, sys
D="/private/tmp/claude-501/-Users-ayakuroki/c3392a07-1679-43a6-9c17-12e421cb6409/scratchpad"
tok = subprocess.run(["security","find-generic-password","-s","line-salonaijuku","-a","token","-w"],
                     capture_output=True, text=True).stdout.strip()
H = {"Authorization": f"Bearer {tok}", "Content-Type":"application/json"}
def api(method, url, body=None, raw=None, ctype=None):
    h = dict(H)
    if ctype: h["Content-Type"] = ctype
    data = raw if raw is not None else (json.dumps(body).encode() if body else None)
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=40) as resp:
            t = resp.read()
            return resp.status, (json.loads(t) if t.strip().startswith(b"{") else t[:200])
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:400].decode()

menu = {
  "size": {"width": 2500, "height": 843},
  "selected": True,
  "name": "サロンAI塾メニュー",
  "chatBarText": "メニュー",
  "areas": [
    {"bounds": {"x": 0, "y": 0, "width": 1250, "height": 843},
     "action": {"type": "uri", "label": "申し込み",
                "uri": "https://rocky-ash-f11.notion.site/AI-3dee55d89092814b9e50c4f099d6d01d"}},
    {"bounds": {"x": 1250, "y": 0, "width": 1250, "height": 843},
     "action": {"type": "uri", "label": "サロンAI塾とは",
                "uri": "https://saas.shikisai.work/salon-ai-school"}}
  ]
}
s, d = api("POST", "https://api.line.me/v2/bot/richmenu", menu)
print("1) メニュー作成", s, d if s!=200 else d.get("richMenuId"))
if s != 200: sys.exit(1)
rid = d["richMenuId"]

img = open(f"{D}/richmenu.jpg","rb").read()
s, d = api("POST", f"https://api-data.line.me/v2/bot/richmenu/{rid}/content", raw=img, ctype="image/jpeg")
print("2) 画像アップロード", s, d if s!=200 else "")
if s != 200: sys.exit(1)

s, d = api("POST", f"https://api.line.me/v2/bot/user/all/richmenu/{rid}")
print("3) デフォルトに設定", s, d if s!=200 else "")
if s != 200: sys.exit(1)

s, d = api("GET", "https://api.line.me/v2/bot/user/all/richmenu")
print("4) 現在のデフォルト", s, d)
s, d = api("GET", "https://api.line.me/v2/bot/richmenu/list")
print("5) 登録一覧", s, [ (m["richMenuId"], m["name"], m["chatBarText"], [a["action"]["uri"] for a in m["areas"]]) for m in d.get("richmenus",[]) ])
