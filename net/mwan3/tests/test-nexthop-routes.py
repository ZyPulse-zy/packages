#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise actual route normalization, startup, ifup and monitor handlers."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--shell',default='/bin/bash',help='bash or BusyBox ash executable')
parser.add_argument('--installed-root',type=Path,help='optional extracted package root')
args=parser.parse_args()
if os.geteuid()!=0 or os.readlink('/proc/self/ns/net')==os.environ.get('MWAN_TEST_PARENT_NETNS',os.readlink('/proc/self/ns/net')):
    raise SystemExit('Refusing route writes outside a distinct network namespace')
root=Path(__file__).resolve().parents[1]
source=args.installed_root if args.installed_root else root/'files'
checks=0


def run(*cmd):
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=5)
    if p.returncode:raise RuntimeError((cmd,p.returncode,p.stdout,p.stderr))
    return p.stdout


def check(value,why=''):
    global checks
    assert value,why
    checks+=1


with tempfile.TemporaryDirectory(prefix='mwan-nexthop-') as name:
    temp=Path(name);script=temp/'actual-functions.sh'
    common=(source/'lib/mwan3/common.sh').read_text()
    library=(source/'lib/mwan3/mwan3.sh').read_text()
    monitor=(source/'usr/sbin/mwan3rtmon').read_text()
    # Keep all actual function bodies; omit only OpenWrt imports and daemon entry.
    library='\n'.join(l for l in library.splitlines() if not l.startswith('. '))
    monitor='\n'.join(l for l in monitor.splitlines() if not l.startswith('. ') and l!='main "$@"')
    script.write_text(common+'\n'+library+'\n'+monitor+'\n'+r'''
FAMILY="$2"
MWAN3_STATUS_DIR="$1/status"
MWAN3_STATUS_IPTABLES_LOG_DIR="$1/logs"
IP4="$MWAN_TEST_IP -4"
IP6="$MWAN_TEST_IP -6"
NO_IPV6=0
config_load() { :; }
config_get() {
 local value="$4"
 case "$3" in family) value="$FAMILY";; enabled) value=1;; esac
 eval "$1=\$value"
}
config_get_bool() { config_get "$@"; }
config_foreach() { "$1" wan0; "$1" wan1; }
config_list_foreach() { :; }
uci_toggle_state() { :; }
uci_get_state() { echo 60; }
network_flush_cache() { :; }
network_get_device() { [ -n "$2" ] || return 1; eval "$1=nh$2"; }
mwan3_get_mwan3track_status() { eval "$1=active"; }
mwan3_set_connected_ipv4() { :; }
mwan3_set_connected_ipv6() { :; }
LOG() { echo "$*" >> "$MWAN3_STATUS_DIR/test.log"; }
IPS=true
IPT4=true
IPT6=true
mwan3_init
if [ "$FAMILY" = ipv4 ]; then IP="$IP4"; else IP="$IP6"; fi
case "$3" in
 normalize) printf '%s\n' "$4" | sed -ne "$MWAN3_ROUTE_LINE_EXP";;
 initial) mwan3_add_all_routes "$FAMILY";;
 ifup) mwan3_create_iface_route "$4";;
 event) mwan3_rtmon_route_handle "$4" "$FAMILY";;
 *) exit 2;;
esac
''')
    def call(family,op,line=''):
        # Some host BusyBox builds prefer their own ip applet. This regression
        # explicitly needs iproute2's nexthop support, as used for setup/readback.
        return run('env','MWAN_TEST_IP='+shutil.which('ip'),args.shell,str(script),str(temp),family,op,line).strip()
    def rows(family,table,prefix):
        return json.loads(run('ip','-'+family[-1],'-j','route','show','table',str(table),prefix))
    def clear(family):
        for table in (1,2):
            # Allocate initially absent FIB tables before querying/flushing them.
            run('ip','-'+family[-1],'route','replace','blackhole','default','table',str(table),'metric','4242')
            # iproute2 flush can itself replay incompatible expanded nhid attrs.
            # Delete our private rows by table/prefix instead of that dump replay.
            for row in json.loads(run('ip','-'+family[-1],'-j','route','show','table',str(table))):
                cmd=['ip','-'+family[-1],'route','del','table',str(table),row['dst']]
                if 'metric' in row:cmd+=['metric',str(row['metric'])]
                run(*cmd)
    for dev in ('nhwan0','nhwan1','nhlan'):
        run('ip','link','add',dev,'type','dummy');run('ip','link','set',dev,'up')
    plain='192.0.2.0/24 dev eth0 scope link src 192.0.2.1'
    for flags in ('dead linkdown','linkdown dead','dead','linkdown'):
        check(call('ipv4','normalize',plain+' '+flags)==plain,'legacy flag removal changed')
    address='fd00:1::/64 via fe80::dead dev eth0 proto static metric 1024 pref medium'
    check(call('ipv6','normalize',address)==address,'IPv6 address suffix changed')
    for family,ip,prefix,gw,new_gw,source in (
        ('ipv4','192.0.2.2/24','203.0.113.0/24','192.0.2.1','192.0.2.3','192.0.2.2'),
        ('ipv6','2001:db8:1::2/64','2001:db8:9::/64','2001:db8:1::1','2001:db8:1::3','2001:db8:1::2')):
        v='-'+family[-1];nh=101 if family=='ipv4' else 102
        run('ip',v,'addr','add',ip,'dev','nhwan0','nodad')
        run('ip',v,'nexthop','add','id',str(nh),'via',gw,'dev','nhwan0','onlink')
        run('ip',v,'route','add',prefix,'nhid',str(nh),'proto','ospf','metric','20','src',source)
        # Keep the spaces ip monitor/read -r actually preserves.
        line=run('ip',v,'-o','route','show',prefix).rstrip('\n')
        check('nhid' in line and ' via ' in line,line)
        compact=prefix+' nhid '+str(nh)+' proto ospf metric 20'
        check(call(family,'normalize',compact)==compact,'non-expanded ID changed')
        plain=prefix+' via '+gw+' dev nhwan0 proto ospf metric 20'
        if family=='ipv6':plain+=' pref medium'
        check(call(family,'normalize',plain)==plain,'ordinary route changed')
        normalized=call(family,'normalize',line)
        for op in ('initial','ifup','event'):
            clear(family)
            call(family,op,'wan0' if op=='ifup' else line)
            found=rows(family,1,prefix)
            check(len(found)==1 and found[0].get('gateway')==gw,(op,found))
            check(found[0].get('metric')==20 and found[0].get('protocol')=='ospf',(op,found,line,normalized,(temp/'status/test.log').read_text()))
            check(found[0].get('prefsrc')==source and 'onlink' in found[0].get('flags',[]),found)
            check(not rows(family,2,prefix),'WAN-specific route copied to other WAN')
        check('nhid' not in normalized and ' via '+gw+' ' in normalized,normalized)
        clear(family);call(family,'event',compact)
        check(all(rows(family,t,prefix)[0].get('nhid')==nh for t in (1,2)),'ID-only replay failed')
        clear(family);call(family,'event',line)
        run('ip',v,'nexthop','replace','id',str(nh),'via',new_gw,'dev','nhwan0','onlink')
        changed=run('ip',v,'-o','route','show',prefix).rstrip('\n')
        call(family,'event',changed)
        check(rows(family,1,prefix)[0].get('gateway')==new_gw,'nexthop update lost')
        run('ip',v,'route','del',prefix)
        call(family,'event','Deleted '+changed)
        check(not rows(family,1,prefix),('route deletion lost',family,changed,rows(family,1,prefix),(temp/'status/test.log').read_text()))
        run('ip',v,'nexthop','del','id',str(nh))
        # A non-WAN route is shared across both active tables.
        run('ip',v,'addr','del',ip,'dev','nhwan0')
        run('ip',v,'addr','add',ip,'dev','nhlan','nodad')
        run('ip',v,'nexthop','add','id',str(nh),'via',gw,'dev','nhlan')
        run('ip',v,'route','add',prefix,'nhid',str(nh),'proto','ospf','metric','20')
        shared=run('ip',v,'-o','route','show',prefix).rstrip('\n')
        clear(family);call(family,'event',shared)
        check(all(rows(family,t,prefix)[0].get('gateway')==gw for t in (1,2)))
        run('ip',v,'route','del',prefix);run('ip',v,'nexthop','del','id',str(nh))
    print(json.dumps({'passed':True,'checks':checks,'shell':args.shell,
                      'actualFunctions':True,'actualKernelRoutes':True,
                      'families':['ipv4','ipv6'],'productionChanges':False}))
