"""Static binary auditors: ELF/PE architecture, interpreters, DT_NEEDED,
bzImage, SquashFS, and ISO El Torito inspection.

This module is part of the Ribi OS ISO builder package. Split out of the
original monolithic builder for readability; behaviour is unchanged.
"""

import logging
import re
import struct
from pathlib import Path
from typing import List, Optional


def get_elf_arch(file_path: Path) -> Optional[str]:
    """Reads ELF magic and machine ID in pure Python."""
    if not file_path.is_file() or file_path.stat().st_size < 52:
        return None
    try:
        with open(file_path, "rb") as f:
            magic = f.read(4)
            if magic != b"\x7fELF":
                return None
            elf_class = ord(f.read(1))
            data_encoding = ord(f.read(1))
            f.seek(18)
            e_machine = f.read(2)
            if data_encoding == 1:
                machine_id = struct.unpack("<H", e_machine)[0]
            else:
                machine_id = struct.unpack(">H", e_machine)[0]

            if elf_class == 2 and machine_id == 62:
                return "x86_64"
            elif elf_class == 2 and machine_id == 183:
                return "aarch64"
            elif elf_class == 1 and machine_id == 3:
                return "i386"
            elif elf_class == 1 and machine_id == 40:
                return "arm"
            return f"other({machine_id})"
    except Exception:
        return None


def is_elf_x86_64(file_path: Path) -> bool:
    return get_elf_arch(file_path) == "x86_64"


def is_efi_x86_64(file_path: Path) -> bool:
    """Verifies x86_64 PE32+ executable (EFI application binary)."""
    if not file_path.is_file() or file_path.stat().st_size < 0x100:
        return False
    try:
        with open(file_path, "rb") as f:
            mz = f.read(2)
            if mz != b"MZ":
                return False
            f.seek(0x3C)
            pe_offset_bytes = f.read(4)
            if len(pe_offset_bytes) < 4:
                return False
            pe_offset = struct.unpack("<I", pe_offset_bytes)[0]
            if pe_offset < 0x40 or pe_offset > file_path.stat().st_size - 6:
                return False
            f.seek(pe_offset)
            pe_sig = f.read(4)
            if pe_sig != b"PE\x00\x00":
                return False
            machine_bytes = f.read(2)
            if len(machine_bytes) < 2:
                return False
            machine = struct.unpack("<H", machine_bytes)[0]
            return machine == 0x8664
    except Exception:
        return False


def get_elf_interpreter(file_path: Path) -> Optional[str]:
    """Extracts PT_INTERP dynamic linker path directly from 64-bit ELF headers."""
    if not is_elf_x86_64(file_path):
        return None
    try:
        with open(file_path, "rb") as f:
            hdr = f.read(64)
            if len(hdr) < 64 or hdr[:4] != b"\x7fELF":
                return None
            e_phoff = struct.unpack("<Q", hdr[32:40])[0]
            e_phentsize = struct.unpack("<H", hdr[54:56])[0]
            e_phnum = struct.unpack("<H", hdr[56:58])[0]

            f.seek(e_phoff)
            for _ in range(e_phnum):
                phdr = f.read(e_phentsize)
                if len(phdr) < 56:
                    break
                p_type = struct.unpack("<I", phdr[0:4])[0]
                if p_type == 3:  # PT_INTERP
                    p_offset = struct.unpack("<Q", phdr[8:16])[0]
                    p_filesz = struct.unpack("<Q", phdr[32:40])[0]
                    f.seek(p_offset)
                    raw_interp = f.read(p_filesz).split(b"\x00")[0]
                    return raw_interp.decode("utf-8", errors="replace")
    except Exception:
        pass
    return None


