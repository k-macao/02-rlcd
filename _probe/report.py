#!/usr/bin/env python3
"""临时：把 ESP-IDF 构建日志压缩成一份可提交的报告（运行日志在本环境无法下载）。"""
import re
import sys

log_path, exit_code, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
lines = open(log_path, encoding="utf-8", errors="replace").read().splitlines()
lines = [re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line) for line in lines]

error_re = re.compile(
    r"error:|Error:|ERROR|undefined reference|overflowed|FAILED|CMake Error|"
    r"multiple definition|No such file|ninja: build stopped|region `.*' overflowed")
warning_re = re.compile(r"warning:")

keep = set()
for index, line in enumerate(lines):
    if error_re.search(line):
        keep.update(range(max(0, index - 4), min(len(lines), index + 14)))

report = [f"exit_code={exit_code}", f"log_lines={len(lines)}", ""]
report.append("=== errors with context ===")
previous = None
shown = 0
for index in sorted(keep):
    if shown >= 700:
        report.append("... (truncated)")
        break
    if previous is not None and index != previous + 1:
        report.append("    ...")
    report.append(f"{index + 1}: {lines[index][:600]}")
    previous = index
    shown += 1

report.append("")
report.append("=== warnings (project sources only, unique) ===")
seen = set()
for line in lines:
    if warning_re.search(line) and "/main/" in line and line not in seen:
        seen.add(line)
        report.append(line[:600])
        if len(seen) >= 200:
            report.append("... (truncated)")
            break

report.append("")
report.append("=== size / partition lines ===")
for line in lines:
    if re.search(r"binary size|Smallest app partition|bytes \(\d+%\) free|Project build complete", line):
        report.append(line[:600])

report.append("")
report.append("=== log tail ===")
report.extend(line[:600] for line in lines[-60:])
open(out_path, "w", encoding="utf-8").write("\n".join(report) + "\n")
