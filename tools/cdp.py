"""A minimal Chrome DevTools Protocol client built on the standard library.

Why this exists: `--dump-dom` and `--screenshot` snapshot the page when Chrome
decides the load is done, which is before this app has finished booting -- it
awaits /api/auth/me and /api/heroes before it can know which screen to show.
`--virtual-time-budget` does not help, because virtual time races ahead while a
real network request is still in flight, so the snapshot still lands on the
login gate no matter how large the budget is.

Driving the browser over CDP lets us wait for an actual condition instead of
guessing a duration. The project deliberately has no npm dependencies and no
build step, so rather than pulling in Puppeteer this speaks the protocol
directly: an HTTP upgrade plus enough WebSocket framing for JSON messages.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import socket
import struct
import time
import urllib.request
from dataclasses import dataclass, field

_OPCODE_TEXT = 0x1
_OPCODE_BINARY = 0x2
_OPCODE_CLOSE = 0x8
_OPCODE_PING = 0x9
_OPCODE_PONG = 0xA


class CdpError(RuntimeError):
    pass


@dataclass
class Browser:
    """A CDP session against one page target."""

    port: int
    sock: socket.socket
    _next_id: int = 1
    _buffer: bytearray = field(default_factory=bytearray)

    # -- WebSocket plumbing ------------------------------------------------

    def _send_frame(self, payload: bytes) -> None:
        header = bytearray([0x80 | _OPCODE_TEXT])
        mask = os.urandom(4)
        size = len(payload)
        if size < 126:
            header.append(0x80 | size)
        elif size < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack(">H", size)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", size)
        header += mask
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def _read_exactly(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CdpError("浏览器连接已关闭")
            self._buffer += chunk
        taken = bytes(self._buffer[:count])
        del self._buffer[:count]
        return taken

    def _recv_frame(self) -> tuple[int, bytes]:
        first, second = self._read_exactly(2)
        opcode = first & 0x0F
        size = second & 0x7F
        if size == 126:
            size = struct.unpack(">H", self._read_exactly(2))[0]
        elif size == 127:
            size = struct.unpack(">Q", self._read_exactly(8))[0]
        # Server frames are never masked.
        return opcode, self._read_exactly(size)

    def _recv_message(self) -> dict:
        payload = bytearray()
        while True:
            opcode, chunk = self._recv_frame()
            if opcode == _OPCODE_CLOSE:
                raise CdpError("浏览器关闭了调试连接")
            if opcode == _OPCODE_PING:
                continue
            if opcode in (_OPCODE_TEXT, _OPCODE_BINARY, 0x0):
                payload += chunk
                return json.loads(payload.decode("utf-8"))

    # -- CDP ---------------------------------------------------------------

    def call(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        message_id = self._next_id
        self._next_id += 1
        self._send_frame(json.dumps({"id": message_id, "method": method, "params": params or {}}).encode())
        self.sock.settimeout(timeout)
        while True:
            message = self._recv_message()
            if message.get("id") != message_id:
                continue  # An event, or a reply we are no longer waiting on.
            if "error" in message:
                raise CdpError(f"{method} 失败: {message['error']}")
            return message.get("result", {})

    def evaluate(self, expression: str, timeout: float = 30.0):
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        if result.get("exceptionDetails"):
            detail = result["exceptionDetails"]
            text = detail.get("exception", {}).get("description") or detail.get("text")
            raise CdpError(f"页面内求值抛错: {text}")
        return result.get("result", {}).get("value")

    def navigate(self, url: str) -> None:
        self.call("Page.enable")
        self.call("Page.navigate", {"url": url})

    def wait_for(self, expression: str, timeout: float = 20.0, interval: float = 0.25) -> bool:
        """Poll a JS predicate until it is truthy. Returns whether it became true."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.evaluate(expression, timeout=10.0):
                    return True
            except CdpError:
                pass  # A navigation can invalidate the context mid-poll.
            time.sleep(interval)
        return False

    def html(self) -> str:
        return self.evaluate("document.documentElement.outerHTML") or ""

    def screenshot(self, path) -> None:
        result = self.call("Page.captureScreenshot", {"format": "png"}, timeout=60.0)
        with open(path, "wb") as handle:
            handle.write(base64.b64decode(result["data"]))

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.sock.close()


def connect(port: int, timeout: float = 20.0) -> Browser:
    """Open a page target on an already-running Chrome and speak CDP to it."""
    deadline = time.time() + timeout
    target = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as response:
                targets = json.load(response)
            target = next((item for item in targets if item.get("type") == "page"), None)
            if target:
                break
        except Exception:  # noqa: BLE001 - the browser may still be starting
            pass
        time.sleep(0.2)
    if not target:
        raise CdpError("未能连接到浏览器调试端口")

    ws_url = target["webSocketDebuggerUrl"]
    path = ws_url.split(f"127.0.0.1:{port}", 1)[-1] if f"127.0.0.1:{port}" in ws_url else "/" + ws_url.split("/", 3)[-1]

    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(request.encode())

    # Read just the handshake response; anything past it belongs to the framing layer.
    buffer = bytearray()
    while b"\r\n\r\n" not in buffer:
        chunk = sock.recv(4096)
        if not chunk:
            raise CdpError("调试端口握手失败")
        buffer += chunk
    head, _, rest = bytes(buffer).partition(b"\r\n\r\n")
    if b"101" not in head.split(b"\r\n")[0]:
        raise CdpError(f"调试端口拒绝升级: {head.splitlines()[0]!r}")

    browser = Browser(port=port, sock=sock)
    browser._buffer = bytearray(rest)
    return browser