def get_elf_needed_libraries(file_path: Path) -> List[str]:
    """Pure-Python DT_NEEDED dynamic library name parser for x86_64 ELFs."""
    needed: List[str] = []
    if not is_elf_x86_64(file_path):
        return needed
    try:
        with open(file_path, "rb") as f:
            hdr = f.read(64)
            if len(hdr) < 64 or hdr[:4] != b"\x7fELF":
                return needed

            e_phoff = struct.unpack("<Q", hdr[32:40])[0]
            e_phentsize = struct.unpack("<H", hdr[54:56])[0]
            e_phnum = struct.unpack("<H", hdr[56:58])[0]

            pt_dynamic_offset = None
            pt_dynamic_filesz = 0
            program_headers = []

            f.seek(e_phoff)
            for _ in range(e_phnum):
                phdr = f.read(e_phentsize)
                if len(phdr) < 56:
                    break
                p_type = struct.unpack("<I", phdr[0:4])[0]
                p_offset = struct.unpack("<Q", phdr[8:16])[0]
                p_vaddr = struct.unpack("<Q", phdr[16:24])[0]
                p_filesz = struct.unpack("<Q", phdr[32:40])[0]
                program_headers.append((p_vaddr, p_offset, p_filesz))

                if p_type == 2:  # PT_DYNAMIC
                    pt_dynamic_offset = p_offset
                    pt_dynamic_filesz = p_filesz

            if pt_dynamic_offset is None:
                return needed

            f.seek(pt_dynamic_offset)
            dyn_data = f.read(pt_dynamic_filesz)
            dyn_entries = []
            strtab_vaddr = None

            for i in range(0, len(dyn_data) - 15, 16):
                d_tag = struct.unpack("<q", dyn_data[i:i+8])[0]
                d_val = struct.unpack("<Q", dyn_data[i+8:i+16])[0]
                if d_tag == 0:
                    break
                elif d_tag == 1:
                    dyn_entries.append(d_val)
                elif d_tag == 5:
                    strtab_vaddr = d_val

            if strtab_vaddr is None or not dyn_entries:
                return needed

            strtab_offset = None
            for p_vaddr, p_offset, p_filesz in program_headers:
                if p_vaddr <= strtab_vaddr < p_vaddr + p_filesz:
                    strtab_offset = p_offset + (strtab_vaddr - p_vaddr)
                    break

            if strtab_offset is None:
                return needed

            for name_idx in dyn_entries:
                f.seek(strtab_offset + name_idx)
                s_bytes = bytearray()
                while True:
                    b = f.read(1)
                    if not b or b == b"\x00":
                        break
                    s_bytes.extend(b)
                if s_bytes:
                    needed.append(s_bytes.decode("utf-8", errors="replace"))
    except Exception as err:
        logging.debug(f"ELF parse warning on {file_path}: {err}")
    return needed


def is_linux_bzimage(file_path: Path) -> bool:
    """Verifies Linux bzImage boot header signature (HdrS at offset 0x202)."""
    if not file_path.is_file() or file_path.stat().st_size < 1024:
        return False
    try:
        with open(file_path, "rb") as f:
            f.seek(0x202)
            return f.read(4) == b"HdrS"
    except Exception:
        return False


def is_squashfs(file_path: Path) -> bool:
    """Verifies SquashFS 4.0 magic signature (hsqs)."""
    if not file_path.is_file() or file_path.stat().st_size < 4096:
        return False
    try:
        with open(file_path, "rb") as f:
            return f.read(4) == b"hsqs"
    except Exception:
        return False


def has_el_torito_boot_record(iso_path: Path) -> bool:
    """Verifies ISO 9660 PVD and El Torito Boot Record Volume Descriptor."""
    if not iso_path.is_file() or iso_path.stat().st_size < 0x10000:
        return False
    try:
        with open(iso_path, "rb") as f:
            # Check PVD at sector 16 (0x8000)
            f.seek(0x8000)
            pvd = f.read(2048)
            if len(pvd) < 2048 or pvd[0] != 1 or pvd[1:6] != b"CD001":
                return False

            # Scan Volume Descriptors between sector 17 and 32
            for sector_num in range(17, 33):
                f.seek(sector_num * 2048)
                desc = f.read(2048)
                if len(desc) < 2048:
                    break
                desc_type = desc[0]
                if desc_type == 255:
                    break
                if desc_type == 0 and desc[1:6] == b"CD001":
                    boot_sys_id = desc[7:39].rstrip(b" \x00")
                    if b"EL TORITO SPECIFICATION" in boot_sys_id:
                        return True
    except Exception as e:
        logging.debug(f"El Torito validation exception: {e}")
    return False


def has_uefi_el_torito_entry(iso_path: Path) -> bool:
    """Verifies that the ISO El Torito catalog contains an EFI-platform boot entry."""
    if not iso_path.is_file():
        return False
    try:
        import subprocess
        result = subprocess.run(
            ["xorriso", "-indev", str(iso_path), "-report_el_torito", "plain"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
        out = result.stdout
        if result.returncode != 0:
            return False

        # xorriso reports UEFI El Torito entries with platform identifier EFI.
        for line in out.splitlines():
            normalized = line.strip().upper()
            if normalized.startswith("PLATFORM ID") and ("EFI" in normalized or "UEFI" in normalized):
                return True
            if "PLATFORM:" in normalized and ("EFI" in normalized or "UEFI" in normalized):
                return True
            # xorriso 1.5.x uses a compact table format whose platform is
            # reported on the boot-image row instead of a PLATFORM column.
            if re.search(r"EL TORITO BOOT IMG\s*:.*\b(?:EFI|UEFI)\b", normalized):
                return True
        return False
    except Exception as e:
        logging.debug(f"UEFI El Torito validation exception: {e}")
        return False
