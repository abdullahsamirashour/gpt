# -*- coding: utf-8 -*-
"""
Telegram Smart Compressor
Readable Colab engine. No embedded payloads and no user secrets in this file.
"""
from __future__ import annotations

import asyncio
import getpass
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

ENGINE_BUNDLE_VERSION = "5.2.0"
APP_NAME = "Smart Compressor"
WORKSPACE_TITLE = "📦 Smart Compressor"
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
    packages = ["telethon==1.45.0", "cryptg", "nest_asyncio"]
    needs_install = False
    try:
        import telethon  # noqa
        import cryptg  # noqa
        import nest_asyncio  # noqa
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


def first_time_setup(config):
    if config.get("api_id") and config.get("api_hash") and config.get("phone"):
        return config

    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("👋 إعداد أول مرة")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("تحتاج بيانات Telegram API الخاصة بك مرة واحدة فقط.")
    print("يمكنك الحصول عليها من my.telegram.org ثم Apps.")
    print()

    api_id_raw = input("اكتب API ID: ").strip()
    api_hash = getpass.getpass("اكتب API Hash: ").strip()
    phone = input("اكتب رقم الهاتف مع كود الدولة، مثال +2010...: ").strip()

    if not api_id_raw.isdigit() or not api_hash or not phone:
        raise AppError("E110", "بيانات Telegram غير مكتملة أو غير صحيحة.")

    config.update(
        api_id=int(api_id_raw),
        api_hash=api_hash,
        phone=phone,
    )
    save_json(CONFIG_PATH, config)
    print("✅ تم حفظ بيانات الإعداد في Google Drive الخاص بك.")
    return config


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
    if any(state.get(key) for key in ("processed_source_ids", "output_ids", "command_ids")):
        return state
    if not OLD_STATE_PATH.exists():
        return state

    old = load_json(OLD_STATE_PATH, {})
    old_channel = old.get(str(getattr(channel, "id", "")))
    if not old_channel:
        return state

    if isinstance(old_channel, list):
        state["processed_source_ids"] = [int(x) for x in old_channel]
    elif isinstance(old_channel, dict):
        state["processed_source_ids"] = [int(x) for x in old_channel.get("sources", [])]
        state["output_ids"] = [int(x) for x in old_channel.get("outputs", [])]

    save_json(STATE_PATH, state)
    print("✅ تم نقل سجل الملفات القديمة تلقائيًا بدون إعادة ضغطها.")
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


def run_ffmpeg(cmd, duration: float, label: str):
    full = [cmd[0], "-y", "-nostdin", "-hide_banner", "-loglevel", "error", *cmd[1:-1], "-progress", "pipe:1", "-nostats", cmd[-1]]
    proc = subprocess.Popen(
        full,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    last_percent = -10
    lines = []
    if proc.stdout:
        for raw in proc.stdout:
            line = raw.strip()
            lines.append(line)
            if line.startswith("out_time_ms=") and duration > 0:
                try:
                    seconds = int(line.split("=", 1)[1]) / 1_000_000
                    percent = min(100, int(seconds / duration * 100))
                    if percent >= last_percent + 10:
                        last_percent = percent
                        print(f"   {label}: {percent}%")
                except Exception:
                    pass
    code = proc.wait()
    if code != 0:
        tail = "\n".join(lines[-30:])
        raise AppError("E420", "فشل ضغط الملف.", tail)


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
        return

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
            run_ffmpeg(first, info.duration, "تحليل الفيديو")
            run_ffmpeg(second, info.duration, "ضغط الفيديو")
        finally:
            for p in TMP_DIR.glob(Path(passlog).name + "*"):
                try:
                    p.unlink()
                except Exception:
                    pass
        return

    if profile == "VIDEO_FAST":
        vf = video_filter(info, 1280, 720, 18)
        if nvenc:
            codec = [
                "-c:v", "h264_nvenc", "-preset", "p3",
                "-rc", "vbr", "-cq", "31", "-b:v", "0",
            ]
        else:
            codec = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "31"]
    else:
        vf = video_filter(info, 1280, 720, 15)
        if nvenc:
            codec = [
                "-c:v", "h264_nvenc", "-preset", "p4",
                "-rc", "vbr", "-cq", "30", "-b:v", "0",
            ]
        else:
            codec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "29"]

    cmd = [
        "ffmpeg", "-y", "-i", str(src), "-vf", vf,
        *codec,
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "48k", "-ac", "1",
        "-movflags", "+faststart",
        str(dst),
    ]
    run_ffmpeg(cmd, info.duration, "ضغط الفيديو")


