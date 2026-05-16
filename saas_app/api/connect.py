from http.server import BaseHTTPRequestHandler
from urllib.parse import urlencode
import os

APP_ID = os.environ.get("THREADS_APP_ID", "985270787180212")
CALLBACK_URL = os.environ.get("CALLBACK_URL", "")
SCOPE = "threads_basic,threads_content_publish"


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = {
            "client_id": APP_ID,
            "redirect_uri": CALLBACK_URL,
            "scope": SCOPE,
            "response_type": "code",
        }
        url = "https://threads.net/oauth/authorize?" + urlencode(params)
        self.send_response(302)
        self.send_header("Location", url)
        self.end_headers()
