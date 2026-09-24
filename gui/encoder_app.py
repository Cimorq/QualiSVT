"""EncoderApp: the main window, assembled from the mixins in gui/mixins/.

Each mixin owns one responsibility; EncoderApp itself only wires them together and
initialises the shared state they read and write:

    ProcessControlMixin     thread-safe UI queue, child-process spawn/kill/pause, failure logging
    UIBuilderMixin          Tk widget construction, settings save/load, window handlers
    LoggingMixin            log(), save_current_log(), status/progress updates
    FileListMixin           add/remove/clear files, drag & drop
    EncodingControlMixin    start/stop/pause/resume buttons
    MediaPrepMixin          GOP calculation, crop detection, filter-chain helpers
    CommandBuilderMixin     ffmpeg / HandBrakeCLI command lines (via encoder plugins)
    ExecutionMixin          runs one command and streams live progress
    MediaExtractMixin       sample-chunk / frame extraction for the quality preview
    QualityEvalMixin        FFVship/VMAF scoring, Auto-CRF search, pre/post quality tests
    QueueRunnerMixin        the per-file encode loop (uses everything above)

The three places where the plugin system plugs in are marked with "# PLUGIN HOOK":
gui/mixins/queue_runner.py (system monitor) and gui/mixins/command_builder.py
(ffmpeg codec flags, HandBrakeCLI command line).
"""
import queue
import threading

import core.config as config
from gui.mixins.command_builder import CommandBuilderMixin
from gui.mixins.encoding_control import EncodingControlMixin
from gui.mixins.execution import ExecutionMixin
from gui.mixins.file_list import FileListMixin
from gui.mixins.logging_mixin import LoggingMixin
from gui.mixins.media_extract import MediaExtractMixin
from gui.mixins.media_prep import MediaPrepMixin
from gui.mixins.process_control import ProcessControlMixin
from gui.mixins.quality_eval import QualityEvalMixin
from gui.mixins.queue_runner import QueueRunnerMixin
from gui.mixins.ui_builder import UIBuilderMixin


class EncoderApp(
    ProcessControlMixin,
    UIBuilderMixin,
    LoggingMixin,
    FileListMixin,
    EncodingControlMixin,
    MediaPrepMixin,
    CommandBuilderMixin,
    ExecutionMixin,
    MediaExtractMixin,
    QualityEvalMixin,
    QueueRunnerMixin,
):
    def __init__(self, root):
        self.root = root
        self.root.title(f"QualiSVT v{config.APP_VERSION} (Advanced Batch Encoder)")
        self.root.geometry("1050x730")
        self.root.minsize(980, 650)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self._init_state()
        self._init_thread_safety()

        self.build_ui()
        self.load_settings()
        self._drain_after_id = self.root.after(40, self._drain_ui_queue)

    def _init_state(self):
        self.files_to_process = []
        self.is_encoding = False
        self.is_paused = False
        self.cancel_requested = False
        self.current_process = None
        self.active_monitor = None
        self.current_paused_duration = 0.0
        self.pause_start_time = 0.0
        self.last_status = "Status: Idle"
        self.current_log_data = {}
        self.current_job_settings = {}
        self.live_priority = "Normal"
        self.live_cpu_cores = "1x"
        self.current_log_file = None
        self.job_files = []

    def _init_thread_safety(self):
        # Worker threads never touch Tk directly; they enqueue work via ui_call().
        self._ui_queue = queue.Queue()
        self._ui_thread_id = threading.get_ident()
        self._ui_closed = False
        # Guards current_process / is_paused / _suspended, which the UI thread and the
        # worker thread both need to read and change consistently.
        self._proc_lock = threading.RLock()
        self._suspended = {}  # root pid -> handles frozen by pause_subprocess()
