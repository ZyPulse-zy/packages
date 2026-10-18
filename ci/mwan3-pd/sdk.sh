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
sudo env MWAN3_TEST_PARENT_NETNS="$(readlink /proc/self/ns/net)" unshare --net python3 "$source_dir/net/mwan3/tests/test-ipv6-pd-source.py" --common "$evidence_dir/extracted/lib/mwan3/common.sh" --shell 'busybox ash' --sed "$(command -v busybox)" --ip "$(command -v busybox)" --report "$evidence_dir/packaged-source.json" | tee "$evidence_dir/packaged-source.log"
cp "${apks[0]}" "$evidence_dir/"
(cd "$evidence_dir" && sha256sum mwan3-*.apk > APK-SHA256SUMS)

# Exercise the official target ip-tiny, not a native applet with the same name.
arm_root="$evidence_dir/arm-root"
mkdir -p "$arm_root/lib"
cp -L sdk/staging_dir/toolchain-aarch64_cortex-a53_gcc-14.3.0_musl/lib/libc.so "$arm_root/lib/libc.so"
ln -s libc.so "$arm_root/lib/ld-musl-aarch64.so.1"
cp -L sdk/staging_dir/toolchain-aarch64_cortex-a53_gcc-14.3.0_musl/lib/libgcc_s.so.1 "$arm_root/lib/libgcc_s.so.1"
curl --fail --location --retry 3 --connect-timeout 15 --max-time 120 https://downloads.openwrt.org/releases/25.12.0/packages/aarch64_cortex-a53/base/ip-tiny-6.18.0-r2.apk -o "$evidence_dir/ip-tiny.apk"
curl --fail --location --retry 3 --connect-timeout 15 --max-time 120 https://downloads.openwrt.org/releases/25.12.0/packages/aarch64_cortex-a53/base/libmnl0-1.0.5-r1.apk -o "$evidence_dir/libmnl.apk"
echo '57bb04438e112c1bca0b46f6837b4955d1c73ee1a429915a8d70aa750c688f65  '"$evidence_dir/ip-tiny.apk" | sha256sum -c -
echo 'e6c123fa136c628c50fcfdc98fbc3407786ec37db1a605341c0388568eadfd63  '"$evidence_dir/libmnl.apk" | sha256sum -c -
sdk/staging_dir/host/bin/apk extract --allow-untrusted --no-chown --destination "$arm_root" "$evidence_dir/ip-tiny.apk" "$evidence_dir/libmnl.apk"
cat > "$evidence_dir/ip-arm" <<ARM
#!/bin/sh
exec qemu-aarch64 -L "$arm_root" "$arm_root/usr/libexec/ip-tiny" "\$@"
ARM
chmod +x "$evidence_dir/ip-arm"
sudo env MWAN3_TEST_PARENT_NETNS="$(readlink /proc/self/ns/net)" unshare --net strace -f -s 300 -e trace=execve -o "$evidence_dir/target-exec.log" python3 "$source_dir/net/mwan3/tests/test-ipv6-pd-source.py" --common "$evidence_dir/extracted/lib/mwan3/common.sh" --shell 'busybox ash' --sed "$(command -v busybox)" --ip "$evidence_dir/ip-arm" --report "$evidence_dir/arm-ip-tiny.json" | tee "$evidence_dir/arm-ip-tiny.log"
python3 - "$evidence_dir" <<'PYCODE'
from pathlib import Path
import json,sys
root=Path(sys.argv[1]);text=(root/'target-exec.log').read_text();lines=[line for line in text.splitlines() if 'execve(' in line and 'qemu-aarch64' in line and 'usr/libexec/ip-tiny' in line and '= 0' in line];assert len(lines)==20,len(lines);(root/'target-exec-verification.json').write_text(json.dumps({'actual_target_executions':len(lines),'passed':True,'scope':'native shell/sed and setup; real AArch64/musl ip-tiny queries under QEMU against isolated host kernel'},indent=2)+'\n');print('Verified target ip-tiny executions:',len(lines))
PYCODE
