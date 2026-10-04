"""Logging facade and the safe subprocess / file helpers.

This module is part of the Ribi OS ISO builder package. Split out of the
original monolithic builder for readability; behaviour is unchanged.
"""

import hashlib
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional
from .config import BUILD_LOG, DIR_LOGS


class BuildLogger:
    @staticmethod
    def init():
        DIR_LOGS.mkdir(parents=True, exist_ok=True)
        handlers = [logging.StreamHandler(sys.stdout)]
        try:
            handlers.insert(0, logging.FileHandler(BUILD_LOG, mode="a", encoding="utf-8"))
        except PermissionError:
            print(f"[WARN] Build log is not writable: {BUILD_LOG}; continuing on stdout", file=sys.stderr)
        logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s", handlers=handlers)

    @staticmethod
    def step(step_num: int, total_steps: int, title: str):
        msg = f"\n\033[1;36m[{step_num}/{total_steps}] >>> {title}\033[0m"
        print(msg)
        logging.info(f"STAGE [{step_num}/{total_steps}]: {title}")

    @staticmethod
    def cic(action: str, resource: str, status: str):
        print(f"  \033[1;34m[C.I.C.]\033[0m {action:<8} | {resource:<35} -> \033[1;32m{status}\033[0m")
        logging.info(f"[C.I.C.] {action} | {resource} -> {status}")

    @staticmethod
    def info(msg: str):
        print(f"\033[1;32m[*] INFO:\033[0m {msg}")
        logging.info(msg)

    @staticmethod
    def warn(msg: str):
        print(f"\033[1;33m[!] WARN:\033[0m {msg}")
        logging.warning(msg)

    @staticmethod
    def error(msg: str):
        print(f"\033[1;31m[!] ERROR:\033[0m {msg}")
        logging.error(msg)


def run_cmd(
    cmd: List[str],
    check: bool = True,
    env: Optional[Dict[str, str]] = None,
    cwd: Optional[Path] = None,
    capture: bool = False,
) -> subprocess.CompletedProcess:
    cmd_str = " ".join(str(c) for c in cmd)
    logging.info(f"EXEC: {cmd_str} (cwd={cwd})")
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    try:
        proc = subprocess.run(
            cmd,
            check=check,
            cwd=cwd,
            env=merged_env,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            text=True,
        )
        return proc
    except subprocess.CalledProcessError as err:
        BuildLogger.error(f"Command execution failed (Code {err.returncode}): {cmd_str}")
        if err.stdout:
            logging.error(f"STDOUT:\n{err.stdout}")
        if err.stderr:
            logging.error(f"STDERR:\n{err.stderr}")
        raise


def write_file(path: Path, content: str, mode: int = 0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content.strip() + "\n")
    path.chmod(mode)
    logging.info(f"Generated file: {path} (mode={oct(mode)})")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
