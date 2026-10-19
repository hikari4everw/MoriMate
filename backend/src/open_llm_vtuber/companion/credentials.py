"""macOS Keychain only; never silently falls back to plaintext."""


class Keychain:
    service = "ai-desktop-companion.cloud"

    def _backend(self):
        try:
            from keyring.backends.macOS import Keyring
            return Keyring()
        except Exception:
            raise RuntimeError("macOS 钥匙串不可用，请安装本地依赖并解锁钥匙串") from None

    def get(self, key):
        return self._backend().get_password(self.service, key)

    def set(self, key, value):
        self._backend().set_password(self.service, key, value)

    def delete(self, key):
        backend = self._backend()
        if backend.get_password(self.service, key) is not None:
            backend.delete_password(self.service, key)
