import json
import httpx
from fastapi.testclient import TestClient
from open_llm_vtuber.companion.app import create_app
from open_llm_vtuber.companion.connections import ConnectionStore
from test_connections import Vault
from test_app import make_client, until, Model, Capture
from test_lan_app import login, connect
from test_lan_app import lan as _lan_fixture

lan = _lan_fixture


def test_owner_cloud_selection_and_guest_authorization(tmp_path, monkeypatch):
    from open_llm_vtuber.companion import guest_server, model_router
    from open_llm_vtuber.companion.cloud_model import CloudModel
    make_client(tmp_path)
    store = ConnectionStore(tmp_path / '.runtime/connections.json', Vault())
    store.save({'id': 'chat', 'provider': 'openai', 'model': 'chosen'}, 'secret')
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        text = json.dumps({'choices': [{'delta': {'content': '{"zh":"云端正常。","ja":"大丈夫。"}\n'}, 'finish_reason': 'stop'}]})
        return httpx.Response(200, text='data: ' + text + '\n\ndata: [DONE]\n\n')
    class RoutedCloud(CloudModel):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs, transport=httpx.MockTransport(respond))
    monkeypatch.setattr(model_router, 'CloudModel', RoutedCloud)
    monkeypatch.setattr(guest_server, 'lan_addresses', lambda: [{'address': '192.168.1.20', 'label': 'test'}])
    async def no_listener(self):
        pass
    monkeypatch.setattr(guest_server.LanSharing, 'start_listener', no_listener)
    app = create_app(tmp_path, model=Model(), voice=object(), capture=Capture(), connection_store=store)
    with TestClient(app) as client, client.websocket_connect('/client-ws') as owner:
        until(owner, 'memories')
        owner.send_json({'type': 'model-switch', 'model_key': 'cloud:chat'})
        while not (state := until(owner, 'status')).get('cloud') or state['model_phase'] != 'ready':
            pass
        owner.send_json({'type': 'sharing-password-set', 'password': 'test-password'})
        until(owner, 'sharing-status')
        owner.send_json({'type': 'sharing-enable', 'address': '192.168.1.20'})
        until(owner, 'sharing-status')
        guest = TestClient(app.state.sharing.app, base_url='http://192.168.1.20:12394')
        guest.portal = client.portal
        login(guest)
        with connect(guest) as ws:
            until(ws, 'memories')
            ws.send_json({'type': 'cloud-list'})
            assert '权限' in until(ws, 'error')['message']
            ws.send_json({'type': 'text-input', 'text': '未授权消息'})
            assert '授权' in until(ws, 'error')['message']
            assert requests == []
            owner.send_json({'type': 'cloud-save', 'connection': {'id': 'chat', 'provider': 'openai', 'model': 'chosen', 'allow_guests': True}})
            assert until(owner, 'cloud-connections')['items'][0]['has_key']
            while not until(ws, 'status').get('cloud_guests_allowed'):
                pass
            ws.send_json({'type': 'settings', 'muted': True})
            until(ws, 'status')
            ws.send_json({'type': 'text-input', 'text': '你好'})
            assert until(ws, 'sentence')['zh'] == '云端正常。'
            until(ws, 'audio')
            assert len(requests) == 1 and '未授权消息' not in str(requests)
            owner.send_json({'type': 'cloud-save', 'connection': {'id': 'chat', 'provider': 'openai', 'model': 'chosen', 'allow_guests': False}})
            until(owner, 'cloud-connections')
            while until(ws, 'status').get('cloud_guests_allowed'):
                pass
            ws.send_json({'type': 'text-input', 'text': '撤销后'})
            assert '授权' in until(ws, 'error')['message']
            assert len(requests) == 1
        owner.send_json({'type': 'cloud-usage'})
        usage = until(owner, 'cloud-usage')['items']
        assert usage[0]['purpose'] == 'chat' and usage[0]['usage'] is None


def test_saving_revoked_authorization_blocks_concurrent_guest_generation(lan, tmp_path, monkeypatch):
    import threading
    from open_llm_vtuber.companion import model_router
    from open_llm_vtuber.companion.cloud_model import CloudModel
    client, owner, guest = lan
    route = client.app.state.model_router
    store = ConnectionStore(tmp_path / 'race-connections.json', Vault())
    store.save({'id': 'chat', 'provider': 'openai', 'model': 'chosen', 'allow_guests': True}, 'secret')
    route.connections = store
    requests = []
    def respond(request):
        requests.append(request)
        event = {'choices': [{'delta': {'content': '{"zh":"不该输出。","ja":"だめ。"}\n'}, 'finish_reason': 'stop'}]}
        return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n')
    class RoutedCloud(CloudModel):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs, transport=httpx.MockTransport(respond))
    monkeypatch.setattr(model_router, 'CloudModel', RoutedCloud)
    owner.send_json({'type': 'model-switch', 'model_key': 'cloud:chat'})
    while not (state := until(owner, 'status')).get('cloud') or state['model_phase'] != 'ready':
        pass
    login(guest)
    entered, release = threading.Event(), threading.Event()
    save = store.save
    def slow_save(*args):
        entered.set()
        release.wait(2)
        return save(*args)
    store.save = slow_save
    with connect(guest) as ws:
        until(ws, 'memories')
        owner.send_json({'type': 'cloud-save', 'connection': {'id': 'chat', 'provider': 'openai', 'model': 'chosen', 'allow_guests': False}})
        try:
            assert entered.wait(1)
            ws.send_json({'type': 'text-input', 'text': '撤销期间的新请求'})
            assert '切换' in until(ws, 'error')['message']
            assert requests == []
        finally:
            release.set()
        until(owner, 'cloud-connections')
        while until(ws, 'status').get('cloud_guests_allowed'):
            pass
        ws.send_json({'type': 'text-input', 'text': '撤销完成后的请求'})
        assert '授权' in until(ws, 'error')['message']


def test_slow_connection_test_does_not_block_websocket_stop(lan, tmp_path):
    import asyncio
    import threading
    client, owner, _ = lan
    route = client.app.state.model_router
    store = ConnectionStore(tmp_path / 'test-connections.json', Vault())
    store.save({'id': 'chat', 'provider': 'openai', 'model': 'chosen'}, 'secret')
    route.connections = store
    entered, release = threading.Event(), threading.Event()
    async def slow_test(key):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
    route.test_connection = slow_test
    timer = threading.Timer(2, release.set)
    timer.start()
    try:
        owner.send_json({'type': 'cloud-test', 'connection_id': 'chat'})
        assert entered.wait(1)
        owner.send_json({'type': 'interrupt-signal'})
        assert '取消' in until(owner, 'cloud-test-result')['message']
        assert route.test_task.done()
    finally:
        release.set()
        timer.cancel()
