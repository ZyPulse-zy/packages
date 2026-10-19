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

# The kernel's expanded nexthop dump must be replayable without nhid.
# These parser checks require no extra tools or changes to the routing tables.
check_nexthop_route()
{
	actual=$(sanitize_route "$1")
	[ "$actual" = "$2" ] || {
		echo "failed to normalize nexthop route: '$1'" >&2
		echo "expected: '$2'" >&2
		echo "actual:   '$actual'" >&2
		exit 1
	}
}

check_nexthop_route \
	'198.51.100.0/24 nhid 7 via 192.0.2.1 dev eth0 proto static metric 20' \
	'198.51.100.0/24 via 192.0.2.1 dev eth0 proto static metric 20'
check_nexthop_route \
	'2001:db8:2::/64 nhid 8 via fe80::1 dev eth0 proto static metric 30 pref medium' \
	'2001:db8:2::/64 via fe80::1 dev eth0 proto static metric 30 pref medium'
check_nexthop_route '192.0.2.0/24 nhid 9 dev eth0 scope link' \
	'192.0.2.0/24 dev eth0 scope link'
check_nexthop_route '198.51.100.0/24 nhid 7 proto static metric 20' \
	'198.51.100.0/24 nhid 7 proto static metric 20'
check_nexthop_route '198.51.100.0/24 via 192.0.2.1 dev eth0 proto static metric 20' \
	'198.51.100.0/24 via 192.0.2.1 dev eth0 proto static metric 20'