def parse_rerun_command(text: str):
    text = (text or "").strip()
    if not text.startswith("🔁"):
        return None

    rest = text[1:].strip()
    if not rest:
        return {"profile": None, "target_mb": None}
    if "صوت" in rest:
        return {"profile": "AUDIO_TINY", "target_mb": None}
    if "أصغر" in rest or "اصغر" in rest:
        return {"profile": "VIDEO_SMALLEST", "target_mb": None}
    if "سريع" in rest:
        return {"profile": "VIDEO_FAST", "target_mb": None}
    if "متوازن" in rest:
        return {"profile": "VIDEO_BALANCED", "target_mb": None}

    match = re.search(r"(\d{1,5})", rest)
    if match:
        return {"profile": "VIDEO_TARGET_SIZE", "target_mb": int(match.group(1))}
    return {"profile": None, "target_mb": None}


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
    return bool(re.match(r"^✅\s*v\d+\s*•", text))


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


async def progress_download(current, total, started, label):
    now = time.time()
    if not hasattr(progress_download, "_last"):
        progress_download._last = 0
    if now - progress_download._last < 2 and current < total:
        return
    progress_download._last = now
    pct = (current / total * 100) if total else 0
    speed = current / max(now - started, 0.1) / 1024 / 1024
    print(f"   {label}: {pct:5.1f}% — {speed:.1f} MB/s")


def make_progress(label):
    started = time.time()
    last = {"t": 0.0}
    def callback(current, total):
        now = time.time()
        if now - last["t"] < 2 and current < total:
            return
        last["t"] = now
        pct = (current / total * 100) if total else 0
        speed = current / max(now - started, 0.1) / 1024 / 1024
        print(f"   {label}: {pct:5.1f}% — {speed:.1f} MB/s")
    return callback


async def ensure_workspace(client, config):
    from telethon import functions, types

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
        async for dialog in client.iter_dialogs():
            if (dialog.name or "").strip() == WORKSPACE_TITLE:
                channel = dialog.entity
                break

    if channel is None:
        print()
        print("📦 إنشاء مساحة العمل الخاصة بك...")
        result = await client(
            functions.channels.CreateChannelRequest(
                title=WORKSPACE_TITLE,
                about="مساحة خاصة لضغط ملفات الصوت والفيديو عبر Google Colab.",
                megagroup=False,
            )
        )
        channel = result.chats[0]
        created = True

    config["workspace_id"] = int(channel.id)
    save_json(CONFIG_PATH, config)

    instruction = None
    instruction_id = config.get("workspace_instruction_id")
    if instruction_id:
        try:
            instruction = await client.get_messages(channel, ids=int(instruction_id))
        except Exception:
            instruction = None

    if not instruction:
        instructions = (
            "📦 Smart Compressor\n\n"
            "الاستخدام العادي:\n"
            "1) ابعت أي ملف صوت أو فيديو هنا.\n"
            "2) افتح ملف Colab واضغط تشغيل.\n"
            "3) النتيجة هتظهر هنا تلقائيًا.\n\n"
            "إعادة معالجة نتيجة قديمة:\n"
            "🔁  = نفس الإعداد الحالي\n"
            "🔁 أصغر  = ضغط فيديو أقوى\n"
            "🔁 صوت  = استخراج صوت صغير جدًا\n"
            "🔁 80  = محاولة الوصول إلى 80 MB تقريبًا\n\n"
            "النتائج التي يصنعها البرنامج لا يعيد ضغطها تلقائيًا."
        )
        instruction = await client.send_message(channel, instructions)
        config["workspace_instruction_id"] = int(instruction.id)
        save_json(CONFIG_PATH, config)

    try:
        await client.pin_message(channel, instruction, notify=False)
    except Exception:
        pass

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
        print("✅ تم إنشاء قناة «📦 Smart Compressor» وحفظها للاستخدام القادم.")
    return channel, created


