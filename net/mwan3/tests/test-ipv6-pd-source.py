#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check source selection using real addresses and binds in a private netns.

The netifd accessors are fixtures: a PD-only WAN has a prefix and no global
address. The complete common.sh, ip address enumeration and socket binds are
real. Never run this test in the caller's network namespace.
"""

import argparse
import ipaddress
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--common", type=Path, required=True)
    parser.add_argument("--shell", default="busybox ash")
    parser.add_argument("--sed", type=Path, default=Path(shutil.which("sed")))
    parser.add_argument("--ip", type=Path, default=Path(shutil.which("ip")))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    parent = os.environ.get("MWAN3_TEST_PARENT_NETNS")
    current = os.readlink("/proc/self/ns/net")
    if os.geteuid() != 0 or not parent or parent == current:
        raise SystemExit("Refusing test outside a root, isolated network namespace")
    common = args.common.resolve(strict=True)
    sed = args.sed.resolve(strict=True)
    ip_command = args.ip.resolve(strict=True)
    tools = tempfile.TemporaryDirectory(prefix="mwan3-pd-tools-")
    # BusyBox selects its actual sed applet by the symlink basename.
    (Path(tools.name) / "sed").symlink_to(sed)
    (Path(tools.name) / "ip").symlink_to(ip_command)
    count = 0
    results = []

    def check(condition, message):
        nonlocal count
        count += 1
        if not condition:
            raise AssertionError(message)

    def ip(*words):
        return subprocess.check_output(["ip", *words], text=True)

    for device in ("pd-wan", "other-wan", "pd-lan"):
        ip("link", "add", device, "type", "dummy")
        ip("link", "set", device, "up")
    ip("-4", "addr", "add", "192.0.2.2/24", "dev", "pd-wan")
    ip("-6", "addr", "add", "2001:db8:eeee::1/64", "dev", "other-wan", "nodad")

    shell = r"""
