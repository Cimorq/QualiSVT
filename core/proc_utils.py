"""Child-process lifecycle helpers: kill trees, suspend/resume, human-readable cmd logging."""
import os
import ctypes
import signal
import shlex
import subprocess

import core.config as config

if config.HAS_PSUTIL:
    import psutil


def kill_process_tree(proc, timeout=5.0):
    """Kill *proc* and every descendant it has spawned. Safe on None / finished processes.

    A process that has already been reaped is deliberately left alone: its PID may
    have been recycled by an unrelated program. Children are killed before the
    parent so the parent cannot respawn them, and the parent is never handed to
    psutil.wait_procs() because that would reap it behind Popen's back and hide
    its real return code.
    """
    if proc is None or proc.poll() is not None:
        return
    children = []
    try:
        if config.HAS_PSUTIL:
            try:
                children = psutil.Process(proc.pid).children(recursive=True)
            except psutil.Error:
                children = []
            for c in children:
                try:
                    c.kill()
                except psutil.Error:
                    pass
        elif config.IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=config.C_FLAGS,
                timeout=timeout
            )
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=timeout)
    except Exception:
        pass
    if children:
        try:
            psutil.wait_procs(children, timeout=min(timeout, 2.0))
        except Exception:
            pass


def format_cmd(cmd):
    """Human-readable, copy-pasteable command line for logs."""
    try:
        parts = [str(c) for c in cmd]
        return subprocess.list2cmdline(parts) if os.name == "nt" else shlex.join(parts)
    except Exception:
        return str(cmd)


def _signal_process(pid, suspend):
    """Suspend/resume ONE process (fallback when psutil is not installed)."""
    try:
        if os.name == "nt":
            h = ctypes.windll.kernel32.OpenProcess(0x0800, False, pid)
            if h:
                (ctypes.windll.ntdll.NtSuspendProcess if suspend else ctypes.windll.ntdll.NtResumeProcess)(h)
                ctypes.windll.kernel32.CloseHandle(h)
        else:
            os.kill(pid, signal.SIGSTOP if suspend else signal.SIGCONT)
    except Exception as e:
        print(f"Error {'pausing' if suspend else 'resuming'}: {e}")


def pause_subprocess(pid):
    """Suspend a process AND all of its descendants; returns handles for resume_subprocess().

    The parent is frozen first so it cannot spawn new children while the tree is
    being enumerated. Without psutil only the single process can be frozen.
    """
    if not config.HAS_PSUTIL:
        _signal_process(pid, True)
        return [pid]
    handles = []
    try:
        root = psutil.Process(pid)
        root.suspend()
        handles.append(root)
        for child in root.children(recursive=True):
            try:
                child.suspend()
                handles.append(child)
            except psutil.Error:
                pass
    except psutil.Error as e:
        print(f"Error pausing: {e}")
    return handles


def resume_subprocess(handles):
    """Resume exactly what pause_subprocess() froze (children first, then the parent)."""
    for h in reversed(handles or []):
        if isinstance(h, int):
            _signal_process(h, False)
        else:
            try:
                h.resume()
            except psutil.Error:
                pass
