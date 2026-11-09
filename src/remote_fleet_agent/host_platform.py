"""Host OS services. Endpoint platform selection remains a separate concern."""

from __future__ import annotations

import csv
import ctypes
import io
import os
import platform
import re
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
    if path.is_symlink():
        raise ValueError("Private storage must not be a symbolic link.")
    if platform.system() == "Windows":
        _windows_restrict(path, directory)
    else:
        path.chmod(0o700 if directory else 0o600)


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