. "$1"
# Explicit paths prevent BusyBox standalone applets from bypassing the chosen
# external tools (especially a target ip-tiny binary running under QEMU).
IP4="$TEST_IP_COMMAND -4"
IP6="$TEST_IP_COMMAND -6"
sed() { command "$TEST_SED_COMMAND" "$@"; }
mwan3_get_true_iface() { export "$1=$2"; }
config_get() { export "$1=$TEST_FAMILY"; }
network_get_ipaddr() { export "$1=$TEST_ADDRESS"; }
network_get_ipaddr6() { export "$1=$TEST_ADDRESS"; }
network_get_prefix6() { export "$1=$TEST_PREFIX"; }
network_get_device() { export "$1=pd-wan"; }
LOG() { :; }
mwan3_get_src_ip selected wan
printf '%s\n' "$selected"
"""

    def run_case(
        name,
        expected,
        *,
        delegated=None,
        family="ipv6",
        direct="",
        prefix="2001:db8:abcd::/48",
        competing=None,
    ):
        ip("-6", "addr", "flush", "dev", "pd-lan", "scope", "global")
        if delegated:
            ip("-6", "addr", "add", delegated + "/64", "dev", "pd-lan", "nodad")
        if competing:
            ip("-6", "addr", "add", competing + "/64", "dev", "other-wan", "nodad")
        env = dict(
            os.environ,
            TEST_FAMILY=family,
            TEST_ADDRESS=direct,
            TEST_PREFIX=prefix,
            TEST_IP_COMMAND=str(Path(tools.name) / "ip"),
            TEST_SED_COMMAND=str(Path(tools.name) / "sed"),
            PATH=tools.name + os.pathsep + os.environ["PATH"],
        )
        selected = subprocess.check_output(
            shlex.split(args.shell) + ["-c", shell, "mwan3-pd-test", str(common)],
            env=env,
            text=True,
        ).strip()
        bind_error = None
        bound = None
        try:
            address = ipaddress.ip_address(selected)
            with socket.socket(
                socket.AF_INET6 if address.version == 6 else socket.AF_INET, socket.SOCK_DGRAM
            ) as sock:
                sock.bind((selected, 0))
                bound = sock.getsockname()[0]
        except (ValueError, OSError) as exc:
            bind_error = str(exc)
        check(
            selected == expected,
            f"{name}: expected {expected}, got {selected!r}; bind error: {bind_error}",
        )
        check(bind_error is None, f"{name}: cannot bind selected source: {bind_error}")
        check(bound == selected, f"{name}: kernel bind selected another address")
        results.append({"case": name, "selected": selected, "kernel_bind": True})
        if competing:
            ip("-6", "addr", "del", competing + "/64", "dev", "other-wan")

    run_case("PD digits 0-6", "2001:db8:abcd:1234::6", delegated="2001:db8:abcd:1234::6")
    for digit in "789":
        address = "2001:db8:abcd:1234::" + digit
        run_case("PD suffix digit " + digit, address, delegated=address)
        address = "2001:db8:abcd:" + digit + "::1"
        run_case("PD subnet digit " + digit, address, delegated=address)
    for suffix in ("789a:bcde:f012:3456:789a", "1:1234:5678:9abc:def0", "aaaa:bbbb:cccc:dddd:ffff"):
        address = "2001:db8:abcd:" + suffix
        run_case("PD full suffix " + suffix, address, delegated=address)
    run_case(
        "netifd direct IPv6",
        "2001:db8:abcd:7::1",
        delegated="2001:db8:abcd:7::1",
        direct="2001:db8:abcd:7::1",
    )
    ip("-6", "addr", "add", "2001:db8:ffff::1/64", "dev", "pd-wan", "nodad")
    run_case("no prefix: WAN fallback", "2001:db8:ffff::1", prefix="")
    ip("-6", "addr", "del", "2001:db8:ffff::1/64", "dev", "pd-wan")
    run_case("netifd direct IPv4", "192.0.2.2", family="ipv4", direct="192.0.2.2")
    run_case("IPv4 WAN fallback", "192.0.2.2", family="ipv4")
    run_case(
        "PD excludes other WAN outside /56",
        "2001:db8:abcd:ab::1",
        delegated="2001:db8:abcd:ab::1",
        prefix="2001:db8:abcd::/56",
        competing="2001:db8:abcd:100::1",
    )
    for subnet in ("1200", "127f", "12ff"):
        address = "2001:db8:abcd:" + subnet + "::1"
        run_case(
            "PD /56 subnet " + subnet, address, delegated=address, prefix="2001:db8:abcd:1200::/56"
        )
    run_case(
        "PD /60 non-hextet boundary",
        "2001:db8:abcd:123a::1",
        delegated="2001:db8:abcd:123a::1",
        prefix="2001:db8:abcd:1230::/60",
    )
    run_case(
        "PD /62 non-nibble boundary",
        "2001:db8:abcd:1237::1",
        delegated="2001:db8:abcd:1237::1",
        prefix="2001:db8:abcd:1234::/62",
    )
    run_case(
        "PD uppercase prefix",
        "2001:db8:abcd:1::1",
        delegated="2001:db8:abcd:1::1",
        prefix="2001:DB8:ABCD::/48",
    )
    run_case(
        "PD uncompressed prefix",
        "2001:db8:abcd::1234",
        delegated="2001:db8:abcd::1234",
        prefix="2001:0db8:abcd:0000::/64",
    )
    report = {
        "passed": True,
        "shell": args.shell,
        "sed": str(sed),
        "ip_command": str(ip_command),
        "cases": results,
        "assertions": count,
        "kernel": os.uname().release,
        "netifd_accessors": "fixtures",
        "namespace_isolated": True,
    }
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"PASS: {count} real-kernel assertions, {len(results)} source-selection cases ({args.shell})"
    )
    tools.cleanup()


if __name__ == "__main__":
    main()
