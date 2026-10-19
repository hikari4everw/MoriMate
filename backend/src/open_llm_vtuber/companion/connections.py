"""Non-secret connection metadata, separate from Keychain credentials."""
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

from .credentials import Keychain

PRESETS = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "compatible": "",
}


def validate_url(value, *, proxy=False):
    parsed = urlparse(value)
    if (
        not parsed.hostname or parsed.username or parsed.password
        or parsed.query or parsed.fragment
        or parsed.scheme not in ({"http", "https"} if proxy else {"https"})
        and not (not proxy and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
    ):
        raise ValueError("地址需为 HTTPS（本机服务可 HTTP），不能包含凭据、查询或片段")
    if proxy and parsed.path not in {"", "/"}:
        raise ValueError("代理地址不能包含路径")
    try:
        parsed.port
    except ValueError:
        raise ValueError("地址端口无效") from None
    return value.rstrip("/")


@dataclass(frozen=True)
class ConnectionSpec:
    id: str
    provider: str
    model: str
    name: str = ""
    base_url: str = ""
    proxy: str = ""
    context_tokens: int = 32768
    max_output_tokens: int = 8192
    supports_vision: bool = False
    allow_guests: bool = False
    allow_images: bool = False

    def __post_init__(self):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", self.id):
            raise ValueError("连接ID需为1–64位字母、数字、下划线或短横线")
        if self.provider not in PRESETS or not isinstance(self.model, str) or not self.model.strip() or len(self.model) > 200:
            raise ValueError("请选择提供方并填写模型名")
        if not isinstance(self.name, str) or len(self.name) > 100:
            raise ValueError("连接名称过长")
        for value in (self.context_tokens, self.max_output_tokens):
            if type(value) is not int or value < 256 or value > 2000000:
                raise ValueError("token容量无效")
        if self.context_tokens < self.max_output_tokens + 1024:
            raise ValueError("上下文需为输出保留至少1024 token输入空间")
        for value in (self.supports_vision, self.allow_guests, self.allow_images):
            if type(value) is not bool:
                raise ValueError("能力与授权必须为布尔值")
        object.__setattr__(self, "base_url", validate_url(self.base_url or PRESETS[self.provider]))
        if self.proxy:
            object.__setattr__(self, "proxy", validate_url(self.proxy, proxy=True))


class ConnectionStore:
    def __init__(self, path, credentials=None):
        self.path = Path(path)
        self.credentials = credentials if credentials is not None else Keychain()
        raw = json.loads(self.path.read_text()) if self.path.exists() else []
        self.specs = {item["id"]: ConnectionSpec(**item) for item in raw}

    def get(self, key):
        if key not in self.specs:
            raise ValueError("云连接不存在")
        return self.specs[key]

    def key(self, key):
        try:
            value = self.credentials.get(key)
        except Exception:
            raise RuntimeError("无法读取API密钥，请检查钥匙串") from None
        if not value:
            raise RuntimeError("此连接尚未配置API密钥")
        return value

    def _write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps([asdict(s) for s in self.specs.values()], ensure_ascii=False, indent=2))
        temporary.chmod(0o600)
        temporary.replace(self.path)

    def view(self, spec):
        try:
            configured = bool(self.credentials.get(spec.id))
        except Exception:
            configured = False
        return {**asdict(spec), "has_key": configured}

    def views(self):
        return [self.view(spec) for spec in self.specs.values()]

    def save(self, data, api_key=None):
        try:
            spec = ConnectionSpec(**data)
        except TypeError:
            raise ValueError("连接配置字段无效") from None
        if api_key is not None:
            if not isinstance(api_key, str) or not api_key.strip() or len(api_key) > 4096:
                raise ValueError("API密钥不能为空或过长")
            try:
                self.credentials.set(spec.id, api_key.strip())
            except Exception:
                raise RuntimeError("API密钥未保存：macOS钥匙串不可用") from None
        self.specs[spec.id] = spec
        self._write()
        return self.view(spec)

    def delete(self, key):
        self.get(key)
        try:
            self.credentials.delete(key)
        except Exception:
            raise RuntimeError("无法删除钥匙串凭据，连接未移除") from None
        del self.specs[key]
        self._write()
