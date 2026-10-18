#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Check -E --buffer-size on real netlink sockets in an isolated netns.

Requires Python 3, strace and GNU timeout. Run as follows:
  CONNTRACK_TEST_PARENT_NETNS=$(readlink /proc/self/ns/net) \
    unshare --user --map-root-user --net python3 test-event-buffer.py /path/to/conntrack
No connections are created, removed or modified.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("conntrack", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    binary = args.conntrack.resolve()
    parent = os.environ.get("CONNTRACK_TEST_PARENT_NETNS")
    if not parent or parent == os.readlink("/proc/self/ns/net") or os.geteuid() != 0:
        raise SystemExit("Run in a new user/network namespace as documented")
    report = {"passed": False, "checks": 0, "cases": []}

    def check(condition, description):
        if not condition:
            raise AssertionError(description)
        report["checks"] += 1

    try:
        with tempfile.TemporaryDirectory(prefix="conntrack-event-buffer-") as temporary:
            for mask, groups in [(None, 7), ("NEW", 1), ("UPDATE", 2), ("DESTROY", 4)]:
                for requested in (32768, 1048576):
                    trace = Path(temporary) / "syscalls.log"
                    command = [
                        "strace",
                        "-f",
                        "-o",
                        str(trace),
                        "-e",
                        "trace=socket,bind,setsockopt,getsockopt",
                        "timeout",
                        "--preserve-status",
                        "--signal=TERM",
                        "1",
                        str(binary),
                        "-E",
                        "--buffer-size",
                        str(requested),
                    ]
                    if mask:
                        command += ["--event-mask", mask]
                    result = subprocess.run(command, capture_output=True, text=True, timeout=5)
                    text = trace.read_text()
                    case = {
                        "mask": mask or "ALL",
                        "requested": requested,
                        "exit": result.returncode,
                        "stderr": result.stderr,
                        "trace": text,
                    }
                    report["cases"].append(case)
                    check(result.returncode == 0, "Event command did not shut down cleanly")
                    binds = re.findall(
                        r"bind\((\d+), .*nl_groups=(0x[0-9a-f]+|0+)\}, \d+\) = 0", text
                    )
                    control = [int(fd) for fd, group in binds if int(group, 0) == 0]
                    events = [int(fd) for fd, group in binds if int(group, 0) == groups]
                    check(
                        len(control) == len(events) == 1,
                        "Expected control and event netlink sockets",
                    )
                    event_fd = events[0]
                    check(event_fd != control[0], "Event listener must be a separate socket")
                    writes = re.findall(
                        r"setsockopt\((\d+), SOL_SOCKET, SO_RCVBUF(?:FORCE)?, \[(\d+)\].*", text
                    )
                    check(
                        bool(writes) and all(int(fd) == event_fd for fd, _ in writes),
                        "--buffer-size changed the query socket instead of the event listener",
                    )
                    check(
                        all(int(size) == requested for _, size in writes), "Requested size changed"
                    )
                    check(
                        bool(
                            re.search(
                                r"setsockopt\("
                                + str(event_fd)
                                + r", SOL_SOCKET, SO_RCVBUF(?:FORCE)?, .* = 0",
                                text,
                            )
                        ),
                        "No receive-buffer option succeeded on the event listener",
                    )
                    reads = re.findall(r"getsockopt\((\d+), SOL_SOCKET, SO_RCVBUF, \[(\d+)\]", text)
                    check(
                        len(reads) == 1 and int(reads[0][0]) == event_fd,
                        "Reported buffer size was read from the query socket",
                    )
                    actual = int(reads[0][1])
                    check(actual > 0, "Kernel returned an invalid buffer size")
                    check(
                        f"set to {actual} bytes" in result.stderr,
                        "Notice does not describe the kernel event socket readback",
                    )
                    case.update(
                        event_fd=event_fd,
                        control_fd=control[0],
                        actual=actual,
                        force_fallback="EPERM" in text,
                    )
        report["passed"] = True
    except BaseException as exc:
        report["failure"] = str(exc)
        raise
    finally:
        if args.report:
            args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(f"PASS: {report['checks']} real-kernel assertions, {len(report['cases'])} event cases")


if __name__ == "__main__":
    main()
