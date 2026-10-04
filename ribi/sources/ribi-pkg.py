#!/usr/bin/env python3
import sys, os, tarfile, json, hashlib, re, tempfile, shutil, copy, subprocess, fcntl
from pathlib import Path
DB_DIR=Path('/var/lib/ribi/pkgdb'); LOCK=DB_DIR/'.lock'; NAME_RE=re.compile(r'^[a-z0-9][a-z0-9._+-]{0,63}$')

def init_db(): DB_DIR.mkdir(parents=True,exist_ok=True)
def pkgfile(name):
    if not isinstance(name,str) or not NAME_RE.fullmatch(name): raise ValueError('invalid package name')
    return DB_DIR/f'{name}.json'
def digest_file(p):
    h=hashlib.sha256();
    with open(p,'rb') as f:
        for c in iter(lambda:f.read(1024*1024),b''): h.update(c)
    return h.hexdigest()
def safe_member(member):
    rel=Path(member.name)
    if rel.is_absolute() or '..' in rel.parts or str(rel)=='.': raise RuntimeError(f'unsafe package path: {member.name}')
    if member.issym() or member.islnk():
        link=Path(member.linkname)
        if link.is_absolute() or '..' in link.parts: raise RuntimeError(f'unsafe package link: {member.name}')
def payload_digest(tar):
    h=hashlib.sha256()
    for m in tar.getmembers():
        if not m.name.startswith('payload/'): continue
        f=tar.extractfile(m)
        if f: h.update(m.name[8:].encode()+b'\0'); h.update(hashlib.sha256(f.read()).digest())
    return h.hexdigest()
