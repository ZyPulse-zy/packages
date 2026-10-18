The normal package `test.sh` includes six lightweight delegated-source checks.
They require no Python, root or interface changes. Fixture netifd accessors and
an ip command check that the complete delegated CIDR is passed to ip, while
the actual source function and sed preserve the selected IPv6 digits.

`test-ipv6-pd-source.py` is an additional Linux integration test. It needs
Python 3, root, iproute2 and a private network namespace; it is not run on the
target by package `test.sh`. It uses actual assigned addresses and UDP binds,
with only netifd accessors replaced. This independently verifies kernel CIDR
selection, including non-hextet prefix lengths and competing upstreams.

```sh
sudo env MWAN3_TEST_PARENT_NETNS="$(readlink /proc/self/ns/net)" \
  unshare --net python3 net/mwan3/tests/test-ipv6-pd-source.py \
    --common net/mwan3/files/lib/mwan3/common.sh --report /tmp/pd-source.json
```

Use `--shell 'bash'` for Bash or `--shell 'busybox ash'` for BusyBox ash. The
`--ip` and `--sed` options select explicit tool paths, including a target ip
wrapper under QEMU. Shell, setup and bind checks remain native in that mode.
Never run the integration test in the caller's network namespace: its guard
requires a different namespace ID and refuses root without that evidence.
