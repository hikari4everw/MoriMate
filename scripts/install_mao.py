"""Ensure the selected official Mao model is installed on a running local engine."""

import json
import urllib.parse
import urllib.request

URL = "http://127.0.0.1:10101"
with urllib.request.urlopen(URL + "/speakers", timeout=10) as response:
    speakers = json.load(response)
if not any(s["name"] == "まお" for s in speakers):
    # The engine accepts multipart url; curl performs the official installation API.
    import subprocess

    subprocess.run(
        [
            "curl",
            "--fail",
            "--show-error",
            "--form",
            "url=https://hub.aivis-project.com/aivm-models/a59cb814-0083-4369-8542-f51a29e72af7",
            URL + "/aivm_models/install",
        ],
        check=True,
    )
with urllib.request.urlopen(URL + "/speakers", timeout=10) as response:
    speaker = next(s for s in json.load(response) if s["name"] == "まお")
print(json.dumps(speaker, ensure_ascii=False, indent=2))
