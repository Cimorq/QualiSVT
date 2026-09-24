"""
Plugin interfaces for QualiSVT.

Two extension points, matching what was discussed:

1. SystemMonitorPlugin — a source of live CPU/RAM/power telemetry during an
   encode (today: OpenHardwareMonitor via WMI). A new sensor source (e.g.
   LibreHardwareMonitor, a Linux hwmon reader, NVML for GPU power) can be
   added as another SystemMonitorPlugin without touching EncoderApp.

2. EncoderBackend — something that knows how to encode a file. Today the app
   only calls ffmpeg's built-in "libsvtav1"/"libx265" codecs; this interface
   is the seam that lets a future backend (like the standalone SvtAv1EncApp
   binary) plug in as an alternative encoding path, selectable in the UI,
   without the rest of the app (queueing, pause/cancel, logging, caching)
   needing to know which one is active.

Plugins are discovered by PluginManager from a fixed built-in list plus
(optionally) any *_plugin.py files dropped in the user's plugins folder, so
this stays simple: no pip packaging or entry_points needed for this scale.
"""
from abc import ABC, abstractmethod


class SystemMonitorPlugin(ABC):
    """A live telemetry source (CPU %, RAM, power draw) sampled during an encode.

    Concrete plugins wrap whatever tool actually reads the hardware (WMI +
    OpenHardwareMonitor today). EncoderApp only ever talks to this interface,
    so a new sensor source is a new plugin file, not a change to EncoderApp.
    """
    #: Short unique id, shown in the UI's "Sensor source" dropdown.
    name: str = "base"

    @classmethod
    def is_available(cls) -> bool:
        """Cheap, side-effect-free check: can this plugin run on this machine at all?"""
        return True

    @abstractmethod
    def __init__(self, app_ref):
        """app_ref: the EncoderApp instance, so the monitor can read app.current_process."""
        raise NotImplementedError

    @abstractmethod
    def start(self) -> None:
        ...

    @abstractmethod
    def stop(self) -> None:
        ...

    @abstractmethod
    def get_report(self, custom_duration_hrs=None) -> dict:
        """Return {"avg_cpu": float, "peak_ram": float, "avg_power": float, "total_wh": float}."""
        ...


class EncoderBackend(ABC):
    """Something that can turn (input file, settings) into an encoded output.

    `ffmpeg_video_args` is the narrow seam used today: EncoderApp still builds
    the full ffmpeg command line (input, filters, mapping, audio, subtitles —
    all encoder-agnostic), and only asks the active EncoderBackend for the
    video-codec-specific flags to splice in. This is enough to make the two
    ffmpeg-native codecs (libsvtav1, libx265) real, swappable plugins.

    A backend that is NOT an ffmpeg libavcodec codec (like the standalone
    SvtAv1EncApp binary) can't just contribute "extra ffmpeg flags" — it needs
    its own pipeline (decode with ffmpeg -> pipe y4m -> SvtAv1EncApp -> mux).
    For that case implement `is_ffmpeg_native = False` and `build_pipeline()`
    instead; see plugins/encoder_svtav1encapp.py for the concrete shape and the
    TODO marking where EncoderApp's runner needs a matching branch.
    """
    name: str = "base"
    #: True: contributes just video-codec flags to EncoderApp's own ffmpeg command.
    #: False: needs its own multi-process pipeline (build_pipeline()).
    is_ffmpeg_native: bool = True

    @classmethod
    def is_available(cls) -> bool:
        return True

    def ffmpeg_video_args(
        self, s: dict, crf_val: str, pix_fmt: str, gop_frames, is_sample: bool, output_file: str
    ) -> list:
        """Return the ffmpeg CLI args for this codec (e.g. ["-c:v", "libsvtav1", ...]).

        Only required when is_ffmpeg_native is True.
        s: the job's settings dict (same shape used throughout EncoderApp).
        """
        raise NotImplementedError

    def build_pipeline(self, input_file: str, output_file: str, s: dict, **kwargs) -> list:
        """Return an ordered list of subprocess command lines to run in sequence.

        Only required when is_ffmpeg_native is False.
        """
        raise NotImplementedError


class PluginManager:
    """Tiny registry: built-in plugins + optional drop-in *_plugin.py files."""

    def __init__(self):
        self._monitors: dict[str, type] = {}
        self._encoders: dict[str, type] = {}

    def register_monitor(self, cls):
        self._monitors[cls.name] = cls
        return cls

    def register_encoder(self, cls):
        self._encoders[cls.name] = cls
        return cls

    def available_monitors(self):
        return {n: c for n, c in self._monitors.items() if c.is_available()}

    def available_encoders(self):
        return {n: c for n, c in self._encoders.items() if c.is_available()}

    def get_monitor(self, name: str, default=None):
        return self._monitors.get(name, default)

    def get_encoder(self, name: str, default=None):
        return self._encoders.get(name, default)


# One shared manager instance, populated by plugins/__init__.py at import time.
manager = PluginManager()
