"""
M1 sanity check: confirm we can OpenProcess(PROCESS_VM_READ) on TaskBarHero
and read a few bytes from GameAssembly.dll's base. No game state inspection yet.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

import psutil

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.ReadProcessMemory.argtypes = [
    wt.HANDLE, wt.LPCVOID, wt.LPVOID, ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wt.BOOL


def find_game() -> psutil.Process | None:
    for p in psutil.process_iter(["name"]):
        if (p.info.get("name") or "").lower() == "taskbarhero.exe":
            return p
    return None


def get_module_base(proc: psutil.Process, name: str) -> int | None:
    for m in proc.memory_maps(grouped=False):
        if m.path.lower().endswith(name.lower()):
            return int(m.addr.split("-")[0], 16)
    return None


def main() -> int:
    proc = find_game()
    if proc is None:
        print("TaskBarHero.exe not running. Start the game first.")
        return 2
    print(f"Found TaskBarHero.exe: pid={proc.pid}")

    base = get_module_base(proc, "GameAssembly.dll")
    if base is None:
        print("GameAssembly.dll not in module list.")
        return 3
    print(f"GameAssembly.dll base: 0x{base:X}")

    handle = kernel32.OpenProcess(
        PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION, False, proc.pid
    )
    if not handle:
        err = ctypes.get_last_error()
        print(f"OpenProcess failed: error={err}")
        return 4

    try:
        buf = (ctypes.c_ubyte * 16)()
        read = ctypes.c_size_t(0)
        ok = kernel32.ReadProcessMemory(
            handle, base, buf, ctypes.sizeof(buf), ctypes.byref(read)
        )
        if not ok:
            err = ctypes.get_last_error()
            print(f"ReadProcessMemory failed: error={err}")
            return 5
        hex_dump = " ".join(f"{b:02X}" for b in buf)
        print(f"Bytes at base (read={read.value}): {hex_dump}")
        if buf[0] == 0x4D and buf[1] == 0x5A:
            print("OK: MZ header confirmed. Read access works.")
        else:
            print("WARN: bytes are not MZ. Investigate.")
    finally:
        kernel32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
