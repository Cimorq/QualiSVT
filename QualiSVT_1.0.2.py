
# --- START OF FILE aistudio-v2- bugfix.py ---

import subprocess
import re
import os

def get_metric_thread_count():
    """Logical CPU count minus one; minimum one thread."""
    return max(1, (os.cpu_count() or 1) - 1)

def parse_vmaf_json_score(data):
    """Extract the pooled VMAF mean across libvmaf JSON schema variants."""
    pooled = data.get("pooled_metrics", {}) if isinstance(data, dict) else {}
    vmaf = pooled.get("vmaf", {}) if isinstance(pooled, dict) else {}
    if isinstance(vmaf, dict) and vmaf.get("mean") is not None:
        return float(vmaf["mean"])

    # Some libvmaf builds/configurations expose pooled values differently.
    for key in ("pooled", "aggregate", "summary"):
        obj = data.get(key) if isinstance(data, dict) else None
        if isinstance(obj, dict):
            for k in ("vmaf", "VMAF", "mean"):
                val = obj.get(k)
                if isinstance(val, dict) and val.get("mean") is not None:
                    return float(val["mean"])
                if isinstance(val, (int, float)):
                    return float(val)

    return None

import time
import sys
import tempfile
import threading
import json
import shutil
import ctypes
import signal
import datetime
import platform
import traceback
import math
import hashlib
import multiprocessing
from collections import Counter
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
import webbrowser

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False

# --- Directory and Configuration Setup ---
APP_VERSION = "1.0.2"

def get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.getcwd()

APP_DIR = get_app_dir()
BIN_DIR = os.path.join(APP_DIR, "Bin")
os.makedirs(BIN_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(BIN_DIR, "QualiSVT_Config.json")
CACHE_FILE = os.path.join(BIN_DIR, "QualiSVT_Cache.json")

VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.webm', '.m4v')
C_FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# --- Tool Executables ---
FFMPEG_EXE = "ffmpeg"
FFPROBE_EXE = "ffprobe"
FFVSHIP_EXE = "FFVship"
HANDBRAKE_EXE = "HandBrakeCLI"

def resolve_tools():
    global FFMPEG_EXE, FFPROBE_EXE, FFVSHIP_EXE, HANDBRAKE_EXE
    def _find(name):
        exe = name + (".exe" if os.name == "nt" else "")
        bin_p = os.path.join(BIN_DIR, exe)
        if os.path.isfile(bin_p): return bin_p
        root_p = os.path.join(APP_DIR, exe)
        if os.path.isfile(root_p): return root_p
        return shutil.which(exe) or exe

    FFMPEG_EXE = _find("ffmpeg")
    FFPROBE_EXE = _find("ffprobe")
    FFVSHIP_EXE = _find("FFVship")
    HANDBRAKE_EXE = _find("HandBrakeCLI")

# --- Universal Caching Functions ---
def get_video_fingerprint(filepath):
    if not os.path.exists(filepath): return None
    stat = os.stat(filepath)
    hasher = hashlib.md5()
    hasher.update(f"{stat.st_size}_{stat.st_mtime}".encode('utf-8'))
    try:
        with open(filepath, 'rb') as f:
            hasher.update(f.read(1024 * 1024))
    except Exception:
        pass
    return hasher.hexdigest()

def get_settings_fingerprint(settings_dict):
    return hashlib.md5(json.dumps(settings_dict, sort_keys=True).encode('utf-8')).hexdigest()

def check_cache(filepath, settings_dict, cache_type="autocrf"):
    vid_hash = get_video_fingerprint(filepath)
    if not vid_hash: return None
    set_hash = get_settings_fingerprint(settings_dict)
    
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                db = json.load(f)
            
            if vid_hash in db and not any(isinstance(db[vid_hash].get(k), dict) for k in ["autocrf", "eval_pre", "crf_eval"]):
                return None
                
            if vid_hash in db and cache_type in db[vid_hash] and set_hash in db[vid_hash][cache_type]:
                entry = db[vid_hash][cache_type][set_hash]
                if isinstance(entry, dict) and "data" in entry and "settings" in entry:
                    return entry["data"]
                return entry
        except Exception: pass
    return None

def save_cache(filepath, settings_dict, result_data, cache_type="autocrf"):
    vid_hash = get_video_fingerprint(filepath)
    if not vid_hash: return
    set_hash = get_settings_fingerprint(settings_dict)
    db = {}
    
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                db = json.load(f)
        except Exception: pass
        
    if vid_hash not in db:
        db[vid_hash] = {}
        
    if vid_hash in db and not any(isinstance(db[vid_hash].get(k), dict) for k in ["autocrf", "eval_pre", "crf_eval"]):
        db[vid_hash] = {}
        
    if cache_type not in db[vid_hash]:
        db[vid_hash][cache_type] = {}
        
    if cache_type == "crf_eval" and set_hash in db[vid_hash][cache_type]:
        existing = db[vid_hash][cache_type][set_hash]
        if isinstance(existing, dict) and "data" in existing:
            existing_data = existing["data"]
            if "scores" in result_data and "scores" in existing_data:
                existing_data["scores"].update(result_data["scores"])
            for k, v in result_data.items():
                if k != "scores":
                    existing_data[k] = v
            db[vid_hash][cache_type][set_hash]["data"] = existing_data
            db[vid_hash][cache_type][set_hash]["settings"] = settings_dict
        else:
            db[vid_hash][cache_type][set_hash] = {
                "settings": settings_dict,
                "data": result_data
            }
    else:
        db[vid_hash][cache_type][set_hash] = {
            "settings": settings_dict,
            "data": result_data
        }
    
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(db, f, indent=4)
    except Exception: pass

# --- Advanced Resource Monitor Class ---
class ProcessMonitor:
    def __init__(self, app_ref):
        self.app = app_ref
        self.running = False
        self.cpu_usages = []
        self.ram_usages = []
        self.cpu_powers = []
        self.monitor_thread = None
        self.ps_proc = None
        self.lhm_thread = None
        self.start_time = 0
        self.end_time = 0

    def start(self):
        self.running = True
        self.start_time = time.time()
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        
        ps_script = """
        $ErrorActionPreference = 'SilentlyContinue'
        $ns = 'root\\OpenHardwareMonitor'
        while($true) {
            $sensor = Get-CimInstance -Namespace $ns -ClassName Sensor -ErrorAction SilentlyContinue | Where-Object { $_.SensorType -eq 'Power' -and $_.Name -match 'Package' -and $_.Identifier -match 'cpu' } | Select-Object -First 1
            if ($null -ne $sensor) { [Console]::WriteLine($sensor.Value) } else { [Console]::WriteLine("None") }
            Start-Sleep -Seconds 2
        }
        """
        try:
            self.ps_proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", ps_script],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            )
            self.lhm_thread = threading.Thread(target=self._lhm_loop, daemon=True)
            self.lhm_thread.start()
        except: pass

    def _lhm_loop(self):
        if not self.ps_proc: return
        while self.running:
            try:
                line = self.ps_proc.stdout.readline()
                if not line: break
                line = line.strip()
                if line and line != "None":
                    self.cpu_powers.append(float(line))
            except:
                break

    def _monitor_loop(self):
        num_cores = psutil.cpu_count() or 1
        last_pid = None
        proc = None
        
        while self.running:
            try:
                curr_proc = self.app.current_process
                if curr_proc and curr_proc.poll() is None:
                    if curr_proc.pid != last_pid:
                        proc = psutil.Process(curr_proc.pid)
                        last_pid = curr_proc.pid
                        
                        try:
                            self.app.apply_process_settings()
                        except: pass
                        
                        proc.cpu_percent(interval=None) 
                        time.sleep(0.5)
                        continue
                        
                    cpu = proc.cpu_percent(interval=None) / num_cores
                    ram = proc.memory_info().rss / (1024 * 1024)
                    
                    self.cpu_usages.append(cpu)
                    self.ram_usages.append(ram)
                    time.sleep(1.0)
                else:
                    time.sleep(0.5)
            except Exception:
                last_pid = None
                time.sleep(0.5)

    def stop(self):
        self.running = False
        self.end_time = time.time()
        if self.ps_proc:
            try:
                if HAS_PSUTIL:
                    parent = psutil.Process(self.ps_proc.pid)
                    for child in parent.children(recursive=True):
                        child.kill()
                    parent.kill()
                else:
                    self.ps_proc.kill()
            except: 
                pass

    def get_report(self, custom_duration_hrs=None):
        avg_cpu = sum(self.cpu_usages) / max(1, len(self.cpu_usages)) if self.cpu_usages else 0.0
        peak_ram = max(self.ram_usages) if self.ram_usages else 0.0
        avg_pwr = sum(self.cpu_powers) / max(1, len(self.cpu_powers)) if self.cpu_powers else 0.0
        
        if custom_duration_hrs is not None:
            hrs = custom_duration_hrs
        else:
            hrs = (self.end_time - self.start_time) / 3600.0
            if hrs < 0: hrs = 0
            
        return {
            "avg_cpu": avg_cpu,
            "peak_ram": peak_ram,
            "avg_power": avg_pwr,
            "total_wh": avg_pwr * hrs
        }

def get_system_specs():
    os_name = platform.system()
    os_release = platform.release()
    os_version = platform.version()
    
    if os_name == "Windows" and os_release == "10":
        try:
            build = int(os_version.split('.')[2])
            if build >= 22000:
                os_release = "11"
        except: pass
        
    specs = {
        "OS": f"{os_name} {os_release} (Build {os_version})",
        "CPU": platform.processor() or "Unknown",
        "RAM": "Unknown",
        "GPU": "Unknown"
    }
    
    if os_name == "Windows":
        ps_script = """
        $ErrorActionPreference = 'SilentlyContinue'
        $os = Get-CimInstance Win32_OperatingSystem | Select-Object -First 1
        $caption = $os.Caption -replace '^Microsoft\\s*', ''
        $build = $os.BuildNumber
        $ubr = (Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion' -Name UBR -ErrorAction SilentlyContinue).UBR
        if ($null -ne $ubr) { $version = "$build.$ubr" } else { $version = $build }
        $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
        $ram = Get-CimInstance Win32_ComputerSystem
        $gpus = @(Get-CimInstance Win32_VideoController)
        $gpuNames = @()
        foreach ($g in $gpus) { if ($g.Name) { $gpuNames += $g.Name } }
        @{
            os_name = $caption
            os_version = $version
            cpu_name = $cpu.Name
            cores = $cpu.NumberOfCores
            threads = $cpu.NumberOfLogicalProcessors
            clock = $cpu.MaxClockSpeed
            ram = $ram.TotalPhysicalMemory
            gpu = $gpuNames
        } | ConvertTo-Json -Compress
        """
        try:
            cmd = ["powershell", "-NoProfile", "-Command", ps_script]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=C_FLAGS)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout.strip())
                os_n = data.get("os_name", "")
                os_v = data.get("os_version", "")
                if os_n: specs["OS"] = f"{os_n} (OS Build {os_v})"
                
                c_name = str(data.get("cpu_name", "")).strip()
                c_cores = data.get("cores", 0)
                c_threads = data.get("threads", 0)
                c_clock = data.get("clock", 0)
                if c_name:
                    freq_ghz = f"{c_clock / 1000:.2f} GHz" if c_clock else "Unknown Hz"
                    specs["CPU"] = f"{c_name} ({c_cores} Cores / {c_threads} Threads, ~{freq_ghz})"
                    
                ram_bytes = data.get("ram", 0)
                if ram_bytes: specs["RAM"] = f"{round(int(ram_bytes) / (1024**3))} GB"
                gpus = data.get("gpu", [])
                if gpus:
                    if isinstance(gpus, str): gpus = [gpus]
                    specs["GPU"] = " | ".join(gpus)
        except Exception:
            pass

    return specs

def pause_subprocess(pid):
    try:
        if os.name == 'nt':
            PROCESS_SUSPEND_RESUME = 0x0800
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
            if handle:
                ctypes.windll.ntdll.NtSuspendProcess(handle)
                ctypes.windll.kernel32.CloseHandle(handle)
        else:
            os.kill(pid, signal.SIGSTOP)
    except Exception: pass

def resume_subprocess(pid):
    try:
        if os.name == 'nt':
            PROCESS_SUSPEND_RESUME = 0x0800
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
            if handle:
                ctypes.windll.ntdll.NtResumeProcess(handle)
                ctypes.windll.kernel32.CloseHandle(handle)
        else:
            os.kill(pid, signal.SIGCONT)
    except Exception: pass

def shorten_filename(name, max_len=100):
    if len(name) > max_len:
        return name[:max_len - 20] + "..." + name[-15:]
    return name

def get_video_dimensions(filename):
    cmd = [FFPROBE_EXE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", filename]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, creationflags=C_FLAGS)
        w, h = map(int, res.stdout.strip().split('x'))
        return w, h
    except Exception:
        return None, None

def get_video_pix_fmt(filename):
    """Return the source video's pixel format."""
    cmd = [FFPROBE_EXE, "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=pix_fmt",
           "-of", "default=noprint_wrappers=1:nokey=1", filename]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, check=True, creationflags=C_FLAGS)
        return res.stdout.strip() or None
    except Exception:
        return None

def get_duration(filename):
    cmd = [FFPROBE_EXE, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", filename]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, creationflags=C_FLAGS)
        return float(res.stdout.strip())
    except Exception: return 0.0

def get_total_frames(filename):
    cmd = [FFPROBE_EXE, "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", filename]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, creationflags=C_FLAGS)
        return int(res.stdout.strip())
    except Exception: return 0

def get_fps(filename):
    cmd = [FFPROBE_EXE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate", "-of", "default=noprint_wrappers=1:nokey=1", filename]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, creationflags=C_FLAGS)
        rate = res.stdout.strip()
        if '/' in rate:
            num, den = rate.split('/')
            return float(num) / float(den)
        return float(rate)
    except Exception: return 24.0

def get_chapter_times(filename, start_ch, end_ch):
    cmd = [FFPROBE_EXE, "-v", "error", "-print_format", "json", "-show_chapters", filename]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, creationflags=C_FLAGS)
        data = json.loads(res.stdout)
        chapters = data.get("chapters", [])
        if not chapters: return None, None
        
        start_ch = max(1, min(start_ch, len(chapters)))
        end_ch = max(start_ch, min(end_ch, len(chapters)))
        
        s_time = float(chapters[start_ch - 1].get("start_time", 0))
        e_time = float(chapters[end_ch - 1].get("end_time", 0))
        return s_time, e_time
    except Exception: return None, None

def parse_time(time_str):
    parts = str(time_str).split(':')
    try:
        if len(parts) == 3: return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2: return float(parts[0]) * 60 + float(parts[1])
        if len(parts) == 1: return float(parts[0])
    except ValueError: pass
    return 0.0

def format_duration(seconds):
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0: return f"{int(h)}h {int(m)}m {int(s)}s"
    elif m > 0: return f"{int(m)}m {int(s)}s"
    return f"{int(s)}s"

def get_quality_label(metric: str, score: float) -> str:
    if score is None or not isinstance(score, (int, float)): return "Unknown"
    metric_clean = metric.upper().strip()

    if metric_clean == "VMAF":
        if score >= 97.0: return "Visually Lossless" 
        if score >= 93.0: return "Excellent"         
        if score >= 85.0: return "Good"              
        if score >= 75.0: return "Fair"              
        if score >= 60.0: return "Poor"              
        return "Bad"                                 

    elif metric_clean in ["SSIMULACRA2", "SSIM2"]:
        if score >= 90.0: return "Visually Lossless"
        if score >= 75.0: return "Excellent"
        if score >= 60.0: return "Good"
        if score >= 50.0: return "Fair"
        if score >= 30.0: return "Poor"
        return "Bad"

    elif metric_clean == "CVVDP":
        if score >= 9.5: return "Visually Lossless"
        if score >= 8.5: return "Excellent"
        if score >= 7.5: return "Good"
        if score >= 6.5: return "Fair"
        if score >= 5.5: return "Poor"
        return "Bad"

    elif metric_clean == "BUTTERAUGLI":
        if score <= 0.5: return "Visually Lossless"
        if score <= 1.5: return "Excellent"
        if score <= 2.5: return "Good"
        if score <= 3.5: return "Fair"
        if score <= 4.5: return "Poor"
        return "Bad"

    elif metric_clean in ["XPSNR", "WPSNR"]:
        if score >= 45.0: return "Visually Lossless"
        if score >= 42.0: return "Excellent"
        if score >= 38.0: return "Good"
        if score >= 35.0: return "Fair"
        if score >= 30.0: return "Poor"
        return "Bad"

    return "Unknown"

def get_quality_color_tag(label):
    if label == "Visually Lossless": return "q_vlossless"
    if label == "Excellent": return "q_super"
    if label == "Good": return "q_high"
    if label == "Fair": return "q_med"
    if label == "Poor": return "q_low"
    return "q_bad"

# --- Main GUI Class ---
if HAS_DND:
    BaseRootClass = TkinterDnD.Tk
else:
    BaseRootClass = tk.Tk

