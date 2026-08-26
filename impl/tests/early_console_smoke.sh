#!/usr/bin/env bash
set -euo pipefail

if [[ ${1:-} != -- || $# -lt 2 ]]; then
    echo "usage: $0 -- qemu-system-riscv64 [arguments...]" >&2
    exit 2
fi
shift

output=$(mktemp)
qemu_pid=

stop_qemu() {
    if [[ -z ${qemu_pid} ]]; then
        return
    fi
    if ! kill -0 "${qemu_pid}" 2>/dev/null; then
        wait "${qemu_pid}" 2>/dev/null || true
        qemu_pid=
        return
    fi
    kill -TERM "${qemu_pid}" 2>/dev/null || true
    for _ in {1..20}; do
        if ! kill -0 "${qemu_pid}" 2>/dev/null; then
            break
        fi
        sleep 0.05
    done
    if kill -0 "${qemu_pid}" 2>/dev/null; then
        kill -KILL "${qemu_pid}" 2>/dev/null || true
    fi
    wait "${qemu_pid}" 2>/dev/null || true
    qemu_pid=
}

cleanup() {
    stop_qemu
    rm -f "${output}"
}
trap cleanup EXIT INT TERM

"$@" >"${output}" 2>&1 &
qemu_pid=$!

deadline=$((SECONDS + 10))
while (( SECONDS < deadline )); do
    if grep -aFq 'LKM2 kernel' "${output}"; then
        break
    fi
    if ! kill -0 "${qemu_pid}" 2>/dev/null; then
        echo "error: QEMU exited before the LKM2 banner appeared" >&2
        sed -n '1,240p' "${output}" >&2
        exit 1
    fi
    sleep 0.05
done

sleep 0.1
stop_qemu

occurrences=$(grep -aFo 'LKM2 kernel' "${output}" | wc -l || true)
complete_lines=$(sed 's/\r$//' "${output}" | grep -aFxc 'LKM2 kernel' || true)
if [[ ${occurrences} -ne 1 || ${complete_lines} -ne 1 ]]; then
    echo "error: expected exactly one complete 'LKM2 kernel' line" >&2
    sed -n '1,240p' "${output}" >&2
    exit 1
fi

echo "early-console-smoke: observed 'LKM2 kernel' exactly once"
