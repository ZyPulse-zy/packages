#!/usr/bin/env python3
"""Limit SDK APK packaging to the actual conntrack dependency closure.

The SDK freezes hidden PACKAGE_kmod defaults. Command-line package selectors
avoid repackaging unrelated precompiled modules; kernel ABI inputs stay intact.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--sdk", type=Path, required=True)
p.add_argument("--report", type=Path, required=True)
p.add_argument("--log", type=Path, required=True)
a = p.parse_args()
sdk = a.sdk.resolve()
packages = {}
name = None
for line in (sdk / "tmp/.packageinfo").read_text().splitlines():
    if line.startswith("Package: "):
        name = line.removeprefix("Package: ")
        packages[name] = []
    elif name and line.startswith("Depends: "):
        packages[name] = re.findall(r"\+([a-z][a-z0-9+_.-]*)", line)
closure = {"conntrack", "conntrackd"}
while True:
    extra = {dep for package in closure for dep in packages.get(package, [])} - closure
    if not extra:
        break
    closure.update(extra)
needed = sorted(name for name in closure if name.startswith("kmod-"))
all_kmods = re.findall(r"^CONFIG_PACKAGE_(kmod-[^=]+)=[my]$", (sdk / ".config").read_text(), re.M)
assert needed and set(needed).issubset(all_kmods), needed
kernel = list(sdk.glob("build_dir/target-*/linux-qualcommax_ipq60xx/linux-*/.config"))
assert len(kernel) == 1
inputs = [kernel[0], kernel[0].parent / "Module.symvers"]
digest = lambda f: hashlib.sha256(f.read_bytes()).hexdigest()
original = {str(file): digest(file) for file in inputs}
command = ["make", "-C", str(sdk), "package/conntrack-tools/compile", "V=s", "-j4"]
command += ["CONFIG_PACKAGE_" + name + ("=m" if name in needed else "=") for name in all_kmods]
report = {"passed": False, "required_kernel_packages": needed,
          "disabled_unrelated_package_selectors": len(all_kmods) - len(needed),
          "kernel_abi_inputs_before": original, "kernel_source_modified": False}
try:
    with a.log.open("w") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=900)
    report["exit"] = result.returncode
    report["kernel_abi_inputs_unchanged"] = original == {str(file): digest(file) for file in inputs}
    result.check_returncode()
    assert report["kernel_abi_inputs_unchanged"]
    report["passed"] = True
finally:
    a.report.write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
