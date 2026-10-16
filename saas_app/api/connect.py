from http.server import BaseHTTPRequestHandler
from urllib.parse import urlencode, urlparse, parse_qs
import os

APP_ID = os.environ.get("THREADS_APP_ID", "985270787180212")
CALLBACK_URL = os.environ.get("CALLBACK_URL", "")
# threads_manage_insights は 2026-10-16 に承認済み（高度なアクセス）。
# 承認前は未承認の権限を混ぜるとテスターでないクライアントのOAuthがその場で失敗したため
# ?insights=1 のときだけ足していたが、承認後は通常の申込みリンクに入れてよい。
# ⚠️ 既に発行済みのトークンには後から許可は足されない＝既存クライアントは連携し直しが要る。
SCOPE = (
    "threads_basic,threads_content_publish,"
    "threads_manage_replies,threads_manage_insights"
)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # customer_id を state に乗せてコールバックまで引き回す
        qs = parse_qs(urlparse(self.path).query)
        customer_id = qs.get("customer_id", [""])[0]
        scope = SCOPE
        if qs.get("insights", [""])[0] == "1":
            # Meta審査の録画用：英語の入口（/review）から来たときは完了画面も英語にする。
            # state に印を乗せる（customer_id は無いので顧客の照合・保存には使わない）
            # 権限は SCOPE に入ったので、ここで足すものはもう無い
            if qs.get("lang", [""])[0] == "en":
                customer_id = "review-en"

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
