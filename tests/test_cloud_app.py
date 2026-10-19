import json
import httpx
from fastapi.testclient import TestClient
from open_llm_vtuber.companion.app import create_app
from open_llm_vtuber.companion.connections import ConnectionStore
from test_connections import Vault
from test_app import make_client, until, Model, Capture
from test_lan_app import login, connect


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
