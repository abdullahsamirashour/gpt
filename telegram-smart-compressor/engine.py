# -*- coding: utf-8 -*-
"""
Telegram Smart Compressor
Readable Colab engine. No embedded payloads and no user secrets in this file.
"""
from __future__ import annotations

import asyncio
import getpass
import html
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

ENGINE_BUNDLE_VERSION = "5.4.0-beta"
APP_NAME = "اضغطها | Media Lite"
WORKSPACE_TITLE = "🗜️ اضغطها | Media Lite"
WORKSPACE_ALIASES = {
    WORKSPACE_TITLE,
    "📦 ضغط الفيديو والصوت",
    "🎓 ضغط المحاضرات",
    "📦 Smart Compressor",
    "Smart Compressor",
}
BRANDING_VERSION = "media-lite-v1"
BRAND_REPO = "abdullahsamirashour/gpt"
BRAND_ASSET_DIR = "telegram-smart-compressor/assets"
BRAND_LOGO_FILE = "media_lite_logo.jpg"
BRAND_BANNER_FILE = "media_lite_banner.jpg"
CHANNEL_ABOUT = "اضغطها | Media Lite — ضغط ذكي للفيديو والصوت عبر Google Colab."
COLAB_URL = "https://colab.research.google.com/github/abdullahsamirashour/gpt/blob/main/telegram-smart-compressor/Smart_Compressor.ipynb"
TURBO_THRESHOLD = 8 * 1024 * 1024
TURBO_CONNECTIONS = 4
TURBO_PART_KB = 512
BASE_DIR = Path("/content/drive/MyDrive/Telegram_Extreme_Compressor")
TMP_DIR = Path("/content/telegram_smart_compressor_tmp")
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state_v5.json"
OLD_STATE_PATH = BASE_DIR / "processed_v4.json"
ERROR_LOG = BASE_DIR / "last_error.log"
SESSION_PATH = BASE_DIR / "telegram_user"
SCAN_LIMIT = 3000

PROFILE_FROM_UI = {
    "تلقائي — مناسب لمعظم الاستخدامات": "SMART_AUTO",
    "صوت صغير جدًا": "AUDIO_TINY",
    "فيديو متوازن": "VIDEO_BALANCED",
    "فيديو سريع": "VIDEO_FAST",
    "أصغر حجم للفيديو": "VIDEO_SMALLEST",
    "حجم فيديو محدد": "VIDEO_TARGET_SIZE",
}
PROFILE = PROFILE_FROM_UI.get(
    os.environ.get("TSC_PROFILE", "SMART_AUTO"),
    os.environ.get("TSC_PROFILE", "SMART_AUTO"),
)
TARGET_SIZE_TEXT = os.environ.get("TSC_TARGET_SIZE_MB", "").strip()


class AppError(Exception):
    def __init__(self, code: str, message: str, details: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass
class MediaInfo:
    duration: float = 0.0
    size: int = 0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_video: bool = False
    has_audio: bool = False


def run_quiet(args, check=True):
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=check,
    )


def ensure_dependencies():
    packages = [
        "telethon==1.45.0",
        "cryptg",
        "nest_asyncio",
        "aiofasttelethonhelper==0.1.3",
    ]
    needs_install = False
    try:
        import telethon  # noqa
        import cryptg  # noqa
        import nest_asyncio  # noqa
        import aiofasttelethonhelper  # noqa
        if getattr(telethon, "__version__", "") != "1.45.0":
            needs_install = True
    except Exception:
        needs_install = True

    if needs_install:
        print("🔧 تجهيز الأدوات المطلوبة لأول مرة...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "--upgrade", *packages],
            check=True,
        )

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        subprocess.run(["apt-get", "-qq", "update"], check=True)
        subprocess.run(["apt-get", "-qq", "install", "-y", "ffmpeg"], check=True)



def mount_drive():
    try:
        from google.colab import drive
    except Exception as exc:
        raise AppError("E101", "هذا الملف مخصص للعمل داخل Google Colab.", str(exc))
    print("☁️ توصيل Google Drive...")
    drive.mount("/content/drive", force_remount=False)
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_config():
    return load_json(CONFIG_PATH, {})


def first_time_setup_html():
    return """
    <div dir="rtl" style="
        max-width: 760px;
        margin: 10px 0 18px;
        padding: 18px 20px;
        border: 1px solid #d0d7de;
        border-radius: 14px;
        background: #f8fafc;
        color: #111827;
        line-height: 1.9;
        text-align: right;
        font-family: Arial, sans-serif;
    ">
      <div style="font-size: 22px; font-weight: 700; margin-bottom: 6px;">
        🔐 توصيل تيليجرام لأول مرة
      </div>

      <div style="margin-bottom: 14px;">
        الخطوة دي بتتعمل <b>مرة واحدة فقط</b>. بعد حفظ البيانات، البوكس ده مش هيظهر في التشغيلات الجاية.
      </div>

      <div style="font-weight: 700; margin-top: 10px;">١) افتح صفحة Telegram API</div>
      <div style="margin: 8px 0 14px;">
        <a href="https://my.telegram.org/apps" target="_blank" rel="noopener noreferrer"
           style="
             display: inline-block;
             padding: 8px 14px;
             border-radius: 9px;
             background: #2563eb;
             color: white;
             text-decoration: none;
             font-weight: 700;
           ">
          فتح my.telegram.org/apps ↗
        </a>
      </div>

      <div style="font-weight: 700;">٢) من الصفحة انسخ البيانات التالية</div>
      <div style="margin: 6px 0 14px;">
        <div><code dir="ltr">API ID</code> — رقم.</div>
        <div><code dir="ltr">API Hash</code> — نص طويل.</div>
      </div>

      <div style="font-weight: 700;">٣) ارجع هنا وأدخل البيانات في الخانات اللي هتظهر تحت البوكس</div>
      <div style="margin-top: 6px;">
        واكتب رقم موبايلك بصيغة دولية، مثال:
        <code dir="ltr">+2010XXXXXXX</code>
      </div>

      <div style="
          margin-top: 16px;
          padding: 10px 12px;
          border-radius: 9px;
          background: #eef6ff;
      ">
        ✅ بعد أول إعداد، البيانات تتقرأ تلقائيًا من Google Drive الخاص بيك.
      </div>
    </div>
    """


def show_first_time_setup_card():
    try:
        from IPython.display import HTML, display
        display(HTML(first_time_setup_html()))
    except Exception:
        print()
        print("🔐 توصيل تيليجرام لأول مرة")
        print("الخطوة دي بتتعمل مرة واحدة فقط.")
        print("افتح: https://my.telegram.org/apps")
        print("انسخ API ID و API Hash، ثم ارجع وأدخلهم هنا.")
        print()


