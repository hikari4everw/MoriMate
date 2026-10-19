import json
import pytest


class Vault:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


def test_connection_save_hides_key_and_edit_preserves_it(tmp_path):
    from open_llm_vtuber.companion.connections import ConnectionStore
    vault = Vault()
    store = ConnectionStore(tmp_path / 'connections.json', vault)
    view = store.save({'id': 'chat', 'provider': 'openai', 'model': 'my-model'}, 'secret-token')
    assert view['has_key'] and view['base_url'] == 'https://api.openai.com/v1'
    assert 'secret-token' not in json.dumps(view)
    assert 'secret-token' not in (tmp_path / 'connections.json').read_text()
    store.save({'id': 'chat', 'provider': 'openai', 'model': 'new-model'})
    assert vault.get('chat') == 'secret-token'
    assert ConnectionStore(store.path, vault).get('chat').model == 'new-model'
    store.delete('chat')
    assert not vault.values and store.views() == []


@pytest.mark.parametrize('field,value', [
    ('base_url', 'http://remote.test/v1'),
    ('base_url', 'https://user:password@example.com/v1'),
    ('base_url', 'https://example.com/v1?api_key=secret'),
    ('proxy', 'socks5://localhost:1080'),
    ('context_tokens', 100),
    ('id', '../bad'),
])
def test_connection_rejects_unsafe_or_impossible_configuration(tmp_path, field, value):
    from open_llm_vtuber.companion.connections import ConnectionStore
    store = ConnectionStore(tmp_path / 'c.json', Vault())
    with pytest.raises(ValueError):
        store.save({'id': 'chat', 'provider': 'gemini', 'model': 'chosen', field: value}, 'key')
    assert not store.views()


def test_keychain_failure_is_not_saved_as_plaintext_or_leaked(tmp_path):
    from open_llm_vtuber.companion.connections import ConnectionStore
    class Broken(Vault):
        def set(self, key, value):
            raise RuntimeError('failed: secret-token')
    store = ConnectionStore(tmp_path / 'c.json', Broken())
    with pytest.raises(RuntimeError) as error:
        store.save({'id': 'chat', 'provider': 'deepseek', 'model': 'chosen'}, 'secret-token')
    assert 'secret-token' not in str(error.value)
    assert not store.path.exists()
