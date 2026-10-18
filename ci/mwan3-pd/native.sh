#!/bin/bash
set -euo pipefail
source_dir=$(realpath "$1")
evidence_dir=$2
mkdir -p "$evidence_dir"
test_file="$source_dir/net/mwan3/tests/test-ipv6-pd-source.py"
common="$source_dir/net/mwan3/files/lib/mwan3/common.sh"
git -C "$source_dir" fetch origin 3d6229131a40fc3417ad2d8bd9d4050458c4fd95
git -C "$source_dir" show 3d6229131a40fc3417ad2d8bd9d4050458c4fd95:net/mwan3/files/lib/mwan3/common.sh > "$evidence_dir/original-common.sh"
if python3 "$test_file" --common "$common" > "$evidence_dir/guard.log" 2>&1; then
  echo 'Isolation guard unexpectedly accepted caller namespace'; exit 1
fi
grep -F 'Refusing test outside' "$evidence_dir/guard.log"
for variant in busybox bash; do
  if [ "$variant" = busybox ]; then
    shell='busybox ash'; sed=$(command -v busybox); ip_tool=$(command -v busybox)
  else
    shell=bash; sed=$(command -v sed); ip_tool=$(command -v ip)
  fi
  if sudo env MWAN3_TEST_PARENT_NETNS="$(readlink /proc/self/ns/net)" unshare --net python3 "$test_file" --common "$evidence_dir/original-common.sh" --shell "$shell" --sed "$sed" --ip "$ip_tool" > "$evidence_dir/original-$variant.log" 2>&1; then
    echo 'Original source must fail the delegated-address assertion'; exit 1
  fi
  cat "$evidence_dir/original-$variant.log"
  grep -F 'PD suffix digit 7:' "$evidence_dir/original-$variant.log"
  grep -F 'Cannot assign requested address' "$evidence_dir/original-$variant.log"
  sudo env MWAN3_TEST_PARENT_NETNS="$(readlink /proc/self/ns/net)" unshare --net python3 "$test_file" --common "$common" --shell "$shell" --sed "$sed" --ip "$ip_tool" --report "$evidence_dir/fixed-$variant.json" | tee "$evidence_dir/fixed-$variant.log"
done
# The first digit-only fix is insufficient for a non-hextet prefix.
git -C "$source_dir" fetch origin 468595cd094d718323fa2f2bb050c80ab940239e
git -C "$source_dir" show 468595cd094d718323fa2f2bb050c80ab940239e:net/mwan3/files/lib/mwan3/common.sh > "$evidence_dir/digit-only-common.sh"
if sudo env MWAN3_TEST_PARENT_NETNS="$(readlink /proc/self/ns/net)" unshare --net python3 "$test_file" --common "$evidence_dir/digit-only-common.sh" --shell 'busybox ash' --sed "$(command -v busybox)" --ip "$(command -v busybox)" > "$evidence_dir/digit-only-cidr-failure.log" 2>&1; then
  echo 'Expected the exact out-of-prefix WAN selection failure'; exit 1
fi
grep -F 'PD excludes other WAN outside /56:' "$evidence_dir/digit-only-cidr-failure.log"
grep -F "got '2001:db8:abcd:100::1'" "$evidence_dir/digit-only-cidr-failure.log"

# Exercise the exact newly added section of the package test.sh; the upstream
# package runner also executes the existing initialization/route checks.
python3 - "$source_dir/net/mwan3/test.sh" "$evidence_dir/lightweight.sh" <<'PYTEST'
from pathlib import Path
import sys
marker='# Delegated-source regression:'
text=Path(sys.argv[1]).read_text();assert text.count(marker)==1
Path(sys.argv[2]).write_text(marker+text.split(marker,1)[1])
PYTEST
for variant in bash busybox; do
  if [ "$variant" = bash ]; then shell=(bash); else shell=(busybox ash); fi
  "${shell[@]}" -c 'set +e; . "$1"; set -e; . "$2"' sh "$common" "$evidence_dir/lightweight.sh" > "$evidence_dir/lightweight-$variant.log" 2>&1
  if "${shell[@]}" -c 'set +e; . "$1"; set -e; . "$2"' sh "$evidence_dir/original-common.sh" "$evidence_dir/lightweight.sh" > "$evidence_dir/lightweight-original-$variant.log" 2>&1; then echo 'Original passed lightweight regression'; exit 1; fi
  grep -F 'failed delegated source' "$evidence_dir/lightweight-original-$variant.log"
done
printf '%s\n' 'Six package test.sh cases passed per shell; original rejected' > "$evidence_dir/lightweight-summary.txt"