def first_time_setup(config):
    if config.get("api_id") and config.get("api_hash") and config.get("phone"):
        return config

    show_first_time_setup_card()

    print("١/٣ — API ID")
    api_id_raw = input("اكتب API ID: ").strip()
    print()

    print("٢/٣ — API Hash")
    print("لن يظهر النص أثناء الكتابة — ده طبيعي.")
    api_hash = getpass.getpass("اكتب API Hash: ").strip()
    print()

    print("٣/٣ — رقم الهاتف")
    print("لن يظهر الرقم أثناء الكتابة — ده طبيعي.")
    phone = getpass.getpass("رقم الهاتف بصيغة دولية: ").strip()
    print()

    if not api_id_raw.isdigit() or not api_hash or not phone:
        raise AppError("E110", "بيانات Telegram غير مكتملة أو غير صحيحة.")

    config.update(
        api_id=int(api_id_raw),
        api_hash=api_hash,
        phone=phone,
    )
    save_json(CONFIG_PATH, config)
    print("✅ تم حفظ إعداد تيليجرام. البوكس ده مش هيظهر في التشغيلات القادمة.")
    return config


def get_brand_asset(filename: str) -> Path:
    local = Path("telegram-smart-compressor") / "assets" / filename
    if os.environ.get("TSC_TEST_MODE") == "1" and local.exists():
        return local

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    target = TMP_DIR / filename
    if target.exists() and target.stat().st_size > 1024:
        return target

    ref = (
        os.environ.get("TSC_ENGINE_SHA")
        or os.environ.get("TSC_UPDATE_CHANNEL")
        or "main"
    )
    url = (
        f"https://raw.githubusercontent.com/{BRAND_REPO}/{ref}/"
        f"{BRAND_ASSET_DIR}/{filename}"
    )
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Media-Lite-Colab",
            "Cache-Control": "no-cache",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        data = response.read()

    if len(data) < 1024 or not data.startswith(b"\xff\xd8"):
        raise RuntimeError(f"invalid-brand-asset:{filename}")

    target.write_bytes(data)
    return target


async def apply_channel_branding(client, channel, config):
    from telethon import functions, types

    changed = False

    if config.get("workspace_about_version") != BRANDING_VERSION:
        try:
            await client(
                functions.channels.EditAboutRequest(
                    channel=channel,
                    about=CHANNEL_ABOUT,
                )
            )
            config["workspace_about_version"] = BRANDING_VERSION
            changed = True
        except Exception:
            pass

    if config.get("workspace_photo_version") != BRANDING_VERSION:
        try:
            logo = get_brand_asset(BRAND_LOGO_FILE)
            uploaded = await client.upload_file(str(logo))
            await client(
                functions.channels.EditPhotoRequest(
                    channel=channel,
                    photo=types.InputChatUploadedPhoto(file=uploaded),
                )
            )
            config["workspace_photo_version"] = BRANDING_VERSION
            changed = True
            print("✅ تم تحديث لوجو القناة.")
        except Exception:
            print("↪️ تعذر تحديث لوجو القناة الآن؛ هنكمل عادي ونجرب في التشغيل القادم.")

    if changed:
        save_json(CONFIG_PATH, config)


def pinned_message_html():
    return (
        "🗜️ <b>اضغطها | Media Lite</b>\n"
        "ضغط ذكي للفيديو والصوت.\n\n"
        "ابعت الملف هنا، وبعدها افتح Colab واضغط تشغيل.\n"
        f'<a href="{COLAB_URL}">▶ افتح Colab</a>\n\n'
        "<b>إعادة نتيجة قديمة</b> — اعمل Reply عليها واكتب:\n"
        "<code>1</code> نفس الإعداد  •  <code>2</code> أصغر  •  <code>3</code> صوت فقط\n"
        "أو اكتب الحجم مباشرة، مثال <code>80</code>."
    )


async def ensure_pinned_brand_message(client, channel, config):
    instructions = pinned_message_html()
    old_instruction = None
    instruction_id = config.get("workspace_instruction_id")

    if instruction_id:
        try:
            old_instruction = await client.get_messages(channel, ids=int(instruction_id))
        except Exception:
            old_instruction = None

    if (
        old_instruction
        and config.get("workspace_pin_version") == BRANDING_VERSION
    ):
        try:
            updated = await client.edit_message(
                channel,
                old_instruction,
                instructions,
                parse_mode="html",
            )
            await client.pin_message(channel, updated or old_instruction, notify=False)
            return updated or old_instruction
        except Exception:
            old_instruction = None

    banner = None
    try:
        banner = get_brand_asset(BRAND_BANNER_FILE)
    except Exception:
        banner = None

    try:
        if banner is not None:
            instruction = await client.send_file(
                channel,
                str(banner),
                caption=instructions,
                parse_mode="html",
                force_document=False,
            )
        else:
            instruction = await client.send_message(
                channel,
                instructions,
                parse_mode="html",
                link_preview=False,
            )
    except Exception:
        instruction = await client.send_message(
            channel,
            instructions,
            parse_mode="html",
            link_preview=False,
        )

    await client.pin_message(channel, instruction, notify=False)

    config["workspace_instruction_id"] = int(instruction.id)
    config["workspace_pin_version"] = BRANDING_VERSION
    save_json(CONFIG_PATH, config)

    if old_instruction and int(old_instruction.id) != int(instruction.id):
        try:
            await client.delete_messages(channel, [int(old_instruction.id)])
        except Exception:
            pass

    return instruction




def default_state():
    return {
        "processed_source_ids": [],
        "output_ids": [],
        "command_ids": [],
        "lineage": {},
    }


def normalize_state(state):
    base = default_state()
    if isinstance(state, dict):
        base.update(state)
    for key in ("processed_source_ids", "output_ids", "command_ids"):
        base[key] = [int(x) for x in base.get(key, []) if str(x).lstrip("-").isdigit()]
    if not isinstance(base.get("lineage"), dict):
        base["lineage"] = {}
    return base



