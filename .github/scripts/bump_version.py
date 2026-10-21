#!/usr/bin/env python3
"""自动递增固件版本号：vX.Y.Z -> vX.Y.(Z+1)

- 读取 RLCD_CLOCK/CMakeLists.txt 的 PROJECT_VER
- 递增 PATCH
- 更新 CMakeLists.txt
- 更新 firmware/OTA_RELEASE_NOTES.md 首行版本号（若匹配旧版本）
- 输出新旧版本，供 workflow 使用
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CMAKE_PATH = ROOT / "RLCD_CLOCK" / "CMakeLists.txt"
OTA_NOTES_PATH = ROOT / "firmware" / "OTA_RELEASE_NOTES.md"

VERSION_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
CMAKE_VER_RE = re.compile(r'set\(PROJECT_VER\s+"(v[^"]+)"\)')

def parse_version(v: str) -> tuple[int, int, int]:
    m = VERSION_RE.fullmatch(v.strip())
    if not m:
        raise ValueError(f"invalid version: {v}")
    return tuple(int(x) for x in m.groups())  # type: ignore

def bump_patch(v: str) -> str:
    major, minor, patch = parse_version(v)
    return f"v{major}.{minor}.{patch+1}"

def read_cmake_version() -> str:
    text = CMAKE_PATH.read_text(encoding="utf-8")
    m = CMAKE_VER_RE.search(text)
    if not m:
        raise ValueError("PROJECT_VER not found in CMakeLists.txt")
    return m.group(1)

def write_cmake_version(old: str, new: str):
    text = CMAKE_PATH.read_text(encoding="utf-8")
    new_text = text.replace(f'set(PROJECT_VER "{old}")', f'set(PROJECT_VER "{new}")')
    if new_text == text:
        raise ValueError("CMake version replace failed")
    CMAKE_PATH.write_text(new_text, encoding="utf-8")

def update_ota_notes(old: str, new: str):
    if not OTA_NOTES_PATH.is_file():
        return
    text = OTA_NOTES_PATH.read_text(encoding="utf-8")
    # 首行形如 "v1.7.1：..."
    if text.startswith(old + "：") or text.startswith(old + ":"):
        # 替换首行版本号
        lines = text.splitlines()
        if lines:
            # 保留冒号后的内容
            rest = lines[0][len(old):]
            lines[0] = new + rest
            OTA_NOTES_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"OTA notes updated: {old} -> {new}")
    else:
        print(f"OTA notes first line does not start with {old}, skip update")

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--set", type=str, help="直接设为指定版本，不递增")
    args = parser.parse_args()

    try:
        current = read_cmake_version()
        print(f"current version: {current}")
        if args.set:
            new = args.set.strip()
            parse_version(new)
        else:
            new = bump_patch(current)
        print(f"new version: {new}")

        if args.dry_run:
            print("dry-run, not writing")
            return 0

        write_cmake_version(current, new)
        update_ota_notes(current, new)

        # 输出供 GitHub Actions 读取
        gh_output = Path.cwd() / ".bump_output"
        # 也尝试写 GITHUB_OUTPUT
        import os
        out_path = os.environ.get("GITHUB_OUTPUT")
        if out_path:
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(f"old_version={current}\n")
                f.write(f"new_version={new}\n")
        print(f"{current} -> {new}")
        return 0
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