class EncoderApp:
    def __init__(self, root):
        self.root = root
        self.root.title(f"QualiSVT v{APP_VERSION} (Advanced Batch Encoder)")
        self.root.geometry("1050x730")
        self.root.minsize(980, 650)
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self.files_to_process = []
        self.is_encoding = False
        self.is_paused = False
        self.cancel_requested = False
        self.current_process = None
        self.current_paused_duration = 0.0
        self.pause_start_time = 0.0
        self.last_status = "Status: Idle"
        self.current_log_data = {}
        
        self.build_ui()
        self.load_settings()

    def apply_process_settings(self, event=None):
        if not HAS_PSUTIL or not self.current_process: return
        
        prio_str = self.priority_var.get()
        core_str = self.cpu_cores_var.get()
        
        def _apply():
            try:
                proc = self.current_process
                if not proc or proc.poll() is not None: return
                p = psutil.Process(proc.pid)
                
                if os.name == 'nt':
                    mapping = {
                        "Realtime": psutil.REALTIME_PRIORITY_CLASS,
                        "High": psutil.HIGH_PRIORITY_CLASS,
                        "Above Normal": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
                        "Normal": psutil.NORMAL_PRIORITY_CLASS,
                        "Below Normal": psutil.BELOW_NORMAL_PRIORITY_CLASS,
                        "Idle": psutil.IDLE_PRIORITY_CLASS
                    }
                    if prio_str in mapping: p.nice(mapping[prio_str])
                else:
                    mapping = {
                        "Realtime": -20, "High": -10, "Above Normal": -5,
                        "Normal": 0, "Below Normal": 5, "Idle": 19
                    }
                    if prio_str in mapping: p.nice(mapping[prio_str])

                cores_to_use = int(core_str.split('x')[0])
                valid_cores = list(range(cores_to_use))
                
                try:
                    p.cpu_affinity(valid_cores)
                except AttributeError:
                    pass
                
                for child in p.children(recursive=True):
                    try:
                        if os.name == 'nt' and prio_str in mapping:
                            child.nice(mapping[prio_str])
                        elif os.name != 'nt' and prio_str in mapping:
                            child.nice(mapping[prio_str])
                        try:
                            child.cpu_affinity(valid_cores)
                        except AttributeError: pass
                    except: pass
            except Exception:
                pass
                
        threading.Thread(target=_apply, daemon=True).start()

    def build_ui(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        top_container = ttk.Frame(main_frame)
        top_container.pack(side=tk.TOP, fill=tk.X)
        
        bottom_container = ttk.Frame(main_frame)
        bottom_container.pack(side=tk.BOTTOM, fill=tk.X)

        self.middle_container = ttk.Frame(main_frame)
        self.middle_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(5, 5))

        info_frame = ttk.Frame(bottom_container)
        info_frame.pack(side=tk.BOTTOM, fill=tk.X)

        if not HAS_DND:
            ttk.Label(info_frame, text="Tip: Install 'tkinterdnd2' for Drag & Drop support", font=("TkDefaultFont", 8, "italic")).pack(side=tk.LEFT, padx=5)
        if not HAS_PSUTIL:
            ttk.Label(info_frame, text="Note: Install 'psutil' to enable Advanced Resource Monitoring and Real-time CPU Limits", font=("TkDefaultFont", 8, "italic"), foreground="#FF8C00").pack(side=tk.LEFT, padx=5)

        action_frame = ttk.Frame(bottom_container)
        action_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(5, 5))

        self.btn_start = ttk.Button(action_frame, text="Start Encoding", command=self.toggle_encoding, style="Accent.TButton")
        self.btn_start.pack(side=tk.RIGHT, padx=5)

        self.btn_pause = ttk.Button(action_frame, text="Pause", command=self.toggle_pause, state=tk.DISABLED)
        self.btn_pause.pack(side=tk.RIGHT, padx=5)

        self.priority_frame = ttk.Frame(action_frame)
        self.priority_frame.pack(side=tk.LEFT, padx=5)

        ttk.Label(self.priority_frame, text="Priority:").pack(side=tk.LEFT, padx=(0, 2))
        self.priority_var = tk.StringVar(value="Normal")
        self.priority_cb = ttk.Combobox(self.priority_frame, textvariable=self.priority_var, values=["Realtime", "High", "Above Normal", "Normal", "Below Normal", "Idle"], state="readonly", width=12)
        self.priority_cb.pack(side=tk.LEFT, padx=(0, 5))
        self.priority_cb.bind("<<ComboboxSelected>>", self.apply_process_settings)

        try:
            TOTAL_CORES = multiprocessing.cpu_count() or 1
        except:
            TOTAL_CORES = 1
            
        core_values = [f"{i}x ({int((i / TOTAL_CORES) * 100)}%)" for i in range(TOTAL_CORES, 0, -1)]
        self.cpu_cores_var = tk.StringVar(value=core_values[0])
        self.cpu_cores_cb = ttk.Combobox(self.priority_frame, textvariable=self.cpu_cores_var, values=core_values, state="readonly", width=12)
        self.cpu_cores_cb.pack(side=tk.LEFT, padx=(0, 5))
        self.cpu_cores_cb.bind("<<ComboboxSelected>>", self.apply_process_settings)
        
        if not HAS_PSUTIL:
            self.priority_cb.config(state=tk.DISABLED)
            self.cpu_cores_cb.config(state=tk.DISABLED)

        prog_frame = ttk.LabelFrame(bottom_container, text=" Progress ", padding="5")
        prog_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 5))

        self.status_lbl = ttk.Label(prog_frame, text="Status: Idle", font=("TkDefaultFont", 9, "bold"))
        self.status_lbl.pack(anchor=tk.W, padx=5, pady=2)

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(prog_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, padx=5, pady=5)

        self.stats_lbl = ttk.Label(prog_frame, text="0% | FPS: 0.0 | Avg: 0.0 | ETA: --")
        self.stats_lbl.pack(anchor=tk.E, padx=5, pady=2)

        self.file_frame = ttk.LabelFrame(top_container, text=" Video Files (Drag & Drop Supported) ", padding="5")
        self.file_frame.pack(side=tk.TOP, fill=tk.X, pady=(0, 5))

        list_container = ttk.Frame(self.file_frame)
        list_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        
        yscrollbar = ttk.Scrollbar(list_container, orient=tk.VERTICAL)
        xscrollbar = ttk.Scrollbar(list_container, orient=tk.HORIZONTAL)
        
        self.listbox = tk.Listbox(list_container, selectmode=tk.EXTENDED, height=4, yscrollcommand=yscrollbar.set, xscrollcommand=xscrollbar.set)
        
        yscrollbar.config(command=self.listbox.yview)
        xscrollbar.config(command=self.listbox.xview)
        
        xscrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        yscrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        btn_frame = ttk.Frame(self.file_frame)
        btn_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=5)
        ttk.Button(btn_frame, text="Add Files", command=self.add_files).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="Add Folder", command=self.add_folder).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="Remove Selected", command=self.remove_files).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="Clear All", command=self.clear_files).pack(fill=tk.X, pady=2)

        if HAS_DND:
            self.listbox.drop_target_register(DND_FILES)
            self.listbox.dnd_bind('<<Drop>>', self.handle_drop)

        self.notebook = ttk.Notebook(top_container)
        self.notebook.pack(side=tk.TOP, fill=tk.X, pady=(0, 5))

        # ================= TAB 1: Video Settings =================
        settings_frame = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(settings_frame, text=" Video & Output ")

        enc_lf = ttk.LabelFrame(settings_frame, text=" Encoder Settings ", padding="5")
        enc_lf.pack(fill=tk.X, pady=(0, 5))

        row0_eng = ttk.Frame(enc_lf)
        row0_eng.pack(fill=tk.X, pady=2)
        ttk.Label(row0_eng, text="Final Engine:").pack(side=tk.LEFT, padx=(5, 2))
        self.engine_var = tk.StringVar(value="FFmpeg")
        self.engine_cb = ttk.Combobox(row0_eng, textvariable=self.engine_var, values=["FFmpeg", "HandBrakeCLI"], state="readonly", width=15)
        self.engine_cb.pack(side=tk.LEFT, padx=(0, 15))
        self.engine_cb.bind("<<ComboboxSelected>>", self.on_engine_change)

        row0 = ttk.Frame(enc_lf)
        row0.pack(fill=tk.X, pady=2)
        ttk.Label(row0, text="Encoder:").pack(side=tk.LEFT, padx=(5, 2))
        self.encoder_var = tk.StringVar(value="SVT-AV1")
        self.encoder_cb = ttk.Combobox(row0, textvariable=self.encoder_var, values=["SVT-AV1", "x265 (HEVC)"], state="readonly", width=15)
        self.encoder_cb.pack(side=tk.LEFT, padx=(0, 15))
        self.encoder_cb.bind("<<ComboboxSelected>>", self.on_encoder_change)

        ttk.Label(row0, text="Bit-Depth:").pack(side=tk.LEFT, padx=(0, 2))
        self.bit_depth_var = tk.StringVar(value="10-bit (yuv420p10le)")
        self.bit_depth_cb = ttk.Combobox(
            row0,
            textvariable=self.bit_depth_var,
            values=["8-bit (yuv420p)", "10-bit (yuv420p10le)", "12-bit (yuv420p12le)"],
            width=18,
            state="readonly"
        )
        self.bit_depth_cb.pack(side=tk.LEFT, padx=(0, 5))
        self.bit_depth_cb.bind("<<ComboboxSelected>>", lambda e: self.save_settings())

        ttk.Label(row0, text="CRF:").pack(side=tk.LEFT, padx=(5, 2))
        self.crf_var = tk.StringVar(value="34.0")
        self.crf_spinbox = ttk.Spinbox(row0, from_=0, to=51, increment=0.5, format="%.1f", textvariable=self.crf_var, width=6)
        self.crf_spinbox.pack(side=tk.LEFT, padx=(0, 15))
        
        ttk.Label(row0, text="Preset:").pack(side=tk.LEFT, padx=(0, 2))
        self.preset_var = tk.StringVar(value="6")
        self.preset_cb = ttk.Combobox(row0, textvariable=self.preset_var, values=[str(i) for i in range(-3, 11)], width=10)
        self.preset_cb.pack(side=tk.LEFT, padx=(0, 15))
        
        ttk.Label(row0, text="Tune:").pack(side=tk.LEFT, padx=(0, 2))
        self.tune_var = tk.StringVar(value="0 (vq)")
        self.tune_cb = ttk.Combobox(row0, textvariable=self.tune_var, values=["0 (vq)", "1 (psnr)", "2 (ssim)", "3 (iq)", "4 (ms-ssim)", "5 (grain)"], width=12)
        self.tune_cb.pack(side=tk.LEFT, padx=(0, 15))

        self.row1_svt = ttk.Frame(enc_lf)
        self.row1_svt.pack(fill=tk.X, pady=2)
        ttk.Label(self.row1_svt, text="SVT Params:").pack(side=tk.LEFT, padx=(5, 2))
        self.svt_params_var = tk.StringVar(value="scd=1:hbd-mds=1:ac-bias=2:enable-variance-boost=1:complex-hvs=1")
        ttk.Entry(self.row1_svt, textvariable=self.svt_params_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

        self.row1_x265 = ttk.Frame(enc_lf)
        ttk.Label(self.row1_x265, text="x265 Params:").pack(side=tk.LEFT, padx=(5, 2))
        self.x265_params_var = tk.StringVar(value="crqpoffs=-2:cbqpoffs=-2:aq-mode=3")
        ttk.Entry(self.row1_x265, textvariable=self.x265_params_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        
        # New row for GOP Mode
        self.row1_gop = ttk.Frame(enc_lf)
        self.row1_gop.pack(fill=tk.X, pady=2)
        ttk.Label(self.row1_gop, text="GOP Mode:").pack(side=tk.LEFT, padx=(5, 2))
        self.gop_mode_var = tk.StringVar(value="10-Second GOP")
        self.gop_mode_cb = ttk.Combobox(self.row1_gop, textvariable=self.gop_mode_var, values=["10-Second GOP", "Encoder Default", "Custom"], state="readonly", width=16)
        self.gop_mode_cb.pack(side=tk.LEFT, padx=(0, 5))
        self.gop_mode_cb.bind("<<ComboboxSelected>>", self.on_gop_mode_change)
        
        self.gop_custom_sec_var = tk.StringVar(value="10")
        self.gop_custom_sec_entry = ttk.Entry(self.row1_gop, textvariable=self.gop_custom_sec_var, width=5)
        self.gop_custom_sec_entry.pack(side=tk.LEFT, padx=(0, 2))
        self.gop_custom_sec_lbl = ttk.Label(self.row1_gop, text="Seconds")
        self.gop_custom_sec_lbl.pack(side=tk.LEFT, padx=(0, 2))
        
        out_lf = ttk.LabelFrame(settings_frame, text=" Output & System ", padding="5")
        out_lf.pack(fill=tk.X, pady=5)

        row2 = ttk.Frame(out_lf)
        row2.pack(fill=tk.X, pady=2)
        ttk.Label(row2, text="Output Mode:").pack(side=tk.LEFT, padx=(5, 2))
        self.out_mode_var = tk.StringVar(value="Next to Original")
        self.out_combo = ttk.Combobox(row2, textvariable=self.out_mode_var, values=["Next to Original", "Subfolder", "Browse Folder..."], state="readonly", width=18)
        self.out_combo.pack(side=tk.LEFT, padx=(0, 15))
        self.out_combo.bind("<<ComboboxSelected>>", self.on_output_mode_change)

        self.subfolder_var = tk.StringVar(value="encoded")
        self.subfolder_entry = ttk.Entry(row2, textvariable=self.subfolder_var, width=12)
        self.subfolder_var.trace_add("write", lambda *args: self.update_expected_output_path())

        self.custom_folder_var = tk.StringVar(value="")
        self.custom_folder_frame = ttk.Frame(row2)
        self.custom_folder_entry = ttk.Entry(self.custom_folder_frame, textvariable=self.custom_folder_var, width=20, state="readonly")
        self.custom_folder_btn = ttk.Button(self.custom_folder_frame, text="Browse", command=self.browse_output_folder)
        self.custom_folder_entry.pack(side=tk.LEFT, padx=(0, 2))
        self.custom_folder_btn.pack(side=tk.LEFT)

        ttk.Label(row2, text="Format:").pack(side=tk.LEFT, padx=(0, 2))
        self.out_format_var = tk.StringVar(value="MKV")
        self.out_format_cb = ttk.Combobox(row2, textvariable=self.out_format_var, values=["MKV", "MP4"], state="readonly", width=8)
        self.out_format_cb.pack(side=tk.LEFT, padx=(0, 15))
        self.out_format_cb.bind("<<ComboboxSelected>>", lambda e: self.update_expected_output_path())

        ttk.Label(row2, text="Save Log File:").pack(side=tk.LEFT, padx=(5, 2))
        self.log_loc_var = tk.StringVar(value="Next to Original")
        ttk.Combobox(row2, textvariable=self.log_loc_var, values=["Next to Original", "App 'Logs' Folder", "Don't Save"], state="readonly", width=18).pack(side=tk.LEFT, padx=(0, 15))

        ttk.Label(row2, text="Post-Action:").pack(side=tk.LEFT, padx=(0, 2))
        self.power_var = tk.StringVar(value="Do Nothing")
        self.power_cb = ttk.Combobox(row2, textvariable=self.power_var, values=["Do Nothing", "Quit", "Shutdown", "Sleep"], state="readonly", width=12)
        self.power_cb.pack(side=tk.LEFT, padx=(0, 15))

        self.row2_out_path = ttk.Frame(out_lf)
        self.row2_out_path.pack(fill=tk.X, pady=(2, 0), padx=5)
        self.out_path_lbl = ttk.Label(self.row2_out_path, text="Expected Output: (Add a video file first)", foreground="gray", wraplength=850)
        self.out_path_lbl.pack(side=tk.LEFT)

        row3 = ttk.Frame(out_lf)
        row3.pack(fill=tk.X, pady=2)
        self.auto_resume_var = tk.BooleanVar(value=False)
        self.auto_resume_chk = ttk.Checkbutton(row3, text="Enable Auto-Resume (Recover Incomplete Encodes)", variable=self.auto_resume_var)
        self.auto_resume_chk.pack(side=tk.LEFT, padx=(5, 15))
        self.auto_resume_var.trace_add("write", lambda *args: self.save_settings())

        # ================= TAB 2: Audio Settings =================
        audio_frame = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(audio_frame, text=" Audio Settings ")

        audio_lf = ttk.LabelFrame(audio_frame, text=" Audio Tracks & Parameters ", padding="5")
        audio_lf.pack(fill=tk.BOTH, expand=True, pady=5)

        ttk.Label(audio_lf, text="Source Track", font=("TkDefaultFont", 9, "bold")).grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        ttk.Label(audio_lf, text="Codec", font=("TkDefaultFont", 9, "bold")).grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)
        ttk.Label(audio_lf, text="Quality (Bitrate)", font=("TkDefaultFont", 9, "bold")).grid(row=0, column=2, padx=5, pady=5, sticky=tk.W)
        ttk.Label(audio_lf, text="Mixdown", font=("TkDefaultFont", 9, "bold")).grid(row=0, column=3, padx=5, pady=5, sticky=tk.W)

        self.audio_track_var = tk.StringVar(value="All Audio Tracks")
        ttk.Combobox(audio_lf, textvariable=self.audio_track_var, values=["All Audio Tracks", "Track 1 (Default)"], state="readonly", width=18).grid(row=1, column=0, padx=5, pady=5)

        self.audio_codec_var = tk.StringVar(value="OPUS")
        self.audio_codec_cb = ttk.Combobox(audio_lf, textvariable=self.audio_codec_var, values=["OPUS", "AAC", "Copy"], state="readonly", width=12)
        self.audio_codec_cb.grid(row=1, column=1, padx=5, pady=5)
        self.audio_codec_cb.bind("<<ComboboxSelected>>", self.on_audio_codec_change)

        self.audio_br_var = tk.StringVar(value="128K")
        self.audio_br_cb = ttk.Combobox(audio_lf, textvariable=self.audio_br_var, values=["64K", "96K", "128K", "160K", "192K", "256K", "320K", "512K"], width=12, state="readonly")
        self.audio_br_cb.grid(row=1, column=2, padx=5, pady=5)

        self.audio_mixdown_var = tk.StringVar(value="Auto")
        self.audio_mixdown_cb = ttk.Combobox(audio_lf, textvariable=self.audio_mixdown_var, values=["Auto", "Mono", "Stereo", "5.1", "7.1"], state="readonly", width=10)
        self.audio_mixdown_cb.grid(row=1, column=3, padx=5, pady=5)
        
        # ================= TAB 3: Quality & Metrics (With Sub-Tabs) =================
        metrics_frame = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(metrics_frame, text=" Quality & Metrics ")
        
        op_frame = ttk.Frame(metrics_frame)
        op_frame.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(op_frame, text="Operation Mode:").pack(side=tk.LEFT, padx=(5, 2))
        self.op_mode_var = tk.StringVar(value="None (Fastest)")
        self.op_mode_cb = ttk.Combobox(op_frame, textvariable=self.op_mode_var, values=["None (Fastest)", "Enable Quality Estimation", "Enable Auto-CRF Search"], state="readonly", width=25)
        self.op_mode_cb.pack(side=tk.LEFT, padx=(0, 15))
        self.op_mode_cb.bind("<<ComboboxSelected>>", self.on_op_mode_change)

        self.benchmark_var = tk.BooleanVar(value=False)
        self.benchmark_chk = ttk.Checkbutton(op_frame, text="Analyze Only (Skip Full Encode)", variable=self.benchmark_var, command=self.on_benchmark_toggle)
        self.benchmark_chk.pack(side=tk.LEFT, padx=(5, 5))

        self.samp_lf = ttk.LabelFrame(metrics_frame, text=" Global Sampling Settings (Applies to Estimation & Auto-CRF) ", padding="5")
        self.samp_lf.pack(fill=tk.X, pady=(0, 5))

        row5 = ttk.Frame(self.samp_lf)
        row5.pack(fill=tk.X, pady=2)
        
        ttk.Label(row5, text="Samples (Count):").pack(side=tk.LEFT, padx=(5, 2))
        self.samples_count_var = tk.StringVar(value="0")
        self.samples_count_entry = ttk.Spinbox(row5, from_=0, to=999, textvariable=self.samples_count_var, width=5)
        self.samples_count_entry.pack(side=tk.LEFT, padx=(0, 15))
        ttk.Label(row5, text="(Overrides interval)").pack(side=tk.LEFT, padx=(0, 15))

        self.sample_interval_lbl = ttk.Label(row5, text="Sample-Every (min):")
        self.sample_interval_lbl.pack(side=tk.LEFT, padx=(0, 2))
        self.sample_interval_var = tk.StringVar(value="12")
        self.sample_interval_entry = ttk.Spinbox(row5, from_=1, to=999, textvariable=self.sample_interval_var, width=5)
        self.sample_interval_entry.pack(side=tk.LEFT, padx=(0, 15))
        
        ttk.Label(row5, text="Sample-Duration (s):").pack(side=tk.LEFT, padx=(0, 2))
        self.sample_duration_var = tk.StringVar(value="20")
        self.sample_duration_entry = ttk.Spinbox(row5, from_=1, to=999, textvariable=self.sample_duration_var, width=5)
        self.sample_duration_entry.pack(side=tk.LEFT, padx=(0, 15))

        row6 = ttk.Frame(self.samp_lf)
        row6.pack(fill=tk.X, pady=2)
        ttk.Label(row6, text="Samples Folder:").pack(side=tk.LEFT, padx=(5, 2))
        self.samples_loc_var = tk.StringVar(value="System Temp")
        self.samples_loc_cb = ttk.Combobox(row6, textvariable=self.samples_loc_var, values=["System Temp", "Next to Original"], state="readonly", width=16)
        self.samples_loc_cb.pack(side=tk.LEFT, padx=(0, 15))

        self.keep_samples_var = tk.BooleanVar(value=True)
        self.keep_samples_chk = ttk.Checkbutton(row6, text="Keep Samples (Extract Worst/Mid/Best Frames)", variable=self.keep_samples_var)
        self.keep_samples_chk.pack(side=tk.LEFT, padx=(0, 15))

        self.use_cache_var = tk.BooleanVar(value=True)
        self.use_cache_chk = ttk.Checkbutton(row6, text="Use Cache (Skip redundant tests)", variable=self.use_cache_var)
        self.use_cache_chk.pack(side=tk.LEFT, padx=(0, 5))

        self.qm_notebook = ttk.Notebook(metrics_frame)
        self.qm_notebook.pack(fill=tk.BOTH, expand=True)
        
        self.tab_eval = ttk.Frame(self.qm_notebook, padding="5")
        self.tab_autocrf = ttk.Frame(self.qm_notebook, padding="5")
        
        self.qm_notebook.add(self.tab_eval, text=" Quality Estimation ")
        self.qm_notebook.add(self.tab_autocrf, text=" Auto-CRF Search ")
        self.qm_notebook.bind("<<NotebookTabChanged>>", lambda e: self.adjust_notebook_height())

        # --- SUB-TAB 1: Quality Estimation ---
        qm_lf = ttk.LabelFrame(self.tab_eval, text=" Quality Testing & Metrics ", padding="5")
        qm_lf.pack(fill=tk.X, pady=(0, 5))

        row4 = ttk.Frame(qm_lf)
        row4.pack(fill=tk.X, pady=2)
        
        ttk.Label(row4, text="Estimation Stage:").pack(side=tk.LEFT, padx=(5, 2))
        self.eval_submode_var = tk.StringVar(value="Both (Pre & Post)")
        self.eval_submode_cb = ttk.Combobox(row4, textvariable=self.eval_submode_var, values=["Pre-Encode Estimate", "Post-Encode Verification", "Both (Pre & Post)"], state="readonly", width=22)
        self.eval_submode_cb.pack(side=tk.LEFT, padx=(0, 15))
        self.eval_submode_cb.bind("<<ComboboxSelected>>", self.on_eval_submode_change)

        self.merge_samples_var = tk.BooleanVar(value=True)
        self.merge_samples_chk = ttk.Checkbutton(row4, text="Merge Images (Side-by-Side)", variable=self.merge_samples_var)
        
        row4_1 = ttk.Frame(qm_lf)
        row4_1.pack(fill=tk.X, pady=(5, 2))
        ttk.Label(row4_1, text="Metrics:").pack(side=tk.LEFT, padx=(5, 5))
        self.met_inner_frame = ttk.Frame(row4_1)
        self.met_inner_frame.pack(side=tk.LEFT)
        self.metric_vars = {}
        for m in ["VMAF", "xPSNR", "SSIMULACRA2", "Butteraugli", "CVVDP"]:
            var = tk.BooleanVar(value=(m == "VMAF"))
            self.metric_vars[m] = var
            ttk.Checkbutton(self.met_inner_frame, text=m, variable=var).pack(side=tk.LEFT, padx=3)

        # --- SUB-TAB 2: Auto-CRF Search ---
        autocrf_lf = ttk.LabelFrame(self.tab_autocrf, text=" Target Quality Preferences ", padding="5")
        autocrf_lf.pack(fill=tk.X, pady=(0, 5))
        
        row_auto1 = ttk.Frame(autocrf_lf)
        row_auto1.pack(fill=tk.X, pady=5)
        
        ttk.Label(row_auto1, text="Target Metric:").pack(side=tk.LEFT, padx=(5, 2))
        self.autocrf_metric_var = tk.StringVar(value="VMAF")
        self.autocrf_metric_cb = ttk.Combobox(row_auto1, textvariable=self.autocrf_metric_var, values=["VMAF", "xPSNR", "SSIMULACRA2", "Butteraugli", "CVVDP"], state="readonly", width=14)
        self.autocrf_metric_cb.pack(side=tk.LEFT, padx=(0, 15))
        self.autocrf_metric_cb.bind("<<ComboboxSelected>>", self.on_autocrf_metric_change)
        
        self.autocrf_score_lbl = ttk.Label(row_auto1, text="Min Target Score:")
        self.autocrf_score_lbl.pack(side=tk.LEFT, padx=(5, 2))
        self.autocrf_score_var = tk.StringVar(value="93.0")
        self.autocrf_score_spinbox = ttk.Spinbox(row_auto1, from_=70.0, to=99.0, increment=0.5, format="%.1f", textvariable=self.autocrf_score_var, width=7)
        self.autocrf_score_spinbox.pack(side=tk.LEFT, padx=(0, 15))
        
        ttk.Label(row_auto1, text="Max Size Limit (% of Original):").pack(side=tk.LEFT, padx=(5, 2))
        self.autocrf_size_var = tk.StringVar(value="30")
        ttk.Spinbox(row_auto1, from_=0, to=100, increment=1, format="%.0f", textvariable=self.autocrf_size_var, width=6).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Label(row_auto1, text="(0 = No Limit)", font=("TkDefaultFont", 8, "italic")).pack(side=tk.LEFT, padx=(0, 5))

        row_auto2 = ttk.Frame(autocrf_lf)
        row_auto2.pack(fill=tk.X, pady=5)
        
        ttk.Label(row_auto2, text="Search Range (CRF) - Min:").pack(side=tk.LEFT, padx=(5, 2))
        self.autocrf_min_var = tk.StringVar(value="20.0")
        ttk.Spinbox(row_auto2, from_=0, to=51, increment=0.5, format="%.1f", textvariable=self.autocrf_min_var, width=6).pack(side=tk.LEFT, padx=(0, 15))
        
        ttk.Label(row_auto2, text="Max:").pack(side=tk.LEFT, padx=(0, 2))
        self.autocrf_max_var = tk.StringVar(value="40.0")
        ttk.Spinbox(row_auto2, from_=0, to=51, increment=0.5, format="%.1f", textvariable=self.autocrf_max_var, width=6).pack(side=tk.LEFT, padx=(0, 25))

        # ================= TAB 4: Advanced & Filters =================
        adv_frame = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(adv_frame, text=" Advanced & Filters ")

        trim_frame = ttk.LabelFrame(adv_frame, text=" Range & Trimming (Applied globally to queue) ", padding="5")
        trim_frame.pack(fill=tk.X, pady=(0, 5))

        row_trim = ttk.Frame(trim_frame)
        row_trim.pack(fill=tk.X, pady=2)
        ttk.Label(row_trim, text="Mode:").pack(side=tk.LEFT, padx=(5, 2))
        self.range_mode_var = tk.StringVar(value="Full Video")
        self.range_combo = ttk.Combobox(row_trim, textvariable=self.range_mode_var, values=["Full Video", "Time (Seconds)", "Time (hh:mm:ss)", "Frames", "Chapters"], state="readonly", width=16)
        self.range_combo.pack(side=tk.LEFT, padx=(0, 15))
        self.range_combo.bind("<<ComboboxSelected>>", self.on_range_mode_change)

        ttk.Label(row_trim, text="Start:").pack(side=tk.LEFT, padx=(0, 2))
        self.range_start_var = tk.StringVar(value="0")
        self.range_start_entry = ttk.Entry(row_trim, textvariable=self.range_start_var, width=10, state=tk.DISABLED)
        self.range_start_entry.pack(side=tk.LEFT, padx=(0, 15))

        ttk.Label(row_trim, text="End:").pack(side=tk.LEFT, padx=(0, 2))
        self.range_end_var = tk.StringVar(value="0")
        self.range_end_entry = ttk.Entry(row_trim, textvariable=self.range_end_var, width=10, state=tk.DISABLED)
        self.range_end_entry.pack(side=tk.LEFT, padx=(0, 15))

        self.range_hint = ttk.Label(row_trim, text="", font=("TkDefaultFont", 8, "italic"))
        self.range_hint.pack(side=tk.LEFT, padx=(0, 5))

        qf_frame = ttk.LabelFrame(adv_frame, text=" Quick Video Filters ", padding="5")
        qf_frame.pack(fill=tk.X, pady=5)

        row7 = ttk.Frame(qf_frame)
        row7.pack(fill=tk.X, pady=2)
        ttk.Label(row7, text="Resize/Scale:").pack(side=tk.LEFT, padx=(5, 2))
        self.scale_var = tk.StringVar(value="Original")
        ttk.Combobox(row7, textvariable=self.scale_var, values=["Original", "4K (2160p)", "1440p", "1080p", "720p", "480p"], state="readonly", width=14).pack(side=tk.LEFT, padx=(0, 15))

        self.auto_crop_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row7, text="Auto Crop Black Bars", variable=self.auto_crop_var).pack(side=tk.LEFT, padx=(0, 15))

        self.deint_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row7, text="Deinterlace (yadif)", variable=self.deint_var).pack(side=tk.LEFT, padx=(0, 15))

        ttk.Label(row7, text="Auto Fade:").pack(side=tk.LEFT, padx=(0, 2))
        self.fade_mode_var = tk.StringVar(value="None")
        self.fade_mode_cb = ttk.Combobox(row7, textvariable=self.fade_mode_var, values=["None", "Fade In", "Fade Out", "Both"], state="readonly", width=8)
        self.fade_mode_cb.pack(side=tk.LEFT, padx=(0, 5))

        ttk.Label(row7, text="Dur (s):").pack(side=tk.LEFT, padx=(0, 2))
        self.fade_dur_var = tk.StringVar(value="1.5")
        self.fade_dur_entry = ttk.Entry(row7, textvariable=self.fade_dur_var, width=5)
        self.fade_dur_entry.pack(side=tk.LEFT, padx=(0, 5))

        self.custom_cmd_container = ttk.Frame(adv_frame)
        self.custom_cmd_container.pack(fill=tk.X, pady=5)

        self.cf_ffmpeg_frame = ttk.LabelFrame(self.custom_cmd_container, text=" Custom FFmpeg Filters ", padding="5")
        
        row8 = ttk.Frame(self.cf_ffmpeg_frame)
        row8.pack(fill=tk.X, pady=2)
        ttk.Label(row8, text="Video (-vf):").pack(side=tk.LEFT, padx=(5, 2))
        self.custom_vf_var = tk.StringVar(value="")
        self.custom_vf_entry = ttk.Entry(row8, textvariable=self.custom_vf_var)
        self.custom_vf_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Label(row8, text="e.g. hqdn3d=1.5:1.5:6:6", font=("TkDefaultFont", 8, "italic")).pack(side=tk.LEFT, padx=(0, 5))

        row9 = ttk.Frame(self.cf_ffmpeg_frame)
        row9.pack(fill=tk.X, pady=2)
        ttk.Label(row9, text="Audio (-af):").pack(side=tk.LEFT, padx=(5, 2))
        self.custom_af_var = tk.StringVar(value="")
        self.custom_af_entry = ttk.Entry(row9, textvariable=self.custom_af_var)
        self.custom_af_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Label(row9, text="e.g. volume=1.5", font=("TkDefaultFont", 8, "italic")).pack(side=tk.LEFT, padx=(0, 42))

        self.cf_hb_frame = ttk.LabelFrame(self.custom_cmd_container, text=" Custom HandBrake Params ", padding="5")

        row10 = ttk.Frame(self.cf_hb_frame)
        row10.pack(fill=tk.X, pady=2)
        ttk.Label(row10, text="Extra Args:").pack(side=tk.LEFT, padx=(5, 2))
        self.custom_hb_var = tk.StringVar(value="")
        self.custom_hb_entry = ttk.Entry(row10, textvariable=self.custom_hb_var)
        self.custom_hb_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Label(row10, text="e.g. --rotate 4", font=("TkDefaultFont", 8, "italic")).pack(side=tk.LEFT, padx=(0, 5))

        # ================= TAB 5: About =================
        about_frame = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(about_frame, text=" About ")

        about_lf = ttk.LabelFrame(about_frame, text=" Software Information ", padding="15")
        about_lf.pack(fill=tk.BOTH, expand=True, pady=5)

        desc_text = (
            f"QualiSVT v{APP_VERSION}\n"
            "An advanced batch video encoder and quality assessment tool.\n"
            "Specifically designed and optimized for custom SVT-AV1 forks, including SVT-AV1-HDR and HandBrake-SVT-AV1-Tritium."
        )
        ttk.Label(about_lf, text=desc_text, wraplength=900, justify=tk.LEFT, font=("TkDefaultFont", 10)).pack(anchor=tk.W, pady=(0, 15))

        ttk.Label(about_lf, text="Author: Simorq (assisted by Google AI Studio)", font=("TkDefaultFont", 9, "bold")).pack(anchor=tk.W, pady=(0, 5))

        def make_link(parent, text, url):
            link_lbl = ttk.Label(parent, text=text, foreground="#87CEEB", cursor="hand2", font=("TkDefaultFont", 9, "underline"))
            link_lbl.pack(anchor=tk.W, padx=15, pady=2)
            link_lbl.bind("<Button-1>", lambda e, u=url: webbrowser.open_new_tab(u))

        make_link(about_lf, "🔗 QualiSVT GitHub Repository", "https://github.com/Cimorq/QualiSVT")

        ttk.Label(about_lf, text="Powered by these amazing open-source tools (Click to visit):", font=("TkDefaultFont", 9, "bold")).pack(anchor=tk.W, pady=(15, 5))

        make_link(about_lf, "• FFmpeg-Builds-SVT-AV1-HDR", "https://github.com/QuickFatHedgehog/FFmpeg-Builds-SVT-AV1-HDR/releases/tag/latest")
        make_link(about_lf, "• HandBrakeCLI (SVT-AV1-Tritium)", "https://github.com/Uranite/HandBrake-SVT-AV1-Tritium/releases/tag/win")
        make_link(about_lf, "• Vship (FFVship) : Fast Metric Computation on GPU", "https://codeberg.org/Line-fr/Vship")
        make_link(about_lf, "• Open Hardware Monitor", "https://github.com/HardwareMonitor/openhardwaremonitor")

        self.console_frame = ttk.LabelFrame(self.middle_container, text=" Logs & Output ", padding="5")
        self.console_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.console = scrolledtext.ScrolledText(self.console_frame, bg="black", fg="white", font=("Consolas", 9), state=tk.DISABLED, height=12)
        self.console.pack(fill=tk.BOTH, expand=True)
        
        self.console.tag_config("info", foreground="#FFFFFF")
        self.console.tag_config("header", foreground="#00FFFF", font=("Consolas", 9, "bold"))
        self.console.tag_config("success", foreground="#32CD32")
        self.console.tag_config("error", foreground="#FF4500")
        self.console.tag_config("warning", foreground="#FFD700")
        
        self.console.tag_config("q_vlossless", foreground="#00FFFF") 
        self.console.tag_config("q_super", foreground="#00FF7F")     
        self.console.tag_config("q_high", foreground="#9ACD32")      
        self.console.tag_config("q_med", foreground="#FFD700")       
        self.console.tag_config("q_low", foreground="#FF8C00")       
        self.console.tag_config("q_bad", foreground="#FF4500")       
        
        self.console.tag_config("svt_cfg", foreground="#87CEEB") 

        self.create_console_menu()
        
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_change)

        self.samples_count_var.trace_add("write", lambda *args: self.sync_samples_ui())

        self.on_engine_change(None)
        self.on_encoder_change(None)
        self.on_output_mode_change(None)
        self.on_op_mode_change(None)
        self.on_range_mode_change(None)
        self.on_eval_submode_change(None)
        self.on_audio_codec_change(None)
        self.on_gop_mode_change(None)

    def on_gop_mode_change(self, event=None):
        if hasattr(self, 'gop_mode_var'):
            if self.gop_mode_var.get() == "Custom":
                self.gop_custom_sec_lbl.config(state=tk.NORMAL)
                self.gop_custom_sec_entry.config(state=tk.NORMAL)
            else:
                self.gop_custom_sec_lbl.config(state=tk.DISABLED)
                self.gop_custom_sec_entry.config(state=tk.DISABLED)

    def on_audio_codec_change(self, event=None):
        if hasattr(self, 'audio_codec_var'):
            codec = self.audio_codec_var.get()
            if codec == "Copy":
                self.audio_br_cb.config(state=tk.DISABLED)
                self.audio_mixdown_cb.config(state=tk.DISABLED)
            else:
                self.audio_br_cb.config(state="readonly")
                self.audio_mixdown_cb.config(state="readonly")

    def adjust_notebook_height(self):
        try:
            self.root.update_idletasks()
            current_tab_id = self.notebook.select()
            if current_tab_id:
                selected_frame = self.notebook.nametowidget(current_tab_id)
                req_height = selected_frame.winfo_reqheight()
                self.notebook.config(height=req_height)
        except Exception:
            pass

    def create_console_menu(self):
        self.console_menu = tk.Menu(self.console, tearoff=0)
        self.console_menu.add_command(label="Copy", command=self.copy_console_text)
        self.console_menu.add_separator()
        self.console_menu.add_command(label="Select All", command=self.select_all_console_text)
        
        if platform.system() == "Darwin":
            self.console.bind("<Button-2>", self.show_console_menu)
            self.console.bind("<Control-Button-1>", self.show_console_menu)
        else:
            self.console.bind("<Button-3>", self.show_console_menu)

    def on_tab_change(self, event=None):
        try:
            current_tab = self.notebook.tab(self.notebook.select(), "text").strip()
            if current_tab == "About":
                self.middle_container.pack_forget()
            else:
                self.middle_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(5, 5))
            self.adjust_notebook_height()
        except Exception:
            pass

    def show_console_menu(self, event):
        try:
            self.console_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.console_menu.grab_release()

    def copy_console_text(self):
        try:
            selected_text = self.console.selection_get()
            self.root.clipboard_clear()
            self.root.clipboard_append(selected_text)
        except Exception:
            pass

    def select_all_console_text(self):
        self.console.tag_add(tk.SEL, "1.0", tk.END)
        self.console.mark_set(tk.INSERT, "1.0")
        self.console.see(tk.INSERT)
        return "break"

    def set_ui_state(self, disable=True):
        frames = [self.file_frame, self.notebook]
        for f in frames:
            self.toggle_frame_state(f, disable)
        
        self.power_cb.configure(state="readonly")
        
        if not disable:
            self.on_engine_change(None)
            self.on_encoder_change(None)
            self.on_output_mode_change(None)
            self.on_op_mode_change(None)
            self.on_range_mode_change(None)
            self.sync_samples_ui()
            self.on_benchmark_toggle()
            self.on_audio_codec_change(None)
            self.on_gop_mode_change(None)

    def sync_samples_ui(self):
        if self.op_mode_var.get() == "None (Fastest)":
            self.sample_interval_entry.config(state=tk.DISABLED)
            self.sample_interval_lbl.config(state=tk.DISABLED)
            return

        try:
            count = int(self.samples_count_var.get())
        except ValueError:
            count = 0
            
        if count > 0:
            self.sample_interval_entry.config(state=tk.DISABLED)
            self.sample_interval_lbl.config(state=tk.DISABLED)
        else:
            self.sample_interval_entry.config(state=tk.NORMAL)
            self.sample_interval_lbl.config(state=tk.NORMAL)

    def toggle_frame_state(self, widget, disable=True):
        try:
            if disable:
                if str(widget.cget("state")) != str(tk.DISABLED):
                    widget._original_state = widget.cget("state")
                widget.configure(state=tk.DISABLED)
            else:
                orig = getattr(widget, "_original_state", tk.NORMAL)
                widget.configure(state=orig)
        except Exception:
            pass
        for child in widget.winfo_children():
            self.toggle_frame_state(child, disable)

    def on_eval_submode_change(self, event=None):
        mode = getattr(self, "eval_submode_var", tk.StringVar()).get()
        if hasattr(self, "merge_samples_chk"):
            if mode in ["Post-Encode Verification", "Both (Pre & Post)"] and self.op_mode_var.get() == "Enable Quality Estimation":
                self.merge_samples_chk.pack(side=tk.LEFT, padx=(15, 5))
            else:
                self.merge_samples_chk.pack_forget()
        self.adjust_notebook_height()

    def on_op_mode_change(self, event=None):
        mode = self.op_mode_var.get()
        
        if mode == "None (Fastest)":
            self.benchmark_var.set(False)
            self.benchmark_chk.config(state=tk.DISABLED)
            self.toggle_frame_state(self.samp_lf, disable=True)
            
            self.toggle_frame_state(self.tab_eval, disable=True)
            self.toggle_frame_state(self.tab_autocrf, disable=True)
        elif mode == "Enable Quality Estimation":
            self.benchmark_chk.config(state=tk.NORMAL)
            self.toggle_frame_state(self.samp_lf, disable=False)
            
            self.toggle_frame_state(self.tab_eval, disable=False)
            self.toggle_frame_state(self.tab_autocrf, disable=True)
            self.qm_notebook.select(0)
            
            if self.benchmark_var.get():
                self.eval_submode_var.set("Pre-Encode Estimate")
                self.eval_submode_cb.config(state=tk.DISABLED)
            else:
                self.eval_submode_cb.config(state="readonly")
                
        elif mode == "Enable Auto-CRF Search":
            self.benchmark_chk.config(state=tk.NORMAL)
            self.toggle_frame_state(self.samp_lf, disable=False)
            
            self.toggle_frame_state(self.tab_eval, disable=True)
            self.toggle_frame_state(self.tab_autocrf, disable=False)
            self.qm_notebook.select(1)
            
        if mode != "None (Fastest)":
            self.on_benchmark_toggle()
        self.sync_samples_ui()
        self.on_eval_submode_change()
        self.adjust_notebook_height()

    def on_benchmark_toggle(self, event=None):
        is_bench = self.benchmark_var.get()
        cb_state = tk.DISABLED if is_bench else "readonly"
        btn_state = tk.DISABLED if is_bench else tk.NORMAL
        
        self.out_combo.config(state=cb_state)
        self.out_format_cb.config(state=cb_state)
        if hasattr(self, 'custom_folder_btn'): self.custom_folder_btn.config(state=btn_state)
        
        mode = self.op_mode_var.get()
        if mode == "Enable Quality Estimation":
            if is_bench:
                self.eval_submode_var.set("Pre-Encode Estimate")
                self.eval_submode_cb.config(state=tk.DISABLED)
            else:
                self.eval_submode_cb.config(state="readonly")

    def on_autocrf_metric_change(self, event=None):
        metric = self.autocrf_metric_var.get().strip().upper()
        if metric == "VMAF":
            self.autocrf_score_lbl.config(text="Min Target Score:")
            self.autocrf_score_spinbox.config(from_=70.0, to=99.0, increment=0.5, format="%.1f")
            if event: self.autocrf_score_var.set("93.0")
        elif metric == "SSIMULACRA2":
            self.autocrf_score_lbl.config(text="Min Target Score:")
            self.autocrf_score_spinbox.config(from_=60.0, to=99.0, increment=0.5, format="%.1f")
            if event: self.autocrf_score_var.set("65.0")
        elif metric == "BUTTERAUGLI":
            self.autocrf_score_lbl.config(text="Max Target Score:")
            self.autocrf_score_spinbox.config(from_=0.4, to=4.0, increment=0.1, format="%.2f")
            if event: self.autocrf_score_var.set("1.5")
        elif metric == "CVVDP":
            self.autocrf_score_lbl.config(text="Min Target Score:")
            self.autocrf_score_spinbox.config(from_=8.0, to=9.9, increment=0.1, format="%.2f")
            if event: self.autocrf_score_var.set("9.5")
        elif metric == "XPSNR":
            self.autocrf_score_lbl.config(text="Min Target Score:")
            self.autocrf_score_spinbox.config(from_=30.0, to=45.0, increment=0.5, format="%.1f")
            if event: self.autocrf_score_var.set("38.0")

    def is_valid_svt_preset(self, val):
        try:
            v = int(val)
            return -3 <= v <= 13
        except:
            return False

    def on_engine_change(self, event=None):
        engine = self.engine_var.get()
        if engine == "HandBrakeCLI":
            self.fade_mode_cb.config(state=tk.DISABLED)
            self.fade_dur_entry.config(state=tk.DISABLED)
            self.cf_ffmpeg_frame.pack_forget()
            self.cf_hb_frame.pack(fill=tk.X)
        else:
            self.fade_mode_cb.config(state="readonly")
            self.fade_dur_entry.config(state=tk.NORMAL)
            self.cf_hb_frame.pack_forget()
            self.cf_ffmpeg_frame.pack(fill=tk.X)
        self.on_encoder_change(None)

    def on_encoder_change(self, event=None):
        enc = self.encoder_var.get()
        self.crf_spinbox.config(from_=0, to=51, increment=0.5, format="%.1f")

        # HandBrakeCLI + SVT-AV1 is exposed as 8/10-bit only.
        # FFmpeg + SVT-AV1 and x265 keep their 12-bit option.
        if hasattr(self, "bit_depth_cb"):
            if self.engine_var.get() == "HandBrakeCLI" and enc == "SVT-AV1":
                self.bit_depth_cb.config(values=[
                    "8-bit (yuv420p)",
                    "10-bit (yuv420p10le)"
                ])
                if "12-bit" in self.bit_depth_var.get():
                    self.bit_depth_var.set("10-bit (yuv420p10le)")
            else:
                self.bit_depth_cb.config(values=[
                    "8-bit (yuv420p)",
                    "10-bit (yuv420p10le)",
                    "12-bit (yuv420p12le)"
                ])
            
        if enc == "SVT-AV1":
            self.preset_cb.config(values=[str(i) for i in range(-3, 11)])
            if not self.is_valid_svt_preset(self.preset_var.get()):
                self.preset_var.set("6")
                
            self.tune_cb.config(values=["0 (vq)", "1 (psnr)", "2 (ssim)", "3 (iq)", "4 (ms-ssim)", "5 (grain)"])
            if self.tune_var.get() not in ["0 (vq)", "1 (psnr)", "2 (ssim)", "3 (iq)", "4 (ms-ssim)", "5 (grain)"]:
                old_val = self.tune_var.get()
                mapping = {"0": "0 (vq)", "1": "1 (psnr)", "2": "2 (ssim)", "3": "3 (iq)", "4": "4 (ms-ssim)", "5": "5 (grain)", "vq": "0 (vq)"}
                self.tune_var.set(mapping.get(old_val, "0 (vq)"))
            
            self.row1_x265.pack_forget()
            self.row1_svt.pack(fill=tk.X, pady=2)
        else:
            self.preset_cb.config(values=["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow", "placebo"])
            if self.preset_var.get().lstrip('-').isdigit():
                self.preset_var.set("medium")
                
            self.tune_cb.config(values=["None", "film", "animation", "grain", "stillimage", "psnr", "ssim", "fastdecode", "zerolatency"])
            if self.tune_var.get() not in ["None", "film", "animation", "grain", "stillimage", "psnr", "ssim", "fastdecode", "zerolatency"]:
                self.tune_var.set("None")
                
            self.row1_svt.pack_forget()
            self.row1_x265.pack(fill=tk.X, pady=2)
            
        self.adjust_notebook_height()

    def save_settings(self):
        config = {
            "engine": self.engine_var.get(),
            "encoder": self.encoder_var.get(),
            "crf": self.crf_var.get(),
            "tune": self.tune_var.get(),
            "preset": self.preset_var.get(),
            "bit_depth": self.bit_depth_var.get(),
            "gop_mode": getattr(self, "gop_mode_var", tk.StringVar(value="10-Second GOP")).get(),
            "gop_custom_sec": getattr(self, "gop_custom_sec_var", tk.StringVar(value="10")).get(),
            "audio_track": self.audio_track_var.get(),
            "audio_codec": self.audio_codec_var.get(),
            "audio_br": self.audio_br_var.get(),
            "audio_mixdown": self.audio_mixdown_var.get(),
            "svt_params": self.svt_params_var.get(),
            "x265_params": self.x265_params_var.get(),
            "out_mode": self.out_mode_var.get(),
            "subfolder": self.subfolder_var.get(),
            "custom_folder": getattr(self, "custom_folder_var", tk.StringVar()).get(),
            "out_format": self.out_format_var.get(),
            "power": self.power_var.get(),
            "log_loc": self.log_loc_var.get(),
            "auto_resume": getattr(self, "auto_resume_var", tk.BooleanVar(value=False)).get(),
            "files_queue": self.files_to_process if getattr(self, "auto_resume_var", tk.BooleanVar(value=False)).get() else [],
            "op_mode": self.op_mode_var.get(),
            "eval_submode": self.eval_submode_var.get(),
            "benchmark": self.benchmark_var.get(),
            "metrics": {m: v.get() for m, v in self.metric_vars.items()},
            "samples_count": self.samples_count_var.get(),
            "sample_dur": self.sample_duration_var.get(),
            "sample_int": self.sample_interval_var.get(),
            "samples_loc": self.samples_loc_var.get(),
            "keep_samples": self.keep_samples_var.get(),
            "merge_samples": getattr(self, "merge_samples_var", tk.BooleanVar(value=True)).get(),
            "use_cache": self.use_cache_var.get(),
            "autocrf_metric": self.autocrf_metric_var.get(),
            "autocrf_score": self.autocrf_score_var.get(),
            "autocrf_min": self.autocrf_min_var.get(),
            "autocrf_max": self.autocrf_max_var.get(),
            "autocrf_size": self.autocrf_size_var.get(),
            "range_mode": self.range_mode_var.get(),
            "range_start": self.range_start_var.get(),
            "range_end": self.range_end_var.get(),
            "scale": self.scale_var.get(),
            "auto_crop": self.auto_crop_var.get(),
            "deint": self.deint_var.get(),
            "fade_mode": self.fade_mode_var.get(),
            "fade_dur": self.fade_dur_var.get(),
            "custom_vf": self.custom_vf_var.get(),
            "custom_af": self.custom_af_var.get(),
            "custom_hb": self.custom_hb_var.get(),
            "priority": self.priority_var.get(),
            "cpu_cores": self.cpu_cores_var.get()
        }
        try:
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4)
        except Exception:
            pass

    def load_settings(self):
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
            
            if "engine" in config: self.engine_var.set(config["engine"])
            if "encoder" in config: self.encoder_var.set(config["encoder"])
            if "crf" in config: self.crf_var.set(config["crf"])
            
            if "tune" in config:
                loaded_tune = config["tune"]
                if loaded_tune.isdigit():
                    mapping = {"0": "0 (vq)", "1": "1 (psnr)", "2": "2 (ssim)", "3": "3 (iq)", "4": "4 (ms-ssim)", "5": "5 (grain)"}
                    self.tune_var.set(mapping.get(loaded_tune, loaded_tune))
                else:
                    self.tune_var.set(loaded_tune)
                    
            if "preset" in config: self.preset_var.set(config["preset"])
            if "bit_depth" in config: self.bit_depth_var.set(config["bit_depth"])
            if "gop_mode" in config: getattr(self, "gop_mode_var", tk.StringVar()).set(config["gop_mode"])
            if "gop_custom_sec" in config: getattr(self, "gop_custom_sec_var", tk.StringVar()).set(config["gop_custom_sec"])
            if "audio_track" in config: self.audio_track_var.set(config["audio_track"])
            if "audio_codec" in config: self.audio_codec_var.set(config["audio_codec"])
            if "audio_br" in config: self.audio_br_var.set(config["audio_br"])
            if "audio_mixdown" in config: self.audio_mixdown_var.set(config["audio_mixdown"])
            if "svt_params" in config: self.svt_params_var.set(config["svt_params"])
            if "x265_params" in config: self.x265_params_var.set(config["x265_params"])
            if "out_mode" in config: self.out_mode_var.set(config["out_mode"])
            if "subfolder" in config: self.subfolder_var.set(config["subfolder"])
            if "custom_folder" in config: getattr(self, "custom_folder_var", tk.StringVar()).set(config["custom_folder"])
            if "out_format" in config: self.out_format_var.set(config["out_format"])
            if "power" in config: self.power_var.set(config["power"])
            if "log_loc" in config: self.log_loc_var.set(config["log_loc"])
            
            if "auto_resume" in config and hasattr(self, "auto_resume_var"):
                self.auto_resume_var.set(config["auto_resume"])
                
            if config.get("auto_resume") and "files_queue" in config:
                for f_path in config["files_queue"]:
                    if os.path.exists(f_path) and f_path not in self.files_to_process:
                        self.files_to_process.append(f_path)
                        self.listbox.insert(tk.END, f_path)

            if "op_mode" in config: self.op_mode_var.set(config["op_mode"])
            if "eval_submode" in config: self.eval_submode_var.set(config["eval_submode"])
            if "benchmark" in config: self.benchmark_var.set(config["benchmark"])
            
            if "metrics" in config:
                for m, v in config["metrics"].items():
                    if m in self.metric_vars:
                        self.metric_vars[m].set(v)
                        
            if "samples_count" in config: self.samples_count_var.set(config["samples_count"])
            if "sample_dur" in config: self.sample_duration_var.set(config["sample_dur"])
            if "sample_int" in config: self.sample_interval_var.set(config["sample_int"])
            if "samples_loc" in config: self.samples_loc_var.set(config["samples_loc"])
            if "keep_samples" in config: self.keep_samples_var.set(config["keep_samples"])
            if "merge_samples" in config and hasattr(self, "merge_samples_var"): self.merge_samples_var.set(config["merge_samples"])
            if "use_cache" in config: self.use_cache_var.set(config["use_cache"])
            
            if "autocrf_metric" in config: 
                self.autocrf_metric_var.set(config["autocrf_metric"])
                self.on_autocrf_metric_change(None)
            if "autocrf_score" in config: self.autocrf_score_var.set(config["autocrf_score"])
            if "autocrf_min" in config: self.autocrf_min_var.set(config["autocrf_min"])
            if "autocrf_max" in config: self.autocrf_max_var.set(config["autocrf_max"])
            if "autocrf_size" in config: self.autocrf_size_var.set(config["autocrf_size"])
            
            if "range_mode" in config: self.range_mode_var.set(config["range_mode"])
            if "range_start" in config: self.range_start_var.set(config["range_start"])
            if "range_end" in config: self.range_end_var.set(config["range_end"])
            if "scale" in config: self.scale_var.set(config["scale"])
            if "auto_crop" in config: self.auto_crop_var.set(config["auto_crop"])
            if "deint" in config: self.deint_var.set(config["deint"])
            if "fade_mode" in config: self.fade_mode_var.set(config["fade_mode"])
            if "fade_dur" in config: self.fade_dur_var.set(config["fade_dur"])
            if "custom_vf" in config: self.custom_vf_var.set(config["custom_vf"])
            if "custom_af" in config: self.custom_af_var.set(config["custom_af"])
            if "custom_hb" in config: self.custom_hb_var.set(config["custom_hb"])
            
            if "priority" in config: self.priority_var.set(config["priority"])
            if "cpu_cores" in config and config["cpu_cores"] in self.cpu_cores_cb['values']: 
                self.cpu_cores_var.set(config["cpu_cores"])
            
            self.on_engine_change(None)
            self.on_encoder_change(None)
            self.on_output_mode_change(None)
            self.on_op_mode_change(None)
            self.on_range_mode_change(None)
            self.sync_samples_ui()
            self.on_eval_submode_change(None)
            self.update_expected_output_path()
            self.adjust_notebook_height()
            self.on_audio_codec_change(None)
            self.on_gop_mode_change(None)
        except Exception:
            pass

    def on_closing(self):
        self.save_settings()
        if self.is_encoding:
            if messagebox.askyesno("Confirm Exit", "An encoding process is currently running.\n\nDo you want to stop it and exit?"):
                self.stop_encoding()
                self.root.destroy()
        else:
            self.root.destroy()

    def browse_output_folder(self):
        folder = filedialog.askdirectory(title="Select Output Folder")
        if folder:
            self.custom_folder_var.set(folder)
            self.update_expected_output_path()

    def update_expected_output_path(self, event=None):
        if not self.files_to_process:
            self.out_path_lbl.config(text="Expected Output: (Add a video file first)", foreground="gray")
            return
            
        first_file = self.files_to_process[0]
        file_dir, file_name = os.path.split(first_file)
        raw_name_we, file_ext = os.path.splitext(file_name)
        name_we = shorten_filename(raw_name_we, max_len=100)
        
        out_fmt_pref = getattr(self, "out_format_var", tk.StringVar(value="MKV")).get()
        target_ext = ".mp4" if out_fmt_pref == "MP4" else ".mkv"

        mode = getattr(self, "out_mode_var", tk.StringVar(value="Next to Original")).get()
        if mode == "Subfolder":
            sub_name = getattr(self, "subfolder_var", tk.StringVar(value="encoded")).get().strip() or "encoded"
            out_path = os.path.join(file_dir, sub_name, f"{name_we}_encoded{target_ext}")
        elif mode == "Browse Folder...":
            custom_dir = getattr(self, "custom_folder_var", tk.StringVar(value="")).get().strip()
            if not custom_dir: custom_dir = file_dir
            out_path = os.path.join(custom_dir, f"{name_we}_encoded{target_ext}")
        else: 
            out_path = os.path.join(file_dir, f"{name_we}_encoded{target_ext}")
        
        prefix = "Expected Output (1st file):" if len(self.files_to_process) > 1 else "Expected Output:"
        self.out_path_lbl.config(text=f"{prefix} {out_path}", foreground="#00FFFF")

    def on_output_mode_change(self, event):
        mode = self.out_mode_var.get()
        self.subfolder_entry.pack_forget()
        self.custom_folder_frame.pack_forget()

        if mode == "Subfolder":
            self.subfolder_entry.pack(side=tk.LEFT, padx=(0, 15), after=self.out_combo)
        elif mode == "Browse Folder...":
            self.custom_folder_frame.pack(side=tk.LEFT, padx=(0, 15), after=self.out_combo)
            
        self.update_expected_output_path()
        self.adjust_notebook_height()

    def on_range_mode_change(self, event):
        mode = self.range_mode_var.get()
        if mode == "Full Video":
            self.range_start_entry.config(state=tk.DISABLED)
            self.range_end_entry.config(state=tk.DISABLED)
            self.range_hint.config(text="")
        else:
            self.range_start_entry.config(state=tk.NORMAL)
            self.range_end_entry.config(state=tk.NORMAL)
            if mode == "Time (hh:mm:ss)":
                self.range_hint.config(text="e.g. 00:01:30 to 00:05:00")
            elif mode == "Time (Seconds)":
                self.range_hint.config(text="e.g. 90.5 to 300.0")
            elif mode == "Frames":
                self.range_hint.config(text="e.g. 1500 to 4000")
            elif mode == "Chapters":
                self.range_hint.config(text="e.g. 1 to 3")

    def log(self, message, tag="info"):
        self.console.config(state=tk.NORMAL)
        self.console.insert(tk.END, message + "\n", tag)
        self.console.see(tk.END)
        self.console.config(state=tk.DISABLED)
        self.root.update_idletasks()

    def write_debug_log(self, name_we, timestamp):
        try:
            debug_dir = os.path.join(APP_DIR, "Logs", "DEBUG")
            os.makedirs(debug_dir, exist_ok=True)
            debug_file = os.path.join(debug_dir, f"{name_we}_DEBUG_{timestamp}.txt")
            with open(debug_file, "w", encoding="utf-8") as df:
                df.write(self.console.get(1.0, tk.END))
        except Exception as e:
            self.log(f" -> Warning: Could not write DEBUG log: {e}", tag="warning")

    def write_log_file(self, file_path, content, mode="a"):
        if not file_path or self.log_loc_var.get() == "Don't Save": return
        try:
            with open(file_path, mode, encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            self.log(f" -> Warning: Could not write to log file: {e}", tag="warning")

    def get_original_file_specs(self, input_file):
        cmd = [FFPROBE_EXE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,width,height,display_aspect_ratio,r_frame_rate,bit_rate:format=format_name,duration,bit_rate", "-of", "json", input_file]
        cmd_a = [FFPROBE_EXE, "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_name", "-of", "json", input_file]
        
        format_name = "Unknown"
        v_codec = "Unknown"
        a_codecs = []
        bitrate = "Unknown"
        duration_str = "Unknown"
        resolution = "Unknown"
        aspect_ratio = "N/A"
        framerate = "Unknown"

        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=C_FLAGS)
            data = json.loads(res.stdout)
            
            format_info = data.get("format", {})
            f_name = format_info.get("format_name", "Unknown").split(',')[0].strip()
            if f_name.lower() != "unknown":
                format_name = f_name.upper()
            
            dur = float(format_info.get("duration", 0))
            if dur > 0:
                duration_str = format_duration(dur)
            
            streams = data.get("streams", [])
            if streams:
                v_stream = streams[0]
                vc = str(v_stream.get("codec_name", "Unknown"))
                if vc.lower() != "unknown":
                    v_codec = vc.upper()
                w = v_stream.get("width", 0)
                h = v_stream.get("height", 0)
                if w and h:
                    resolution = f"{w}x{h}"
                
                ar = v_stream.get("display_aspect_ratio", "")
                if ar and ar != "0:1":
                    aspect_ratio = ar
                    
                fps = v_stream.get("r_frame_rate", "")
                if fps and '/' in fps:
                    num, den = fps.split('/')
                    if float(den) > 0:
                        framerate = f"{float(num)/float(den):.3f} FPS"
                elif fps:
                    framerate = f"{fps} FPS"
                    
                br = v_stream.get("bit_rate") or format_info.get("bit_rate")
                if br:
                    bitrate = f"{int(br)//1000} kbps"
        except Exception:
            pass
            
        try:
            res_a = subprocess.run(cmd_a, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=C_FLAGS)
            data_a = json.loads(res_a.stdout)
            a_streams = data_a.get("streams", [])
            for a in a_streams:
                c = str(a.get("codec_name", "Unknown")).upper()
                if c != "UNKNOWN" and c not in a_codecs:
                    a_codecs.append(c)
        except Exception:
            pass

        try:
            sz_bytes = os.path.getsize(input_file)
            size_str = f"{sz_bytes / (1024*1024):.2f} MB"
        except Exception:
            size_str = "Unknown"

        a_codec_str = ", ".join(a_codecs) if a_codecs else "None"

        log_str = (
            " [ Original File Information ]\n"
            f"   - Container      : {format_name}\n"
            f"   - Video Codec    : {v_codec}\n"
            f"   - Audio Codec(s) : {a_codec_str}\n"
            f"   - Resolution     : {resolution} ({aspect_ratio})\n"
            f"   - Frame Rate     : {framerate}\n"
            f"   - Bitrate        : {bitrate}\n"
            f"   - Duration       : {duration_str}\n"
            f"   - File Size      : {size_str}\n\n"
        )
        return log_str

    def save_current_log(self, log_file):
        if not log_file: return
        d = self.current_log_data
        if not d: return
        
        is_bench = self.benchmark_var.get()
        title_log = "                ANALYZE ONLY SUMMARY (NO OUTPUT GENERATED)            " if is_bench else "                     ENCODING LOG"

        final_log = (
            "======================================================================\n"
            f"{title_log}\n"
            "======================================================================\n"
            f" File       : {d.get('file', 'Unknown')}\n"
            f" Start Time : {d.get('start', 'Unknown')}\n"
            f" End Time   : {d.get('end', 'Running...')}\n\n"
        )
        
        if d.get("orig_specs"): final_log += d["orig_specs"]
        
        if d.get("settings"): final_log += d["settings"]
        if d.get("pre"): final_log += d["pre"]
        if d.get("enc"): final_log += d["enc"]
        if d.get("post"): final_log += d["post"]
        
        specs = d.get('specs', {})
        final_log += (
            " [ System Specifications ]\n"
            f"   - OS   : {specs.get('OS', 'Unknown')}\n"
            f"   - CPU  : {specs.get('CPU', 'Unknown')}\n"
            f"   - RAM  : {specs.get('RAM', 'Unknown')}\n"
            f"   - GPU  : {specs.get('GPU', 'Unknown')}\n\n"
        )
        
        final_log += "======================================================================\n"
        self.write_log_file(log_file, final_log, "w")

    def update_status(self, text):
        if self.is_paused:
            self.last_status = text
        else:
            self.status_lbl.config(text=text)
            self.root.update_idletasks()

    def update_stats(self, percent, fps, avg_fps, eta, curr_time=""):
        self.progress_var.set(percent)
        self.stats_lbl.config(text=f"{percent:.1f}% ({curr_time}) | FPS: {fps:05.1f} | Avg: {avg_fps:05.1f} | ETA: {eta}")
        self.root.update_idletasks()

    def handle_drop(self, event):
        raw_data = event.data
        if "{" in raw_data:
            files = re.findall(r'\{(.*?)\}', raw_data)
        else:
            files = raw_data.split()
            
        for f in files:
            if f.lower().endswith(VIDEO_EXTENSIONS) and f not in self.files_to_process:
                self.files_to_process.append(f)
                self.listbox.insert(tk.END, f)
        self.update_expected_output_path()
        self.save_settings()

    def add_files(self):
        files = filedialog.askopenfilenames(title="Select Videos", filetypes=[("Video Files", "*.mp4 *.mkv *.avi *.mov *.flv *.wmv *.webm *.m4v")])
        for f in files:
            if f not in self.files_to_process:
                self.files_to_process.append(f)
                self.listbox.insert(tk.END, f)
        self.update_expected_output_path()
        self.save_settings()

    def add_folder(self):
        folder = filedialog.askdirectory(title="Select Folder")
        if folder:
            for root_dir, _, files in os.walk(folder):
                for file in files:
                    if file.lower().endswith(VIDEO_EXTENSIONS):
                        path = os.path.join(root_dir, file)
                        if path not in self.files_to_process:
                            self.files_to_process.append(path)
                            self.listbox.insert(tk.END, path)
        self.update_expected_output_path()
        self.save_settings()

    def remove_files(self):
        selected = list(self.listbox.curselection())
        selected.reverse()
        for idx in selected:
            self.listbox.delete(idx)
            del self.files_to_process[idx]
        self.update_expected_output_path()
        self.save_settings()

    def clear_files(self):
        self.listbox.delete(0, tk.END)
        self.files_to_process.clear()
        self.update_expected_output_path()
        self.save_settings()
        
    def get_adjusted_elapsed_time(self, start_time):
        paused = self.current_paused_duration
        if self.is_paused:
            paused += (time.time() - self.pause_start_time)
        return max(0, time.time() - start_time - paused)

    def toggle_encoding(self):
        if not self.is_encoding:
            self.start_encoding()
        else:
            self.stop_encoding()

    def toggle_pause(self):
        if not self.is_encoding or not self.current_process:
            return
            
        if self.is_paused:
            resume_subprocess(self.current_process.pid)
            self.is_paused = False
            self.btn_pause.config(text="Pause")
            self.current_paused_duration += time.time() - self.pause_start_time
            self.status_lbl.config(text=getattr(self, "last_status", "Status: Resumed"))
            self.root.update_idletasks()
            self.log("[+] Process resumed.", tag="success")
        else:
            pause_subprocess(self.current_process.pid)
            self.is_paused = True
            self.pause_start_time = time.time()
            self.btn_pause.config(text="Resume")
            self.last_status = self.status_lbl.cget("text")
            self.status_lbl.config(text="Status: Paused")
            self.root.update_idletasks()
            self.log("[!] Process paused.", tag="warning")

    def start_encoding(self):
        if not self.files_to_process:
            messagebox.showwarning("No Files", "Please add video files first.")
            return

        if self.engine_var.get() == "HandBrakeCLI" and not shutil.which(HANDBRAKE_EXE) and not os.path.isfile(HANDBRAKE_EXE):
            messagebox.showerror("Missing Dependency", "HandBrakeCLI was not found in your system PATH or app directory.\n\nPlease install it or switch the Engine back to FFmpeg.")
            return

        self.save_settings()

        self.is_encoding = True
        self.is_paused = False
        self.cancel_requested = False
        self.current_paused_duration = 0.0
        
        self.btn_start.config(text="Stop / Cancel")
        self.btn_pause.config(state=tk.NORMAL, text="Pause")
        
        self.set_ui_state(disable=True)
        
        self.console.config(state=tk.NORMAL)
        self.console.delete(1.0, tk.END)
        self.console.config(state=tk.DISABLED)
        
        threading.Thread(target=self.process_queue, daemon=True).start()

    def stop_encoding(self):
        if self.is_encoding:
            self.cancel_requested = True
            self.log("\n[!] Cancellation requested. Stopping current process...", tag="error")
            if self.is_paused:
                self.toggle_pause()
            if self.current_process:
                try:
                    self.current_process.kill()
                except: pass

    def calculate_gop(self, input_file, total_duration):
        gop_mode = getattr(self, "gop_mode_var", tk.StringVar(value="10-Second GOP")).get()
        if gop_mode == "Encoder Default":
            return None
        
        if total_duration < 180.0:
            return None
            
        try:
            if gop_mode == "Custom":
                target_sec = float(self.gop_custom_sec_var.get())
            else:
                target_sec = 10.0
        except ValueError:
            target_sec = 10.0
            
        vf_string = self.custom_vf_var.get().strip()
        fps_val = None
        if vf_string:
            match = re.search(r'fps=([\d\.]+)', vf_string)
            if match:
                try: fps_val = float(match.group(1))
                except: pass
                
        if not fps_val:
            fps_val = get_fps(input_file)
            if fps_val <= 0:
                fps_val = 24.0
                
        return int(round(fps_val * target_sec))

    def get_actual_readable_duration(self, file_path):
        cmd = [FFMPEG_EXE, "-err_detect", "ignore_err", "-i", file_path, "-c", "copy", "-f", "null", "-"]
        last_time = 0.0
        try:
            self.update_status("Status: Analyzing incomplete file duration...")
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, creationflags=C_FLAGS)
            time_pattern = re.compile(r"time=(\d{2}:\d{2}:\d{2}\.\d+)")
            
            while True:
                line = proc.stderr.readline()
                if not line and proc.poll() is not None:
                    break
                if line:
                    match = time_pattern.search(line)
                    if match:
                        last_time = parse_time(match.group(1))
            proc.wait()
        except Exception as e:
            self.log(f" -> Error reading incomplete file: {e}", tag="error")
        return last_time

    def concat_files(self, part1, part2, output):
        concat_txt = os.path.join(tempfile.gettempdir(), f"qual_concat_{int(time.time())}.txt")
        try:
            with open(concat_txt, "w", encoding="utf-8") as f:
                safe_part1 = os.path.abspath(part1).replace(os.sep, '/').replace("'", "'\\''")
                safe_part2 = os.path.abspath(part2).replace(os.sep, '/').replace("'", "'\\''")
                f.write(f"file '{safe_part1}'\n")
                f.write(f"file '{safe_part2}'\n")
    
            cmd = [FFMPEG_EXE, "-err_detect", "ignore_err", "-y", "-f", "concat", "-safe", "0", "-i", concat_txt, "-c", "copy", output]
            self.update_status("Status: Concatenating resumed parts...")
            proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, creationflags=C_FLAGS)
            
            if proc.returncode == 0 and os.path.exists(output):
                return True
            else:
                self.log(f" -> Concat failed: {proc.stderr}", tag="error")
                return False
        finally:
            if os.path.exists(concat_txt):
                try: os.remove(concat_txt)
                except: pass

    def detect_crop(self, input_file, duration):
        seek = max(0, duration * 0.2)
        cmd = [FFMPEG_EXE, "-y", "-ss", str(seek), "-i", input_file, "-t", "2", "-vf", "cropdetect=24:16:0", "-f", "null", "-"]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=C_FLAGS)
            matches = re.findall(r'crop=([0-9]+:[0-9]+:[0-9]+:[0-9]+)', res.stderr)
            if matches:
                return f"crop={Counter(matches).most_common(1)[0][0]}"
        except:
            pass
        return None

    def get_reference_filters(self):
        filters = []
        if self.auto_crop_var.get() and getattr(self, 'current_crop', None):
            filters.append(self.current_crop)
            
        scale_val = self.scale_var.get()
        if scale_val != "Original":
            if "4K" in scale_val: filters.append("scale='min(3840,iw)':'min(2160,ih)':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2")
            elif "1440p" in scale_val: filters.append("scale='min(2560,iw)':'min(1440,ih)':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2")
            elif "1080p" in scale_val: filters.append("scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2")
            elif "720p" in scale_val: filters.append("scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2")
            elif "480p" in scale_val: filters.append("scale='min(854,iw)':'min(480,ih)':force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2")
            
        return ",".join(filters) if filters else ""

    def get_exact_ref_filter(self, enc_w, enc_h):
        filters = []
        if self.auto_crop_var.get() and getattr(self, 'current_crop', None):
            filters.append(self.current_crop)
        if self.deint_var.get():
            filters.append("yadif=1")
        if enc_w and enc_h:
            filters.append(f"scale={enc_w}:{enc_h}:flags=lanczos")
        return ",".join(filters) if filters else ""

    def get_sample_filters(self):
        vf_list = []
        ref_vf = self.get_reference_filters()
        if ref_vf:
            vf_list.append(ref_vf)
            
        if self.deint_var.get():
            vf_list.append("yadif=1")
            
        cust_vf = self.custom_vf_var.get().strip()
        if cust_vf: 
            vf_list.append(cust_vf)
            
        return ",".join(vf_list) if vf_list else ""

    def get_handbrake_cmd(self, input_file, output_file, encode_duration=0, seek_start=0.0, apply_trim=False, with_audio=True, crf_override=None):
        cmd = [HANDBRAKE_EXE, "-i", input_file, "-o", output_file]
        
        out_fmt_pref = self.out_format_var.get()
        if out_fmt_pref == "MP4": cmd.extend(["-f", "av_mp4"])
        elif out_fmt_pref == "MKV": cmd.extend(["-f", "av_mkv"])
        
        enc = self.encoder_var.get()
        depth = self.bit_depth_var.get()
        hb_enc = "x265"
        if enc == "SVT-AV1":
            hb_enc = "svt_av1_10bit" if "10-bit" in depth else "svt_av1"
        else:
            if "10-bit" in depth: hb_enc = "x265_10bit"
            elif "12-bit" in depth: hb_enc = "x265_12bit"
            else: hb_enc = "x265"
            
        crf_val = f"{crf_override:g}" if isinstance(crf_override, (float, int)) else (str(crf_override) if crf_override is not None else str(self.crf_var.get()))
        cmd.extend(["-e", hb_enc, "-q", crf_val])
        
        preset = self.preset_var.get()
        if preset: cmd.extend(["--encoder-preset", preset])
        
        tune = self.tune_var.get()
        if tune and tune.lower() != "none":
            if enc == "SVT-AV1" and "(" in tune:
                tune_name = tune.split("(")[1].replace(")", "").strip()
                cmd.extend(["--encoder-tune", tune_name])
            elif enc != "SVT-AV1" and tune != "0":
                cmd.extend(["--encoder-tune", tune])
            
        params = self.svt_params_var.get() if enc == "SVT-AV1" else self.x265_params_var.get()
        
        gop_frames = getattr(self, "current_gop_frames", None)
        if gop_frames is not None:
            if params.strip():
                params = params.strip() + f":keyint={gop_frames}"
            else:
                params = f"keyint={gop_frames}"
                
        if params.strip():
            cmd.extend(["-x", params.strip()])
            
        if not with_audio:
            cmd.extend(["-a", "none"])
        else:
            track = self.audio_track_var.get()
            if track == "Track 1 (Default)":
                cmd.extend(["-a", "1"])
            else:
                cmd.extend(["--all-audio"])
                
            codec = self.audio_codec_var.get()
            if codec == "OPUS":
                cmd.extend(["-E", "opus", "-B", self.audio_br_var.get().replace("K", "")])
            elif codec == "AAC":
                cmd.extend(["-E", "av_aac", "-B", self.audio_br_var.get().replace("K", "")])
            elif codec == "Copy":
                cmd.extend(["-E", "copy", "--audio-fallback", "av_aac"])
                
            if codec != "Copy":
                mix = self.audio_mixdown_var.get()
                if mix == "Mono": cmd.extend(["-6", "mono"])
                elif mix == "Stereo": cmd.extend(["-6", "stereo"])
                elif mix == "5.1": cmd.extend(["-6", "5point1"])
                elif mix == "7.1": cmd.extend(["-6", "7point1"])
            
        if self.deint_var.get():
            cmd.extend(["--deinterlace", "yadif"])
            
        if self.auto_crop_var.get():
            cmd.extend(["--crop-mode", "auto"])
        else:
            cmd.extend(["--crop-mode", "none"])
            
        scale = self.scale_var.get()
        if scale != "Original":
            if "4K" in scale: cmd.extend(["--maxWidth", "3840", "--maxHeight", "2160"])
            elif "1440p" in scale: cmd.extend(["--maxWidth", "2560", "--maxHeight", "1440"])
            elif "1080p" in scale: cmd.extend(["--maxWidth", "1920", "--maxHeight", "1080"])
            elif "720p" in scale: cmd.extend(["--maxWidth", "1280", "--maxHeight", "720"])
            elif "480p" in scale: cmd.extend(["--maxWidth", "854", "--maxHeight", "480"])
            
        custom_hb = getattr(self, "custom_hb_var", tk.StringVar()).get().strip()
        if custom_hb:
            import shlex
            try: cmd.extend(shlex.split(custom_hb))
            except: cmd.extend(custom_hb.split())
            
        if apply_trim or seek_start > 0:
            r_mode = self.range_mode_var.get()
            if r_mode == "Chapters":
                start_val = self.range_start_var.get().strip() or "1"
                end_val = self.range_end_var.get().strip() or "1"
                cmd.extend(["-c", f"{start_val}-{end_val}"])
            else:
                if seek_start > 0:
                    cmd.extend(["--start-at", f"seconds:{seek_start:.3f}"])
                if apply_trim and encode_duration > 0:
                    cmd.extend(["--stop-at", f"seconds:{encode_duration:.3f}"])
                    
        return cmd

    def get_ffmpeg_cmd(self, input_file, output_file, target_duration=0, is_sample=False, with_audio=True, seek_start=0.0, apply_trim=False, skip_filters=False, crf_override=None):
        cmd = [FFMPEG_EXE, "-y"]
        
        if is_sample:
            cmd.extend(["-hide_banner", "-nostdin"])
            
        if not is_sample and seek_start > 0:
            cmd.extend(["-ss", str(seek_start)])
            
        cmd.extend(["-i", input_file])
        
        vf_list = []
        af_list = []
        
        if not is_sample:
            ref_vf = self.get_reference_filters()
            if ref_vf:
                vf_list.append(ref_vf)
                
            if self.deint_var.get():
                vf_list.append("yadif=1")
                
            fade_mode = self.fade_mode_var.get()
            if fade_mode != "None" and target_duration > 0:
                try: fdur = float(self.fade_dur_var.get())
                except: fdur = 1.5
                
                fade_start = max(0, target_duration - fdur)
                if fade_mode in ["Fade In", "Both"]:
                    vf_list.append(f"fade=t=in:st=0:d={fdur}")
                    af_list.append(f"afade=t=in:st=0:d={fdur}")
                if fade_mode in ["Fade Out", "Both"]:
                    vf_list.append(f"fade=t=out:st={fade_start:.3f}:d={fdur}")
                    af_list.append(f"afade=t=out:st={fade_start:.3f}:d={fdur}")
                
            cust_vf = self.custom_vf_var.get().strip()
            if cust_vf: vf_list.append(cust_vf)
            
            cust_af = self.custom_af_var.get().strip()
            if cust_af: af_list.append(cust_af)
        else:
            if not skip_filters:
                sample_vfs = self.get_sample_filters()
                if sample_vfs:
                    vf_list.append(sample_vfs)

        a_codec = self.audio_codec_var.get()
        if with_audio:
            if self.audio_track_var.get() == "Track 1 (Default)":
                cmd.extend(["-map", "0:v:0", "-map", "0:a:0?"])
            else:
                cmd.extend(["-map", "0:v:0", "-map", "0:a?"])
                
            if a_codec == "OPUS":
                cmd.extend(["-c:a", "libopus", "-b:a", self.audio_br_var.get(), "-vbr", "on", "-compression_level", "10"])
            elif a_codec == "AAC":
                cmd.extend(["-c:a", "aac", "-b:a", self.audio_br_var.get()])
            elif a_codec == "Copy":
                cmd.extend(["-c:a", "copy"])
                
            if a_codec != "Copy":
                mixdown = self.audio_mixdown_var.get()
                if mixdown == "Mono": cmd.extend(["-ac", "1"])
                elif mixdown == "Stereo": cmd.extend(["-ac", "2"])
                elif mixdown == "5.1": cmd.extend(["-ac", "6"])
                elif mixdown == "7.1": cmd.extend(["-ac", "8"])
                
            if output_file.lower().endswith(".mkv") and a_codec != "Copy":
                cmd.extend(["-c:s", "copy"])
                
            if af_list and a_codec != "Copy":
                cmd.extend(["-af", ",".join(af_list)])
        else:
            cmd.extend(["-map", "0:v:0", "-an", "-sn"])
            
        if vf_list:
            cmd.extend(["-vf", ",".join(vf_list)])
            
        depth_str = self.bit_depth_var.get()
        if "8-bit" in depth_str: pix_fmt = "yuv420p"
        elif "12-bit" in depth_str: pix_fmt = "yuv420p12le"
        else: pix_fmt = "yuv420p10le"
            
        encoder = self.encoder_var.get()
        crf_val = f"{crf_override:g}" if isinstance(crf_override, (float, int)) else (str(crf_override) if crf_override is not None else str(self.crf_var.get()))
        gop_frames = getattr(self, "current_gop_frames", None)
        
        if encoder == "x265 (HEVC)":
            cmd.extend([
                "-c:v", "libx265",
                "-crf", crf_val,
                "-pix_fmt", pix_fmt,
            ])
            preset = self.preset_var.get()
            if preset:
                cmd.extend(["-preset", preset])
            
            tune_val = self.tune_var.get().strip()
            if tune_val and tune_val.lower() != "none":
                cmd.extend(["-tune", tune_val])
                
            x265_p = self.x265_params_var.get().strip()
            if gop_frames is not None:
                if x265_p:
                    x265_p += f":keyint={gop_frames}"
                else:
                    x265_p = f"keyint={gop_frames}"
                    
            if x265_p:
                cmd.extend(["-x265-params", x265_p])
                
            if gop_frames is not None:
                cmd.extend(["-g", str(gop_frames)])
        else:
            cmd.extend([
                "-c:v", "libsvtav1",
                "-crf", crf_val,
                "-pix_fmt", pix_fmt,
                "-preset", self.preset_var.get(),
                "-dn", "-write_crc32", "false",
                "-cues_to_front", "y",
            ])
            
            if is_sample:
                cmd.extend(["-fps_mode", "passthrough"])
                
            base_svt_p = self.svt_params_var.get().strip()
            if gop_frames is not None:
                if base_svt_p:
                    base_svt_p += f":keyint={gop_frames}"
                else:
                    base_svt_p = f"keyint={gop_frames}"
                    
            tune_val = self.tune_var.get().strip()
            params_list = [p for p in base_svt_p.split(':') if p and not p.startswith('tune=')]
            
            if tune_val and tune_val.lower() != "none":
                if "(" in tune_val:
                    tune_num = tune_val.split(" ")[0].strip()
                    params_list.insert(0, f"tune={tune_num}")
                else:
                    params_list.insert(0, f"tune={tune_val}")
            
            final_svt_p = ':'.join(params_list)
            if final_svt_p:
                cmd.extend(["-svtav1-params", final_svt_p])
                
            if gop_frames is not None:
                cmd.extend(["-g", str(gop_frames)])
        
        if not is_sample and apply_trim and target_duration > 0:
            cmd.extend(["-t", str(target_duration)])
            
        cmd.append(output_file)
        return cmd
        
    def get_sampling_settings(self, target_duration):
        try: s_dur = float(self.sample_duration_var.get().strip())
        except: s_dur = 20.0
        
        try: s_count = int(self.samples_count_var.get().strip())
        except: s_count = 0
        
        try: s_interval = float(self.sample_interval_var.get().strip()) * 60
        except: s_interval = 12 * 60

        if target_duration <= 0:
            return s_dur, [0.0]

        if s_count > 0:
            num_samples = s_count
        else:
            if s_interval <= 0: s_interval = 12 * 60
            num_samples = math.ceil(target_duration / s_interval)
            if num_samples < 1:
                num_samples = 1

        if num_samples * s_dur >= target_duration:
            if s_dur >= target_duration:
                return target_duration, [0.0]
            num_samples = max(1, math.floor(target_duration / s_dur))

        gap = (target_duration - (s_dur * num_samples)) / (num_samples + 1)
        points = []
        
        for i in range(num_samples):
            start = gap * (i + 1) + s_dur * i
            
            if s_dur >= 2.0:
                start = float(math.floor(start))
                
            points.append(start)
                
        valid_points = []
        for p in points:
            p = max(0.0, p)
            if p + s_dur > target_duration:
                p = max(0.0, target_duration - s_dur)
            if p not in valid_points:
                valid_points.append(p)
                
        if not valid_points: valid_points = [0.0]
        return s_dur, valid_points

    def get_shared_cache_settings(self, crf_val, seek_offset, target_duration):
        return {
            "crf": f"{crf_val:g}" if isinstance(crf_val, float) else str(crf_val),
            "engine": self.engine_var.get(),
            "encoder": self.encoder_var.get(),
            "preset": self.preset_var.get(),
            "tune": self.tune_var.get(),
            "params": self.svt_params_var.get() if self.encoder_var.get() == "SVT-AV1" else self.x265_params_var.get(),
            "bit_depth": self.bit_depth_var.get(),
            "gop_mode": getattr(self, "gop_mode_var", tk.StringVar(value="10-Second GOP")).get(),
            "gop_custom_sec": getattr(self, "gop_custom_sec_var", tk.StringVar(value="10")).get(),
            "scale": self.scale_var.get(),
            "deint": self.deint_var.get(),
            "auto_crop": self.auto_crop_var.get(),
            "sample_dur": self.sample_duration_var.get(),
            "sample_int": self.sample_interval_var.get(),
            "samples_count": self.samples_count_var.get(),
            "seek_offset": seek_offset,
            "target_duration": target_duration
        }

    def extract_and_log_encoder_info(self, input_file, file_name, out_fmt_pref, r_mode, seek_start, encode_duration, crf_override=None):
        engine = self.engine_var.get()
        enc_name = self.encoder_var.get()
        active_params = self.x265_params_var.get() if enc_name == "x265 (HEVC)" else self.svt_params_var.get()
        
        gop_frames = getattr(self, "current_gop_frames", None)
        if gop_frames is not None:
            if active_params.strip():
                active_params = active_params.strip() + f":keyint={gop_frames}"
            else:
                active_params = f"keyint={gop_frames}"
        
        if crf_override is not None:
            if isinstance(crf_override, (int, float)):
                crf_cmd_val = f"{crf_override:g}"
                crf_log_val = f"{crf_override:g}"
            else:
                crf_cmd_val = str(self.crf_var.get())
                crf_log_val = str(crf_override)
        else:
            crf_cmd_val = str(self.crf_var.get())
            crf_log_val = str(self.crf_var.get())
        
        encoder_lines = []
        enc_version = None
        depth_str = self.bit_depth_var.get()

        if engine == "FFmpeg":
            cmd = [FFMPEG_EXE, "-y"]
            if seek_start > 0: cmd.extend(["-ss", str(seek_start)])
            cmd.extend(["-i", input_file])
            
            vf_list = []
            ref_vf = self.get_reference_filters()
            if ref_vf: vf_list.append(ref_vf)
            if self.deint_var.get(): vf_list.append("yadif=1")
            cust_vf = self.custom_vf_var.get().strip()
            if cust_vf: vf_list.append(cust_vf)
            if vf_list: cmd.extend(["-vf", ",".join(vf_list)])

            pix_fmt = "yuv420p12le" if "12-bit" in depth_str else ("yuv420p" if "8-bit" in depth_str else "yuv420p10le")

            if enc_name == "x265 (HEVC)":
                cmd.extend(["-c:v", "libx265", "-crf", crf_cmd_val, "-pix_fmt", pix_fmt])
                preset = self.preset_var.get()
                if preset: cmd.extend(["-preset", preset])
                tune_val = self.tune_var.get().strip()
                if tune_val and tune_val.lower() != "none": cmd.extend(["-tune", tune_val])
                if active_params: cmd.extend(["-x265-params", active_params])
                if gop_frames is not None: cmd.extend(["-g", str(gop_frames)])
            else:
                cmd.extend(["-c:v", "libsvtav1", "-crf", crf_cmd_val, "-pix_fmt", pix_fmt, "-preset", self.preset_var.get()])
                params_list = [p for p in active_params.split(':') if p and not p.startswith('tune=')]
                tune_val = self.tune_var.get().strip()
                if tune_val and tune_val.lower() != "none":
                    if "(" in tune_val:
                        tune_num = tune_val.split(" ")[0].strip()
                        params_list.insert(0, f"tune={tune_num}")
                    else:
                        params_list.insert(0, f"tune={tune_val}")
                final_svt_p = ':'.join(params_list)
                if final_svt_p: cmd.extend(["-svtav1-params", final_svt_p])
                if gop_frames is not None: cmd.extend(["-g", str(gop_frames)])
                active_params = final_svt_p

            cmd.extend(["-vframes", "1", "-f", "null", "-"])

            self.update_status("Status: Fetching encoder specific details (FFmpeg)...")
            proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, universal_newlines=True, creationflags=C_FLAGS)
            
            for line in proc.stderr.splitlines():
                line = line.strip()
                if "Svt[info]:" in line:
                    val = line.split("Svt[info]:", 1)[1].strip()
                    if val.startswith("SVT [version]:"): 
                        enc_version = val.replace("SVT [version]:", "").strip()
                    elif val.startswith("SVT [config]:"):
                        config_val = val.replace("SVT [config]:", "").strip()
                        if config_val:
                            encoder_lines.append(config_val)
                elif "x265 [info]:" in line:
                    val = line.split("x265 [info]:", 1)[1].strip()
                    if val.startswith("HEVC encoder version"): 
                        enc_version = val
                    else:
                        encoder_lines.append(val)
        else:
            self.update_status("Status: Reading generic encoder details (HandBrakeCLI)...")
            fd, tmp_out = tempfile.mkstemp(suffix=".mkv")
            os.close(fd)
            
            hb_cmd = self.get_handbrake_cmd(input_file, tmp_out, encode_duration=0, seek_start=0.0, apply_trim=False, with_audio=False, crf_override=crf_cmd_val)
            hb_cmd.extend(["--start-at", "frames:0", "--stop-at", "frames:1"])
            
            try:
                proc = subprocess.run(hb_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=C_FLAGS)
                for line in proc.stdout.splitlines():
                    line = line.strip()
                    if "Svt[info]:" in line:
                        val = line.split("Svt[info]:", 1)[1].strip()
                        if val.startswith("SVT [version]:"):
                            enc_version = val.replace("SVT [version]:", "").strip()
                        elif val.startswith("SVT [config]:"):
                            config_val = val.replace("SVT [config]:", "").strip()
                            if config_val:
                                encoder_lines.append(config_val)
                    elif "x265 [info]:" in line:
                        val = line.split("x265 [info]:", 1)[1].strip()
                        if val.startswith("HEVC encoder version"):
                            enc_version = val
                        else:
                            encoder_lines.append(val)
                    elif not enc_version and "HandBrake" in line and "master" in line:
                        enc_version = line.strip()
            except: pass
            finally:
                if os.path.exists(tmp_out):
                    try: os.remove(tmp_out)
                    except: pass
                    
            if enc_name == "SVT-AV1":
                params_list = [p for p in active_params.split(':') if p and not p.startswith('tune=')]
                active_params = ':'.join(params_list)

        self.log("\n [ Video Settings ]", tag="header")
        self.log(f"   - Engine         : {engine}", tag="svt_cfg")
        self.log(f"   - Container      : {out_fmt_pref.upper()}", tag="svt_cfg")
        self.log(f"   - Encoder        : {enc_name}", tag="svt_cfg")
        if enc_version: self.log(f"   - Version        : {enc_version}", tag="svt_cfg")
        self.log(f"   - CRF            : {crf_log_val}", tag="svt_cfg")
        self.log(f"   - Preset         : {self.preset_var.get()}", tag="svt_cfg")
        self.log(f"   - Tune           : {self.tune_var.get()}", tag="svt_cfg")
        self.log(f"   - Bit-Depth      : {self.bit_depth_var.get()}", tag="svt_cfg")
        self.log(f"   - Params         : {active_params}", tag="svt_cfg")
        if gop_frames is not None: self.log(f"   - Auto GOP       : ~{gop_frames} frames", tag="svt_cfg")

        for eline in encoder_lines:
            self.log(f"   - {eline}", tag="svt_cfg")

        if r_mode != "Full Video":
            self.log(f"   - Trim Active    : Start={format_duration(seek_start)} | Length={format_duration(encode_duration)}", tag="warning")
        
        active_vfs = [f for f in [
            self.scale_var.get() if self.scale_var.get() != "Original" else "",
            "Auto Crop" if self.auto_crop_var.get() and (self.current_crop or engine == "HandBrakeCLI") else "",
            "Yadif (Deinterlace)" if self.deint_var.get() else "",
            f"Auto {self.fade_mode_var.get()} ({self.fade_dur_var.get()}s)" if self.fade_mode_var.get() != "None" and engine == "FFmpeg" else "",
            self.custom_vf_var.get().strip() if engine == "FFmpeg" else ""
        ] if f]
        
        if active_vfs: self.log(f"   - V-Filters      : {', '.join(active_vfs)}", tag="svt_cfg")
        if self.custom_af_var.get().strip() and engine == "FFmpeg": self.log(f"   - A-Filters      : {self.custom_af_var.get().strip()}", tag="svt_cfg")
        if self.custom_hb_var.get().strip() and engine == "HandBrakeCLI": self.log(f"   - HB Params      : {self.custom_hb_var.get().strip()}", tag="svt_cfg")
        
        a_mix = self.audio_mixdown_var.get()
        a_codec = self.audio_codec_var.get()
        
        self.log(f"   - Audio          : Track={self.audio_track_var.get()} | {a_codec} | {self.audio_br_var.get()} | {a_mix}", tag="svt_cfg")

        header_file = (
            " [ Video Settings ]\n"
            f"   - Engine         : {engine}\n"
            f"   - Encoder        : {enc_name}\n"
            f"   - Container      : {out_fmt_pref.upper()}\n"
        )
        if enc_version: header_file += f"   - Version        : {enc_version}\n"
        header_file += (
            f"   - CRF            : {crf_log_val}\n"
            f"   - Preset         : {self.preset_var.get()}\n"
            f"   - Tune           : {self.tune_var.get()}\n"
            f"   - Bit-Depth      : {self.bit_depth_var.get()}\n"
            f"   - Params         : {active_params}\n"
        )
        if gop_frames is not None: header_file += f"   - Auto GOP       : ~{gop_frames} frames\n"

        for eline in encoder_lines:
            header_file += f"   - {eline}\n"

        if r_mode != "Full Video":
            header_file += f"   - Target Range   : Mode={r_mode} | Start={format_duration(seek_start)} | Length={format_duration(encode_duration)}\n"
        
        if active_vfs: header_file += f"   - V-Filters      : {', '.join(active_vfs)}\n"
        if self.custom_af_var.get().strip() and engine == "FFmpeg": header_file += f"   - A-Filters      : {self.custom_af_var.get().strip()}\n"
        if self.custom_hb_var.get().strip() and engine == "HandBrakeCLI": header_file += f"   - HB Params      : {self.custom_hb_var.get().strip()}\n"

        header_file += f"\n [ Audio Settings ]\n"
        header_file += f"   - Track          : {self.audio_track_var.get()}\n"
        header_file += f"   - Codec          : {a_codec}\n"
        header_file += f"   - Bitrate        : {self.audio_br_var.get()}\n"
        header_file += f"   - Mixdown        : {a_mix}\n\n"

        return header_file

    def run_auto_crf_search(self, input_file, target_duration, file_dir, name_we, seek_offset=0.0):
        self.current_paused_duration = 0.0
        
        try:
            target_metric = self.autocrf_metric_var.get().strip()
            target_score = float(self.autocrf_score_var.get().strip())
            min_crf = float(self.autocrf_min_var.get().strip())
            max_crf = float(self.autocrf_max_var.get().strip())
            max_pct = float(self.autocrf_size_var.get().strip())
        except ValueError:
            self.log("[!] Invalid Auto-CRF parameters. Falling back to default CRF.", tag="error")
            return None, {}, 0.0, 0.0, False

        is_lower_better = (target_metric.upper() == "BUTTERAUGLI")

        # --- Cache System (Full History) ---
        cache_settings = {
            "metric": target_metric,
            "score": target_score,
            "min_crf": min_crf,
            "max_crf": max_crf,
            "max_pct": max_pct,
            "engine": self.engine_var.get(),
            "encoder": self.encoder_var.get(),
            "preset": self.preset_var.get(),
            "tune": self.tune_var.get(),
            "params": self.svt_params_var.get() if self.encoder_var.get() == "SVT-AV1" else self.x265_params_var.get(),
            "bit_depth": self.bit_depth_var.get(),
            "gop_mode": getattr(self, "gop_mode_var", tk.StringVar(value="10-Second GOP")).get(),
            "gop_custom_sec": getattr(self, "gop_custom_sec_var", tk.StringVar(value="10")).get(),
            "scale": self.scale_var.get(),
            "deint": self.deint_var.get(),
            "auto_crop": self.auto_crop_var.get(),
            "sample_dur": self.sample_duration_var.get(),
            "sample_int": self.sample_interval_var.get(),
            "samples_count": self.samples_count_var.get(),
            "seek_offset": seek_offset,
            "target_duration": target_duration
        }
        
        sample_duration, sample_points = self.get_sampling_settings(target_duration)

        if self.use_cache_var.get():
            try:
                cached_result = check_cache(input_file, cache_settings, "autocrf")
                if cached_result:
                    self.log(f"\n[!] Auto-CRF Cache Hit! Restoring previous search history...", tag="q_super")
                    self.log(f"\n--- Starting Iterative Auto-CRF Search (CACHED) ---", tag="header")
                    if is_lower_better:
                        self.log(f" -> Goal: Find highest CRF where {target_metric} is \u2264 {target_score}")
                    else:
                        self.log(f" -> Goal: Find highest CRF where {target_metric} is \u2265 {target_score}")
                    if max_pct > 0: self.log(f" -> Constraint: Estimated Output Size Must Be \u2264 {max_pct}% Of Original")
                    self.log(f" -> Using {len(sample_points)} Sample Segments Per CRF Test")

                    for h in cached_result.get("history", []):
                        self.log(f" ├─ CRF {h['crf']:g}")
                        self.log(f" │    └─ Avg {target_metric}: {h['score']:.2f} (Est. Size: {h['pct']:.1f}%)")
                        if h.get('log_msg'): self.log(h['log_msg'], tag=h['log_tag'])
                        
                    if cached_result.get("success"):
                        self.log(f"\n -> Auto-CRF Search Complete. Selected Optimal CRF: {cached_result['best_crf']:g}", tag="q_super")
                        return (
                            cached_result['best_crf'], 
                            cached_result['scores'], 
                            cached_result['est_size'], 
                            cached_result['est_time'], 
                            True
                        )
                    else:
                        self.log(f"\n -> Auto-CRF Search Complete. No valid CRF found within constraints.", tag="error")
                        if cached_result.get('best_crf') is not None:
                            self.log(f" -> Returning last tested CRF for fallback: {cached_result['best_crf']:g}", tag="warning")
                        return (
                            cached_result.get('best_crf'), 
                            cached_result.get('scores', {}), 
                            cached_result.get('est_size', 0.0), 
                            cached_result.get('est_time', 0.0), 
                            False
                        )
            except Exception as e:
                self.log(f" -> Cache check error: {e}", tag="warning")
        # -----------------------------

        self.log(f"\n--- Starting Iterative Auto-CRF Search ---", tag="header")
        if is_lower_better:
            self.log(f" -> Goal: Find highest CRF where {target_metric} is \u2264 {target_score}")
        else:
            self.log(f" -> Goal: Find highest CRF where {target_metric} is \u2265 {target_score}")
        if max_pct > 0: self.log(f" -> Constraint: Estimated Output Size Must Be \u2264 {max_pct}% Of Original")
        self.log(f" -> Using {len(sample_points)} Sample Segments Per CRF Test")
            
        base_folder_name = ".QualiSVT"
        work_dir = os.path.join(tempfile.gettempdir() if self.samples_loc_var.get() == "System Temp" else file_dir, base_folder_name, f"{name_we}.autocrf")
        os.makedirs(work_dir, exist_ok=True)
        
        time_pattern = re.compile(r"time=(\d{2}:\d{2}:\d{2}\.\d+)")
        vid_fps = get_fps(input_file)
        if vid_fps <= 0: vid_fps = 24.0
        fps = vid_fps
        
        depth_str = self.bit_depth_var.get()
        pix_fmt = "yuv420p12le" if "12-bit" in depth_str else ("yuv420p" if "8-bit" in depth_str else "yuv420p10le")
        
        ref_pix_fmt = get_video_pix_fmt(input_file) or "yuv420p"
        allowed = {
            "yuv420p", "yuv422p", "yuv444p",
            "yuv420p10le", "yuv422p10le", "yuv444p10le",
            "yuv420p12le", "yuv422p12le", "yuv444p12le"
        }
        if ref_pix_fmt not in allowed:
            ref_pix_fmt = "yuv420p"

        has_ffvship = (target_metric.upper() in ["SSIMULACRA2", "BUTTERAUGLI", "CVVDP"])
        sample_vfs = self.get_sample_filters()
        use_ffv1_ref = bool(sample_vfs and has_ffvship)
        
        
        orig_samples = []
        filtered_refs = [None] * len(sample_points)
        encoded_dims = [None] * len(sample_points)
        expected_sizes = []
        
        input_vid_dur = max(1.0, get_duration(input_file))
        total_actual_sample_duration = 0

        try:
            # Extract one source sample per segment directly from the original input.
            # If filters are active and FFVship is required, the filtered FFV1
            # reference is created directly from input_file. We never create
            # orig_sample first and then transcode that file into filtered_ref.
            for i, point in enumerate(sample_points):
                if self.cancel_requested: return None, {}, 0.0, 0.0, False
                s_idx = i + 1
                s_tot = len(sample_points)
                actual_sample_duration = min(sample_duration, target_duration - point)
                if actual_sample_duration <= 0: actual_sample_duration = 1
                total_actual_sample_duration += actual_sample_duration

                seek_point_in_orig = seek_offset + point
                orig_sample = os.path.join(work_dir, f"sample{i:02d}_orig.mkv")
                filtered_ref = os.path.join(work_dir, f"sample{i:02d}_filtered_ref.mkv")
                extract_frames = int(actual_sample_duration * vid_fps)
                expected_sizes.append((os.path.getsize(input_file) / input_vid_dur) * actual_sample_duration)

                if use_ffv1_ref:
                    self.update_status(f"Status: Auto-CRF - Extracting Filtered Ref {s_idx}/{s_tot}...")
                    ext_cmd = [
                        FFMPEG_EXE, "-y", "-ss", str(seek_point_in_orig), "-i", input_file,
                        "-frames:v", str(extract_frames), "-vf", sample_vfs,
                        "-c:v", "ffv1", "-level", "3", "-pix_fmt", pix_fmt,
                        "-an", "-sn", filtered_ref
                    ]
                    self.current_process = subprocess.Popen(
                        ext_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=C_FLAGS
                    )
                    self.current_process.wait()
                    if self.cancel_requested: raise Exception("Cancelled")
                    orig_samples.append(filtered_ref)
                    filtered_refs[i] = filtered_ref
                else:
                    self.update_status(f"Status: Auto-CRF - Extracting Original Sample {s_idx}/{s_tot}...")
                    ext_cmd = [
                        FFMPEG_EXE, "-y", "-ss", str(seek_point_in_orig), "-i", input_file,
                        "-frames:v", str(extract_frames), "-c:v", "copy",
                        "-an", "-sn", orig_sample
                    ]
                    self.current_process = subprocess.Popen(
                        ext_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=C_FLAGS
                    )
                    self.current_process.wait()
                    if self.cancel_requested: raise Exception("Cancelled")
                    orig_samples.append(orig_sample)
                    filtered_refs[i] = orig_sample

            total_expected_size = sum(expected_sizes)

            # --- ab-av1 Smart CRF Search Logic ---
            crf_inc = 0.5
            min_q = int(round(min_crf / crf_inc))
            max_q = int(round(max_crf / crf_inc))
            q = (min_q + max_q) // 2
            
            best_crf = None
            best_res = None
            last_tested_crf = None
            last_tested_res = None
            history = []
            run_count = 1
            
            # ab-av1 heuristic: (max - min) > (default_max - default_min) * 0.5
            cut_on_iter2 = (max_crf - min_crf) > 23.0
            
            def vmaf_lerp_q(target, w_samp, b_samp):
                diff = b_samp["score"] - w_samp["score"]
                if diff == 0: 
                    return (w_samp["q"] + b_samp["q"]) // 2
                factor = (target - w_samp["score"]) / diff
                q_diff = w_samp["q"] - b_samp["q"]
                lerp = round(w_samp["q"] - q_diff * factor)
                return max(b_samp["q"] + 1, min(w_samp["q"] - 1, int(lerp)))

            while True:
                if self.cancel_requested: break
                
                q = max(min_q, min(max_q, q))
                next_crf = q * crf_inc
                
                self.update_status(f"Status: Auto-CRF - Testing CRF {next_crf:g}...")
                self.log(f" ├─ CRF {next_crf:g}")

                shared_settings = self.get_shared_cache_settings(next_crf, seek_offset, target_duration)
                shared_cached = None
                if self.use_cache_var.get():
                    shared_cached = check_cache(input_file, shared_settings, "crf_eval")
                    
                if shared_cached and target_metric in shared_cached.get("scores", {}):
                    avg_qual_score = shared_cached["scores"][target_metric]
                    est_total_size = shared_cached.get("est_size", 0)
                    est_total_time = shared_cached.get("est_time", 0)
                    
                    total_enc_size = (est_total_size / target_duration) * total_actual_sample_duration if target_duration > 0 else est_total_size
                    total_enc_time = (est_total_time / target_duration) * total_actual_sample_duration if target_duration > 0 else est_total_time
                    enc_pct = (total_enc_size / total_expected_size) * 100 if total_expected_size > 0 else 0
                    
                    res = (avg_qual_score, total_enc_size, total_enc_time, enc_pct)
                    self.log(f" │    └─ Avg {target_metric}: {avg_qual_score:.2f} (Est. Size: {enc_pct:.1f}%) [Cache Hit]")
                else:
                    total_enc_size = 0
                    total_enc_time = 0
                    all_scores = []
                    filter_error = False
                    
                    for i, point in enumerate(sample_points):
                        if self.cancel_requested: break
                        s_idx = i + 1
                        s_tot = len(sample_points)
                        enc_sample = os.path.join(work_dir, f"sample{i:02d}_enc.mkv")
                        actual_sample_duration = min(sample_duration, target_duration - point)
                        if actual_sample_duration <= 0: actual_sample_duration = 1
                        
                        source_to_encode = orig_samples[i]
                        if self.engine_var.get() == "HandBrakeCLI":
                            enc_cmd = self.get_handbrake_cmd(
                                source_to_encode, enc_sample, encode_duration=0,
                                seek_start=0.0, apply_trim=False, with_audio=False,
                                crf_override=next_crf
                            )
                        else:
                            enc_cmd = self.get_ffmpeg_cmd(
                                source_to_encode, enc_sample, actual_sample_duration,
                                is_sample=True, with_audio=False,
                                skip_filters=use_ffv1_ref, crf_override=next_crf
                            )

                        pipe_out = subprocess.PIPE if self.engine_var.get() == "HandBrakeCLI" else subprocess.DEVNULL
                        pipe_err = subprocess.STDOUT if self.engine_var.get() == "HandBrakeCLI" else subprocess.PIPE
                        
                        start_enc_time = time.time()
                        self.current_process = subprocess.Popen(enc_cmd, stdout=pipe_out, stderr=pipe_err, text=True, universal_newlines=True, creationflags=C_FLAGS)
                        self.apply_process_settings()
                        
                        while True:
                            if self.engine_var.get() == "HandBrakeCLI":
                                line = self.current_process.stdout.readline()
                            else:
                                line = self.current_process.stderr.readline()
                                
                            if not line and self.current_process.poll() is not None: break
                            if not line: continue
                            
                            if self.engine_var.get() == "HandBrakeCLI":
                                match_hb = re.search(r"(\d+(?:\.\d+)?)\s*%", line)
                                if match_hb:
                                    prog = float(match_hb.group(1))
                                    self.progress_var.set(prog)
                                    self.stats_lbl.config(text=f"Auto-CRF Encoding Chunk {s_idx}/{s_tot} (CRF {next_crf:g})... {prog:.1f}%")
                            else:
                                match_time = time_pattern.search(line)
                                if match_time:
                                    cur_sec = parse_time(match_time.group(1))
                                    prog = min((cur_sec / actual_sample_duration) * 100, 100.0)
                                    self.progress_var.set(prog)
                                    self.stats_lbl.config(text=f"Auto-CRF Encoding Chunk {s_idx}/{s_tot} (CRF {next_crf:g})... {prog:.1f}%")
                                    
                        self.current_process.wait()
                        enc_dur = self.get_adjusted_elapsed_time(start_enc_time)
                        total_enc_time += enc_dur
                        if self.cancel_requested: break
                        
                        enc_size = os.path.getsize(enc_sample) if os.path.exists(enc_sample) else 0
                        total_enc_size += enc_size

                        # --- Metric Reference ---
                        # Filtered references, when required, already come directly
                        # from input_file above. Never derive filtered_ref from orig_sample.
                        if encoded_dims[i] is None:
                            enc_w, enc_h = get_video_dimensions(enc_sample)
                            encoded_dims[i] = (enc_w, enc_h)

                        enc_w, enc_h = encoded_dims[i]
                        ref_vf = self.get_exact_ref_filter(enc_w, enc_h)
                        actual_ref_for_metrics = filtered_refs[i] if has_ffvship else orig_samples[i]
                        ref_filter_str = "" if has_ffvship else f"{ref_vf},"

                        qual_score = None
                        # xPSNR must use FFmpeg's xpsnr filter here, not FFVship.
                        # FFVship's aggregate does not expose FFMetrics' final
                        # ((4*Y)+U+V)/6 score, so using it causes the pre-score
                        # to disagree with FFMetrics (e.g. ~32 instead of 34.127).
                        if has_ffvship and target_metric != "xPSNR":
                            try:
                                qual_score, frame_scores, _ffvship_output = self.run_ffvship_metric(
                                    actual_ref_for_metrics, enc_sample, target_metric, work_dir
                                )
                                self.stats_lbl.config(text=f"Auto-CRF Eval Chunk {s_idx}/{s_tot}... {len(frame_scores)} frames")
                                if qual_score is None:
                                    filter_error = True
                            except Exception:
                                if self.cancel_requested: raise
                                filter_error = True
                        else:
                            if target_metric == "xPSNR":
                                filter_str = f"[0:v]settb=AVTB,setpts=PTS-STARTPTS,format={ref_pix_fmt}[main];[1:v]{ref_filter_str}settb=AVTB,setpts=PTS-STARTPTS[ref];[ref][main]xpsnr=eof_action=endall:stats_file='-'"
                                qual_cmd = [
                                    FFMPEG_EXE, "-hide_banner", "-nostdin", "-probesize", "50M",
                                    "-r", str(fps), "-i", enc_sample,
                                    "-r", str(fps), "-i", actual_ref_for_metrics,
                                    "-lavfi", filter_str, "-f", "null", "-"
                                ]
                            else:
                                log_filename = f"autocrf_{target_metric.lower()}_{i}.json"
                                log_filepath = os.path.join(work_dir, log_filename)
                                if os.path.exists(log_filepath): os.remove(log_filepath)
                                
                                vmaf_scale = ""
                                if enc_w and enc_h:
                                    ref_w, ref_h = get_video_dimensions(actual_ref_for_metrics)
                                    if ref_w and ref_h and (enc_w != ref_w or enc_h != ref_h):
                                        vmaf_scale = f",scale={ref_w}:{ref_h}:flags=bicubic"

                                filter_str = (
                                    f"[0:v]settb=AVTB,setpts=PTS-STARTPTS,format={ref_pix_fmt}{vmaf_scale}[main];"
                                    f"[1:v]{ref_filter_str}settb=AVTB,setpts=PTS-STARTPTS{vmaf_scale}[ref];"
                                    f"[main][ref]libvmaf=eof_action=endall:log_fmt=json:"
                                    f"log_path='{log_filename}':n_threads={get_metric_thread_count()}:"
                                    f"pool=Mean:model=version=vmaf_v0.6.1"
                                )
                                qual_cmd = [
                                    FFMPEG_EXE, "-hide_banner", "-nostdin", "-probesize", "50M",
                                    "-r", str(fps), "-i", enc_sample,
                                    "-r", str(fps), "-i", actual_ref_for_metrics,
                                    "-lavfi", filter_str, "-f", "null", "-"
                                ]

                            self.current_process = subprocess.Popen(qual_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, universal_newlines=True, cwd=work_dir, creationflags=C_FLAGS)
                            self.apply_process_settings()
                            
                            while True:
                                line = self.current_process.stderr.readline()
                                if not line and self.current_process.poll() is not None: break
                                if not line: continue
                                if target_metric == "xPSNR":
                                    if "No such filter" in line: filter_error = True
                                match_time = time_pattern.search(line)
                                if match_time:
                                    cur_sec = parse_time(match_time.group(1))
                                    prog = min((cur_sec / actual_sample_duration) * 100, 100.0)
                                    self.progress_var.set(prog)
                                    self.stats_lbl.config(text=f"Auto-CRF Eval Chunk {s_idx}/{s_tot}... {prog:.1f}%")
                                    
                            self.current_process.wait()
                            
                            if target_metric == "xPSNR" and not filter_error:
                                if os.path.exists(log_filepath):
                                    try:
                                        with open(log_filepath, 'r', encoding='utf-8') as f:
                                            for line in f:
                                                match = re.search(r"XPSNR\s+average.*?y:\s*([\d\.]+).*?u:\s*([\d\.]+).*?v:\s*([\d\.]+)", line, re.IGNORECASE)
                                                if match:
                                                    y_val = float(match.group(1))
                                                    u_val = float(match.group(2))
                                                    v_val = float(match.group(3))
                                                    qual_score = ((4.0 * y_val) + u_val + v_val) / 6.0
                                                    self.log(
                                                        f" │    └─ xPSNR components: Y={y_val:.4f}, U={u_val:.4f}, V={v_val:.4f} -> "
                                                        f"((4×Y)+U+V)/6 = {qual_score:.4f}"
                                                    )
                                    except Exception:
                                        filter_error = True
                                    finally:
                                        try: os.remove(log_filepath)
                                        except OSError: pass

                            if target_metric == "VMAF" and not filter_error:
                                try:
                                    with open(log_filepath, 'r') as f:
                                        vmaf_data = json.load(f)
                                        qual_score = parse_vmaf_json_score(vmaf_data)
                                except: filter_error = True
                                finally:
                                    if os.path.exists(log_filepath):
                                        try: os.remove(log_filepath)
                                        except: pass

                        if qual_score is not None:
                            all_scores.append(qual_score)
                            sample_pct = (enc_size / expected_sizes[i]) * 100 if expected_sizes[i] > 0 else 0
                            tree_char = "└─" if s_idx == s_tot else "├─"
                            self.log(f" │    {tree_char} Sample {s_idx}/{s_tot}: {target_metric} {qual_score:.2f}, Size {sample_pct:.1f}%")
                            
                            preset = self.preset_var.get()
                            tune = self.tune_var.get()
                            if "(" in tune: tune = tune.split("(")[1].replace(")", "").strip()
                            ext_name = "hevc" if "x265" in self.encoder_var.get() else "av1"
                            crf_txt = f"{next_crf}"
                            
                            enc_name_final = f"sample{i:02d}+{target_metric.lower()}.{qual_score:.2f}.{ext_name}.crf{crf_txt}_{preset}_{tune}.mkv"
                            if os.path.exists(enc_sample):
                                target_path = os.path.join(work_dir, enc_name_final)
                                try:
                                    os.replace(enc_sample, target_path)
                                except Exception:
                                    pass
                                enc_sample = target_path
                        else:
                            tree_char = "└─" if s_idx == s_tot else "├─"
                            self.log(f" │    {tree_char} Sample {s_idx}/{s_tot}: Failed")
                    
                    if self.cancel_requested: break
                    
                    if not all_scores or filter_error:
                        res = (None, 0, 0, 0)
                    else:
                        avg_qual_score = sum(all_scores) / len(all_scores)
                        enc_pct = (total_enc_size / total_expected_size) * 100 if total_expected_size > 0 else 0
                        res = (avg_qual_score, total_enc_size, total_enc_time, enc_pct)

                        est_size_to_save = (total_enc_size / total_actual_sample_duration) * target_duration if total_actual_sample_duration > 0 else 0
                        est_time_to_save = (total_enc_time / total_actual_sample_duration) * target_duration if total_actual_sample_duration > 0 else 0
                        try:
                            save_cache(input_file, shared_settings, {
                                "scores": {target_metric: avg_qual_score},
                                "est_size": est_size_to_save,
                                "est_time": est_time_to_save
                            }, "crf_eval")
                        except: pass

                    if res[0] is None:
                        self.log(f" │    └─ CRF {next_crf:g} Failed: Metric calculation error.", tag="error")
                        break
                        
                    avg_qual_score, enc_size, enc_duration, enc_pct = res
                    self.log(f" │    └─ Avg {target_metric}: {avg_qual_score:.2f} (Est. Size: {enc_pct:.1f}%)")
                
                size_ok = (max_pct <= 0 or res[3] <= max_pct)
                qual_ok = (res[0] <= target_score) if is_lower_better else (res[0] >= target_score)
                
                current_sample = {
                    "crf": next_crf,
                    "q": q,
                    "score": res[0],
                    "pct": res[3],
                    "res": res
                }
                history.append(current_sample)
                last_tested_crf = next_crf
                last_tested_res = res
                
                # Status string for console
                if not size_ok and not qual_ok:
                    status_text = f"Rejected: Size ({res[3]:.1f}%) & Quality ({res[0]:.2f})"
                    log_col = "error"
                elif not size_ok:
                    status_text = f"Rejected: Size ({res[3]:.1f}%)"
                    log_col = "warning"
                elif not qual_ok:
                    status_text = f"Rejected: {target_metric} ({res[0]:.2f})"
                    log_col = "warning"
                else:
                    status_text = "Target Met!"
                    log_col = "q_vlossless"

                # ab-av1 tolerance: Increases over time to accept near-perfect results
                higher_tolerance = max(0.1, crf_inc * (2 ** (run_count - 1)) * 0.1)
                diff_to_target = (target_score - res[0]) if is_lower_better else (res[0] - target_score)
                
                action_text = ""
                is_done = False
                
                if qual_ok:
                    if size_ok and diff_to_target < higher_tolerance:
                        action_text = "Optimal CRF Accepted."
                        best_crf = next_crf
                        best_res = res
                        is_done = True
                    else:
                        upper_candidates = [x for x in history if x["q"] > q]
                        if upper_candidates:
                            upper = min(upper_candidates, key=lambda x: x["q"])
                            if upper["q"] == q + 1:
                                if not size_ok:
                                    action_text = "Failed constraints. Unable to increase CRF."
                                    log_col = "error"
                                else:
                                    action_text = "Best achievable within constraints. Accepted."
                                    best_crf = next_crf
                                    best_res = res
                                is_done = True
                            else:
                                q = vmaf_lerp_q(target_score, upper, current_sample)
                                action_text = f"Interpolating next CRF: {q * crf_inc:g}"
                                log_col = "q_high" 
                        else:
                            if q == max_q:
                                if not size_ok:
                                    action_text = "Max CRF reached but size is too large."
                                    log_col = "error"
                                else:
                                    action_text = "Max CRF reached. Accepted."
                                    best_crf = next_crf
                                    best_res = res
                                is_done = True
                            elif cut_on_iter2 and run_count == 1 and q + 1 < max_q:
                                q = int(round(q * 0.4 + max_q * 0.6))
                                action_text = f"Jumping to {q * crf_inc:g}"
                                log_col = "q_high"
                            else:
                                q = max_q
                                action_text = f"Testing Max CRF: {q * crf_inc:g}"
                                log_col = "q_high"
                else: # qual_ok is False
                    if (not size_ok) or (q == min_q):
                        action_text = "Impossible constraints or Min CRF reached."
                        log_col = "error"
                        is_done = True
                    else:
                        lower_candidates = [x for x in history if x["q"] < q]
                        if lower_candidates:
                            lower = max(lower_candidates, key=lambda x: x["q"])
                            if lower["q"] + 1 == q:
                                lower_size_ok = (max_pct <= 0 or lower["pct"] <= max_pct)
                                if not lower_size_ok:
                                    action_text = "No valid CRF left."
                                    log_col = "error"
                                else:
                                    action_text = f"Returning to last good CRF {lower['crf']:g}."
                                    log_col = "q_super"
                                    best_crf = lower["crf"]
                                    best_res = lower["res"]
                                is_done = True
                            else:
                                q = vmaf_lerp_q(target_score, current_sample, lower)
                                action_text = f"Interpolating next CRF: {q * crf_inc:g}"
                        else:
                            if cut_on_iter2 and run_count == 1 and q > min_q + 1:
                                q = int(round(q * 0.4 + min_q * 0.6))
                                action_text = f"Jumping to {q * crf_inc:g}"
                            else:
                                q = min_q
                                action_text = f"Testing Min CRF: {q * crf_inc:g}"

                # Print the fully structured log
                current_sample["log_msg"] = f" │         └─ CRF {next_crf:g} [{status_text}] -> {action_text}"
                current_sample["log_tag"] = log_col
                self.log(current_sample["log_msg"], tag=current_sample["log_tag"])
                
                if is_done:
                    break
                
                run_count += 1
                            
        except Exception as e:
            if not self.cancel_requested: self.log(f" -> Auto-CRF Error: {e}", tag="error")
        finally:
            if not getattr(self, "keep_samples_var", tk.BooleanVar(value=False)).get():
                try: shutil.rmtree(work_dir)
                except: pass
            
        if self.cancel_requested: return None, {}, 0.0, 0.0, False
        
        if best_crf is None:
            self.log(f"\n -> Auto-CRF Search Complete. No valid CRF found within constraints.", tag="error")
            if last_tested_crf is not None and last_tested_res:
                self.log(f" -> Returning last tested CRF for fallback: {last_tested_crf:g}", tag="warning")
                final_score, final_size, final_time, final_pct = last_tested_res
                est_total_size = (final_size / total_actual_sample_duration) * target_duration if total_actual_sample_duration > 0 else 0
                est_total_time = (final_time / total_actual_sample_duration) * target_duration if total_actual_sample_duration > 0 else 0
                
                try:
                    save_cache(input_file, cache_settings, {
                        "success": False,
                        "best_crf": last_tested_crf,
                        "scores": {target_metric: final_score},
                        "est_size": est_total_size,
                        "est_time": est_total_time,
                        "history": history
                    }, "autocrf")
                except: pass
                
                return last_tested_crf, {target_metric: final_score}, est_total_size, est_total_time, False
            
            try:
                save_cache(input_file, cache_settings, {
                    "success": False,
                    "best_crf": None,
                    "history": history
                }, "autocrf")
            except: pass
                
            return None, {}, 0.0, 0.0, False
            
        self.log(f"\n -> Auto-CRF Search Complete. Selected Optimal CRF: {best_crf:g}", tag="q_super")
        final_score, final_size, final_time, final_pct = best_res
        
        est_total_size = (final_size / total_actual_sample_duration) * target_duration if total_actual_sample_duration > 0 else 0
        est_total_time = (final_time / total_actual_sample_duration) * target_duration if total_actual_sample_duration > 0 else 0
        
        try:
            save_cache(input_file, cache_settings, {
                "success": True,
                "best_crf": best_crf,
                "scores": {target_metric: final_score},
                "est_size": est_total_size,
                "est_time": est_total_time,
                "history": history
            }, "autocrf")
        except Exception:
            pass

        return best_crf, {target_metric: final_score}, est_total_size, est_total_time, True

    def run_ffvship_metric(self, source, encoded, metric, work_dir, start=None, end=None, encoded_offset=None, status_prefix="Calculating"):
        """Run FFVship once WITHOUT --live-score-output.

        FFVship's live mode exposes per-frame values but suppresses the final
        aggregate report.  That is fine for SSIMULACRA2/Butteraugli only if
        we deliberately average the frames, but it is WRONG for CVVDP because
        CVVDP is temporal and its final video score is the LAST cumulative score.

        We therefore use the normal FFVship output for the authoritative final
        score and --json for per-frame values used only for Worst/Mid/Best frame
        selection.
        """
        json_path = os.path.join(work_dir, f".ffvship_{metric.lower()}_{os.getpid()}_{int(time.time()*1000000)}.json")
        cmd = [FFVSHIP_EXE, "-s", source, "-e", encoded, "-m", metric, "--json", json_path]
        if start is not None:
            cmd += ["--start", str(start)]
        if end is not None:
            cmd += ["--end", str(end)]
        if encoded_offset is not None:
            cmd += ["--encoded-offset", str(encoded_offset)]

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, universal_newlines=True, cwd=work_dir,
                                creationflags=C_FLAGS)
        self.current_process = proc
        self.apply_process_settings()
        output_lines = []

        try:
            while True:
                line = proc.stdout.readline()
                if not line and proc.poll() is not None:
                    break
                if not line:
                    continue
                output_lines.append(line.rstrip())
                if self.cancel_requested:
                    try: proc.terminate()
                    except: pass
                    raise Exception("Cancelled")

            proc.wait()
            if self.cancel_requested:
                raise Exception("Cancelled")

            output = "\n".join(output_lines)
            frame_scores = []

            # Read FFVship JSON for per-frame values.  The exact JSON shape has
            # changed between Vship releases, so accept the known array forms.
            if os.path.exists(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as jf:
                        data = json.load(jf)

                    values = None
                    if isinstance(data, dict):
                        for key in ("scores", "frames", "data", "results"):
                            if key in data:
                                values = data[key]
                                break
                    else:
                        values = data

                    if isinstance(values, list):
                        for idx, item in enumerate(values):
                            val = None
                            if isinstance(item, (int, float)):
                                val = float(item)
                            elif isinstance(item, list) and item:
                                # SSIMULACRA2: [score]
                                # Butteraugli: [2Norm, 3Norm, INFNorm]
                                # CVVDP builds may use [score] as well.
                                try: val = float(item[0])
                                except: val = None
                            elif isinstance(item, dict):
                                for key in ("score", "value", "cvvdp", "ssimulacra2", "butteraugli"):
                                    if key in item:
                                        try:
                                            val = float(item[key])
                                            break
                                        except: pass
                            if val is not None:
                                frame_scores.append((idx, val))
                except Exception as e:
                    self.log(f" ├─ FFVship JSON parse warning ({metric}): {e}", tag="warning")

            final_score = None
            metric_upper = metric.upper()

            if metric_upper == "CVVDP":
                # CVVDP is temporal: FFVship/Vship explicitly defines the
                # video score as the LAST cumulative score, not the mean.
                m = re.search(r"Video Score:\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)", output, re.I)
                if m:
                    final_score = float(m.group(1))
                elif frame_scores:
                    final_score = frame_scores[-1][1]
            elif metric_upper == "SSIMULACRA2":
                m = re.search(r"Average\s*:\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)", output, re.I)
                if m:
                    final_score = float(m.group(1))
                elif frame_scores:
                    final_score = sum(v for _, v in frame_scores) / len(frame_scores)
            elif metric_upper == "BUTTERAUGLI":
                # The default qnorm is 2, so use the Average immediately after
                # the 2-Norm section.  Do NOT use 3-Norm or INF-Norm averages.
                in_2norm = False
                for line in output.splitlines():
                    if re.search(r"2-Norm", line, re.I):
                        in_2norm = True
                        continue
                    if in_2norm:
                        m = re.search(r"Average\s*:\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)", line, re.I)
                        if m:
                            final_score = float(m.group(1))
                            break
                if final_score is None and frame_scores:
                    final_score = sum(v for _, v in frame_scores) / len(frame_scores)

            if proc.returncode != 0 and final_score is None:
                raise RuntimeError(f"FFVship exited with code {proc.returncode}")

            return final_score, frame_scores, output
        finally:
            if os.path.exists(json_path):
                try: os.remove(json_path)
                except: pass

    def run_pre_quality_test(self, input_file, target_duration, file_dir, name_we, seek_offset=0.0, crf_override=None):
        self.current_paused_duration = 0.0
        test_start_time = time.time()
        
        selected_metrics = [m for m, v in self.metric_vars.items() if v.get()]
        if not selected_metrics:
            self.log(" -> No metrics selected. Skipping Pre-Encode test.", tag="warning")
            return {}, 0.0, 0.0

        crf_to_use = crf_override if crf_override is not None else float(self.crf_var.get())
        shared_settings = self.get_shared_cache_settings(crf_to_use, seek_offset, target_duration)
        
        missing_metrics = selected_metrics.copy()
        cached_scores = {}
        est_total_size = 0
        est_total_time = 0

        if self.use_cache_var.get():
            try:
                shared_cached = check_cache(input_file, shared_settings, "crf_eval")
                if shared_cached:
                    est_total_size = shared_cached.get("est_size", 0)
                    est_total_time = shared_cached.get("est_time", 0)
                    for m in selected_metrics:
                        if m in shared_cached.get("scores", {}):
                            cached_scores[m] = shared_cached["scores"][m]
                            if m in missing_metrics:
                                missing_metrics.remove(m)
                            
                    if not missing_metrics:
                        self.log(f"\n[!] Estimation Cache Hit! Restoring results...", tag="q_super")
                        self.log(f"\n--- Pre-Encode Estimation (CACHED) ---", tag="header")
                        for m in selected_metrics:
                            lbl = get_quality_label(m, cached_scores[m])
                            color_tag = get_quality_color_tag(lbl)
                            self.log(f" ├─ Predicted {m} Score: {cached_scores[m]:.2f} [{lbl}]", tag=color_tag)
                        self.log(f" ├─ Predicted Output Size   : {est_total_size / (1024*1024):.2f} MB")
                        self.log(f" └─ Predicted Encoding Time : {format_duration(est_total_time)}")
                        return cached_scores, est_total_size, est_total_time
                    elif cached_scores:
                        self.log(f"\n[!] Partial Cache Hit. Cached: {', '.join(cached_scores.keys())}. Need to calculate: {', '.join(missing_metrics)}", tag="info")
            except Exception as e:
                pass

        sample_duration, sample_points = self.get_sampling_settings(target_duration)

        self.log(f"\n--- Starting Pre-Encode Estimation & Quality Test ---", tag="header")
        self.log(f" -> Testing Metrics: {', '.join(missing_metrics)}")
        self.log(f" -> Will test {len(sample_points)} chunk(s) across the video.")
        
        depth_str = self.bit_depth_var.get()
        if "8-bit" in depth_str: pix_fmt = "yuv420p"
        elif "12-bit" in depth_str: pix_fmt = "yuv420p12le"
        else: pix_fmt = "yuv420p10le"
            
        ref_pix_fmt = get_video_pix_fmt(input_file) or "yuv420p"
        allowed = {
            "yuv420p", "yuv422p", "yuv444p",
            "yuv420p10le", "yuv422p10le", "yuv444p10le",
            "yuv420p12le", "yuv422p12le", "yuv444p12le"
        }
        if ref_pix_fmt not in allowed:
            ref_pix_fmt = "yuv420p"

        scores = {m: [] for m in missing_metrics}
        total_sample_size = 0
        total_sample_encode_time = 0
        total_actual_sample_duration = 0
        
        metric_str = "multi" if len(missing_metrics) > 1 else missing_metrics[0].lower()
        base_folder_name = ".QualiSVT"
        sub_folder = f"{name_we}.pre.{metric_str}"
        
        if self.samples_loc_var.get() == "System Temp":
            work_dir = os.path.join(tempfile.gettempdir(), base_folder_name, sub_folder)
        else:
            work_dir = os.path.join(file_dir, base_folder_name, sub_folder)
        
        os.makedirs(work_dir, exist_ok=True)
        time_pattern = re.compile(r"time=(\d{2}:\d{2}:\d{2}\.\d+)")
        
        preset = self.preset_var.get()
        tune = self.tune_var.get()
        if "(" in tune: tune = tune.split("(")[1].replace(")", "").strip()
        ext_name = "hevc" if "x265" in self.encoder_var.get() else "av1"
        
        vid_fps = get_fps(input_file)
        if vid_fps <= 0: vid_fps = 24.0
        
        has_ffvship = any(m in missing_metrics for m in ["SSIMULACRA2", "Butteraugli", "CVVDP"])
        sample_vfs = self.get_sample_filters()
        use_ffv1_ref = bool(sample_vfs and has_ffvship)
        orig_samples = []

        # Extract exactly one source sample per segment.
        # With filters + FFVship, create filtered_ref directly from input_file.
        # With no filters, create the old-style stream-copy orig_sample only.
        for i, point in enumerate(sample_points):
            orig_sample = os.path.join(work_dir, f"sample{i:02d}_orig.mkv")
            filtered_ref = os.path.join(work_dir, f"sample{i:02d}_filtered_ref.mkv")
            actual_sample_duration = min(sample_duration, target_duration - point)
            if actual_sample_duration <= 0: actual_sample_duration = 1
            seek_point_in_orig = seek_offset + point
            extract_frames = int(actual_sample_duration * vid_fps)

            s_idx = i + 1
            s_tot = len(sample_points)
            if use_ffv1_ref:
                self.update_status(f"Status: Pre-Test - Extracting Filtered Ref {s_idx}/{s_tot}")
                ext_cmd = [
                    FFMPEG_EXE, "-y", "-ss", str(seek_point_in_orig), "-i", input_file,
                    "-frames:v", str(extract_frames), "-vf", sample_vfs,
                    "-c:v", "ffv1", "-level", "3", "-pix_fmt", pix_fmt,
                    "-an", "-sn", filtered_ref
                ]
                self.current_process = subprocess.Popen(
                    ext_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=C_FLAGS
                )
                self.current_process.wait()
                if self.cancel_requested: return {}, 0.0, 0.0
                orig_samples.append(filtered_ref)
            else:
                self.update_status(f"Status: Pre-Test - Fast Extracting Sample {s_idx}/{s_tot}")
                ext_cmd = [
                    FFMPEG_EXE, "-y", "-ss", str(seek_point_in_orig), "-i", input_file,
                    "-frames:v", str(extract_frames), "-c:v", "copy",
                    "-an", "-sn", orig_sample
                ]
                self.current_process = subprocess.Popen(
                    ext_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=C_FLAGS
                )
                self.current_process.wait()
                if self.cancel_requested: return {}, 0.0, 0.0
                orig_samples.append(orig_sample)

        # Encode and Evaluate
        for i, point in enumerate(sample_points):
            s_idx = i + 1
            s_tot = len(sample_points)
            if self.cancel_requested: return {}, 0.0, 0.0
            
            enc_sample = os.path.join(work_dir, f"sample{i:02d}_enc.mkv")
            actual_sample_duration = min(sample_duration, target_duration - point)
            if actual_sample_duration <= 0: actual_sample_duration = 1
            seek_point_in_orig = seek_offset + point
            source_to_encode = orig_samples[i]
            if use_ffv1_ref:
                input_vid_dur = max(1.0, get_duration(input_file))
                orig_size = (os.path.getsize(input_file) / input_vid_dur) * actual_sample_duration
            else:
                orig_size = os.path.getsize(source_to_encode) if os.path.exists(source_to_encode) else 1
            
            try:
                self.update_status(f"Status: Pre-Test - Encoding Sample {s_idx}/{s_tot}")
                
                if self.engine_var.get() == "HandBrakeCLI":
                    enc_cmd = self.get_handbrake_cmd(
                        source_to_encode, enc_sample, 
                        encode_duration=0, seek_start=0.0, 
                        apply_trim=False, with_audio=False, crf_override=crf_override
                    )
                else:
                    enc_cmd = self.get_ffmpeg_cmd(
                        source_to_encode, enc_sample, 
                        actual_sample_duration, is_sample=True, 
                        with_audio=False, skip_filters=use_ffv1_ref, crf_override=crf_override
                    )

                start_enc_time = time.time()
                
                pipe_out = subprocess.PIPE if self.engine_var.get() == "HandBrakeCLI" else subprocess.DEVNULL
                pipe_err = subprocess.STDOUT if self.engine_var.get() == "HandBrakeCLI" else subprocess.PIPE
                
                self.current_process = subprocess.Popen(
                    enc_cmd, stdout=pipe_out, stderr=pipe_err, 
                    text=True, universal_newlines=True, creationflags=C_FLAGS
                )
                self.apply_process_settings()
                
                while True:
                    if self.engine_var.get() == "HandBrakeCLI":
                        line = self.current_process.stdout.readline()
                    else:
                        line = self.current_process.stderr.readline()
                        
                    if not line and self.current_process.poll() is not None: break
                    if not line: continue
                    
                    if self.engine_var.get() == "HandBrakeCLI":
                        match_hb = re.search(r"(\d+(?:\.\d+)?)\s*%", line)
                        if match_hb:
                            prog = float(match_hb.group(1))
                            self.progress_var.set(prog)
                            self.stats_lbl.config(text=f"Encoding Sample {s_idx}... {prog:.1f}%")
                    else:
                        match_time = time_pattern.search(line)
                        if match_time:
                            cur_sec = parse_time(match_time.group(1))
                            prog = min((cur_sec / actual_sample_duration) * 100, 100.0)
                            self.progress_var.set(prog)
                            self.stats_lbl.config(text=f"Encoding Sample {s_idx}... {prog:.1f}%")
                
                self.current_process.wait()
                if self.cancel_requested: raise Exception("Cancelled")
                
                enc_duration = self.get_adjusted_elapsed_time(start_enc_time) 
                total_sample_encode_time += enc_duration
                total_actual_sample_duration += actual_sample_duration
                
                enc_size = os.path.getsize(enc_sample) if os.path.exists(enc_sample) else 0
                total_sample_size += enc_size
                size_ratio = (enc_size / orig_size) * 100 if orig_size > 0 else 0

                # --- Metric Reference ---
                # Never create a second FFV1 file from orig_sample.
                enc_w, enc_h = get_video_dimensions(enc_sample)
                ref_vf = self.get_exact_ref_filter(enc_w, enc_h)
                filtered_ref = os.path.join(work_dir, f"sample{i:02d}_filtered_ref.mkv")

                if has_ffvship:
                    actual_ref_for_metrics = filtered_ref if use_ffv1_ref else source_to_encode
                else:
                    actual_ref_for_metrics = source_to_encode
                
                qual_scores = {}
                
                for m_name in missing_metrics:
                    if self.cancel_requested: raise Exception("Cancelled")
                    self.update_status(f"Status: Pre-Test - Calc {m_name} {s_idx}/{s_tot}")
                    
                    qual_score = None
                    filter_error = False

                    if m_name in ["SSIMULACRA2", "Butteraugli", "CVVDP"]:
                        try:
                            qual_score, frame_scores, _ffvship_output = self.run_ffvship_metric(
                                actual_ref_for_metrics, enc_sample, m_name, work_dir
                            )
                            self.stats_lbl.config(text=f"Calculating {m_name} {s_idx}... {len(frame_scores)} frames")
                            if qual_score is None:
                                filter_error = True
                        except Exception:
                            if self.cancel_requested: raise
                            filter_error = True

                    else:
                        log_filename = f"sample_{i:02d}_{m_name.lower()}.log"
                        log_filepath = os.path.join(work_dir, log_filename)
                        if os.path.exists(log_filepath):
                            try: os.remove(log_filepath)
                            except OSError: pass

                        if m_name == "xPSNR":
                            filter_str = f"[0:v]settb=AVTB,setpts=PTS-STARTPTS,format={ref_pix_fmt}[main];[1:v]settb=AVTB,setpts=PTS-STARTPTS[ref];[ref][main]xpsnr=eof_action=endall:stats_file='{log_filename}'"
                            qual_cmd = [
                                FFMPEG_EXE, "-hide_banner", "-nostdin",
                                "-i", enc_sample,
                                "-i", actual_ref_for_metrics,
                                "-lavfi", filter_str, 
                                "-f", "null", "-"
                            ]
                        else:
                            # Scale up to 1080p for accurate VMAF comparison (v0.6.1 model requirement)
                            vmaf_scale = ""
                            if enc_w and enc_h and (enc_w < 1920 or enc_h < 1080):
                                vmaf_scale = ",scale=1920:-1:flags=bicubic"
                                
                            filter_str = (
                                f"[0:v]settb=AVTB,setpts=PTS-STARTPTS,format={ref_pix_fmt}{vmaf_scale}[main];"
                                f"[1:v]settb=AVTB,setpts=PTS-STARTPTS{vmaf_scale}[ref];"
                                f"[main][ref]libvmaf=log_path='{log_filename}':log_fmt=json:shortest=true:ts_sync_mode=nearest:n_threads={get_metric_thread_count()}:pool=Mean:model=version=vmaf_v0.6.1"
                            )
                            qual_cmd = [
                                FFMPEG_EXE, "-hide_banner", "-nostdin",
                                "-i", enc_sample, 
                                "-i", actual_ref_for_metrics, 
                                "-lavfi", filter_str, 
                                "-f", "null", "-"
                            ]

                        self.current_process = subprocess.Popen(
                            qual_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, 
                            text=True, universal_newlines=True, cwd=work_dir, creationflags=C_FLAGS
                        )
                        self.apply_process_settings()
                        
                        while True:
                            line = self.current_process.stderr.readline()
                            if not line and self.current_process.poll() is not None: break
                            if not line: continue
                            
                            if m_name == "xPSNR":
                                if "No such filter" in line: filter_error = True
                                
                            match_time = time_pattern.search(line)
                            if match_time:
                                cur_sec = parse_time(match_time.group(1))
                                prog = min((cur_sec / actual_sample_duration) * 100, 100.0)
                                self.progress_var.set(prog)
                                self.stats_lbl.config(text=f"Calculating {m_name} {s_idx}... {prog:.1f}%")
                                
                        self.current_process.wait()
                        if self.cancel_requested: raise Exception("Cancelled")
                        
                        if m_name == "xPSNR" and not filter_error:
                            if os.path.exists(log_filepath):
                                try:
                                    with open(log_filepath, 'r', encoding='utf-8') as f:
                                        for line in f:
                                            match = re.search(r"XPSNR\s+average.*?y:\s*([\d\.]+).*?u:\s*([\d\.]+).*?v:\s*([\d\.]+)", line, re.IGNORECASE)
                                            if match:
                                                y_val = float(match.group(1))
                                                u_val = float(match.group(2))
                                                v_val = float(match.group(3))
                                                qual_score = ((4.0 * y_val) + u_val + v_val) / 6.0
                                except Exception:
                                    filter_error = True
                                finally:
                                    try: os.remove(log_filepath)
                                    except OSError: pass

                        if m_name == "VMAF" and not filter_error:
                            try:
                                with open(log_filepath, 'r') as f:
                                    vmaf_data = json.load(f)
                                    qual_score = float(vmaf_data['pooled_metrics']['vmaf']['mean'])
                            except Exception:
                                filter_error = True
                            finally:
                                if os.path.exists(log_filepath):
                                    try: os.remove(log_filepath)
                                    except: pass

                    if qual_score is not None:
                        qual_scores[m_name] = qual_score
                        scores[m_name].append(qual_score)
                        lbl = get_quality_label(m_name, qual_score)
                        color_tag = get_quality_color_tag(lbl)
                        self.log(f" ├─ Sample {s_idx}/{s_tot}: {m_name} = {qual_score:.2f} [{lbl}]", tag=color_tag)
                    else:
                        if filter_error: self.log(f" ├─ Sample {s_idx}/{s_tot}: {m_name} Failed (Dependencies)", tag="error")
                        else: self.log(f" ├─ Sample {s_idx}/{s_tot}: {m_name} Failed", tag="error")

                self.log(f" │    └─ Sample Output Size Ratio: {size_ratio:.0f}%")
                
                if qual_scores:
                    scores_str = "+".join([f"{m.lower()}.{s:.2f}" for m, s in qual_scores.items()])
                    crf_txt = f"{crf_override:g}" if isinstance(crf_override, (float, int)) else (str(crf_override) if crf_override is not None else str(self.crf_var.get()))
                    enc_name_final = f"sample{i:02d}+{scores_str}.{ext_name}.crf{crf_txt}_{preset}_{tune}.mkv"
                    if os.path.exists(enc_sample):
                        target_path = os.path.join(work_dir, enc_name_final)
                        try:
                            os.replace(enc_sample, target_path)
                        except Exception:
                            pass
                        enc_sample = target_path

            except Exception as e:
                if not self.cancel_requested: self.log(f" ├─ Sample {s_idx}: Error occurred -> {e}", tag="error")
            finally:
                if not getattr(self, "keep_samples_var", tk.BooleanVar(value=False)).get():
                    for f_to_rem in [orig_samples[i], enc_sample, filtered_ref]: 
                        if os.path.exists(f_to_rem):
                            try: os.remove(f_to_rem)
                            except: pass
                
        if not getattr(self, "keep_samples_var", tk.BooleanVar(value=False)).get():
            try: shutil.rmtree(work_dir)
            except: pass 
        else:
            for file in os.listdir(work_dir):
                if file.endswith(".ffindex"):
                    try: os.remove(os.path.join(work_dir, file))
                    except: pass

        if not self.cancel_requested:
            final_avg = cached_scores.copy()
            for m in missing_metrics:
                if scores[m]: final_avg[m] = sum(scores[m]) / len(scores[m])
                
            if total_actual_sample_duration > 0:
                est_total_size = (total_sample_size / total_actual_sample_duration) * target_duration
                est_total_time = (total_sample_encode_time / total_actual_sample_duration) * target_duration
            elif est_total_size == 0 and est_total_time == 0:
                est_total_size = 0.0
                est_total_time = 0.0
                
            test_duration = self.get_adjusted_elapsed_time(test_start_time)
            
            for m, avg_qual in final_avg.items():
                lbl = get_quality_label(m, avg_qual)
                color_tag = get_quality_color_tag(lbl)
                self.log(f" ├─ Predicted {m} Score: {avg_qual:.2f} [{lbl}]", tag=color_tag)
                
            self.log(f" ├─ Predicted Output Size   : {est_total_size / (1024*1024):.2f} MB")
            self.log(f" ├─ Predicted Encoding Time : {format_duration(est_total_time)}")
            self.log(f" └─ Pre-Test Duration       : {format_duration(test_duration)}")
            
            try:
                save_cache(input_file, shared_settings, {
                    "scores": final_avg,
                    "est_size": est_total_size,
                    "est_time": est_total_time
                }, "crf_eval")
            except: pass

            return final_avg, est_total_size, est_total_time
        
        return {}, 0.0, 0.0

    def run_post_quality_test(self, input_file, encoded_file, target_duration, file_dir, name_we, seek_offset=0.0):
        self.current_paused_duration = 0.0
        test_start_time = time.time()
        
        selected_metrics = [m for m, v in self.metric_vars.items() if v.get()]
        if not selected_metrics:
          self.log(" -> No metrics selected. Skipping Post-Encode verification.", tag="warning")
          return {}

        sample_duration, sample_points = self.get_sampling_settings(target_duration)

        self.log(f"\n--- Starting Post-Encode Evaluation (Worst/Mid/Best Frames) ---", tag="header")
        
        depth_str = self.bit_depth_var.get()
        if "8-bit" in depth_str: pix_fmt = "yuv420p"
        elif "12-bit" in depth_str: pix_fmt = "yuv420p12le"
        else: pix_fmt = "yuv420p10le"
            
        metric_str = "multi" if len(selected_metrics) > 1 else selected_metrics[0].lower()
            
        scores = {m: [] for m in selected_metrics}
        time_pattern = re.compile(r"time=(\d{2}:\d{2}:\d{2}\.\d+)")
        
        base_folder_name = ".QualiSVT"
        sub_folder = f"{name_we}.post.{metric_str}"
        
        if self.samples_loc_var.get() == "System Temp":
            work_dir = os.path.join(tempfile.gettempdir(), base_folder_name, sub_folder)
        else:
            work_dir = os.path.join(file_dir, base_folder_name, sub_folder)
        
        os.makedirs(work_dir, exist_ok=True)
        
        vid_fps = get_fps(input_file)
        if vid_fps <= 0: vid_fps = 24.0
        fps = vid_fps

        for i, point in enumerate(sample_points):
            s_idx = i + 1
            s_tot = len(sample_points)
            if self.cancel_requested: return {}
            
            actual_sample_duration = min(sample_duration, target_duration - point)
            if actual_sample_duration <= 0: actual_sample_duration = 1
            
            seek_point_in_orig = seek_offset + point
            start_frame = int(seek_point_in_orig * vid_fps)
            end_frame = start_frame + int(actual_sample_duration * vid_fps)
            enc_start_frame = int(point * vid_fps)
            
            try:
                # POST-ENCODE uses the complete original and encoded files directly.
                # No temporary filtered-reference MKV is created.
                enc_w, enc_h = get_video_dimensions(encoded_file)
                ref_vf = self.get_exact_ref_filter(enc_w, enc_h)

                ffvship_source = input_file
                ffvship_start = start_frame
                ffvship_end = end_frame
                ffvship_encoded_offset = enc_start_frame - start_frame

                # FFMetrics converts the encoded/main stream to the reference
                # pixel format when the two inputs have different formats.
                ref_pix_fmt = get_video_pix_fmt(input_file) or "yuv420p"
                allowed = {
                    "yuv420p", "yuv422p", "yuv444p",
                    "yuv420p10le", "yuv422p10le", "yuv444p10le",
                    "yuv420p12le", "yuv422p12le", "yuv444p12le"
                }
                if ref_pix_fmt not in allowed:
                    ref_pix_fmt = "yuv420p"

                for m_name in selected_metrics:
                    if self.cancel_requested: raise Exception("Cancelled")
                    frame_scores = []
                    sample_avg_score = None
                    
                    if m_name in ["SSIMULACRA2", "Butteraugli", "CVVDP"]:
                        self.update_status(f"Status: Post-Test - Analyzing {m_name} {s_idx}/{s_tot}")
                        try:
                            qual_score_ffvship, frame_scores, _ffvship_output = self.run_ffvship_metric(
                                ffvship_source, encoded_file, m_name, work_dir,
                                start=ffvship_start, end=ffvship_end,
                                encoded_offset=ffvship_encoded_offset,
                                status_prefix=f"Analyzing {m_name} {s_idx}"
                            )
                            self.stats_lbl.config(text=f"Analyzing {m_name} {s_idx}... {len(frame_scores)} frames")
                            if qual_score_ffvship is not None:
                                # Authoritative FFVship final score.  For CVVDP
                                # this is the final temporal Video Score, NOT a frame average.
                                sample_avg_score = qual_score_ffvship
                            else:
                                sample_avg_score = None
                        except Exception:
                            if self.cancel_requested: raise
                            sample_avg_score = None
                            frame_scores = []

                    elif m_name == "xPSNR":
                        log_filename = f"sample_{i:02d}_{m_name.lower()}.log"
                        log_filepath = os.path.join(work_dir, log_filename)
                        if os.path.exists(log_filepath):
                            try: os.remove(log_filepath)
                            except OSError: pass

                        filter_str = (
                            f"[0:v]settb=AVTB,setpts=PTS-STARTPTS,format={ref_pix_fmt}[main];"
                            f"[1:v]settb=AVTB,setpts=PTS-STARTPTS[ref];"
                            f"[ref][main]xpsnr=eof_action=endall:stats_file='{log_filename}'"
                        )
                        qual_cmd = [
                            FFMPEG_EXE, "-hide_banner", "-nostdin", "-probesize", "50M",
                            "-r", str(fps), "-ss", str(point),
                            "-t", str(actual_sample_duration), "-i", encoded_file,
                            "-r", str(fps), "-ss", str(seek_point_in_orig),
                            "-t", str(actual_sample_duration), "-i", input_file,
                            "-lavfi", filter_str, "-f", "null", "-"
                        ]
                        self.update_status(f"Status: Post-Test - Analyzing {m_name} {s_idx}/{s_tot}")
                        self.current_process = subprocess.Popen(
                            qual_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True, universal_newlines=True, cwd=work_dir, creationflags=C_FLAGS
                        )
                        self.apply_process_settings()

                        xpsnr_scores = []
                        while True:
                            line = self.current_process.stderr.readline()
                            if not line and self.current_process.poll() is not None:
                                break
                            if not line:
                                continue

                            match_time = time_pattern.search(line)
                            if match_time:
                                cur_sec = parse_time(match_time.group(1))
                                self.progress_var.set(min((cur_sec / actual_sample_duration) * 100, 100.0))

                        self.current_process.wait()
                        if self.cancel_requested:
                            raise Exception("Cancelled")

                        if os.path.exists(log_filepath):
                            try:
                                with open(log_filepath, 'r', encoding='utf-8') as f:
                                    for line in f:
                                        match = re.search(r"n:\s*\d+\s+XPSNR\s+y:\s*([\d\.]+)\s+XPSNR\s+u:\s*([\d\.]+)\s+XPSNR\s+v:\s*([\d\.]+)", line, re.IGNORECASE)
                                        if match:
                                            y_val = float(match.group(1))
                                            u_val = float(match.group(2))
                                            v_val = float(match.group(3))
                                            xpsnr_scores.append(((4.0 * y_val) + u_val + v_val) / 6.0)
                            except Exception:
                                pass
                            finally:
                                try: os.remove(log_filepath)
                                except OSError: pass

                        if xpsnr_scores:
                            sample_avg_score = sum(xpsnr_scores) / len(xpsnr_scores)
                            frame_scores = list(enumerate(xpsnr_scores))

                    elif m_name == "VMAF":
                        log_filename = f"sample_{i:02d}_{m_name.lower()}.json"
                        log_filepath = os.path.join(work_dir, log_filename)
                        if os.path.exists(log_filepath):
                            try:
                                os.remove(log_filepath)
                            except OSError:
                                pass

                        ref_w, ref_h = get_video_dimensions(input_file)
                        vmaf_scale = ""
                        if ref_w and ref_h and (enc_w != ref_w or enc_h != ref_h):
                            vmaf_scale = f",scale={ref_w}:{ref_h}:flags=bicubic"

                        # Match the FFMetrics 1080p command semantics:
                        # encoded/main -> {ref_pix_fmt}; reference stays native.
                        filter_str = (
                            f"[0:v]settb=AVTB,setpts=PTS-STARTPTS,format={ref_pix_fmt}{vmaf_scale}[main];"
                            f"[1:v]settb=AVTB,setpts=PTS-STARTPTS{vmaf_scale}[ref];"
                            f"[main][ref]libvmaf=eof_action=endall:log_fmt=json:"
                            f"log_path='{log_filename}':n_threads={get_metric_thread_count()}:"
                            f"pool=Mean:model=version=vmaf_v0.6.1"
                        )

                        qual_cmd = [
                            FFMPEG_EXE, "-hide_banner", "-nostdin", "-probesize", "50M",
                            "-r", str(fps), "-ss", str(point),
                            "-t", str(actual_sample_duration), "-i", encoded_file,
                            "-r", str(fps), "-ss", str(seek_point_in_orig),
                            "-t", str(actual_sample_duration), "-i", input_file,
                            "-lavfi", filter_str, "-f", "null", "-"
                        ]

                        self.update_status(f"Status: Post-Test - Analyzing {m_name} {s_idx}/{s_tot}")
                        self.current_process = subprocess.Popen(
                            qual_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True, universal_newlines=True, cwd=work_dir, creationflags=C_FLAGS
                        )
                        self.apply_process_settings()

                        vmaf_summary_score = None
                        vmaf_stderr = []
                        while True:
                            line = self.current_process.stderr.readline()
                            if not line and self.current_process.poll() is not None:
                                break
                            if not line:
                                continue

                            vmaf_stderr.append(line.rstrip())

                            summary_match = re.search(
                                r"(?:VMAF\s+score|VMAF\s+mean)\s*:\s*([0-9]+(?:\.[0-9]+)?)",
                                line, re.IGNORECASE
                            )
                            if summary_match:
                                vmaf_summary_score = float(summary_match.group(1))

                            match_time = time_pattern.search(line)
                            if match_time:
                                cur_sec = parse_time(match_time.group(1))
                                self.progress_var.set(min((cur_sec / actual_sample_duration) * 100, 100.0))

                        return_code = self.current_process.wait()
                        if self.cancel_requested:
                            raise Exception("Cancelled")

                        # JSON is authoritative when available; FFmpeg's pooled
                        # "VMAF score:" is the fallback. This avoids the previous
                        # false "Log Parsing Issue" when frame JSON differs.
                        frame_scores = []
                        json_error = None
                        if os.path.exists(log_filepath):
                            try:
                                with open(log_filepath, "r", encoding="utf-8") as f:
                                    data = json.load(f)

                                pooled_score = parse_vmaf_json_score(data)
                                if pooled_score is not None:
                                    vmaf_summary_score = pooled_score

                                for frame in data.get("frames", []) if isinstance(data, dict) else []:
                                    metrics = frame.get("metrics", {}) if isinstance(frame, dict) else {}
                                    val = metrics.get("vmaf") if isinstance(metrics, dict) else None
                                    if val is not None:
                                        frame_scores.append(
                                            (frame.get("frameNum", len(frame_scores)), float(val))
                                        )
                            except Exception as e:
                                json_error = str(e)
                            finally:
                                try:
                                    os.remove(log_filepath)
                                except OSError:
                                    pass

                        if vmaf_summary_score is not None:
                            sample_avg_score = vmaf_summary_score
                        elif return_code == 0 and frame_scores:
                            sample_avg_score = sum(v for _, v in frame_scores) / len(frame_scores)
                        else:
                            # Make the real FFmpeg failure visible instead of
                            # hiding it behind the generic "Log Parsing Issue".
                            if return_code != 0:
                                err_lines = [
                                    x for x in vmaf_stderr
                                    if ("Error" in x or "error" in x or "Invalid" in x or
                                        "No such" in x or "Failed" in x)
                                ]
                                detail = err_lines[-1] if err_lines else f"FFmpeg exit code {return_code}"
                                self.log(
                                    f" ├─ VMAF FFmpeg error (Sample {s_idx}): {detail}",
                                    tag="error"
                                )
                            elif json_error:
                                self.log(
                                    f" ├─ VMAF JSON parsing warning (Sample {s_idx}): {json_error}",
                                    tag="warning"
                                )

                    if frame_scores:
                        if m_name != "VMAF" or sample_avg_score is None:
                            sample_avg_score = sum(s[1] for s in frame_scores) / len(frame_scores)
                        scores[m_name].append(sample_avg_score)
                        
                        sorted_scores = sorted(frame_scores, key=lambda x: x[1])
                        
                        if len(sorted_scores) >= 20:
                            trim_count = max(1, int(len(sorted_scores) * 0.05))
                            search_pool = sorted_scores[trim_count:-trim_count]
                        else:
                            search_pool = sorted_scores
                            
                        if m_name.upper() == "BUTTERAUGLI":
                            best_f = search_pool[0]
                            worst_f = search_pool[-1]
                        else:
                            worst_f = search_pool[0]
                            best_f = search_pool[-1]
                            
                        mid_f = search_pool[len(search_pool) // 2]
                        
                        lbl = get_quality_label(m_name, sample_avg_score)
                        color_tag = get_quality_color_tag(lbl)
                        self.log(f" ├─ Sample {s_idx} ({format_duration(point)}): {m_name} = {sample_avg_score:.2f} [{lbl}]", tag=color_tag)
                        
                        self.log(f" │    └─ Worst: {worst_f[1]:.2f} | Mid: {mid_f[1]:.2f} | Best: {best_f[1]:.2f}")
                        
                        if getattr(self, "keep_samples_var", tk.BooleanVar(value=False)).get():
                            targets = [("Worst", worst_f[0], worst_f[1]), ("Mid", mid_f[0], mid_f[1]), ("Best", best_f[0], best_f[1])]
                            for label, f_idx, f_score in targets:
                                
                                if getattr(self, "merge_samples_var", tk.BooleanVar(value=False)).get():
                                    self.update_status(f"Status: Extracting {label} Frame ({m_name}) [Merged] - Sample {s_idx}")
                                    comp_img = os.path.join(work_dir, f"sample{i:02d}_{label}_{m_name.lower()}.{f_score:.2f}_Comparison.webp")
                                else:
                                    self.update_status(f"Status: Extracting {label} Frame ({m_name}) - Sample {s_idx}")
                                    orig_img = os.path.join(work_dir, f"sample{i:02d}_{label}_{m_name.lower()}.{f_score:.2f}_Orig.webp")
                                    enc_img  = os.path.join(work_dir, f"sample{i:02d}_{label}_{m_name.lower()}.{f_score:.2f}_Enc.webp")
                                
                                rel_f_idx = f_idx
                                ref_image_filter = f"{ref_vf}," if ref_vf else ""
                                vf_extract_orig = f"{ref_image_filter}select='eq(n,{rel_f_idx})'"
                                orig_source = input_file
                                seek_args = ["-ss", str(seek_point_in_orig)]

                                if getattr(self, "merge_samples_var", tk.BooleanVar(value=False)).get():
                                    fc_orig = f"{vf_extract_orig},format=yuv420p"
                                    fc_enc = f"select='eq(n,{rel_f_idx})',format=yuv420p"
                                    cmd_comp = [FFMPEG_EXE, "-y"] + seek_args + [
                                        "-i", orig_source, 
                                        "-ss", str(point), "-i", encoded_file, 
                                        "-filter_complex", f"[0:v]{fc_orig}[orig];[1:v]{fc_enc}[enc];[orig][enc]hstack", 
                                        "-vframes", "1", "-c:v", "libwebp", "-lossless", "1", comp_img
                                    ]
                                    self.current_process = subprocess.Popen(cmd_comp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=C_FLAGS)
                                    self.current_process.wait()
                                else:
                                    cmd_o = [FFMPEG_EXE, "-y"] + seek_args + ["-i", orig_source, "-vf", vf_extract_orig, "-vframes", "1", "-c:v", "libwebp", "-lossless", "1", orig_img]
                                    cmd_e = [FFMPEG_EXE, "-y", "-ss", str(point), "-i", encoded_file, "-vf", f"select='eq(n,{rel_f_idx})'", "-vframes", "1", "-c:v", "libwebp", "-lossless", "1", enc_img]
                                    
                                    self.current_process = subprocess.Popen(cmd_o, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=C_FLAGS)
                                    self.current_process.wait()
                                    self.current_process = subprocess.Popen(cmd_e, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=C_FLAGS)
                                    self.current_process.wait()
                    elif sample_avg_score is not None:
                        scores[m_name].append(sample_avg_score)
                        lbl = get_quality_label(m_name, sample_avg_score)
                        color_tag = get_quality_color_tag(lbl)
                        self.log(
                            f" ├─ Sample {s_idx}/{s_tot}: {m_name} = {sample_avg_score:.2f} [{lbl}]",
                            tag=color_tag
                        )
                        if m_name == "VMAF":
                            self.log(" │    └─ Frame-level VMAF details unavailable; pooled libvmaf score used.")
                    else:
                        self.log(f" ├─ Sample {s_idx}/{s_tot}: {m_name} = Failed (Log Parsing Issue)", tag="error")

            except Exception as e:
                if not self.cancel_requested: self.log(f" ├─ Sample {s_idx}: Error occurred -> {e}", tag="error")
                
        if not getattr(self, "keep_samples_var", tk.BooleanVar(value=False)).get():
            try: shutil.rmtree(work_dir)
            except: pass
        else:
            for file in os.listdir(work_dir):
                if file.endswith((".log", ".json", ".mkv", ".ffindex")):
                    try: os.remove(os.path.join(work_dir, file))
                    except: pass
                
        if not self.cancel_requested:
            final_avg = {}
            for m in selected_metrics:
                if scores[m]: final_avg[m] = sum(scores[m]) / len(scores[m])
                
            test_duration = self.get_adjusted_elapsed_time(test_start_time)
            
            for m, avg_qual in final_avg.items():
                lbl = get_quality_label(m, avg_qual)
                color_tag = get_quality_color_tag(lbl)
                self.log(f" ├─ Final Actual {m} Average: {avg_qual:.2f} [{lbl}]", tag=color_tag)
            
            self.log(f" └─ Post-Test Duration           : {format_duration(test_duration)}")
                
            return final_avg
        
        return {}

    def process_queue(self):
        reports = []
        total_start_time = time.time()
        engine = self.engine_var.get()
        
        for index, input_file in enumerate(self.files_to_process, start=1):
            if self.cancel_requested: break
            
            is_benchmark = self.benchmark_var.get()
            op_mode = self.op_mode_var.get()
            
            file_dir, file_name = os.path.split(input_file)
            raw_name_we, file_ext = os.path.splitext(file_name)
            name_we = shorten_filename(raw_name_we, max_len=100)
            
            out_fmt_pref = getattr(self, 'out_format_var', tk.StringVar(value="MKV")).get()
            target_ext = ".mp4" if out_fmt_pref == "MP4" else ".mkv"

            mode = self.out_mode_var.get()
            if mode == "Subfolder":
                sub_name = self.subfolder_var.get().strip() or "encoded"
                target_dir = os.path.join(file_dir, sub_name)
                os.makedirs(target_dir, exist_ok=True)
                output_file = os.path.join(target_dir, f"{name_we}_encoded{target_ext}")
            elif mode == "Browse Folder...":
                custom_dir = getattr(self, "custom_folder_var", tk.StringVar(value="")).get().strip()
                if not custom_dir: custom_dir = file_dir
                os.makedirs(custom_dir, exist_ok=True)
                output_file = os.path.join(custom_dir, f"{name_we}_encoded{target_ext}")
            else: 
                output_file = os.path.join(file_dir, f"{name_we}_encoded{target_ext}")

            timestamp = datetime.datetime.now().strftime("_%d.%m.%Y %H-%M-%S")
            start_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_loc_pref = self.log_loc_var.get()
            log_file = None
            if log_loc_pref == "App 'Logs' Folder":
                app_logs_dir = os.path.join(APP_DIR, "Logs")
                os.makedirs(app_logs_dir, exist_ok=True)
                log_file = os.path.join(app_logs_dir, f"{name_we}_encoded{timestamp}.txt")
            elif log_loc_pref == "Next to Original":
                log_file = os.path.join(file_dir, f"{name_we}_encoded{timestamp}.txt")

            self.log(f"\n==================================================", tag="header")
            if is_benchmark:
                self.log(f"[{index}/{len(self.files_to_process)}] ANALYZE ONLY: {file_name}", tag="header")
            else:
                self.log(f"[{index}/{len(self.files_to_process)}] Processing: {file_name}", tag="header")
                
            self.update_status(f"Status: Processing {file_name} ({index}/{len(self.files_to_process)})")

            total_duration = get_duration(input_file)
            if total_duration == 0.0:
                self.log(" -> Skipping: Could not read video duration.", tag="warning")
                continue

            input_size = os.path.getsize(input_file)
            
            # --- Dynamic GOP Mode Calculation ---
            self.current_gop_frames = self.calculate_gop(input_file, total_duration)
            
            self.current_crop = None
            if self.auto_crop_var.get():
                self.update_status(f"Status: Detecting crop bounds for {file_name}...")
                crop_val = self.detect_crop(input_file, total_duration)
                if crop_val:
                    self.current_crop = crop_val
                    self.log(f" -> Auto Crop detected: {crop_val}", tag="info")
                else:
                    self.log(f" -> Auto Crop enabled but no black bars detected.", tag="warning")

            r_mode = self.range_mode_var.get()
            seek_start = 0.0
            encode_duration = total_duration

            start_val = self.range_start_var.get().strip() or "0"
            end_val = self.range_end_var.get().strip() or "0"
            
            if r_mode != "Full Video":
                try:
                    if r_mode == "Time (Seconds)":
                        s = float(start_val)
                        e = float(end_val)
                        seek_start = s
                        encode_duration = e - s
                    elif r_mode == "Time (hh:mm:ss)":
                        s = parse_time(start_val)
                        e = parse_time(end_val)
                        seek_start = s
                        encode_duration = e - s
                    elif r_mode == "Frames":
                        fps = get_fps(input_file)
                        s = int(start_val)
                        e = int(end_val)
                        seek_start = s / fps
                        encode_duration = (e - s) / fps
                    elif r_mode == "Chapters":
                        s = int(start_val)
                        e = int(end_val)
                        c_start, c_end = get_chapter_times(input_file, s, e)
                        if c_start is not None:
                            seek_start = c_start
                            encode_duration = c_end - c_start
                        else:
                            self.log(" -> No chapters found. Encoding full video.", tag="warning")
                    
                    seek_start = max(0.0, min(seek_start, total_duration))
                    if encode_duration <= 0:
                        encode_duration = total_duration - seek_start
                    else:
                        encode_duration = min(encode_duration, total_duration - seek_start)
                        
                except Exception as e:
                    self.log(f" -> Range parse error ({e}). Falling back to full video.", tag="error")
                    seek_start = 0.0
                    encode_duration = total_duration

            # EARLY LOG INITIALIZATION
            orig_specs_log = self.get_original_file_specs(input_file)
            self.current_log_data = {
                "file": file_name,
                "start": start_time_str,
                "end": "Running...",
                "specs": get_system_specs(),
                "orig_specs": orig_specs_log,
                "enc": "",
                "pre": "",
                "post": "",
                "settings": ""
            }
            self.save_current_log(log_file)

            # -------------------------------------------------------------
            # PRE-FLIGHT PROCESSES (Video Settings Log + Auto-CRF / Pre-Eval)
            # -------------------------------------------------------------
            final_crf = None
            qual_scores_pre, est_size, est_time = {}, 0.0, 0.0

            bench_monitor = None
            if is_benchmark and HAS_PSUTIL:
                bench_monitor = ProcessMonitor(self)
                bench_monitor.start()

            # --- Extract Settings Before Auto-CRF Starts ---
            initial_crf_display = "Auto (Search)" if op_mode == "Enable Auto-CRF Search" else None
            settings_log_str = self.extract_and_log_encoder_info(
                input_file, file_name, out_fmt_pref, r_mode, seek_start, encode_duration, crf_override=initial_crf_display
            )
            if self.cancel_requested: break
            
            self.current_log_data["settings"] = settings_log_str
            self.save_current_log(log_file)

            if op_mode == "Enable Auto-CRF Search":
                res = self.run_auto_crf_search(
                    input_file, encode_duration, file_dir, name_we, seek_offset=seek_start
                )
                if len(res) == 5:
                    final_crf, qual_scores_pre, est_size, est_time, is_auto_crf_success = res
                else:
                    final_crf, qual_scores_pre, est_size, est_time = res
                    is_auto_crf_success = True
                    
                if self.cancel_requested: break
                
                if not is_auto_crf_success:
                    self.log(f" -> Skipping {file_name}: Auto-CRF constraints not met.", tag="error")
                    pre_log = " [ Auto-CRF Estimation Results ]\n"
                    pre_log += "   - Status                  : Failed (Constraints Unmet)\n"
                    if final_crf is not None:
                        pre_log += f"   - Selected CRF            : {final_crf:g}\n"
                    else:
                        pre_log += "   - Selected CRF            : None\n"
                    pre_log += f"   - Predicted Output Size   : {est_size / (1024*1024):.2f} MB\n"
                    pre_log += f"   - Predicted Encode Time   : {format_duration(est_time)}\n"
                    for m, s in qual_scores_pre.items():
                        lbl_pre = get_quality_label(m, s)
                        pre_log += f"   - Pred. Quality ({m}): {s:.2f} [{lbl_pre}]\n"
                    pre_log += "\n"
                    
                    if "pre" not in self.current_log_data: self.current_log_data["pre"] = ""
                    self.current_log_data["pre"] += pre_log
                    
                    self.current_log_data["end"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self.save_current_log(log_file)
                    
                    est_ratio = (est_size / input_size) * 100 if input_size > 0 else 0
                    est_reduction = 100 - est_ratio

                    reports.append({
                        "name": f"{file_name} (Auto-CRF Failed)", "in_size": input_size, "out_size": est_size,
                        "duration": est_time, "fps": 0,
                        "ratio": est_ratio, "reduction": est_reduction, 
                        "qual_pre": qual_scores_pre, "qual_post": {}
                    })
                    
                    self.write_debug_log(name_we, timestamp)
                    continue
                else:
                    pre_log = " [ Auto-CRF Estimation Results ]\n"
                    pre_log += "   - Status                  : Success\n"
                    pre_log += f"   - Selected CRF            : {final_crf:g}\n"
                    pre_log += f"   - Predicted Output Size   : {est_size / (1024*1024):.2f} MB\n"
                    pre_log += f"   - Predicted Encode Time   : {format_duration(est_time)}\n"
                    for m, s in qual_scores_pre.items():
                        lbl_pre = get_quality_label(m, s)
                        pre_log += f"   - Pred. Quality ({m}): {s:.2f} [{lbl_pre}]\n"
                    pre_log += "\n"
                    
                    if "pre" not in self.current_log_data: self.current_log_data["pre"] = ""
                    self.current_log_data["pre"] += pre_log
                    self.save_current_log(log_file)

            elif op_mode == "Enable Quality Estimation":
                eval_sub = self.eval_submode_var.get()
                if eval_sub in ["Pre-Encode Estimate", "Both (Pre & Post)"] or is_benchmark:
                    qual_scores_pre, est_size, est_time = self.run_pre_quality_test(
                        input_file, encode_duration, file_dir, name_we, seek_offset=seek_start, crf_override=final_crf
                    )
                    if self.cancel_requested: break
                    
                    if qual_scores_pre:
                        pre_log = " [ Estimation Results (Pre-Test) ]\n"
                        pre_log += f"   - Predicted Output Size   : {est_size / (1024*1024):.2f} MB\n"
                        pre_log += f"   - Predicted Encode Time   : {format_duration(est_time)}\n"
                        for m, s in qual_scores_pre.items():
                            lbl_pre = get_quality_label(m, s)
                            pre_log += f"   - Pred. Quality ({m}): {s:.2f} [{lbl_pre}]\n"
                        pre_log += "\n"
                        if "pre" not in self.current_log_data: self.current_log_data["pre"] = ""
                        self.current_log_data["pre"] += pre_log
                        self.save_current_log(log_file)

            # -------------------------------------------------------------
            # ANALYZE ONLY COMPLETION
            # -------------------------------------------------------------
            if is_benchmark:
                if bench_monitor: bench_monitor.stop()
                    
                self.log(f"\n -> Analyze Only Finished for {file_name}", tag="success")
                est_ratio = (est_size / input_size) * 100 if input_size > 0 else 0
                est_reduction = 100 - est_ratio
                
                if bench_monitor:
                    rep_actual = bench_monitor.get_report() 
                    rep_proj = bench_monitor.get_report(est_time / 3600.0) 
                    
                    self.log(f"   - Peak RAM Usage : {rep_actual['peak_ram']:.0f} MB", tag="info")
                    self.log(f"   - Avg CPU Usage  : {rep_actual['avg_cpu']:.1f} %", tag="info")
                    
                    bench_res = f" [ Projected Resource Usage (Full Encode) ]\n"
                    bench_res += f"   - Peak RAM Usage : {rep_actual['peak_ram']:.0f} MB\n"
                    bench_res += f"   - Avg CPU Usage  : {rep_actual['avg_cpu']:.1f} %\n"
                    
                    if rep_actual['avg_power'] > 0:
                        self.log(f"   - Avg CPU Power  : {rep_actual['avg_power']:.1f} W", tag="info")
                        self.log(f"   - Est. Total Engy: {rep_proj['total_wh']:.3f} Wh", tag="info")
                        bench_res += f"   - Avg CPU Power  : {rep_actual['avg_power']:.1f} W\n"
                        bench_res += f"   - Est. Total Engy: {rep_proj['total_wh']:.3f} Wh\n"
                    else:
                        self.log("   - Avg CPU Power  : Not Detected (Is OHM running?)", tag="warning")
                        bench_res += f"   - Avg CPU Power  : Not Detected (OHM not running)\n"
                        
                    bench_res += "\n"
                    if "pre" not in self.current_log_data: self.current_log_data["pre"] = ""
                    self.current_log_data["pre"] += bench_res
                
                reports.append({
                    "name": f"{file_name} (Analyze Only)", "in_size": input_size, "out_size": est_size,
                    "duration": est_time, "fps": 0, 
                    "ratio": est_ratio, "reduction": est_reduction, 
                    "qual_pre": qual_scores_pre, "qual_post": {}
                })
                
                self.current_log_data["end"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.save_current_log(log_file)
                
                self.write_debug_log(name_we, timestamp)
                continue 

            # -------------------------------------------------------------
            # FULL ENCODING PROCESS & BULLETPROOF AUTO-RESUME
            # -------------------------------------------------------------
            apply_trim = (r_mode != "Full Video" and encode_duration < total_duration)

            is_resuming = False
            skip_encode = False
            part1_file = output_file + ".part1" + target_ext
            part2_file = output_file + ".part2" + target_ext
            final_target_output = output_file
            
            cmd_seek_start = seek_start
            cmd_encode_duration = encode_duration
            
            if getattr(self, "auto_resume_var", tk.BooleanVar(value=False)).get():
                # 1. Merge previous leftover parts if they exist (interrupted during a previous resume)
                if os.path.exists(part1_file) and os.path.exists(part2_file):
                    self.update_status(f"Status: Merging previous parts for {file_name}...")
                    self.log(" -> Found previously interrupted resumed parts. Merging them first...", tag="warning")
                    tmp_merge = output_file + ".merge" + target_ext
                    if self.concat_files(part1_file, part2_file, tmp_merge):
                        try:
                            os.remove(part1_file)
                            os.remove(part2_file)
                            os.rename(tmp_merge, part1_file)
                        except Exception as e:
                            self.log(f" -> Error during merge cleanup: {e}", tag="error")
                    else:
                        self.log(" -> Failed to merge previous parts. Dropping the broken part2.", tag="error")
                        try: os.remove(part2_file)
                        except: pass

                # 2. Check if a single incomplete output file exists and rename it to part1
                if os.path.exists(final_target_output) and not os.path.exists(part1_file):
                    try:
                        os.rename(final_target_output, part1_file)
                    except Exception as e:
                        self.log(f" -> Error renaming incomplete file for resume: {e}", tag="error")

                # 3. Analyze part1 for resuming
                if os.path.exists(part1_file):
                    if os.path.getsize(part1_file) > 0:
                        actual_duration = self.get_actual_readable_duration(part1_file)
                        
                        if 0 < actual_duration < encode_duration - 3:
                            self.log(f" -> Incomplete output detected. Readable duration: {format_duration(actual_duration)}. Resuming...", tag="warning")
                            is_resuming = True
                            cmd_seek_start = seek_start + actual_duration
                            cmd_encode_duration = encode_duration - actual_duration
                            apply_trim = True
                            output_file = part2_file
                        elif actual_duration >= encode_duration - 3:
                            self.log(" -> Output file exists and seems complete. Restoring and skipping encode.", tag="success")
                            try: os.rename(part1_file, final_target_output)
                            except: pass
                            skip_encode = True
                        else:
                            self.log(" -> Incomplete file unreadable or zero duration. Starting from scratch.", tag="error")
                            try: os.remove(part1_file)
                            except: pass
                    else:
                        try: os.remove(part1_file)
                        except: pass

            if not skip_encode:
                if engine == "HandBrakeCLI":
                    cmd = self.get_handbrake_cmd(
                        input_file, output_file, 
                        encode_duration=cmd_encode_duration, 
                        seek_start=cmd_seek_start, 
                        apply_trim=apply_trim,
                        crf_override=final_crf
                    )
                else:
                    cmd = self.get_ffmpeg_cmd(
                        input_file, output_file, 
                        target_duration=cmd_encode_duration, 
                        is_sample=False, 
                        with_audio=True, 
                        seek_start=cmd_seek_start, 
                        apply_trim=apply_trim,
                        crf_override=final_crf
                    )
                
                self.update_status(f"Status: Encoding {file_name} [{engine}]")
                self.update_stats(0.0, 0.0, 0.0, "Calc...")
                
                self.current_paused_duration = 0.0
                start_time = time.time()
                
                self.current_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, universal_newlines=True, creationflags=C_FLAGS)
                
                self.apply_process_settings()

                monitor = None
                if HAS_PSUTIL:
                    monitor = ProcessMonitor(self)
                    monitor.start()

                time_pattern = re.compile(r"time=(\d{2}:\d{2}:\d{2}\.\d+)")
                fps_pattern = re.compile(r"fps=\s*([\d\.]+)")
                frame_pattern = re.compile(r"frame=\s*(\d+)")
                hb_pattern = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*\(([\d\.]+)\s*fps,\s*avg\s*([\d\.]+)\s*fps,\s*ETA\s*([^)]+)\)")

                while True:
                    if self.cancel_requested:
                        self.current_process.kill()
                        break
                        
                    line = self.current_process.stdout.readline()
                    if not line and self.current_process.poll() is not None: break
                    if not line: continue

                    if engine == "HandBrakeCLI":
                        match_hb = hb_pattern.search(line)
                        if match_hb:
                            progress = float(match_hb.group(1))
                            cur_fps = float(match_hb.group(2))
                            avg_fps = float(match_hb.group(3))
                            eta_str = match_hb.group(4)
                            self.update_stats(progress, cur_fps, avg_fps, eta_str, "")
                    else:
                        match_time = time_pattern.search(line)
                        match_fps = fps_pattern.search(line)
                        match_frame = frame_pattern.search(line)

                        if match_time:
                            cur_fps = float(match_fps.group(1)) if match_fps else 0.0
                            cur_frame = int(match_frame.group(1)) if match_frame else 0
                            cur_sec = parse_time(match_time.group(1))
                            
                            progress = min((cur_sec / cmd_encode_duration) * 100, 100.0)
                            elapsed = self.get_adjusted_elapsed_time(start_time)
                            
                            avg_fps = (cur_frame / elapsed) if elapsed > 0 else 0.0

                            eta_str = "Calc..."
                            if cur_sec > 0:
                                eta_seconds = max(0, ((elapsed / cur_sec) * cmd_encode_duration) - elapsed)
                                eta_str = format_duration(eta_seconds)

                            self.update_stats(progress, cur_fps, avg_fps, eta_str, match_time.group(1))

                self.current_process.wait()
                
                if monitor: monitor.stop()
                    
                if self.cancel_requested:
                    break
                
                if self.current_process.returncode != 0:
                    self.log(f" -> Failed: {engine} exited with error code {self.current_process.returncode}", tag="error")
                    self.write_debug_log(name_we, timestamp)
                    continue
                    
                # --- Concat after successful Resume ---
                if is_resuming and os.path.exists(part2_file) and os.path.exists(part1_file):
                    success_concat = self.concat_files(part1_file, part2_file, final_target_output)
                    if success_concat:
                        self.log(" -> Resumed parts successfully concatenated.", tag="success")
                        try:
                            os.remove(part1_file)
                            os.remove(part2_file)
                        except: pass
                        output_file = final_target_output
                    else:
                        self.log(" -> Failed to concatenate resumed parts.", tag="error")
                        output_file = part2_file

                encoding_duration = self.get_adjusted_elapsed_time(start_time)
            else:
                encoding_duration = 0.0
                output_file = final_target_output
                self.update_stats(100.0, 0.0, 0.0, "0s", "Skipped")

            output_size = os.path.getsize(output_file) if os.path.exists(output_file) else 0
            
            frames_encoded = get_total_frames(part2_file) if (is_resuming and os.path.exists(part2_file)) else get_total_frames(output_file)
            final_avg_fps = frames_encoded / encoding_duration if encoding_duration > 0 else 0
            
            size_ratio = (output_size / input_size) * 100 if input_size > 0 else 0
            compression_ratio = 100 - size_ratio
            self.log(f"\n -> Encoding Completed in {format_duration(encoding_duration)} | Space Saved: {compression_ratio:.1f}%", tag="success")    
            enc_log = " [ Encoding Results ]\n"
            enc_log += f"   - Original Size  : {input_size / (1024*1024):.2f} MB\n"
            enc_log += f"   - Encoded Size   : {output_size / (1024*1024):.2f} MB\n"
            enc_log += f"   - Storage Saved  : {compression_ratio:.1f}% reduction\n"
            enc_log += f"   - Avg Speed      : {final_avg_fps:.1f} FPS | Time: {format_duration(encoding_duration)}\n"
            
            if not skip_encode and monitor:
                rep = monitor.get_report(encoding_duration / 3600.0) 
                self.log(f"   - Peak RAM Usage : {rep['peak_ram']:.0f} MB", tag="info")
                self.log(f"   - Avg CPU Usage  : {rep['avg_cpu']:.1f} %", tag="info")
                
                enc_log += f"   - Peak RAM Usage : {rep['peak_ram']:.0f} MB\n"
                enc_log += f"   - Avg CPU Usage  : {rep['avg_cpu']:.1f} %\n"
                
                if rep['avg_power'] > 0:
                    self.log(f"   - Avg CPU Power  : {rep['avg_power']:.1f} W", tag="info")
                    self.log(f"   - Energy Consumed: {rep['total_wh']:.3f} Wh", tag="info")
                    enc_log += f"   - Avg CPU Power  : {rep['avg_power']:.1f} W\n"
                    enc_log += f"   - Total Energy   : {rep['total_wh']:.3f} Wh\n"
                else:
                    self.log("   - Avg CPU Power  : Not Detected (Is OHM running?)", tag="warning")
                    enc_log += f"   - Avg CPU Power  : Not Detected (OHM not running)\n"

            enc_log += "\n"
            self.current_log_data["enc"] = enc_log
            self.save_current_log(log_file)

            qual_scores_post = {}
            if op_mode == "Enable Quality Estimation" and self.eval_submode_var.get() in ["Post-Encode Verification", "Both (Pre & Post)"]:
                qual_scores_post = self.run_post_quality_test(input_file, output_file, encode_duration, file_dir, name_we, seek_offset=seek_start)
                if self.cancel_requested: break
                
                if qual_scores_post:
                    post_log = " [ Actual Quality (Post-Test) ]\n"
                    for m, s in qual_scores_post.items():
                        lbl_post = get_quality_label(m, s)
                        post_log += f"   - Actual Quality({m}): {s:.2f} [{lbl_post}]\n"
                    post_log += "\n"
                    
                    self.current_log_data["post"] = post_log
                    self.save_current_log(log_file)

            self.current_log_data["end"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.save_current_log(log_file)

            reports.append({
                "name": file_name, "in_size": input_size, "out_size": output_size,
                "duration": encoding_duration, "fps": final_avg_fps,
                "ratio": size_ratio, "reduction": compression_ratio, 
                "qual_pre": qual_scores_pre, "qual_post": qual_scores_post
            })
            
            self.write_debug_log(name_we, timestamp)

        if self.cancel_requested:
            self.log("\n[!] Process was cancelled by user.", tag="error")
            self.update_status("Status: Cancelled")
        else:
            self.log("\n==================================================", tag="header")
            if self.benchmark_var.get():
                self.log("             ANALYZE ONLY SUMMARY           ", tag="header")
            else:
                self.log("              ENCODING SUMMARY              ", tag="header")
            self.log("==================================================", tag="header")
            total_in = sum(r["in_size"] for r in reports)
            total_out = sum(r["out_size"] for r in reports)
            
            for rep in reports:
                qual_txt = ""
                if rep['qual_pre']: 
                    for m, s in rep['qual_pre'].items():
                        lbl = get_quality_label(m, s)
                        qual_txt += f" | Est. {m}: {s:.2f} [{lbl}]"
                if rep['qual_post']: 
                    for m, s in rep['qual_post'].items():
                        lbl = get_quality_label(m, s)
                        qual_txt += f" | Act. {m}: {s:.2f} [{lbl}]"
                        
                size_str = f"-> {rep['out_size']/(1024*1024):.1f}MB" if rep['out_size'] > 0 else "(Analyze Only or Failed)"
                self.log(f" - {rep['name']}: {rep['in_size']/(1024*1024):.1f}MB {size_str} ({rep['ratio']:.1f}%){qual_txt}", tag="success")
            if reports:
                self.log(f"\n Total Processed : {len(reports)}")
                self.log(f" Total Time      : {format_duration(time.time() - total_start_time)}")
                if total_in > 0 and total_out > 0:
                    self.log(f" Total Saved     : {100 - ((total_out / total_in) * 100):.2f}% reduction", tag="success")
            
            self.update_status("Status: All Tasks Completed!")
            
            p_action = self.power_var.get()
            if p_action == "Quit":
                self.log("Closing application in 3 seconds...")
                self.root.after(3000, self.root.destroy)
            elif p_action == "Shutdown":
                self.log("Shutting down in 10 seconds...", tag="warning")
                os.system("shutdown /s /t 10")
            elif p_action == "Sleep":
                self.log("Putting system to sleep...", tag="info")
                os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")

        self.is_encoding = False
        self.btn_start.config(state=tk.NORMAL, text="Start Encoding")
        self.btn_pause.config(state=tk.DISABLED, text="Pause")
        self.set_ui_state(disable=False)


def check_dependencies():
    missing = []
    
    def _is_valid(path):
        return path and (os.path.isfile(path) or shutil.which(path))

    if not _is_valid(FFMPEG_EXE): missing.append("ffmpeg")
    if not _is_valid(FFPROBE_EXE): missing.append("ffprobe")
    if not _is_valid(FFVSHIP_EXE): missing.append("FFVship")
        
    if missing:
        temp_root = tk.Tk()
        temp_root.withdraw()
        error_msg = (
            "The following required tools were not found in your system PATH or 'Bin' folder next to the application:\n\n" +
            "\n".join(f" - {m}" for m in missing) +
            "\n\nPlease install them or place them in the 'Bin' folder before running."
        )
        messagebox.showerror("Missing Dependencies", error_msg)
        temp_root.destroy()
        return False
    return True

if __name__ == "__main__":
    resolve_tools()
    if not check_dependencies(): sys.exit(1)
        
    if HAS_DND: root = TkinterDnD.Tk()
    else: root = tk.Tk()
    
    try: 
        root.tk.call("source", "azure.tcl")
        root.tk.call("set_theme", "dark")
    except: pass

    app = EncoderApp(root)
    root.mainloop()

# --- END OF FILE aistudio-v2- bugfix.py ---
