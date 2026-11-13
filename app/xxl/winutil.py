"""Windows helpers: CPU/GPU scheduling priority, timer resolution, recycle bin.

Everything here is best effort and silently does nothing on other platforms.
"""
import ctypes
import logging
import os
import sys

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

# D3DKMT_SCHEDULINGPRIORITYCLASS
GPU_PRIORITY_NAMES = {0: "idle", 1: "below_normal", 2: "normal", 3: "above_normal", 4: "high", 5: "realtime"}


def com_init():
    """win32com needs COM initialised on every thread that uses it."""
    if not IS_WINDOWS:
        return
    try:
        import pythoncom

        pythoncom.CoInitialize()
    except Exception:
        pass


def is_admin():
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


_k32 = None


def _kernel32():
    """kernel32 with explicit 64-bit-safe prototypes (HANDLEs are pointers)."""
    global _k32
    if _k32 is None:
        import ctypes.wintypes as wt

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetCurrentProcess.restype = wt.HANDLE
        k.GetCurrentThread.restype = wt.HANDLE
        k.SetPriorityClass.argtypes = [wt.HANDLE, wt.DWORD]
        k.SetPriorityClass.restype = wt.BOOL
        k.SetThreadPriority.argtypes = [wt.HANDLE, ctypes.c_int]
        k.SetThreadPriority.restype = wt.BOOL
        k.SetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        k.SetProcessInformation.restype = wt.BOOL
        _k32 = k
    return _k32


def boost_process():
    """High CPU priority, no EcoQoS throttling, 1 ms timer resolution."""
    if not IS_WINDOWS:
        return {}
    result = {}
    kernel32 = _kernel32()
    try:
        HIGH_PRIORITY_CLASS = 0x00000080
        result["cpu_priority"] = bool(kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), HIGH_PRIORITY_CLASS))
    except Exception:
        result["cpu_priority"] = False
    try:
        class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
            _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]

        state = PROCESS_POWER_THROTTLING_STATE(1, 0x1, 0x0)  # execution speed throttling off
        ProcessPowerThrottling = 4
        result["no_throttle"] = bool(
            kernel32.SetProcessInformation(
                kernel32.GetCurrentProcess(), ProcessPowerThrottling, ctypes.byref(state), ctypes.sizeof(state)
            )
        )
    except Exception:
        result["no_throttle"] = False
    try:
        result["timer_1ms"] = ctypes.windll.winmm.timeBeginPeriod(1) == 0
    except Exception:
        result["timer_1ms"] = False
    return result


def set_gpu_priority(level=4):
    """Ask the Windows GPU scheduler to prefer this process over a game.

    Same mechanism OBS uses so encoding is not starved by games. ``high``
    usually needs administrator rights; we fall back to above-normal.
    Returns the class that was applied (name) or None.
    """
    if not IS_WINDOWS:
        return None
    try:
        gdi32 = ctypes.windll.gdi32
        func = gdi32.D3DKMTSetProcessSchedulingPriorityClass
        func.argtypes = [ctypes.c_void_p, ctypes.c_int]
        func.restype = ctypes.c_long
        handle = _kernel32().GetCurrentProcess()
        for candidate in [level] + [lv for lv in (4, 3) if lv < level]:
            status = func(handle, candidate)
            if status == 0:
                return GPU_PRIORITY_NAMES.get(candidate, str(candidate))
            log.info("GPU priority %s refused (NTSTATUS 0x%08x)", candidate, status & 0xFFFFFFFF)
    except Exception:
        log.exception("GPU priority call failed")
    return None


def boost_current_thread():
    if not IS_WINDOWS:
        return False
    try:
        THREAD_PRIORITY_HIGHEST = 2
        kernel32 = _kernel32()
        return bool(kernel32.SetThreadPriority(kernel32.GetCurrentThread(), THREAD_PRIORITY_HIGHEST))
    except Exception:
        return False


def mmcss_pro_audio():
    """Register the calling thread with the Multimedia Class Scheduler ("Pro Audio"),
    like PortAudio does for its audio threads. Returns the task handle or None."""
    if not IS_WINDOWS:
        return None
    try:
        avrt = ctypes.WinDLL("avrt")
        avrt.AvSetMmThreadCharacteristicsW.restype = ctypes.c_void_p
        avrt.AvSetMmThreadCharacteristicsW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
        index = ctypes.c_ulong(0)
        handle = avrt.AvSetMmThreadCharacteristicsW("Pro Audio", ctypes.byref(index))
        if not handle:
            boost_current_thread()
            return None
        avrt.AvSetMmThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        avrt.AvSetMmThreadPriority(handle, 1)  # AVRT_PRIORITY_HIGH
        return handle
    except Exception:
        boost_current_thread()
        return None


def send_to_recycle_bin(paths):
    """Move files/folders to the Recycle Bin (recoverable). Returns (ok, failed)."""
    ok, failed = [], []
    if not IS_WINDOWS:
        return ok, list(paths)
    try:
        from win32com.shell import shell, shellcon
    except Exception:
        shell = None
    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            if shell is not None:
                flags = (
                    shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_SILENT | shellcon.FOF_NOERRORUI
                )
                code, aborted = shell.SHFileOperation((0, shellcon.FO_DELETE, path, None, flags, None, None))
                if code == 0 and not aborted and not os.path.exists(path):
                    ok.append(path)
                    continue
            failed.append(path)
        except Exception:
            log.exception("recycle failed for %s", path)
            failed.append(path)
    return ok, failed


def relaunch_as_admin(executable, arguments, cwd):
    if not IS_WINDOWS:
        return False
    try:
        SW_SHOWNORMAL = 1
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, arguments, cwd, SW_SHOWNORMAL)
        return rc > 32
    except Exception:
        return False


def find_edge():
    from .window import find_edge as _find

    return _find() if IS_WINDOWS else None
