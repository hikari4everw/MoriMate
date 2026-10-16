import argparse
import json
import shutil
import subprocess
import tarfile
from pathlib import Path
from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[1]


def fetch(url, path):
    if not path.exists():
        temporary = path.with_suffix(path.suffix + ".part")
        subprocess.run(
            ["curl", "-fL", "--retry", "2", url, "-o", str(temporary)], check=True
        )
        temporary.rename(path)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", choices=["9b", "27b", "qwen3"], default="27b")
    p.add_argument("--voice", choices=["sayo", "mao"], default="mao")
    args = p.parse_args()
    assets = json.loads((ROOT / "config/assets.json").read_text())
    downloads = ROOT / ".runtime/downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    model = assets["models"][args.model]
    snapshot_download(
        model["repo"],
        revision=model["revision"],
        local_dir=ROOT / ".runtime/models" / model["directory"],
        max_workers=3,
    )
    voice = assets[args.voice]
    folder = ROOT / ".runtime" / voice["directory"]
    folder.mkdir(exist_ok=True)
    archive = downloads / f"{args.voice}.7z"
    if not list(folder.rglob("run")):
        fetch(voice["url"], archive)
        subprocess.run(["tar", "-xf", str(archive), "-C", str(folder)], check=True)
    hiyori = ROOT / "backend/live2d-models/Hiyori"
    if not (hiyori / "Hiyori.moc3").exists():
        source = downloads / "cubism-samples.tar.gz"
        fetch(assets["hiyori"]["url"], source)
        with tarfile.open(source) as bundle:
            for member in bundle.getmembers():
                if (
                    member.name.startswith(assets["hiyori"]["prefix"])
                    and member.isfile()
                ):
                    relative = Path(member.name[len(assets["hiyori"]["prefix"]) :])
                    if ".." in relative.parts or relative.is_absolute():
                        raise ValueError("Invalid asset path")
                    target = hiyori / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.extractfile(member) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
    model_file = hiyori / "Hiyori.model3.json"
    data = json.loads(model_file.read_text())
    data["FileReferences"]["Expressions"] = []
    (hiyori / "expressions").mkdir(exist_ok=True)
    for expression in sorted((ROOT / "config/expressions").glob("*.json")):
        shutil.copy2(expression, hiyori / "expressions" / expression.name)
        data["FileReferences"]["Expressions"].append(
            {
                "Name": expression.name.split(".")[0],
                "File": "expressions/" + expression.name,
            }
        )
    model_file.write_text(json.dumps(data, indent=2))
    print("本地资源已准备。まお音色首次还需运行 scripts/install_mao.py。")


if __name__ == "__main__":
    main()
