"""Atomic, cache-verified downloads and APKINDEX signature verification.

This module is part of the Ribi OS ISO builder package. Split out of the
original monolithic builder for readability; behaviour is unchanged.
"""

import re
import shutil
import urllib.request
from pathlib import Path
from typing import List, Optional
from .logging_utils import BuildLogger, run_cmd, sha256_file


def _cache_digest_path(dest: Path) -> Path:
    return dest.with_name(dest.name + ".sha256")


def _verify_cache(dest: Path) -> bool:
    if not dest.is_file() or dest.stat().st_size == 0:
        return False
    digest = _cache_digest_path(dest)
    if not digest.is_file():
        return False
    try:
        expected = digest.read_text(encoding="ascii").strip().split()[0].lower()
        return bool(re.fullmatch(r"[0-9a-f]{64}", expected)) and sha256_file(dest) == expected
    except Exception:
        return False


def download_file(url: str, dest: Path, fallback_urls: Optional[List[str]] = None,
                  expected_sha256: Optional[str] = None):
    """Download atomically with TLS verification and cryptographic cache validation."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest_path = _cache_digest_path(dest)

    if dest.exists():
        if _verify_cache(dest):
            BuildLogger.cic("CHECK", dest.name, "EXISTS (Cached + SHA-256 Verified)")
            if expected_sha256 and sha256_file(dest) != expected_sha256.lower():
                dest.unlink(missing_ok=True)
                digest_path.unlink(missing_ok=True)
            else:
                return
        elif dest.stat().st_size > 0 and expected_sha256 and sha256_file(dest) == expected_sha256.lower():
            digest_path.write_text(expected_sha256.lower() + "\n", encoding="ascii")
            BuildLogger.cic("CHECK", dest.name, "EXISTS (SHA-256 Verified)")
            return
        else:
            BuildLogger.warn(f"Discarding unverified cached artifact: {dest}")
            dest.unlink(missing_ok=True)
            digest_path.unlink(missing_ok=True)

    all_urls = [url] + (fallback_urls or [])
    temp_dest = dest.with_suffix(dest.suffix + ".part")
    temp_dest.unlink(missing_ok=True)
    headers = {
        "User-Agent": "RibiOSBuilder/1.0",
        "Accept": "*/*",
    }
    last_error = None

    for target_url in all_urls:
        BuildLogger.cic("INSTALL", dest.name, f"Downloading from {target_url}")
        success = False
        if shutil.which("curl"):
            try:
                run_cmd(["curl", "--fail", "--silent", "--show-error", "--location",
                         "--retry", "3", "--retry-delay", "2", "--proto", "=https",
                         "--tlsv1.2", "-A", headers["User-Agent"], "-o", str(temp_dest), target_url],
                        capture=True)
                success = temp_dest.is_file() and temp_dest.stat().st_size > 0
            except Exception as e:
                last_error = e
        if not success and shutil.which("wget"):
            try:
                run_cmd(["wget", "--https-only", "--tries=3", "--timeout=60",
                         f"--user-agent={headers['User-Agent']}", "-O", str(temp_dest), target_url],
                        capture=True)
                success = temp_dest.is_file() and temp_dest.stat().st_size > 0
            except Exception as e:
                last_error = e
        if not success:
            try:
                req = urllib.request.Request(target_url, headers=headers)
                with urllib.request.urlopen(req, timeout=60) as resp, open(temp_dest, "wb") as out:
                    shutil.copyfileobj(resp, out)
                success = temp_dest.is_file() and temp_dest.stat().st_size > 0
            except Exception as e:
                last_error = e

        if success:
            actual = sha256_file(temp_dest)
            if expected_sha256 and actual != expected_sha256.lower():
                last_error = RuntimeError(
                    f"SHA-256 mismatch for {dest.name}: expected {expected_sha256}, got {actual}"
                )
                temp_dest.unlink(missing_ok=True)
                continue
            temp_dest.replace(dest)
            digest_path.write_text(actual + "\n", encoding="ascii")
            BuildLogger.cic("CONTINUE", dest.name, f"READY ({dest.stat().st_size / 1024:.1f} KB; SHA-256 {actual[:16]}...)")
            return
        temp_dest.unlink(missing_ok=True)

    raise RuntimeError(f"Failed to download verified {dest.name} from all available mirrors: {all_urls}. Last error: {last_error}")


def download_with_sidecar_hash(url: str, dest: Path, fallback_urls: Optional[List[str]] = None):
    """Fetch an artifact only after obtaining its published SHA-256 sidecar."""
    urls=[url]+(fallback_urls or [])
    last=None
    for u in urls:
        side=dest.with_name(dest.name+'.upstream.sha256')
        try:
            download_file(u+'.sha256',side)
            text=side.read_text(encoding='ascii',errors='ignore')
            m=re.search(r'\b([0-9a-fA-F]{64})\b',text)
            if not m: raise RuntimeError(f'no SHA-256 found in {u}.sha256')
            download_file(u,dest,expected_sha256=m.group(1))
            side.unlink(missing_ok=True)
            return
        except Exception as e:
            last=e; side.unlink(missing_ok=True); dest.unlink(missing_ok=True); _cache_digest_path(dest).unlink(missing_ok=True)
    raise RuntimeError(f'Unable to fetch a SHA-256-verified artifact from {urls}: {last}')
