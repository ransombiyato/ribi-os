#!/usr/bin/python3
import os, sys, traceback
def mark(s):
    line='[RIBI-HANDOFF] '+s+'\n'
    try: open('/tmp/ribi-handoff.log','a').write(line)
    except Exception: pass
    try: open('/dev/ttyS0','a').write(line)
    except Exception: pass
try:
    mark('python-start uid=%s'%os.getuid())
    env=os.environ.copy()
    env.update(DISPLAY=':0', HOME='/home/ribi', USER='ribi', LOGNAME='ribi',
               XDG_RUNTIME_DIR='/run/user/1000', XDG_CURRENT_DESKTOP='Ribi',
               XDG_SESSION_DESKTOP='ribi')
    os.setgroups([1000]); mark('groups=1000')
    os.setgid(1000); mark('gid=1000')
    os.setuid(1000); mark('uid=1000')
    os.environ.clear(); os.environ.update(env)
    mark('exec-session')
    os.execve('/usr/local/bin/ribi-user-desktop', ['/usr/local/bin/ribi-user-desktop'], env)
except Exception as e:
    mark('ERROR '+repr(e)); traceback.print_exc(file=sys.stderr); raise