def migrate_old_state(channel, state):
    if not OLD_STATE_PATH.exists():
        return state

    old = load_json(OLD_STATE_PATH, {})
    old_channel = old.get(str(getattr(channel, "id", "")))
    if not old_channel:
        return state

    sources = set(int(x) for x in state.get("processed_source_ids", []))
    outputs = set(int(x) for x in state.get("output_ids", []))

    if isinstance(old_channel, list):
        sources.update(int(x) for x in old_channel)
    elif isinstance(old_channel, dict):
        sources.update(int(x) for x in old_channel.get("sources", []))
        outputs.update(int(x) for x in old_channel.get("outputs", []))

    before = (
        len(state.get("processed_source_ids", [])),
        len(state.get("output_ids", [])),
    )
    state["processed_source_ids"] = sorted(sources)
    state["output_ids"] = sorted(outputs)
    after = (
        len(state["processed_source_ids"]),
        len(state["output_ids"]),
    )

    if after != before:
        save_json(STATE_PATH, state)
        print("✅ تم دمج سجل الملفات القديمة تلقائيًا بدون إعادة ضغطها.")
    return state

def ffprobe(path: Path) -> MediaInfo:
    proc = run_quiet(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size",
            "-show_entries",
            "stream=codec_type,width,height,r_frame_rate",
            "-of",
            "json",
            str(path),
        ]
    )
    data = json.loads(proc.stdout or "{}")
    fmt = data.get("format", {})
    info = MediaInfo(
        duration=float(fmt.get("duration") or 0),
        size=int(float(fmt.get("size") or path.stat().st_size)),
    )
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            info.has_video = True
            info.width = int(stream.get("width") or 0)
            info.height = int(stream.get("height") or 0)
            rate = stream.get("r_frame_rate") or "0/1"
            try:
                info.fps = float(Fraction(rate))
            except Exception:
                info.fps = 0.0
        elif stream.get("codec_type") == "audio":
            info.has_audio = True
    return info


def human_size(size: int) -> str:
    size = max(0, int(size or 0))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return f"{size:.1f} GB"


def clean_name(name: str, fallback: str) -> str:
    name = (name or fallback).strip()
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    return name[:180] or fallback


def detect_nvenc() -> bool:
    if shutil.which("nvidia-smi") is None:
        return False
    try:
        run_quiet(["nvidia-smi"], check=True)
        test = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=size=32x32:rate=1",
                "-frames:v",
                "1",
                "-c:v",
                "h264_nvenc",
                "-f",
                "null",
                "-",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
        return test.returncode == 0
    except Exception:
        return False


def _format_duration(seconds):
    seconds = max(0, int(seconds or 0))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


class LiveProgress:
    def __init__(self, label, icon, mode="bytes"):
        self.label = label
        self.icon = icon
        self.mode = mode
        self.started = time.time()
        self.last_time = self.started
        self.last_current = 0.0
        self.speed = 0.0
        self.last_render = 0.0
        self.handle = None
        self.terminal_started = False

    def _text(self, current, total, speed_override=None):
        total = float(total or 0)
        current = float(current or 0)
        pct = min(100.0, current / total * 100) if total > 0 else 0.0

        if self.mode == "time":
            speed_x = speed_override if speed_override and speed_override > 0 else self.speed
            eta = (total - current) / speed_x if total > current and speed_x > 0 else 0
            detail = f"{_format_duration(current)} / {_format_duration(total)}"
            speed_text = f"{speed_x:.1f}x" if speed_x > 0 else "..."
        else:
            eta = (total - current) / self.speed if total > current and self.speed > 0 else 0
            detail = f"{human_size(int(current))} / {human_size(int(total))}"
            speed_text = f"{self.speed / 1024 / 1024:.1f} MB/s" if self.speed > 0 else "..."

        eta_text = _format_duration(eta) if eta > 0 else "..."
        return pct, detail, speed_text, eta_text

    def update(self, current, total, speed_override=None, force=False):
        now = time.time()
        current = float(current or 0)
        total = float(total or 0)

        dt = max(now - self.last_time, 1e-6)
        delta = max(0.0, current - self.last_current)
        if delta > 0:
            instant = delta / dt
            self.speed = instant if self.speed <= 0 else (0.25 * instant + 0.75 * self.speed)

        self.last_time = now
        self.last_current = current

        if not force and now - self.last_render < 0.5 and (not total or current < total):
            return
        self.last_render = now

        pct, detail, speed_text, eta_text = self._text(current, total, speed_override)
        line = f"{self.icon} {self.label} — {pct:.1f}% • {detail} • {speed_text} • باقي {eta_text}"

        try:
            from IPython.display import HTML, display
            card = HTML(
                f'<div dir="rtl" style="max-width:760px;padding:8px 12px;margin:4px 0;'
                f'border:1px solid #d0d7de;border-radius:10px;background:#f8fafc;'
                f'font-family:Arial,sans-serif">'
                f'<div style="margin-bottom:6px">{html.escape(line)}</div>'
                f'<progress value="{pct:.2f}" max="100" style="width:100%;height:12px"></progress>'
                f'</div>'
            )
            if self.handle is None:
                self.handle = display(card, display_id=True)
            elif self.handle is not None:
                self.handle.update(card)
        except Exception:
            print("\r" + line + " " * 8, end="", flush=True)
            self.terminal_started = True

    def callback(self, *args, **kwargs):
        current = kwargs.get("done")
        total = kwargs.get("total")
        if current is None and args:
            current = args[0]
        if total is None and len(args) > 1:
            total = args[1]
        self.update(current or 0, total or 0)

    def finish(self, total, speed_override=None):
        self.update(total, total, speed_override=speed_override, force=True)
        if self.terminal_started:
            print()


