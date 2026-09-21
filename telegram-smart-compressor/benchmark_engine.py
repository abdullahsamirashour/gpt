# Media Lite Encoder Benchmark Engine
# Benchmark-only: never edits Telegram channel state and never uploads benchmark outputs.

from __future__ import annotations

import asyncio
import getpass
import html
import importlib.util
import json
import math
import os
import platform
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

BENCH_VERSION = "1.1.0"
BASE_DIR = Path("/content/drive/MyDrive/Telegram_Extreme_Compressor")
CONFIG_PATH = BASE_DIR / "config.json"
SESSION_FILE = BASE_DIR / "telegram_user.session"
WORK_DIR = Path("/content/media_lite_encoder_benchmark")
REPORT_ROOT = BASE_DIR / "benchmarks"
NVENCC_VERSION = "9.35"
NVENCC_URL = f"https://github.com/rigaya/NVEnc/releases/download/{NVENCC_VERSION}/nvencc_{NVENCC_VERSION}_amd64.deb"
GENERATED_PREFIXES = ("✅ تم •", "✅ v")


@dataclass
class VideoInfo:
    duration: float
    width: int
    height: int
    fps: float
    codec: str
    pix_fmt: str
    size: int
    has_audio: bool


def run(cmd, *, check=True, capture=True, timeout=None, env=None):
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        timeout=timeout,
        env=env,
    )


def ensure_dependencies():
    packages = [
        "telethon==1.45.0",
        "cryptg",
        "nest_asyncio",
        "aiofasttelethonhelper==0.1.3",
        "psutil",
        "pandas",
        "tabulate",
    ]
    missing = []
    for module, pkg in [
        ("telethon", packages[0]),
        ("cryptg", "cryptg"),
        ("nest_asyncio", "nest_asyncio"),
        ("aiofasttelethonhelper", "aiofasttelethonhelper==0.1.3"),
        ("psutil", "psutil"),
        ("pandas", "pandas"),
        ("tabulate", "tabulate"),
    ]:
        if importlib.util.find_spec(module) is None:
            missing.append(pkg)

    if missing:
        print("🔧 تجهيز مكتبات البنش مارك...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "--upgrade", *missing],
            check=True,
        )

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        subprocess.run(["apt-get", "-qq", "update"], check=True)
        subprocess.run(["apt-get", "-qq", "install", "-y", "ffmpeg"], check=True)


def mount_drive():
    from google.colab import drive

    try:
        drive.mount("/content/drive")
    except Exception:
        drive.mount("/content/drive", force_remount=False)
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)


def cpu_model_name():
    try:
        for line in Path("/proc/cpuinfo").read_text(errors="ignore").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or platform.machine() or "Unknown CPU"


def ffmpeg_version():
    try:
        return run(["ffmpeg", "-version"]).stdout.splitlines()[0]
    except Exception:
        return "unknown"


def ffmpeg_has_filter(name: str) -> bool:
    try:
        return name in run(["ffmpeg", "-hide_banner", "-filters"]).stdout
    except Exception:
        return False


def probe_command(cmd, timeout=20):
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
        text = (proc.stderr or proc.stdout or "").strip()
        return {
            "ok": proc.returncode == 0,
            "returncode": int(proc.returncode),
            "reason": text[-1200:] if text else "",
        }
    except Exception as exc:
        return {
            "ok": False,
            "returncode": None,
            "reason": f"{type(exc).__name__}: {str(exc)[:1000]}",
        }


def ffmpeg_has_encoder(name: str) -> bool:
    try:
        text = run(["ffmpeg", "-hide_banner", "-encoders"]).stdout
        return bool(re.search(rf"\\b{re.escape(name)}\\b", text))
    except Exception:
        return False


def detect_nvenc_details():
    listed = ffmpeg_has_encoder("h264_nvenc")
    if not shutil.which("nvidia-smi"):
        return {
            "listed": listed,
            "ok": False,
            "reason": "nvidia-smi غير موجود؛ لا يوجد NVIDIA GPU متاح للـRuntime.",
        }

    probe = probe_command(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=size=128x72:rate=10",
            "-t", "1",
            "-an",
            "-c:v", "h264_nvenc",
            "-preset", "p3",
            "-f", "null", "-",
        ],
        timeout=25,
    )
    return {
        "listed": listed,
        "ok": bool(probe["ok"]),
        "reason": probe["reason"],
    }


def detect_nvenc() -> bool:
    return bool(detect_nvenc_details()["ok"])



def gpu_info():
    if not shutil.which("nvidia-smi"):
        return None
    try:
        q = run([
            "nvidia-smi",
            "--query-gpu=name,driver_version,memory.total",
            "--format=csv,noheader,nounits",
        ]).stdout.strip().splitlines()[0]
        name, driver, memory_mb = [x.strip() for x in q.split(",", 2)]
        return {
            "name": name,
            "driver": driver,
            "memory_mb": int(float(memory_mb)),
        }
    except Exception:
        try:
            text = run(["nvidia-smi"]).stdout
            return {"name": "NVIDIA GPU", "driver": "unknown", "memory_mb": 0, "raw": text[:500]}
        except Exception:
            return None


