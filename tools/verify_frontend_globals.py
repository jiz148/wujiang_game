"""Find identifiers a frontend module uses but never defines or imports.

The ES module link check in verify_frontend_modules.mjs only proves that every
`import` resolves. A bare identifier that was module-local before the split has
no import to resolve, so it stays invisible until the browser executes that exact
line and throws ReferenceError. This scanner closes that gap by comparing, per
module, the identifiers that are read against the ones that are in scope.

Heuristic but deliberately conservative: string and comment text is removed
first, property accesses and object keys are ignored, and anything declared
anywhere in the file counts as in scope. It reports only names that resolve to
nothing at all, which is precisely the case that throws at runtime.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

STATIC_ROOT = Path(__file__).resolve().parent.parent / "static"

# Globals the browser supplies. Anything here is legitimately free.
BROWSER_GLOBALS = {
    "AbortController", "Array", "ArrayBuffer", "Audio", "AudioContext", "Blob",
    "Boolean", "CSS", "CustomEvent", "Date", "Error", "Event", "EventSource",
    "File", "FileReader", "FormData", "Function", "Headers", "Image", "Infinity",
    "Intl", "JSON", "Map", "Math", "NaN", "Number", "Object", "Promise", "Proxy",
    "Range", "Reflect", "RegExp", "Request", "Response", "Set", "String",
    "Symbol", "TextDecoder", "TextEncoder", "URL", "URLSearchParams", "WeakMap",
    "WeakSet", "WebSocket", "Worker", "alert", "atob", "btoa",
    "cancelAnimationFrame", "clearInterval", "clearTimeout", "confirm",
    "console", "crypto", "decodeURI", "decodeURIComponent", "document",
    "encodeURI", "encodeURIComponent", "fetch", "getComputedStyle", "globalThis",
    "history", "isFinite", "isNaN", "localStorage", "location", "matchMedia",
    "navigator", "parseFloat", "parseInt", "performance", "prompt",
    "queueMicrotask", "requestAnimationFrame", "screen", "sessionStorage",
    "setInterval", "setTimeout", "structuredClone", "undefined", "window",
    "BigInt", "CanvasRenderingContext2D", "DOMParser", "DocumentFragment",
    "Element", "HTMLElement", "IntersectionObserver", "MutationObserver",
    "Node", "Notification", "ResizeObserver", "SVGElement", "XMLHttpRequest",
}

# Names published by the classic scripts that load before the module graph.
CLASSIC_SCRIPT_GLOBALS = {"Wujiang", "WujiangReplayUI", "WujiangHomeUI", "WujiangBattleFeedback"}

# Language keywords and contextual words that the identifier regex also matches.
KEYWORDS = {
    "arguments", "as", "async", "await", "break", "case", "catch", "class",
    "const", "continue", "debugger", "default", "delete", "do", "else", "export",
    "extends", "false", "finally", "for", "from", "function", "get", "if",
    "import", "in", "instanceof", "let", "new", "null", "of", "return", "set",
    "static", "super", "switch", "this", "throw", "true", "try", "typeof", "var",
    "void", "while", "with", "yield",
}

IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


# A '/' begins a regex literal only where a value cannot already have ended.
REGEX_ALLOWED_BEFORE = set("=(,:[!&|?{};+-*%~^<>")
REGEX_ALLOWED_KEYWORDS = ("return", "typeof", "instanceof", "in", "of", "case", "do", "else", "yield", "await")


def _starts_regex(code: str, index: int) -> bool:
    head = code[:index].rstrip()
    if not head:
        return True
    if head[-1] in REGEX_ALLOWED_BEFORE:
        return True
    word = re.search(r"[A-Za-z_$][\w$]*$", head)
    return bool(word and word.group() in REGEX_ALLOWED_KEYWORDS)


def strip_noise(source: str) -> str:
    """Remove comments, literal text, and regex bodies.

    Template placeholders are recursively stripped: they hold real code, but that
    code can itself contain strings whose contents would otherwise leak out and
    be mistaken for identifiers.
    """
    out = []
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            i = source.find("\n", i)
            if i == -1:
                break
        elif ch == "/" and nxt == "*":
            end = source.find("*/", i + 2)
            i = n if end == -1 else end + 2
        elif ch == "/" and _starts_regex(source, i):
            i += 1
            in_class = False
            while i < n:
                if source[i] == "\\":
                    i += 2
                    continue
                if source[i] == "[":
                    in_class = True
                elif source[i] == "]":
                    in_class = False
                elif source[i] == "/" and not in_class:
                    i += 1
                    break
                elif source[i] == "\n":
                    break
                i += 1
            while i < n and source[i].isalpha():  # trailing flags
                i += 1
            out.append(" ")
        elif ch in "\"'`":
            quote = ch
            i += 1
            while i < n:
                if source[i] == "\\":
                    i += 2
                    continue
                if quote == "`" and source[i] == "$" and source[i + 1 : i + 2] == "{":
                    depth = 1
                    i += 2
                    start = i
                    while i < n and depth:
                        if source[i] == "{":
                            depth += 1
                        elif source[i] == "}":
                            depth -= 1
                        i += 1
                    out.append(" " + strip_noise(source[start : i - 1]) + " ")
                    continue
                if source[i] == quote:
                    i += 1
                    break
                i += 1
            out.append(" ")
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def declared_names(code: str) -> set[str]:
    """Every name bound anywhere in the file, at any nesting depth."""
    names: set[str] = set()
    for pattern in (
        r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)",
        r"\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)",
        r"\bclass\s+([A-Za-z_$][\w$]*)",
        r"\bcatch\s*\(\s*([A-Za-z_$][\w$]*)",
        r"\bimport\s+([A-Za-z_$][\w$]*)\s+from",
        r"\bfor\s*\(\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)",
    ):
        names.update(re.findall(pattern, code))

    # Destructuring and import lists: pull every name out of the binding group.
    for group in re.findall(r"(?:const|let|var|import)\s*[{\[]([^}\]]*)[}\]]", code):
        names.update(IDENTIFIER.findall(group))

    for group in _parameter_lists(code):
        names.update(_binding_names(group))
    for single in re.findall(r"([A-Za-z_$][\w$]*)\s*=>", code):
        names.add(single)

    return names - KEYWORDS


# `(...)` followed by a block belongs to these constructs, not to a function.
CONTROL_KEYWORDS = {"if", "for", "while", "switch", "catch", "with"}


def _match_bracket(code: str, start: int) -> int:
    """Index just past the bracket that closes the one opened at `start`."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    close = pairs[code[start]]
    depth = 0
    for i in range(start, len(code)):
        if code[i] in pairs:
            depth += 1
        elif code[i] in pairs.values():
            depth -= 1
            if depth == 0 and code[i] == close:
                return i
    return -1


