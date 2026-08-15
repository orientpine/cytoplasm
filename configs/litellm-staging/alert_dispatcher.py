import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
OWNER_ID = os.environ["DISCORD_OWNER_ID"]
DISCORD_API = "https://discord.com/api/v10"
BUDGET_EVENTS = {"budget_crossed", "threshold_crossed", "projected_limit_exceeded"}


def discord_post(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{DISCORD_API}{path}",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bot {BOT_TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "autophagy-litellm-alert/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(404)
            return
        self.send_response(200)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path != "/budget":
            self.send_error(404)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size))
            event = payload.get("event")
            if event not in BUDGET_EVENTS:
                self.send_response(204)
                self.end_headers()
                return
            channel = discord_post("/users/@me/channels", {"recipient_id": OWNER_ID})
            message = payload.get("event_message", "LiteLLM budget alert")
            discord_post(f"/channels/{channel['id']}/messages", {"content": f"LiteLLM budget alert: {message}"})
        except Exception:
            self.send_error(502, "budget alert dispatch failed")
            return
        self.send_response(204)
        self.end_headers()


HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
