"""Thread-safe UI queue, child-process spawn/kill/pause, and failure logging."""
import datetime
import os
import queue
import subprocess
import threading
import traceback
import tkinter as tk

import core.config as config
from core.output_capture import OutputCapture
from core.proc_utils import format_cmd, kill_process_tree, pause_subprocess

if config.HAS_PSUTIL:
    import psutil


class ProcessControlMixin:
    def apply_process_settings(self, event=None):
        proc = self.current_process  # local copy: the worker thread may clear the attribute at any time
        if not config.HAS_PSUTIL or proc is None or proc.poll() is not None:
            return
        prio_str, core_str = getattr(self, "live_priority", "Normal"), getattr(self, "live_cpu_cores", "1x")

        def _apply():
            try:
                if proc.poll() is not None:
                    return
                p = psutil.Process(proc.pid)

                mapping_nt = {
                    "Realtime": psutil.REALTIME_PRIORITY_CLASS,
                    "High": psutil.HIGH_PRIORITY_CLASS,
                    "Above Normal": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
                    "Normal": psutil.NORMAL_PRIORITY_CLASS,
                    "Below Normal": psutil.BELOW_NORMAL_PRIORITY_CLASS,
                    "Idle": psutil.IDLE_PRIORITY_CLASS
                } if os.name == "nt" else {}
                mapping_ux = {
                    "Realtime": -20, "High": -10, "Above Normal": -5, "Normal": 0, "Below Normal": 5, "Idle": 19
                } if os.name != "nt" else {}

                if os.name == "nt" and prio_str in mapping_nt:
                    p.nice(mapping_nt[prio_str])
                elif os.name != "nt" and prio_str in mapping_ux:
                    p.nice(mapping_ux[prio_str])

                valid_cores = list(range(int(core_str.split("x")[0])))
                try:
                    p.cpu_affinity(valid_cores)
                except AttributeError:
                    pass

                for child in p.children(recursive=True):
                    try:
                        if os.name == "nt" and prio_str in mapping_nt:
                            child.nice(mapping_nt[prio_str])
                        elif os.name != "nt" and prio_str in mapping_ux:
                            child.nice(mapping_ux[prio_str])
                        try:
                            child.cpu_affinity(valid_cores)
                        except AttributeError:
                            pass
                    except Exception:
                        pass
            except Exception:
                pass

        threading.Thread(target=_apply, daemon=True).start()

    # ------------------------------------------------------------------
    # Thread-safe UI access
    # ------------------------------------------------------------------
    def ui_call(self, func, *args, **kwargs):
        """Run func(*args, **kwargs) on the Tk main thread.

        Tkinter is not thread-safe: worker threads must not touch widgets, Tk
        variables or even root.after(). They hand the work to a queue instead and
        the main thread executes it from _drain_ui_queue(). Called from the main
        thread itself, the function simply runs immediately.
        """
        if self._ui_closed:
            return
        if threading.get_ident() == self._ui_thread_id:
            func(*args, **kwargs)
        else:
            self._ui_queue.put((func, args, kwargs))

    def _drain_ui_queue(self):
        try:
            for _ in range(500):  # bounded, so a burst of log lines cannot freeze the UI
                try:
                    func, args, kwargs = self._ui_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    func(*args, **kwargs)
                except tk.TclError:
                    pass
                except Exception:
                    traceback.print_exc()
        finally:
            if not self._ui_closed:
                try:
                    self._drain_after_id = self.root.after(40, self._drain_ui_queue)
                except (tk.TclError, RuntimeError):
                    pass

    # ------------------------------------------------------------------
    # Child-process management (register / pause / cancel / clean up)
    # ------------------------------------------------------------------
    def _register_process(self, proc):
        """Make *proc* the current process and honour a pending Pause/Cancel immediately."""
        cancelled = False
        with self._proc_lock:
            self.current_process = proc
            if self.cancel_requested:
                cancelled = True
            elif self.is_paused:
                # a new step must not run while the user has paused
                self._suspended[proc.pid] = pause_subprocess(proc.pid)
        if cancelled:
            kill_process_tree(proc)
        else:
            self.apply_process_settings()

    def _spawn_tracked(self, cmd, **popen_kwargs):
        """subprocess.Popen whose whole process tree can be paused, resumed and cancelled."""
        popen_kwargs.setdefault("creationflags", config.C_FLAGS)
        proc = subprocess.Popen(cmd, **popen_kwargs)
        self._register_process(proc)
        return proc

    def _run_tracked(self, cmd, **popen_kwargs):
        """subprocess.run() replacement (no timeout/check) that stays controllable from the UI."""
        proc = self._spawn_tracked(cmd, **popen_kwargs)
        try:
            out, err = proc.communicate()
            return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
        finally:
            self._release_process(proc)

    def _release_process(self, proc):
        """Idempotent: kill *proc* (and its children) if it is still alive, close its pipes, forget it."""
        if proc is None:
            return
        kill_process_tree(proc)  # no-op when the process already finished
        for stream in (proc.stdout, proc.stderr, proc.stdin):
            try:
                if stream:
                    stream.close()
            except Exception:
                pass
        with self._proc_lock:
            self._suspended.pop(proc.pid, None)
            if self.current_process is proc:
                self.current_process = None

    def _cleanup_job_resources(self):
        """Called from finally blocks: stop the resource monitor and kill any process still running."""
        monitor, self.active_monitor = self.active_monitor, None
        if monitor:
            try:
                monitor.stop()
            except Exception:
                pass
        with self._proc_lock:
            proc = self.current_process
        if proc is not None:
            self._release_process(proc)

    def _record_failure(self, title, cmd, returncode, output_text="", console_tail=40, max_sections=10):
        """Keep everything needed to diagnose a failed child process.

        The console gets the tail of the output; the job's log file gets the complete
        (progress-filtered) output plus the exact command line and exit code.
        """
        try:
            capture = OutputCapture()
            capture.add_text(output_text)
            out_lines = capture.render().splitlines()
            self.log(f" -> {title} failed (exit code {returncode}).", tag="error")
            if out_lines:
                tail = out_lines[-console_tail:]
                self.log(
                    f"    Last {len(tail)} of {len(out_lines)} output lines:\n" + "\n".join(f"    | {l}" for l in tail),
                    tag="error"
                )
            data = self.current_log_data
            if isinstance(data, dict):
                n = data.get("_failures", 0) + 1
                data["_failures"] = n
                if n <= max_sections:
                    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    data["failure"] = data.get("failure", "") + f" [ FAILURE #{n}: {title} ]\n   - Time      : {stamp}\n   - Exit Code : {returncode}\n   - Command   : {format_cmd(cmd) if cmd else 'n/a'}\n   - Output    :\n" + "\n".join(
                        "       " + l for l in out_lines
                    ) + "\n\n"
                elif n == max_sections + 1:
                    data["failure"] = data.get(
                        "failure", ""
                    ) + f" (Further failures are not written to this log; see the console.)\n\n"
            if self.current_log_file:
                self.log(f"    Full output saved to: {self.current_log_file}", tag="error")
            else:
                self.log("    (Log saving is disabled - the full output was not saved.)", tag="warning")
            self.save_current_log(self.current_log_file)
        except Exception as e:
            self.log(f" -> Could not record failure details: {e}", tag="warning")
