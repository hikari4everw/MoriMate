"""Switch Codex Desktop between the existing MoriMate model and cloud inference."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MODEL = str(ROOT / '.runtime/models/Qwen3.5-27B-4bit')
BASE_URL = 'http://127.0.0.1:18080/v1'
KEYS = (
    'model', 'model_provider', 'model_reasoning_effort',
    'model_reasoning_summary', 'model_catalog_json', 'model_context_window',
    'model_auto_compact_token_limit', 'service_tier',
)
LOCAL = {
    'model': MODEL,
    'model_provider': 'morimate_local',
    'model_reasoning_summary': 'none',
    'model_context_window': 32768,
    'model_auto_compact_token_limit': 24576,
}
PROVIDER = '''[model_providers.morimate_local]
name = "MoriMate Local Qwen3.5-27B"
base_url = "http://127.0.0.1:18080/v1"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false
'''


def atomic_write(path, text):
    """Never leave a partial config if the process is interrupted."""
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def switch_config(config, mode):
    original = config.read_text()
    parsed = tomllib.loads(original)
    if mode == 'cloud' and parsed.get('model_provider') != 'morimate_local':
        return
    backup = config.with_name('morimate-codex-backup.toml')
    snapshot = config.with_name('morimate-cloud.json')
    if not backup.exists():
        atomic_write(backup, original)
    if mode == 'local':
        if parsed.get('model_provider') != 'morimate_local':
            cloud = {key: parsed[key] for key in KEYS if key in parsed}
        elif snapshot.exists():
            cloud = json.loads(snapshot.read_text())
        else:
            raise RuntimeError('缺少云端配置快照，请先恢复备份，避免覆盖云端设置。')
        desired = dict(LOCAL, model_catalog_json=str(config.with_name('morimate-model-catalog.json')))
    else:
        if not snapshot.exists():
            raise RuntimeError('尚无云端快照，请先切换本地一次。配置未修改。')
        cloud = json.loads(snapshot.read_text())
        desired = cloud
    # Only top-level scalar model settings belong to this switcher.
    # Preserve all comments, tables, plugins, credentials, and desktop settings.
    lines = original.splitlines(keepends=True)
    first_table = next((i for i, line in enumerate(lines) if line.lstrip().startswith('[')), len(lines))
    top = []
    for line in lines[:first_table]:
        match = re.match(r'^([A-Za-z_][A-Za-z_0-9]*)\s*=', line)
        if not match or match[1] not in KEYS:
            top.append(line)
    new_top = ''.join(f'{key} = {json.dumps(value, ensure_ascii=False)}\n' for key, value in desired.items())
    rest = ''.join(lines[first_table:])
    if mode == 'local':
        section = r'(?ms)^\[model_providers\.morimate_local\][^\n]*\n.*?(?=^\[|\Z)'
        rest = re.sub(section, '', rest)
        rest = rest.rstrip() + '\n\n' + PROVIDER
    updated = new_top + ''.join(top) + rest
    result = tomllib.loads(updated)
    if any(result.get(key) != desired.get(key) for key in KEYS):
        raise RuntimeError('模型设置格式不受支持，配置未修改。')
    if mode == 'local':
        catalog = {'models': [{
            'slug': MODEL,
            'display_name': 'MoriMate Qwen3.5-27B（本地）',
            'description': '本机 MLX / Qwen3.5-27B 4bit',
            'base_instructions': (
                "You are Codex, a coding assistant on the user's Mac. "
                'Follow the user and project instructions. Use the provided tools '
                'to inspect files and perform authorized work. Verify results before '
                "claiming success. Reply in the user's language."
            ),
            'default_reasoning_level': 'none',
            'supported_reasoning_levels': [],
            'shell_type': 'unified_exec',
            'visibility': 'list', 'supported_in_api': True, 'priority': 0,
            'supports_reasoning_summaries': False, 'support_verbosity': False,
            'context_window': 32768,
            'truncation_policy': {'mode': 'tokens', 'limit': 6000},
            'input_modalities': ['text', 'image'],
            'experimental_supported_tools': [],
        }]}
        atomic_write(Path(desired['model_catalog_json']), json.dumps(catalog, ensure_ascii=False, indent=2) + '\n')
        atomic_write(snapshot, json.dumps(cloud, ensure_ascii=False, indent=2) + '\n')
    atomic_write(config, updated)


def server_models():
    with urllib.request.urlopen(BASE_URL + '/models', timeout=3) as response:
        return json.load(response)['data']


def matching_model(models):
    # MoriMate starts MLX with a relative path; the independent entry uses absolute.
    return any(item.get('id') in (MODEL, '.runtime/models/Qwen3.5-27B-4bit') for item in models)


def start_launch_agent():
    """Let macOS own the model so quitting the App cannot terminate it."""
    label = 'com.morimate.codex-mlx'
    domain = f'gui/{os.getuid()}'
    target = f'{domain}/{label}'
    agent = Path.home() / 'Library/LaunchAgents' / (label + '.plist')
    agent.parent.mkdir(parents=True, exist_ok=True)
    logs = Path.home() / 'Library/Logs/MoriMate'
    logs.mkdir(parents=True, exist_ok=True)
    definition = {
        'Label': label,
        'ProgramArguments': [str(ROOT / '.venv-mlx/bin/python'), '-m',
                             'mlx_vlm.server', '--model', MODEL,
                             '--host', '127.0.0.1', '--port', '18080'],
        'WorkingDirectory': str(Path.home()),
        'StandardOutPath': str(logs / 'codex-mlx.log'),
        'StandardErrorPath': str(logs / 'codex-mlx.log'),
        'RunAtLoad': False,
        'KeepAlive': False,
    }
    content = plistlib.dumps(definition).decode()
    loaded = subprocess.run(['launchctl', 'print', target], capture_output=True, text=True)
    changed = not agent.exists() or agent.read_text() != content
    if changed:
        if loaded.returncode == 0:
            subprocess.run(['launchctl', 'bootout', target], check=True, capture_output=True)
        atomic_write(agent, content)
    if changed or loaded.returncode != 0:
        result = subprocess.run(['launchctl', 'bootstrap', domain, str(agent)],
                                capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError('无法注册本地模型服务：' + result.stderr.strip())
    result = subprocess.run(['launchctl', 'kickstart', target], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('无法启动本地模型服务：' + result.stderr.strip())
    return target


def ensure_server():
    try:
        models = server_models()
    except (OSError, ValueError, KeyError):
        models = None
    if models is not None:
        if not matching_model(models):
            raise RuntimeError('18080 端口上的服务不是 MoriMate Qwen3.5-27B，配置未修改。')
        print('复用已运行的 MoriMate 模型服务。', flush=True)
        return
    with socket.socket() as connection:
        if connection.connect_ex(('127.0.0.1', 18080)) == 0:
            raise RuntimeError('18080 端口已占用，但模型接口不可用；配置未修改。')
    if not Path(MODEL, 'config.json').exists():
        raise RuntimeError('本地模型不存在，请检查 .runtime/models。')
    runtime = ROOT / '.runtime'
    runtime.mkdir(exist_ok=True)
    log = Path.home() / 'Library/Logs/MoriMate/codex-mlx.log'
    print('正在加载 Qwen3.5-27B，日志：' + str(log), flush=True)
    target = start_launch_agent()
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        state = subprocess.run(['launchctl', 'print', target], capture_output=True, text=True)
        if state.returncode or ('state = running' not in state.stdout
                                and re.search(r'last exit code = [1-9]', state.stdout)):
            raise RuntimeError('MLX 启动失败，请查看 ' + str(log))
        try:
            if matching_model(server_models()):
                pid = re.search(r'\bpid = (\d+)', state.stdout)
                if pid:
                    (runtime / 'codex-mlx.pid').write_text(pid[1] + '\n')
                return
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(1)
    subprocess.run(['launchctl', 'kill', 'SIGTERM', target], capture_output=True)
    raise RuntimeError('模型启动超时，配置未修改，请查看 ' + str(log))


def activate(config, mode):
    # Check the TOML before starting an expensive model process.
    tomllib.loads(config.read_text())
    if mode == 'local':
        ensure_server()
    switch_config(config, mode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['local', 'cloud'])
    args = parser.parse_args()
    config = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
    try:
        activate(config, args.mode)
    except (OSError, ValueError, RuntimeError) as error:
        print('切换失败：' + str(error), file=sys.stderr)
        return 1
    label = '本地 Qwen3.5-27B' if args.mode == 'local' else '云端模型（原设置）'
    print(f'已配置为{label}。请等待当前任务结束，按 ⌘Q 退出 App，再重新打开并新建聊天。')
    print('切换不会自动终止 App 或本地模型服务。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
