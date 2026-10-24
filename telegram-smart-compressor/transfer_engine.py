# -*- coding: utf-8 -*-
"""Media Lite: interactive, bidirectional Telegram / Google Drive transfers.

The Colab notebook is only a launcher. Nothing runs when this file is imported.
This module deliberately does not import or alter the compressor engine.
"""
from __future__ import annotations

import asyncio
import getpass
import hashlib
from contextlib import contextmanager
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path("/content/drive/MyDrive/Telegram_Extreme_Compressor")
APP_DIR = BASE_DIR / "Media_Lite_Transfer"
TEMP_ROOT = Path("/content/media_lite_transfer_tmp")
CONFIG_PATH = BASE_DIR / "config.json"       # Reuse credentials, not the compressor session.
TRANSFER_CONFIG = APP_DIR / "config.json"
STATE_PATH = APP_DIR / "state_v1.json"
PERSISTENT_SESSION = APP_DIR / "transfer_user.session"
LOCAL_SESSION = Path("/content/media_lite_transfer_user.session")
CHANNEL_TITLE = "📁 Media Lite | File Transfer"
DEFAULT_DRIVE_FOLDER = "Media Lite Transfers"
# ponytail: conservative, free-account-compatible 1.9 GB parts; revisit native 4 GB
# uploads only after an authenticated Premium account / Telethon E2E test.
MAX_TG_PART = 1_900_000_000
DISK_RESERVE = 256 * 1024 * 1024
DRIVE_CHUNK = 8 * 1024 * 1024
UPLOAD_PART_KB = 512  # Official MTProto maximum; fewer sequential round-trips.
SCAN_LIMIT = 3000
LOCK_PATH = APP_DIR / "run.lock"
TAG_PATTERN = re.compile(r"MLTX:([0-9a-f]{18}):(\d+)/(\d+)")


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def load_json(path: Path, default: dict) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else default.copy()
    except (OSError, ValueError):
        return default.copy()


