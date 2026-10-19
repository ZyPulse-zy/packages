#!/bin/sh

# shellcheck shell=busybox

IPKG_INSTROOT=${IPKG_INSTROOT:-}
export IPKG_INSTROOT

. /lib/functions.sh
. /lib/mwan3/mwan3.sh

set -e

mwan3_init

sanitize_route()
{
	printf '%s\n' "$1" | sed -ne "$MWAN3_ROUTE_LINE_EXP"
}

expected='192.0.2.0/24 dev eth0 scope link src 192.0.2.1 '

for flags in 'dead linkdown' 'linkdown dead' 'dead' 'linkdown'; do
	actual=$(sanitize_route "$expected$flags")
	[ "$actual" = "$expected" ] || {
		echo "failed to sanitize route flags: $flags" >&2
		echo "expected: '$expected'" >&2
		echo "actual:   '$actual'" >&2
		exit 1
	}
done

ipv6_route='fd00:1::/64 via fe80::dead dev eth0 proto static metric 1024 pref medium'
actual=$(sanitize_route "$ipv6_route")
[ "$actual" = "$ipv6_route" ] || {
	echo "failed to preserve an IPv6 address ending in dead" >&2
	echo "expected: '$ipv6_route'" >&2
	echo "actual:   '$actual'" >&2
	exit 1
}

# Exercise the installed integer helpers with empty and numeric state files.
ucode -S - <<'EOF'
import { readfile } from 'fs';

const source = readfile('/usr/share/rpcd/ucode/mwan3');
const begin = index(source, 'function get_str_raw(');
const end = index(source, 'function get_uptime(');
if (begin < 0 || end <= begin)
	die('Cannot find mwan3 integer helpers\n');
const read_integer = loadstring(substr(source, begin, end - begin) +
	'\nreturn get_int("wan", "SCORE");', { raw_mode: true });

for (let sample in [['', 0], [null, 0], ['\n', 0], ['invalid', 0],
	['0', 0], ['17', 17], ['17\n', 17]]) {
	const actual = call(read_integer, null, { readfile: () => sample[0] });
	if (actual !== sample[1])
		die(sprintf('get_int(%J): expected %d, got %J\n', sample[0], sample[1], actual));
	printf('get_int(%J): %d\n', sample[0], actual);
}
EOF
