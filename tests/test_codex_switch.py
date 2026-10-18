"""Protect the user's cloud configuration when switching local inference."""
import importlib.util
from pathlib import Path
import tomllib

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'codex_model.py'


def load_switcher():
    assert SCRIPT.exists(), '缺少模型切换脚本'
    spec = importlib.util.spec_from_file_location('codex_model', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_roundtrip_preserves_cloud_and_unrelated_settings(tmp_path):
    switcher = load_switcher()
    original = '# personal settings\nmodel = "gpt-6.1-sol"\nmodel_reasoning_effort = "medium"\nservice_tier = "default"\n\n[desktop]\npreventSleepWhileRunning = true\n'
    config = tmp_path / 'config.toml'
    config.write_text(original)
    switcher.switch_config(config, 'local')
    local = tomllib.loads(config.read_text())
    assert local['model_provider'] == 'morimate_local'
    assert local['model_providers']['morimate_local']['wire_api'] == 'responses'
    assert 'service_tier' not in local
    assert local['desktop']['preventSleepWhileRunning'] is True
    switcher.switch_config(config, 'local')
    assert config.read_text().count('[model_providers.morimate_local]') == 1
    switcher.switch_config(config, 'cloud')
    cloud = tomllib.loads(config.read_text())
    assert cloud['model'] == 'gpt-6.1-sol'
    assert cloud['model_reasoning_effort'] == 'medium'
    assert cloud['service_tier'] == 'default'
    assert 'model_provider' not in cloud
    assert '# personal settings' in config.read_text()
    assert (tmp_path / 'morimate-codex-backup.toml').read_text() == original


def test_service_failure_does_not_touch_config(tmp_path, monkeypatch):
    switcher = load_switcher()
    config = tmp_path / 'config.toml'
    config.write_text('model = "gpt-6.1-sol"\n')
    def fail():
        raise RuntimeError('端口占用')
    monkeypatch.setattr(switcher, 'ensure_server', fail)
    import pytest
    with pytest.raises(RuntimeError, match='端口占用'):
        switcher.activate(config, 'local')
    assert config.read_text() == 'model = "gpt-6.1-sol"\n'
    assert not (tmp_path / 'morimate-cloud.json').exists()


def test_local_catalog_restores_cloud_catalog(tmp_path):
    switcher = load_switcher()
    config = tmp_path / 'config.toml'
    config.write_text('model = "gpt-6.1-sol"\nmodel_catalog_json = "/tmp/cloud-catalog.json"\n')
    switcher.switch_config(config, 'local')
    local = tomllib.loads(config.read_text())
    import json
    catalog = json.loads(Path(local['model_catalog_json']).read_text())
    assert catalog['models'][0]['slug'] == switcher.MODEL
    assert catalog['models'][0]['display_name'] == 'MoriMate Qwen3.5-27B（本地）'
    switcher.switch_config(config, 'cloud')
    assert tomllib.loads(config.read_text())['model_catalog_json'] == '/tmp/cloud-catalog.json'


def test_wrong_service_does_not_touch_config(tmp_path, monkeypatch):
    switcher = load_switcher()
    config = tmp_path / 'config.toml'
    config.write_text('model = "gpt-6.1-sol"\n')
    monkeypatch.setattr(switcher, 'server_models', lambda: [{'id': 'some-other-model'}])
    import pytest
    with pytest.raises(RuntimeError, match='不是 MoriMate'):
        switcher.activate(config, 'local')
    assert config.read_text() == 'model = "gpt-6.1-sol"\n'


def test_occupied_port_does_not_launch_or_write(tmp_path, monkeypatch):
    switcher = load_switcher()
    config = tmp_path / 'config.toml'
    config.write_text('model = "gpt-6.1-sol"\n')
    def unavailable():
        raise OSError('not an HTTP model server')
    class OccupiedSocket:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def connect_ex(self, address):
            return 0
    monkeypatch.setattr(switcher, 'server_models', unavailable)
    monkeypatch.setattr(switcher.socket, 'socket', OccupiedSocket)
    import pytest
    with pytest.raises(RuntimeError, match='端口已占用'):
        switcher.activate(config, 'local')
    assert config.read_text() == 'model = "gpt-6.1-sol"\n'


def test_repeated_cloud_switch_keeps_new_user_model_settings(tmp_path):
    switcher = load_switcher()
    config = tmp_path / 'config.toml'
    config.write_text('model = "gpt-6.1-sol"\nmodel_reasoning_effort = "medium"\n')
    switcher.switch_config(config, 'local')
    switcher.switch_config(config, 'cloud')
    config.write_text(config.read_text().replace('"medium"', '"high"'))
    updated = config.read_text()
    switcher.switch_config(config, 'cloud')
    assert config.read_text() == updated


def test_model_service_is_owned_by_launchd_without_login_autostart(tmp_path, monkeypatch):
    switcher = load_switcher()
    monkeypatch.setattr(switcher.Path, 'home', lambda: tmp_path)
    seen = []
    def run(command, **kwargs):
        seen.append(command)
        import subprocess
        if command[1] == 'print':
            return subprocess.CompletedProcess(command, 113, '', 'service not found')
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(switcher.subprocess, 'run', run)
    assert hasattr(switcher, 'start_launch_agent'), '模型仍作为 App 子进程启动'
    target = switcher.start_launch_agent()
    import plistlib
    agent = tmp_path / 'Library/LaunchAgents/com.morimate.codex-mlx.plist'
    definition = plistlib.loads(agent.read_bytes())
    assert definition['RunAtLoad'] is False
    assert definition['KeepAlive'] is False
    assert definition['ProgramArguments'][1:3] == ['-m', 'mlx_vlm.server']
    assert ['launchctl', 'bootstrap', f'gui/{switcher.os.getuid()}', str(agent)] in seen
    assert ['launchctl', 'kickstart', target] in seen
