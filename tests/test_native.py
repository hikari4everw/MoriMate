"""Opt-in real Apple Vision smoke test; uses a generated image, never the desktop."""

import json
import os
import subprocess
import pytest


@pytest.mark.skipif(
    os.environ.get("NATIVE_TEST") != "1", reason="set NATIVE_TEST=1 on macOS"
)
def test_vision_reads_chinese_fixture(tmp_path):
    fixture = tmp_path / "dialogue.png"
    script = tmp_path / "fixture.swift"
    script.write_text("""import AppKit
let image = NSImage(size: NSSize(width: 1100, height: 180))
image.lockFocus()
NSColor.white.setFill()
NSRect(x:0,y:0,width:1100,height:180).fill()
let text = "小明：今天一起去看海吧。" as NSString
text.draw(at:NSPoint(x:40,y:65),withAttributes:[.font:NSFont.systemFont(ofSize:44),.foregroundColor:NSColor.black])
image.unlockFocus()
let rep=NSBitmapImageRep(data:image.tiffRepresentation!)!
try rep.representation(using:.png,properties:[:])!.write(to:URL(fileURLWithPath:CommandLine.arguments[1]))
""")
    subprocess.run(
        ["swift", "-module-cache-path", ".cache/swift", str(script), str(fixture)],
        check=True,
    )
    result = subprocess.run(
        [".runtime/window-observer", "ocr-file", str(fixture)],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = json.loads(result.stdout)["lines"]
    assert "一起去看海" in "".join(line["text"] for line in lines)
    assert all(line["confidence"] >= 0.55 for line in lines)


@pytest.mark.skipif(
    os.environ.get("NATIVE_CAPTURE_TEST") != "1",
    reason="set NATIVE_CAPTURE_TEST=1 with Screen Recording permission",
)
def test_capture_fixture_window_crop_ocr_and_closed_window(tmp_path):
    executable = tmp_path / "observation-fixture"
    subprocess.run(
        [
            "swiftc",
            "-module-cache-path",
            ".cache/swift",
            "tests/fixtures/ObservationWindow.swift",
            "-o",
            str(executable),
        ],
        check=True,
    )
    fixture = subprocess.Popen([str(executable)], stdout=subprocess.PIPE, text=True)
    try:
        window = fixture.stdout.readline().strip()
        assert window.isdigit(), "fixture must publish its own window ID"

        def capture(*args):
            return subprocess.run(
                [".runtime/window-observer", "capture", window, *args],
                capture_output=True,
                text=True,
                timeout=20,
            )

        full = capture()
        assert full.returncode == 0, full.stderr
        image = json.loads(full.stdout)
        assert image["image"].startswith("data:image/jpeg;base64,")
        assert "一起去看海" in "".join(row["text"] for row in image["lines"])
        cropped = capture("0", "0", "0.5", "1")
        assert cropped.returncode == 0, cropped.stderr
        assert abs(json.loads(cropped.stdout)["width"] * 2 - image["width"]) <= 2
        fixture.terminate()
        fixture.wait(timeout=5)
        closed = capture()
        assert closed.returncode != 0
        assert "重新选择" in closed.stderr
    finally:
        if fixture.poll() is None:
            fixture.terminate()
            fixture.wait(timeout=5)
