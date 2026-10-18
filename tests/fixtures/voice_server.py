import io
import json
import subprocess
import sys
import time
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

port, behavior = int(sys.argv[1]), sys.argv[2]
if behavior == "exit":
    sys.exit(2)
if behavior == "hang":
    time.sleep(60)
child = None
if behavior == "orphan":
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
        ]
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        if self.path == "/speakers":
            self.wfile.write(
                json.dumps(
                    [{"name": "まお", "styles": [{"name": "ノーマル", "id": 1}]}]
                ).encode()
            )
        else:
            self.wfile.write(
                json.dumps({"child_pid": child.pid if child else None}).encode()
            )

    def do_POST(self):
        body = json.loads(
            self.rfile.read(int(self.headers.get("Content-Length", 0))) or "{}"
        )
        self.send_response(500 if body.get("text") == "FAIL" else 200)
        self.end_headers()
        if self.path.startswith("/audio_query"):
            self.wfile.write(b"{}")
        elif behavior == "bad-audio":
            self.wfile.write(b"RIFFbroken")
        else:
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(32000)
                audio.writeframes(b"\x10\x00" * 320)
            self.wfile.write(buffer.getvalue())


HTTPServer(("127.0.0.1", port), Handler).serve_forever()
