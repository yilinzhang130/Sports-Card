import hashlib
import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

VERIFICATION_TOKEN = os.environ.get("EBAY_VERIFICATION_TOKEN", "")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        challenge_code = params.get("challenge_code", [None])[0]

        if not challenge_code:
            self._respond(400, {"error": "missing challenge_code"})
            return

        # eBay spec: sha256(challengeCode + verificationToken + endpointUrl)
        host = self.headers.get("host", "")
        endpoint_url = f"https://{host}/api/ebay_webhook"
        digest = hashlib.sha256(
            (challenge_code + VERIFICATION_TOKEN + endpoint_url).encode()
        ).hexdigest()
        self._respond(200, {"challengeResponse": digest})

    def do_POST(self):
        # Read and discard body; just acknowledge the deletion notification.
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.send_response(200)
        self.end_headers()

    def _respond(self, status: int, body: dict):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass
