# Expanded nexthop route regression

The normal package `test.sh` runs five lightweight nexthop dump checks alongside
the existing flag checks. They use the real normalization expression and need
no Python, root or routing-table changes.

The Python suite below is an additional Linux integration test, separate from
target package `test.sh`; it is not an extra runtime dependency of mwan3.

`test-nexthop-routes.py` sources the actual common functions and keeps the
complete actual route-copy and monitor-handler function bodies. It replaces
only OpenWrt imports, daemon startup and external UCI/network/ipset status I/O.
Route creation, copying, replacement, readback and deletion use real iproute2
and kernel FIB tables. No packets are generated.

```sh
MWAN_TEST_PARENT_NETNS="$(readlink /proc/self/ns/net)" unshare -Urn \
  python3 net/mwan3/tests/test-nexthop-routes.py --shell /absolute/path/to/ash
```

`--installed-root /path/to/extracted-apk` tests the installed shell sources
instead of the checkout. No package is installed on the host.

Requires Python 3, iproute2 with nexthop support, and BusyBox ash or bash.
The executable path must retain the `ash` name when it is a BusyBox symlink.
All route writes occur in a distinct network namespace; the guard refuses to
run without the supplied, different parent ID. On systems restricting user
namespaces, run with `sudo env` and `unshare -n` instead of relaxing the policy.

The 45 checks cover IPv4/IPv6 single nexthops, startup synchronization, ifup,
monitor-handler updates/deletion, source/protocol/metric/onlink preservation,
WAN-specific versus shared routes, valid ID-only replay, and the existing
legacy flag/IPv6 suffix checks. The original
implementation fails actual route installation with “Nexthop specification and
nexthop id are mutually exclusive”; normalized expanded routes install.

The kernel's [nexthop compatibility mode](https://docs.kernel.org/networking/ip-sysctl.html)
includes expanded nexthops and emits route notifications for nexthop changes.
Retaining the expanded inline route is compatible with mwan3's existing route
monitor. ID-only routes are left intact. This regression does not establish
multiline/multipath-group, resilient-group or encapsulated-route support, and
does not exercise the running daemon loop or production failover traffic.

The test adapter explicitly uses the host iproute2 binary for setup and replay
because some host BusyBox ash builds otherwise select their bundled ip applet.
BusyBox/GNU sed still evaluate the actual normalization expression. Cleanup
deletes private rows by prefix: this host's iproute2 flush separately rejects
an ID-only route's expanded dump, which is not hidden as a product test pass.