def run_ffmpeg(cmd, duration: float, label: str):
    full = [cmd[0], "-y", "-nostdin", "-hide_banner", "-loglevel", "error", *cmd[1:-1], "-progress", "pipe:1", "-nostats", cmd[-1]]
    proc = subprocess.Popen(
        full,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    progress = LiveProgress(label, "🎬", mode="time")
    lines = []
    current_seconds = 0.0
    speed_x = None

    if proc.stdout:
        for raw in proc.stdout:
            line = raw.strip()
            lines.append(line)

            if line.startswith("speed="):
                raw_speed = line.split("=", 1)[1].rstrip("x").strip()
                try:
                    speed_x = float(raw_speed)
                except Exception:
                    speed_x = None

            if line.startswith("out_time_ms=") and duration > 0:
                try:
                    current_seconds = int(line.split("=", 1)[1]) / 1_000_000
                    progress.update(current_seconds, duration, speed_override=speed_x)
                except Exception:
                    pass

    code = proc.wait()
    if code != 0:
        tail = "\n".join(lines[-30:])
        raise AppError("E420", "فشل ضغط الملف.", tail)

    progress.finish(duration, speed_override=speed_x)


async def download_media_fast(client, msg, path: Path):
    size = int(getattr(getattr(msg, "file", None), "size", 0) or 0)
    progress = LiveProgress("تنزيل", "⬇️")

    if size >= TURBO_THRESHOLD and getattr(msg, "document", None):
        downloader = None
        try:
            import aiofiles
            from aiofasttelethonhelper.core.transfer import ParallelTransferrer
            from telethon.utils import get_input_location

            dc_id, location = get_input_location(msg.document)
            downloader = ParallelTransferrer(client, dc_id)
            chunks = downloader.download(
                location,
                size,
                part_size_kb=TURBO_PART_KB,
                connection_count=TURBO_CONNECTIONS,
            )

            print(f"⚡ تنزيل سريع — {TURBO_CONNECTIONS} اتصالات")
            async with aiofiles.open(str(path), "wb") as out:
                done = 0
                async for chunk in chunks:
                    await out.write(chunk)
                    done += len(chunk)
                    progress.update(done, size)

            progress.finish(size)
            return str(path), "turbo"

        except Exception:
            try:
                if downloader is not None and getattr(downloader, "senders", None):
                    await downloader._cleanup()
            except Exception:
                pass
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            print("↪️ تعذر التنزيل السريع؛ هنكمل تلقائيًا بالطريقة العادية.")

    downloaded = await client.download_media(
        msg,
        file=str(path),
        progress_callback=progress.callback,
    )
    if downloaded:
        progress.finish(size or Path(downloaded).stat().st_size)
    return downloaded, "normal"



def encode_audio(src: Path, dst: Path, info: MediaInfo):
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-vn",
        "-c:a",
        "libopus",
        "-b:a",
        "8k",
        "-ac",
        "1",
        "-ar",
        "12000",
        "-application",
        "voip",
        "-frame_duration",
        "60",
        "-compression_level",
        "0",
        "-vbr",
        "on",
        str(dst),
    ]
    run_ffmpeg(cmd, info.duration, "ضغط الصوت")


def video_filter(info: MediaInfo, max_w: int, max_h: int, fps_cap: int) -> str:
    source_fps = info.fps if info.fps > 0 else float(fps_cap)
    fps = max(1.0, min(source_fps, float(fps_cap)))
    return (
        f"scale='min({max_w},iw)':'min({max_h},ih)':"
        f"force_original_aspect_ratio=decrease:force_divisible_by=2,"
        f"fps={fps:.3f}"
    )


def classify_video_complexity(src: Path, info: MediaInfo) -> str:
    if info.duration <= 8:
        return "NORMAL"

    points = [max(0.0, info.duration * 0.20), max(0.0, info.duration * 0.65)]
    rates = []

    print("🧠 تحليل سريع لطبيعة المحتوى...")
    for i, start in enumerate(points, 1):
        sample = TMP_DIR / f"sample_{src.stem}_{i}_{int(time.time() * 1000)}.mp4"
        sample_len = min(4.0, max(1.0, info.duration - start))
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start:.2f}", "-i", str(src),
            "-t", f"{sample_len:.2f}",
            "-an",
            "-vf", "scale='min(640,iw)':'min(360,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,fps=8",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
            "-pix_fmt", "yuv420p",
            str(sample),
        ]
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            if proc.returncode == 0 and sample.exists() and sample_len > 0:
                kbps = sample.stat().st_size * 8 / sample_len / 1000
                rates.append(kbps)
        finally:
            try:
                sample.unlink()
            except FileNotFoundError:
                pass

    if not rates:
        return "NORMAL"

    average = sum(rates) / len(rates)
    if average < 220:
        return "STATIC"
    if average < 420:
        return "NORMAL"
    return "DETAIL"


def encode_video(src: Path, dst: Path, info: MediaInfo, profile: str, target_mb: int | None, nvenc: bool):
    if not info.has_video:
        raise AppError("E411", "هذا الملف لا يحتوي على فيديو.")

    if profile == "VIDEO_SMALLEST":
        vf = video_filter(info, 960, 540, 12)
        cmd = [
            "ffmpeg", "-y", "-i", str(src), "-vf", vf,
            "-c:v", "libx264", "-preset", "slow", "-crf", "31",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "40k", "-ac", "1",
            "-movflags", "+faststart",
            str(dst),
        ]
        run_ffmpeg(cmd, info.duration, "ضغط الفيديو")
        return "SMALLEST"

    if profile == "VIDEO_TARGET_SIZE":
        target_mb = int(target_mb or 100)
        if info.duration <= 0:
            raise AppError("E413", "تعذر معرفة مدة الفيديو لحساب الحجم المطلوب.")
        audio_k = 40 if info.has_audio else 0
        total_kbps = (target_mb * 1024 * 1024 * 8 / info.duration) / 1000
        video_k = int(total_kbps * 0.96 - audio_k)
        if video_k < 80:
            recommended = math.ceil(info.duration * (80 + audio_k) * 1000 / 8 / 1024 / 1024 / 0.96)
            raise AppError(
                "E414",
                f"الحجم المطلوب صغير جدًا لهذا الفيديو. جرّب حوالي {recommended} MB أو أكثر.",
            )
        vf = video_filter(info, 1280, 720, 15)
        passlog = str(TMP_DIR / f"pass_{int(time.time() * 1000)}")
        first = [
            "ffmpeg", "-y", "-i", str(src), "-vf", vf,
            "-an", "-c:v", "libx264", "-preset", "veryfast",
            "-b:v", f"{video_k}k", "-pass", "1", "-passlogfile", passlog,
            "-f", "mp4", os.devnull,
        ]
        second = [
            "ffmpeg", "-y", "-i", str(src), "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast",
            "-b:v", f"{video_k}k", "-pass", "2", "-passlogfile", passlog,
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", f"{audio_k}k", "-ac", "1",
            "-movflags", "+faststart",
            str(dst),
        ]
        try:
            run_ffmpeg(first, info.duration, "تحليل الحجم")
            run_ffmpeg(second, info.duration, "ضغط الفيديو")
        finally:
            for p in TMP_DIR.glob(Path(passlog).name + "*"):
                try:
                    p.unlink()
                except Exception:
                    pass
        return "TARGET"

    if profile == "VIDEO_FAST":
        complexity = "FAST"
        fps_cap = 18
        gpu_codec = [
            "-c:v", "h264_nvenc", "-preset", "p3",
            "-rc", "vbr", "-cq", "31", "-b:v", "0",
        ]
        cpu_codec = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "31"]
    else:
        complexity = classify_video_complexity(src, info)
        if complexity == "STATIC":
            fps_cap, gpu_cq, cpu_crf = 12, 31, 30
            print("📄 المحتوى ثابت غالبًا — هنقلل الفريمات بدون ما نضيّع وضوح الشرائح.")
        elif complexity == "DETAIL":
            fps_cap, gpu_cq, cpu_crf = 18, 28, 27
            print("🔎 المحتوى فيه تفاصيل — هنحافظ على جودة أعلى.")
        else:
            fps_cap, gpu_cq, cpu_crf = 15, 30, 29
            print("🎞️ محتوى عادي — إعداد متوازن.")

        gpu_codec = [
            "-c:v", "h264_nvenc", "-preset", "p4",
            "-rc", "vbr", "-cq", str(gpu_cq), "-b:v", "0",
        ]
        cpu_codec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(cpu_crf)]

    vf = video_filter(info, 1280, 720, fps_cap)

    def build_cmd(codec):
        return [
            "ffmpeg", "-y", "-i", str(src), "-vf", vf,
            *codec,
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "48k", "-ac", "1",
            "-movflags", "+faststart",
            str(dst),
        ]

    if nvenc:
        try:
            run_ffmpeg(build_cmd(gpu_codec), info.duration, "ضغط الفيديو")
            return complexity
        except AppError:
            print("↪️ كارت الشاشة ما نفعش مع الملف ده؛ هنكمل تلقائيًا على CPU.")
            try:
                dst.unlink()
            except FileNotFoundError:
                pass

    run_ffmpeg(build_cmd(cpu_codec), info.duration, "ضغط الفيديو")
    return complexity



