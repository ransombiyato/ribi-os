#!/usr/bin/env python3
import sys, os, subprocess, json, signal, time
from pathlib import Path
from typing import Optional, Dict, Tuple, Any
SERVICES_DIR=Path('/etc/ribi/services'); RUN_DIR=Path('/run/ribi/services')
NAME_RE=__import__('re').compile(r'^[A-Za-z0-9_.+-]+$')

def init_runtime(): RUN_DIR.mkdir(parents=True,exist_ok=True); SERVICES_DIR.mkdir(parents=True,exist_ok=True)
def get_service_meta(name):
    if not NAME_RE.fullmatch(name): return None
    p=SERVICES_DIR/f'{name}.json'
    if not p.is_file(): return None
    try:
        d=json.loads(p.read_text());
        if not isinstance(d,dict): return None
        return d
    except Exception as e: print(f'Error reading service {name}: {e}'); return None

def _proc_starttime(pid):
    try:
        fields=Path(f'/proc/{pid}/stat').read_text().split()
        if len(fields) < 22 or fields[2] == 'Z': return None
        return fields[21]
    except Exception: return None

def is_running(name):
    pid_file=RUN_DIR/f'{name}.pid'; meta_file=RUN_DIR/f'{name}.start'
    if not pid_file.is_file(): return False,None
    try:
        pid=int(pid_file.read_text().strip()); start=pid_file.with_suffix('.start').read_text().strip() if pid_file.with_suffix('.start').is_file() else ''
        os.kill(pid,0)
        current=_proc_starttime(pid)
        if current is None or (start and current != start): raise OSError('PID reused or zombie')
        return True,pid
    except Exception:
        pid_file.unlink(missing_ok=True); meta_file.unlink(missing_ok=True); return False,None

def _normalize_cmd(cmd):
    if isinstance(cmd,list) and all(isinstance(x,str) for x in cmd): return cmd
    if isinstance(cmd,str): return ['/bin/sh','-c',cmd]
    return None

def start_service(name):
    init_runtime(); meta=get_service_meta(name)
    if not meta: print(f"Service '{name}' not found or invalid."); return False
    running,pid=is_running(name)
    if running: print(f"Service '{name}' is already running (PID {pid})."); return True
    cmd=_normalize_cmd(meta.get('start_command'))
    if not cmd: print(f"Service '{name}' has no valid start_command."); return False
    try:
        proc=subprocess.Popen(cmd, start_new_session=True, close_fds=True)
        (RUN_DIR/f'{name}.pid').write_text(str(proc.pid)); (RUN_DIR/f'{name}.start').write_text(_proc_starttime(proc.pid) or '')
        return True
    except OSError as e: print(f"Error starting {name}: {e}"); return False

def stop_service(name):
    init_runtime(); running,pid=is_running(name)
    if not running or pid is None: print(f"Service '{name}' is not running."); return False
    try:
        os.killpg(pid,signal.SIGTERM); deadline=time.time()+3
        while time.time()<deadline and is_running(name)[0]: time.sleep(.1)
        if is_running(name)[0]: os.killpg(pid,signal.SIGKILL)
    except OSError: pass
    (RUN_DIR/f'{name}.pid').unlink(missing_ok=True); (RUN_DIR/f'{name}.start').unlink(missing_ok=True); return True

def list_services():
    init_runtime(); print('RIBI OS REGISTERED SERVICES:')
    for p in sorted(SERVICES_DIR.glob('*.json')):
        m=get_service_meta(p.stem)
        if m:
            r,pid=is_running(p.stem); print(f"  {p.stem:<20} [{'RUNNING '+str(pid) if r else 'STOPPED':<14}] [{'ENABLED' if m.get('enabled') else 'DISABLED':<8}] - {m.get('description','')}")

def start_all():
    init_runtime()
    for p in sorted(SERVICES_DIR.glob('*.json')):
        m=get_service_meta(p.stem)
        if m and m.get('enabled'): start_service(p.stem)

if __name__=='__main__':
    init_runtime(); a=sys.argv[1:] if len(sys.argv)>1 else []
    if a==['list']: list_services()
    elif a==['--start-all']: start_all()
    elif len(a)>=2 and a[0] in ('start','stop','restart','status'):
        if a[0]=='start': ok=start_service(a[1])
        elif a[0]=='stop': ok=stop_service(a[1])
        elif a[0]=='restart': stop_service(a[1]); ok=start_service(a[1])
        else:
            r,pid=is_running(a[1]); print(f"Status: {'RUNNING ('+str(pid)+')' if r else 'STOPPED'}"); ok=True
        sys.exit(0 if ok else 1)
    else: print('Usage: ribisvc {start <svc>|stop <svc>|restart <svc>|status <svc>|list|--start-all}'); sys.exit(1)
