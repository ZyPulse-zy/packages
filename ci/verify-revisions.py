#!/usr/bin/env python3
"""Verify maintainer-requested metadata/test revisions, independent of upstream."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

p = argparse.ArgumentParser()
p.add_argument('--manifest', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--source', type=Path)
p.add_argument('--local-root', type=Path)
p.add_argument('--ash', required=True)
a = p.parse_args()
manifest = json.loads(a.manifest.read_text())
out = a.output.resolve()
out.mkdir(parents=True, exist_ok=True)
runner = Path(__file__).with_name('run-entry.py')
digest = lambda data: hashlib.sha256(data).hexdigest()

def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])

def entry(root, report, shell, *, common=None, plugin=None, failure=''):
    cmd = [sys.executable, str(runner), '--source', str(root), '--shell', shell]
    if common: cmd += ['--common', str(common)]
    if plugin: cmd += ['--plugin', str(plugin)]
    if failure: cmd += ['--expected-failure', failure]
    name = ('original-' if failure else 'candidate-') + Path(shell).name + '.log'
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    (report / name).write_text(r.stdout + r.stderr)
    assert r.returncode == 0, (name, r.stdout, r.stderr)
    return name

proofs = []
for c in manifest:
    if a.local_root:
        root = (a.local_root / c['slug']).resolve()
    else:
        root = a.source.resolve()
        git(root, 'fetch', '--depth=1', 'origin', c['base'], c['old'])
        bundle = out / (c['slug'] + '.bundle')
        bundle.write_bytes(base64.b64decode((a.manifest.parent / 'bundles' /
            (c['slug'] + '.b64')).read_text()))
        assert digest(bundle.read_bytes()) == c['bundle_sha256']
        git(root, 'bundle', 'verify', str(bundle))
        git(root, 'fetch', str(bundle), 'HEAD')
        git(root, 'checkout', '--detach', c['candidate'])
    assert git(root, 'rev-parse', 'HEAD').decode().strip() == c['candidate']
    assert git(root, 'rev-parse', 'HEAD^').decode().strip() == c['base']
    assert git(root, 'show', '-s', '--format=%an <%ae>').decode().strip() == 'Yuen Su <zypulsecore@gmail.com>'
    assert 'Signed-off-by: Yuen Su <zypulsecore@gmail.com>' in git(root, 'show', '-s', '--format=%B').decode()
    assert '2.12.3' in git(root, 'show', '-s', '--format=%s').decode()
    git(root, 'diff', '--check', c['base'], c['candidate'])
    names = git(root, 'diff', '--name-only', c['base'], c['candidate']).decode().splitlines()
    assert names == c['upstream_files']
    changed = git(root, 'diff', '--name-only', c['old'], c['candidate']).decode().splitlines()
    expected = ['net/mwan3/Makefile']
    rpcd = c['slug'] == 'mwan3-rpcd'
    if rpcd:
        expected += ['net/mwan3/test-rpcd.uc', 'net/mwan3/test.sh']
    assert changed == expected, changed
    make_before = git(root, 'show', c['old'] + ':net/mwan3/Makefile')
    make_after = (root / 'net/mwan3/Makefile').read_bytes()
    assert make_after == make_before.replace(b'PKG_VERSION:=2.12.2\nPKG_RELEASE:=3\n',
        b'PKG_VERSION:=2.12.3\nPKG_RELEASE:=1\n')
    assert b'PKG_VERSION:=2.12.3\nPKG_RELEASE:=1\n' in make_after
    report = out / c['slug']
    report.mkdir(exist_ok=True)
    runtime = {}
    for path in ['net/mwan3/files/lib/mwan3/common.sh',
                 'net/mwan3/files/usr/share/rpcd/ucode/mwan3']:
        data = (root / path).read_bytes()
        assert data == git(root, 'show', c['old'] + ':' + path), path
        runtime[path] = digest(data)
    old_entry = git(root, 'show', c['old'] + ':net/mwan3/test.sh')
    (report / 'previous-test.sh').write_bytes(old_entry)
    if rpcd:
        block = old_entry.decode().split("ucode -S - <<'EOF'\n", 1)[1].rsplit('\nEOF', 1)[0]
        external = (root / 'net/mwan3/test-rpcd.uc').read_text()
        assert external.endswith(block + '\n'), 'ucode test statements changed'
        assert 'ucode -S - <<' not in (root / 'net/mwan3/test.sh').read_text()
        assert '$(dirname "$0")/test-rpcd.uc' in (root / 'net/mwan3/test.sh').read_text()
    else:
        assert (root / 'net/mwan3/test.sh').read_bytes() == old_entry
    hashes = {}
    for path in names + list(runtime):
        data = (root / path).read_bytes()
        dest = report / 'tested-source' / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        hashes[path] = digest(data)
    common = report / 'original-common.sh'
    common.write_bytes(git(root, 'show', c['base'] + ':net/mwan3/files/lib/mwan3/common.sh'))
    plugin = root / 'net/mwan3/files/usr/share/rpcd/ucode/mwan3'
    logs = []
    if rpcd:
        original_plugin = report / 'original-plugin.uc'
        original_plugin.write_bytes(git(root, 'show', c['base'] + ':net/mwan3/files/usr/share/rpcd/ucode/mwan3'))
        for shell in ('/bin/bash', a.ash):
            logs += [entry(root, report, shell, plugin=original_plugin, failure='get_int(""): expected 0'),
                     entry(root, report, shell, plugin=plugin)]
    else:
        failure = 'failed to normalize nexthop route:' if c['slug'] == 'mwan3-nexthop' else 'failed delegated source for '
        for shell in ('/bin/bash', a.ash):
            logs += [entry(root, report, shell, common=common, failure=failure),
                     entry(root, report, shell)]
    for path in ('net/mwan3/test.sh', 'net/mwan3/files/lib/mwan3/common.sh'):
        for shell in ('/bin/bash', a.ash):
            subprocess.run([shell, '-n', str(root / path)], check=True)
    proof = dict(slug=c['slug'], old=c['old'], candidate=c['candidate'], base=c['base'],
        upstream_files=names, revision_files=changed, package_version='2.12.3',
        package_release=1, runtime_files_identical_to_previous_head=runtime,
        previous_test_sha256=digest(old_entry), tested_source_sha256=hashes,
        entry_logs=logs, kernel_or_sdk_rerun=False)
    (report / 'verification.json').write_text(json.dumps(proof, indent=2) + '\n')
    proofs.append(proof)
(out / 'verification.json').write_text(json.dumps(proofs, indent=2) + '\n')
print('Verified', len(proofs), 'maintainer revisions; unchanged runtime, candidate entries and original failures.')

