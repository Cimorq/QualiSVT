"""
OpenHardwareMonitor system-monitor plugin.

This is the original ProcessMonitor class, unchanged in behavior, now shaped
as a SystemMonitorPlugin so it can sit alongside future sensor sources
(LibreHardwareMonitor, NVML, a Linux hwmon reader, ...) as equal citizens
instead of being the one hardcoded monitor EncoderApp knows about.
"""
import os
import time
import shutil
import threading
import subprocess

import core.config as config
from core.proc_utils import kill_process_tree
from plugins.base import SystemMonitorPlugin, manager

if config.HAS_PSUTIL:
    import psutil

# OpenHardwareMonitor publishes its sensors through WMI/CIM, which only exists on Windows,
# so this probe is never started anywhere else. The script exits on its own when the
# parent process is gone, so a crashed app cannot leave it running forever.
_POWER_PROBE_PS = """
$ErrorActionPreference = 'SilentlyContinue'
$ns = 'root\\OpenHardwareMonitor'
while($true) {
    if (-not (Get-Process -Id __PARENT_PID__ -ErrorAction SilentlyContinue)) { break }
    $sensor = Get-CimInstance -Namespace $ns -ClassName Sensor -ErrorAction SilentlyContinue | Where-Object { $_.SensorType -eq 'Power' -and $_.Name -match 'Package' -and $_.Identifier -match 'cpu' } | Select-Object -First 1
    if ($null -ne $sensor) { [Console]::WriteLine($sensor.Value) } else { [Console]::WriteLine("None") }
    Start-Sleep -Seconds 2
}
"""