@contextmanager
def single_run():
    """Prevent simultaneous Colab runtimes from sharing the transfer session/state."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    # A heartbeat distinguishes an active transfer from a crash in another runtime.
    # An abandoned lock expires after 10 minutes; an active transfer can run longer.
    try:
        if time.time() - LOCK_PATH.stat().st_mtime > 600:
            LOCK_PATH.unlink(missing_ok=True)
    except FileNotFoundError:
        pass
    try:
        fd = os.open(str(LOCK_PATH), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise RuntimeError("فيه عملية نقل تانية شغالة؛ افتح نفس الـColab أو جرّب بعد إعادة الاتصال.")
    with os.fdopen(fd, "w", encoding="utf-8") as guard:
        guard.write(str(os.getpid()))
    stopped = threading.Event()

    def heartbeat():
        while not stopped.wait(30):
            try:
                os.utime(LOCK_PATH, None)
            except OSError:
                return

    worker = threading.Thread(target=heartbeat, daemon=True)
    worker.start()
    try:
        yield
    finally:
        stopped.set()
        worker.join(timeout=1)
        LOCK_PATH.unlink(missing_ok=True)


def parse_drive_id(value: str) -> str:
    """Accept an unambiguous Drive file/folder ID or official Google URL."""
    text = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{15,}", text):
        return text
    url = urlparse(text)
    if url.scheme != "https" or url.hostname not in {"drive.google.com", "docs.google.com"}:
        raise ValueError("استخدم رابط ملف/مجلد Google Drive أو الـID الصحيح.")
    match = re.search(
        r"/(?:file(?:/u/\d+)?/d|drive(?:/u/\d+)?/folders|folders|document(?:/u/\d+)?/d|spreadsheets(?:/u/\d+)?/d|presentation(?:/u/\d+)?/d)/([A-Za-z0-9_-]{15,})",
        url.path,
    )
    identifier = match.group(1) if match else parse_qs(url.query).get("id", [""])[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]{15,}", identifier):
        raise ValueError("لم أستطع استخراج معرف الملف أو المجلد من الرابط.")
    return identifier



def normalize_transfer_inputs(direction: str, source: str, destination: str) -> tuple[str, str]:
    """Accept a single Drive URL even if an older Colab cell put it in 'destination'."""
    source, destination = (source or "").strip(), (destination or "").strip()
    if direction == "drive_to_telegram":
        if source and destination and parse_drive_id(source) != parse_drive_id(destination):
            raise ValueError("في اتجاه Drive → Telegram حط رابط فولدر Drive واحد بس في خانة الرابط.")
        link = source or destination
        if not link:
            raise ValueError("انسخ رابط ملف أو فولدر Google Drive وحطه في خانة الرابط.")
        parse_drive_id(link)
        if not source:
            print("ℹ️ استخدمت رابط Drive المكتوب في خانة الوجهة كمصدر للنقل إلى Telegram.")
        return link, ""
    if direction == "telegram_to_drive":
        if destination:
            parse_drive_id(destination)
        return source, destination
    raise ValueError("اتجاه النقل غير معروف.")


def bind_transfer_channel(state: dict, channel_id: int, previous_channel_id=None) -> None:
    """A recreated Telegram channel needs fresh upload records, not old channel receipts."""
    channel_id = int(channel_id)
    old_id = state.get("drive_to_telegram_channel_id") or previous_channel_id
    if old_id is not None and int(old_id) != channel_id:
        state["drive_to_telegram"] = {}
        print("📣 قناة النقل اتغيرت؛ هراجع الملفات كأنها أول مرة للقناة الجديدة.")
    state["drive_to_telegram_channel_id"] = channel_id
    save_json(STATE_PATH, state)


def transfer_token(metadata: dict) -> str:
    """Prefer the content checksum, falling back to modifiedTime for native Docs."""
    revision = metadata.get("md5Checksum") or metadata.get("modifiedTime") or ""
    identity = ":".join(
        str(value) for value in (metadata.get("id", ""), revision, metadata.get("size", ""))
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:18]


def clean_filename(name: str) -> str:
    name = str(name or "").replace("\\", "/").split("/")[-1].strip()
    name = "".join("_" if c in '\x00\r\n\t' else c for c in name)
    return name[:180] or "unnamed_file"


# Exact, common Drive MIME types. Generic application/octet-stream needs
# content probing rather than a guessed audio/video suffix.
MEDIA_SUFFIXES = {
    "audio/ogg": ".ogg", "application/ogg": ".ogg",
    "audio/opus": ".ogg", "audio/mpeg": ".mp3", "audio/mp3": ".mp3",
    "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "audio/m4a": ".m4a",
    "video/mp4": ".mp4", "video/quicktime": ".mov",
    "video/webm": ".webm", "audio/webm": ".webm",
    "video/x-matroska": ".mkv", "audio/x-matroska": ".mka",
    "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/flac": ".flac",
    "audio/aac": ".aac", "video/x-msvideo": ".avi",
    "application/pdf": ".pdf", "application/zip": ".zip",
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
}


def inferred_extension(path: Path, mime_type: str) -> str:
    """Find a missing extension from file contents, then reliable Drive MIME."""
    mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    with path.open("rb") as fp:
        header = fp.read(512)

    if header.startswith(b"%PDF-"):
        return ".pdf"
    if header.startswith(b"OggS"):
        return ".ogg"
    if header.startswith(b"fLaC"):
        return ".flac"
    if header.startswith(b"ID3"):
        return ".mp3"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if header.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return ".wav"
    if header[:4] == b"RIFF" and header[8:12] == b"AVI ":
        return ".avi"
    if header.startswith(b"PK\x03\x04") and mime in MEDIA_SUFFIXES:
        return MEDIA_SUFFIXES[mime]

    # For unnamed audio/video recordings, inspect the actual container and
    # stream type. A generic MP4 audio recording should be .m4a, not .mp4.
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=format_name:stream=codec_type", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if probe.returncode == 0:
            data = json.loads(probe.stdout)
            formats = set(str(data.get("format", {}).get("format_name", "")).split(","))
            stream_types = {
                s.get("codec_type") for s in data.get("streams", [])
            }
            if "mov" in formats or "mp4" in formats:
                if "video" in stream_types:
                    return ".mov" if mime == "video/quicktime" else ".mp4"
                if "audio" in stream_types:
                    return ".m4a"
            for fmt, ext in (
                ("ogg", ".ogg"), ("mp3", ".mp3"), ("wav", ".wav"),
                ("flac", ".flac"), ("aac", ".aac"), ("amr", ".amr"),
                ("webm", ".webm"), ("matroska", ".mkv"),
                ("avi", ".avi"), ("mpegts", ".ts"),
            ):
                if fmt in formats:
                    if fmt == "matroska" and b"webm" in header.lower():
                        return ".webm"
                    return ext
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
        pass

    if header[4:8] == b"ftyp":
        return ".m4a" if mime in {"audio/mp4", "audio/x-m4a", "audio/m4a"} else ".mp4"
    if header.startswith(b"PK\x03\x04"):
        return ".zip"
    return MEDIA_SUFFIXES.get(mime, "")


def telegram_transfer_filename(metadata: dict, local: Path) -> str:
    """Preserve existing names; append a real extension only when missing."""
    name = clean_filename(metadata.get("name", ""))
    mime = metadata.get("mimeType", "")
    if mime in EXPORT_TYPES:
        extension = EXPORT_TYPES[mime][1]
        return name if name.lower().endswith(extension) else name[:180 - len(extension)] + extension
    if Path(name).suffix:
        return name

    extension = inferred_extension(local, mime)
    if not extension:
        print(f"⚠️ مش قادر أحدد صيغة {name} بثقة؛ هحتفظ بالاسم الأصلي.")
        return name
    fixed = name[:180 - len(extension)] + extension
    print(f"🏷️ الاسم على Telegram: {name} → {fixed}")
    return fixed


def new_state() -> dict:
    return {"telegram_to_drive": {}, "drive_to_telegram": {}}


def state_section(state: dict, key: str) -> dict:
    if not isinstance(state.get(key), dict):
        state[key] = {}
    return state[key]


class SegmentReader:
    """Seekable, size-bounded view of one large file; no extra split-file copy."""
    def __init__(self, path: Path, offset: int, size: int, name: str):
        self._source = open(path, "rb")
        self._offset = offset
        self._size = size
        self._position = 0
        self.name = name
        self._source.seek(offset)

    def read(self, n: int = -1) -> bytes:
        left = self._size - self._position
        count = left if n < 0 else min(n, left)
        if count <= 0:
            return b""
        part = self._source.read(count)
        self._position += len(part)
        return part

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        target = offset
        if whence == os.SEEK_CUR:
            target += self._position
        elif whence == os.SEEK_END:
            target += self._size
        if target < 0 or target > self._size:
            raise ValueError("Seek outside file segment")
        self._source.seek(self._offset + target)
        self._position = target
        return target

    def close(self) -> None:
        self._source.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class Progress:
    def __init__(self, title: str):
        self.title = title
        self.last = 0.0
        self.started = time.monotonic()

    def __call__(self, current: int, total: int):
        now = time.monotonic()
        if now - self.last < 2.5 and current != total:
            return
        self.last = now
        elapsed = max(0.001, now - self.started)
        pct = (100.0 * current / total) if total else 0.0
        speed = current / elapsed / (1024 * 1024)
        print(f"\r{self.title}: {pct:.1f}% — {speed:.1f} MiB/s", end="\n" if current == total else "", flush=True)


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, message):
        for out in self.streams:
            out.write(message)
            out.flush()
        return len(message)

    def flush(self):
        for out in self.streams:
            out.flush()

    def __getattr__(self, name):
        return getattr(self.streams[0], name)


def export_run_log(log_path: Path) -> Path:
    target = APP_DIR / "logs" / log_path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(log_path, target)
    return target


def show_log_controls(log_path: Path) -> None:
    try:
        import ipywidgets as widgets
        from IPython.display import display
        button = widgets.Button(description="📥 Import logs to Drive", button_style="info")
        feedback = widgets.HTML("📝 لوج التشغيل محفوظ مؤقتًا في Colab.")
        def on_click(_):
            try:
                target = export_run_log(log_path)
                button.disabled = True
                feedback.value = f"✅ تم حفظ اللوج في Google Drive: {target.relative_to(BASE_DIR)}"
            except Exception as exc:
                feedback.value = f"❌ تعذر حفظ اللوج: {type(exc).__name__}"
        button.on_click(on_click)
        display(widgets.VBox([button, feedback]))
    except Exception:
        print(f"📝 سجل التشغيل المحلي: {log_path}")


def ensure_dependencies() -> None:
    try:
        import cryptg  # Native Telegram encryption, if an earlier Colab cell did not install it.
        import telethon
        import googleapiclient
        import google.auth
        import nest_asyncio
        if telethon.__version__ == "1.45.0":
            return
    except ImportError:
        pass
    print("🔧 تجهيز أدوات النقل لأول مرة...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q",
         "telethon==1.45.0", "cryptg", "nest_asyncio",
         "google-api-python-client", "google-auth", "google-auth-httplib2"],
        check=True,
    )


def load_account_config() -> dict:
    shared = load_json(CONFIG_PATH, {})
    if all(shared.get(k) for k in ("api_id", "api_hash", "phone")):
        return shared
    print("🔐 بيانات Telegram مطلوبة لأول تشغيل فقط.")
    print("احصل على API ID و API Hash من https://my.telegram.org/apps")
    raw_id = input("Telegram API ID: ").strip()
    api_hash = getpass.getpass("Telegram API Hash: ").strip()
    phone = getpass.getpass("رقم التليفون بصيغته الدولية: ").strip()
    if not raw_id.isdigit() or not api_hash or not phone:
        raise ValueError("بيانات Telegram غير مكتملة.")
    shared.update({"api_id": int(raw_id), "api_hash": api_hash, "phone": phone})
    save_json(CONFIG_PATH, shared)
    return shared


DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"


def _drive_service_for_signed_in_user(default_credentials, build_api):
    """Reuse an existing Drive API login, but never silently use the VM service account."""
    credentials, _project = default_credentials(scopes=[DRIVE_SCOPE])
    service = build_api("drive", "v3", credentials=credentials, cache_discovery=False)
    info = service.about().get(fields="user(emailAddress)").execute(num_retries=2)
    email = str(info.get("user", {}).get("emailAddress", "")).lower()
    if not email or email.endswith(".gserviceaccount.com"):
        raise RuntimeError("Google Drive API مش متصلة بحساب Google شخصي أو Workspace مسجل الدخول.")
    return service


def connect_drive():
    from google.colab import auth, drive
    from google.auth import default
    from googleapiclient.discovery import build

    # Colab's drive.mount can start a *new* browser credential-propagation request
    # even if the Drive FUSE mount is already active (notably on mobile Safari).
    # Avoid repeating a grant when the authorized filesystem is already mounted.
    mountpoint = "/content/drive"
    if os.path.ismount(mountpoint):
        print("✅ Google Drive متوصل بالفعل؛ مش هطلب Mount جديد.")
    else:
        print("🔑 أولًا: توصيل مساحة Google Drive بجلسة Colab...")
        try:
            drive.mount(mountpoint, force_remount=False)
        except Exception as exc:
            raise RuntimeError(
                "فشل توصيل Google Drive من Colab نفسه (قبل Telegram). "
                "افتح Files من الشريط الجانبي وجرب Mount Drive، ووافق على الأذونات. "
                "لو ظهر credential propagation was unsuccessful، فدي مشكلة معروفة "
                "في صلاحيات Colab؛ جرّب نافذة جديدة، واسمح بالكوكيز والنوافذ المنبثقة "
                "أو جرّب متصفح آخر."
            ) from exc

    if not os.path.ismount(mountpoint):
        raise RuntimeError(
            "Colab لم يؤكد ربط Google Drive. اربطه من قائمة Files أولًا قبل تشغيل النقل."
        )

    BASE_DIR.mkdir(parents=True, exist_ok=True)
    APP_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)

    try:
        service = _drive_service_for_signed_in_user(default, build)
        print("✅ Google Drive API متوصلة بالفعل؛ استخدمت الإذن الحالي.")
        return service
    except Exception:
        # Access to mounted Drive files is separate from Google Drive API OAuth.
        # Only ask for a second grant when no working *user* API credential exists.
        print("🔑 Google Drive API محتاجة إذن وصول مرة واحدة...")
    try:
        auth.authenticate_user()
    except Exception as exc:
        raise RuntimeError(
            "فشل تفويض Google Drive API من Colab، بعد نجاح Mount. "
            "اتأكد إن نوافذ تسجيل Google والأذونات بتفتح على المتصفح وإن حساب Google صحيح. "
            "لو رسالة credential propagation was unsuccessful ظهرت هنا أيضًا، "
            "جرب متصفح مختلف؛ المشكلة من ربط أذونات Colab وليست من Telegram."
        ) from exc

    try:
        service = _drive_service_for_signed_in_user(default, build)
    except Exception as exc:
        raise RuntimeError(
            "تم طلب التفويض لكن Google Drive API مش قادرة تقرأ حساب Drive. "
            "تأكد إنك اخترت الحساب اللي فيه الفولدر ووافقت على صلاحيات Google Drive."
        ) from exc
    print("✅ تم توصيل Google Drive API بنجاح.")
    return service


def get_drive_metadata(service, file_id: str) -> dict:
    return service.files().get(
        fileId=file_id, supportsAllDrives=True,
        fields="id,name,mimeType,size,md5Checksum,modifiedTime,webViewLink,parents",
    ).execute(num_retries=3)


def list_drive_sources(service, source_id: str, recursive: bool) -> list[dict]:
    """Traverse files by ID, never by ambiguous name; skip shortcuts."""
    root = get_drive_metadata(service, source_id)
    folder_mime = "application/vnd.google-apps.folder"
    if root["mimeType"] != folder_mime:
        return [{**root, "relative_path": clean_filename(root["name"])}]

    files = []
    visited = set()

    def walk(folder_id: str, prefix: str):
        if folder_id in visited:
            return
        visited.add(folder_id)
        page_token = None
        while True:
            result = service.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken,files(id,name,mimeType,size,md5Checksum,modifiedTime)",
                pageSize=1000, pageToken=page_token,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute(num_retries=3)
            for item in result.get("files", []):
                rel = f"{prefix}/{clean_filename(item['name'])}" if prefix else clean_filename(item["name"])
                if item["mimeType"] == folder_mime:
                    if recursive:
                        walk(item["id"], rel)
                elif item["mimeType"] == "application/vnd.google-apps.shortcut":
                    print(f"↪️ تخطي Shortcut: {rel}")
                else:
                    files.append({**item, "relative_path": rel})
            page_token = result.get("nextPageToken")
            if not page_token:
                return

    walk(root["id"], "")
    return sorted(files, key=lambda x: x["relative_path"].lower())


def ensure_drive_destination(service, folder_ref: str) -> str:
    folder_mime = "application/vnd.google-apps.folder"
    if folder_ref:
        folder_id = parse_drive_id(folder_ref)
    else:
        result = service.files().list(
            q=f"name = '{DEFAULT_DRIVE_FOLDER}' and mimeType = '{folder_mime}' and 'root' in parents and trashed = false",
            fields="files(id)", pageSize=10,
        ).execute(num_retries=3)
        found = result.get("files", [])
        if found:
            folder_id = found[0]["id"]
        else:
            created = service.files().create(
                body={"name": DEFAULT_DRIVE_FOLDER, "mimeType": folder_mime, "parents": ["root"]},
                fields="id",
            ).execute(num_retries=3)
            folder_id = created["id"]
    if get_drive_metadata(service, folder_id)["mimeType"] != folder_mime:
        raise ValueError("الوجهة على Drive لازم تكون مجلدًا، مش ملفًا.")
    return folder_id


def ensure_child_folder(service, parent_id: str, name: str, cache: dict) -> str:
    name = clean_filename(name)
    key = (parent_id, name)
    if key not in cache:
        safe = name.replace("\\", "\\\\").replace("'", "\\'")
        result = service.files().list(
            q=f"'{parent_id}' in parents and name = '{safe}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
            fields="files(id)", pageSize=10,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute(num_retries=3)
        entries = result.get("files", [])
        cache[key] = entries[0]["id"] if entries else service.files().create(
            body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
            fields="id", supportsAllDrives=True,
        ).execute(num_retries=3)["id"]
    return cache[key]


def origin_key(peer_id: int, message_id: int) -> str:
    return f"{peer_id}_{message_id}"


def existing_drive_copy(service, folder_id: str, key: str, expected_size: int) -> dict | None:
    # appProperties lets us recover after a successful upload whose response was lost.
    q = (
        f"'{folder_id}' in parents and trashed = false and "
        f"appProperties has {{ key='media_lite_source' and value='{key}' }}"
    )
    result = service.files().list(
        q=q, fields="files(id,name,size,md5Checksum,webViewLink)", pageSize=100,
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute(num_retries=3)
    for file in result.get("files", []):
        if int(file.get("size", -1)) == expected_size:
            return file
    if result.get("files"):
        raise ValueError("موجود ملف بنفس معرف المصدر لكن بحجم مختلف؛ يلزم مراجعة يدوية.")
    return None


def upload_drive_file(service, local_path: Path, folder_id: str, name: str, key: str) -> dict:
    from googleapiclient.http import MediaFileUpload
    import mimetypes

    meta = {
        "name": name,
        "parents": [folder_id],
        "appProperties": {"media_lite_source": key},
    }
    media = MediaFileUpload(
        str(local_path), mimetype=mimetypes.guess_type(name)[0] or "application/octet-stream",
        chunksize=DRIVE_CHUNK, resumable=True,
    )
    request = service.files().create(
        body=meta, media_body=media,
        fields="id,name,size,md5Checksum,webViewLink", supportsAllDrives=True,
    )
    progress = Progress(f"⬆️ Drive: {name}")
    response = None
    try:
        while response is None:
            status, response = request.next_chunk(num_retries=4)
            if status:
                progress(int(status.progress() * local_path.stat().st_size), local_path.stat().st_size)
    finally:
        media.stream().close()

    if int(response.get("size", -1)) != local_path.stat().st_size:
        raise RuntimeError("Google Drive أعاد حجمًا مختلفًا عن الملف الأصلي.")
    remote_md5 = response.get("md5Checksum")
    if remote_md5:
        digest = hashlib.md5()
        with local_path.open("rb") as fp:
            for chunk in iter(lambda: fp.read(DRIVE_CHUNK), b""):
                digest.update(chunk)
        if digest.hexdigest() != remote_md5:
            raise RuntimeError("محتوى Google Drive غير مطابق (MD5).")
    progress(local_path.stat().st_size, local_path.stat().st_size)
    return response


EXPORT_TYPES = {
    "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"
    ),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"
    ),
    "application/vnd.google-apps.drawing": ("image/png", ".png"),
}


def download_drive_file(service, metadata: dict, path: Path) -> tuple[int, str]:
    from googleapiclient.http import MediaIoBaseDownload

    original_mime = metadata.get("mimeType", "")
    if original_mime in EXPORT_TYPES:
        request = service.files().export_media(
            fileId=metadata["id"], mimeType=EXPORT_TYPES[original_mime][0]
        )
    elif original_mime.startswith("application/vnd.google-apps."):
        raise ValueError("نوع Google Workspace ده مش قابل للتصدير تلقائيًا.")
    else:
        request = service.files().get_media(fileId=metadata["id"], supportsAllDrives=True)

    expected = int(metadata.get("size") or 0)
    if expected and shutil.disk_usage(TEMP_ROOT).free < expected + DISK_RESERVE:
        raise OSError("مساحة Colab غير كافية لهذا الملف، ولم يبدأ تنزيله.")
    progress = Progress(f"⬇️ Drive: {metadata['name']}")
    with path.open("wb") as out:
        transfer = MediaIoBaseDownload(out, request, chunksize=DRIVE_CHUNK)
        done = False
        while not done:
            status, done = transfer.next_chunk(num_retries=4)
            if status:
                progress(int(status.progress() * expected) if expected else out.tell(), expected)
            if shutil.disk_usage(TEMP_ROOT).free < DISK_RESERVE:
                raise OSError("المساحة المتبقية في Colab قليلة؛ تم إيقاف الملف بأمان.")
    actual = path.stat().st_size
    if expected and expected != actual:
        raise RuntimeError("Google Drive: الملف المنزَّل غير مكتمل.")
    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    with path.open("rb") as inp:
        for block in iter(lambda: inp.read(DRIVE_CHUNK), b""):
            md5.update(block)
            sha256.update(block)
    if metadata.get("md5Checksum") and metadata["md5Checksum"] != md5.hexdigest():
        raise RuntimeError("Google Drive: فشل التحقق من MD5 للملف المنزَّل.")
    return actual, sha256.hexdigest()


async def ensure_channel(client, config: dict):
    from telethon import functions, types

    def usable(entity):
        rights = getattr(entity, "admin_rights", None)
        can_post = bool(getattr(entity, "creator", False) or getattr(rights, "post_messages", False))
        return bool(
            getattr(entity, "broadcast", False) and can_post
            and not getattr(entity, "username", None)
        )

    channel_id = config.get("channel_id")
    if channel_id:
        try:
            entity = await client.get_entity(types.PeerChannel(int(channel_id)))
            if usable(entity):
                print(f"📣 استخدام قناة النقل المحفوظة: {getattr(entity, 'title', CHANNEL_TITLE)}")
                return entity
        except (ValueError, TypeError):
            pass
        except Exception:
            # The Telegram entity cache may be unavailable in a fresh session.
            pass

    matches = []
    async for dialog in client.iter_dialogs():
        if dialog.name == CHANNEL_TITLE and usable(dialog.entity):
            matches.append(dialog.entity)
    if len(matches) > 1:
        raise RuntimeError(
            "فيه أكتر من قناة خاصة بنفس اسم Media Lite | File Transfer. "
            "مش هختار قناة عشوائية؛ لازم نحدد القناة المقصودة."
        )
    if matches:
        channel = matches[0]
        config["channel_id"] = int(channel.id)
        save_json(TRANSFER_CONFIG, config)
        print(f"📣 لقيت قناة النقل القديمة وهستخدمها: {CHANNEL_TITLE}")
        return channel

    result = await client(functions.channels.CreateChannelRequest(
        title=CHANNEL_TITLE,
        about="Media Lite: private file transfers between Telegram and Google Drive.",
        broadcast=True, megagroup=False,
    ))
    channel = result.chats[0]
    config["channel_id"] = int(channel.id)
    save_json(TRANSFER_CONFIG, config)
    print(f"✅ أنشأت قناة Telegram خاصة جديدة: {CHANNEL_TITLE}")
    return channel


async def cached_part_messages(client, channel) -> dict:
    result = {}
    async for msg in client.iter_messages(channel, limit=SCAN_LIMIT):
        tag = TAG_PATTERN.search(msg.message or "")
        if tag and getattr(msg, "file", None):
            key = (tag.group(1), int(tag.group(2)))
            if key not in result:  # Telegram returns newest first.
                result[key] = (int(msg.id), int(getattr(msg.file, "size", 0) or 0))
    return result


async def send_transfer_document(
    client, channel, payload, size: int, name: str, caption: str,
    progress, raw_part: bool = False,
):
    """Use Telethon's *own* uploader with fewer 512 KiB MTProto round-trips.

    Unlike the removed experimental parallel uploader, we do not construct
    Telegram part handles manually or change data ordering.
    """
    from telethon.tl.types import DocumentAttributeFilename

    handle = await client.upload_file(
        payload, file_size=size, file_name=name,
        part_size_kb=UPLOAD_PART_KB, progress_callback=progress,
    )
    return await client.send_file(
        channel, handle, caption=caption, force_document=True,
        mime_type="application/octet-stream" if raw_part else None,
        attributes=[DocumentAttributeFilename(name)],
    )


async def transfer_drive_to_telegram(
    client, channel, service, state: dict, source_id: str, recursive: bool,
    max_files: int, preview: bool,
) -> dict:
    all_items = list_drive_sources(service, source_id, recursive)
    if not all_items:
        print("📭 مفيش ملفات في المجلد المحدد (أو هو فاضي).")
        return {"ok": 0, "skipped": 0, "failed": 0}
    records = state_section(state, "drive_to_telegram")
    pending = [m for m in all_items if not records.get(transfer_token(m), {}).get("done")]
    already_done = len(all_items) - len(pending)
    items = pending[:max_files] if max_files else pending
    count_bytes = sum(int(x.get("size") or 0) for x in items)
    print(
        f"📦 Drive → Telegram | إجمالي المجلد: {len(all_items)} ملف | "
        f"اتنقل قبل كده: {already_done} | هيتنقل دلوقتي: {len(items)} "
        f"({count_bytes / 1024**2:.1f} MiB)"
    )
    for item in items[:10]:
        print(" •", item["relative_path"])
    if preview:
        print("👁️ معاينة فقط — لم يتم نقل ملفات أو إنشاء قناة.")
        return {"ok": 0, "skipped": already_done, "failed": 0}
    if not items:
        print("✅ كل ملفات المجلد اتنقلت بالفعل إلى قناة Telegram.")
        return {"ok": 0, "skipped": already_done, "failed": 0}

    sent = await cached_part_messages(client, channel)
    counters = {"ok": 0, "skipped": already_done, "failed": 0}
    for index, meta in enumerate(items, 1):
        token = transfer_token(meta)
        previous = records.get(token, {})
        if previous.get("done"):
            counters["skipped"] += 1
            continue
        try:
            with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as td:
                local = Path(td) / clean_filename(meta["name"])
                size, digest = await asyncio.to_thread(download_drive_file, service, meta, local)
                name = telegram_transfer_filename(meta, local)
                parts = max(1, math.ceil(size / MAX_TG_PART))
                existing = previous.get("parts", {})
                for part_no in range(1, parts + 1):
                    offset = (part_no - 1) * MAX_TG_PART
                    length = min(MAX_TG_PART, size - offset)
                    marker = f"MLTX:{token}:{part_no}/{parts}"
                    cached = sent.get((token, part_no))
                    if cached and cached[1] == length:
                        existing[str(part_no)] = cached[0]
                        records[token] = {"parts": existing, "done": False}
                        save_json(STATE_PATH, state)
                        continue

                    part_name = name if parts == 1 else f"{name}.part{part_no:03d}"
                    caption = f"📁 {meta['relative_path'][-700:]}\n{marker}"
                    progress = Progress(f"⬆️ Telegram [{index}/{len(items)}] {part_name}")
                    if parts == 1:
                        uploaded = await send_transfer_document(
                            client, channel, str(local), length, part_name, caption, progress
                        )
                    else:
                        with SegmentReader(local, offset, length, part_name) as segment:
                            uploaded = await send_transfer_document(
                                client, channel, segment, length, part_name, caption,
                                progress, raw_part=True,
                            )
                    actual = int(getattr(getattr(uploaded, "file", None), "size", 0) or 0)
                    if actual and actual != length:
                        raise RuntimeError("Telegram أرسل حجمًا غير مطابق للجزء.")
                    existing[str(part_no)] = int(uploaded.id)
                    sent[(token, part_no)] = (int(uploaded.id), length)
                    records[token] = {"parts": existing, "done": False}
                    save_json(STATE_PATH, state)

                if parts > 1:
                    manifest = {
                        "format": "media-lite-parts-v1", "name": name,
                        "relative_path": meta["relative_path"], "size": size,
                        "sha256": digest, "part_size": MAX_TG_PART,
                        "parts": [{"name": f"{name}.part{i:03d}", "message_id": existing[str(i)]}
                                  for i in range(1, parts + 1)],
                        "restore": "Concatenate the .part files in ascending order (binary mode).",
                    }
                    manifest_path = Path(td) / f"{name}.parts.json"
                    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
                    manifest_tag = f"MLTX:{token}:manifest"
                    already_sent = previous.get("manifest_id")
                    if not already_sent:
                        for message in await client.get_messages(channel, limit=SCAN_LIMIT):
                            if manifest_tag in (message.message or "") and getattr(message, "file", None):
                                already_sent = int(message.id)
                                break
                    if not already_sent:
                        receipt = await client.send_file(
                            channel, str(manifest_path), force_document=True,
                            caption=f"📋 خريطة تجميع الأجزاء\n{manifest_tag}",
                        )
                        already_sent = int(receipt.id)
                    records[token]["manifest_id"] = already_sent

                records[token] = {
                    **records[token], "done": True, "file_id": meta["id"],
                    "size": size, "sha256": digest, "name": name,
                }
                save_json(STATE_PATH, state)
                counters["ok"] += 1
                print(f"✅ [{index}/{len(items)}] {name}")
        except Exception as exc:
            counters["failed"] += 1
            print(f"❌ [{index}/{len(items)}] {clean_filename(meta['name'])}: {type(exc).__name__}: {exc}")
    return counters


async def transfer_telegram_to_drive(
    client, channel, service, state: dict, folder_id: str, max_files: int, preview: bool,
) -> dict:
    from telethon import utils

    peer_id = utils.get_peer_id(channel)
    downloaded = []
    async for msg in client.iter_messages(channel, limit=SCAN_LIMIT):
        if not getattr(msg, "file", None):
            continue
        if "MLTX:" in (msg.message or ""):
            continue  # Drive → Telegram uploads must never bounce back to Drive.
        key = origin_key(peer_id, int(msg.id))
        if state_section(state, "telegram_to_drive").get(key):
            continue
        downloaded.append(msg)
    jobs = list(reversed(downloaded))
    if max_files:
        jobs = jobs[:max_files]
    print(f"📦 Telegram → Drive | {len(jobs)} ملف جديد من آخر {SCAN_LIMIT} رسالة")
    for msg in jobs[:10]:
        print(" •", clean_filename(getattr(msg.file, "name", "") or f"telegram_{msg.id}"))
    if preview:
        print("👁️ معاينة فقط — لم يتم نقل ملفات.")
        return {"ok": 0, "skipped": 0, "failed": 0}

    records = state_section(state, "telegram_to_drive")
    counters = {"ok": 0, "skipped": 0, "failed": 0}
    folder_cache = {}
    source_name = clean_filename(getattr(channel, "title", "") or "Telegram")
    dst_folder = ensure_child_folder(service, folder_id, source_name, folder_cache)
    for index, msg in enumerate(jobs, 1):
        key = origin_key(peer_id, int(msg.id))
        size = int(getattr(msg.file, "size", 0) or 0)
        name = clean_filename(
            getattr(msg.file, "name", None) or f"telegram_{msg.id}{getattr(msg.file, 'ext', '') or ''}"
        )
        try:
            if size <= 0:
                raise ValueError("Telegram لم يوفّر حجم الملف.")
            existing = await asyncio.to_thread(existing_drive_copy, service, dst_folder, key, size)
            if existing:
                records[key] = {"drive_id": existing["id"], "size": size}
                save_json(STATE_PATH, state)
                counters["skipped"] += 1
                continue
            if shutil.disk_usage(TEMP_ROOT).free < size + DISK_RESERVE:
                raise OSError("مساحة Colab غير كافية؛ لم يبدأ تنزيل الملف.")
            with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as td:
                local = Path(td) / name
                progress = Progress(f"⬇️ Telegram [{index}/{len(jobs)}] {name}")
                got = await client.download_media(msg, file=str(local), progress_callback=progress)
                if not got or not local.exists() or local.stat().st_size != size:
                    raise RuntimeError("تنزيل Telegram غير مكتمل أو حجمه غير مطابق.")
                uploaded = await asyncio.to_thread(upload_drive_file, service, local, dst_folder, name, key)
                records[key] = {"drive_id": uploaded["id"], "size": size, "name": name}
                save_json(STATE_PATH, state)
                counters["ok"] += 1
                print(f"✅ [{index}/{len(jobs)}] {name}")
        except Exception as exc:
            counters["failed"] += 1
            print(f"❌ [{index}/{len(jobs)}] {name}: {type(exc).__name__}: {exc}")
    return counters


async def authorize_telegram(client, phone: str, login_method: str = "code") -> None:
    """Authenticate this transfer session without reusing the compressor's auth key."""
    if login_method not in {"code", "qr"}:
        raise ValueError("طريقة تسجيل دخول Telegram غير معروفة.")

    await client.connect()
    if await client.is_user_authorized():
        print("✅ جلسة نقل Telegram محفوظة ومفعلة؛ مش محتاج كود جديد.")
        return

    if login_method == "code":
        suffix = re.sub(r"\D", "", str(phone))[-2:]
        print(
            f"📩 رمز الدخول للحساب المنتهي بـ {suffix} بيظهر غالبًا في محادثة "
            "Telegram الرسمية (العلامة الزرقاء) داخل التطبيق، مش في SMS."
        )
        print("🔎 راجع الأجهزة التانية والأرشيف. لو مفيش كود، أوقف الخلية واختار QR.")
        await client.start(
            phone=phone,
            code_callback=lambda: getpass.getpass("كود الدخول من محادثة Telegram: ").strip(),
            password=lambda: getpass.getpass("كلمة مرور التحقق بخطوتين: ").strip(),
        )
        return

    # An independent QR login is safer than copying a live compressor session,
    # which can invalidate its Telegram authorization key if used concurrently.
    try:
        import qrcode
    except ImportError:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "qrcode[pil]"], check=True,
        )
        import qrcode
    from html import escape
    from IPython.display import HTML, display
    from telethon.errors import SessionPasswordNeededError

    qr = await client.qr_login()
    for attempt in range(8):
        print("📱 افتح Telegram → الإعدادات → الأجهزة → ربط جهاز، وامسح رمز QR.")
        print("لو فاتح Colab على نفس الجهاز، جرّب رابط الموافقة الموجود تحت الـQR.")
        display(qrcode.make(qr.url).get_image())
        display(HTML(
            f'<p><a href="{escape(qr.url, quote=True)}" target="_blank">'
            "🔗 فتح رابط الموافقة في Telegram على نفس الجهاز</a></p>"
        ))
        try:
            await qr.wait()
            break
        except SessionPasswordNeededError:
            await client.sign_in(
                password=getpass.getpass("كلمة مرور التحقق بخطوتين: ").strip()
            )
            break
        except asyncio.TimeoutError:
            if attempt == 7:
                raise RuntimeError(
                    "انتهت مدة رموز QR بدون موافقة. أعد تشغيل الخلية للحصول على رمز جديد."
                ) from None
            await qr.recreate()
            print("🔄 رمز QR السابق انتهت صلاحيته؛ ده رمز جديد.")
    if not await client.is_user_authorized():
        raise RuntimeError("Telegram لم يؤكد تسجيل الدخول بالـQR.")
    print("✅ تم تأكيد حساب Telegram بنجاح؛ جلسة النقل هتتحفظ على Drive.")


