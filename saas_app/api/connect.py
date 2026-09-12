from http.server import BaseHTTPRequestHandler
from urllib.parse import urlencode, urlparse, parse_qs
import os

APP_ID = os.environ.get("THREADS_APP_ID", "985270787180212")
CALLBACK_URL = os.environ.get("CALLBACK_URL", "")
SCOPE = "threads_basic,threads_content_publish,threads_manage_replies"
# 未承認の権限を通常の申込みリンクに混ぜると、テスターでないクライアントのOAuthが
# その場で失敗して連携できなくなる。だから審査用の追加権限は ?insights=1 のときだけ足す
# （アプリの管理者・テスター＝彩さん自身のアカウントで、審査動画を撮るための経路）。
REVIEW_SCOPE = "threads_manage_insights"


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # customer_id を state に乗せてコールバックまで引き回す
        qs = parse_qs(urlparse(self.path).query)
        customer_id = qs.get("customer_id", [""])[0]
        scope = SCOPE
        if qs.get("insights", [""])[0] == "1":
            scope = f"{SCOPE},{REVIEW_SCOPE}"

        params = {
            "client_id": APP_ID,
            "redirect_uri": CALLBACK_URL,
            "scope": scope,
            "response_type": "code",
            "state": customer_id,
        }
        url = "https://threads.net/oauth/authorize?" + urlencode(params)
        self.send_response(302)
        self.send_header("Location", url)
        self.end_headers()
