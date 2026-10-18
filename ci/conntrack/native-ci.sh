#!/bin/bash
set -euo pipefail
packages=$(realpath "$1")
out=$(realpath -m "$2")
variant=$3
control=$(cd -- "$(dirname -- "$0")" && pwd)
mkdir -p "$out"
exec > >(tee "$out/driver.log") 2>&1

if [ "$variant" = mainline ]; then
  curl --fail --location --retry 3 https://netfilter.org/pub/libnetfilter_conntrack/libnetfilter_conntrack-1.1.1.tar.xz -o "$out/library.tar.xz"
  echo '769d3eaf57fa4fbdb05dd12873b6cb9a5be7844d8937e222b647381d44284820  '"$out/library.tar.xz" | sha256sum -c -
  mkdir "$out/library"
  tar -xJf "$out/library.tar.xz" --strip-components=1 -C "$out/library"
  (cd "$out/library"; ./configure --prefix="$out/prefix" --disable-static; make -j4; make install)
  export PKG_CONFIG_PATH="$out/prefix/lib/pkgconfig"
  export LD_LIBRARY_PATH="$out/prefix/lib"
  git clone --no-checkout https://repo.or.cz/conntrack-tools.git "$out/original"
  git -C "$out/original" checkout --detach ab445258e803106c71c1cbee6580f31c67c3124e
else
  curl --fail --location --retry 3 https://netfilter.org/pub/conntrack-tools/conntrack-tools-1.4.8.tar.xz -o "$out/release.tar.xz"
  echo '067677f4c5f6564819e78ed3a9d4a8980935ea9273f3abb22a420ea30ab5ded6  '"$out/release.tar.xz" | sha256sum -c -
  mkdir "$out/original"
  tar -xJf "$out/release.tar.xz" --strip-components=1 -C "$out/original"
fi
cp -a "$out/original" "$out/fixed"
patch --batch --fuzz=0 -p1 -d "$out/fixed" -i "$packages/net/conntrack-tools/patches/100-conntrack-event-socket-buffer.patch"
python3 - "$control/source-pins.json" "$out" "$variant" <<'PY'
import hashlib,json,pathlib,sys
pins=json.loads(pathlib.Path(sys.argv[1]).read_text())[sys.argv[3]]
for name in ('original','fixed'):
 p=pathlib.Path(sys.argv[2])/name/'src/conntrack.c'
 assert hashlib.sha256(p.read_bytes()).hexdigest()==pins[name],name
print('Complete original/fixed conntrack.c hashes match pinned source')
PY
for mode in original fixed; do
  if [ ! -f "$out/$mode/configure" ]; then (cd "$out/$mode"; autoreconf -fi); fi
  mkdir "$out/build-$mode"
  (cd "$out/build-$mode"; "$out/$mode/configure" --disable-cthelper --disable-cttimeout)
  make -C "$out/build-$mode/extensions" -j4
  make -C "$out/build-$mode/src" conntrack -j4
done

test_script="$control/test-event-buffer.py"
parent_netns=$(readlink /proc/self/ns/net)
run_case() {
  sudo env LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}" CONNTRACK_TEST_PARENT_NETNS="$parent_netns" \
    unshare --net python3 "$test_script" "$1" --report "$2"
}
if run_case "$out/build-original/src/conntrack" "$out/original-failure.json"; then
  echo 'Original unexpectedly passed' >&2
  exit 1
fi
python3 - "$out/original-failure.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]));assert not r['passed']
assert r['failure']=='--buffer-size changed the query socket instead of the event listener'
PY
run_case "$out/build-fixed/src/conntrack" "$out/fixed-result.json"

# Preserve the upstream CLI test runner's expected relative binary path.
ln -s "$out/build-fixed/src/conntrack" "$out/fixed/src/conntrack"
make -C "$out/fixed/tests/conntrack" test
sudo env LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}" unshare --net \
  sh -c 'cd "$1"; exec ./test' sh "$out/fixed/tests/conntrack" | tee "$out/upstream-cli.log"

mkdir "$out/build-sanitize"
(cd "$out/build-sanitize"; CFLAGS='-O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined -fno-pie' LDFLAGS='-fsanitize=address,undefined -no-pie' "$out/fixed/configure" --disable-cthelper --disable-cttimeout)
make -C "$out/build-sanitize/extensions" -j4
make -C "$out/build-sanitize/src" conntrack -j4
# LSan cannot operate under ptrace; ASan and UBSan remain enabled.
sudo env ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}" CONNTRACK_TEST_PARENT_NETNS="$parent_netns" \
  unshare --net python3 "$test_script" "$out/build-sanitize/src/conntrack" --report "$out/asan-ubsan-lsan-disabled.json"
