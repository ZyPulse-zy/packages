#!/usr/bin/env python3
"""Run the existing package entry after its imports, using private init state."""
import argparse
from pathlib import Path
import subprocess
import tempfile

p = argparse.ArgumentParser()
p.add_argument('--source', type=Path, required=True)
p.add_argument('--shell', required=True)
p.add_argument('--trace', action='store_true')
p.add_argument('--plugin', type=Path)
p.add_argument('--common', type=Path)
p.add_argument('--expected-failure', default='')
a = p.parse_args()
root = a.source.resolve()
body = (root / 'net/mwan3/test.sh').read_text().split('set -e\n', 1)[1]
external = root / 'net/mwan3/test-rpcd.uc'
if a.plugin and not external.exists():
    assert body.count("'/usr/share/rpcd/ucode/mwan3'") == 1
    # This substitutes only the installation path in the independent fixture.
    body = body.replace("'/usr/share/rpcd/ucode/mwan3'", repr(str(a.plugin.resolve())))
setup = '''set +e
. "$1"
MWAN3_STATUS_DIR="$2"
MWAN3_STATUS_IPTABLES_LOG_DIR="$2/iptables_log"
config_load() { :; }
config_get() { export "$1=0x3f00"; }
config_get_bool() { export "$1=0"; }
uci_toggle_state() { :; }
LOG() { :; }
set -e
'''
with tempfile.TemporaryDirectory(prefix='mwan-entry-') as directory:
    fixture = Path(directory)
    if external.exists():
        program = external.read_text()
        plugin = a.plugin or root / 'net/mwan3/files/usr/share/rpcd/ucode/mwan3'
        assert program.count("'/usr/share/rpcd/ucode/mwan3'") == 1
        program = program.replace("'/usr/share/rpcd/ucode/mwan3'", repr(str(plugin.resolve())))
        (fixture / external.name).write_text(program)
    entry = fixture / 'test.sh'
    entry.write_text(setup + body)
    result = subprocess.run([a.shell, *(['-x'] if a.trace else []), str(entry),
        str((a.common or root / 'net/mwan3/files/lib/mwan3/common.sh').resolve()), directory],
        cwd='/', capture_output=True, text=True, timeout=30)
    print(result.stdout + result.stderr, end='')
    if a.expected_failure:
        assert result.returncode != 0 and a.expected_failure in result.stderr
    else:
        assert result.returncode == 0
print('Actual entry after imports: expected result; init state is private.')