async def collect_queue(client, channel, state):
    processed = set(int(x) for x in state["processed_source_ids"])
    outputs = set(int(x) for x in state["output_ids"])
    commands_done = set(int(x) for x in state["command_ids"])

    messages = await client.get_messages(channel, limit=SCAN_LIMIT)
    by_id = {m.id: m for m in messages}
    jobs = []
    rerun_source_ids = set()

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


async def process_job(client, channel, state, job, nvenc: bool, index: int, total: int):
    from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeVideo

    msg = job["source"]
    command_msg = job["command"]

    parent_lineage = state.get("lineage", {}).get(str(msg.id), {})
    root_id = int(parent_lineage.get("root_id", msg.id))
    version = int(parent_lineage.get("version", 0)) + 1

    original_name = getattr(getattr(msg, "file", None), "name", None)
    ext = getattr(getattr(msg, "file", None), "ext", None) or ""
    name = clean_name(original_name, f"telegram_{msg.id}{ext}")
    src = TMP_DIR / f"{msg.id}_{int(time.time() * 1000)}_{name}"

    print()
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"📄 ملف {index} من {total}")
    print(f"الاسم: {name}")

    try:
        downloaded = await client.download_media(
            msg,
            file=str(src),
            progress_callback=make_progress("تنزيل"),
        )
        if not downloaded:
            raise AppError("E401", "تعذر تنزيل الملف من Telegram.")
        src = Path(downloaded)
        info = ffprobe(src)

        profile = resolve_profile(msg, job["profile"])
        target_mb = job["target_mb"]
        if profile == "VIDEO_TARGET_SIZE" and target_mb is None:
            target_mb = target_size_from_ui()

        if profile.startswith("VIDEO_") and not info.has_video:
            print("ℹ️ الملف صوتي، لذلك سيتم استخدام ضغط الصوت بدل إعداد الفيديو.")
            profile = "AUDIO_TINY"

        if profile == "AUDIO_TINY":
            dst = TMP_DIR / f"{src.stem}_compressed.ogg"
            print("🎧 المعالجة: صوت صغير جدًا")
            encode_audio(src, dst, info)
            out_info = ffprobe(dst)
            if dst.stat().st_size >= src.stat().st_size * 0.98:
                raise AppError("E430", "الملف مضغوط بالفعل تقريبًا، وإعادة الضغط لن توفر مساحة مفيدة.")

            title = Path(name).stem[:60]
            attrs = [
                DocumentAttributeAudio(
                    duration=max(0, int(out_info.duration)),
                    voice=False,
                    title=title,
                    performer="",
                )
            ]
            sent = await client.send_file(
                channel,
                str(dst),
                caption=f"✅ v{version} • صوت صغير جدًا\n{title}\n{human_size(src.stat().st_size)} → {human_size(dst.stat().st_size)}",
                attributes=attrs,
                mime_type="audio/ogg",
                voice_note=False,
                force_document=False,
                reply_to=msg.id,
                progress_callback=make_progress("رفع"),
            )
        else:
            dst = TMP_DIR / f"{src.stem}_compressed.mp4"
            labels = {
                "VIDEO_BALANCED": "فيديو متوازن",
                "VIDEO_FAST": "فيديو سريع",
                "VIDEO_SMALLEST": "أصغر حجم للفيديو",
                "VIDEO_TARGET_SIZE": f"حجم مستهدف: {target_mb} MB",
            }
            print(f"🎬 المعالجة: {labels.get(profile, profile)}")
            if profile in ("VIDEO_BALANCED", "VIDEO_FAST") and nvenc:
                print("⚡ سيتم استخدام كارت الشاشة في ترميز الفيديو.")
            encode_video(src, dst, info, profile, target_mb, nvenc)

            if dst.stat().st_size >= src.stat().st_size * 0.98:
                raise AppError("E430", "الملف مضغوط بالفعل تقريبًا، وإعادة الضغط لن توفر مساحة مفيدة.")

            out_info = ffprobe(dst)
            sent = await client.send_file(
                channel,
                str(dst),
                caption=f"✅ v{version} • {labels.get(profile, profile)}\n{Path(name).stem}\n{human_size(src.stat().st_size)} → {human_size(dst.stat().st_size)}",
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
                progress_callback=make_progress("رفع"),
            )

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
        saved = max(0, src.stat().st_size - dst.stat().st_size)
        print(f"✅ تم. التوفير: {human_size(saved)}")
        return {
            "ok": True,
            "original": src.stat().st_size,
            "final": dst.stat().st_size,
        }

    except AppError as exc:
        if command_msg and command_msg.id not in state["command_ids"]:
            state["command_ids"].append(int(command_msg.id))
            save_json(STATE_PATH, state)
        try:
            await client.send_message(
                channel,
                f"⚠️ {exc.code}\n{exc.message}",
                reply_to=msg.id,
            )
        except Exception:
            pass
        print(f"⚠️ {exc.code} — {exc.message}")
        if exc.details:
            ERROR_LOG.write_text(exc.details, encoding="utf-8")
        return {"ok": False, "original": 0, "final": 0}

    except Exception as exc:
        details = traceback.format_exc()
        ERROR_LOG.write_text(details, encoding="utf-8")
        try:
            await client.send_message(
                channel,
                "❌ E900\nحصل خطأ غير متوقع أثناء معالجة الملف. الملف الأصلي لم يتأثر.",
                reply_to=msg.id,
            )
        except Exception:
            pass
        print("❌ E900 — حصل خطأ غير متوقع. تم حفظ التفاصيل في Google Drive.")
        return {"ok": False, "original": 0, "final": 0}

    finally:
        for path in (src,):
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass
        for path in TMP_DIR.glob(f"{src.stem}_compressed.*"):
            try:
                path.unlink()
            except Exception:
                pass


