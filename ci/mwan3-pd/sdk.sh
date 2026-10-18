#!/bin/bash
set -euo pipefail
source_dir=$(realpath "$1")
evidence_dir=$2
mkdir -p "$evidence_dir"
curl --fail --location --retry 3 https://downloads.openwrt.org/releases/25.12.0/targets/qualcommax/ipq60xx/openwrt-sdk-25.12.0-qualcommax-ipq60xx_gcc-14.3.0_musl.Linux-x86_64.tar.zst -o sdk.tar.zst
echo '068b9f70a19fa95383be5778cdea5aeeef4ede61022de9e596f14bd4f30a4225  sdk.tar.zst' | sha256sum -c -
mkdir sdk
tar --zstd -xf sdk.tar.zst --strip-components=1 -C sdk
cp -a "$source_dir/net/mwan3" sdk/package/
make -C sdk defconfig > "$evidence_dir/sdk-defconfig.log" 2>&1
# This is a package-only SDK build. Missing runtime dependency recipes are
# reported by OpenWrt; neither a firmware image nor full dependency install is claimed.
make -C sdk package/mwan3/compile V=s -j4 > "$evidence_dir/sdk-compile.log" 2>&1
mapfile -t apks < <(find sdk/bin/packages -name 'mwan3-2.12.2-r3.apk')
test "${#apks[@]}" = 1
mkdir "$evidence_dir/extracted"
sdk/staging_dir/host/bin/apk extract --allow-untrusted --no-chown --destination "$evidence_dir/extracted" "${apks[0]}"
cmp "$source_dir/net/mwan3/files/lib/mwan3/common.sh" "$evidence_dir/extracted/lib/mwan3/common.sh"
readelf -h "$evidence_dir/extracted/lib/mwan3/libwrap_mwan3_sockopt.so.1.0" | tee "$evidence_dir/elf.txt"
grep -F AArch64 "$evidence_dir/elf.txt"
sudo env MWAN3_TEST_PARENT_NETNS="$(readlink /proc/self/ns/net)" unshare --net python3 "$source_dir/net/mwan3/tests/test-ipv6-pd-source.py" --common "$evidence_dir/extracted/lib/mwan3/common.sh" --shell 'busybox ash' --sed "$(command -v busybox)" --report "$evidence_dir/packaged-source.json" | tee "$evidence_dir/packaged-source.log"
cp "${apks[0]}" "$evidence_dir/"
(cd "$evidence_dir" && sha256sum mwan3-*.apk > APK-SHA256SUMS)