def parse_rerun_command(text: str):
    text = (text or "").strip()
    normalized = text.replace("ـ", "").strip()

    same = {"1", "إعادة", "اعادة", "🔁"}
    smaller = {"2", "أصغر", "اصغر", "🔁 أصغر", "🔁 اصغر"}
    audio = {"3", "صوت", "🔁 صوت"}

    if normalized in same:
        return {"profile": None, "target_mb": None}
    if normalized in smaller:
        return {"profile": "VIDEO_SMALLEST", "target_mb": None}
    if normalized in audio:
        return {"profile": "AUDIO_TINY", "target_mb": None}

    match = re.fullmatch(r"(\d{1,5})(?:\s*(?:mb|مب|ميجا))?", normalized, flags=re.IGNORECASE)
    if match:
        value = int(match.group(1))
        if value > 3:
            return {"profile": "VIDEO_TARGET_SIZE", "target_mb": value}

    # Backward compatibility with older verbose commands.
    if normalized.startswith("🔁"):
        rest = normalized[1:].strip()
        if "صوت" in rest:
            return {"profile": "AUDIO_TINY", "target_mb": None}
        if "أصغر" in rest or "اصغر" in rest:
            return {"profile": "VIDEO_SMALLEST", "target_mb": None}
        match = re.search(r"(\d{1,5})", rest)
        if match:
            return {"profile": "VIDEO_TARGET_SIZE", "target_mb": int(match.group(1))}
        return {"profile": None, "target_mb": None}

    return None



def message_media_kind(msg):
    file_obj = getattr(msg, "file", None)
    mime = (getattr(file_obj, "mime_type", None) or "").lower()
    if getattr(msg, "video", None) or mime.startswith("video/"):
        return "video"
    if getattr(msg, "voice", None) or getattr(msg, "audio", None) or mime.startswith("audio/"):
        return "audio"
    return None


def looks_like_generated_output(msg):
    text = (getattr(msg, "message", "") or "").strip()
    return bool(re.match(r"^✅\s*(?:v\d+\s*•|تم\s*•)", text))


def resolve_profile(msg, explicit=None):
    if explicit:
        return explicit
    if PROFILE == "SMART_AUTO":
        return "VIDEO_BALANCED" if message_media_kind(msg) == "video" else "AUDIO_TINY"
    return PROFILE


def target_size_from_ui():
    if not TARGET_SIZE_TEXT:
        return 100
    if not TARGET_SIZE_TEXT.isdigit():
        raise AppError("E210", "الحجم المستهدف يجب أن يكون رقمًا صحيحًا بالميجابايت.")
    value = int(TARGET_SIZE_TEXT)
    if value <= 0:
        raise AppError("E210", "الحجم المستهدف يجب أن يكون أكبر من صفر.")
    return value


async def show_recent_operations(client, channel):
    messages = await client.get_messages(channel, limit=120)
    recent = [
        msg for msg in messages
        if message_media_kind(msg) and looks_like_generated_output(msg)
    ][:5]

    try:
        from IPython.display import HTML, display

        if recent:
            items = []
            for msg in recent:
                lines = [line.strip() for line in (msg.message or "").splitlines() if line.strip()]
                title = lines[1] if len(lines) > 1 else f"عملية #{msg.id}"
                stats = lines[2] if len(lines) > 2 else ""
                items.append(
                    f'<div style="padding:5px 0;border-bottom:1px solid #e5e7eb">'
                    f'<b>{html.escape(title[:90])}</b>'
                    f'{f"<br><span style=\"color:#4b5563\">{html.escape(stats[:120])}</span>" if stats else ""}'
                    f'</div>'
                )
            history = "".join(items)
        else:
            history = '<div style="color:#6b7280">لسه مفيش عمليات سابقة.</div>'

        display(HTML(
            '<div dir="rtl" style="max-width:760px;padding:14px 16px;border:1px solid #d0d7de;'
            'border-radius:12px;background:#f8fafc;font-family:Arial,sans-serif;line-height:1.8">'
            '<b>✅ مفيش ملفات جديدة</b><br>'
            '<span style="color:#4b5563">آخر العمليات:</span>'
            f'{history}'
            '<div style="margin-top:10px"><b>عايز تعالج نتيجة تاني؟</b> '
            'رد عليها بـ <code>1</code> لنفس الإعداد، <code>2</code> لأصغر حجم، '
            '<code>3</code> للصوت فقط، أو اكتب الحجم مثل <code>80</code>.</div>'
            '</div>'
        ))
    except Exception:
        print("✅ مفيش ملفات جديدة.")
        if recent:
            print("آخر العمليات:")
            for msg in recent:
                lines = [line.strip() for line in (msg.message or "").splitlines() if line.strip()]
                print("•", " — ".join(lines[1:3])[:140])
        print("لإعادة نتيجة: رد بـ 1 أو 2 أو 3، أو رقم الحجم مثل 80.")