def hardware_info():
    import psutil

    gpu = gpu_info()
    logical = psutil.cpu_count(logical=True) or os.cpu_count() or 0
    physical = psutil.cpu_count(logical=False) or logical
    ram_gb = psutil.virtual_memory().total / (1024 ** 3)

    try:
        lscpu = run(["lscpu", "-J"]).stdout
        lscpu_json = json.loads(lscpu)
        lscpu_map = {
            row.get("field", "").strip(":"): row.get("data")
            for row in lscpu_json.get("lscpu", [])
        }
    except Exception:
        lscpu_map = {}

    nvenc = detect_nvenc_details()
    scale_cuda = ffmpeg_has_filter("scale_cuda")
    libvmaf = ffmpeg_has_filter("libvmaf")

    if gpu:
        runtime = gpu["name"]
        runtime_kind = "T4 GPU" if "T4" in gpu["name"].upper() else f"GPU — {gpu['name']}"
    else:
        runtime = "CPU only"
        runtime_kind = "CPU only"

    return {
        "runtime": runtime,
        "runtime_kind": runtime_kind,
        "cpu_model": cpu_model_name(),
        "cpu_physical_cores": int(physical),
        "cpu_logical_threads": int(logical),
        "cpu_sockets": lscpu_map.get("Socket(s)", "?"),
        "threads_per_core": lscpu_map.get("Thread(s) per core", "?"),
        "ram_gb": round(ram_gb, 2),
        "gpu": gpu,
        "ffmpeg_nvenc_encoder_listed": bool(nvenc["listed"]),
        "ffmpeg_nvenc_smoke_ok": bool(nvenc["ok"]),
        "ffmpeg_nvenc_smoke_reason": nvenc["reason"],
        "nvenc_available": bool(nvenc["ok"]),
        "scale_cuda_available": scale_cuda,
        "libvmaf_available": libvmaf,
        "pynvvideocodec_available": importlib.util.find_spec("PyNvVideoCodec") is not None,
        "source_nvdec_ok": None,
        "source_nvdec_reason": "",
        "source_full_gpu_ok": None,
        "source_full_gpu_reason": "",
        "nvencc_installed": False,
        "nvencc_hw_ok": None,
        "nvencc_hw_reason": "",
        "ffmpeg": ffmpeg_version(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }



def show_hardware(info):
    try:
        from IPython.display import HTML, display

        gpu_line = "لا يوجد"
        if info["gpu"]:
            gpu_line = (
                f'{info["gpu"]["name"]} — '
                f'{info["gpu"]["memory_mb"] / 1024:.1f} GB VRAM — '
                f'Driver {info["gpu"]["driver"]}'
            )
        display(HTML(
            '<div dir="rtl" style="max-width:900px;padding:14px 16px;border:1px solid #d0d7de;'
            'border-radius:12px;background:#f8fafc;font-family:Arial,sans-serif;line-height:1.9">'
            '<b style="font-size:18px">🧪 بيئة البنش مارك</b><br>'
            f'<b>Runtime:</b> <bdi dir="ltr">{info["runtime_kind"]}</bdi><br>'
            f'<b>Processor:</b> <bdi dir="ltr">{info["cpu_model"]}</bdi><br>'
            f'<b>CPU:</b> <bdi dir="ltr">{info["cpu_physical_cores"]} physical cores / '
            f'{info["cpu_logical_threads"]} logical threads</bdi><br>'
            f'<b>GPU:</b> <bdi dir="ltr">{gpu_line}</bdi><br>'
            f'<b>RAM:</b> <bdi dir="ltr">{info["ram_gb"]:.1f} GB</bdi><br>'
            f'<b>FFmpeg h264_nvenc listed:</b> {"✅" if info["ffmpeg_nvenc_encoder_listed"] else "❌"} &nbsp; '
            f'<b>NVENC smoke:</b> {"✅" if info["ffmpeg_nvenc_smoke_ok"] else "❌"} &nbsp; '
            f'<b>scale_cuda:</b> {"✅" if info["scale_cuda_available"] else "❌"} &nbsp; '
            f'<b>libvmaf:</b> {"✅" if info["libvmaf_available"] else "❌"}'
            '</div>'
        ))
    except Exception:
        print(json.dumps(info, ensure_ascii=False, indent=2))


def show_gpu_diagnostics(info):
    if not info.get("gpu"):
        return
    rows = [
        ("FFmpeg h264_nvenc موجود", info.get("ffmpeg_nvenc_encoder_listed"), ""),
        ("FFmpeg NVENC smoke", info.get("ffmpeg_nvenc_smoke_ok"), info.get("ffmpeg_nvenc_smoke_reason", "")),
        ("NVDEC على فيديو المصدر", info.get("source_nvdec_ok"), info.get("source_nvdec_reason", "")),
        ("Full GPU NVDEC→CUDA→NVENC", info.get("source_full_gpu_ok"), info.get("source_full_gpu_reason", "")),
        ("NVEncC hardware check", info.get("nvencc_hw_ok"), info.get("nvencc_hw_reason", "")),
    ]
    try:
        from IPython.display import HTML, display
        html_rows = []
        for label, ok, reason in rows:
            mark = "✅" if ok is True else ("❌" if ok is False else "➖")
            detail = ""
            if ok is False and reason:
                clean = html.escape(str(reason).replace("\n", " ")[:260])
                detail = f'<br><span style="color:#6b7280;font-size:12px"><bdi dir="ltr">{clean}</bdi></span>'
            html_rows.append(f'<div style="margin:5px 0">{mark} <b>{label}</b>{detail}</div>')
        display(HTML(
            '<div dir="rtl" style="max-width:900px;padding:12px 16px;margin:8px 0;'
            'border:1px solid #d0d7de;border-radius:12px;background:#fff7ed;'
            'font-family:Arial,sans-serif">'
            '<b>🧰 تشخيص مسار الـGPU</b>'
            + "".join(html_rows) +
            '</div>'
        ))
    except Exception:
        print("GPU diagnostics:")
        for label, ok, reason in rows:
            print(label, ok, reason[:300] if reason else "")



def parse_rate(value):
    if not value or value in ("0/0", "N/A"):
        return 0.0
    try:
        if "/" in value:
            a, b = value.split("/", 1)
            return float(a) / float(b) if float(b) else 0.0
        return float(value)
    except Exception:
        return 0.0


def probe_video(path: Path) -> VideoInfo:
    data = json.loads(run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path)
    ]).stdout)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise RuntimeError("الملف لا يحتوي على فيديو.")
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    duration = float(
        video.get("duration")
        or data.get("format", {}).get("duration")
        or 0
    )
    return VideoInfo(
        duration=duration,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps=parse_rate(video.get("avg_frame_rate") or video.get("r_frame_rate")),
        codec=str(video.get("codec_name") or ""),
        pix_fmt=str(video.get("pix_fmt") or ""),
        size=int(data.get("format", {}).get("size") or path.stat().st_size),
        has_audio=has_audio,
    )


def fit_dims(width, height, max_w=1280, max_h=720):
    if width <= 0 or height <= 0:
        return max_w, max_h
    ratio = min(1.0, max_w / width, max_h / height)
    w = max(2, int(width * ratio) // 2 * 2)
    h = max(2, int(height * ratio) // 2 * 2)
    return w, h



def diagnose_source_gpu(src: Path, info: VideoInfo, hw):
    if not hw.get("gpu"):
        return hw

    start = max(0.0, min(info.duration * 0.25, max(0.0, info.duration - 2.0)))
    target_w, target_h = fit_dims(info.width, info.height)

    nvdec_probe = probe_command(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-hwaccel", "cuda",
            "-hwaccel_output_format", "cuda",
            "-ss", f"{start:.3f}",
            "-i", str(src),
            "-t", "1.5",
            "-an",
            "-vf", "hwdownload,format=nv12",
            "-f", "null", "-",
        ],
        timeout=30,
    )
    hw["source_nvdec_ok"] = bool(nvdec_probe["ok"])
    hw["source_nvdec_reason"] = nvdec_probe["reason"]

    full_probe = probe_command(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-hwaccel", "cuda",
            "-hwaccel_output_format", "cuda",
            "-ss", f"{start:.3f}",
            "-i", str(src),
            "-t", "1.5",
            "-an",
            "-vf", f"scale_cuda={target_w}:{target_h}",
            "-c:v", "h264_nvenc",
            "-preset", "p3",
            "-f", "null", "-",
        ],
        timeout=30,
    )
    hw["source_full_gpu_ok"] = bool(full_probe["ok"])
    hw["source_full_gpu_reason"] = full_probe["reason"]
    return hw


def human_time(seconds):
    seconds = max(0, int(round(seconds or 0)))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def generated_output(msg):
    text = (getattr(msg, "message", None) or "").strip()
    return text.startswith(GENERATED_PREFIXES)


async def telegram_source(message_id=""):
    from telethon import TelegramClient, types

    if not CONFIG_PATH.exists():
        raise RuntimeError(
            "لم أجد إعداد اضغطها في Google Drive. شغّل Smart_Compressor مرة واحدة أولًا."
        )
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    required = ("api_id", "api_hash", "phone", "workspace_id")
    if not all(cfg.get(k) for k in required):
        raise RuntimeError("إعداد Telegram غير مكتمل في Google Drive.")

    local_base = "/content/media_lite_benchmark_session"
    local_file = Path(local_base + ".session")
    if SESSION_FILE.exists():
        shutil.copy2(SESSION_FILE, local_file)

    client = TelegramClient(
        local_base,
        int(cfg["api_id"]),
        cfg["api_hash"],
        connection_retries=5,
        request_retries=5,
    )
    try:
        await client.start(
            phone=cfg["phone"],
            code_callback=lambda: getpass.getpass(
                "كود Telegram للبنش مارك (لن يظهر): "
            ).strip(),
            password=lambda: getpass.getpass(
                "كلمة مرور التحقق بخطوتين (لن تظهر): "
            ).strip(),
        )
        channel = await client.get_entity(types.PeerChannel(int(cfg["workspace_id"])))

        msg = None
        if str(message_id).strip():
            msg = await client.get_messages(channel, ids=int(str(message_id).strip()))
            if not msg:
                raise RuntimeError("لم أجد رسالة Telegram بالـID المحدد.")
        else:
            messages = await client.get_messages(channel, limit=300)
            for item in messages:
                mime = str(getattr(getattr(item, "file", None), "mime_type", "") or "")
                if mime.startswith("video/") and not generated_output(item):
                    msg = item
                    break

        if not msg:
            raise RuntimeError("لم أجد فيديو أصلي مناسب في القناة.")

        mime = str(getattr(getattr(msg, "file", None), "mime_type", "") or "")
        if not mime.startswith("video/"):
            raise RuntimeError("الرسالة المختارة ليست فيديو.")

        name = (
            getattr(getattr(msg, "file", None), "name", None)
            or f"telegram_{msg.id}.mp4"
        )
        source = WORK_DIR / ("source_" + re.sub(r"[^A-Za-z0-9._ -]+", "_", name))
        print(f"⬇️ تنزيل عينة المصدر مرة واحدة فقط: {name}")

        try:
            from aiofasttelethonhelper import fast_download
            downloaded = await fast_download(
                client=client,
                message=msg,
                file_path=str(source),
            )
            source = Path(downloaded)
        except Exception:
            downloaded = await client.download_media(msg, file=str(source))
            source = Path(downloaded)

        return source, {
            "source_mode": "telegram",
            "telegram_message_id": int(msg.id),
            "source_name": name,
        }
    finally:
        await client.disconnect()


async def resolve_source(source_mode, drive_file, message_id):
    mode = str(source_mode or "telegram").lower()
    if mode == "drive":
        path = Path(str(drive_file or "").strip())
        if not path.exists():
            raise RuntimeError(f"ملف Drive غير موجود: {path}")
        return path, {
            "source_mode": "drive",
            "source_name": path.name,
            "drive_file": str(path),
        }
    return await telegram_source(message_id)


