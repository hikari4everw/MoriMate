"""Prepare the pinned GGUF candidate locally; never changes the default model."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def fetch_checked(url, target, sha256):
    if target.exists():
        if digest(target) != sha256:
            raise RuntimeError(f"校验失败，保留现有文件供检查：{target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    print(f"下载并校验：{target.name}", flush=True)
    subprocess.run(
        [
            "curl",
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--retry",
            "2",
            "--continue-at",
            "-",
            url,
            "--output",
            str(temporary),
        ],
        check=True,
    )
    if digest(temporary) != sha256:
        raise RuntimeError(f"下载校验失败，未安装；请移走 {temporary} 后重试")
    temporary.replace(target)
    print(f"SHA256 通过：{target.name}", flush=True)


def prepare_weights(assets, root=ROOT):
    model = assets["models"]["qwen38-rp"]
    folder = root / ".runtime/models" / model["directory"]

    def fetch(item):
        url = f"https://huggingface.co/{model['repo']}/resolve/{model['revision']}/{item['name']}"
        fetch_checked(url, folder / item["name"], item["sha256"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(fetch, model["files"]))


def prepare_runtime(assets, root=ROOT):
    spec = assets["llama_cpp"]
    runtime = root / ".runtime"
    archive = runtime / "downloads/llama.cpp-v0.7.0.tar.gz"
    fetch_checked(spec["url"], archive, spec["sha256"])
    source = runtime / spec["directory"]
    if not source.exists():
        with tarfile.open(archive) as bundle:
            bundle.extractall(runtime, filter="data")
        (runtime / ("llama.cpp-" + spec["commit"])).rename(source)
    binary = source / "build/bin/llama-server"
    # Pin build metadata too: an archive nested in this project would otherwise
    # discover the parent project's Git commit instead of llama.cpp's commit.
    subprocess.run(
        [
            "cmake",
            "-S",
            str(source),
            "-B",
            str(source / "build"),
            "-DCMAKE_BUILD_TYPE=Release",
            "-DLLAMA_BUILD_NUMBER=0",
            "-DLLAMA_BUILD_COMMIT=" + spec["commit"],
            "-DGGML_METAL=ON",
            "-DGGML_METAL_EMBED_LIBRARY=ON",
            "-DLLAMA_CURL=OFF",
            "-DLLAMA_BUILD_TESTS=OFF",
            "-DLLAMA_BUILD_EXAMPLES=OFF",
            "-DLLAMA_BUILD_SERVER=ON",
        ],
        check=True,
    )
    subprocess.run(
        [
            "cmake",
            "--build",
            str(source / "build"),
            "--parallel",
            "6",
            "--target",
            "llama-server",
        ],
        check=True,
    )
    subprocess.run([str(binary), "--version"], check=True)
    (source / "PREPARED.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2) + "\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--runtime-only", action="store_true")
    group.add_argument("--weights-only", action="store_true")
    args = parser.parse_args()
    assets = json.loads((ROOT / "config/assets.json").read_text())
    if not args.weights_only:
        prepare_runtime(assets)
    if not args.runtime_only:
        prepare_weights(assets)
    print("候选资源准备完成；默认模型保持原配置。", flush=True)


if __name__ == "__main__":
    main()