async def ensure_workspace(client, config):
    from telethon import functions, types

    async def find_existing_workspace():
        async for dialog in client.iter_dialogs():
            if (dialog.name or "").strip() not in WORKSPACE_ALIASES:
                continue
            entity = dialog.entity
            rights = getattr(entity, "admin_rights", None)
            can_post = bool(
                getattr(entity, "creator", False)
                or (rights and getattr(rights, "post_messages", False))
            )
            if can_post:
                return entity
        return None

    channel = None
    created = False

    saved_id = config.get("workspace_id")
    if saved_id:
        try:
            channel = await client.get_entity(types.PeerChannel(int(saved_id)))
        except Exception:
            config.pop("workspace_id", None)

    if channel is None:
        old_ref = str(config.get("channel", "") or "").strip()
        if old_ref:
            try:
                ref = int(old_ref) if re.fullmatch(r"-?\d+", old_ref) else old_ref
                channel = await client.get_entity(ref)
            except Exception:
                channel = None

    if channel is None:
        channel = await find_existing_workspace()

    if channel is None:
        print()
        print("🗜️ إنشاء مساحة اضغطها | Media Lite...")

        last_error = None
        for attempt, delay in enumerate((0, 3, 7, 15), 1):
            if delay:
                print(f"↻ إعادة المحاولة {attempt}/4 بعد {delay} ثوانٍ...")
                await asyncio.sleep(delay)

            try:
                result = await client(
                    functions.channels.CreateChannelRequest(
                        title=WORKSPACE_TITLE,
                        about=CHANNEL_ABOUT,
                        broadcast=True,
                        megagroup=False,
                    )
                )
                channel = result.chats[0]
                created = True
                break

            except Exception as exc:
                last_error = exc
                try:
                    channel = await find_existing_workspace()
                except Exception:
                    channel = None

                if channel is not None:
                    created = True
                    break

                seconds = int(getattr(exc, "seconds", 0) or 0)
                if seconds:
                    if seconds <= 60 and attempt < 4:
                        await asyncio.sleep(max(seconds, 1))
                        continue
                    raise AppError(
                        "E131",
                        f"Telegram طلب الانتظار {seconds} ثانية قبل إنشاء القناة. جرّب بعد انتهاء المدة.",
                        repr(exc),
                    )

                error_name = type(exc).__name__
                error_text = str(exc)
                error_upper = error_text.upper()

                if error_name == "ChannelsTooMuchError" or "CHANNELS_TOO_MUCH" in error_upper:
                    raise AppError(
                        "E132",
                        "حساب Telegram وصل لحد القنوات أو المجموعات المسموح بها.",
                        repr(exc),
                    )

                if error_name == "UserRestrictedError" or "USER_RESTRICTED" in error_upper:
                    raise AppError(
                        "E133",
                        "Telegram مانع الحساب حاليًا من إنشاء قنوات أو مجموعات. راجع @SpamBot.",
                        repr(exc),
                    )

                transient = error_name in {
                    "RpcCallFailError", "ServerError", "TimedOutError", "TimeoutError"
                } or "internal issues" in error_text.lower() or "try again later" in error_text.lower()

                if not transient:
                    safe_reason = re.sub(r"[^A-Za-z0-9_ -]", "", error_name)[:80] or "TelegramError"
                    raise AppError(
                        "E134",
                        f"Telegram رفض إنشاء القناة تلقائيًا ({safe_reason}).",
                        repr(exc),
                    )

        if channel is None:
            raise AppError(
                "E130",
                "Telegram واجه مشكلة مؤقتة أثناء إنشاء القناة. تسجيل الدخول محفوظ؛ جرّب بعد دقيقة.",
                repr(last_error),
            )

    current_title = (getattr(channel, "title", "") or "").strip()
    if current_title in (WORKSPACE_ALIASES - {WORKSPACE_TITLE}):
        try:
            await client(
                functions.channels.EditTitleRequest(
                    channel=channel,
                    title=WORKSPACE_TITLE,
                )
            )
            channel = await client.get_entity(channel)
            print(f"✅ تم تحديث اسم القناة إلى «{WORKSPACE_TITLE}».")
        except Exception:
            pass

    config["workspace_id"] = int(channel.id)
    save_json(CONFIG_PATH, config)

    await apply_channel_branding(client, channel, config)
    instruction = await ensure_pinned_brand_message(client, channel, config)

    try:
        input_peer = await client.get_input_entity(channel)
        await client(
            functions.messages.ToggleDialogPinRequest(
                pinned=True,
                peer=types.InputDialogPeer(peer=input_peer),
            )
        )
    except Exception:
        pass

    if created:
        print(f"✅ تم إنشاء قناة «{WORKSPACE_TITLE}» وحفظها للاستخدام القادم.")
    return channel, created


async def collect_queue(client, channel, state):
    processed = set(int(x) for x in state["processed_source_ids"])
    outputs = set(int(x) for x in state["output_ids"])
    commands_done = set(int(x) for x in state["command_ids"])

    messages = await client.get_messages(channel, limit=SCAN_LIMIT)
    by_id = {m.id: m for m in messages}
    jobs = []
    rerun_source_ids = set()

    for msg in messages:
        if message_media_kind(msg) and looks_like_generated_output(msg):
            outputs.add(int(msg.id))
            if getattr(msg, "reply_to_msg_id", None):
                processed.add(int(msg.reply_to_msg_id))

    state["processed_source_ids"] = sorted(processed)
    state["output_ids"] = sorted(outputs)

    for msg in reversed(messages):
        if msg.id in commands_done or not getattr(msg, "message", None) or not getattr(msg, "reply_to_msg_id", None):
            continue

        command = parse_rerun_command(msg.message)
        if command is None:
            continue

        target = by_id.get(msg.reply_to_msg_id)
        if target is None:
            try:
                target = await client.get_messages(channel, ids=msg.reply_to_msg_id)
            except Exception:
                target = None

        if not target or not message_media_kind(target):
            if msg.id not in state["command_ids"]:
                state["command_ids"].append(int(msg.id))
            continue

        jobs.append(
            {
                "source": target,
                "command": msg,
                "profile": command["profile"],
                "target_mb": command["target_mb"],
                "rerun": True,
            }
        )
        rerun_source_ids.add(int(target.id))

    for msg in reversed(messages):
        if not message_media_kind(msg):
            continue
        if msg.id in outputs or msg.id in processed or msg.id in rerun_source_ids:
            continue
        if looks_like_generated_output(msg):
            continue

        jobs.append(
            {
                "source": msg,
                "command": None,
                "profile": None,
                "target_mb": None,
                "rerun": False,
            }
        )

    return jobs


