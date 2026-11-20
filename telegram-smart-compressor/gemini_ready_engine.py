# -*- coding: utf-8 -*-
"""Gemini Ready: source-agnostic, size-verified audio batches for Media Lite.

This is an orchestration layer; it reuses the existing Media Lite encoder,
Telegram transfer client and Internet downloader. No credentials live here.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = "abdullahsamirashour/gpt"
COMPONENT_DIR = "telegram-smart-compressor"
WORK_DIR = Path("/content/gemini_ready_work")
AUDIO_SUFFIXES = {".ogg", ".oga", ".opus", ".mp3", ".m4a", ".aac", ".wav", ".flac", ".wma", ".aiff", ".amr"}
VIDEO_SUFFIXES = {".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi", ".ts", ".3gp"}
OUTPUT_SUFFIXES = {"opus": ".ogg", "aac": ".m4a", "mp3": ".mp3"}
# The user wants *less than* 9 MB (decimal), not 9 MiB. Safety margin for muxing.
MIN_HEADROOM = 80_000
MAX_BATCH = 200
MAX_LINKS = 100
STATE_VERSION = 1
# Fast paths require validated codec and container. The established encoder
# handles any unsupported, ambiguous or uninspectable media.
SMART_DIRECT_CODECS = {
    ".mp3": {"mp3"},
    ".ogg": {"opus", "vorbis"},
    ".m4a": {"aac"},
    ".wav": {"pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le"},
    ".flac": {"flac"},
    ".aiff": {"pcm_s16be", "pcm_s24be"},
}
SMART_SPLIT_SUFFIXES = {".mp3", ".ogg", ".m4a"}
SMART_MAX_COPY_PARTS = 12
SMART_MODES = {"smart", "preserve", "always"}



def _hash(value: str, size: int = 24) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:size]


def _urls(text: str, limit: int = MAX_LINKS) -> list[str]:
    values = re.split(r"[\s,،;؛]+", str(text or "").strip())
    unique, seen = [], set()
    for value in values:
        value = value.strip()
        if not value:
            continue
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("الروابط لازم تبدأ بـ https:// وتكون روابط صحيحة.")
        if value not in seen:
            unique.append(value)
            seen.add(value)
    if not unique:
        raise ValueError("اكتب رابطًا واحدًا على الأقل.")
    if len(unique) > limit:
        raise ValueError(f"الحد الأقصى في الدفعة {limit} رابط.")
    return unique


def _clean_name(value: str, fallback: str = "Recording") -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", str(value or ""))
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:150].strip(" .") or fallback


def _label(value: str, fallback: str) -> str:
    return re.sub(r"\s+", "_", _clean_name(value, fallback))[:55]


def _is_media(name: str, mime: str = "", allow_unknown: bool = False) -> bool:
    mime = (mime or "").lower()
    if mime.startswith("application/vnd.google-apps."):
        return False
    if mime.startswith(("audio/", "video/")):
        return True
    if mime in ("application/ogg", "application/x-ogg"):
        return True
    if Path(str(name)).suffix.lower() in AUDIO_SUFFIXES | VIDEO_SUFFIXES:
        return True
    return bool(allow_unknown and (not mime or mime == "application/octet-stream"))


def _required_part_count(size: int, max_bytes: int, duration: float = 0.0, max_seconds: int = 0) -> int:
    if max_bytes < 10_000:
        raise ValueError("الحد الأقصى صغير جدًا.")
    # Strictly below max_bytes, including after OGG/MP3/M4A container overhead.
    by_size = 1 if size < max_bytes else math.ceil(size / (max_bytes - min(MIN_HEADROOM, max_bytes // 50)))
    by_duration = math.ceil(duration / max_seconds) if max_seconds > 0 and duration > 0 else 1
    return max(1, by_size, by_duration)


def _duration(path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        text=True, capture_output=True, check=True,
    )
    duration = float(proc.stdout.strip() or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError(f"لا يمكن تحديد مدة الصوت: {path.name}")
    return duration


def _packet_cut_times(src: Path, count: int, duration: float, time_limit: int) -> list[float]:
    if count <= 1:
        return []
    if time_limit:
        return [duration * i / count for i in range(1, count)]
    # Packet bytes are a better proxy for balanced file sizes than clock time for
    # VBR recordings. No lossy encoding is done here: FFmpeg copies whole packets.
    cmd = ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_packets",
           "-show_entries", "packet=pts_time,size", "-of", "csv=p=0", str(src)]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    records = []
    try:
        assert process.stdout is not None
        for raw in process.stdout:
            parts = raw.strip().split(",")
            if len(parts) < 2:
                continue
            try:
                pts, size = float(parts[0]), int(parts[1])
            except ValueError:
                continue
            if math.isfinite(pts) and pts >= 0 and size > 0:
                records.append((pts, size))
        _err = process.stderr.read() if process.stderr is not None else ""
        code = process.wait()
        if code:
            raise RuntimeError(f"ffprobe packet scan failed: {_err[-350:]}")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
    if len(records) < count * 2:
        return [duration * i / count for i in range(1, count)]
    total = sum(size for _pts, size in records)
    target = total / count
    cuts, cumulative, next_cut = [], 0, 1
    for pts, size in records:
        cumulative += size
        if next_cut < count and cumulative >= target * next_cut:
            if pts > 0 and (not cuts or pts > cuts[-1]):
                cuts.append(pts)
                next_cut += 1
    if len(cuts) != count - 1:
        return [duration * i / count for i in range(1, count)]
    return cuts


def split_audio(src: Path, dst_dir: Path, max_bytes: int = 9_000_000,
                max_minutes: int = 0, max_attempts: int = 40) -> list[Path]:
    """Split on packet boundaries; verify each result and retry until every part fits.

    The source is already encoded to the chosen format. A short recording is
    returned untouched. No segment is re-encoded and all segments are contiguous.
    """
    src = Path(src)
    if not src.is_file() or src.stat().st_size <= 0:
        raise ValueError("ملف الصوت المضغوط غير موجود أو فارغ.")
    max_bytes = int(max_bytes)
    if max_bytes <= 10_000:
        raise ValueError("Max part size must exceed 10 KB.")
    if not 0 <= int(max_minutes) <= 180:
        raise ValueError("حد المدة يكون 0 إلى 180 دقيقة.")
    duration = _duration(src)
    seconds_limit = int(max_minutes) * 60
    count = _required_part_count(src.stat().st_size, max_bytes, duration, seconds_limit)
    if count == 1:
        return [src]
    dst_dir = Path(dst_dir)
    fmt = {".ogg": "ogg", ".mp3": "mp3", ".m4a": "mp4"}.get(src.suffix.lower())
    if fmt is None:
        raise ValueError("صيغة التقسيم غير مدعومة.")
    dst_dir.mkdir(parents=True, exist_ok=True)
    for attempt in range(max_attempts):
        for old in dst_dir.glob("segment_*" + src.suffix):
            old.unlink()
        cuts = _packet_cut_times(src, count, duration, seconds_limit)
        if len(cuts) != count - 1 or len(set(cuts)) != len(cuts):
            raise RuntimeError("لا يمكن إنشاء حدود تقسيم غير متداخلة للصوت.")
        mask = str(dst_dir / f"segment_%04d{src.suffix}")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-i", str(src), "-map", "0:a:0", "-vn", "-c:a", "copy", "-f", "segment",
               "-segment_format", fmt, "-segment_times", ",".join(f"{t:.6f}" for t in cuts),
               "-reset_timestamps", "1", mask]
        result = subprocess.run(cmd, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError(f"فشل تقسيم الصوت بدون إعادة ضغط: {result.stderr[-600:]}")
        parts = sorted(dst_dir.glob("segment_*" + src.suffix))
        sizes_ok = len(parts) == count and all(0 < part.stat().st_size < max_bytes for part in parts)
        durations = []
        if sizes_ok:
            try:
                durations = [_duration(part) for part in parts]
            except (RuntimeError, ValueError):
                sizes_ok = False
        times_ok = not seconds_limit or all(d <= seconds_limit + 0.10 for d in durations)
        # Small encoder delay / packet-edge variation can add a few milliseconds.
        coverage_ok = bool(durations) and abs(sum(durations) - duration) <= max(1.0, count * 0.08)
        if sizes_ok and times_ok and coverage_ok:
            return parts
        print(f"↪️ تقسيم {count} جزء لم يحقق كل الحدود؛ إعادة التوزيع ({attempt + 1}/{max_attempts}).")
        count += 1
    raise RuntimeError("تعذر الوصول إلى أجزاء أقل من الحد المحدد؛ لم تُرفع أي ملفات غير مكتملة.")


def _state_path(transfer) -> Path:
    return transfer.BASE_DIR / "Media_Lite_Studio" / "Gemini_Ready" / "state_v1.json"


def _read_state(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(state, dict) and state.get("version") == STATE_VERSION and isinstance(state.get("items"), dict):
            return state
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": STATE_VERSION, "items": {}}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _component(commit: str, name: str):
    if not re.fullmatch(r"[a-f0-9]{40}", str(commit or "")):
        raise ValueError("يجب تحميل مكونات Media Lite من Git commit ثابت.")
    cache = Path("/content") / f"gemini_ready_{name}"
    marker = Path(str(cache) + ".commit")
    code = None
    if cache.exists() and marker.exists() and marker.read_text(encoding="ascii") == commit:
        code = cache.read_text(encoding="utf-8")
    if not code:
        url = f"https://raw.githubusercontent.com/{REPO}/{commit}/{COMPONENT_DIR}/{name}"
        request = urllib.request.Request(url, headers={"User-Agent": "Gemini-Ready-Colab"})
        with urllib.request.urlopen(request, timeout=60) as response:
            code = response.read().decode("utf-8")
        if len(code) < 10000:
            raise RuntimeError(f"المكوّن {name} غير مكتمل.")
        temp = Path(str(cache) + ".tmp")
        temp.write_text(code, encoding="utf-8")
        os.replace(temp, cache)
        marker.write_text(commit, encoding="ascii")
    module_name = "gemini_ready_reuse_" + name.replace(".", "_")
    module = types.ModuleType(module_name)
    module.__file__ = str(cache)
    sys.modules[module_name] = module
    exec(compile(code, str(cache), "exec"), module.__dict__)
    return module


def _settings(engine, preset: str, advanced: bool, audio_codec: str,
              audio_kbps: int, channels: str, sample_rate: str, noise: str):
    if preset not in engine.STUDIO_AUDIO_PRESETS:
        raise ValueError("Audio preset غير معروف.")
    config = dict(engine.STUDIO_FACTORY_SETTINGS)
    config["source"] = "audio"
    config["audio_preset"] = preset
    config.update(engine.STUDIO_AUDIO_PRESETS[preset])
    if advanced:
        config.update({"audio_codec": audio_codec, "audio_kbps": int(audio_kbps),
                       "audio_channels": channels, "audio_sample_rate": str(sample_rate),
                       "audio_noise": noise})
    config["skip_if_no_saving"] = False  # Always honor output format and bitrate.
    return engine.normalize_studio_settings(config)


def _drive_jobs(transfer, service, source: str, text: str, recursive: bool) -> list[dict]:
    if source == "drive_folder":
        raw = [text.strip()]
        if not raw[0]:
            raise ValueError("الصق رابط فولدر Google Drive.")
    else:
        raw = _urls(text)
    jobs, seen = [], set()
    for link in raw:
        root_id = transfer.parse_drive_id(link)
        root = transfer.get_drive_metadata(service, root_id)
        if source == "drive_folder" and root.get("mimeType") != "application/vnd.google-apps.folder":
            raise ValueError("هذا الرابط لملف، اختار Drive Links للملفات المنفردة.")
        metas = transfer.list_drive_sources(service, root_id, recursive)
        for meta in metas:
            fid = meta["id"]
            if fid in seen:
                continue
            seen.add(fid)
            if not _is_media(meta["name"], meta.get("mimeType", ""), allow_unknown=source == "drive_links"):
                print(f"↩️ تخطي غير صوت/فيديو: {_clean_name(meta['name'])}")
                continue
            signature = f"{fid}:{meta.get('md5Checksum') or meta.get('modifiedTime', '')}:{meta.get('size', '')}"
            jobs.append({"kind": "drive", "key": "drive:" + _hash(signature),
                         "name": _clean_name(meta["name"]), "size": int(meta.get("size") or 0),
                         "meta": meta})
    return jobs


def _internet_jobs(text: str) -> list[dict]:
    return [{"kind": "internet", "key": "web:" + _hash(url),
             "name": _clean_name(Path(urllib.parse.urlsplit(url).path).stem, "Internet_Recording"),
             "size": 0, "url": url} for url in _urls(text)]


def _run_folder(transfer, service, batch: str) -> tuple[str, str]:
    root = "root"
    cache = {}
    for part in ("Telegram_Extreme_Compressor", "Media_Lite_Studio", "Gemini_Ready"):
        root = transfer.ensure_child_folder(service, root, part, cache)
    today = datetime.now(ZoneInfo("Africa/Cairo")).strftime("%Y-%m-%d")
    prefix = f"{today}_{_label(batch, 'Audio')}"
    for number in range(1, 1000):
        title = prefix if number == 1 else f"{prefix}_{number:02d}"
        if not transfer.find_child_folder(service, root, title):
            return transfer.ensure_child_folder(service, root, title, cache), title
    raise RuntimeError("تعذر اختيار اسم فريد لفولدر النتائج.")


def _drive_link(file_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{file_id}"


def _fingerprint(path: Path) -> str:
    digest = hashlib.md5()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verified_remote(service, remote: dict, part: Path, folder_id: str) -> bool:
    if not remote or not remote.get("id"):
        return False
    expected_size = part.stat().st_size
    meta = service.files().get(
        fileId=remote["id"], fields="id,size,md5Checksum,parents,trashed", supportsAllDrives=True,
    ).execute(num_retries=3)
    if meta.get("trashed") or folder_id not in meta.get("parents", []):
        return False
    return (int(meta.get("size") or -1) == expected_size
            and meta.get("md5Checksum") == _fingerprint(part))


def _record_verified(service, record: dict) -> bool:
    folder = record.get("folder_id")
    parts = record.get("parts") or []
    if not folder or not parts:
        return False
    for p in parts:
        if not p.get("id") or not p.get("md5") or not p.get("size"):
            return False
        try:
            meta = service.files().get(
                fileId=p["id"], fields="size,md5Checksum,parents,trashed", supportsAllDrives=True,
            ).execute(num_retries=3)
        except Exception:
            return False
        if (meta.get("trashed") or folder not in meta.get("parents", [])
                or int(meta.get("size") or 0) != p["size"]
                or meta.get("md5Checksum") != p["md5"]):
            return False
    return True



def _publish_manifest(service, state: dict, folder_id: str) -> None:
    """Keep a small, human-readable inventory alongside the audio files."""
    from googleapiclient.http import MediaFileUpload

    entries = [item for item in state["items"].values()
               if item.get("done") and item.get("folder_id") == folder_id]
    if not entries:
        return
    entries.sort(key=lambda item: (item.get("name", ""), item.get("completed_at", "")))
    data = {"format": "gemini-ready-v1", "generated_at": datetime.now(ZoneInfo("Africa/Cairo")).isoformat(),
            "files": [{"original_name": item["name"],
                       "outputs": [{"name": part["name"], "bytes": part["size"],
                                    "duration_seconds": part["duration"]}
                                   for part in item.get("parts", [])]}
                      for item in entries]}
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    local = WORK_DIR / ("manifest_" + _hash(folder_id) + ".json")
    local.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    safe_folder = folder_id.replace("'", "\\'")
    listing = service.files().list(
        q=f"'{safe_folder}' in parents and name = 'manifest.json' and trashed = false",
        fields="files(id)", pageSize=10, supportsAllDrives=True,
    ).execute(num_retries=3)
    media = MediaFileUpload(str(local), mimetype="application/json", resumable=False)
    found = listing.get("files", [])
    if found:
        output = service.files().update(
            fileId=found[0]["id"], media_body=media,
            fields="id,size,md5Checksum", supportsAllDrives=True,
        ).execute(num_retries=3)
    else:
        output = service.files().create(
            body={"name": "manifest.json", "parents": [folder_id], "mimeType": "application/json"},
            media_body=media, fields="id,size,md5Checksum", supportsAllDrives=True,
        ).execute(num_retries=3)
    if int(output.get("size") or -1) != local.stat().st_size or output.get("md5Checksum") != _fingerprint(local):
        raise RuntimeError("فشل التحقق من manifest.json على Drive.")
    print(f"📋 manifest.json: {len(entries)} ريكورد مكتمل.")

def _web_media(web, url: str, stage: Path) -> Path:
    stage.mkdir(parents=True, exist_ok=True)
    ext = Path(urllib.parse.urlsplit(url).path).suffix.lower()
    if ext in AUDIO_SUFFIXES:
        requests = web._install_if_needed("requests", "requests")
        original_name = urllib.parse.unquote(Path(urllib.parse.urlsplit(url).path).name)
        safe_original = _clean_name(original_name, "internet" + ext)
        target = stage / safe_original
        if target.exists() and target.stat().st_size:
            return target
        partial = stage / ("download" + ext + ".part")
        partial.unlink(missing_ok=True)
        with requests.get(url, stream=True, timeout=(20, 180), allow_redirects=True,
                          headers={"User-Agent": web.USER_AGENT}) as response:
            response.raise_for_status()
            ctype = (response.headers.get("Content-Type") or "").lower()
            if "text/html" in ctype or "application/json" in ctype:
                raise RuntimeError("رابط الصوت رجّع صفحة ويب بدل ملف صوت.")
            with partial.open("wb") as out:
                for piece in response.iter_content(chunk_size=1024 * 1024):
                    if piece:
                        out.write(piece)
        if not partial.exists() or partial.stat().st_size == 0:
            raise RuntimeError("ملف الإنترنت فارغ.")
        os.replace(partial, target)
        return target
    # Reuse existing yt-dlp, direct media, browser fallback and progress logic.
    try:
        files = web._download_url(url, stage, "الأصلية", True, False)
    except Exception as exc:
        # The older web-video engine intentionally enumerates videos only. An
        # audio-only yt-dlp result is supported here without modifying it.
        candidates = [p for p in stage.iterdir() if p.suffix.lower() in AUDIO_SUFFIXES and p.stat().st_size]
        if candidates:
            return candidates[0]
        try:
            yt_dlp = web._install_if_needed("yt_dlp", "yt-dlp")
            with yt_dlp.YoutubeDL({"format": "bestaudio/best", "outtmpl": str(stage / "%(title).120B-%(id)s.%(ext)s"),
                                   "noplaylist": True, "quiet": True, "no_warnings": True,
                                   "continuedl": True, "retries": 5}) as ydl:
                ydl.download([url])
            candidates = [p for p in stage.iterdir() if p.suffix.lower() in AUDIO_SUFFIXES | VIDEO_SUFFIXES and p.stat().st_size]
            if candidates:
                return sorted(candidates)[0]
        except Exception:
            pass
        raise RuntimeError("تعذر تنزيل محتوى صوتي من الموقع أو الرابط غير مسموح بتنزيله.") from exc
    if not files:
        raise RuntimeError("لم يتم العثور على صوت أو فيديو في الرابط.")
    return Path(files[0])


def _download_job(job: dict, stage: Path, transfer, service, web,
                  client=None, channel=None, downloader=None) -> Path:
    if job["kind"] == "internet":
        return _web_media(web, job["url"], stage)
    name = _clean_name(job["name"])
    dest = stage / ("source" + (Path(name).suffix or ".bin"))
    expected = job["size"]
    if expected and dest.exists() and dest.stat().st_size == expected:
        print("♻️ تنزيل محلي مكتمل من محاولة سابقة.")
        return dest
    dest.unlink(missing_ok=True)
    if expected and shutil.disk_usage(stage).free < expected + 256 * 1024 * 1024:
        raise OSError("مساحة Colab غير كافية لتنزيل الريكورد.")
    if job["kind"] == "drive":
        transfer.download_drive_file(service, job["meta"], dest)
    else:
        if client is None or channel is None:
            raise RuntimeError("جلسة Telegram غير متصلة.")
        asyncio.get_event_loop()  # Explicitly handled in async runner below.
        raise RuntimeError("Telegram downloads must run through the async entry point.")
    if not dest.exists() or dest.stat().st_size <= 0:
        raise RuntimeError("فشل تنزيل الملف من المصدر.")
    return dest


def _output_names(stem: str, codec: str, count: int,
                  output_suffix: str = "") -> list[str]:
    suffix = output_suffix or OUTPUT_SUFFIXES[codec]
    stem = _clean_name(stem)
    if count == 1:
        return [stem + suffix]
    width = max(2, len(str(count)))
    return [f"{stem}-pt{i:0{width}d}{suffix}" for i in range(1, count + 1)]


def _settings_signature(settings: dict, max_mb: float, max_minutes: int,
                        mode: str = "always", advanced_audio: bool = False) -> str:
    keys = ("audio_preset", "audio_codec", "audio_kbps", "audio_channels", "audio_sample_rate", "audio_noise")
    details = {key: settings[key] for key in keys}
    details["max_bytes"] = int(float(max_mb) * 1_000_000)
    details["max_minutes"] = int(max_minutes)
    # Modes have different output semantics; never reuse the wrong receipts.
    details["preparation_mode"] = mode
    details["advanced_audio"] = bool(advanced_audio)
    details["policy_version"] = 2
    return _hash(json.dumps(details, sort_keys=True, ensure_ascii=True))



def _media_probe(path: Path) -> dict:
    """Probe the real codec, number of streams and total duration."""
    cmd = ["ffprobe", "-v", "error", "-show_entries",
           "stream=index,codec_name,codec_type:format=duration",
           "-of", "json", str(path)]
    result = subprocess.run(cmd, text=True, capture_output=True,
                            check=True, timeout=45)
    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    audio = [item for item in streams if item.get("codec_type") == "audio"]
    video = [item for item in streams if item.get("codec_type") == "video"]
    seconds = float((data.get("format") or {}).get("duration") or 0)
    if len(audio) != 1 or not math.isfinite(seconds) or seconds <= 0:
        return {"valid": False}
    return {"valid": True, "codec": audio[0].get("codec_name", "").lower(),
            "audio_index": int(audio[0]["index"]),
            "video": bool(video), "duration": seconds}


def _copy_container_for_codec(codec: str) -> tuple[str, str] | None:
    return {"mp3": (".mp3", "mp3"), "aac": (".m4a", "ipod"),
            "opus": (".ogg", "ogg"), "vorbis": (".ogg", "ogg")}.get(codec)


def _fast_prepare_audio(source: Path, stage: Path, max_bytes: int,
                        max_minutes: int, mode: str, advanced_audio: bool,
                        output_kbps: int) -> tuple[Path | None, str]:
    """Select source/pass-through/remux without any lossy encoding.

    Advanced Audio explicitly requests format/bitrate; it takes precedence.
    """
    if mode == "always":
        return None, "Always Compress محدد"
    if advanced_audio:
        return None, "Advanced Audio محدد؛ نحترم إعدادات التحويل اليدوية"
    try:
        info = _media_probe(source)
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
        return None, "تعذر التحقق من صيغة/مدة الصوت"
    if not info.get("valid"):
        return None, "صوت غير مؤهل للمسار السريع أو متعدد المسارات"
    codec = info["codec"]
    suffix = source.suffix.lower()
    duration = info["duration"]
    need_split = (source.stat().st_size >= max_bytes
                  or (max_minutes > 0 and duration > max_minutes * 60))
    direct = not info["video"] and codec in SMART_DIRECT_CODECS.get(suffix, set())
    can_split_direct = suffix in SMART_SPLIT_SUFFIXES
    target_path = source

    if not direct or (need_split and not can_split_direct):
        # Copy only a single verified compatible audio stream. If there are
        # multiple audio streams, rely on the original encoder stream selector.
        container = _copy_container_for_codec(codec)
        if container is None:
            return None, "الصيغة لا تسمح بالاستخراج السريع المتوافق"
        ext, fmt = container
        dst = stage / ("extracted" + ext)
        temp = stage / ("extract_pending" + ext)
        temp.unlink(missing_ok=True)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-i", str(source), "-map", f"0:{info['audio_index']}",
               "-vn", "-sn", "-dn", "-c:a", "copy", "-f", fmt, str(temp)]
        try:
            proc = subprocess.run(cmd, text=True, capture_output=True, timeout=300)
            if proc.returncode or not temp.is_file() or not temp.stat().st_size:
                return None, "Stream Copy غير متاح؛ هنستخدم الضغط المعتاد"
            remuxed = _media_probe(temp)
            if (not remuxed.get("valid") or remuxed["video"]
                    or remuxed["codec"] != codec
                    or abs(remuxed["duration"] - duration) > max(1.5, duration * 0.01)):
                return None, "فشل التحقق من استخراج الصوت بدون ضغط"
            os.replace(temp, dst)
            target_path = dst
        except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
            return None, "تعذر استخراج الصوت بدون ضغط"
        finally:
            temp.unlink(missing_ok=True)

    estimated_parts = _required_part_count(
        target_path.stat().st_size, max_bytes, duration, max_minutes * 60)
    bitrate_kbps = (target_path.stat().st_size * 8) / (1000 * duration)
    if (mode == "smart" and estimated_parts > SMART_MAX_COPY_PARTS
            and bitrate_kbps > max(64, output_kbps * 1.7)):
        return None, f"{estimated_parts} أجزاء متوقعة؛ ضغط واحد أنسب للدفعة الكبيرة"
    return target_path, ("الصوت الأصلي جاهز" if target_path == source
                         else "استخراج صوت سريع بدون إعادة ترميز")


def _process(job: dict, transfer, engine, web, service, state: dict, state_file: Path,
             settings: dict, setting_id: str, max_bytes: int, max_minutes: int,
             folder_id: str, folder_name: str, stem: str, source_path: Path,
             mode: str = "always", advanced_audio: bool = False) -> dict:
    if mode not in SMART_MODES:
        raise ValueError("Audio Preparation Mode غير معروف.")
    uid = _hash(job["key"] + ":" + setting_id)
    record = state["items"].setdefault(uid, {})
    record.update({"source_key": job["key"], "name": job["name"], "folder_id": folder_id,
                   "folder_name": folder_name, "stem": stem, "settings": setting_id,
                   "preparation_mode": mode, "done": False})
    _save_state(state_file, state)
    stage = source_path.parent
    chosen, reason = _fast_prepare_audio(
        source_path, stage, max_bytes, max_minutes, mode,
        advanced_audio, int(settings["audio_kbps"]))

    def encode() -> Path:
        output = stage / ("compressed" + OUTPUT_SUFFIXES[settings["audio_codec"]])
        if output.exists() and output.stat().st_size:
            try:
                info = engine.ffprobe(output)
                if not info.has_audio or info.duration <= 0:
                    raise ValueError("invalid cached compressed file")
            except Exception:
                output.unlink(missing_ok=True)
        if not output.exists():
            info = engine.ffprobe(source_path)
            if not info.has_audio or info.duration <= 0:
                raise RuntimeError("الملف المصدر لا يحتوي مسار صوت صالحًا.")
            print(f"🎚️ Encode: {settings['audio_codec'].upper()} {settings['audio_kbps']} kbps")
            pending = stage / ("pending" + output.suffix)
            pending.unlink(missing_ok=True)
            try:
                engine.encode_audio(source_path, pending, info, settings=settings)
                if not pending.is_file() or pending.stat().st_size == 0:
                    raise RuntimeError("فشل إنشاء ملف صوتي مضغوط.")
                os.replace(pending, output)
            finally:
                pending.unlink(missing_ok=True)
        return output

    if chosen is None:
        print(f"🎚️ {reason} → Encode")
        output = encode()
        preparation = "encoded"
    else:
        output = chosen
        preparation = "kept" if output == source_path else "remuxed"
        print(f"⚡ {reason}: {output.stat().st_size / 1_000_000:.2f} MB")

    try:
        parts = split_audio(output, stage / "segments", max_bytes, max_minutes)
    except (RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        # A copy-only split may fail for unusual packet timestamps; fall back
        # to the established encoder rather than silently dropping a lecture.
        if preparation == "encoded":
            raise
        print(f"↪️ تقسيم بدون ضغط لم ينجح ({type(exc).__name__})؛ إعادة ترميز آمنة.")
        output = encode()
        preparation = "encoded"
        parts = split_audio(output, stage / "segments", max_bytes, max_minutes)

    names = _output_names(stem, settings["audio_codec"], len(parts), output.suffix.lower())
    print(f"✂️ ناتج {output.stat().st_size / 1_000_000:.2f} MB → {len(parts)} ملف")
    record["preparation"] = preparation
    receipts = []
    for i, (part, name) in enumerate(zip(parts, names), 1):
        key = f"GR{uid}P{i:04d}"
        existing = transfer.existing_drive_copy(service, folder_id, key, part.stat().st_size)
        if existing:
            if not _verified_remote(service, existing, part, folder_id):
                raise RuntimeError(f"ملف موجود على Drive لكنه غير مطابق: {name}")
            result = existing
            print(f"♻️ الجزء {i}/{len(parts)} موجود ومتحقق منه: {name}")
        else:
            result = transfer.upload_drive_file(service, part, folder_id, name, key)
            if not _verified_remote(service, result, part, folder_id):
                raise RuntimeError(f"التحقق بعد الرفع فشل: {name}")
            print(f"✅ Drive {i}/{len(parts)}: {name} ({part.stat().st_size / 1_000_000:.2f} MB)")
        receipts.append({"id": result["id"], "name": name, "size": part.stat().st_size,
                         "md5": _fingerprint(part), "duration": round(_duration(part), 3)})
        record["parts"] = receipts
        _save_state(state_file, state)
    record["done"] = True
    record["completed_at"] = datetime.now(ZoneInfo("Africa/Cairo")).isoformat(timespec="seconds")
    _save_state(state_file, state)
    return record


def _telegram_jobs(message_list, channel_id: int) -> list[dict]:
    out = []
    for msg in message_list:
        file = getattr(msg, "file", None)
        if not file:
            continue
        name = str(getattr(file, "name", "") or f"Telegram_{msg.id}")
        mime = str(getattr(file, "mime_type", "") or "")
        if not _is_media(name, mime, allow_unknown=bool(getattr(msg, "audio", None) or getattr(msg, "voice", None))):
            continue
        size = int(getattr(file, "size", 0) or 0)
        if not size:
            continue
        if not Path(name).suffix:
            name += mimetypes.guess_extension(mime) or ".bin"
        key = f"telegram:{channel_id}:{msg.id}:{size}"
        out.append({"kind": "telegram", "key": key, "name": _clean_name(name),
                    "size": size, "message": msg})
    return out


def _done(state: dict, job: dict, setting_id: str) -> bool:
    uid = _hash(job["key"] + ":" + setting_id)
    return bool(state["items"].get(uid, {}).get("done"))


def _done_in_any_mode(state: dict, job: dict) -> bool:
    """Telegram 'new' means new source, not new compression settings.

    Switching the default to Smart must not accidentally reprocess or delete
    the existing Telegram archive that was completed with the old mode.
    """
    return any(record.get("done") and record.get("source_key") == job["key"]
               for record in state.get("items", {}).values())


def _delete_allowed(channel) -> bool:
    rights = getattr(channel, "admin_rights", None)
    return bool(getattr(channel, "creator", False) or getattr(rights, "delete_messages", False))


async def _telegram_run(transfer, web, engine, service, state, state_file, config) -> dict:
    from telethon import TelegramClient

    account = transfer.load_account_config()
    credentials = transfer.load_json(transfer.TRANSFER_CONFIG, {})
    if transfer.PERSISTENT_SESSION.exists():
        shutil.copy2(transfer.PERSISTENT_SESSION, transfer.LOCAL_SESSION)
    else:
        transfer.LOCAL_SESSION.unlink(missing_ok=True)
    client = TelegramClient(str(transfer.LOCAL_SESSION.with_suffix("")), int(account["api_id"]),
                            account["api_hash"], connection_retries=5, request_retries=5)
    try:
        await transfer.authorize_telegram(client, account["phone"], config["login_method"])
        me = await client.get_me()
        configured_phone = re.sub(r"\D", "", str(account["phone"]))
        active_phone = re.sub(r"\D", "", str(getattr(me, "phone", "")))
        if active_phone and configured_phone != active_phone:
            raise RuntimeError("جلسة Telegram تخص حسابًا مختلفًا؛ توقف التشغيل.")
        channel = await web._existing_transfer_channel(client, transfer, credentials)
        if config["delete_tg"] and not config["preview"] and not _delete_allowed(channel):
            raise PermissionError("ليس لديك صلاحية حذف رسائل قناة Media Lite.")
        messages = []
        scan_limit = 3000
        async for msg in client.iter_messages(channel, limit=scan_limit):
            if getattr(msg, "file", None):
                messages.append(msg)
        jobs = _telegram_jobs(messages, channel.id)
        if config["tg_mode"] == "new" and not config["force"]:
            jobs = [j for j in jobs if not _done_in_any_mode(state, j)
                    or state["items"].get(_hash(j["key"] + ":" + config["setting_id"]), {}).get("delete_pending")]
        elif config["tg_mode"] not in {"new", "recent"}:
            raise ValueError("Telegram mode غير معروف.")
        # Telegram returns newest first; select recent N, then process old→new.
        if config["max_files"]:
            jobs = jobs[:config["max_files"]]
        jobs.reverse()
        print(f"📣 Telegram: {len(jobs)} تسجيل مؤهل للمعالجة.")
        if config["preview"]:
            _preview(jobs, state, config["setting_id"])
            return {"ok": 0, "failed": 0, "skipped": 0, "folders": []}
        downloader = transfer.TelegramParallelDownloader(client)
        async def download_telegram(job, stage):
            expected = job["size"]
            dest = stage / ("source" + (Path(job["name"]).suffix or ".bin"))
            if dest.exists() and dest.stat().st_size == expected:
                return dest
            dest.unlink(missing_ok=True)
            if shutil.disk_usage(stage).free < expected + 256 * 1024 * 1024:
                raise OSError("مساحة Colab غير كافية لتنزيل ملف Telegram.")
            await transfer.fetch_telegram_message(client, channel, job["message"], dest, expected, downloader)
            return dest
        return await _process_many(jobs, transfer, engine, web, service, state, state_file,
                                   config, download_telegram, client, channel)
    finally:
        try:
            await client.disconnect()
        finally:
            if transfer.LOCAL_SESSION.exists():
                shutil.copy2(transfer.LOCAL_SESSION, transfer.PERSISTENT_SESSION)


def _preview(jobs: list[dict], state: dict, setting_id: str) -> None:
    print(f"👁️ معاينة: {len(jobs)} ملف/رابط؛ لن يتم تنزيل أو ضغط أو إنشاء فولدر أو حذف أي شيء.")
    for index, job in enumerate(jobs[:50], 1):
        status = "✅ سبق تجهيزه" if _done(state, job, setting_id) else "🆕 جديد"
        size = f"{job['size']/1_000_000:.2f} MB" if job["size"] else "الحجم بعد التنزيل"
        print(f"  {index:>2}. {job['name']} — {size} — {status}")
    if len(jobs) > 50:
        print(f"  ... وأكثر من ذلك: {len(jobs) - 50} ملف.")


async def _process_many(jobs, transfer, engine, web, service, state, state_file,
                        config, download_tg=None, tg_client=None, tg_channel=None):
    ok = failed = skipped = 0
    folder_ids = set()
    new_folder = None
    used_names = {}
    for index, job in enumerate(jobs, 1):
        print(f"\n{'─'*16} [{index}/{len(jobs)}] {job['name']} {'─'*16}")
        uid = _hash(job["key"] + ":" + config["setting_id"])
        if config["force"] and uid in state["items"]:
            del state["items"][uid]
            _save_state(state_file, state)
        record = state["items"].get(uid, {})
        if record.get("done"):
            if (job["kind"] == "telegram" and record.get("delete_pending") and config["delete_tg"]
                    and await _maybe_delete(tg_client, tg_channel, job["message"], service, record)):
                record["delete_pending"] = False
                record["deleted"] = True
                _save_state(state_file, state)
            skipped += 1
            if record.get("folder_id"):
                folder_ids.add(record["folder_id"])
            print("✅ معمول قبل كده؛ لن نعيد ضغطه.")
            continue
        stage = WORK_DIR / uid
        stage.mkdir(parents=True, exist_ok=True)
        try:
            if record.get("folder_id"):
                folder_id, folder_name = record["folder_id"], record.get("folder_name", "Previous_Run")
            else:
                if new_folder is None:
                    new_folder = _run_folder(transfer, service, config["batch"])
                    print(f"📁 فولدر النتائج: {_drive_link(new_folder[0])}")
                folder_id, folder_name = new_folder
            folder_ids.add(folder_id)
            print("⬇️ Fetch → Smart Prepare → Split if needed → Drive")
            if job["kind"] == "telegram":
                source = await download_tg(job, stage)
            else:
                source = await asyncio.to_thread(_download_job, job, stage, transfer, service, web)
            # yt-dlp supplies the real media title only after downloading.
            stem = record.get("stem") or (Path(source).stem if job["kind"] == "internet"
                                          else Path(job["name"]).stem)
            stem = _clean_name(stem)
            # Avoid distinct source files with the same visible name in one batch.
            if not record.get("stem"):
                collision_key = (folder_id, stem.lower())
                if collision_key in used_names and used_names[collision_key] != uid:
                    stem = f"{stem}_{uid[:5]}"
                used_names[collision_key] = uid
            result = await asyncio.to_thread(
                _process, job, transfer, engine, web, service, state, state_file,
                config["settings"], config["setting_id"], config["max_bytes"],
                config["max_minutes"], folder_id, folder_name, stem, source,
                config.get("preparation_mode", "always"),
                config.get("advanced_audio", False),
            )
            ok += 1
            if job["kind"] == "telegram" and config["delete_tg"]:
                result["delete_pending"] = True
                _save_state(state_file, state)
                if await _maybe_delete(tg_client, tg_channel, job["message"], service, result):
                    result["deleted"] = True
                    result["delete_pending"] = False
                    _save_state(state_file, state)
            shutil.rmtree(stage, ignore_errors=True)
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as exc:
            failed += 1
            print(f"❌ تعذر تجهيز {job['name']}: {type(exc).__name__}: {str(exc)[:500]}")
            print("💾 أصل التسجيل محفوظ، والرفع المكتمل قابل للاستكمال في التشغيل القادم.")
    for folder in sorted(folder_ids):
        try:
            await asyncio.to_thread(_publish_manifest, service, state, folder)
        except Exception as exc:
            print(f"⚠️ تعذر تحديث ملف الفهرس في Drive: {type(exc).__name__}: {str(exc)[:120]}")
    print(f"\n🏁 Gemini Ready: ✅ {ok} • ♻️ تخطي {skipped} • ❌ فشل {failed}")
    for folder in sorted(folder_ids):
        print(f"📁 النتائج: {_drive_link(folder)}")
    return {"ok": ok, "failed": failed, "skipped": skipped, "folders": sorted(folder_ids)}


async def _maybe_delete(client, channel, msg, service, record) -> bool:
    if not _delete_allowed(channel):
        print("⚠️ لم تُحذف رسالة Telegram بسبب عدم وجود صلاحيات الحذف.")
        return False
    # Re-fetch provider metadata immediately before irreversible deletion.
    if not await asyncio.to_thread(_record_verified, service, record):
        print("⚠️ لم تُحذف الرسالة: إحدى نسخ Drive غير مؤكدة.")
        return False
    try:
        await client.delete_messages(channel, [int(msg.id)])
        print(f"🗑️ حُذفت رسالة Telegram #{msg.id} بعد التحقق من كل الأجزاء على Drive.")
        return True
    except Exception as exc:
        print(f"⚠️ تعذر حذف رسالة Telegram #{msg.id}: {type(exc).__name__}")
        return False


def run(source: str = "telegram", links: str = "", action: str = "preview", batch_name: str = "",
        max_files: int = 10, recursive: bool = True, tg_mode: str = "new", delete_telegram: bool = True,
        login_method: str = "qr", preset: str = "speech", advanced_audio: bool = False,
        audio_codec: str = "opus", audio_kbps: int = 24, channels: str = "mono",
        sample_rate: str = "24000", noise: str = "off", max_part_mb: float = 9.0,
        max_minutes: int = 0, force_reprocess: bool = False, repo_commit: str = "",
        preparation_mode: str = "smart") -> dict:
    """Top-level Colab Forms API. Preview is read-only regarding source/output files."""
    if source not in {"telegram", "drive_folder", "drive_links", "internet"}:
        raise ValueError("مصدر غير معروف.")
    if preparation_mode not in SMART_MODES:
        raise ValueError("وضع تجهيز الصوت غير معروف.")
    if action not in {"preview", "process"}:
        raise ValueError("اختار معاينة أو تنفيذ.")
    if not 0 <= int(max_files) <= MAX_BATCH:
        raise ValueError("Max files لازم يكون بين 0 و200؛ الصفر يعني الكل.")
    if not 0.5 <= float(max_part_mb) <= 9.0:
        raise ValueError("Max part MB لازم يكون من 0.5 إلى 9.")
    if not 0 <= int(max_minutes) <= 180:
        raise ValueError("حد المدة من 0 إلى 180 دقيقة.")
    if login_method not in {"qr", "code"}:
        raise ValueError("طريقة Telegram غير معروفة.")
    if source != "telegram" and delete_telegram:
        # The checkbox is for Telegram only; never delete Drive or web sources.
        delete_telegram = False
    if not re.fullmatch(r"[0-9a-f]{40}", repo_commit):
        raise ValueError("تعذر تحديد GitHub commit لمكونات التشغيل.")
    transfer = _component(repo_commit, "transfer_engine.py")
    # Exact studio preferences still need engine; no web/FFmpeg startup for Drive Preview.
    engine = _component(repo_commit, "engine.py")
    processing = action == "process"
    web = (_component(repo_commit, "web_video_downloader.py")
           if processing or source == "telegram" else None)
    if source == "telegram":
        transfer.ensure_dependencies()
    else:
        try:
            import googleapiclient  # noqa: F401
        except ImportError:
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "google-api-python-client", "google-auth"], check=True)
    # No FFmpeg check or event-loop patch for Drive/Internet previews.
    if processing or source == "telegram":
        try:
            import nest_asyncio
        except ImportError:
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "nest_asyncio"], check=True)
            import nest_asyncio
        nest_asyncio.apply()
    if processing:
        web._ensure_ffmpeg()
    settings = _settings(engine, preset, advanced_audio, audio_codec, audio_kbps,
                         channels, sample_rate, noise)
    config = {"settings": settings, "setting_id": _settings_signature(settings, max_part_mb, max_minutes,
                                                                     preparation_mode, advanced_audio),
              "preparation_mode": preparation_mode, "advanced_audio": bool(advanced_audio),
              "max_bytes": int(float(max_part_mb) * 1_000_000), "max_minutes": int(max_minutes),
              "max_files": int(max_files), "tg_mode": tg_mode, "delete_tg": bool(delete_telegram),
              "login_method": login_method, "preview": action == "preview", "force": force_reprocess,
              "batch": batch_name.strip() or {"telegram": "Telegram", "drive_folder": "Drive_Folder",
                                              "drive_links": "Drive_Links", "internet": "Internet"}[source]}
    service = transfer.connect_drive()
    state_file = _state_path(transfer)
    print(f"🎧 Gemini Ready | {source} | {preparation_mode} | "
          f"encode preset: {settings['audio_codec']} {settings['audio_kbps']} kbps")
    print(f"📏 الحد: أقل من {config['max_bytes'] / 1_000_000:g} MB" +
          (f" • مدة ≤ {max_minutes} دقيقة" if max_minutes else "") +
          f" • {'Preview' if config['preview'] else 'Process'}")
    with transfer.single_run():
        state = _read_state(state_file)
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        if source == "telegram":
            result = loop.run_until_complete(_telegram_run(transfer, web, engine, service, state, state_file, config))
        else:
            if source in {"drive_folder", "drive_links"}:
                jobs = _drive_jobs(transfer, service, source, links, recursive)
            else:
                jobs = _internet_jobs(links)
            if config["max_files"]:
                jobs = jobs[:config["max_files"]]
            print(f"📦 {len(jobs)} ملف/رابط مؤهل.")
            if config["preview"]:
                _preview(jobs, state, config["setting_id"])
                result = {"ok": 0, "failed": 0, "skipped": 0, "folders": []}
            else:
                result = loop.run_until_complete(_process_many(
                    jobs, transfer, engine, web, service, state, state_file, config))
    return result
