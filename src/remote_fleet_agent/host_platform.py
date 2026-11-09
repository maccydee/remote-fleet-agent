"""Host OS services. Endpoint platform selection remains a separate concern."""

from __future__ import annotations

import csv
import ctypes
import errno
import io
import os
import platform
import re
import stat
import subprocess
from functools import lru_cache
from pathlib import Path


def process_exists(pid):
    if type(pid) is not int or pid <= 0:
        return False
    if platform.system() == "Windows":
        return _windows_process_exists(pid)
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OverflowError):
        return False
    except PermissionError:
        return True
    return True


def _windows_process_exists(pid):
    # os.kill(pid, 0) invokes TerminateProcess on Windows. Query only instead.
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:
            return False
        if error == 5:
            return True  # Inaccessible is not evidence that a process died.
        raise ctypes.WinError(error)
    try:
        status = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(status)):
            raise ctypes.WinError(ctypes.get_last_error())
        return status.value == 259
    finally:
        kernel.CloseHandle(handle)


def restrict_private(path, *, directory=False):
    path = Path(path)
    validate_private_location(path)
    if platform.system() == "Windows":
        _windows_restrict(path, directory)
    else:
        if platform.system() == "Darwin":
            _macos_acl(path, clear=True)
        path.chmod(0o700 if directory else 0o600)
        if path.stat().st_mode & 0o077:
            raise PermissionError(
                f"Filesystem did not enforce private permissions: {path}"
            )


def validate_private_location(path):
    """Reject untrusted ownership and replaceable parents before touching storage."""
    path = Path(path).absolute()
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise ValueError("Private storage must not be a symbolic link or junction.")
    try:
        if getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("Private storage must not be a Windows reparse point.")
    except FileNotFoundError:
        pass
    if platform.system() == "Windows":
        trusted = {_windows_sid(), "S-1-5-18", "S-1-5-32-544"}
        # Windows system folders may be owned by TrustedInstaller.
        system_owners = trusted | {
            "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
        }
        for item in dict.fromkeys((path, *path.parents, *path.resolve().parents)):
            if not item.exists():
                continue
            owner, grants = _windows_security(item)
            if owner not in (trusted if item == path else system_owners):
                raise PermissionError(f"Private storage has an untrusted owner: {item}")
            if item != path and any(
                sid not in trusted and mask & 0x100D0040 for sid, mask in grants
            ):
                raise PermissionError(
                    f"Private storage parent allows replacement: {item}"
                )
        return
    uid = os.geteuid()
    for item in dict.fromkeys((path, *path.parents, *path.resolve().parents)):
        if not item.exists():
            continue
        info = item.stat()
        if info.st_uid not in ({uid} if item == path else {uid, 0}):
            raise PermissionError(f"Private storage has an untrusted owner: {item}")
        # Sticky parents (e.g. /tmp) protect an owned child's directory entry.
        if item != path and info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
            raise PermissionError(f"Private storage parent allows replacement: {item}")
        if item != path and platform.system() == "Darwin" and _macos_acl(item):
            raise PermissionError(
                f"Private storage parent ACL allows replacement: {item}"
            )


def _macos_acl(path, *, clear=False):
    """Remove extended grants on owned storage; reject writable parent ACLs.

    macOS extended ACLs can grant access despite mode 0700/0600. Parent
    permissions are inspected conservatively, without changing parent ACLs.
    """
    libc = ctypes.CDLL(None, use_errno=True)
    pointer = ctypes.c_void_p
    libc.acl_init.argtypes, libc.acl_init.restype = [ctypes.c_int], pointer
    libc.acl_get_file.argtypes, libc.acl_get_file.restype = (
        [ctypes.c_char_p, ctypes.c_int],
        pointer,
    )
    libc.acl_set_file.argtypes, libc.acl_set_file.restype = (
        [ctypes.c_char_p, ctypes.c_int, pointer],
        ctypes.c_int,
    )
    libc.acl_free.argtypes, libc.acl_free.restype = [pointer], ctypes.c_int
    libc.acl_get_entry.argtypes, libc.acl_get_entry.restype = (
        [pointer, ctypes.c_int, ctypes.POINTER(pointer)],
        ctypes.c_int,
    )
    libc.acl_get_tag_type.argtypes, libc.acl_get_tag_type.restype = (
        [pointer, ctypes.POINTER(ctypes.c_int)],
        ctypes.c_int,
    )
    libc.acl_get_permset_mask_np.argtypes, libc.acl_get_permset_mask_np.restype = (
        [pointer, ctypes.POINTER(ctypes.c_uint64)],
        ctypes.c_int,
    )
    acl = libc.acl_init(0) if clear else libc.acl_get_file(os.fsencode(path), 0x100)
    if not acl:
        if not clear and ctypes.get_errno() == errno.ENOENT and Path(path).exists():
            return False  # Darwin reports ENOENT when an existing object has no ACL.
        raise OSError(
            ctypes.get_errno(), "Could not inspect private storage ACL", str(path)
        )
    try:
        if clear:
            if libc.acl_set_file(os.fsencode(path), 0x100, acl):
                raise OSError(
                    ctypes.get_errno(),
                    "Could not remove extended storage grants",
                    str(path),
                )
            return False
        entry, tag, mask = pointer(), ctypes.c_int(), ctypes.c_uint64()
        index = 0
        while libc.acl_get_entry(acl, index, ctypes.byref(entry)) == 0:
            index = -1
            if libc.acl_get_tag_type(
                entry, ctypes.byref(tag)
            ) or libc.acl_get_permset_mask_np(entry, ctypes.byref(mask)):
                raise OSError(
                    ctypes.get_errno(), "Could not inspect parent ACL entry", str(path)
                )
            if tag.value == 1 and mask.value & 0x3050:
                return True  # delete, delete-child, write-security or change-owner
        return False
    finally:
        libc.acl_free(acl)