async def prepare_job(client, job, index: int, total: int):
    msg = job["source"]
    original_name = getattr(getattr(msg, "file", None), "name", None)
    ext = getattr(getattr(msg, "file", None), "ext", None) or ""
    name = clean_name(original_name, f"telegram_{msg.id}{ext}")
    src = TMP_DIR / f"{msg.id}_{int(time.time() * 1000)}_{name}"
    started_at = time.time()

    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"📄 ملف {index} من {total} — {name}")

    try:
        downloaded, mode = await download_media_fast(client, msg, src)
        if not downloaded:
            raise AppError("E401", "تعذر تنزيل الملف من Telegram.")
        src = Path(downloaded)
        return {
            "job": job,
            "msg": msg,
            "name": name,
            "src": src,
            "info": ffprobe(src),
            "started_at": started_at,
            "download_mode": mode,
            "error": None,
        }
    except AppError as exc:
        try:
            if src.exists():
                src.unlink()
        except Exception:
            pass
        return {"job": job, "msg": msg, "name": name, "src": src, "error": exc, "started_at": started_at}
    except Exception as exc:
        try:
            if src.exists():
                src.unlink()
        except Exception:
            pass
        return {
            "job": job,
            "msg": msg,
            "name": name,
            "src": src,
            "error": AppError("E401", "تعذر تنزيل الملف من Telegram.", traceback.format_exc()),
            "started_at": started_at,
        }




async def process_job(client, channel, state, prepared, nvenc: bool, index: int, total: int):
    from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeVideo

    job = prepared["job"]
    msg = prepared["msg"]
    command_msg = job["command"]
    src = Path(prepared["src"])
    name = prepared["name"]
    started_at = prepared.get("started_at", time.time())
    dst = None

    if prepared.get("error"):
        exc = prepared["error"]
        print(f"⚠️ {exc.code} — {exc.message}")
        if exc.details:
            ERROR_LOG.write_text(exc.details, encoding="utf-8")
        try:
            await client.send_message(channel, f"⚠️ {exc.code}\n{exc.message}", reply_to=msg.id)
        except Exception:
            pass
        return {"ok": False, "skipped": False, "original": 0, "final": 0}

    info = prepared["info"]
    parent_lineage = state.get("lineage", {}).get(str(msg.id), {})
    root_id = int(parent_lineage.get("root_id", msg.id))
    version = int(parent_lineage.get("version", 0)) + 1

    try:
        profile = resolve_profile(msg, job["profile"])
        target_mb = job["target_mb"]
        if profile == "VIDEO_TARGET_SIZE" and target_mb is None:
            target_mb = target_size_from_ui()

        if profile.startswith("VIDEO_") and not info.has_video:
            print("ℹ️ الملف صوتي؛ هنستخدم ضغط الصوت.")
            profile = "AUDIO_TINY"

        if profile == "AUDIO_TINY":
            dst = TMP_DIR / f"{src.stem}_compressed.ogg"
            label = "صوت صغير جدًا"
            print(f"🎧 {label}")
            encode_audio(src, dst, info)
            out_info = ffprobe(dst)

            if dst.stat().st_size >= src.stat().st_size * 0.98:
                raise AppError("E430", "الملف مضغوط بالفعل تقريبًا؛ إعادة الضغط مش هتوفر مساحة مفيدة.")

            title = Path(name).stem[:60]
            attrs = [
                DocumentAttributeAudio(
                    duration=max(0, int(out_info.duration)),
                    voice=False,
                    title=title,
                    performer="",
                )
            ]

            original_size = src.stat().st_size
            final_size = dst.stat().st_size
            saved_pct = max(0.0, (original_size - final_size) / original_size * 100) if original_size else 0
            elapsed = time.time() - started_at
            caption = (
                f"✅ تم • {label}\n"
                f"{Path(name).stem}\n"
                f"{human_size(original_size)} → {human_size(final_size)} • وفر {saved_pct:.0f}%\n"
                f"⏱ {_format_duration(elapsed)}"
            )

            upload = LiveProgress("رفع", "⬆️")
            sent = await client.send_file(
                channel,
                str(dst),
                caption=caption,
                attributes=attrs,
                mime_type="audio/ogg",
                voice_note=False,
                force_document=False,
                reply_to=msg.id,
                progress_callback=upload.callback,
            )
            upload.finish(final_size)

        else:
            dst = TMP_DIR / f"{src.stem}_compressed.mp4"
            labels = {
                "VIDEO_BALANCED": "فيديو متوازن",
                "VIDEO_FAST": "فيديو سريع",
                "VIDEO_SMALLEST": "أصغر حجم",
                "VIDEO_TARGET_SIZE": f"حجم مستهدف {target_mb} MB",
            }
            label = labels.get(profile, profile)
            print(f"🎬 {label}")
            if profile in ("VIDEO_BALANCED", "VIDEO_FAST") and nvenc:
                print("⚡ كارت الشاشة متاح للترميز.")

            complexity = encode_video(src, dst, info, profile, target_mb, nvenc)
            if dst.stat().st_size >= src.stat().st_size * 0.98:
                raise AppError("E430", "الملف مضغوط بالفعل تقريبًا؛ إعادة الضغط مش هتوفر مساحة مفيدة.")

            out_info = ffprobe(dst)
            original_size = src.stat().st_size
            final_size = dst.stat().st_size
            saved_pct = max(0.0, (original_size - final_size) / original_size * 100) if original_size else 0
            elapsed = time.time() - started_at
            quality_hint = f"{out_info.height}p" if out_info.height else ""
            caption = (
                f"✅ تم • {label}\n"
                f"{Path(name).stem}\n"
                f"{human_size(original_size)} → {human_size(final_size)} • وفر {saved_pct:.0f}%\n"
                f"⏱ {_format_duration(elapsed)}"
                + (f" • {quality_hint}" if quality_hint else "")
            )

            upload = LiveProgress("رفع", "⬆️")
            sent = await client.send_file(
                channel,
                str(dst),
                caption=caption,
                force_document=False,
                mime_type="video/mp4",
                supports_streaming=True,
                attributes=[
                    DocumentAttributeVideo(
                        duration=max(0, int(out_info.duration)),
                        w=max(1, int(out_info.width)),
                        h=max(1, int(out_info.height)),
                        supports_streaming=True,
                    )
                ],
                reply_to=msg.id,
                progress_callback=upload.callback,
            )
            upload.finish(final_size)

        state["output_ids"].append(int(sent.id))
        state["lineage"][str(sent.id)] = {
            "root_id": root_id,
            "parent_id": int(msg.id),
            "version": version,
            "profile": profile,
            "created_at": int(time.time()),
        }

        if not job["rerun"]:
            state["processed_source_ids"].append(int(msg.id))
        if command_msg:
            state["command_ids"].append(int(command_msg.id))

        save_json(STATE_PATH, state)

        if command_msg:
            try:
                await client.delete_messages(channel, [int(command_msg.id)])
            except Exception:
                pass

        print(f"✅ تم — {human_size(original_size)} → {human_size(final_size)}")
        return {
            "ok": True,
            "skipped": False,
            "original": original_size,
            "final": final_size,
        }

    except AppError as exc:
        skipped = exc.code == "E430"

        if skipped and not job["rerun"] and msg.id not in state["processed_source_ids"]:
            state["processed_source_ids"].append(int(msg.id))
        if command_msg and command_msg.id not in state["command_ids"]:
            state["command_ids"].append(int(command_msg.id))
        save_json(STATE_PATH, state)

        try:
            await client.send_message(
                channel,
                f"ℹ️ {exc.message}" if skipped else f"⚠️ {exc.code}\n{exc.message}",
                reply_to=msg.id,
            )
        except Exception:
            pass

        if command_msg and skipped:
            try:
                await client.delete_messages(channel, [int(command_msg.id)])
            except Exception:
                pass

        print(f"{'ℹ️' if skipped else '⚠️'} {exc.code} — {exc.message}")
        if exc.details:
            ERROR_LOG.write_text(exc.details, encoding="utf-8")
        return {"ok": False, "skipped": skipped, "original": 0, "final": 0}

    except Exception:
        details = traceback.format_exc()
        ERROR_LOG.write_text(details, encoding="utf-8")
        try:
            await client.send_message(
                channel,
                "❌ حصل خطأ أثناء معالجة الملف. الملف الأصلي لم يتأثر.",
                reply_to=msg.id,
            )
        except Exception:
            pass
        print("❌ E900 — حصل خطأ غير متوقع. التفاصيل محفوظة في Google Drive.")
        return {"ok": False, "skipped": False, "original": 0, "final": 0}

    finally:
        for path in (src, dst):
            try:
                if path and Path(path).exists():
                    Path(path).unlink()
            except Exception:
                pass




