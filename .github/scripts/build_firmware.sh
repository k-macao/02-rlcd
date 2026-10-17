#!/usr/bin/env bash
# 在 espressif/esp-idf-ci-action 容器内构建 App 与整机合并固件。
set -euo pipefail

version="${1:?用法: build_firmware.sh vX.Y.Z}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
out_dir="${repo_root}"
app_bin="weather_clock_${version}.bin"
merged_bin="weather_clock_${version}_merged.bin"

cd "${repo_root}/RLCD_CLOCK"
idf.py build
cp "build/weather_clock.bin" "${out_dir}/${app_bin}"

cd build
set +e
esptool.py --chip esp32s3 merge_bin \
    -o "${out_dir}/${merged_bin}" @flash_args
status=$?
if [ "$status" -ne 0 ]; then
    # esptool v5 起改为子命令 merge-bin。
    python -m esptool --chip esp32s3 merge-bin \
        -o "${out_dir}/${merged_bin}" @flash_args
    status=$?
fi
set -e
if [ "$status" -ne 0 ]; then
    echo "merge_bin 失败" >&2
    exit "$status"
fi

ls -l "${out_dir}/${app_bin}" "${out_dir}/${merged_bin}"
sha256sum "${out_dir}/${app_bin}" "${out_dir}/${merged_bin}"
