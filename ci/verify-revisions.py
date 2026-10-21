#!/usr/bin/env python3
"""Validate two rebased mwan3 fixes against the actual merged rpcd baseline."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys

p = argparse.ArgumentParser()
p.add_argument('--manifest', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--source', type=Path)
p.add_argument('--local-root', type=Path)
p.add_argument('--ash', required=True)
a = p.parse_args()
out = a.output.resolve()
out.mkdir(parents=True, exist_ok=True)
manifest = json.loads(a.manifest.read_text())
runner = Path(__file__).with_name('run-entry.py')
sha = lambda data: hashlib.sha256(data).hexdigest()
def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])

proofs = []
for c in manifest:
    if a.local_root:
        root = (a.local_root / c['slug']).resolve()
    else:
        root = a.source.resolve()
        git(root, 'fetch', '--depth=1', 'origin', c['base'], c['old'], c['old_base'])
        bundle = out / (c['slug'] + '.bundle')
        bundle.write_bytes(base64.b64decode((a.manifest.parent / 'bundles' /
            (c['slug'] + '.b64')).read_text()))
        assert sha(bundle.read_bytes()) == c['bundle_sha256']
        git(root, 'bundle', 'verify', str(bundle))
        git(root, 'fetch', str(bundle), 'HEAD')
        git(root, 'checkout', '--detach', c['candidate'])
    assert git(root, 'rev-parse', 'HEAD').decode().strip() == c['candidate']
    assert git(root, 'rev-parse', 'HEAD^').decode().strip() == c['base']
    assert c['base'] == '2a5d2301904f84ff61615dd4e4d1db6d4f1fbf67'
    assert git(root, 'show', '-s', '--format=%an <%ae>').decode().strip() == 'Yuen Su <zypulsecore@gmail.com>'
    assert git(root, 'show', '-s', '--format=%an <%ae> %aI', c['old']) == git(root, 'show', '-s', '--format=%an <%ae> %aI')
    assert git(root, 'show', '-s', '--format=%b', c['old']) == git(root, 'show', '-s', '--format=%b')
    assert 'Signed-off-by: Yuen Su <zypulsecore@gmail.com>' in git(root, 'show', '-s', '--format=%B').decode()
    assert '2.12.4' in git(root, 'show', '-s', '--format=%s').decode()
    git(root, 'diff', '--check', c['base'], c['candidate'])
    names = git(root, 'diff', '--name-only', c['base'], c['candidate']).decode().splitlines()
    assert names == c['upstream_files']
    make = (root / 'net/mwan3/Makefile').read_bytes()
    assert make == git(root, 'show', c['base'] + ':net/mwan3/Makefile').replace(
        b'PKG_VERSION:=2.12.3\nPKG_RELEASE:=1\n', b'PKG_VERSION:=2.12.4\nPKG_RELEASE:=1\n')
    assert b'PKG_VERSION:=2.12.4\nPKG_RELEASE:=1\n' in make
    common = 'net/mwan3/files/lib/mwan3/common.sh'
    assert git(root, 'show', c['old_base'] + ':' + common) == git(root, 'show', c['base'] + ':' + common)
    assert (root / common).read_bytes() == git(root, 'show', c['old'] + ':' + common)
    old_base_test = git(root, 'show', c['old_base'] + ':net/mwan3/test.sh')
    old_test = git(root, 'show', c['old'] + ':net/mwan3/test.sh')
    assert old_test.startswith(old_base_test)
    old_suffix = old_test[len(old_base_test):].lstrip(b'\n')
    base_test = git(root, 'show', c['base'] + ':net/mwan3/test.sh')
    assert (root / 'net/mwan3/test.sh').read_bytes() == base_test + b'\n' + old_suffix
    preserved = {}
    for name in ['net/mwan3/test-rpcd.uc', 'net/mwan3/files/usr/share/rpcd/ucode/mwan3']:
        data = (root / name).read_bytes()
        assert data == git(root, 'show', c['base'] + ':' + name)
        preserved[name] = sha(data)
    report = out / c['slug']
    report.mkdir(exist_ok=True)
    original_common = report / 'original-common.sh'
    original_common.write_bytes(git(root, 'show', c['base'] + ':' + common))
    (report / 'previous-test.sh').write_bytes(old_test)
    hashes = {}
    for name in names + list(preserved):
        data = (root / name).read_bytes()
        dest = report / 'tested-source' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        hashes[name] = sha(data)
    marker = 'failed to normalize nexthop route:' if c['slug'] == 'mwan3-nexthop' else 'failed delegated source for '
    logs = []
    for shell in ('/bin/bash', a.ash):
        for original in (True, False):
            name = ('original-' if original else 'candidate-') + Path(shell).name + '.log'
            cmd = [sys.executable, str(runner), '--source', str(root), '--shell', shell]
            if original:
                cmd += ['--common', str(original_common), '--expected-failure', marker]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
            (report / name).write_text(r.stdout + r.stderr)
            assert r.returncode == 0, (name, r.stdout, r.stderr)
            assert r.stdout.count('get_int(') == 7, 'Merged rpcd cases must execute before parser/PD checks'
            logs.append(name)
        for name in ('net/mwan3/test.sh', common):
            subprocess.run([shell, '-n', str(root / name)], check=True)
    proof = dict(slug=c['slug'], old=c['old'], candidate=c['candidate'], base=c['base'],
        upstream_files=names, package_version='2.12.4', package_release=1,
        own_common_blob_preserved=sha((root / common).read_bytes()),
        upstream_common_unchanged_before_applying_fix=True,
        merged_rpcd_files_preserved=preserved, upstream_test_prefix_preserved=True,
        own_test_suffix_sha256=sha(old_suffix), previous_test_sha256=sha(old_test),
        tested_source_sha256=hashes, entry_logs=logs,
        merged_rpcd_cases_per_entry=7, kernel_or_sdk_rerun=False)
    (report / 'verification.json').write_text(json.dumps(proof, indent=2) + '\n')
    proofs.append(proof)
(out / 'verification.json').write_text(json.dumps(proofs, indent=2) + '\n')
print('Verified two rebased fixes, merged rpcd tests, preserved own regressions and exact original failures.')