def sample_segments(duration, mode):
    mode = str(mode or "full").lower()
    each = {"quick": 8.0, "full": 15.0, "max": 20.0}.get(mode, 15.0)
    if duration <= each * 1.4:
        return [(0.0, max(2.0, min(duration, each)))]

    fractions = [0.18, 0.50, 0.82]
    segments = []
    for frac in fractions:
        start = max(0.0, duration * frac - each / 2)
        start = min(start, max(0.0, duration - each))
        segments.append((start, min(each, duration - start)))

    unique = []
    for item in segments:
        if not any(abs(item[0] - old[0]) < 1.0 for old in unique):
            unique.append(item)
    return unique


def candidate_specs(info: VideoInfo, hw, mode, target_mb):
    target_w, target_h = fit_dims(info.width, info.height)
    base_fps = min(info.fps if info.fps > 0 else 15.0, 15.0)
    specs = [
        {
            "id": "cpu_x264_veryfast",
            "label": "CPU x264 veryfast — الحالي",
            "group": "CPU preset",
            "backend": "ffmpeg_cpu",
            "preset": "veryfast",
            "crf": 29,
            "fps": base_fps,
            "scale": "default",
            "notes": "خط الأساس الحالي",
        },
        {
            "id": "cpu_x264_superfast",
            "label": "CPU x264 superfast",
            "group": "CPU preset",
            "backend": "ffmpeg_cpu",
            "preset": "superfast",
            "crf": 29,
            "fps": base_fps,
            "scale": "default",
            "notes": "أسرع من veryfast مع احتمال كفاءة ضغط أقل",
        },
        {
            "id": "cpu_x264_ultrafast",
            "label": "CPU x264 ultrafast",
            "group": "CPU preset",
            "backend": "ffmpeg_cpu",
            "preset": "ultrafast",
            "crf": 29,
            "fps": base_fps,
            "scale": "default",
            "notes": "أقصى سرعة CPU تقريبًا",
        },
        {
            "id": "cpu_x264_faster",
            "label": "CPU x264 faster",
            "group": "CPU preset",
            "backend": "ffmpeg_cpu",
            "preset": "faster",
            "crf": 29,
            "fps": base_fps,
            "scale": "default",
            "notes": "مرجع أبطأ/أعلى كفاءة ضغط",
        },
        {
            "id": "cpu_fast_bilinear",
            "label": "CPU veryfast + fast_bilinear",
            "group": "Scaler",
            "backend": "ffmpeg_cpu",
            "preset": "veryfast",
            "crf": 29,
            "fps": base_fps,
            "scale": "fast_bilinear",
            "notes": "اختبار scaler أسرع",
        },
        {
            "id": "cpu_no_scale_if_native",
            "label": "CPU veryfast بدون resize",
            "group": "Scaler",
            "backend": "ffmpeg_cpu",
            "preset": "veryfast",
            "crf": 29,
            "fps": base_fps,
            "scale": "none",
            "only_if_native": True,
            "notes": "يعمل فقط لو المصدر أصلاً ≤720p",
        },
        {
            "id": "cpu_fps12",
            "label": "CPU veryfast — 12 fps",
            "group": "Smart FPS",
            "backend": "ffmpeg_cpu",
            "preset": "veryfast",
            "crf": 29,
            "fps": min(info.fps or 12.0, 12.0),
            "scale": "default",
            "notes": "للشرائح والحركة الخفيفة",
        },
        {
            "id": "cpu_fps10",
            "label": "CPU veryfast — 10 fps",
            "group": "Smart FPS",
            "backend": "ffmpeg_cpu",
            "preset": "veryfast",
            "crf": 29,
            "fps": min(info.fps or 10.0, 10.0),
            "scale": "default",
            "notes": "اختبار تقليل فريمات أقوى",
        },
        {
            "id": "cpu_fps8",
            "label": "CPU veryfast — 8 fps",
            "group": "Smart FPS",
            "backend": "ffmpeg_cpu",
            "preset": "veryfast",
            "crf": 29,
            "fps": min(info.fps or 8.0, 8.0),
            "scale": "default",
            "notes": "للمحتوى شبه الثابت فقط",
        },
        {
            "id": "cpu_mpdecimate",
            "label": "CPU veryfast + mpdecimate",
            "group": "Duplicate frames",
            "backend": "ffmpeg_cpu",
            "preset": "veryfast",
            "crf": 29,
            "fps": base_fps,
            "scale": "default",
            "mpdecimate": True,
            "notes": "VFR — حذف الفريمات شبه المكررة",
        },
        {
            "id": "cpu_target_1pass",
            "label": f"Target {target_mb}MB — one-pass",
            "group": "Target size",
            "backend": "ffmpeg_target_1pass",
            "fps": base_fps,
            "scale": "default",
            "target_mb": int(target_mb),
            "notes": "بديل سريع للـ2-pass",
        },
        {
            "id": "cpu_target_2pass",
            "label": f"Target {target_mb}MB — two-pass",
            "group": "Target size",
            "backend": "ffmpeg_target_2pass",
            "fps": base_fps,
            "scale": "default",
            "target_mb": int(target_mb),
            "notes": "المرجع الحالي للدقة في الحجم",
        },
    ]

    if hw.get("gpu"):
        specs.append({
            "id": "gpu_nvenc_cpu_filters_p4",
            "label": "FFmpeg NVENC p4 + CPU filters",
            "group": "GPU pipeline",
            "backend": "ffmpeg_nvenc_cpu",
            "preset": "p4",
            "cq": 30,
            "fps": base_fps,
            "scale": "default",
            "notes": "مسار GPU التقليدي للمقارنة",
        })
        if True:
            for preset in ("p4", "p3", "p2", "p1"):
                specs.append({
                    "id": f"gpu_full_{preset}",
                    "label": f"FFmpeg Full GPU NVDEC→CUDA→NVENC {preset}",
                    "group": "Full GPU",
                    "backend": "ffmpeg_nvenc_full",
                    "preset": preset,
                    "cq": 30,
                    "fps": base_fps,
                    "scale": "cuda",
                    "notes": "Zero/low-copy GPU path",
                })
            specs.append({
                "id": "gpu_full_no_scale_p3",
                "label": "Full GPU p3 بدون resize",
                "group": "Full GPU",
                "backend": "ffmpeg_nvenc_full",
                "preset": "p3",
                "cq": 30,
                "fps": base_fps,
                "scale": "none",
                "only_if_native": True,
                "notes": "اختبار تخطي resize بالكامل",
            })

    if str(mode).lower() == "quick":
        keep = {
            "cpu_x264_veryfast",
            "cpu_x264_superfast",
            "cpu_x264_ultrafast",
            "cpu_fps12",
            "gpu_nvenc_cpu_filters_p4",
            "gpu_full_p4",
            "gpu_full_p3",
            "gpu_full_p1",
        }
        specs = [x for x in specs if x["id"] in keep]

    return specs


def should_skip(spec, info, hw, nvencc_path):
    native_fits = info.width <= 1280 and info.height <= 720
    if spec.get("only_if_native") and not native_fits:
        return "المصدر أكبر من 720p؛ تخطي no-resize غير عادل."

    backend = spec["backend"]
    if backend == "ffmpeg_nvenc_cpu":
        if not hw.get("gpu"):
            return "لا يوجد NVIDIA GPU في الـRuntime."
        if not hw.get("ffmpeg_nvenc_smoke_ok"):
            reason = hw.get("ffmpeg_nvenc_smoke_reason") or "FFmpeg NVENC smoke test فشل."
            return f"FFmpeg NVENC غير صالح: {reason[:260]}"

    if backend == "ffmpeg_nvenc_full":
        if not hw.get("gpu"):
            return "لا يوجد NVIDIA GPU في الـRuntime."
        if not hw.get("source_full_gpu_ok"):
            reason = hw.get("source_full_gpu_reason") or "Full GPU source probe فشل."
            return f"Full GPU pipeline غير صالح: {reason[:260]}"

    if backend == "nvencc":
        if not hw.get("gpu"):
            return "لا يوجد NVIDIA GPU في الـRuntime."
        if not nvencc_path:
            reason = hw.get("nvencc_hw_reason") or "NVEncC غير مثبت."
            return f"NVEncC غير متاح: {reason[:260]}"
        if hw.get("nvencc_hw_ok") is False:
            reason = hw.get("nvencc_hw_reason") or "NVEncC hardware check فشل."
            return f"NVEncC hardware check فشل: {reason[:260]}"

    return None



