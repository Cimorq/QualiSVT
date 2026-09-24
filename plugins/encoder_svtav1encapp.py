"""
SvtAv1EncApp encoder backend — encodes directly with the official standalone
SVT-AV1 sample app instead of going through ffmpeg's "libsvtav1" wrapper.

Why this is worth having as a separate backend: SvtAv1EncApp exposes SVT-AV1
CLI options ahead of what ffmpeg's build often carries, and updates land
there first. The trade-off is that it is NOT an ffmpeg libavcodec codec, so
it can't just contribute extra flags to EncoderApp's existing ffmpeg command
the way FfmpegSvtAv1Backend does — it needs its own small pipeline:

    ffmpeg (decode + filters, same -vf chain EncoderApp already builds)
        -f yuv4mpegpipe -   (stdout)
      | SvtAv1EncApp -i stdin ...     -> raw .ivf elementary stream
    ffmpeg -i raw.ivf -i <audio/subs from original> -c copy output_file

`build_pipeline()` below returns that as three ready-to-run command lists.
is_ffmpeg_native = False is the flag EncoderApp's backend-selection code uses
to know it must call build_pipeline() instead of ffmpeg_video_args().

STATUS: this plugin is functionally complete and self-testable (is_available(),
build_pipeline()) but is not yet wired into EncoderApp.run_command_with_progress(),
which today only knows how to stream-parse progress from a single ffmpeg or
HandBrakeCLI process. Wiring it in means adding a third branch there that
runs the 3 commands in sequence and reads progress from SvtAv1EncApp's own
stderr (it prints "Encoding frame N/M" style lines, similar shape to ffmpeg's
"frame=" but not identical — needs its own regex). That's the next concrete
step; flagged here rather than guessed at, since run_command_with_progress
also owns pause/cancel semantics that a 3-process pipeline has to respect.
"""
import os

import core.config as config
from plugins.base import EncoderBackend, manager


@manager.register_encoder
class SvtAv1EncAppBackend(EncoderBackend):
    name = "SVT-AV1 (SvtAv1EncApp, direct)"
    is_ffmpeg_native = False

    @classmethod
    def is_available(cls) -> bool:
        exe = getattr(config, "SVTAV1ENCAPP_EXE", "SvtAv1EncApp")
        return bool(exe) and (os.path.isfile(exe) or exe != "SvtAv1EncApp")

    def _svt_params_from_settings(self, s: dict, gop_frames) -> list:
        """Reuse the same svt_params/tune/keyint logic FfmpegSvtAv1Backend uses,
        translated to SvtAv1EncApp's own --flag form instead of -svtav1-params."""
        args = []
        preset = s.get("preset", "6")
        args.extend(["--preset", str(preset)])
        crf = s.get("crf", "34.0")
        args.extend(["--crf", str(crf)])
        if gop_frames is not None:
            args.extend(["--keyint", str(gop_frames)])
        tune_val = s.get("tune", "").strip()
        if tune_val and tune_val.lower() != "none":
            args.extend(["--tune", tune_val.split(" ")[0].strip() if "(" in tune_val else tune_val])
        # svt_params from the UI is currently written in ffmpeg's "-svtav1-params
        # k=v:k=v" shape; SvtAv1EncApp takes the same keys as separate --k v flags.
        base_svt_p = s.get("svt_params", "").strip()
        for pair in base_svt_p.split(":"):
            if not pair or "=" not in pair or pair.startswith("tune="):
                continue
            k, v = pair.split("=", 1)
            args.extend([f"--{k}", v])
        return args

    def build_pipeline(
        self, input_file, output_file, s, *, vf_chain="", pix_fmt="yuv420p10le", gop_frames=None, with_audio=True
    ):
        """Return [decode_cmd, encode_cmd, mux_cmd] — see module docstring for the shape."""
        ffmpeg = config.FFMPEG_EXE
        svtenc = getattr(config, "SVTAV1ENCAPP_EXE", "SvtAv1EncApp")
        raw_es = os.path.splitext(output_file)[0] + ".svtav1.ivf"

        decode_cmd = [ffmpeg, "-y", "-i", input_file]
        if vf_chain:
            decode_cmd.extend(["-vf", vf_chain])
        decode_cmd.extend(["-pix_fmt", pix_fmt, "-f", "yuv4mpegpipe", "-strict", "-1", "-"])

        encode_cmd = [svtenc, "-i", "stdin", "-b", raw_es]
        encode_cmd.extend(self._svt_params_from_settings(s, gop_frames))

        mux_cmd = [ffmpeg, "-y", "-i", raw_es, "-i", input_file]
        if with_audio:
            mux_cmd.extend(["-map", "0:v:0", "-map", "1:a?", "-c:a", "copy"])
        else:
            mux_cmd.extend(["-map", "0:v:0", "-an"])
        mux_cmd.extend(["-map", "1:s?", "-c:s", "copy", "-c:v", "copy", output_file])

        return [decode_cmd, encode_cmd, mux_cmd]
