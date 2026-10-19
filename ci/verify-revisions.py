#!/usr/bin/env python3
"""Verify minimal PR trees and run the retained package test entries.

The existing kernel/SDK verification remains separate. This revision changes
test placement and the rpcd regression only; production files must be identical.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

p = argparse.ArgumentParser()
p.add_argument('--manifest', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--local-root', type=Path)
p.add_argument('--source', type=Path)
p.add_argument('--ash', required=True)
a = p.parse_args()
manifest = json.loads(a.manifest.read_text())
out = a.output.resolve()
out.mkdir(parents=True, exist_ok=True)
runner = Path(__file__).with_name('run-entry.py')


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


def execute_entry(root, report_dir, shell, *, common=None, plugin=None, failure=''):
    cmd = [sys.executable, str(runner), '--source', str(root), '--shell', shell]
    if common:
        cmd += ['--common', str(common)]
    if plugin:
        cmd += ['--plugin', str(plugin)]
    if failure:
        cmd += ['--expected-failure', failure]
    name = ('original-' if failure else 'candidate-') + Path(shell).name + '.log'
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    (report_dir / name).write_text(result.stdout + result.stderr)
    assert result.returncode == 0, (str(root), name, result.stdout, result.stderr)
    return name


results = []
for c in manifest:
    if a.local_root:
        root = (a.local_root / c['slug']).resolve()
    else:
        root = a.source.resolve()
        git(root, 'fetch', '--depth=1', 'origin', c['base'], c['old'])
        bundle = out / (c['slug'] + '.bundle')
        bundle.write_bytes(base64.b64decode((a.manifest.parent / 'bundles' /
            (c['slug'] + '.b64')).read_text()))
        assert hashlib.sha256(bundle.read_bytes()).hexdigest() == c['bundle_sha256']
        git(root, 'bundle', 'verify', str(bundle))
        git(root, 'fetch', str(bundle), 'HEAD')
        git(root, 'checkout', '--detach', c['candidate'])
    assert git(root, 'rev-parse', 'HEAD').decode().strip() == c['candidate']
    assert git(root, 'rev-parse', 'HEAD^').decode().strip() == c['base']
    assert git(root, 'show', '-s', '--format=%an <%ae>').decode().strip() == 'Yuen Su <zypulsecore@gmail.com>'
    assert 'Signed-off-by: Yuen Su <zypulsecore@gmail.com>' in git(root, 'show', '-s', '--format=%B').decode()
    git(root, 'diff', '--check', c['base'], c['candidate'])
    names = git(root, 'diff', '--name-only', c['base'], c['candidate']).decode().splitlines()
    assert names == c['upstream_files']
    assert not any('/tests/' in n for n in names)
    changes = git(root, 'diff', '--name-status', c['old'], c['candidate']).decode().splitlines()
    for line in changes:
        status, path = line.split('\t')
        assert (status == 'D' and '/tests/' in path) or (
            c['slug'] == 'mwan3-rpcd' and status == 'M' and path == 'net/mwan3/test.sh'), line
    report = out / c['slug']
    report.mkdir(exist_ok=True)
    preserved = []
    for path in c['files']:
        if '/tests/' in path or path.endswith('/test.sh'):
            dest = report / 'previous-validation' / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = git(root, 'show', c['old'] + ':' + path)
            dest.write_bytes(data)
            preserved.append({'path': path, 'sha256': hashlib.sha256(data).hexdigest()})
        else:
            before = git(root, 'show', c['old'] + ':' + path)
            assert (root / path).read_bytes() == before, path
    logs = []
    if c['slug'].startswith('mwan3-'):
        common = report / 'original-common.sh'
        common.write_bytes(git(root, 'show', c['base'] + ':net/mwan3/files/lib/mwan3/common.sh'))
        plugin = root / 'net/mwan3/files/usr/share/rpcd/ucode/mwan3'
        if c['slug'] == 'mwan3-rpcd':
            original_plugin = report / 'original-plugin.uc'
            original_plugin.write_bytes(git(root, 'show', c['base'] + ':net/mwan3/files/usr/share/rpcd/ucode/mwan3'))
            for shell in ('/bin/bash', a.ash):
                logs.append(execute_entry(root, report, shell, plugin=original_plugin,
                    failure='get_int(""): expected 0'))
                logs.append(execute_entry(root, report, shell, plugin=plugin))
        else:
            failure = ('failed to normalize nexthop route:' if c['slug'] == 'mwan3-nexthop'
                else 'failed delegated source for ')
            for shell in ('/bin/bash', a.ash):
                logs.append(execute_entry(root, report, shell, common=common, failure=failure))
                logs.append(execute_entry(root, report, shell))
        for file in ('net/mwan3/test.sh', 'net/mwan3/files/lib/mwan3/common.sh'):
            subprocess.run(['/bin/bash', '-n', str(root / file)], check=True)
            subprocess.run([a.ash, '-n', str(root / file)], check=True)
    summary = {
        'slug': c['slug'], 'old': c['old'], 'candidate': c['candidate'],
        'base': c['base'], 'upstream_files': names,
        'production_files_identical': True, 'previous_validation_preserved': preserved,
        'entry_logs': logs, 'kernel_or_sdk_rerun': False,
    }
    (report / 'verification.json').write_text(json.dumps(summary, indent=2) + '\n')
    results.append(summary)
(out / 'verification.json').write_text(json.dumps(results, indent=2) + '\n')
print('Verified', len(results), 'minimal source revisions; retained entries and original failures passed.')