def verify_rpk_signature(tar, manifest, payload_hash):
    sig_member = next((x for x in tar.getmembers() if x.name == 'signature.bin'), None)
    require = os.environ.get('RIBI_REQUIRE_SIGNED') == '1' or bool(manifest.get('signature_required'))
    if sig_member is None:
        if require: raise SystemExit('Error: signed package required but signature.bin is missing')
        return False
    if manifest.get('signature_algorithm', 'ed25519') != 'ed25519':
        raise SystemExit('Error: unsupported RPK signature algorithm')
    key = Path(os.environ.get('RIBI_REPOSITORY_KEY', '/etc/ribi/keys/repository.pub'))
    if not key.is_file(): raise SystemExit(f'Error: signature present but repository key is unavailable: {key}')
    sig = tar.extractfile(sig_member)
    if sig is None: raise SystemExit('Error: unreadable package signature')
    canonical = dict(manifest); canonical.pop('signature_required', None); canonical.pop('signature_algorithm', None)
    data = (json.dumps(canonical, sort_keys=True, separators=(',', ':')) + chr(10) + payload_hash + chr(10)).encode()
    with tempfile.TemporaryDirectory(prefix='rpk-verify-', dir='/tmp') as td:
        base=Path(td); (base/'data').write_bytes(data); (base/'sig').write_bytes(sig.read())
        result=subprocess.run(['openssl','pkeyutl','-verify','-pubin','-inkey',str(key),'-rawin','-in',str(base/'data'),'-sigfile',str(base/'sig')], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0: raise SystemExit('Error: RPK signature verification failed')
    return True
def install_rpk(path):
    init_db()
    if not path.is_file(): raise SystemExit(f"Error: Package file '{path}' not found.")
    with tarfile.open(path,'r:*') as tar:
        mf=tar.extractfile('manifest.json')
        if not mf: raise SystemExit('Error: Invalid package. Missing manifest.json.')
        try: m=json.loads(mf.read().decode('utf-8'))
        except Exception as e: raise SystemExit(f'Error: Invalid manifest JSON: {e}')
        if not isinstance(m,dict): raise SystemExit('Error: manifest must be an object')
        name=m.get('name'); ver=m.get('version'); arch=m.get('architecture',m.get('arch','x86_64'))
        if not isinstance(name,str) or not NAME_RE.fullmatch(name): raise SystemExit('Error: Invalid package name')
        if not isinstance(ver,str) or not re.fullmatch(r'[0-9A-Za-z][0-9A-Za-z._+~-]{0,127}',ver): raise SystemExit('Error: Invalid package version')
        if arch!='x86_64': raise SystemExit(f'Error: Unsupported package architecture: {arch}')
        members=[x for x in tar.getmembers() if x.name.startswith('payload/') and x.name!='payload/']
        if not members: raise SystemExit('Error: Package payload is empty')
        expected=m.get('payload_sha256')
        actual=payload_digest(tar)
        if expected and (not isinstance(expected,str) or not re.fullmatch(r'[0-9a-fA-F]{64}',expected) or actual.lower()!=expected.lower()):
            raise SystemExit('Error: Package payload integrity check failed')
        if not expected: raise SystemExit('Error: Package lacks required payload_sha256')
        signed=verify_rpk_signature(tar, m, actual)
        existing=pkgfile(name)
        if existing.exists(): raise SystemExit(f"Error: Package '{name}' is already installed")

        seen=set(); payload=[]
        for member in members:
            safe_member(member)
            rel=Path(member.name[8:])
            if rel.is_absolute() or '..' in rel.parts or str(rel)=='.':
                raise SystemExit(f'Error: unsafe package path: {member.name}')
            key=rel.as_posix()
            if key in seen: raise SystemExit(f'Error: duplicate package path: {member.name}')
            seen.add(key)
            payload.append((member,rel))

        protected={'/sbin/ribi-init','/sbin/init','/usr/local/bin/ribi-pkg','/etc/passwd','/etc/shadow'}
        installed=['/'+rel.as_posix() for member,rel in payload if not member.isdir()]
        if any(x in protected for x in installed):
            raise SystemExit('Error: Package attempts to replace protected Ribi system files')

        with tempfile.TemporaryDirectory(prefix='rpk-',dir='/tmp') as td:
            root=Path(td)/'root'; root.mkdir()
            # Extract directories/regular files/symlinks first. Hardlinks are deferred
            # so a valid forward hardlink does not depend on archive member order.
            hardlinks=[]
            for member,rel in payload:
                dest=root/rel
                curr=root
                for part in rel.parts[:-1]:
                    curr=curr/part
                    if curr.is_symlink():
                        raise SystemExit(f'Error: symlink used as package directory: {curr}')
                    if curr.exists() and not curr.is_dir():
                        raise SystemExit(f'Error: non-directory package path component: {curr}')
                    curr.mkdir(exist_ok=True)
                if dest.exists() or dest.is_symlink():
                    if member.isdir() and dest.is_dir() and not dest.is_symlink():
                        continue
                    raise SystemExit(f'Error: duplicate/existing package staging path: {dest}')
                member_copy=copy.copy(member)
                member_copy.name=rel.as_posix()
                if member.issym():
                    link=Path(member.linkname)
                    if link.is_absolute() or '..' in link.parts:
                        raise SystemExit(f'Error: unsafe package link: {member.name}')
                if member.islnk():
                    hardlinks.append((member_copy,rel)); continue
                tar.extract(member_copy,path=root,filter='data')
            for member,rel in hardlinks:
                target=Path(member.linkname)
                if target.is_absolute() or '..' in target.parts or target.as_posix() not in seen:
                    raise SystemExit(f'Error: unsafe/missing hardlink target: {member.linkname}')
                dest=root/rel
                if dest.exists() or dest.is_symlink():
                    raise SystemExit(f'Error: duplicate package staging path: {dest}')
                tar.extract(member,path=root,filter='data')

            destinations=[]
            for rel in installed:
                src=root/rel.lstrip('/'); dst=Path('/'+rel.lstrip('/'))
                if not src.exists() and not src.is_symlink():
                    raise SystemExit(f'Error: staged package file missing: {src}')
                if dst.exists() or dst.is_symlink():
                    raise SystemExit(f'Error: refusing to overwrite existing path: {dst}')
                destinations.append((src,dst))
            moved=[]
            try:
                for src,dst in destinations:
                    dst.parent.mkdir(parents=True,exist_ok=True)
                    shutil.move(str(src),str(dst)); moved.append((src,dst))
            except Exception:
                for src,dst in reversed(moved):
                    if dst.exists() or dst.is_symlink():
                        src.parent.mkdir(parents=True,exist_ok=True); shutil.move(str(dst),str(src))
                raise

        m['installed_files']=installed; m['payload_sha256']=actual; m['signature_verified']=signed
        pkgfile(name).write_text(json.dumps(m,indent=2)+'\n')
    print(f"[+] Package '{name}' ({ver}) installed.")

def install_compat(name):
    if not NAME_RE.fullmatch(name):
        raise SystemExit(f"Error: Invalid package name '{name}'.")
    apk=Path('/sbin/apk')
    if not apk.exists():
        raise SystemExit('Error: Compatibility package client is unavailable in this image.')
    print(f"[*] Installing '{name}' from the configured signed compatibility repositories...")
    try:
        result=subprocess.run([str(apk),'add','--no-cache',name], timeout=120)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"Error: Compatibility package '{name}' timed out while contacting repositories.")
    if result.returncode:
        raise SystemExit(f"Error: Compatibility package '{name}' could not be installed.")
    print(f"[+] Compatibility package '{name}' installed.")