def _parameter_lists(code: str) -> list[str]:
    """Bodies of every `(...)` that introduces function parameters."""
    groups = []
    for i, ch in enumerate(code):
        if ch != "(":
            continue
        end = _match_bracket(code, i)
        if end == -1:
            continue
        after = code[end + 1 :].lstrip()
        if not (after.startswith("=>") or after.startswith("{")):
            continue
        head = code[:i].rstrip()
        word = re.search(r"[A-Za-z_$][\w$]*$", head)
        if word and word.group() in CONTROL_KEYWORDS:
            continue
        groups.append(code[i + 1 : end])
    return groups


def _split_top_level(text: str) -> list[str]:
    parts, depth, current = [], 0, []
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _binding_names(group: str) -> set[str]:
    """Names bound by a parameter list, ignoring default-value expressions."""
    names: set[str] = set()
    for segment in _split_top_level(group):
        depth = 0
        cut = len(segment)
        for i, ch in enumerate(segment):
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == "=" and depth == 0 and segment[i : i + 2] != "==":
                cut = i
                break
        target = segment[:cut].strip().lstrip(".")
        if not target:
            continue
        if target[0] in "{[":
            inner = target[1:-1] if len(target) > 1 else ""
            for piece in _split_top_level(inner):
                # `{ key: binding }` binds the right-hand name, not the key.
                piece = piece.split(":", 1)[-1] if ":" in piece else piece
                names.update(_binding_names(piece))
        else:
            match = re.match(r"[A-Za-z_$][\w$]*", target)
            if match:
                names.add(match.group())
    return names


def used_names(code: str) -> set[str]:
    """Identifiers read as values, excluding property names and object keys."""
    used: set[str] = set()
    for match in IDENTIFIER.finditer(code):
        name = match.group()
        if name in KEYWORDS:
            continue
        before = code[max(0, match.start() - 2) : match.start()]
        if before.rstrip().endswith(".") or before.rstrip().endswith("?."):
            continue
        after = code[match.end() : match.end() + 2].lstrip()
        if after.startswith(":"):
            preceding = code[: match.start()].rstrip()
            if preceding.endswith("{") or preceding.endswith(","):
                continue
        used.add(name)
    return used


def scan() -> dict[str, set[str]]:
    findings: dict[str, set[str]] = {}
    for path in sorted(STATIC_ROOT.rglob("*.js")):
        raw = path.read_text(encoding="utf-8")
        if "import " not in raw and "export " not in raw:
            continue  # classic script, not part of the module graph
        code = strip_noise(raw)
        # Module statement headers bind or forward names rather than read them.
        body = re.sub(r"^[ \t]*import\b[^;]*;", " ", code, flags=re.M)
        body = re.sub(r"^[ \t]*export\s*\{[^}]*\}\s*from[^;]*;", " ", body, flags=re.M)
        free = used_names(body) - declared_names(code) - BROWSER_GLOBALS - CLASSIC_SCRIPT_GLOBALS
        if free:
            findings[str(path.relative_to(STATIC_ROOT)).replace("\\", "/")] = free
    return findings


def main() -> int:
    findings = scan()
    if not findings:
        print("GLOBALS OK: every identifier resolves to an import or a declaration")
        return 0
    total = sum(len(v) for v in findings.values())
    print(f"UNDEFINED IDENTIFIERS: {total} across {len(findings)} module(s)")
    for module, names in findings.items():
        print(f"  {module}: {', '.join(sorted(names))}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