async def app():
    mount_drive()

    from telethon import TelegramClient

    logging.getLogger("telethon").setLevel(logging.ERROR)

    config = first_time_setup(load_config())
    state = normalize_state(load_json(STATE_PATH, default_state()))

    persistent_session = BASE_DIR / "telegram_user.session"
    local_session_base = "/content/telegram_user"
    local_session_file = Path(local_session_base + ".session")
    local_journal_file = Path(local_session_base + ".session-journal")

    if persistent_session.exists():
        shutil.copy2(persistent_session, local_session_file)
    else:
        for stale in (local_session_file, local_journal_file):
            try:
                stale.unlink()
            except FileNotFoundError:
                pass

    print()
    print("🔐 تسجيل الدخول إلى Telegram...")
    client = TelegramClient(
        local_session_base,
        int(config["api_id"]),
        config["api_hash"],
        connection_retries=5,
        request_retries=5,
    )

    try:
        try:
            await client.start(
                phone=config["phone"],
                code_callback=lambda: getpass.getpass(
                    "اكتب كود Telegram (لن يظهر أثناء الكتابة): "
                ).strip(),
                password=lambda: getpass.getpass(
                    "كلمة مرور التحقق بخطوتين (لن تظهر): "
                ).strip(),
            )
        except Exception as exc:
            raise AppError(
                "E120",
                "تعذر تسجيل الدخول إلى Telegram. راجع رقم الهاتف وبيانات API.",
                str(exc),
            )

        channel, _created = await ensure_workspace(client, config)
        state = migrate_old_state(channel, state)
        print(f"✅ القناة جاهزة: {WORKSPACE_TITLE}")

        try:
            client.session.save()
        except Exception:
            pass
        if local_session_file.exists():
            shutil.copy2(local_session_file, persistent_session)

        nvenc = detect_nvenc()
        print("⚡ GPU متاح." if nvenc else "💻 هنستخدم CPU للفيديو.")

        jobs = await collect_queue(client, channel, state)
        save_json(STATE_PATH, state)

        if not jobs:
            await show_recent_operations(client, channel)
            return

        print()
        print(f"📥 الملفات المنتظرة: {len(jobs)}")
        started = time.time()
        results = []

        # Pipeline: while file N is being compressed/uploaded, file N+1 downloads.
        current_download = asyncio.create_task(prepare_job(client, jobs[0], 1, len(jobs)))

        for idx, job in enumerate(jobs, 1):
            prepared = await current_download

            if idx < len(jobs):
                current_download = asyncio.create_task(
                    prepare_job(client, jobs[idx], idx + 1, len(jobs))
                )

            result = await process_job(
                client,
                channel,
                state,
                prepared,
                nvenc=nvenc,
                index=idx,
                total=len(jobs),
            )
            results.append(result)

        ok = sum(1 for r in results if r["ok"])
        skipped = sum(1 for r in results if r.get("skipped"))
        failed = len(results) - ok - skipped
        original = sum(r["original"] for r in results)
        final = sum(r["final"] for r in results)
        saved_pct = ((original - final) / original * 100) if original else 0
        elapsed = time.time() - started

        print()
        print(
            f"✅ خلصنا — نجاح {ok} • تخطي {skipped} • فشل {failed}"
            + (f" • وفر {saved_pct:.0f}% • {_format_duration(elapsed)}" if original else "")
        )

    finally:
        try:
            await client.disconnect()
        finally:
            if local_session_file.exists():
                try:
                    shutil.copy2(local_session_file, persistent_session)
                except Exception:
                    pass



def run():
    print(f"✅ المحرك {ENGINE_BUNDLE_VERSION}")
    try:
        ensure_dependencies()
        import nest_asyncio
        nest_asyncio.apply()

        loop = asyncio.get_event_loop()
        loop.run_until_complete(app())

    except AppError as exc:
        if exc.details:
            try:
                BASE_DIR.mkdir(parents=True, exist_ok=True)
                ERROR_LOG.write_text(exc.details, encoding="utf-8")
            except Exception:
                pass
        print()
        print(f"❌ {exc.code}")
        print(exc.message)
        print("إذا استمرت المشكلة، أرسل كود الخطأ فقط.")

    except KeyboardInterrupt:
        print()
        print("⏹️ تم إيقاف التشغيل. الملفات الأصلية لم تتأثر.")

    except Exception:
        details = traceback.format_exc()
        try:
            BASE_DIR.mkdir(parents=True, exist_ok=True)
            ERROR_LOG.write_text(details, encoding="utf-8")
        except Exception:
            pass
        print()
        print("❌ E999")
        print("حصل خطأ غير متوقع. تم حفظ التفاصيل في Google Drive.")
        print("إذا استمرت المشكلة، أرسل كود الخطأ E999.")


if os.environ.get("TSC_TEST_MODE") != "1":
    run()
