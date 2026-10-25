"""
deploy/linux/mt5_ipc_trace.py - READ-ONLY look at the MetaTrader5 package's
pipe traffic during initialize(), under Wine. Changes nothing it observes.

    wine C:/Python311/python.exe C:/algoedge/mt5_ipc_trace.py

Hooks ReadFile/WriteFile in the import table of MetaTrader5's own C extension
(_core*.pyd) only, logs each call's length and first bytes, then restores the
table. Used to confirm the "terminal answers 'initializing' forever" deadlock
before the bridge's --wine-ipc-fix touches a single byte of it.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

import MetaTrader5 as mt5

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.GetModuleHandleW.restype = ctypes.c_void_p
k32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
k32.VirtualProtect.restype = wt.BOOL
k32.VirtualProtect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, ctypes.POINTER(wt.DWORD)]

READFILE = ctypes.WINFUNCTYPE(wt.BOOL, wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p)
WRITEFILE = ctypes.WINFUNCTYPE(wt.BOOL, wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p)


def core_module() -> int:
    core = sys.modules.get("MetaTrader5._core")
    if core is None:
        import MetaTrader5._core as core                # noqa: F401
    name = os.path.basename(core.__file__)
    h = k32.GetModuleHandleW(name)
    if not h:
        raise SystemExit(f"{name} is not loaded")
    return h


def iat_slot(base: int, dll: str, func: str) -> int:
    """Address of `func`'s import-table slot in the module at `base` (PE32+)."""
    rd = lambda off, n: ctypes.string_at(base + off, n)              # noqa: E731
    u32 = lambda off: int.from_bytes(rd(off, 4), "little")           # noqa: E731
    u64 = lambda off: int.from_bytes(rd(off, 8), "little")           # noqa: E731
    nt = u32(0x3C)
    opt = nt + 24
    assert int.from_bytes(rd(opt, 2), "little") == 0x20B, "not PE32+"
    imp_rva = u32(opt + 112 + 8)                         # DataDirectory[1] = imports
    d = imp_rva
    while u32(d + 12):
        name = ctypes.string_at(base + u32(d + 12)).decode().lower()
        if name == dll.lower():
            ilt, iat = u32(d) or u32(d + 16), u32(d + 16)
            i = 0
            while True:
                entry = u64(ilt + 8 * i)
                if not entry:
                    break
                if not entry >> 63:
                    fname = ctypes.string_at(base + (entry & 0x7FFFFFFF) + 2).decode()
                    if fname == func:
                        return base + iat + 8 * i
                i += 1
        d += 20
    raise LookupError(f"{dll}!{func} not imported")


def patch_slot(slot: int, new_ptr: int) -> int:
    old = wt.DWORD()
    assert k32.VirtualProtect(slot, 8, 0x04, ctypes.byref(old))      # PAGE_READWRITE
    prev = ctypes.c_uint64.from_address(slot).value
    ctypes.c_uint64.from_address(slot).value = new_ptr
    k32.VirtualProtect(slot, 8, old.value, ctypes.byref(wt.DWORD()))
    return prev


def main():
    base = core_module()
    log = []
    rslot, wslot = iat_slot(base, "kernel32.dll", "ReadFile"), iat_slot(base, "kernel32.dll", "WriteFile")
    real_read = READFILE(ctypes.c_uint64.from_address(rslot).value)
    real_write = WRITEFILE(ctypes.c_uint64.from_address(wslot).value)

    @READFILE
    def on_read(h, buf, n, nread, ov):
        ok = real_read(h, buf, n, nread, ov)
        got = nread[0] if nread else 0
        if ok and got:
            log.append(("R", got, ctypes.string_at(buf, min(got, 24)).hex(" ")))
        return ok

    @WRITEFILE
    def on_write(h, buf, n, nwritten, ov):
        log.append(("W", n, ctypes.string_at(buf, min(n, 24)).hex(" ")))
        return real_write(h, buf, n, nwritten, ov)

    keep = (on_read, on_write)                           # keep the callbacks alive
    pr = patch_slot(rslot, ctypes.cast(on_read, ctypes.c_void_p).value)
    pw = patch_slot(wslot, ctypes.cast(on_write, ctypes.c_void_p).value)
    try:
        t = time.time()
        ok = mt5.initialize(path=r"C:\Program Files\MetaTrader 5\terminal64.exe", timeout=8000)
        print("initialize:", ok, mt5.last_error(), f"{time.time() - t:.1f}s")
    finally:
        patch_slot(rslot, pr)
        patch_slot(wslot, pw)
    del keep
    seen = {}
    for kind, n, head in log:
        seen.setdefault((kind, n, head[:11]), [0, head])[0] += 1
    print(f"{len(log)} pipe calls; distinct (kind, length, first 4 bytes):")
    for (kind, n, _), (count, head) in sorted(seen.items(), key=lambda x: -x[1][0])[:25]:
        print(f"  {kind} len={n:<5} x{count:<5} {head}")


if __name__ == "__main__":
    main()
