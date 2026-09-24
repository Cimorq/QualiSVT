"""
ffmpeg-native encoder backends.

These wrap ffmpeg's own libavcodec encoders (libsvtav1, libx265). The video
codec flags below are extracted verbatim from the original single-file app's
EncoderApp.get_ffmpeg_cmd() — behavior is unchanged, it's just now a
swappable plugin instead of an inline if/else EncoderApp owned directly.
"""
import core.config as config
from plugins.base import EncoderBackend, manager


@manager.register_encoder
class FfmpegSvtAv1Backend(EncoderBackend):
    name = "SVT-AV1 (ffmpeg libsvtav1)"
    is_ffmpeg_native = True

    @classmethod
    def is_available(cls) -> bool:
        return bool(config.FFMPEG_EXE)

    def ffmpeg_video_args(self, s, crf_val, pix_fmt, gop_frames, is_sample, output_file):
        cmd = ["-c:v", "libsvtav1", "-crf", crf_val, "-pix_fmt", pix_fmt, "-preset", s.get("preset", "6"), "-dn"]
        if output_file.lower().endswith(".mkv") or (not is_sample and s.get("out_format") == "MKV"):
            cmd.extend(["-write_crc32", "false", "-cues_to_front", "y"])
        if is_sample:
            cmd.extend(["-fps_mode", "passthrough"])
        base_svt_p = s.get("svt_params", "").strip()
        if gop_frames is not None:
            base_svt_p = (base_svt_p + f":keyint={gop_frames}") if base_svt_p else f"keyint={gop_frames}"
        tune_val = s.get("tune", "").strip()
        params_list = [p for p in base_svt_p.split(":") if p and not p.startswith("tune=")]
        if tune_val and tune_val.lower() != "none":
            params_list.insert(0, f"tune={tune_val.split(' ')[0].strip() if '(' in tune_val else tune_val}")
        if final_svt_p := ":".join(params_list):
            cmd.extend(["-svtav1-params", final_svt_p])
        if gop_frames is not None:
            cmd.extend(["-g", str(gop_frames)])
        return cmd


@manager.register_encoder
class FfmpegX265Backend(EncoderBackend):
    name = "x265 (HEVC)"
    is_ffmpeg_native = True

    @classmethod
    def is_available(cls) -> bool:
        return bool(config.FFMPEG_EXE)

    def ffmpeg_video_args(self, s, crf_val, pix_fmt, gop_frames, is_sample, output_file):
        cmd = ["-c:v", "libx265", "-crf", crf_val, "-pix_fmt", pix_fmt]
        if preset := s.get("preset", ""):
            cmd.extend(["-preset", preset])
        tune_val = s.get("tune", "").strip()
        if tune_val and tune_val.lower() != "none":
            cmd.extend(["-tune", tune_val])
        x265_p = s.get("x265_params", "").strip()
        if gop_frames is not None:
            x265_p = (x265_p + f":keyint={gop_frames}") if x265_p else f"keyint={gop_frames}"
        if x265_p:
            cmd.extend(["-x265-params", x265_p])
        if gop_frames is not None:
            cmd.extend(["-g", str(gop_frames)])
        return cmd


def backend_for_encoder_setting(encoder_setting: str) -> EncoderBackend:
    """Map the existing settings["encoder"] string ("SVT-AV1" / "x265 (HEVC)") to a backend
    instance, so EncoderApp's existing settings UI keeps working unchanged."""
    if encoder_setting == "x265 (HEVC)":
        return FfmpegX265Backend()
    return FfmpegSvtAv1Backend()
