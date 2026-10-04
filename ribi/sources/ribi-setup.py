#!/usr/bin/env python3
import os, subprocess, sys, time
from pathlib import Path

def sh(cmd, check=False):
    return subprocess.run(cmd, text=True, capture_output=True, check=check)
def ask(q, default=''):
    v=input(f'{q} [{default}]: ').strip()
    return v or default
def yes(q):
    return input(q+' Type YES to continue: ').strip() == 'YES'
def choices(q, values, default):
    value=ask(q, default).lower()
    if value not in values: raise SystemExit(f"Invalid choice '{value}'. Choose one of: {', '.join(values)}")
    return value
def interfaces():
    try:
        return sorted(p.name for p in Path('/sys/class/net').iterdir() if p.name != 'lo')
    except Exception: return []
def devices():
    try:
        out=subprocess.check_output(['lsblk','-rpo','NAME,TYPE,SIZE,FSTYPE,LABEL,MODEL'],text=True)
        rows=[]
        for line in out.splitlines():
            f=line.split(None,5)
            if len(f)>=3 and f[1] in ('disk','part'):
                rows.append(f)
        return rows
    except Exception: return []
def main():
    if os.geteuid()!=0: raise SystemExit('Setup Ribi OS must run as administrator.')
    print('\n=== Ribi OS First-Boot Setup ===\n')
    print('This wizard configures networking and can prepare persistence.')
    print('Nothing is erased unless you explicitly type YES.\n')
    hostname=ask('Hostname','ribi')
    if not __import__('re').fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,62}', hostname):
        raise SystemExit('Invalid hostname. Use letters, numbers, dots, and hyphens.')
    detected=interfaces(); iface=ask('Network interface (detected: '+(', '.join(detected) or 'none')+')',detected[0] if detected else 'eth0')
    ipv4=choices('IPv4 mode (dhcp/manual)', {'dhcp','manual'}, 'dhcp')
    ipv4addr=''; gateway=''; dns=''
    if ipv4=='manual':
        ipv4addr=ask('IPv4 address/CIDR','192.168.1.100/24'); gateway=ask('Gateway','192.168.1.1'); dns=ask('DNS','1.1.1.1')
    ipv6=choices('IPv6 mode (auto/disabled/manual)', {'auto','disabled','manual'}, 'auto')
    timezone=ask('Timezone','UTC')
    print('\nStorage devices/partitions:')
    rows=devices()
    for i,r in enumerate(rows): print(f'  {i}: '+' | '.join(r))
    selected=''
    if rows:
        raw=ask('Enter device path or number (blank = RAM only)','')
        if raw.isdigit() and int(raw)<len(rows): selected=rows[int(raw)][0]
        else: selected=raw
    mode=choices('Disk use (persistent-overlay/full-install/ram-only)', {'persistent-overlay','full-install','ram-only'}, 'persistent-overlay')
    if mode=='ram-only' or not selected:
        print('Keeping RAM-only mode. Network settings will be applied for this session.')
        Path('/etc/ribi').mkdir(exist_ok=True)
        Path('/etc/ribi/setup.conf').write_text(f'hostname={hostname}\ninterface={iface}\nipv4={ipv4}\nipv6={ipv6}\ntimezone={timezone}\n')
        return 0
    if not selected.startswith('/dev/') or not Path(selected).exists(): raise SystemExit('Selected device does not exist.')
    typ=sh(['lsblk','-dnpo','TYPE',selected]).stdout.strip()
    if typ not in ('part','disk'): raise SystemExit('Selected path is not a disk or partition.')
    print(f'\nSelected: {selected} ({typ})')
    print('1) use existing ext4 filesystem\n2) erase and create ext4 filesystem')
    action=ask('Storage action','1')
    if action in ('2','erase','format'):
        if not yes(f'PERMANENTLY ERASE ALL DATA on {selected}?'): raise SystemExit('Formatting cancelled.')
        subprocess.run(['mkfs.ext4','-F','-L','RibiPersistence',selected],check=True)
    fstype=sh(['blkid','-s','TYPE','-o','value',selected]).stdout.strip()
    if fstype!='ext4': raise SystemExit('Persistence requires ext4. Choose erase/create ext4 or cancel.')
    if not yes(f'Prepare {selected} for Ribi persistent storage?'): raise SystemExit('Persistence cancelled.')
    mount='/run/ribi-setup-storage'; Path(mount).mkdir(exist_ok=True)
    subprocess.run(['mount',selected,mount],check=True)
    try:
        Path(mount,'upper').mkdir(exist_ok=True); Path(mount,'work').mkdir(exist_ok=True)
        Path(mount,'.ribi-persistence').write_text('Ribi OS persistent live overlay\n')
        Path(mount,'setup.conf').write_text(f'hostname={hostname}\ninterface={iface}\nipv4={ipv4}\nipv6={ipv6}\ntimezone={timezone}\n')
    finally: subprocess.run(['umount',mount],check=False)
    if mode=='full-install':
        print('Launching the full-disk installer. Review its separate confirmation carefully.')
        return subprocess.run(['/usr/local/bin/ribi-installer']).returncode
    print('\nPersistence prepared successfully. Reboot and choose Ribi OS normally.')
    print('The next boot will discover the prepared disk automatically.')
    return 0
if __name__=='__main__': raise SystemExit(main())
