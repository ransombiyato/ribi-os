#!/usr/bin/env python3
import os, sys, shutil, subprocess, time

def run(c): return subprocess.run(c,check=True)
def parts(dev):
    out=subprocess.check_output(['lsblk','-lnpo','NAME,TYPE',dev],text=True)
    return [line.split()[0] for line in out.splitlines() if len(line.split())>=2 and line.split()[1]=='part']
def mountpoints_under(path):
    base=os.path.abspath(path).rstrip('/')
    try:
        with open('/proc/mounts','r') as mounts_file:
            found=[]
            for line in mounts_file:
                fields=line.split()
                if len(fields)>=2 and (fields[1]==base or fields[1].startswith(base+'/')):
                    found.append(fields[1])
        return sorted(set(found),key=len,reverse=True)
    except OSError:
        return [base]
def cleanup(paths):
    for p in reversed(paths):
        for mountpoint in mountpoints_under(p):
            subprocess.run(['umount',mountpoint],check=False)
def has_mounts_below(path): return bool(mountpoints_under(path))

def preflight_target(dev):
    rows=subprocess.run(['lsblk','-nrpo','NAME,MOUNTPOINTS',dev],capture_output=True,text=True,check=False).stdout.splitlines()
    mounted=[]
    for row in rows:
        fields=row.split(None,1)
        if len(fields)==2 and fields[1].strip() not in ('','-'):
            mounted.append(f'{fields[0]} -> {fields[1].strip()}')
    if mounted:
        raise SystemExit('Error: target disk or one of its partitions is mounted: '+', '.join(mounted))
    try:
        with open('/proc/mounts','r') as mounts_file:
            root_source=''
            for line in mounts_file:
                fields=line.split()
                if len(fields)>=2 and fields[1]=='/':
                    root_source=fields[0].split('[',1)[0]
                    break
    except OSError as exc:
        raise SystemExit('Error: cannot read active root source; refusing to install.') from exc
    if not root_source:
        raise SystemExit('Error: cannot determine active root source; refusing to install.')
    if root_source.startswith('/dev/'):
        root_source=os.path.realpath(root_source)
    if root_source and (root_source==dev or root_source.startswith(dev+'p') or root_source.startswith(dev)):
        raise SystemExit(f'Error: refusing to overwrite the active root device ({root_source}).')
    print('[safety] target and child partitions are not mounted; active root is protected.')

def dry_run_plan(dev):
    if not dev.startswith('/dev/') or dev == '/dev/':
        raise SystemExit('Error: dry-run target must look like a device path under /dev/.')
    if os.path.exists(dev):
        typ=subprocess.run(['lsblk','-dn','-o','TYPE',dev],capture_output=True,text=True,check=False).stdout.strip()
        if typ!='disk': raise SystemExit('Error: dry-run target exists but is not a whole disk.')
        preflight_target(dev)
        print(f'Target detected: whole disk {dev}; no mounts found')
    else:
        print('Target is not present in this environment; showing a static plan only.')
    uefi=os.path.isdir('/sys/firmware/efi')
    print('RIBI INSTALLER DRY-RUN — NO DISK WILL BE MODIFIED')
    print(f'Target placeholder: {dev}')
    print(f'Firmware plan: {"GPT + UEFI" if uefi else "MBR + BIOS"}')
    if uefi:
        print(f'  parted -s {dev} mklabel gpt')
        print(f'  create 513 MiB FAT32 ESP and mark esp')
        print(f'  create remaining ext4 RibiRoot partition')
    else:
        print(f'  parted -s {dev} mklabel msdos')
        print(f'  create full-disk ext4 RibiRoot partition and mark boot')
    print('  format filesystems only after interactive YES confirmation')
    print('  mount root, synchronize /run/rootfs, write fstab and machine-id')
    print('  install GRUB without NVRAM changes, write /boot/grub/grub.cfg')
    print('DRY-RUN COMPLETE — rerun without --dry-run only after reviewing the target disk.')
    return 0

def require_installer_tools():
    required=('lsblk','parted','partprobe','blockdev','mkfs.ext4','mkfs.vfat','mount','umount','rsync','blkid','grub-install','chroot','udevadm')
    missing=[tool for tool in required if shutil.which(tool) is None]
    if missing: raise SystemExit('Error: installer tools missing: '+', '.join(missing)+'. No disks were changed.')