async def _run_transfer(
    service, direction: str, source: str, destination: str,
    max_files: int, recursive: bool, preview: bool, login_method: str = "code",
) -> dict:
    from telethon import TelegramClient

    state = load_json(STATE_PATH, new_state())
    if direction == "drive_to_telegram" and preview:
        return await transfer_drive_to_telegram(
            None, None, service, state, parse_drive_id(source), recursive, max_files, True
        )

    account = load_account_config()
    transfer_config = load_json(TRANSFER_CONFIG, {})
    if PERSISTENT_SESSION.exists():
        shutil.copy2(PERSISTENT_SESSION, LOCAL_SESSION)
    elif LOCAL_SESSION.exists():
        LOCAL_SESSION.unlink()

    client = TelegramClient(
        str(LOCAL_SESSION.with_suffix("")), int(account["api_id"]), account["api_hash"],
        connection_retries=5, request_retries=5,
    )
    try:
        print("🔐 توصيل Telegram بجلسة النقل المستقلة...")
        await authorize_telegram(client, account["phone"], login_method)
        me = await client.get_me()
        configured_phone = re.sub(r"\D", "", str(account["phone"]))
        actual_phone = re.sub(r"\D", "", str(getattr(me, "phone", "")))
        if actual_phone and configured_phone != actual_phone:
            raise RuntimeError("جلسة النقل مرتبطة بحساب Telegram مختلف عن الإعدادات؛ لا يمكن استخدامها.")
        if direction == "drive_to_telegram":
            previous_id = transfer_config.get("channel_id")
            channel = await ensure_channel(client, transfer_config)
            bind_transfer_channel(state, int(channel.id), previous_id)
            return await transfer_drive_to_telegram(
                client, channel, service, state, parse_drive_id(source), recursive, max_files, False
            )
        if direction == "telegram_to_drive":
            if source.strip():
                ref = int(source) if re.fullmatch(r"-?\d+", source.strip()) else source.strip()
                source_entity = await client.get_entity(ref)
            else:
                source_entity = await ensure_channel(client, transfer_config)
            dest_id = "" if preview else await asyncio.to_thread(
                ensure_drive_destination, service, destination
            )
            return await transfer_telegram_to_drive(
                client, source_entity, service, state, dest_id, max_files, preview
            )
        raise ValueError("اتجاه النقل غير معروف.")
    finally:
        try:
            await client.disconnect()
        finally:
            if LOCAL_SESSION.exists():
                shutil.copy2(LOCAL_SESSION, PERSISTENT_SESSION)