def _windows_security(path):
    """Read owner and effective inheritable write grants without invoking a shell."""
    from ctypes import wintypes

    class ACL_SIZE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    advapi.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(pointer),
        pointer,
        ctypes.POINTER(pointer),
        pointer,
        ctypes.POINTER(pointer),
    ]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.ConvertSidToStringSidW.argtypes = [pointer, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi.GetAclInformation.argtypes = [
        pointer,
        pointer,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    advapi.GetAclInformation.restype = wintypes.BOOL
    advapi.GetAce.argtypes = [pointer, wintypes.DWORD, ctypes.POINTER(pointer)]
    advapi.GetAce.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [pointer]
    kernel.LocalFree.restype = pointer

    def sid_text(sid):
        text = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return text.value
        finally:
            kernel.LocalFree(ctypes.cast(text, pointer))

    owner, acl, descriptor = pointer(), pointer(), pointer()
    error = advapi.GetNamedSecurityInfoW(
        str(path),
        1,
        5,
        ctypes.byref(owner),
        None,
        ctypes.byref(acl),
        None,
        ctypes.byref(descriptor),
    )
    if error:
        raise ctypes.WinError(error)
    try:
        if not acl.value:
            return sid_text(owner), [("Everyone", 0x10000000)]
        size = ACL_SIZE_INFORMATION()
        if not advapi.GetAclInformation(
            acl, ctypes.byref(size), ctypes.sizeof(size), 2
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        grants = []
        for index in range(size.AceCount):
            ace = pointer()
            if not advapi.GetAce(acl, index, ctypes.byref(ace)):
                raise ctypes.WinError(ctypes.get_last_error())
            kind, flags = (ctypes.c_ubyte * 2).from_address(ace.value)
            if flags & 8 or kind == 1:  # Inherit-only and deny entries grant no access.
                continue
            if kind != 0:
                raise PermissionError(f"Unsupported storage ACL entry: {path}")
            mask = wintypes.DWORD.from_address(ace.value + 4).value
            grants.append((sid_text(ace.value + 8), mask))
        return sid_text(owner), grants
    finally:
        kernel.LocalFree(descriptor)


def _windows_restrict(path, directory):
    """Protected DACL for the current account and SYSTEM, without shell interpolation."""
    from ctypes import wintypes

    sid = _windows_sid()
    inherit = "OICI" if directory else ""
    sddl = f"D:P(A;{inherit};FA;;;{sid})(A;{inherit};FA;;;SY)"
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi.SetFileSecurityW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    advapi.SetFileSecurityW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not advapi.SetFileSecurityW(str(path.resolve()), 0x80000004, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.LocalFree(descriptor)


@lru_cache(maxsize=1)
def _windows_sid():
    # whoami is part of Windows; CSV avoids localized column headings/account names.
    result = subprocess.run(
        ["whoami.exe", "/user", "/fo", "csv", "/nh"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    rows = list(csv.reader(io.StringIO(result.stdout)))
    sid = rows[0][-1].strip() if len(rows) == 1 and rows[0] else ""
    if not re.fullmatch(r"S-1-(?:\d+-)*\d+", sid):
        raise ValueError("Could not determine the current Windows account SID.")
    return sid
