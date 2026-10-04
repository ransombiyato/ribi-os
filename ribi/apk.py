"""Pure-Python APKINDEX parsing, dependency resolution, integrity checks, and
safe archive extraction.

This module is part of the Ribi OS ISO builder package. Split out of the
original monolithic builder for readability; behaviour is unchanged.
"""

import copy
import base64
import hashlib
import io
import os
import re
import shutil
import subprocess
import tarfile
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def verify_apkindex_signature(index_path: Path, key_dir: Path):
    """Verify an Alpine v2 signed APKINDEX.tar.gz.

    The signed index is two concatenated gzip streams: a signature tar segment
    followed by the original unsigned APKINDEX.tar.gz stream. The RSA signature
    covers the exact bytes of that second gzip stream.
    """
    raw = index_path.read_bytes()
    streams = _gzip_streams(raw)
    if len(streams) != 2:
        raise RuntimeError(
            f"{index_path.name}: expected 2 gzip streams, found {len(streams)}"
        )
    sig_payload = streams[0][1]
    unsigned_index_gz = streams[1][0]

    sig_name = None
    signature = None
    with tarfile.open(fileobj=io.BytesIO(sig_payload), mode="r:", ignore_zeros=True) as tar:
        for member in tar.getmembers():
            name = Path(member.name).name
            if name.startswith(".SIGN.RSA."):
                sig_name = name
                f = tar.extractfile(member)
                signature = f.read() if f else None
                break
    if not sig_name or not signature:
        raise RuntimeError(f"{index_path.name}: missing repository signature member")
    if not shutil.which("openssl"):
        raise RuntimeError("openssl is required to verify Alpine repository signatures")

    key_name = sig_name[len(".SIGN.RSA."):]
    key = key_dir / key_name
    if not key.is_file():
        raise RuntimeError(
            f"{index_path.name}: trusted repository key missing: {key_name}"
        )

    import tempfile
    with tempfile.TemporaryDirectory(prefix="ribi-index-") as td:
        data = Path(td) / "APKINDEX.tar.gz"
        sig = Path(td) / "signature"
        data.write_bytes(unsigned_index_gz)
        sig.write_bytes(signature)
        proc = subprocess.run(
            ["openssl", "dgst", "-sha1", "-verify", str(key),
             "-signature", str(sig), str(data)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if proc.returncode != 0 or "Verified OK" not in proc.stdout:
            raise RuntimeError(
                f"{index_path.name}: repository signature verification failed"
            )

def determine_dest_subdir(file_path: Path) -> str:
    """
    Determines canonical target subdirectory (usr/bin, usr/sbin, bin, sbin)
    using explicit path-component logic without naive substring ambiguity.
    """
    parts = [p.lower() for p in file_path.parts]
    if len(parts) >= 2 and parts[-2] == "bin" and len(parts) >= 3 and parts[-3] == "usr":
        return "usr/bin"
    if len(parts) >= 2 and parts[-2] == "sbin" and len(parts) >= 3 and parts[-3] == "usr":
        return "usr/sbin"
    if len(parts) >= 2 and parts[-2] == "sbin":
        return "sbin"
    if len(parts) >= 2 and parts[-2] == "bin":
        return "bin"

    path_str = str(file_path)
    if "/usr/bin" in path_str:
        return "usr/bin"
    if "/usr/sbin" in path_str:
        return "usr/sbin"
    if "/sbin" in path_str:
        return "sbin"
    if "/bin" in path_str:
        return "bin"
    return "usr/bin"


def _gzip_streams(raw: bytes) -> List[Tuple[bytes, bytes]]:
    """Return [(compressed_stream, decompressed_payload), ...] for concatenated gzip data."""
    out=[]; pos=0
    while pos < len(raw):
        if raw[pos:pos+2] != b"\x1f\x8b":
            break
        d=zlib.decompressobj(16 + zlib.MAX_WBITS)
        dec=d.decompress(raw[pos:]) + d.flush()
        unused=d.unused_data
        consumed=len(raw[pos:]) - len(unused)
        if consumed <= 0: break
        out.append((raw[pos:pos+consumed], dec)); pos += consumed
    return out


def verify_apk_integrity(archive_path: Path, expected_csum: str, key_dir: Optional[Path] = None):
    """Verify Alpine APK control checksum, datahash and, when possible, RSA signature."""
    raw=archive_path.read_bytes()
    streams=_gzip_streams(raw)
    if len(streams) < 3:
        raise RuntimeError(f"{archive_path.name}: APK does not contain signature/control/data gzip streams")
    # streams[i] is (compressed_segment, decompressed_payload). The compressed
    # segment is what checksums/signatures are computed over; the decompressed
    # payload is what must be fed to tarfile (mode="r:" expects plain tar bytes,
    # not gzip -- passing compressed bytes here silently yields an empty archive
    # instead of raising, which is what caused the ".PKGINFO not found" KeyError).
    signature_gz, control_gz, data_gz = streams[0][0], streams[1][0], streams[-1][0]
    signature_tar_bytes, control_tar_bytes = streams[0][1], streams[1][1]
    if expected_csum:
        try:
            encoded=expected_csum[2:] if expected_csum.startswith("Q1") else expected_csum
            expected_sha1=base64.b64decode(encoded)
            if len(expected_sha1)==20 and hashlib.sha1(control_gz).digest()!=expected_sha1:
                raise RuntimeError(f"{archive_path.name}: APKINDEX control checksum mismatch")
        except Exception as e:
            raise RuntimeError(f"{archive_path.name}: invalid APKINDEX checksum: {e}")
    pkginfo=None; signature_name=None; signature_bytes=None
    with tarfile.open(fileobj=io.BytesIO(signature_tar_bytes), mode="r:", ignore_zeros=True) as tar:
        for m in tar.getmembers():
            if m.name.startswith(".SIGN.RSA."):
                signature_name=m.name; f=tar.extractfile(m); signature_bytes=f.read() if f else None
                break
    with tarfile.open(fileobj=io.BytesIO(control_tar_bytes), mode="r:", ignore_zeros=True) as tar:
        f=tar.extractfile(".PKGINFO")
        if f: pkginfo=f.read().decode("utf-8", errors="replace")
    if not pkginfo:
        raise RuntimeError(f"{archive_path.name}: missing .PKGINFO control metadata")
    datahash=None
    for line in pkginfo.splitlines():
        if line.startswith("datahash = "):
            datahash=line.split(" = ",1)[1].strip(); break
    if not datahash:
        raise RuntimeError(f"{archive_path.name}: .PKGINFO has no datahash")
    if hashlib.sha256(data_gz).hexdigest().lower()!=datahash.lower():
        raise RuntimeError(f"{archive_path.name}: datahash mismatch")
    if not signature_name or not signature_bytes or not key_dir:
        raise RuntimeError(f"{archive_path.name}: signed APK is required")
    if signature_name and signature_bytes and key_dir and shutil.which("openssl"):
        key_name=signature_name[len(".SIGN.RSA."):]
        key=key_dir / key_name
        if not key.exists():
            raise RuntimeError(f"{archive_path.name}: trusted Alpine signing key missing: {key_name}")
        import tempfile
        with tempfile.TemporaryDirectory(prefix="ribi-apk-") as td:
            control=Path(td)/"control.gz"; sig=Path(td)/"signature"
            control.write_bytes(control_gz); sig.write_bytes(signature_bytes)
            proc=subprocess.run(["openssl","dgst","-sha1","-verify",str(key),"-signature",str(sig),str(control)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc.returncode != 0 or "Verified OK" not in proc.stdout:
                raise RuntimeError(f"{archive_path.name}: APK RSA signature verification failed")

def safe_tar_extract(archive_path: Path, target_dir: Path):
    """Safely extract tar archives while preserving safe Alpine rootfs links.

    Python 3.13's ``tarfile`` ``filter="data"`` intentionally rejects absolute
    symlinks. Alpine minirootfs archives legitimately contain links such as
    ``usr/bin/yes -> /bin/busybox``; inside a rootfs that means ``<root>/bin``.
    We therefore validate the link *within the extraction root* and rewrite a
    safe absolute link to the equivalent relative link before extraction.

    Archive paths, symlinks, and hardlinks are checked lexically so a malicious
    archive cannot escape ``target_dir``. Device nodes, FIFOs, and other special
    files are rejected because this function is used for untrusted downloaded
    staging archives, not for creating a live /dev tree.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    norm_target = os.path.abspath(str(target_dir))
    target_prefix = norm_target if norm_target.endswith(os.sep) else norm_target + os.sep

    def inside_target(path: str) -> bool:
        return path == norm_target or path.startswith(target_prefix)

    def clean_archive_path(name: str) -> str:
        # Tar member names always use '/', even on POSIX hosts. Treat backslash
        # as a separator too so crafted Windows-style traversal cannot bypass it.
        clean = os.path.normpath(name.replace("\\", "/")).lstrip("/\\")
        if clean in ("", "."):
            return "."
        resolved = os.path.abspath(os.path.join(norm_target, clean))
        if not inside_target(resolved):
            raise RuntimeError(f"Security Alert: Path traversal entry blocked: {name}")
        return clean

    def _extract_tar(tar: tarfile.TarFile):
        members = tar.getmembers()
        member_names = set()

        # Validate the complete archive namespace first. Hardlinks may legally point
        # forward to a later member, so extraction itself is performed in two passes:
        # ordinary members first, hardlinks second.
        cleaned_members = []
        all_names = set()
        for original in members:
            if original.name in (".PKGINFO", ".INSTALL", ".PRE-INSTALL", ".POST-INSTALL") or original.name.startswith(".SIGN."):
                continue
            clean_name = clean_archive_path(original.name)
            all_names.add(clean_name)
            cleaned_members.append((original, clean_name))

        for original, clean_name in cleaned_members:
            if original.name in (".PKGINFO", ".INSTALL", ".PRE-INSTALL", ".POST-INSTALL") or original.name.startswith(".SIGN."):
                continue

            member = copy.copy(original)
            member.name = clean_name
            norm_member = os.path.abspath(os.path.join(norm_target, clean_name))
            member_names.add(clean_name)

            # Do not allow special files from a downloaded archive to become
            # device nodes/FIFOs on the host filesystem.
            if member.ischr() or member.isblk() or member.isfifo():
                raise RuntimeError(f"Security Alert: Special file blocked: {original.name}")

            if member.islnk():
                # Tar hardlink names refer to archive members. Normalize both
                # absolute and relative spellings and require the target to stay
                # inside the archive root.
                raw_link = member.linkname.replace("\\", "/")
                clean_link = raw_link.lstrip("/")
                clean_link = clean_archive_path(clean_link)
                if clean_link not in all_names:
                    # A hardlink target must exist somewhere in the archive namespace.
                    raise RuntimeError(
                        f"Security Alert: Hardlink target missing: {original.linkname} -> {original.name}"
                    )
                member.linkname = clean_link

            elif member.issym():
                raw_link = member.linkname.replace("\\", "/")
                if raw_link.startswith("/"):
                    # Absolute links in a rootfs archive are rooted at the
                    # archive root, not the host root. Convert them to a relative
                    # link so the extracted sysroot retains the intended meaning.
                    link_target = os.path.abspath(os.path.join(norm_target, raw_link.lstrip("/")))
                    if not inside_target(link_target):
                        raise RuntimeError(
                            f"Security Alert: Symlink escape blocked: {member.linkname}"
                        )
                    member.linkname = os.path.relpath(link_target, os.path.dirname(norm_member))
                else:
                    link_target = os.path.abspath(os.path.join(os.path.dirname(norm_member), raw_link))
                    if not inside_target(link_target):
                        raise RuntimeError(
                            f"Security Alert: Symlink escape blocked: {member.linkname}"
                        )
                    # Preserve relative links exactly when they are already safe.
                    member.linkname = raw_link

            # Ensure no pre-existing symlink in an intermediate component can
            # redirect extraction outside the target. Replace such components
            # with real directories before extracting the member.
            rel_parts = os.path.relpath(norm_member, norm_target).split(os.sep)
            curr = norm_target
            for part in rel_parts[:-1]:
                curr = os.path.join(curr, part)
                if os.path.islink(curr):
                    os.unlink(curr)
                    os.makedirs(curr, exist_ok=True)
                elif not os.path.exists(curr):
                    os.makedirs(curr, exist_ok=True)
                elif not os.path.isdir(curr):
                    raise RuntimeError(f"Security Alert: Non-directory path component: {curr}")

            # The extraction root and directory members may already exist.  The
            # root is created before extraction, and package archives commonly
            # contain parent-directory entries that can also have been created
            # while processing another member.  Existing real directories are
            # therefore safe to reuse; existing non-directories are replaced.
            if os.path.lexists(norm_member):
                if member.isdir() and os.path.isdir(norm_member) and not os.path.islink(norm_member):
                    continue
                if norm_member == norm_target and os.path.isdir(norm_member) and not os.path.islink(norm_member):
                    continue
                if os.path.isdir(norm_member) and not os.path.islink(norm_member):
                    raise RuntimeError(f"Archive collision with existing directory: {norm_member}")
                os.unlink(norm_member)

            # All security checks above are stricter than tarfile's data filter,
            # and the latter rejects legitimate rewritten Alpine links on Python
            # 3.13. Extract ordinary members now; defer hardlinks until their targets
            # have been materialized.
            if not member.islnk():
                tar.extract(member, path=target_dir)

        # Second pass: hardlinks. Every target was validated against all_names above,
        # and now exists if it is an ordinary archive member.
        for original, clean_name in cleaned_members:
            if not original.islnk():
                continue
            member = copy.copy(original)
            raw_link = member.linkname.replace("\\", "/")
            clean_link = raw_link.lstrip("/")
            clean_link = clean_archive_path(clean_link)
            member.name = clean_name
            member.linkname = clean_link
            norm_member = os.path.abspath(os.path.join(norm_target, clean_name))
            # Recreate the same parent-component protection for the deferred pass.
            rel_parts = os.path.relpath(norm_member, norm_target).split(os.sep)
            curr = norm_target
            for part in rel_parts[:-1]:
                curr = os.path.join(curr, part)
                if os.path.islink(curr):
                    os.unlink(curr); os.makedirs(curr, exist_ok=True)
                elif not os.path.exists(curr):
                    os.makedirs(curr, exist_ok=True)
                elif not os.path.isdir(curr):
                    raise RuntimeError(f"Security Alert: Non-directory path component: {curr}")
            if os.path.lexists(norm_member):
                if os.path.isdir(norm_member) and not os.path.islink(norm_member):
                    raise RuntimeError(f"Archive collision with existing directory: {norm_member}")
                os.unlink(norm_member)
            tar.extract(member, path=target_dir)

    def _extract_gzip_streams(raw_bytes: bytes) -> int:
        offset = 0
        streams_found = 0
        raw_len = len(raw_bytes)
        while offset < raw_len:
            if raw_bytes[offset:offset + 2] != b"\x1f\x8b":
                raise RuntimeError(f"{archive_path.name}: trailing non-gzip data after {streams_found} stream(s)")
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
            try:
                decompressed = d.decompress(raw_bytes[offset:]) + d.flush()
            except zlib.error as exc:
                raise RuntimeError(f"{archive_path.name}: invalid gzip stream: {exc}") from exc
            if not decompressed or not d.eof:
                raise RuntimeError(f"{archive_path.name}: truncated gzip stream")
            consumed = raw_len - offset - len(d.unused_data)
            if consumed <= 0:
                raise RuntimeError(f"{archive_path.name}: gzip parser made no progress")
            with tarfile.open(fileobj=io.BytesIO(decompressed), mode="r:", ignore_zeros=True) as tar:
                _extract_tar(tar)
            streams_found += 1
            offset += consumed
        return streams_found

    # Alpine APKs may contain concatenated gzip streams. The normal minirootfs
    # is a single .tar.gz, so only use multi-stream handling for .apk files.
    if archive_path.name.endswith(".apk"):
        raw_bytes = archive_path.read_bytes()
        _extract_gzip_streams(raw_bytes)
        return

    with tarfile.open(archive_path, "r:*") as tar:
        _extract_tar(tar)


def _apk_bare_token(token: str) -> str:
    """Return the package/provide name from an Alpine dependency expression."""
    token = token.strip()
    if token.startswith("!"):
        token = token[1:]
    # Alpine uses =, <, <=, >, >=, ~ and ~= forms.
    m = re.match(r"^([^<>=~]+?)(?:>=|<=|~=|=|>|<|~).*$", token)
    return m.group(1) if m else token


def _apk_constraint(token: str) -> Tuple[str, Optional[str]]:
    m = re.match(r"^[^<>=~]+?(>=|<=|~=|=|>|<|~)(.+)$", token.strip())
    return (m.group(1), m.group(2)) if m else ("", None)


def _apk_version_key(version: str):
    """Deterministic Alpine-ish version key; exact equality remains the safest check."""
    parts = re.split(r"([0-9]+|[A-Za-z]+|-r[0-9]+|~)", version)
    key=[]
    order={"alpha":-5,"beta":-4,"pre":-3,"rc":-2,"cvs":1,"svn":2,"git":3,"hg":4,"p":5}
    for x in parts:
        if not x: continue
        if x.isdigit(): key.append((1,int(x)))
        elif x.startswith("-r") and x[2:].isdigit(): key.append((3,int(x[2:])))
        elif x in order: key.append((2,order[x]))
        else: key.append((2,x))
    return key


def _apk_satisfies(version: str, op: str, wanted: Optional[str]) -> bool:
    if not op or wanted is None:
        return True
    if op == "=": return version == wanted
    a,b=_apk_version_key(version),_apk_version_key(wanted)
    if op == ">": return a>b
    if op == ">=": return a>=b
    if op == "<": return a<b
    if op == "<=": return a<=b
    if op in ("~", "~="):
        # Compatible-release dependency: exact major/minor prefix when possible.
        vp=re.match(r"^(\d+(?:\.\d+)*)", version)
        wp=re.match(r"^(\d+(?:\.\d+)*)", wanted)
        return bool(vp and wp and vp.group(1).split(".")[:2] == wp.group(1).split(".")[:2] and a>=b)
    return False


def parse_apkindex(raw_content: str, pkg_versions: Dict[str, str],
                   pkg_depends: Dict[str, List[str]], provides_map: Dict[str, str],
                   pkg_checksums: Optional[Dict[str, str]] = None,
                   pkg_arch: Optional[Dict[str, str]] = None,
                   pkg_install_if: Optional[Dict[str, List[str]]] = None):
    """Parse Alpine APKINDEX v2 records."""
    for block in raw_content.split("\n\n"):
        name=version=None; depends=[]; provides=[]; checksum=None; arch=None; install_if=[]
        for line in block.splitlines():
            if line.startswith("C:"): checksum=line[2:].strip()
            elif line.startswith("P:"): name=line[2:].strip()
            elif line.startswith("V:"): version=line[2:].strip()
            elif line.startswith("A:"): arch=line[2:].strip()
            elif line.startswith("D:"): depends=[d for d in line[2:].strip().split() if d]
            elif line.startswith("p:"): provides=[p for p in line[2:].strip().split() if p]
            elif line.startswith("i:"): install_if=[d for d in line[2:].strip().split() if d]
        if not name or not version: continue
        if name not in pkg_versions:
            pkg_versions[name]=version; pkg_depends[name]=depends
            if pkg_checksums is not None: pkg_checksums[name]=checksum or ""
            if pkg_arch is not None: pkg_arch[name]=arch or ""
            if pkg_install_if is not None: pkg_install_if[name]=install_if
        provides_map.setdefault(name,name)
        for prov in provides:
            provides_map.setdefault(_apk_bare_token(prov),name)


def resolve_apk_closure(seed_packages: List[str], pkg_versions: Dict[str, str],
                        pkg_depends: Dict[str, List[str]], provides_map: Dict[str, str]) -> List[str]:
    """Resolve a strict dependency closure, rejecting unsatisfied versioned dependencies."""
    resolved=[]; seen=set(); queue=list(seed_packages); unresolved=[]
    while queue:
        token=queue.pop(0).strip()
        if token.startswith("!"): continue
        bare=_apk_bare_token(token); op,wanted=_apk_constraint(token)
        pkg_name=provides_map.get(bare, bare if bare in pkg_versions else None)
        if pkg_name is None or not _apk_satisfies(pkg_versions[pkg_name],op,wanted):
            unresolved.append(token); continue
        if pkg_name in seen: continue
        seen.add(pkg_name); resolved.append(pkg_name)
        queue.extend(d for d in pkg_depends.get(pkg_name,[]) if not d.startswith("!"))
    if unresolved:
        raise RuntimeError("Unresolved/unsatisfied Alpine dependencies: " + ", ".join(sorted(set(unresolved))[:30]))
    return resolved
