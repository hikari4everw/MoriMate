"""Small isolated model endpoint for process ownership integration tests."""

import json
import os
import signal
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

port, identity, journal, behavior = sys.argv[1:]
live_prior = []
if Path(journal).exists():
    for line in Path(journal).read_text().splitlines():
        pid = json.loads(line)["pid"]
        try:
            os.kill(pid, 0)
            live_prior.append(pid)
        except ProcessLookupError:
            pass
with Path(journal).open("a") as stream:
    stream.write(
        json.dumps({"pid": os.getpid(), "identity": identity, "live_prior": live_prior})
        + "\n"
    )
if behavior == "ignore-term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if behavior == "exit":
    sys.exit(3)
if behavior == "hang":
    time.sleep(60)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"data": [{"id": identity}]}).encode())

    def log_message(self, *_):
        pass


HTTPServer(("127.0.0.1", int(port)), Handler).serve_forever()
