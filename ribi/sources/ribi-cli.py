#!/usr/bin/env python3
import sys
import subprocess

HELP_TEXT = """
===============================================================================
 RIBI OS - Unified System & Package Controller
===============================================================================
 Usage:
   ribi install <name|file.rpk> Install a repository package or native .rpk
   ribi search <name>       Search configured compatibility repositories
   ribi update              Refresh compatibility repository indexes
   ribi remove <pkg>        Remove installed Ribi package
   ribi list                List installed native packages
   ribi service <cmd>       Manage services via ribisvc
   ribi sysinfo             Display hardware, memory, and kernel status
   ribi version             Show Ribi OS release information
===============================================================================
"""

def main():
    if len(sys.argv) < 2:
        print(HELP_TEXT)
        sys.exit(0)

    action = sys.argv[1]
    if action == "install" and len(sys.argv) > 2:
        sys.exit(subprocess.run(["ribi-pkg", "install", sys.argv[2]]).returncode)
    elif action == "remove" and len(sys.argv) > 2:
        sys.exit(subprocess.run(["ribi-pkg", "remove", sys.argv[2]]).returncode)
    elif action in ("search", "update"):
        sys.exit(subprocess.run(["ribi-pkg"] + sys.argv[1:]).returncode)
    elif action == "list":
        sys.exit(subprocess.run(["ribi-pkg", "list"]).returncode)
    elif action == "service" and len(sys.argv) > 2:
        sys.exit(subprocess.run(["ribisvc"] + sys.argv[2:]).returncode)
    elif action == "sysinfo":
        print("=== Ribi OS System Status ===")
        subprocess.run(["uname", "-a"])
        subprocess.run(["uptime"])
    elif action in ("version", "--version", "-v"):
        print("RIBI OS x86_64")
    else:
        print(HELP_TEXT)

if __name__ == "__main__":
    main()