def run_transfer(
    direction: str, source: str = "", destination: str = "",
    max_files: int = 20, recursive: bool = True, preview: bool = True,
    login_method: str = "code",
) -> None:
    """Entry point called by the small GitHub-backed Colab launcher cell."""
    if login_method not in {"code", "qr"}:
        raise ValueError("طريقة تسجيل دخول Telegram غير معروفة.")
    source, destination = normalize_transfer_inputs(direction, source, destination)
    max_files = int(max_files)
    if not 0 <= max_files <= 200:
        raise ValueError("عدد الملفات لازم يكون 0 (كل الملفات) أو رقم من 1 إلى 200.")

    ensure_dependencies()
    import nest_asyncio
    nest_asyncio.apply()
    service = connect_drive()
    started = time.time()
    log_path = TEMP_ROOT / f"media_lite_transfer_{time.strftime('%Y%m%d_%H%M%S')}.log"
    old_out, old_err = sys.stdout, sys.stderr
    with log_path.open("w", encoding="utf-8") as log:
        sys.stdout = _Tee(old_out, log)
        sys.stderr = _Tee(old_err, log)
        try:
            print("📦 Media Lite — File Transfer | ملفاتك الأصلية لن تُحذف.")
            with single_run():
                result = asyncio.get_event_loop().run_until_complete(
                    _run_transfer(service, direction, source, destination, max_files, recursive, preview, login_method)
                )
            print(f"\n🏁 نجاح {result['ok']} • تخطي {result['skipped']} • فشل {result['failed']} • {time.time()-started:.0f} ثانية")
            if not preview:
                save_json(APP_DIR / "last_run_summary.json", {
                    "direction": direction, "finished": int(time.time()), **result,
                })
        except KeyboardInterrupt:
            print("\n⏹️ تم إيقاف التشغيل. الملفات الأصلية لم تُمس.")
        except Exception as exc:
            print(f"\n❌ تعذر بدء النقل: {type(exc).__name__}: {exc}")
            raise
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            show_log_controls(log_path)
