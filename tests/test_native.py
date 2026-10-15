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
