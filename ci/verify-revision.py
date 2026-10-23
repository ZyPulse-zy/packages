#!/usr/bin/env python3
"""Verify the existing nexthop fix on the merged PD/rpcd baseline."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

p = argparse.ArgumentParser()
p.add_argument('--manifest', type=Path, required=True)
p.add_argument('--source', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--ash', required=True)
p.add_argument('--local', action='store_true')
a = p.parse_args()
root = a.source.resolve()
out = a.output.resolve()
out.mkdir(parents=True, exist_ok=True)
c, = json.loads(a.manifest.read_text())
sha = lambda b: hashlib.sha256(b).hexdigest()
def git(*args):
    return subprocess.check_output(['git', '-C', str(root), *args])
bundle = out / 'mwan3-nexthop.bundle'
bundle.write_bytes(base64.b64decode((a.manifest.parent / 'bundles/mwan3-nexthop.b64').read_text()))
assert sha(bundle.read_bytes()) == c['bundle_sha256']
if not a.local:
    git('fetch', '--depth=1', 'origin', c['base'], c['old'], c['old_base'])
git('bundle', 'verify', str(bundle))
if not a.local:
    git('fetch', str(bundle), 'HEAD')
    git('checkout', '--detach', c['candidate'])
assert git('rev-parse', 'HEAD').decode().strip() == c['candidate']
assert git('rev-parse', 'HEAD^').decode().strip() == c['base']
assert c['base'] == '770d4058b06f09d4c16321ca53401454f148095e'
assert git('show', '-s', '--format=%an <%ae>').decode().strip() == 'Yuen Su <zypulsecore@gmail.com>'
assert git('show', '-s', '--format=%an <%ae> %aI', c['old']) == git('show', '-s', '--format=%an <%ae> %aI')
assert git('show', '-s', '--format=%b', c['old']) == git('show', '-s', '--format=%b')
assert git('show', '-s', '--format=%s').decode().strip() == 'mwan3: normalize expanded nexthop routes in 2.12.5'
assert 'Signed-off-by: Yuen Su <zypulsecore@gmail.com>' in git('show', '-s', '--format=%B').decode()
git('diff', '--check', c['base'], c['candidate'])
names = git('diff', '--name-only', c['base'], c['candidate']).decode().splitlines()
assert names == c['upstream_files']
make = 'net/mwan3/Makefile'
before = git('show', c['base'] + ':' + make)
assert before.count(b'PKG_VERSION:=2.12.4\nPKG_RELEASE:=1\n') == 1
assert (root / make).read_bytes() == before.replace(b'PKG_VERSION:=2.12.4\nPKG_RELEASE:=1\n', b'PKG_VERSION:=2.12.5\nPKG_RELEASE:=1\n')
common = 'net/mwan3/files/lib/mwan3/common.sh'
base_common = git('show', c['base'] + ':' + common)
own_patch = git('diff', '--binary', '--full-index', c['old_base'], c['old'], '--', common)
assert git('diff', '--numstat', c['old_base'], c['old'], '--', common).decode().split('\t')[:2] == ['3', '0']
with tempfile.TemporaryDirectory(prefix='mwan-replay-') as directory:
    target = Path(directory) / common
    target.parent.mkdir(parents=True)
    target.write_bytes(base_common)
    subprocess.run(['git', 'apply', '--check', '-'], cwd=directory, input=own_patch, check=True)
    subprocess.run(['git', 'apply', '-'], cwd=directory, input=own_patch, check=True)
    assert target.read_bytes() == (root / common).read_bytes()
old_base_test = git('show', c['old_base'] + ':net/mwan3/test.sh')
old_test = git('show', c['old'] + ':net/mwan3/test.sh')
assert old_test.startswith(old_base_test)
own_suffix = old_test[len(old_base_test):].lstrip(b'\n')
base_test = git('show', c['base'] + ':net/mwan3/test.sh')
assert (root / 'net/mwan3/test.sh').read_bytes() == base_test + b'\n' + own_suffix
preserved = {}
for name in ['net/mwan3/test-rpcd.uc', 'net/mwan3/files/usr/share/rpcd/ucode/mwan3']:
    b = (root / name).read_bytes()
    assert b == git('show', c['base'] + ':' + name)
    preserved[name] = sha(b)
report = out / c['slug']
report.mkdir(exist_ok=True)
(report / 'original-common.sh').write_bytes(base_common)
(report / 'previous-test.sh').write_bytes(old_test)
(report / 'upstream-test.sh').write_bytes(base_test)
(report / 'own-common.patch').write_bytes(own_patch)
hashes = {}
for name in names + list(preserved):
    b = (root / name).read_bytes()
    dest = report / 'tested-source' / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b)
    hashes[name] = sha(b)
runner = Path(__file__).with_name('run-entry.py')
logs = []
execution = []
for shell in ('/bin/bash', a.ash):
    for original in (True, False):
        name = ('original-' if original else 'candidate-') + Path(shell).name + '.log'
        cmd = [sys.executable, str(runner), '--source', str(root), '--shell', shell, '--trace']
        if original:
            cmd += ['--common', str(report / 'original-common.sh'), '--expected-failure', 'failed to normalize nexthop route:']
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        text = r.stdout + r.stderr
        (report / name).write_text(text)
        assert r.returncode == 0, (name, text)
        rpcd = re.findall(r'^get_int\(', text, re.M)
        pd = re.findall(r'^\++\s+mwan3_get_src_ip actual wan$', text, re.M)
        nexthop = re.findall(r'^\++\s+check_nexthop_route\b', text, re.M)
        assert len(rpcd) == 7 and len(pd) == 6 and len(nexthop) == (1 if original else 5)
        rpcd_call = re.search(r'^\++\s+ucode -S ', text, re.M)
        first_pd = re.search(r'^\++\s+mwan3_get_src_ip actual wan$', text, re.M)
        first_nexthop = re.search(r'^\++\s+check_nexthop_route\b', text, re.M)
        assert rpcd_call.start() < first_pd.start() < first_nexthop.start()
        logs.append(name)
        execution.append(dict(log=name,rpcd=7,pd=6,nexthop=len(nexthop),original=original,merged_tests_run_first=True))
    for name in ['net/mwan3/test.sh', common]:
        subprocess.run([shell, '-n', str(root / name)], check=True)
proof = dict(slug=c['slug'],old=c['old'],candidate=c['candidate'],base=c['base'],
    upstream_files=names,package_version='2.12.5',package_release=1,
    old_common_patch_replayed_on_current_base=True,old_common_patch_sha256=sha(own_patch),
    all_other_upstream_common_bytes_preserved=True,merged_rpcd_files_preserved=preserved,
    upstream_test_prefix_preserved=True,upstream_test_sha256=sha(base_test),
    own_test_suffix_sha256=sha(own_suffix),previous_test_sha256=sha(old_test),
    tested_source_sha256=hashes,entry_logs=logs,executions=execution,
    merged_rpcd_cases_per_entry=7,merged_pd_cases_per_entry=6,kernel_or_sdk_rerun=False)
(report / 'verification.json').write_text(json.dumps(proof, indent=2) + '\n')
(out / 'verification.json').write_text(json.dumps([proof], indent=2) + '\n')
print('Verified exact nexthop fix on current base; both shells execute rpcd7, PD6, own cases, and accurate original failure.')