def install_target(target):
    p=Path(target)
    if p.exists() or target.endswith('.rpk') or '/' in target:
        install_rpk(p)
    else:
        install_compat(target)

def compat_command(action, args):
    apk=Path('/sbin/apk')
    if not apk.exists(): raise SystemExit('Error: Compatibility package client is unavailable.')
    cmd=[str(apk), action, '--no-cache'] + args
    try:
        result=subprocess.run(cmd, timeout=30 if action=='search' else 120)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"Error: '{action}' timed out while contacting repositories. Check networking and try again.")
    raise SystemExit(result.returncode)

def remove_pkg(name):
    try: db=pkgfile(name)
    except ValueError: print('Error: Invalid package name.'); return 1
    if not db.exists(): print(f"Error: Package '{name}' is not installed."); return 1
    m=json.loads(db.read_text()); installed=m.get('installed_files',[])
    # Ownership-aware removal: do not remove a path referenced by another package.
    owned={x for x in installed if isinstance(x,str) and x.startswith('/')}
    others=set()
    for f in DB_DIR.glob('*.json'):
        if f==db: continue
        try: others.update(x for x in json.loads(f.read_text()).get('installed_files',[]) if isinstance(x,str))
        except Exception: pass
    for x in sorted(owned-others,key=len,reverse=True):
        p=Path(x)
        if p.is_file() or p.is_symlink(): p.unlink(missing_ok=True)
    db.unlink(); print(f"[+] Package '{name}' removed."); return 0
def locked(fn, *args):
    init_db()
    with open(LOCK, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return fn(*args)

def list_pkgs():
    init_db(); print(f"{'PACKAGE':<25} {'VERSION':<15} DESCRIPTION")
    for f in sorted(DB_DIR.glob('*.json')):
        try:
            d=json.loads(f.read_text()); print(f"{d.get('name',f.stem):<25} {d.get('version','?'):<15} {d.get('description','')}")
        except Exception: print(f"{f.stem:<25} {'INVALID':<15}")

if __name__=='__main__':
    a=sys.argv[1:]
    if not a: print('Usage: ribi-pkg {install <name|pkg.rpk>|remove <name>|list|info}'); raise SystemExit(0)
    if a[0]=='install' and len(a)>1: locked(install_target, a[1])
    elif a[0]=='remove' and len(a)>1: raise SystemExit(locked(remove_pkg, a[1]))
    elif a[0]=='list': list_pkgs()
    elif a[0] in ('search','update'): compat_command(a[0],a[1:])
    elif a[0]=='info': print('Ribi packages: signed Alpine compatibility packages by name, plus authenticated RPK files.')
    else: raise SystemExit('Usage: ribi-pkg {install <pkg.rpk>|remove <name>|list|info}')
