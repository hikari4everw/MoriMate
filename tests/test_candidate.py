import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, Path("scripts") / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_asset_checksum_is_verified_before_installing(tmp_path):
    module = load_script("prepare_candidate")
    source = tmp_path / "source"
    source.write_bytes(b"controlled fixture")
    target = tmp_path / "model.gguf"
    with pytest.raises(RuntimeError, match="校验"):
        module.fetch_checked(source.as_uri(), target, "0" * 64)
    assert not target.exists()
    # A failed download is not mistaken for an installed model.
    target.with_suffix(".gguf.part").unlink(missing_ok=True)
    module.fetch_checked(
        source.as_uri(), target, hashlib.sha256(source.read_bytes()).hexdigest()
    )
    assert target.read_bytes() == b"controlled fixture"


def test_existing_asset_with_wrong_hash_is_rejected(tmp_path):
    module = load_script("prepare_candidate")
    target = tmp_path / "model.gguf"
    target.write_bytes(b"wrong")
    with pytest.raises(RuntimeError, match="校验"):
        module.fetch_checked("https://invalid.example/model", target, "0" * 64)
    assert target.read_bytes() == b"wrong"


def test_candidate_command_has_projector_single_slot_and_local_port(tmp_path):
    module = load_script("run_local")
    (tmp_path / "config").mkdir()
    (tmp_path / "config/assets.json").write_text(Path("config/assets.json").read_text())
    assets = json.loads(Path("config/assets.json").read_text())
    entry = assets["models"]["qwen38-rp"]
    folder = tmp_path / ".runtime/models" / entry["directory"]
    folder.mkdir(parents=True)
    for item in entry["files"]:
        (folder / item["name"]).touch()
    binary = (
        tmp_path
        / ".runtime"
        / assets["llama_cpp"]["directory"]
        / "build/bin/llama-server"
    )
    binary.parent.mkdir(parents=True)
    binary.touch()
    command, identity, backend, vision = module.model_launch(tmp_path, "qwen38-rp")
    assert identity == "qwen38-rp" and backend == "llama.cpp" and vision
    assert command[command.index("--port") + 1] == "18080"
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--parallel") + 1] == "1"
    assert command[command.index("--ctx-size") + 1] == "32768"
    assert command[command.index("--mmproj") + 1].endswith("mmproj-f16.gguf")

    assert command[command.index("--reasoning-budget") + 1] == "-1"