def run_cli_installer():
    if '--dry-run' in sys.argv:
        targets=[x for x in sys.argv[1:] if x != '--dry-run']
        if len(targets)!=1: raise SystemExit('Usage: ribi-installer --dry-run /dev/<target-disk>')
        return dry_run_plan(targets[0])
    if os.geteuid()!=0: raise SystemExit('Error: Installer must be run as root.')
    src='/run/rootfs'
    if not os.path.isfile(src+'/sbin/ribi-init'): raise SystemExit('Error: Live rootfs not found at /run/rootfs')
    require_installer_tools()
    kernel='/run/media/live/vmlinuz' if os.path.isfile('/run/media/live/vmlinuz') else '/live/vmlinuz'
    initrd='/run/media/live/initrd.img' if os.path.isfile('/run/media/live/initrd.img') else '/live/initrd.img'
    if not os.path.isfile(kernel): raise SystemExit('FATAL: live kernel missing; no disks were changed.')
    subprocess.run(['lsblk','-d','-o','NAME,SIZE,MODEL,TYPE'])
    dev=input('Enter target disk device (e.g. /dev/sda): ').strip()
    if not dev.startswith('/dev/') or not os.path.exists(dev): raise SystemExit('Error: Device not found.')
    if subprocess.run(['lsblk','-dn','-o','TYPE',dev],capture_output=True,text=True).stdout.strip()!='disk': raise SystemExit('Error: Target must be a whole disk.')
    preflight_target(dev)
    if input(f"Re-enter target device exactly ({dev}) to confirm: ").strip()!=dev: raise SystemExit('Installation aborted: target confirmation did not match.')
    if input(f"WARNING: ALL DATA ON {dev} WILL BE ERASED! Type 'YES': ").strip()!='YES': raise SystemExit('Installation aborted.')
    uefi=os.path.isdir('/sys/firmware/efi'); print(f"[*] Partitioning {dev} ({'GPT / UEFI' if uefi else 'MBR / BIOS'})...")
    if uefi:
        run(['parted','-s',dev,'mklabel','gpt']); run(['parted','-s',dev,'mkpart','ESP','fat32','1MiB','513MiB']); run(['parted','-s',dev,'set','1','esp','on']); run(['parted','-s',dev,'mkpart','RibiRoot','ext4','513MiB','100%'])
    else:
        run(['parted','-s',dev,'mklabel','msdos']); run(['parted','-s',dev,'mkpart','primary','ext4','1MiB','100%']); run(['parted','-s',dev,'set','1','boot','on'])
    probe=subprocess.run(['partprobe',dev],capture_output=True,text=True)
    if probe.returncode!=0:
        reread=subprocess.run(['blockdev','--rereadpt',dev],capture_output=True,text=True)
        if reread.returncode!=0:
            print(f"WARNING: kernel did not immediately reread {dev}; waiting for udev.")
    disk_parts=[]
    for _ in range(20):
        disk_parts=parts(dev)
        if (len(disk_parts)>=2 if uefi else len(disk_parts)>=1): break
        subprocess.run(['udevadm','settle'],check=False); time.sleep(.5)
    if (len(disk_parts)<2 if uefi else len(disk_parts)<1): raise SystemExit(f'Error: partition table did not appear for {dev}')
    if uefi: esp,root=disk_parts[0],disk_parts[1]
    else: root=disk_parts[0]; esp=None
    mounted=[]; mount_dir='/tmp/ribi_target'
    try:
        if esp: run(['mkfs.vfat','-F32',esp])
        run(['mkfs.ext4','-F','-L','RibiRoot',root]); os.makedirs(mount_dir,exist_ok=True); run(['mount',root,mount_dir]); mounted.append(mount_dir)
        print('[*] Synchronizing clean Ribi OS rootfs...')
        run(['rsync','-aH','--delete','--exclude=/run/*','--exclude=/tmp/*','--exclude=/proc/*','--exclude=/sys/*','--exclude=/dev/*',src+'/',mount_dir+'/'])
        os.makedirs(mount_dir+'/boot',exist_ok=True)
        if esp: os.makedirs(mount_dir+'/boot/efi',exist_ok=True); run(['mount',esp,mount_dir+'/boot/efi']); mounted.append(mount_dir+'/boot/efi')
        # The installed system gets its own fstab and machine-id instead of live-media assumptions.
        os.makedirs(mount_dir+'/etc',exist_ok=True)
        uuid_root=subprocess.check_output(['blkid','-s','UUID','-o','value',root],text=True).strip()
        lines=[f'UUID={uuid_root} / ext4 defaults 0 1']
        if esp:
            uuid_esp=subprocess.check_output(['blkid','-s','UUID','-o','value',esp],text=True).strip(); lines.append(f'UUID={uuid_esp} /boot/efi vfat umask=0077 0 2')
        open(mount_dir+'/etc/fstab','w').write('\n'.join(lines)+'\n')
        os.makedirs(mount_dir+'/etc',exist_ok=True); open(mount_dir+'/etc/machine-id','w').write('')
        shutil.copy2(kernel,mount_dir+'/boot/vmlinuz'); os.chmod(mount_dir+'/boot/vmlinuz',0o755)
        if os.path.isfile(initrd): shutil.copy2(initrd,mount_dir+'/boot/initrd.img')
        vfs=[]
        try:
            for v in ('dev','proc','sys'):
                t=f'{mount_dir}/{v}'; os.makedirs(t,exist_ok=True); run(['mount','--rbind','/'+v,t]); vfs.append(t)
            if uefi:
                run(['chroot',mount_dir,'grub-install','--target=x86_64-efi','--efi-directory=/boot/efi','--bootloader-id=RibiOS','--no-nvram','--removable','--recheck'])
            else:
                run(['grub-install',f'--boot-directory={mount_dir}/boot','--target=i386-pc','--recheck',dev])
        finally: cleanup(vfs)
        os.makedirs(mount_dir+'/boot/grub',exist_ok=True)
        initrd_line=' ribi.installed=1 console=tty0 console=ttyS0,115200'+('\n    initrd /boot/initrd.img' if os.path.isfile(mount_dir+'/boot/initrd.img') else '')
        open(mount_dir+'/boot/grub/grub.cfg','w').write(f'set default=0\nset timeout=5\nmenuentry "Ribi OS 1.0 (bulbQT)" {{\n    linux /boot/vmlinuz root=UUID={uuid_root} rw init=/sbin/ribi-init{initrd_line}\n}}\n')
        print('[+] Ribi OS installation successfully completed!')
    finally:
        cleanup(mounted)
        if has_mounts_below(mount_dir):
            print('WARNING: target mounts remain; preserving the mounted tree to protect live devices.')
        else:
            shutil.rmtree(mount_dir,ignore_errors=True)

if __name__=='__main__': run_cli_installer()
