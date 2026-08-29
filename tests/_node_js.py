"""用 Node 顶替 quickjs，让前端行为测试在没有 C 编译器的机器上也能真的跑起来。

这些测试的用法只有两件事：`Context()` 和 `ctx.eval(源码)`，后者返回最后一个表达式
的值。所以这里不复刻 quickjs 的 API，只把这两件事接到一个常驻的 node 进程上：
每次 eval 是同一个 vm 上下文里的一段脚本，函数与顶层声明因此跨调用存活。

值按 JSON 往回带。测试比较的都是布尔、数字、字符串，或者自己先 JSON.stringify 过
一遍，所以够用；函数、Symbol 这类带不回来的东西会变成 None。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading

DRIVER = r"""
const vm = require("vm");
const context = vm.createContext({ console, JSON, Math, Date, RegExp, Promise });

let buffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  let newline = buffer.indexOf("\n");
  while (newline >= 0) {
    const line = buffer.slice(0, newline);
    buffer = buffer.slice(newline + 1);
    handle(line);
    newline = buffer.indexOf("\n");
  }
});

function encode(value) {
  if (value === undefined) return { ok: true, undefined: true };
  try {
    const json = JSON.stringify(value);
    // 函数、Symbol、循环引用都带不回去；对调用方来说它们等同于"没有值"。
    if (json === undefined) return { ok: true, undefined: true };
    return { ok: true, json };
  } catch (error) {
    return { ok: true, undefined: true };
  }
}

function handle(line) {
  if (!line) return;
  let reply;
  try {
    const code = JSON.parse(line).code;
    reply = encode(vm.runInContext(code, context, { timeout: 60000 }));
  } catch (error) {
    reply = { ok: false, error: String((error && error.stack) || error) };
  }
  process.stdout.write(JSON.stringify(reply) + "\n");
}
"""


class JSError(RuntimeError):
    pass


class Context:
    """一个常驻 node 进程里的 vm 上下文。"""

    def __init__(self) -> None:
        node = shutil.which("node")
        if not node:
            raise RuntimeError("node is required to evaluate frontend modules")
        self._lock = threading.Lock()
        self._process = subprocess.Popen(
            [node, "-e", DRIVER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )

    def eval(self, code: str):
        with self._lock:
            assert self._process.stdin and self._process.stdout
            self._process.stdin.write(json.dumps({"code": code}) + "\n")
            self._process.stdin.flush()
            line = self._process.stdout.readline()
        if not line:
            stderr = self._process.stderr.read() if self._process.stderr else ""
            raise JSError(f"node exited before answering: {stderr.strip()}")
        reply = json.loads(line)
        if not reply.get("ok"):
            raise JSError(reply.get("error", "unknown error"))
        if reply.get("undefined"):
            return None
        return json.loads(reply["json"])

    def __del__(self) -> None:  # pragma: no cover - 解释器退出时的清理
        process = getattr(self, "_process", None)
        if process and process.poll() is None:
            process.kill()


def available() -> bool:
    return shutil.which("node") is not None