def cpu_filter(info, spec):
    target_w, target_h = fit_dims(info.width, info.height)
    filters = []
    if spec.get("scale") != "none":
        flags = ":flags=fast_bilinear" if spec.get("scale") == "fast_bilinear" else ""
        filters.append(f"scale={target_w}:{target_h}{flags}")
    if spec.get("mpdecimate"):
        filters.append("mpdecimate")
    else:
        fps = float(spec.get("fps") or 15)
        filters.append(f"fps={fps:.3f}")
    return ",".join(filters)


def target_video_kbps(info, target_mb):
    audio_k = 48 if info.has_audio else 0
    total_k = (float(target_mb) * 1024 * 1024 * 8 / max(info.duration, 1)) / 1000
    return max(80, int(total_k * 0.96 - audio_k))


def ffmpeg_cmd(spec, src, out, start, dur, info, pass_num=None, passlog=None):
    base = [
        "ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
    ]
    backend = spec["backend"]

    if backend == "ffmpeg_nvenc_full":
        target_w, target_h = fit_dims(info.width, info.height)
        cmd = base + [
            "-hwaccel", "cuda",
            "-hwaccel_output_format", "cuda",
            "-ss", f"{start:.3f}",
            "-i", str(src),
            "-t", f"{dur:.3f}",
        ]
        if spec.get("scale") != "none":
            cmd += ["-vf", f"scale_cuda={target_w}:{target_h}"]
        cmd += [
            "-r", f"{float(spec.get('fps') or 15):.3f}",
            "-fps_mode", "cfr",
            "-an",
            "-c:v", "h264_nvenc",
            "-preset", spec["preset"],
            "-rc", "vbr",
            "-cq", str(spec.get("cq", 30)),
            "-b:v", "0",
            "-movflags", "+faststart",
            str(out),
        ]
        return cmd

    cmd = base + [
        "-ss", f"{start:.3f}",
        "-i", str(src),
        "-t", f"{dur:.3f}",
    ]
    vf = cpu_filter(info, spec)
    if vf:
        cmd += ["-vf", vf]
    if spec.get("mpdecimate"):
        cmd += ["-fps_mode", "vfr"]

    cmd += ["-an"]

    if backend == "ffmpeg_cpu":
        cmd += [
            "-c:v", "libx264",
            "-preset", spec.get("preset", "veryfast"),
            "-crf", str(spec.get("crf", 29)),
            "-pix_fmt", "yuv420p",
        ]
    elif backend == "ffmpeg_nvenc_cpu":
        cmd += [
            "-c:v", "h264_nvenc",
            "-preset", spec.get("preset", "p4"),
            "-rc", "vbr",
            "-cq", str(spec.get("cq", 30)),
            "-b:v", "0",
            "-pix_fmt", "yuv420p",
        ]
    elif backend in ("ffmpeg_target_1pass", "ffmpeg_target_2pass"):
        video_k = target_video_kbps(info, spec["target_mb"])
        cmd += [
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-b:v", f"{video_k}k",
        ]
        if backend == "ffmpeg_target_1pass":
            cmd += [
                "-maxrate", f"{int(video_k * 1.25)}k",
                "-bufsize", f"{int(video_k * 2)}k",
            ]
        else:
            cmd += [
                "-pass", str(pass_num),
                "-passlogfile", str(passlog),
            ]
            if pass_num == 1:
                cmd += ["-f", "mp4", os.devnull]
                return cmd
        cmd += ["-pix_fmt", "yuv420p"]
    else:
        raise ValueError(f"Unsupported backend: {backend}")

    cmd += ["-movflags", "+faststart", str(out)]
    return cmd


def sample_gpu_stats():
    if not shutil.which("nvidia-smi"):
        return None
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,utilization.encoder,memory.used,clocks.sm",
                "--format=csv,noheader,nounits",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        parts = [x.strip() for x in proc.stdout.strip().splitlines()[0].split(",")]
        def num(value):
            value = value.replace("%", "").strip()
            if value.lower() in {"n/a", "[n/a]", ""}:
                return None
            try:
                return float(value)
            except Exception:
                return None
        return {
            "gpu_pct": num(parts[0]) if len(parts) > 0 else None,
            "encoder_pct": num(parts[1]) if len(parts) > 1 else None,
            "vram_mb": num(parts[2]) if len(parts) > 2 else None,
            "clock_mhz": num(parts[3]) if len(parts) > 3 else None,
        }
    except Exception:
        return None


def run_process_monitored(cmd, monitor_gpu=False):
    import psutil

    started = time.time()
    cpu_samples = []
    gpu_samples = []
    psutil.cpu_percent(interval=None)
    last_gpu = 0.0

    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as log:
        proc = subprocess.Popen(
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )

        while proc.poll() is None:
            time.sleep(0.20)
            try:
                cpu_samples.append(float(psutil.cpu_percent(interval=None)))
            except Exception:
                pass
            now = time.time()
            if monitor_gpu and now - last_gpu >= 0.55:
                sample = sample_gpu_stats()
                if sample:
                    gpu_samples.append(sample)
                last_gpu = now

        proc.wait()
        elapsed = time.time() - started
        log.seek(0)
        output = log.read()

    if proc.returncode != 0:
        raise RuntimeError((output or "unknown error")[-2500:])

    def avg(values):
        values = [v for v in values if v is not None]
        return (sum(values) / len(values)) if values else None

    result = {
        "elapsed": elapsed,
        "cpu_avg_pct": avg(cpu_samples),
        "cpu_peak_pct": max(cpu_samples) if cpu_samples else None,
        "gpu_avg_pct": avg([s.get("gpu_pct") for s in gpu_samples]),
        "gpu_encoder_avg_pct": avg([s.get("encoder_pct") for s in gpu_samples]),
        "gpu_vram_peak_mb": max(
            [s.get("vram_mb") for s in gpu_samples if s.get("vram_mb") is not None],
            default=None,
        ),
        "gpu_clock_avg_mhz": avg([s.get("clock_mhz") for s in gpu_samples]),
    }
    return result


def run_process_timed(cmd):
    return run_process_monitored(cmd, monitor_gpu=False)["elapsed"]



def output_fps(path):
    try:
        info = probe_video(path)
        return info.fps if info.fps > 0 else 15.0
    except Exception:
        return 15.0


def compute_ssim(src, start, dur, encoded):
    try:
        out_info = probe_video(encoded)
        fps = max(1.0, min(out_info.fps or 15.0, 60.0))
        w, h = out_info.width, out_info.height
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "info",
            "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
            "-i", str(encoded),
            "-filter_complex",
            (
                f"[0:v]scale={w}:{h}:flags=bicubic,fps={fps:.6f},"
                "format=yuv420p,setpts=PTS-STARTPTS[ref];"
                f"[1:v]fps={fps:.6f},format=yuv420p,setpts=PTS-STARTPTS[dist];"
                "[ref][dist]ssim"
            ),
            "-an", "-f", "null", "-"
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        text = proc.stderr
        matches = re.findall(r"All:([0-9.]+)", text)
        return float(matches[-1]) if matches else None
    except Exception:
        return None


def compute_vmaf(src, start, dur, encoded):
    try:
        out_info = probe_video(encoded)
        fps = max(1.0, min(out_info.fps or 15.0, 60.0))
        w, h = out_info.width, out_info.height
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "info",
            "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
            "-i", str(encoded),
            "-filter_complex",
            (
                f"[0:v]scale={w}:{h}:flags=bicubic,fps={fps:.6f},"
                "format=yuv420p,setpts=PTS-STARTPTS[ref];"
                f"[1:v]fps={fps:.6f},format=yuv420p,setpts=PTS-STARTPTS[dist];"
                "[dist][ref]libvmaf"
            ),
            "-an", "-f", "null", "-"
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        text = proc.stderr
        matches = re.findall(r"VMAF score:\s*([0-9.]+)", text)
        return float(matches[-1]) if matches else None
    except Exception:
        return None


