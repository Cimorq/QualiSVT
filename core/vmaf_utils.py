"""Quality-metric parsing and human labels (VMAF, SSIMULACRA2, CVVDP, Butteraugli, XPSNR/WPSNR)."""
import os


def get_metric_thread_count():
    return max(1, (os.cpu_count() or 1) - 1)


def parse_vmaf_json_score(data):
    pooled = data.get("pooled_metrics", {}) if isinstance(data, dict) else {}
    vmaf = pooled.get("vmaf", {}) if isinstance(pooled, dict) else {}
    if isinstance(vmaf, dict) and vmaf.get("mean") is not None:
        return float(vmaf["mean"])
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


# Labels from best to worst; anything that misses every threshold is "Bad".
_QUALITY_LABELS = ("Visually Lossless", "Excellent", "Good", "Fair", "Poor")

# metric -> (higher_is_better, thresholds for the labels above)
_SSIM2_SCALE = (True, (90.0, 75.0, 60.0, 50.0, 30.0))
_PSNR_SCALE = (True, (45.0, 42.0, 38.0, 35.0, 30.0))
_QUALITY_SCALES = {
    "VMAF": (True, (97.0, 93.0, 85.0, 75.0, 60.0)),
    "SSIMULACRA2": _SSIM2_SCALE,
    "SSIM2": _SSIM2_SCALE,
    "CVVDP": (True, (9.5, 8.5, 7.5, 6.5, 5.5)),
    "BUTTERAUGLI": (False, (0.5, 1.5, 2.5, 3.5, 4.5)),
    "XPSNR": _PSNR_SCALE,
    "WPSNR": _PSNR_SCALE,
}


def get_quality_label(metric: str, score: float) -> str:
    if score is None or not isinstance(score, (int, float)):
        return "Unknown"
    scale = _QUALITY_SCALES.get(metric.upper().strip())
    if scale is None:
        return "Unknown"
    higher_is_better, thresholds = scale
    for label, limit in zip(_QUALITY_LABELS, thresholds):
        if (score >= limit) if higher_is_better else (score <= limit):
            return label
    return "Bad"


def get_quality_color_tag(label):
    return {
        "Visually Lossless": "q_vlossless", "Excellent": "q_super", "Good": "q_high", "Fair": "q_med", "Poor": "q_low"
    }.get(label, "q_bad")
