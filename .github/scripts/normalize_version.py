#!/usr/bin/env python3
"""清理并校验构建版本标签：输出严格的 vX.Y.Z。

手动触发时输入框内容常带有首尾空格、换行或复制粘贴带来的零宽字符，
直接进正则会误报「版本格式无效」。这里先清掉空白与不可见字符再校验，
只接受 vMAJOR.MINOR.PATCH（纯数字三段）。
"""
from __future__ import annotations

import re
import sys

_INVISIBLE = re.compile(r"[\s\u00a0\u200b-\u200d\u2060\ufeff]")
_VERSION = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")


def normalize(raw: str) -> str | None:
    if raw.startswith("refs/tags/"):
        raw = raw[len("refs/tags/"):]
    cleaned = _INVISIBLE.sub("", raw)
    return cleaned if _VERSION.fullmatch(cleaned) else None


def main() -> int:
    raw = sys.argv[1] if len(sys.argv) > 1 else ""
    version = normalize(raw)
    if version is None:
        print(f"版本格式无效：{raw}", file=sys.stderr)
        return 1
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