def install_nvencc(enabled, hw):
    if not enabled or not hw.get("gpu"):
        return None

    existing = shutil.which("NVEncC") or shutil.which("nvencc")
    if existing:
        hw["nvencc_installed"] = True
        probe = probe_command([existing, "--check-hw", "0"], timeout=30)
        hw["nvencc_hw_ok"] = bool(probe["ok"])
        hw["nvencc_hw_reason"] = probe["reason"]
        return existing if probe["ok"] else existing

    print(f"📦 تثبيت NVEncC {NVENCC_VERSION} للبنش مارك فقط...")
    deb = WORK_DIR / f"nvencc_{NVENCC_VERSION}_amd64.deb"
    try:
        urllib.request.urlretrieve(NVENCC_URL, deb)
        subprocess.run(
            ["apt-get", "-qq", "update"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["apt-get", "-y", "install", str(deb)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        path = shutil.which("NVEncC") or shutil.which("nvencc")
        hw["nvencc_installed"] = bool(path)
        if path:
            probe = probe_command([path, "--check-hw", "0"], timeout=30)
            hw["nvencc_hw_ok"] = bool(probe["ok"])
            hw["nvencc_hw_reason"] = probe["reason"]
        else:
            hw["nvencc_hw_ok"] = False
            hw["nvencc_hw_reason"] = "تم تثبيت الحزمة لكن لم يتم العثور على NVEncC في PATH."
        return path
    except Exception as exc:
        hw["nvencc_installed"] = False
        hw["nvencc_hw_ok"] = False
        hw["nvencc_hw_reason"] = f"{type(exc).__name__}: {str(exc)[:1000]}"
        print(f"↪️ NVEncC لم يثبت: {type(exc).__name__}")
        return None



def nvencc_specs(info, hw, nvencc_path, mode):
    if not hw.get("gpu"):
        return []
    base_fps = min(info.fps if info.fps > 0 else 15.0, 15.0)
    presets = ("p4", "p3", "p2", "p1")
    if str(mode).lower() == "quick":
        presets = ("p3", "p1")
    return [
        {
            "id": f"nvencc_{p}",
            "label": f"NVEncC Full GPU {p}",
            "group": "NVEncC",
            "backend": "nvencc",
            "preset": p,
            "qvbr": 30,
            "fps": base_fps,
            "notes": "NVEncC + avhw + GPU resize",
        }
        for p in presets
    ]



def nvencc_cmd(spec, nvencc_path, src, out, start, dur, info):
    target_w, target_h = fit_dims(info.width, info.height)
    return [
        nvencc_path,
        "--avhw",
        "--seek", f"{start:.3f}",
        "--seekto", f"{start + dur:.3f}",
        "-i", str(src),
        "-o", str(out),
        "-c", "h264",
        "-u", spec.get("preset", "p3"),
        "--qvbr", str(spec.get("qvbr", 30)),
        "--output-res", f"{target_w}x{target_h},preserve_aspect_ratio=decrease",
    ]


def merge_metrics(items):
    if not items:
        return {
            "elapsed": 0.0,
            "cpu_avg_pct": None,
            "cpu_peak_pct": None,
            "gpu_avg_pct": None,
            "gpu_encoder_avg_pct": None,
            "gpu_vram_peak_mb": None,
            "gpu_clock_avg_mhz": None,
        }

    def mean_key(key):
        vals = [m.get(key) for m in items if m.get(key) is not None]
        return (sum(vals) / len(vals)) if vals else None

    return {
        "elapsed": sum(float(m.get("elapsed") or 0.0) for m in items),
        "cpu_avg_pct": mean_key("cpu_avg_pct"),
        "cpu_peak_pct": max(
            [m.get("cpu_peak_pct") for m in items if m.get("cpu_peak_pct") is not None],
            default=None,
        ),
        "gpu_avg_pct": mean_key("gpu_avg_pct"),
        "gpu_encoder_avg_pct": mean_key("gpu_encoder_avg_pct"),
        "gpu_vram_peak_mb": max(
            [m.get("gpu_vram_peak_mb") for m in items if m.get("gpu_vram_peak_mb") is not None],
            default=None,
        ),
        "gpu_clock_avg_mhz": mean_key("gpu_clock_avg_mhz"),
    }


def run_candidate_segment(spec, src, info, start, dur, out, work_dir, nvencc_path):
    monitor_gpu = spec["backend"] in {"ffmpeg_nvenc_cpu", "ffmpeg_nvenc_full", "nvencc"}

    if spec["backend"] == "nvencc":
        cmd = nvencc_cmd(spec, nvencc_path, src, out, start, dur, info)
        return run_process_monitored(cmd, monitor_gpu=monitor_gpu)

    if spec["backend"] == "ffmpeg_target_2pass":
        passlog = work_dir / f"pass_{spec['id']}_{out.stem}"
        first = ffmpeg_cmd(spec, src, out, start, dur, info, pass_num=1, passlog=passlog)
        second = ffmpeg_cmd(spec, src, out, start, dur, info, pass_num=2, passlog=passlog)
        metrics = []
        try:
            metrics.append(run_process_monitored(first, monitor_gpu=False))
            metrics.append(run_process_monitored(second, monitor_gpu=False))
        finally:
            for p in work_dir.glob(passlog.name + "*"):
                try:
                    p.unlink()
                except Exception:
                    pass
        return merge_metrics(metrics)

    cmd = ffmpeg_cmd(spec, src, out, start, dur, info)
    return run_process_monitored(cmd, monitor_gpu=monitor_gpu)


def warmup_candidate(spec, src, info, segments, work_dir, nvencc_path):
    if spec["backend"] == "ffmpeg_target_2pass":
        return

    start, dur = segments[0]
    warm_dur = min(2.0, max(0.8, dur))
    out = work_dir / f"warmup_{spec['id']}.mp4"
    try:
        if out.exists():
            out.unlink()
        run_candidate_segment(
            spec,
            src,
            info,
            start,
            warm_dur,
            out,
            work_dir,
            nvencc_path,
        )
    finally:
        try:
            out.unlink()
        except Exception:
            pass


def run_single_candidate(
    spec,
    src,
    info,
    segments,
    work_dir,
    nvencc_path,
    keep_outputs,
    repeats=3,
    warmup=True,
):
    repeats = max(1, int(repeats or 1))
    total_sample = sum(float(d) for _, d in segments)

    if warmup:
        warmup_candidate(spec, src, info, segments, work_dir, nvencc_path)

    repeat_times = []
    repeat_bytes = []
    process_metrics = []
    ssims = []
    outputs = []

    for rep in range(repeats):
        rep_elapsed = 0.0
        rep_bytes = 0

        for idx, (start, dur) in enumerate(segments, 1):
            out = work_dir / f"{spec['id']}_r{rep + 1}_seg{idx}.mp4"
            if out.exists():
                out.unlink()

            metrics = run_candidate_segment(
                spec,
                src,
                info,
                start,
                dur,
                out,
                work_dir,
                nvencc_path,
            )
            process_metrics.append(metrics)

            if not out.exists() or out.stat().st_size <= 0:
                raise RuntimeError("لم يتم إنشاء ملف ناتج صالح.")

            rep_elapsed += float(metrics.get("elapsed") or 0.0)
            rep_bytes += out.stat().st_size

            if rep == 0:
                metric = compute_ssim(src, start, dur, out)
                if metric is not None:
                    ssims.append(metric)
                outputs.append((start, dur, out))
            else:
                try:
                    out.unlink()
                except Exception:
                    pass

        repeat_times.append(rep_elapsed)
        repeat_bytes.append(rep_bytes)

    median_encode = statistics.median(repeat_times)
    median_bytes = statistics.median(repeat_bytes)
    speed_x = total_sample / median_encode if median_encode > 0 else 0.0
    full_encode_s = info.duration / speed_x if speed_x > 0 else None

    video_full_bytes = (median_bytes / total_sample) * info.duration if total_sample > 0 else 0
    audio_full_bytes = (48_000 / 8) * info.duration if info.has_audio else 0
    estimated_mb = (video_full_bytes + audio_full_bytes) / (1024 ** 2)

    merged = merge_metrics(process_metrics)
    timing_cv = None
    if len(repeat_times) > 1:
        avg_t = statistics.mean(repeat_times)
        if avg_t > 0:
            timing_cv = statistics.pstdev(repeat_times) / avg_t * 100

    result = {
        "id": spec["id"],
        "candidate": spec["label"],
        "group": spec["group"],
        "backend": spec["backend"],
        "preset": spec.get("preset", ""),
        "fps": round(float(spec.get("fps") or 0), 2),
        "sample_seconds": round(total_sample, 2),
        "repeats": repeats,
        "encode_seconds": round(median_encode, 3),
        "repeat_encode_seconds": [round(x, 3) for x in repeat_times],
        "timing_cv_pct": round(timing_cv, 2) if timing_cv is not None else None,
        "speed_x": round(speed_x, 3),
        "estimated_full_encode_seconds": round(full_encode_s, 2) if full_encode_s else None,
        "estimated_full_encode": human_time(full_encode_s) if full_encode_s else None,
        "estimated_output_mb": round(estimated_mb, 2),
        "ssim": round(sum(ssims) / len(ssims), 6) if ssims else None,
        "vmaf": None,
        "cpu_avg_pct": round(merged["cpu_avg_pct"], 1) if merged["cpu_avg_pct"] is not None else None,
        "cpu_peak_pct": round(merged["cpu_peak_pct"], 1) if merged["cpu_peak_pct"] is not None else None,
        "gpu_avg_pct": round(merged["gpu_avg_pct"], 1) if merged["gpu_avg_pct"] is not None else None,
        "gpu_encoder_avg_pct": round(merged["gpu_encoder_avg_pct"], 1) if merged["gpu_encoder_avg_pct"] is not None else None,
        "gpu_vram_peak_mb": round(merged["gpu_vram_peak_mb"], 1) if merged["gpu_vram_peak_mb"] is not None else None,
        "gpu_clock_avg_mhz": round(merged["gpu_clock_avg_mhz"], 1) if merged["gpu_clock_avg_mhz"] is not None else None,
        "status": "ok",
        "notes": spec.get("notes", ""),
        "_outputs": outputs,
    }

    return result



def cleanup_result_outputs(result):
    for _start, _dur, path in result.pop("_outputs", []):
        try:
            Path(path).unlink()
        except Exception:
            pass


async def gpu_batch_throughput(src, info, hw, work_dir, jobs=2):
    if not hw.get("source_full_gpu_ok"):
        return None

    dur = min(20.0, max(6.0, info.duration * 0.1))
    start = max(0.0, min(info.duration * 0.5 - dur / 2, info.duration - dur))
    spec = {
        "id": f"gpu_batch_{jobs}x_p3",
        "label": f"Full GPU p3 — {jobs} encodes معًا",
        "group": "Batch throughput",
        "backend": "ffmpeg_nvenc_full",
        "preset": "p3",
        "cq": 30,
        "fps": min(info.fps if info.fps > 0 else 15.0, 15.0),
        "scale": "cuda",
        "notes": "يقيس زمن دفعة متعددة وليس زمن فيديو واحد",
    }

    procs = []
    outputs = []
    started = time.time()
    try:
        for i in range(jobs):
            out = work_dir / f"batch_{jobs}_{i}.mp4"
            outputs.append(out)
            cmd = ffmpeg_cmd(spec, src, out, start, dur, info)
            procs.append(subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            ))
        errors = []
        for p in procs:
            _out, err = p.communicate()
            if p.returncode != 0:
                errors.append((err or "")[-1000:])
        elapsed = time.time() - started
        if errors:
            raise RuntimeError(" | ".join(errors))
        aggregate_x = (dur * jobs) / elapsed if elapsed > 0 else 0
        return {
            "id": spec["id"],
            "candidate": spec["label"],
            "group": spec["group"],
            "backend": spec["backend"],
            "preset": "p3",
            "fps": round(spec["fps"], 2),
            "sample_seconds": round(dur * jobs, 2),
            "encode_seconds": round(elapsed, 3),
            "speed_x": round(aggregate_x, 3),
            "estimated_full_encode_seconds": None,
            "estimated_full_encode": None,
            "estimated_output_mb": None,
            "ssim": None,
            "vmaf": None,
            "status": "ok",
            "notes": spec["notes"],
        }
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
        for out in outputs:
            try:
                out.unlink()
            except Exception:
                pass


def pick_vmaf_shortlist(results):
    ok = [r for r in results if r.get("status") == "ok" and r.get("_outputs")]
    baseline = next((r for r in ok if r["id"] == "cpu_x264_veryfast"), None)
    chosen = []
    if baseline:
        chosen.append(baseline)
    for row in sorted(ok, key=lambda x: x.get("speed_x") or 0, reverse=True):
        if row not in chosen:
            chosen.append(row)
        if len(chosen) >= 5:
            break
    return chosen


def add_vmaf(results, src, hw):
    if not hw["libvmaf_available"]:
        return
    print("🎯 حساب VMAF لأسرع النتائج المختصرة...")
    for row in pick_vmaf_shortlist(results):
        values = []
        for start, dur, path in row.get("_outputs", []):
            score = compute_vmaf(src, start, dur, path)
            if score is not None:
                values.append(score)
        if values:
            row["vmaf"] = round(sum(values) / len(values), 3)


def enrich_relative_metrics(results):
    baseline = next(
        (r for r in results if r.get("id") == "cpu_x264_veryfast" and r.get("status") == "ok"),
        None,
    )
    if not baseline:
        return

    bspeed = baseline.get("speed_x") or 0
    bsize = baseline.get("estimated_output_mb") or 0
    bssim = baseline.get("ssim")
    bfps = baseline.get("fps") or 0

    for r in results:
        if r.get("status") != "ok":
            continue
        r["speedup_vs_current"] = round((r.get("speed_x") or 0) / bspeed, 3) if bspeed else None
        r["size_vs_current"] = round((r.get("estimated_output_mb") or 0) / bsize, 3) if bsize and r.get("estimated_output_mb") else None
        r["fps_ratio_vs_current"] = round((r.get("fps") or 0) / bfps, 3) if bfps and r.get("fps") else None
        if bssim is not None and r.get("ssim") is not None:
            r["ssim_delta"] = round(r["ssim"] - bssim, 6)
        else:
            r["ssim_delta"] = None


def recommendations(results):
    normal = [
        r for r in results
        if r.get("status") == "ok"
        and r.get("group") != "Batch throughput"
        and r.get("ssim") is not None
        and r.get("estimated_output_mb") is not None
    ]
    baseline = next((r for r in normal if r["id"] == "cpu_x264_veryfast"), None)
    if not baseline:
        return {}

    bssim = baseline["ssim"]
    bsize = baseline["estimated_output_mb"]
    bfps = baseline.get("fps") or 15.0

    general_safe = [
        r for r in normal
        if r.get("group") not in {"Smart FPS", "Target size", "Duplicate frames"}
        and (r.get("fps") or bfps) >= bfps * 0.95
        and r["ssim"] >= bssim - 0.010
        and r["estimated_output_mb"] <= bsize * 1.20
    ]
    general_safe.sort(key=lambda r: r["speed_x"], reverse=True)

    static_safe = [
        r for r in normal
        if r.get("group") == "Smart FPS"
        and r["ssim"] >= bssim - 0.010
        and r["estimated_output_mb"] <= bsize * 1.05
    ]
    static_safe.sort(key=lambda r: r["speed_x"], reverse=True)

    targets = [r for r in normal if r.get("group") == "Target size"]
    targets.sort(key=lambda r: r["speed_x"], reverse=True)

    quality = sorted(normal, key=lambda r: (r.get("ssim") or 0, r.get("speed_x") or 0), reverse=True)
    smallest = sorted(normal, key=lambda r: (r.get("estimated_output_mb") or 1e9, -(r.get("ssim") or 0)))

    fastest_general = general_safe[0] if general_safe else None
    return {
        "baseline": baseline,
        "fastest_safe": fastest_general,
        "fastest_general": fastest_general,
        "fastest_static": static_safe[0] if static_safe else None,
        "fastest_target": targets[0] if targets else None,
        "best_ssim": quality[0] if quality else None,
        "smallest": smallest[0] if smallest else None,
    }


def confirmation_shortlist(recs):
    chosen = []
    for key in ("baseline", "fastest_general", "fastest_static"):
        row = recs.get(key)
        if row and row.get("id") not in chosen:
            chosen.append(row["id"])
    return chosen[:3]


def confirm_candidates(results, spec_by_id, recs, src, info, work_dir, nvencc_path):
    ids = confirmation_shortlist(recs)
    if not ids:
        return

    dur = min(90.0, max(45.0, info.duration * 0.015))
    dur = min(dur, max(2.0, info.duration))
    start = max(0.0, min(info.duration * 0.5 - dur / 2, max(0.0, info.duration - dur)))
    segment = [(start, dur)]

    print()
    print(f"🔍 تأكيد أفضل النتائج على مقطع أطول: {dur:.0f} ثانية")

    rows = {r.get("id"): r for r in results}
    for cid in ids:
        spec = spec_by_id.get(cid)
        row = rows.get(cid)
        if not spec or not row or row.get("status") != "ok":
            continue
        try:
            confirm = run_single_candidate(
                spec,
                src,
                info,
                segment,
                work_dir,
                nvencc_path,
                keep_outputs=False,
                repeats=1,
                warmup=True,
            )
            row["confirm_speed_x"] = confirm.get("speed_x")
            row["confirm_estimated_full_encode"] = confirm.get("estimated_full_encode")
            row["confirm_estimated_output_mb"] = confirm.get("estimated_output_mb")
            row["confirm_ssim"] = confirm.get("ssim")
            row["confirm_cpu_avg_pct"] = confirm.get("cpu_avg_pct")
            row["confirm_gpu_avg_pct"] = confirm.get("gpu_avg_pct")
            row["confirm_gpu_encoder_avg_pct"] = confirm.get("gpu_encoder_avg_pct")
            cleanup_result_outputs(confirm)
            print(
                f"   ✅ {row['candidate']} — "
                f"{row['confirm_speed_x']:.2f}x • "
                f"{row['confirm_estimated_full_encode']}"
            )
        except Exception as exc:
            row["confirmation_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
            print(f"   ⚠️ تعذر تأكيد {row['candidate']}: {type(exc).__name__}")



def display_results(results, hw, source_info, recs):
    import pandas as pd
    from IPython.display import HTML, display

    clean = []
    for r in results:
        row = {k: v for k, v in r.items() if not k.startswith("_")}
        clean.append(row)
    df = pd.DataFrame(clean)

    desired = [
        "candidate", "group", "status", "speed_x", "speedup_vs_current",
        "estimated_full_encode", "estimated_output_mb", "size_vs_current",
        "ssim", "ssim_delta", "vmaf", "fps", "fps_ratio_vs_current",
        "repeats", "timing_cv_pct",
        "cpu_avg_pct", "gpu_avg_pct", "gpu_encoder_avg_pct", "gpu_vram_peak_mb",
        "confirm_speed_x", "confirm_estimated_full_encode", "confirm_ssim",
        "notes"
    ]
    cols = [name for name in desired if name in df.columns]
    if "status" in df.columns:
        df = df.sort_values(
            by=["status", "speed_x"],
            ascending=[True, False],
            na_position="last",
        )

    display(HTML(
        '<div dir="rtl" style="max-width:900px;padding:10px 14px;margin:10px 0;'
        'border-radius:10px;background:#eef6ff;font-family:Arial,sans-serif">'
        f'<b>📹 المصدر:</b> <bdi dir="ltr">{source_info.get("source_name","")}</bdi><br>'
        f'<b>المدة:</b> <bdi dir="ltr">{source_info.get("duration","")}</bdi> — '
        f'<b>الدقة:</b> <bdi dir="ltr">{source_info.get("resolution","")}</bdi> — '
        f'<b>FPS:</b> <bdi dir="ltr">{source_info.get("fps","")}</bdi>'
        '</div>'
    ))
    display(df[cols])

    general = recs.get("fastest_general")
    static = recs.get("fastest_static")

    if general:
        confirm = ""
        if general.get("confirm_speed_x"):
            confirm = (
                f'<br>تأكيد المقطع الأطول: '
                f'<bdi dir="ltr">{general["confirm_speed_x"]:.2f}x • '
                f'{general.get("confirm_estimated_full_encode","")}</bdi>'
            )
        display(HTML(
            '<div dir="rtl" style="max-width:900px;padding:14px 16px;margin:12px 0;'
            'border:1px solid #16a34a;border-radius:12px;background:#f0fdf4;'
            'font-family:Arial,sans-serif;line-height:1.8">'
            '<b>🏁 أسرع مرشح عام بدون تقليل FPS</b><br>'
            f'<b>{general["candidate"]}</b><br>'
            f'السرعة: <bdi dir="ltr">{general["speed_x"]:.2f}x</bdi> — '
            f'الزمن المتوقع: <bdi dir="ltr">{general["estimated_full_encode"]}</bdi><br>'
            f'الحجم المتوقع: <bdi dir="ltr">{general["estimated_output_mb"]:.1f} MB</bdi> — '
            f'SSIM: <bdi dir="ltr">{general["ssim"]:.5f}</bdi>'
            f'{confirm}'
            '</div>'
        ))

    if static and static.get("id") != (general or {}).get("id"):
        display(HTML(
            '<div dir="rtl" style="max-width:900px;padding:12px 16px;margin:10px 0;'
            'border:1px solid #f59e0b;border-radius:12px;background:#fffbeb;'
            'font-family:Arial,sans-serif;line-height:1.8">'
            '<b>📊 مرشح للمحتوى الثابت/الشرائح فقط</b><br>'
            f'<b>{static["candidate"]}</b> — '
            f'<bdi dir="ltr">{static["speed_x"]:.2f}x • {static["estimated_full_encode"]}</bdi><br>'
            'ده مش توصية عامة لأن تقليل FPS ممكن يؤثر على نعومة الحركة.'
            '</div>'
        ))



def write_report(report_dir, hardware, source_meta, video_info, segments, results, recs, config):
    import pandas as pd

    report_dir.mkdir(parents=True, exist_ok=True)
    clean_results = [
        {k: v for k, v in r.items() if not k.startswith("_")}
        for r in results
    ]

    payload = {
        "benchmark_version": BENCH_VERSION,
        "created_at": datetime.now().astimezone().isoformat(),
        "config": config,
        "hardware": hardware,
        "source": {
            **source_meta,
            **asdict(video_info),
        },
        "segments": [{"start": s, "duration": d} for s, d in segments],
        "results": clean_results,
        "recommendations": {
            k: ({kk: vv for kk, vv in v.items() if not kk.startswith("_")} if v else None)
            for k, v in recs.items()
        },
        "notes": {
            "quality_metric": "SSIM is spatial quality only. Lower-FPS candidates are reported separately because SSIM alone does not measure motion smoothness fairly.",
            "timing": "Candidates use a warm-up plus repeated timed runs; speed is based on the median repeat.",
            "confirmation": "The baseline and best general/static candidates are rechecked on a longer segment in full/max modes.",
            "gpu": "FFmpeg NVENC, source NVDEC, full CUDA pipeline and NVEncC are diagnosed independently; failures stay visible with their reasons.",
            "audio": "Video encoder benchmark excludes audio encode. Estimated full output adds 48 kbps audio when the source has audio.",
            "safety": "Benchmark notebook never edits the Telegram channel and never uploads benchmark outputs.",
            "sdk_probe": (
                "PyNvVideoCodec detected"
                if hardware.get("pynvvideocodec_available")
                else "PyNvVideoCodec not installed; direct SDK path remains a future experiment and is not ranked."
            ),
        },
    }

    (report_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    pd.DataFrame(clean_results).to_csv(report_dir / "results.csv", index=False)

    lines = [
        "# Media Lite Encoder Benchmark",
        "",
        f"- Version: {BENCH_VERSION}",
        f"- Runtime: {hardware['runtime_kind']}",
        f"- CPU: {hardware['cpu_model']}",
        f"- Physical cores: {hardware['cpu_physical_cores']}",
        f"- Logical threads: {hardware['cpu_logical_threads']}",
        f"- GPU: {(hardware['gpu'] or {}).get('name', 'None')}",
        f"- FFmpeg h264_nvenc listed: {hardware.get('ffmpeg_nvenc_encoder_listed')}",
        f"- FFmpeg NVENC smoke: {hardware.get('ffmpeg_nvenc_smoke_ok')}",
        f"- Source NVDEC: {hardware.get('source_nvdec_ok')}",
        f"- Full GPU pipeline: {hardware.get('source_full_gpu_ok')}",
        f"- NVEncC hardware check: {hardware.get('nvencc_hw_ok')}",
        f"- Source: {source_meta.get('source_name', '')}",
        f"- Duration: {video_info.duration:.2f}s",
        f"- Resolution: {video_info.width}x{video_info.height}",
        f"- FPS: {video_info.fps:.3f}",
        "",
        "## Results",
        "",
        pd.DataFrame(clean_results).to_markdown(index=False),
        "",
        "## Recommendation",
        "",
    ]

    general = recs.get("fastest_general")
    static = recs.get("fastest_static")
    target = recs.get("fastest_target")

    if general:
        lines += [
            f"- Fastest general candidate without lowering FPS: **{general['candidate']}**",
            f"- Speed: {general['speed_x']:.2f}x",
            f"- Estimated full encode: {general['estimated_full_encode']}",
            f"- Estimated output: {general['estimated_output_mb']:.1f} MB",
            f"- SSIM: {general['ssim']:.6f}",
        ]
        if general.get("confirm_speed_x"):
            lines += [
                f"- Longer confirmation speed: {general['confirm_speed_x']:.2f}x",
                f"- Longer confirmation estimate: {general.get('confirm_estimated_full_encode')}",
            ]
    else:
        lines.append("- No conservative general candidate could be selected automatically.")

    if static:
        lines += [
            "",
            f"- Static/slide-content candidate only: **{static['candidate']}**",
            f"- Speed: {static['speed_x']:.2f}x",
            "- Warning: reduced FPS is not treated as a general-quality win.",
        ]

    if target:
        lines += [
            "",
            f"- Fastest target-size candidate: **{target['candidate']}**",
            f"- Speed: {target['speed_x']:.2f}x",
        ]

    (report_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return payload




async def benchmark_main():
    ensure_dependencies()
    mount_drive()
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    source_mode = os.environ.get("BENCH_SOURCE_MODE", "telegram")
    drive_file = os.environ.get("BENCH_DRIVE_FILE", "")
    message_id = os.environ.get("BENCH_TELEGRAM_MESSAGE_ID", "")
    mode = os.environ.get("BENCH_MODE", "full").lower()
    target_mb = int(os.environ.get("BENCH_TARGET_MB", "80") or 80)
    install_nvencc_flag = os.environ.get("BENCH_INSTALL_NVENCC", "1") == "1"
    keep_outputs = os.environ.get("BENCH_KEEP_OUTPUTS", "0") == "1"
    default_repeats = 2 if mode == "quick" else 3
    repeats = max(1, min(5, int(os.environ.get("BENCH_REPEATS", str(default_repeats)) or default_repeats)))

    hw = hardware_info()
    show_hardware(hw)

    print()
    print("📥 تجهيز فيديو المصدر...")
    src, source_meta = await resolve_source(source_mode, drive_file, message_id)
    info = probe_video(src)
    source_meta.update({
        "duration": human_time(info.duration),
        "resolution": f"{info.width}x{info.height}",
        "fps": round(info.fps, 3),
        "codec": info.codec,
    })

    print("🧰 تشخيص مسار GPU على نفس فيديو المصدر...")
    diagnose_source_gpu(src, info, hw)

    # Important: NVEncC is tested independently from FFmpeg NVENC.
    nvencc_path = install_nvencc(install_nvencc_flag, hw)
    show_gpu_diagnostics(hw)

    segments = sample_segments(info.duration, mode)
    sample_total = sum(d for _, d in segments)
    print(
        f"🧪 العينة: {len(segments)} مقاطع بإجمالي {sample_total:.0f} ثانية "
        f"من فيديو مدته {human_time(info.duration)}."
    )
    print(
        f"🔁 كل مرشح: Warm-up ثم {repeats} قياسات؛ النتيجة = Median. "
        "ترتيب الاختبارات يتغير لتقليل تحيز حرارة/ضغط المعالج."
    )
    print("ℹ️ البنش يقيس الفيديو فقط؛ لا رفع إلى Telegram ولا تعديل للقناة.")

    specs = candidate_specs(info, hw, mode, target_mb)
    specs.extend(nvencc_specs(info, hw, nvencc_path, mode))
    spec_by_id = {spec["id"]: spec for spec in specs}

    seed = 20260921 + int(info.size % 100000)
    random.Random(seed).shuffle(specs)

    results = []
    test_dir = WORK_DIR / "outputs"
    test_dir.mkdir(parents=True, exist_ok=True)

    for idx, spec in enumerate(specs, 1):
        reason = should_skip(spec, info, hw, nvencc_path)
        if reason:
            results.append({
                "id": spec["id"],
                "candidate": spec["label"],
                "group": spec["group"],
                "backend": spec["backend"],
                "preset": spec.get("preset", ""),
                "fps": spec.get("fps"),
                "execution_order": idx,
                "repeats": repeats,
                "sample_seconds": None,
                "encode_seconds": None,
                "speed_x": None,
                "estimated_full_encode_seconds": None,
                "estimated_full_encode": None,
                "estimated_output_mb": None,
                "ssim": None,
                "vmaf": None,
                "status": "skipped",
                "notes": reason,
            })
            print(f"[{idx}/{len(specs)}] ➖ {spec['label']} — {reason[:160]}")
            continue

        print()
        print(f"[{idx}/{len(specs)}] ▶ {spec['label']}")
        try:
            row = run_single_candidate(
                spec,
                src,
                info,
                segments,
                test_dir,
                nvencc_path,
                keep_outputs,
                repeats=repeats,
                warmup=True,
            )
            row["execution_order"] = idx
            results.append(row)
            util = ""
            if row.get("gpu_encoder_avg_pct") is not None:
                util = f" • NVENC {row['gpu_encoder_avg_pct']:.0f}%"
            elif row.get("cpu_avg_pct") is not None:
                util = f" • CPU {row['cpu_avg_pct']:.0f}%"
            print(
                f"   ✅ Median {row['speed_x']:.2f}x • "
                f"{row['estimated_full_encode']} للفيديو الكامل • "
                f"CV {row.get('timing_cv_pct') if row.get('timing_cv_pct') is not None else 'N/A'}%"
                f"{util}"
            )
        except Exception as exc:
            results.append({
                "id": spec["id"],
                "candidate": spec["label"],
                "group": spec["group"],
                "backend": spec["backend"],
                "preset": spec.get("preset", ""),
                "fps": spec.get("fps"),
                "execution_order": idx,
                "repeats": repeats,
                "sample_seconds": None,
                "encode_seconds": None,
                "speed_x": None,
                "estimated_full_encode_seconds": None,
                "estimated_full_encode": None,
                "estimated_output_mb": None,
                "ssim": None,
                "vmaf": None,
                "status": "failed",
                "notes": f"{type(exc).__name__}: {str(exc)[:500]}",
            })
            print(f"   ⚠️ FAILED: {type(exc).__name__}: {str(exc)[:180]}")

    if hw["libvmaf_available"]:
        add_vmaf(results, src, hw)

    if mode in ("full", "max") and hw.get("source_full_gpu_ok"):
        for jobs in ((2, 3) if mode == "max" else (2,)):
            print(f"⚡ اختبار Throughput لعدد {jobs} encode بالتوازي...")
            try:
                row = await gpu_batch_throughput(src, info, hw, test_dir, jobs=jobs)
                if row:
                    row["execution_order"] = len(results) + 1
                    results.append(row)
            except Exception as exc:
                results.append({
                    "id": f"gpu_batch_{jobs}x_p3",
                    "candidate": f"Full GPU p3 — {jobs} encodes معًا",
                    "group": "Batch throughput",
                    "backend": "ffmpeg_nvenc_full",
                    "preset": "p3",
                    "fps": None,
                    "execution_order": len(results) + 1,
                    "sample_seconds": None,
                    "encode_seconds": None,
                    "speed_x": None,
                    "estimated_full_encode_seconds": None,
                    "estimated_full_encode": None,
                    "estimated_output_mb": None,
                    "ssim": None,
                    "vmaf": None,
                    "status": "failed",
                    "notes": f"{type(exc).__name__}: {str(exc)[:500]}",
                })

    enrich_relative_metrics(results)
    recs = recommendations(results)

    if mode in ("full", "max"):
        confirm_candidates(
            results,
            spec_by_id,
            recs,
            src,
            info,
            test_dir,
            nvencc_path,
        )
        recs = recommendations(results)

    run_stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    report_dir = REPORT_ROOT / run_stamp
    config = {
        "mode": mode,
        "target_mb": target_mb,
        "install_nvencc": install_nvencc_flag,
        "keep_outputs": keep_outputs,
        "sample_total_seconds": sample_total,
        "repeats": repeats,
        "warmup": True,
        "random_seed": seed,
        "confirmation_enabled": mode in ("full", "max"),
    }
    write_report(report_dir, hw, source_meta, info, segments, results, recs, config)
    display_results(results, hw, source_meta, recs)

    if not keep_outputs:
        for row in results:
            cleanup_result_outputs(row)

    print()
    print(f"💾 التقرير الكامل: {report_dir}")
    print("✅ البنش انتهى. لم يتم تعديل القناة أو محرك الضغط الأساسي.")
    return {
        "hardware": hw,
        "source": source_meta,
        "report_dir": str(report_dir),
        "recommendations": recs,
    }



def run_benchmark():
    import nest_asyncio
    nest_asyncio.apply()

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        return loop.run_until_complete(benchmark_main())
    return asyncio.run(benchmark_main())