async def app():
    mount_drive()

    from telethon import TelegramClient

    config = first_time_setup(load_config())
    state = normalize_state(load_json(STATE_PATH, default_state()))

    persistent_session = BASE_DIR / "telegram_user.session"
    local_session_base = "/content/telegram_user"
    local_session_file = Path(local_session_base + ".session")
    if persistent_session.exists() and not local_session_file.exists():
        shutil.copy2(persistent_session, local_session_file)

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
            await client.start(phone=config["phone"])
        except Exception as exc:
            raise AppError(
                "E120",
                "تعذر تسجيل الدخول إلى Telegram. راجع رقم الهاتف وبيانات API.",
                str(exc),
            )

        channel, _created = await ensure_workspace(client, config)
        state = migrate_old_state(channel, state)
        print(f"✅ مساحة العمل جاهزة: {WORKSPACE_TITLE}")

        try:
            client.session.save()
        except Exception:
            pass
        if local_session_file.exists():
            shutil.copy2(local_session_file, persistent_session)

        nvenc = detect_nvenc()
        if nvenc:
            print("⚡ تم اكتشاف كارت شاشة مناسب لتسريع الفيديو.")
        else:
            print("💻 سيتم استخدام المعالج في ضغط الفيديو.")

        jobs = await collect_queue(client, channel, state)
        save_json(STATE_PATH, state)

        if not jobs:
            print()
            print("✅ لا توجد ملفات جديدة تحتاج معالجة.")
            print("ابعت ملفًا في قناة «📦 Smart Compressor» ثم شغّل الخلية مرة أخرى.")
            return

        print()
        print(f"📥 الملفات المنتظرة: {len(jobs)}")
        started = time.time()
        results = []

        for idx, job in enumerate(jobs, 1):
            result = await process_job(
                client,
                channel,
                state,
                job,
                nvenc=nvenc,
                index=idx,
                total=len(jobs),
            )
            results.append(result)

        ok = sum(1 for r in results if r["ok"])
        failed = len(results) - ok
        original = sum(r["original"] for r in results)
        final = sum(r["final"] for r in results)
        saved_pct = ((original - final) / original * 100) if original else 0
        elapsed = int(time.time() - started)
        minutes, seconds = divmod(elapsed, 60)

        summary = (
            "✅ انتهت الدفعة\n\n"
            f"تم بنجاح: {ok}\n"
            f"فشل: {failed}\n"
            f"الحجم قبل: {human_size(original)}\n"
            f"الحجم بعد: {human_size(final)}\n"
            f"التوفير: {saved_pct:.1f}%\n"
            f"الوقت: {minutes} دقيقة و {seconds} ثانية"
        )
        await client.send_message(channel, summary)
        print()
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(summary)
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

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