@manager.register_monitor
class OpenHardwareMonitorPlugin(SystemMonitorPlugin):
    name = "openhardwaremonitor"

    @classmethod
    def is_available(cls) -> bool:
        # CPU/RAM sampling works without it; the power-probe half needs Windows + powershell.
        return True

    def __init__(self, app_ref):
        self.app = app_ref
        self.running, self.start_time, self.end_time = False, 0, 0
        self.cpu_usages, self.ram_usages, self.cpu_powers = [], [], []
        self.monitor_thread, self.lhm_thread, self.ps_proc = None, None, None
        self._stop_lock, self._stopped = threading.Lock(), False

    def start(self):
        self.running, self._stopped = True, False
        self.start_time = time.time()
        if config.HAS_PSUTIL:
            self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self.monitor_thread.start()
        self._start_power_probe()

    def _start_power_probe(self):
        if not config.IS_WINDOWS:
            return
        ps_exe = shutil.which("powershell")
        if not ps_exe:
            return
        script = _POWER_PROBE_PS.replace("__PARENT_PID__", str(os.getpid()))
        try:
            self.ps_proc = subprocess.Popen(
                [ps_exe, "-NoProfile", "-Command", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                errors="replace",
                creationflags=config.C_FLAGS
            )
            self.lhm_thread = threading.Thread(target=self._lhm_loop, daemon=True)
            self.lhm_thread.start()
        except Exception:
            self.ps_proc = None

    def _lhm_loop(self):
        proc = self.ps_proc  # local copy: stop() clears self.ps_proc
        if not proc or not proc.stdout:
            return
        try:
            while self.running:
                line = proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if line and line != "None":
                    try:
                        self.cpu_powers.append(float(line))
                    except ValueError:
                        pass
        except Exception:
            pass
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass

    def _monitor_loop(self):
        if not config.HAS_PSUTIL:
            return
        num_cores = psutil.cpu_count() or 1
        last_pid = None
        child_procs = {}
        while self.running:
            try:
                curr_proc = self.app.current_process
                if curr_proc and curr_proc.poll() is None:
                    if curr_proc.pid != last_pid:
                        proc = psutil.Process(curr_proc.pid)
                        last_pid = curr_proc.pid
                        child_procs.clear()
                        try:
                            self.app.apply_process_settings()
                        except Exception:
                            pass
                        proc.cpu_percent(interval=None)
                        time.sleep(0.5)
                        continue

                    total_cpu = proc.cpu_percent(interval=None)
                    total_ram = proc.memory_info().rss
                    try:
                        current_children = proc.children(recursive=True)
                        current_child_pids = {c.pid for c in current_children}

                        child_procs = {pid: p for pid, p in child_procs.items() if pid in current_child_pids}

                        for child in current_children:
                            if child.pid not in child_procs:
                                child_procs[child.pid] = child
                                child.cpu_percent(interval=None)
                            else:
                                total_cpu += child_procs[child.pid].cpu_percent(interval=None)
                            total_ram += child.memory_info().rss
                    except Exception:
                        pass

                    self.cpu_usages.append(total_cpu / num_cores)
                    self.ram_usages.append(total_ram / (1024 * 1024))
                    time.sleep(1.0)
                else:
                    child_procs.clear()
                    time.sleep(0.5)
            except Exception:
                last_pid = None
                child_procs.clear()
                time.sleep(0.5)

    def stop(self):
        """Idempotent: safe to call from finally blocks, cancel handlers and app exit alike."""
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True
        self.running = False
        self.end_time = time.time()
        ps, self.ps_proc = self.ps_proc, None
        kill_process_tree(ps)

    def get_report(self, custom_duration_hrs=None):
        avg_cpu = sum(self.cpu_usages) / max(1, len(self.cpu_usages)) if self.cpu_usages else 0.0
        peak_ram = max(self.ram_usages) if self.ram_usages else 0.0
        avg_pwr = sum(self.cpu_powers) / max(1, len(self.cpu_powers)) if self.cpu_powers else 0.0
        hrs = custom_duration_hrs if custom_duration_hrs is not None else max(
            0, (self.end_time - self.start_time) / 3600.0
        )
        return {"avg_cpu": avg_cpu, "peak_ram": peak_ram, "avg_power": avg_pwr, "total_wh": avg_pwr * hrs}


_SYSTEM_SPECS_CACHE = None


def get_system_specs():
    """Static machine info (OS/CPU/RAM/GPU) shown in reports — not per-encode telemetry,
    so it stays a plain function rather than part of the plugin interface."""
    import platform
    import json
    global _SYSTEM_SPECS_CACHE
    if _SYSTEM_SPECS_CACHE is not None:
        return _SYSTEM_SPECS_CACHE
    specs = {
        "OS": f"{platform.system()} {platform.release()} (Build {platform.version()})",
        "CPU": platform.processor() or "Unknown",
        "RAM": "Unknown",
        "GPU": "Unknown"
    }
    if platform.system() == "Windows":
        ps_script = """
        $os = Get-CimInstance Win32_OperatingSystem | Select-Object -First 1
        $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
        @{ os_name = ($os.Caption -replace '^Microsoft\\s*', ''); os_version = $os.BuildNumber; cpu_name = $cpu.Name; cores = $cpu.NumberOfCores; threads = $cpu.NumberOfLogicalProcessors; clock = $cpu.MaxClockSpeed; ram = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory; gpu = @(Get-CimInstance Win32_VideoController | Where-Object { $_.Name } | Select-Object -ExpandProperty Name) } | ConvertTo-Json -Compress
        """
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                stdout=subprocess.PIPE,
                text=True,
                errors="replace",
                creationflags=config.C_FLAGS
            )
            if res.returncode == 0 and res.stdout.strip():
                d = json.loads(res.stdout.strip())
                if d.get("os_name"):
                    specs["OS"] = f"{d['os_name']} (OS Build {d.get('os_version','')})"
                if str(d.get("cpu_name", "")).strip():
                    specs["CPU"] = (
                        f"{d['cpu_name']} ({d.get('cores',0)} Cores / {d.get('threads',0)} Threads, "
                        f"~{d.get('clock',0)/1000:.2f} GHz)"
                    )
                if d.get("ram"):
                    specs["RAM"] = f"{round(int(d['ram']) / (1024**3))} GB"
                # [FIX] Robustly handle GPU field being a string, a list, or containing None/non-string entries.
                if d.get("gpu"):
                    gpu_val = d["gpu"]
                    if isinstance(gpu_val, str):
                        specs["GPU"] = gpu_val
                    elif isinstance(gpu_val, (list, tuple)):
                        cleaned = [str(g) for g in gpu_val if g]
                        if cleaned:
                            specs["GPU"] = " | ".join(cleaned)
                    else:
                        specs["GPU"] = str(gpu_val)
        except Exception:
            pass
    _SYSTEM_SPECS_CACHE = specs
    return specs
