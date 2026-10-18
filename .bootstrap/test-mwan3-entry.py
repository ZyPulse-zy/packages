#!/usr/bin/env python3
"""Execute actual test.sh after its OpenWrt imports, with private init state."""
import argparse
from pathlib import Path
import subprocess
import tempfile

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--common',type=Path,required=True)
p.add_argument('--test-script',type=Path,required=True)
p.add_argument('--shell',required=True)
p.add_argument('--expect-original-failure',action='store_true')
a=p.parse_args()
body=a.test_script.read_text().split('set -e\n',1)[1]
setup='''set +e
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
    r=subprocess.run([a.shell,'-c',setup+body,'entry',str(a.common.resolve()),directory],text=True,capture_output=True)
    print(r.stdout+r.stderr,end='')
    assert r.returncode==(1 if a.expect_original_failure else 0)
    if a.expect_original_failure:assert 'failed to normalize nexthop route:' in r.stderr
print('Actual package test entry: expected result, 5 existing + 5 nexthop cases, private init directory')
