"""Manually controlled LAN listener. Only guest-safe routes live on this app."""

import asyncio
import contextlib
import ipaddress
import secrets
import socket
import subprocess

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse

from .sharing import GuestRegistry, digest

IDENTITY_COOKIE = "companion_identity"
SESSION_COOKIE = "companion_session"


def lan_addresses():
    """macOS network interface addresses; no public discovery service is contacted."""
    try:
        output = subprocess.run(
            ["/sbin/ifconfig"], capture_output=True, text=True, timeout=2, check=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    result, interface = [], ""
    for line in output.splitlines():
        if line and not line[0].isspace():
            interface = line.split(":", 1)[0]
        parts = line.strip().split()
        if len(parts) > 1 and parts[0] == "inet":
            address = ipaddress.ip_address(parts[1])
            if (
                address.is_private
                and not address.is_loopback
                and not address.is_link_local
            ):
                result.append(
                    {"address": str(address), "label": f"{interface} · {address}"}
                )
    return result


class GuestServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        # The primary loopback server owns process signal handling.
        yield


class LanSharing:
    port = 12394

    def __init__(self, root, handler, revoke):
        self.registry = GuestRegistry(root / ".runtime")
        self.handler, self.revoke = handler, revoke
        self.addresses = lan_addresses()
        self.address = self.addresses[0]["address"] if self.addresses else ""
        self.enabled = False
        self.sessions = {}
        self.server = self.server_task = None
        self.lock = asyncio.Lock()
        self.app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        self.register_routes()

    @property
    def origin(self):
        return f"http://{self.address}:{self.port}"

    def snapshot(self, online=0):
        return {
            "type": "sharing-status",
            "enabled": self.enabled,
            "password_set": self.registry.has_password,
            "addresses": self.addresses,
            "address": self.address,
            "url": self.origin if self.enabled else "",
            "online": online,
        }

    def refresh_addresses(self):
        if not self.enabled:
            self.addresses = lan_addresses()
            if self.address not in {item["address"] for item in self.addresses}:
                self.address = self.addresses[0]["address"] if self.addresses else ""

    def authenticated(self, cookies):
        token = cookies.get(SESSION_COOKIE)
        guest_id = self.sessions.get(digest(token)) if token else None
        if not self.enabled or guest_id is None:
            return None
        return self.registry.get(guest_id)

    def require_origin(self, origin):
        if origin != self.origin:
            raise HTTPException(403, "不允许此来源访问")

    def register_routes(self):
        @self.app.middleware("http")
        async def restrict_host(request: Request, call_next):
            if not self.enabled:
                return JSONResponse({"detail": "局域网共享已关闭"}, status_code=503)
            if request.headers.get("host") != f"{self.address}:{self.port}":
                return JSONResponse({"detail": "访问地址无效"}, status_code=403)
            return await call_next(request)

        @self.app.get("/api/session")
        async def session(request: Request):
            guest = self.authenticated(request.cookies)
            return {"role": "guest", "authenticated": guest is not None, "guest": guest}

        @self.app.post("/api/login")
        async def login(request: Request):
            self.require_origin(request.headers.get("origin"))
            try:
                data = await request.json()
            except ValueError:
                raise HTTPException(400, "登录内容无效") from None
            async with self.lock:
                if not self.enabled:
                    raise HTTPException(503, "局域网共享已关闭")
                if not isinstance(data, dict) or not self.registry.check_password(
                    data.get("password")
                ):
                    raise HTTPException(401, "访问密码错误")
                guest, identity = self.registry.identify(
                    request.cookies.get(IDENTITY_COOKIE)
                )
                old_session = request.cookies.get(SESSION_COOKIE)
                if old_session:
                    self.sessions.pop(digest(old_session), None)
                session_token = secrets.token_urlsafe(32)
                self.sessions[digest(session_token)] = guest["id"]
            response = JSONResponse(
                {"role": "guest", "authenticated": True, "guest": guest}
            )
            response.set_cookie(
                IDENTITY_COOKIE,
                identity,
                max_age=365 * 86400,
                httponly=True,
                samesite="strict",
            )
            response.set_cookie(
                SESSION_COOKIE, session_token, httponly=True, samesite="strict"
            )
            return response

        @self.app.post("/api/logout")
        async def logout(request: Request):
            self.require_origin(request.headers.get("origin"))
            guest = self.authenticated(request.cookies)
            token = request.cookies.get(SESSION_COOKIE)
            if token:
                self.sessions.pop(digest(token), None)
            if guest:
                await self.revoke(guest["id"], 4401, "已退出，请重新登录")
            response = JSONResponse({"authenticated": False})
            response.delete_cookie(SESSION_COOKIE)
            return response

        @self.app.websocket("/client-ws")
        async def websocket(ws: WebSocket):
            if (
                ws.headers.get("origin") != self.origin
                or ws.headers.get("host") != f"{self.address}:{self.port}"
            ):
                await ws.close(code=4403, reason="访问来源无效")
                return
            guest = self.authenticated(ws.cookies) if not self.lock.locked() else None
            if not guest:
                # A pre-accept close becomes HTTP 403; browsers lose the close code.
                await ws.accept()
                await ws.close(code=4401, reason="请输入访问密码登录")
                return
            await self.handler(ws, guest)

    async def enable(self, address):
        async with self.lock:
            if self.enabled:
                raise ValueError("共享已开启，请先关闭再更换地址")
            self.addresses = lan_addresses()
            if address not in {item["address"] for item in self.addresses}:
                raise ValueError("请选择有效的局域网地址")
            if not self.registry.has_password:
                raise ValueError("请先设置访问密码")
            self.address = address
            self.enabled = True
            try:
                await self.start_listener()
            except BaseException:
                self.enabled = False
                await self.stop_listener()
                raise

    async def start_listener(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.address, self.port))
            sock.listen(128)
            sock.setblocking(False)
            self.server = GuestServer(
                uvicorn.Config(
                    self.app,
                    host=self.address,
                    port=self.port,
                    lifespan="off",
                    ws_max_size=65536,
                    timeout_graceful_shutdown=2,
                    log_level="warning",
                )
            )
            self.server_task = asyncio.create_task(self.server.serve(sockets=[sock]))
            async with asyncio.timeout(5):
                while not self.server.started:
                    if self.server_task.done():
                        await self.server_task
                        raise RuntimeError("访客服务启动失败")
                    await asyncio.sleep(0.01)
        except BaseException:
            sock.close()
            raise

    async def stop_listener(self):
        if self.server_task:
            self.server.should_exit = True
            try:
                await asyncio.wait_for(asyncio.shield(self.server_task), timeout=5)
            except TimeoutError:
                self.server_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.server_task
            finally:
                self.server = self.server_task = None

    async def revoke_all(self, reason):
        self.sessions.clear()
        for guest in self.registry.list():
            await self.revoke(guest["id"], 4401, reason)

    async def disable(self):
        async with self.lock:
            self.enabled = False
            await self.revoke_all("局域网共享已关闭")
            await self.stop_listener()

    async def change_password(self, password):
        async with self.lock:
            self.registry.set_password(password)
            await self.revoke_all("访问密码已修改，请重新登录")

    async def delete(self, guest_id):
        async with self.lock:
            self.registry.get(guest_id)
            await self.revoke(guest_id, 4401, "访客数据已被主人清除，请重新登录")
            self.sessions = {
                key: value for key, value in self.sessions.items() if value != guest_id
            }
            self.registry.delete(guest_id)

    async def close(self):
        await self.disable()
        self.registry.close()
