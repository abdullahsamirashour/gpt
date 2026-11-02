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
import uuid
import unicodedata
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
        state.pop("reupload_batches", None)
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


def normalize_transfer_subfolder(value: str) -> str:
    """Single safe child-folder name, never a path or arbitrary Drive URL."""
    name = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not name:
        return ""
    if (len(name) > 120 or name in {".", ".."} or
            not name.strip(". ") or
            any(c in "/\\\\" or unicodedata.category(c).startswith("C") for c in name)):
        raise ValueError(
            "اسم الفولدر لازم يكون اسمًا واحدًا من 1 إلى 120 حرفًا، "
            "بدون / أو \\\\ أو أسطر جديدة."
        )
    return name


def normalize_transfer_hashtag(value: str) -> str:
    """Accept #arabic_tag, or an unprefixed label; spaces become underscores."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return ""
    tag = re.sub(r"\s+", "_", text[1:] if text.startswith("#") else text)
    if not tag or len(tag) > 64 or not all(c == "_" or c.isalnum() for c in tag):
        raise ValueError("اكتب هاشتاج واحدًا من حروف/أرقام و_ فقط (مثل #محاضرات_2026).")
    if not any(c.isalpha() for c in tag):
        raise ValueError("الهاشتاج لازم يحتوي على حرف واحد على الأقل.")
    return "#" + tag


def add_transfer_hashtag(caption: str, hashtag: str = "") -> str:
    """Keep MLTX markers intact; the hashtag is appended on its own line."""
    return f"{caption}\n{hashtag}" if hashtag else caption


def telegram_drive_state_key(peer_id: int, message_id: int,
                             parent_id: str, subfolder_name: str) -> str:
    """New named destinations keep independent receipts; old default keys survive."""
    original = origin_key(peer_id, message_id)
    if not subfolder_name:
        return original
    scope = hashlib.sha256(
        f"{parent_id}\0{subfolder_name}".encode("utf-8")
    ).hexdigest()[:18]
    return f"folder-{scope}:{original}"


def find_existing_drive_destination(service, folder_ref: str) -> str:
    """Read-only lookup for Preview; never create the selected folder."""
    folder_mime = "application/vnd.google-apps.folder"
    if folder_ref:
        folder_id = parse_drive_id(folder_ref)
        if get_drive_metadata(service, folder_id)["mimeType"] != folder_mime:
            raise ValueError("الوجهة على Drive لازم تكون فولدرًا مش ملفًا.")
        return folder_id
    result = service.files().list(
        q=f"name = '{DEFAULT_DRIVE_FOLDER}' and mimeType = '{folder_mime}' and 'root' in parents and trashed = false",
        fields="files(id)", pageSize=10,
    ).execute(num_retries=3)
    found = result.get("files", [])
    return found[0]["id"] if found else ""


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
    if mime in MEDIA_SUFFIXES and not mime.startswith(("audio/", "video/")):
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
            if "matroska" in formats or "webm" in formats:
                if b"webm" in header.lower() or mime.endswith("/webm"):
                    return ".webm"
                return ".mka" if mime == "audio/x-matroska" else ".mkv"
            for fmt, ext in (
                ("ogg", ".ogg"), ("mp3", ".mp3"), ("wav", ".wav"),
                ("flac", ".flac"), ("aac", ".aac"), ("amr", ".amr"),
                ("avi", ".avi"), ("mpegts", ".ts"),
            ):
                if fmt in formats:
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


def find_child_folder(service, parent_id: str, name: str) -> str:
    """Read-only child-folder lookup for safe previews and scoped receipts."""
    if not parent_id:
        return ""
    name = clean_filename(name)
    safe = name.replace("\\", "\\\\").replace("'", "\\'")
    result = service.files().list(
        q=f"'{parent_id}' in parents and name = '{safe}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
        fields="files(id)", pageSize=10,
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute(num_retries=3)
    entries = result.get("files", [])
    return entries[0]["id"] if entries else ""


def ensure_child_folder(service, parent_id: str, name: str, cache: dict) -> str:
    name = clean_filename(name)
    key = (parent_id, name)
    if key not in cache:
        existing = find_child_folder(service, parent_id, name)
        cache[key] = existing or service.files().create(
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


TG_UPLOAD_CONNECTIONS = 6
TG_UPLOAD_PART_BYTES = 512 * 1024
TG_UPLOAD_MIN_BYTES = 5 * 1024 * 1024


class TelegramParallelUploader:
    """Six reusable MTProto upload connections; native Telethon remains fallback."""

    def __init__(self, client):
        self.client = client
        self.senders = []
        self.dc_id = None
        self.disabled = False

    @staticmethod
    def supported(client):
        required = ("_get_dc", "_connection", "_call", "_log", "_proxy", "session")
        return hasattr(os, "pread") and all(hasattr(client, key) for key in required)

    async def close(self):
        senders, self.senders = self.senders, []
        self.dc_id = None
        if senders:
            await asyncio.gather(*(s.disconnect() for s in senders), return_exceptions=True)

    async def _connect(self):
        from telethon.network import MTProtoSender

        if self.disabled or not self.supported(self.client):
            raise RuntimeError("Parallel Telegram uploader unavailable")
        current_dc = self.client.session.dc_id
        if self.senders and current_dc == self.dc_id:
            return
        await self.close()
        dc = await self.client._get_dc(current_dc)

        async def make_sender():
            sender = MTProtoSender(self.client.session.auth_key, loggers=self.client._log)
            try:
                await sender.connect(self.client._connection(
                    dc.ip_address, dc.port, dc.id,
                    loggers=self.client._log, proxy=self.client._proxy,
                    local_addr=getattr(self.client, "_local_addr", None)))
                return sender
            except BaseException:
                await sender.disconnect()
                raise

        results = await asyncio.gather(
            *(make_sender() for _ in range(TG_UPLOAD_CONNECTIONS)),
            return_exceptions=True,
        )
        errors = [r for r in results if isinstance(r, BaseException)]
        self.senders = [r for r in results if not isinstance(r, BaseException)]
        if errors:
            await self.close()
            raise errors[0]
        self.dc_id = current_dc

    async def upload_file(self, payload, size, name, progress):
        """Return a real Telethon InputFile handle only after every part is ACKed.

        Supports an ordinary path and the existing SegmentReader used for split
        files >1.9 GB. All reads use positioned os.pread, not shared seek state.
        """
        from telethon import helpers
        from telethon.tl import functions, types

        if size <= 0:
            raise ValueError("Cannot upload empty Telegram file")
        if isinstance(payload, (str, Path)):
            fd = os.open(str(payload), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            close_fd = True
            base = 0
        elif isinstance(payload, SegmentReader):
            fd = payload._source.fileno()
            close_fd = False
            base = payload._offset
            if payload._size != size:
                raise ValueError("Segment size mismatch")
        else:
            raise TypeError("Unsupported parallel upload source")

        try:
            if os.fstat(fd).st_size < base + size:
                raise IOError("Telegram upload source is smaller than declared size")
            await self._connect()
            part_count = math.ceil(size / TG_UPLOAD_PART_BYTES)
            is_big = size > 10 * 1024 * 1024
            file_id = helpers.generate_random_long()
            md5 = None
            if not is_big:
                md5 = hashlib.md5()
                for offset in range(0, size, TG_UPLOAD_PART_BYTES):
                    amount = min(TG_UPLOAD_PART_BYTES, size - offset)
                    data = os.pread(fd, amount, base + offset)
                    if len(data) != amount:
                        raise IOError("Cannot read full small-file chunk for MD5")
                    md5.update(data)

            queue = asyncio.Queue()
            for part_no in range(part_count):
                queue.put_nowait(part_no)
            acknowledged = 0

            async def worker(sender):
                nonlocal acknowledged
                while True:
                    try:
                        part_no = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    offset = part_no * TG_UPLOAD_PART_BYTES
                    amount = min(TG_UPLOAD_PART_BYTES, size - offset)
                    data = os.pread(fd, amount, base + offset)
                    if len(data) != amount:
                        raise IOError(f"Incomplete local upload part {part_no}")
                    if is_big:
                        request = functions.upload.SaveBigFilePartRequest(
                            file_id, part_no, part_count, data)
                    else:
                        request = functions.upload.SaveFilePartRequest(file_id, part_no, data)
                    if not await self.client._call(sender, request):
                        raise RuntimeError(f"Telegram rejected upload part {part_no}")
                    acknowledged += amount
                    progress(acknowledged, size)

            tasks = [asyncio.create_task(worker(sender)) for sender in self.senders]
            try:
                await asyncio.gather(*tasks)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            if acknowledged != size:
                raise RuntimeError(f"Telegram upload part total {acknowledged} != {size}")
            if is_big:
                return types.InputFileBig(file_id, part_count, name)
            return types.InputFile(file_id, part_count, name, md5.hexdigest())
        finally:
            if close_fd:
                os.close(fd)


async def prepare_drive_upload(service, meta):
    """Fetch the next Drive file into its own bounded Colab staging directory."""
    temporary = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
    local = Path(temporary.name) / clean_filename(meta["name"])
    try:
        size, digest = await asyncio.to_thread(download_drive_file, service, meta, local)
        name = telegram_transfer_filename(meta, local)
        return temporary, local, size, digest, name
    except BaseException:
        temporary.cleanup()
        raise


def can_prefetch_drive_file(meta):
    """Bound pipeline disk use to the current upload plus one known-size file."""
    try:
        expected = int(meta.get("size") or 0)
    except (ValueError, TypeError):
        return False
    return expected > 0 and shutil.disk_usage(TEMP_ROOT).free >= expected + 2 * DISK_RESERVE



async def send_transfer_document(
    client, channel, payload, size: int, name: str, caption: str,
    progress, raw_part: bool = False, parallel_uploader=None,
):
    """Upload with six independent MTProto senders, with native Telethon fallback.

    Send only the fully acknowledged input file handle, preserving receipt logic.
    """
    from telethon.tl.types import DocumentAttributeFilename

    handle = None
    if parallel_uploader is not None and not parallel_uploader.disabled and size >= TG_UPLOAD_MIN_BYTES:
        started_parallel = time.monotonic()
        try:
            handle = await parallel_uploader.upload_file(payload, size, name, progress)
            elapsed = max(0.001, time.monotonic() - started_parallel)
            print(f"⚡ Telegram رفع متوازي ({TG_UPLOAD_CONNECTIONS} اتصالات): "
                  f"{size / elapsed / (1024 * 1024):.2f} MiB/s")
        except Exception as exc:
            parallel_uploader.disabled = True
            await parallel_uploader.close()
            if type(exc).__name__ in ("FloodWaitError", "FloodPremiumWaitError"):
                raise
            print(f"↩️ رفع متوازي غير متاح ({type(exc).__name__})؛ "
                  "الرجوع لرفع Telethon العادي لبقية التشغيل.")
    if handle is None:
        handle = await client.upload_file(
            payload, file_size=size, file_name=name,
            part_size_kb=UPLOAD_PART_KB, progress_callback=progress,
        )
    return await client.send_file(
        channel, handle, caption=caption, force_document=True,
        mime_type="application/octet-stream" if raw_part else None,
        attributes=[DocumentAttributeFilename(name)],
    )


def parse_file_numbers(spec: str, total: int) -> list[int]:
    """Parse human-facing 1-based file numbers: 1,3,5-8."""
    text = str(spec or "").strip().replace("،", ",")
    if not text:
        raise ValueError("اكتب أرقام الملفات اللي عايز تعيد رفعها، مثل 1-7 أو 1,3,5-8.")
    numbers = set()
    for field in text.split(","):
        match = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", field)
        if not match:
            raise ValueError("صيغة الأرقام غير صحيحة. مثال: 1-7 أو 1,3,5-8.")
        first = int(match.group(1))
        last = int(match.group(2) or match.group(1))
        if not 1 <= first <= last <= total:
            raise ValueError(f"أرقام الملفات لازم تكون من 1 إلى {total} (بنفس ترتيب المعاينة).")
        if last - first > 200:
            raise ValueError("أقصى عدد ملفات لإعادة الرفع في المرة الواحدة هو 200.")
        numbers.update(range(first, last + 1))
        if len(numbers) > 200:
            raise ValueError("اختار 200 ملف على الأكثر في كل تشغيل.")
    return sorted(numbers)


async def transfer_drive_to_telegram(
    client, channel, service, state: dict, source_id: str, recursive: bool,
    max_files: int, preview: bool, reupload_selected: bool = False,
    selected_numbers: str = "", telegram_hashtag: str = "",
) -> dict:
    telegram_hashtag = normalize_transfer_hashtag(telegram_hashtag)
    if telegram_hashtag:
        print(f"🏷️ هيتضاف الهاشتاج {telegram_hashtag} لوصف كل ملف/جزء جديد.")
    all_items = list_drive_sources(service, source_id, recursive)
    if not all_items:
        print("📭 مفيش ملفات في المجلد المحدد (أو هو فاضي).")
        return {"ok": 0, "skipped": 0, "failed": 0}

    records = state_section(state, "drive_to_telegram")
    total_done = sum(bool(records.get(transfer_token(m), {}).get("done")) for m in all_items)
    if preview:
        print(f"📋 المجلد فيه {len(all_items)} ملف | اتنقل قبل كده: {total_done}")
        for idx, meta in enumerate(all_items[:200], 1):
            done = bool(records.get(transfer_token(meta), {}).get("done"))
            print(f"  {idx:>3}. {'✅ مكتمل' if done else '🆕 جديد'} — {meta['relative_path']}")
        if len(all_items) > 200:
            print("⚠️ عرضت أول 200 ملف فقط. اختار فولدر أصغر لو عايز تعيد ملفات بعد كده.")
        print("👁️ معاينة فقط — مفيش رفع ولا إنشاء قناة.")
        print("💡 لإعادة رفع ملفات معينة، اختار «إعادة رفع المحدد» واكتب أرقامها مثل 1-7.")
        return {"ok": 0, "skipped": total_done, "failed": 0}

    redo_batch = None
    if reupload_selected:
        numbers = parse_file_numbers(selected_numbers, min(len(all_items), 200))
        selected_items = [all_items[i - 1] for i in numbers]
        batch_fingerprint = hashlib.sha256(
            (source_id + "|" + "|".join(transfer_token(m) for m in selected_items)).encode()
        ).hexdigest()[:18]
        batches = state.setdefault("reupload_batches", {})
        redo_batch = batches.get(batch_fingerprint)
        if not isinstance(redo_batch, dict) or redo_batch.get("done"):
            redo_batch = {
                "completed": [], "done": False, "started_at": int(time.time()),
                "hashtag": telegram_hashtag,
            }
            batches[batch_fingerprint] = redo_batch
            save_json(STATE_PATH, state)
        # A partially completed reupload batch must keep its original tag.
        # Old batches without a tag were untagged; do not silently mix styles.
        batch_hashtag = normalize_transfer_hashtag(redo_batch.get("hashtag", ""))
        if telegram_hashtag != batch_hashtag:
            print(f"ℹ️ إعادة الرفع دي بدأت بهاشتاج {batch_hashtag or '(بدون)'}؛ "
                  "هنكمل بيه عشان الأجزاء المرسلة قبل كده تفضل متسقة.")
        telegram_hashtag = batch_hashtag
        completed = set(redo_batch.get("completed", []))
        items = [m for m in selected_items if transfer_token(m) not in completed]
        skipped = len(selected_items) - len(items)
        print(
            f"🔁 إعادة رفع محددة: {len(selected_items)} ملف | "
            f"اكتمل من نفس المحاولة: {skipped} | متبقي: {len(items)}"
        )
        print("🛡️ النسخ القديمة ورسائلها محفوظة، ومش هنلمس الملفات التانية.")
        for number, meta in zip(numbers, selected_items):
            marker = "✅ اتعملت في المحاولة" if transfer_token(meta) in completed else "⬆️ هتترفع"
            print(f"  {number}. {marker} — {meta['relative_path']}")
    else:
        pending = [m for m in all_items if not records.get(transfer_token(m), {}).get("done")]
        items = pending[:max_files] if max_files else pending
        skipped = total_done
        print(
            f"📦 Drive → Telegram | إجمالي المجلد: {len(all_items)} ملف | "
            f"اتنقل قبل كده: {total_done} | هيتنقل دلوقتي: {len(items)} "
            f"({sum(int(x.get('size') or 0) for x in items) / 1024**2:.1f} MiB)"
        )
        for meta in items[:10]:
            print(" •", meta["relative_path"])

    if not items:
        if redo_batch is not None:
            redo_batch["done"] = True
            save_json(STATE_PATH, state)
            print("✅ كل الملفات المحددة اكتملت في محاولة إعادة الرفع دي.")
        else:
            print("✅ مفيش ملفات جديدة محتاجة رفع.")
        return {"ok": 0, "skipped": skipped, "failed": 0}

    sent = await cached_part_messages(client, channel)
    counters = {"ok": 0, "skipped": skipped, "failed": 0}
    uploader = TelegramParallelUploader(client) if TelegramParallelUploader.supported(client) else None
    prefetch_task = None
    print(f"⚡ Telegram upload: {TG_UPLOAD_CONNECTIONS} اتصالات للملفات من 5 MiB؛ "
          "تجهيز الملف التالي أثناء رفع الحالي.")
    try:
        for index, meta in enumerate(items, 1):
            token = transfer_token(meta)
            previous = records.get(token, {})
            previously_done = bool(previous.get("done"))
            repairing = reupload_selected and previously_done
            if previously_done and not repairing:
                counters["skipped"] += 1
                continue

            if repairing:
                # A fresh marker intentionally bypasses the old Telegram receipts.
                # Persist it before sending so a failed retry can resume safely.
                repair = previous.get("reupload")
                if not isinstance(repair, dict) or not re.fullmatch(
                    r"[0-9a-f]{18}", str(repair.get("tag", ""))
                ):
                    repair = {"tag": uuid.uuid4().hex[:18], "parts": {}}
                    previous["reupload"] = repair
                    records[token] = previous
                    save_json(STATE_PATH, state)
                receipt_token = repair["tag"]
                existing = dict(repair.get("parts", {}))
                # A partially sent reupload uses its original tag on resume.
                effective_hashtag = (
                    normalize_transfer_hashtag(repair.get("hashtag", ""))
                    if existing else telegram_hashtag
                )
                repair["hashtag"] = effective_hashtag
            else:
                repair = None
                receipt_token = token
                existing = dict(previous.get("parts", {}))
                effective_hashtag = (
                    normalize_transfer_hashtag(previous.get("hashtag", ""))
                    if existing else telegram_hashtag
                )
                if existing and effective_hashtag != telegram_hashtag:
                    print(f"ℹ️ {meta['name']}: استكمال الأجزاء بالهاشتاج "
                          f"{effective_hashtag or '(بدون)'} القديم.")

            def record_part(part_no: int, message_id: int) -> None:
                existing[str(part_no)] = int(message_id)
                if repairing:
                    repair["parts"] = existing
                    records[token] = previous
                else:
                    records[token] = {
                        "parts": existing, "done": False,
                        "hashtag": effective_hashtag,
                    }
                save_json(STATE_PATH, state)

            try:
                with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as td:
                    local = Path(td) / clean_filename(meta["name"])
                    if prefetch_task is not None:
                        staged = None
                        try:
                            staged = await prefetch_task
                            staged_dir, staged_path, size, digest, name = staged
                            os.replace(staged_path, local)
                        finally:
                            prefetch_task = None
                            if staged is not None:
                                staged[0].cleanup()
                    else:
                        size, digest = await asyncio.to_thread(download_drive_file, service, meta, local)
                        name = telegram_transfer_filename(meta, local)

                    if index < len(items) and can_prefetch_drive_file(items[index]):
                        next_meta = items[index]
                        prefetch_task = asyncio.create_task(prepare_drive_upload(service, next_meta))
                        print(f"⏩ تجهيز الملف التالي أثناء رفع الحالي: {next_meta['relative_path']}")
                    parts = max(1, math.ceil(size / MAX_TG_PART))
                    for part_no in range(1, parts + 1):
                        offset = (part_no - 1) * MAX_TG_PART
                        length = min(MAX_TG_PART, size - offset)
                        marker = f"MLTX:{receipt_token}:{part_no}/{parts}"
                        cached = sent.get((receipt_token, part_no))
                        if cached and cached[1] == length:
                            record_part(part_no, cached[0])
                            continue

                        part_name = name if parts == 1 else f"{name}.part{part_no:03d}"
                        caption = add_transfer_hashtag(
                            f"📁 {meta['relative_path'][-700:]}\n{marker}",
                            effective_hashtag,
                        )
                        progress = Progress(f"⬆️ Telegram [{index}/{len(items)}] {part_name}")
                        if parts == 1:
                            uploaded = await send_transfer_document(
                                client, channel, str(local), length, part_name, caption, progress,
                                parallel_uploader=uploader,
                            )
                        else:
                            with SegmentReader(local, offset, length, part_name) as segment:
                                uploaded = await send_transfer_document(
                                    client, channel, segment, length, part_name, caption,
                                    progress, raw_part=True, parallel_uploader=uploader,
                                )
                        actual = int(getattr(getattr(uploaded, "file", None), "size", 0) or 0)
                        if actual and actual != length:
                            raise RuntimeError("Telegram أرسل حجمًا غير مطابق للجزء.")
                        record_part(part_no, int(uploaded.id))
                        sent[(receipt_token, part_no)] = (int(uploaded.id), length)

                    manifest_id = None
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
                        manifest_tag = f"MLTX:{receipt_token}:manifest"
                        manifest_id = (
                            repair.get("manifest_id") if repairing else previous.get("manifest_id")
                        )
                        if not manifest_id:
                            for message in await client.get_messages(channel, limit=SCAN_LIMIT):
                                if manifest_tag in (message.message or "") and getattr(message, "file", None):
                                    manifest_id = int(message.id)
                                    break
                        if not manifest_id:
                            receipt = await client.send_file(
                                channel, str(manifest_path), force_document=True,
                                caption=add_transfer_hashtag(
                                    f"📋 خريطة تجميع الأجزاء\n{manifest_tag}",
                                    effective_hashtag,
                                ),
                            )
                            manifest_id = int(receipt.id)
                        if repairing:
                            repair["manifest_id"] = manifest_id
                            save_json(STATE_PATH, state)
                        else:
                            records[token]["manifest_id"] = manifest_id

                    if repairing:
                        history = previous.get("history", [])
                        history.append({
                            "parts": previous.get("parts", {}).copy(),
                            "manifest_id": previous.get("manifest_id"),
                            "name": previous.get("name"),
                        })
                        previous["history"] = history[-50:]
                        previous.pop("reupload", None)
                        final_record = previous
                    else:
                        final_record = records.get(token, {})
                    final_record.update({
                        "parts": existing, "done": True, "file_id": meta["id"],
                        "size": size, "sha256": digest, "name": name,
                        "hashtag": effective_hashtag,
                    })
                    if manifest_id:
                        final_record["manifest_id"] = manifest_id
                    else:
                        final_record.pop("manifest_id", None)
                    records[token] = final_record
                    if redo_batch is not None:
                        completed_now = set(redo_batch.get("completed", []))
                        completed_now.add(token)
                        redo_batch["completed"] = sorted(completed_now)
                    save_json(STATE_PATH, state)
                    counters["ok"] += 1
                    print(f"✅ [{index}/{len(items)}] {name}")
            except Exception as exc:
                counters["failed"] += 1
                print(
                    f"❌ [{index}/{len(items)}] {clean_filename(meta['name'])}: "
                    f"{type(exc).__name__}: {exc}"
                )
                if type(exc).__name__ in ("FloodWaitError", "FloodPremiumWaitError"):
                    print("⏸️ Telegram طلب الانتظار؛ تم إيقاف الدفعة لحماية الحساب.")
                    break
        if redo_batch is not None and counters["failed"] == 0:
            redo_batch["done"] = True
            save_json(STATE_PATH, state)
        return counters

    finally:
        if prefetch_task is not None:
            # Drain and clean any prepared file even if the current upload fails.
            try:
                unused = await prefetch_task
                unused[0].cleanup()
            except BaseException:
                pass
        if uploader is not None:
            await uploader.close()

# Telegram -> Drive: use independent MTProto download connections for files
# >= 5 MiB, and keep Telethon's native path as the safe fallback.
TG_PARALLEL_MIN_BYTES = 5 * 1024 * 1024
TG_PARALLEL_CONNECTIONS = 6
TG_PARALLEL_PART_BYTES = 512 * 1024


class TelegramParallelDownloader:
    """One file at a time, using independent MTProto senders and offset writes.

    This uses Telethon 1.45's private sender API, so callers must preserve a
    normal download_media fallback for version differences and RPC errors.
    """

    def __init__(self, client):
        self.client = client
        self.foreign_keys = {}  # Memory-only DC auth cache; never persist auth keys.

    @staticmethod
    def supported(client):
        required = ("_get_dc", "_connection", "_call", "_log", "_proxy",
                    "_init_request", "_borrow_sender_lock", "session")
        return hasattr(os, "pwrite") and all(hasattr(client, key) for key in required)

    async def _connect_one(self, dc_id, auth_key, opened=None):
        from telethon.network import MTProtoSender
        dc = await self.client._get_dc(dc_id)
        sender = MTProtoSender(auth_key, loggers=self.client._log)
        try:
            await sender.connect(self.client._connection(
                dc.ip_address, dc.port, dc.id,
                loggers=self.client._log, proxy=self.client._proxy,
                local_addr=getattr(self.client, "_local_addr", None),
            ))
            if opened is not None:
                opened.append(sender)
            return sender
        except BaseException:
            await sender.disconnect()
            raise

    async def _connect_many(self, dc_id, auth_key, amount, opened):
        results = await asyncio.gather(
            *(self._connect_one(dc_id, auth_key, opened) for _ in range(amount)),
            return_exceptions=True,
        )
        errors = [item for item in results if isinstance(item, BaseException)]
        if errors:
            raise errors[0]

    async def _senders(self, dc_id, opened):
        from telethon.tl.alltlobjects import LAYER
        from telethon.tl.functions import InvokeWithLayerRequest
        from telethon.tl.functions.auth import ExportAuthorizationRequest, ImportAuthorizationRequest

        home_dc = self.client.session.dc_id
        target_dc = dc_id or home_dc
        if target_dc == home_dc:
            await self._connect_many(target_dc, self.client.session.auth_key,
                                     TG_PARALLEL_CONNECTIONS, opened)
            return

        key = self.foreign_keys.get(target_dc)
        if key is None:
            first = await self._connect_one(target_dc, None, opened)
            async with self.client._borrow_sender_lock:
                original_query = self.client._init_request.query
                try:
                    exported = await self.client(ExportAuthorizationRequest(target_dc))
                    self.client._init_request.query = ImportAuthorizationRequest(
                        id=exported.id, bytes=exported.bytes)
                    await first.send(InvokeWithLayerRequest(LAYER, self.client._init_request))
                finally:
                    self.client._init_request.query = original_query
            key = first.auth_key
            self.foreign_keys[target_dc] = key
            extra = TG_PARALLEL_CONNECTIONS - 1
        else:
            extra = TG_PARALLEL_CONNECTIONS
        if extra:
            await self._connect_many(target_dc, key, extra, opened)

    @staticmethod
    def _write_all(fd, chunk, offset):
        view = memoryview(chunk)
        done = 0
        while done < len(view):
            wrote = os.pwrite(fd, view[done:], offset + done)
            if wrote <= 0:
                raise OSError("Telegram parallel download: zero-byte disk write")
            done += wrote

    async def download(self, message, path: Path, expected_size: int, progress) -> None:
        from telethon import utils
        from telethon.tl.functions.upload import GetFileRequest
        if not self.supported(self.client):
            raise RuntimeError("Telethon parallel sender API unavailable")
        dc_id, location = utils.get_input_location(message)
        senders = []
        fd = -1
        tasks = []
        downloaded = 0
        stride = TG_PARALLEL_CONNECTIONS * TG_PARALLEL_PART_BYTES
        try:
            await self._senders(dc_id, senders)
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                         getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.ftruncate(fd, expected_size)

            async def worker(index, sender):
                nonlocal downloaded
                for offset in range(index * TG_PARALLEL_PART_BYTES, expected_size, stride):
                    packet = await self.client._call(sender, GetFileRequest(
                        location, offset=offset, limit=TG_PARALLEL_PART_BYTES))
                    data = packet.bytes
                    required = min(TG_PARALLEL_PART_BYTES, expected_size - offset)
                    if len(data) != required:
                        raise IOError(f"Telegram part {offset}: received {len(data)}, expected {required}")
                    self._write_all(fd, data, offset)
                    downloaded += required
                    progress(downloaded, expected_size)

            tasks = [asyncio.create_task(worker(i, sender))
                     for i, sender in enumerate(senders)]
            await asyncio.gather(*tasks)
            if downloaded != expected_size or path.stat().st_size != expected_size:
                raise IOError("Telegram parallel download size/coverage mismatch")
            os.fsync(fd)
        except BaseException:
            if dc_id is not None and dc_id != self.client.session.dc_id:
                self.foreign_keys.pop(dc_id, None)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if fd != -1:
                os.close(fd)
                fd = -1
            path.unlink(missing_ok=True)
            raise
        finally:
            if fd != -1:
                os.close(fd)
            for sender in senders:
                try:
                    await sender.disconnect()
                except Exception:
                    pass


async def fetch_telegram_message(client, channel, msg, local: Path, size: int, downloader) -> None:
    """Try parallel MTProto for files >=5 MiB; safely fall back to Telethon."""
    started = time.monotonic()
    title = f"⬇️ Telegram: {local.name}"
    mode = "عادي"
    completed = False
    if downloader is not None and size >= TG_PARALLEL_MIN_BYTES:
        print(f"⚡ Parallel Telegram: {TG_PARALLEL_CONNECTIONS} اتصالات × 512 KiB — {local.name}")
        try:
            await downloader.download(msg, local, size, Progress(title))
            completed = True
            mode = "متوازي"
        except Exception as exc:
            # Never immediately retry a rate-limited transfer through another path.
            if type(exc).__name__ in ("FloodWaitError", "FloodPremiumWaitError"):
                raise
            local.unlink(missing_ok=True)
            print(f"↩️ تعذر التنزيل المتوازي ({type(exc).__name__})؛ الرجوع للتنزيل العادي.")
            if type(exc).__name__ in ("FileReferenceExpiredError", "FileReferenceEmptyError"):
                try:
                    refreshed = await client.get_messages(channel, ids=msg.id)
                    if refreshed and getattr(refreshed, "file", None):
                        msg = refreshed
                except Exception:
                    pass

    if not completed:
        got = await client.download_media(msg, file=str(local), progress_callback=Progress(title))
        if not got:
            raise RuntimeError("تنزيل Telegram لم يُرجع ملفًا.")
    if not local.exists() or local.stat().st_size != size:
        raise RuntimeError("تنزيل Telegram غير مكتمل أو حجمه غير مطابق.")
    elapsed = max(time.monotonic() - started, 0.001)
    print(f"⏱️ Telegram {mode}: {elapsed:.1f} ثانية — {size / elapsed / (1024 * 1024):.2f} MiB/s")


def file_md5_checksum(path: Path) -> str:
    """Hash downloaded binary data; do not trust remote file size alone for deletion."""
    digest = hashlib.md5()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(DRIVE_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def telegram_media_identity(msg) -> str:
    """Detect media edits when retrying a pending deletion."""
    media = getattr(msg, "document", None) or getattr(msg, "photo", None)
    if media is None:
        wrapper = getattr(msg, "media", None)
        media = getattr(wrapper, "document", None) or getattr(wrapper, "photo", None)
    return str(getattr(media, "id", "") or "")


def confirmed_drive_copy_for_deletion(service, folder_id: str, record: dict) -> bool:
    """Recheck an uploaded Drive file before *irreversibly* deleting Telegram."""
    wanted_id = str(record.get("drive_id") or "")
    wanted_md5 = str(record.get("verified_md5") or "").lower()
    try:
        wanted_size = int(record.get("size") or 0)
    except (TypeError, ValueError):
        return False
    if not wanted_id or not re.fullmatch(r"[a-f0-9]{32}", wanted_md5) or wanted_size <= 0:
        return False

    remote = get_drive_metadata(service, wanted_id)
    return (
        str(remote.get("id") or "") == wanted_id
        and int(remote.get("size") or -1) == wanted_size
        and str(remote.get("md5Checksum") or "").lower() == wanted_md5
        and folder_id in (remote.get("parents") or [])
    )


async def delete_telegram_after_confirmed_drive_upload(
    client, channel, msg, service, folder_id: str, record: dict,
) -> None:
    """Delete only the original message after fresh Drive metadata verification.

    The caller must persist delete_pending before calling this function;
    any failed check leaves the Telegram source intact and safely retryable.
    """
    if not record.get("delete_pending") or record.get("telegram_deleted"):
        raise ValueError("الحذف يتطلب طلبًا محفوظًا بعد رفع موثق على Google Drive.")
    size = int(getattr(getattr(msg, "file", None), "size", 0) or 0)
    if size <= 0 or size != int(record.get("size") or 0):
        raise ValueError("حجم رسالة Telegram اتغير؛ لن يتم حذفها.")
    initial_media = str(record.get("telegram_media_id") or "")
    if initial_media and telegram_media_identity(msg) != initial_media:
        raise ValueError("هوية ملف Telegram اتغيرت؛ لن يتم حذف الرسالة.")
    verified = await asyncio.to_thread(
        confirmed_drive_copy_for_deletion, service, folder_id, record
    )
    if not verified:
        raise RuntimeError("ملف Google Drive مش مثبت بالحجم وMD5 والفولدر الصحيح؛ لن نحذف رسالة Telegram.")
    # Telethon's channel-aware call: never pass a bare message ID without
    # an explicit channel, and never bulk-delete arbitrary message IDs.
    receipt = await client.delete_messages(channel, [int(msg.id)], revoke=True)
    if not receipt:
        raise RuntimeError("Telegram لم يؤكد تنفيذ طلب حذف الرسالة.")


async def transfer_telegram_to_drive(
    client, channel, service, state: dict, folder_id: str, max_files: int, preview: bool,
    subfolder_name: str = "", delete_after_transfer: bool = False,
) -> dict:
    from telethon import utils

    subfolder_name = normalize_transfer_subfolder(subfolder_name)
    peer_id = utils.get_peer_id(channel)
    records = state_section(state, "telegram_to_drive")
    source_name = clean_filename(getattr(channel, "title", "") or "Telegram")
    target_name = subfolder_name or source_name
    print(f"📁 الحفظ: داخل فولدر Drive المختار / {target_name}"
          + (" (فولدر مخصص؛ يُنشأ لو مش موجود)" if subfolder_name else ""))
    folder_cache = {}
    target_id = ""
    if subfolder_name:
        # Resolve the actual destination before filtering saved receipts.
        # When an old custom folder is deleted/recreated its ID changes, so
        # previously transferred files must be copied to the new folder.
        finder = find_child_folder if preview else ensure_child_folder
        if preview:
            target_id = await asyncio.to_thread(finder, service, folder_id, target_name)
        else:
            target_id = await asyncio.to_thread(
                finder, service, folder_id, target_name, folder_cache
            )
            print(f"✅ فولدر الحفظ جاهز: {target_name}")
    downloaded = []
    pending_for_deletion = []
    if delete_after_transfer:
        print("⚠️ حذف رسائل Telegram مفعّل: الحذف نهائي للقناة، وبعد إثبات نسخة Drive فقط.")
    async for msg in client.iter_messages(channel, limit=SCAN_LIMIT):
        if not getattr(msg, "file", None):
            continue
        if "MLTX:" in (msg.message or ""):
            continue  # Drive → Telegram uploads must never bounce back to Drive.
        key = telegram_drive_state_key(peer_id, int(msg.id), target_id, subfolder_name)
        existing_record = records.get(key)
        if existing_record:
            # Never retroactively delete messages copied in older runs.
            # Only explicitly queued, checksum-verified cleanup is resumable.
            if (delete_after_transfer and existing_record.get("delete_pending")
                    and not existing_record.get("telegram_deleted")):
                pending_for_deletion.append((msg, key))
            continue
        downloaded.append(msg)
    jobs = list(reversed(downloaded))
    pending_for_deletion.reverse()
    if max_files:
        jobs = jobs[:max_files]
        pending_for_deletion = pending_for_deletion[:max_files]
    print(f"📦 Telegram → Drive | {len(jobs)} ملف جديد من آخر {SCAN_LIMIT} رسالة")
    if delete_after_transfer:
        print(f"🧹 عمليات حذف معلّقة من تشغيل سابق: {len(pending_for_deletion)}")
    for msg in jobs[:10]:
        print(" •", clean_filename(getattr(msg.file, "name", "") or f"telegram_{msg.id}"))
    if preview:
        print("👁️ معاينة فقط — لن ننقل أو نحذف رسائل أو ننشئ فولدر.")
        return {"ok": 0, "skipped": 0, "failed": 0}

    counters = {"ok": 0, "skipped": 0, "failed": 0,
                "deleted": 0, "delete_failed": 0}
    if not jobs and not pending_for_deletion:
        print("✅ مفيش ملفات جديدة للوجهة المختارة.")
        return counters
    dst_folder = target_id
    if not subfolder_name:
        dst_folder = await asyncio.to_thread(
            ensure_child_folder, service, folder_id, target_name, folder_cache
        )
        print(f"✅ فولدر الحفظ جاهز: {target_name}")
    # Retry *only* deletions explicitly queued after a previously verified
    # upload. Reverify Drive metadata before every attempt.
    for msg, key in pending_for_deletion:
        try:
            await delete_telegram_after_confirmed_drive_upload(
                client, channel, msg, service, dst_folder, records[key]
            )
            records[key]["telegram_deleted"] = True
            records[key]["delete_pending"] = False
            save_json(STATE_PATH, state)
            counters["deleted"] += 1
            print(f"🗑️ حُذفت رسالة Telegram رقم {msg.id} بعد التحقق من Drive.")
        except Exception as exc:
            counters["delete_failed"] += 1
            print(f"⚠️ تعذر حذف الرسالة {msg.id}؛ النسخة على Drive محفوظة. "
                  f"هنعيد المحاولة لو فعلت الخيار تاني. {type(exc).__name__}: {exc}")

    downloader = TelegramParallelDownloader(client) if TelegramParallelDownloader.supported(client) else None
    if jobs:
        print(f"⚡ Telegram parallel download: من 5 MiB، {TG_PARALLEL_CONNECTIONS} اتصالات؛ fallback تلقائي.")
    for index, msg in enumerate(jobs, 1):
        key = telegram_drive_state_key(peer_id, int(msg.id), target_id, subfolder_name)
        remote_key = origin_key(peer_id, int(msg.id))  # Stable Drive appProperties across destinations.
        size = int(getattr(msg.file, "size", 0) or 0)
        name = clean_filename(
            getattr(msg.file, "name", None) or f"telegram_{msg.id}{getattr(msg.file, 'ext', '') or ''}"
        )
        try:
            if size <= 0:
                raise ValueError("Telegram لم يوفّر حجم الملف.")
            existing = await asyncio.to_thread(existing_drive_copy, service, dst_folder, remote_key, size)
            if existing:
                records[key] = {"drive_id": existing["id"], "size": size}
                save_json(STATE_PATH, state)
                counters["skipped"] += 1
                continue
            if shutil.disk_usage(TEMP_ROOT).free < size + DISK_RESERVE:
                raise OSError("مساحة Colab غير كافية؛ لم يبدأ تنزيل الملف.")
            with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as td:
                local = Path(td) / name
                await fetch_telegram_message(client, channel, msg, local, size, downloader)
                uploaded = await asyncio.to_thread(
                    upload_drive_file, service, local, dst_folder, name, remote_key
                )
                record = {"drive_id": uploaded["id"], "size": size, "name": name}
                if delete_after_transfer:
                    # The Drive upload routine already validates the returned
                    # size and, if present, MD5. Require the MD5 *again* as
                    # an explicit deletion prerequisite; no checksum, no delete.
                    remote_md5 = str(uploaded.get("md5Checksum") or "").lower()
                    local_md5 = await asyncio.to_thread(file_md5_checksum, local)
                    if remote_md5 and remote_md5 == local_md5:
                        record["verified_md5"] = local_md5
                        record["telegram_media_id"] = telegram_media_identity(msg)
                        record["delete_pending"] = True
                        record["telegram_deleted"] = False
                    else:
                        print("🛡️ رسالة Telegram محفوظة: Drive لم يوفّر MD5 "
                              "مطابقًا، لذلك لن نحذف الأصل.")
                # Persist the verified destination before ANY destructive step.
                records[key] = record
                save_json(STATE_PATH, state)
                counters["ok"] += 1
                print(f"✅ [{index}/{len(jobs)}] {name}")
                if record.get("delete_pending"):
                    try:
                        await delete_telegram_after_confirmed_drive_upload(
                            client, channel, msg, service, dst_folder, record
                        )
                        record["telegram_deleted"] = True
                        record["delete_pending"] = False
                        save_json(STATE_PATH, state)
                        counters["deleted"] += 1
                        print(f"🗑️ تم حذف رسالة Telegram رقم {msg.id} بعد التحقق.")
                    except Exception as exc:
                        counters["delete_failed"] += 1
                        print(f"⚠️ النسخة اتحفظت على Drive، لكن حذف رسالة {msg.id} فشل. "
                              f"الحذف معلّق للمحاولة القادمة. {type(exc).__name__}: {exc}")
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
    reupload_selected: bool = False, selected_numbers: str = "",
    drive_subfolder: str = "", telegram_hashtag: str = "",
) -> dict:
    drive_subfolder = normalize_transfer_subfolder(drive_subfolder)
    telegram_hashtag = normalize_transfer_hashtag(telegram_hashtag)
    from telethon import TelegramClient

    state = load_json(STATE_PATH, new_state())
    if direction == "drive_to_telegram" and preview:
        return await transfer_drive_to_telegram(
            None, None, service, state, parse_drive_id(source), recursive, max_files, True,
            telegram_hashtag=telegram_hashtag,
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
                client, channel, service, state, parse_drive_id(source), recursive, max_files,
                False, reupload_selected=reupload_selected, selected_numbers=selected_numbers,
                telegram_hashtag=telegram_hashtag,
            )
        if direction == "telegram_to_drive":
            if source.strip():
                ref = int(source) if re.fullmatch(r"-?\d+", source.strip()) else source.strip()
                source_entity = await client.get_entity(ref)
            else:
                source_entity = await ensure_channel(client, transfer_config)
            # Preview may query existing folders but must never create a new one.
            finder = find_existing_drive_destination if preview else ensure_drive_destination
            dest_id = await asyncio.to_thread(finder, service, destination)
            return await transfer_telegram_to_drive(
                client, source_entity, service, state, dest_id, max_files, preview,
                subfolder_name=drive_subfolder,
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
    login_method: str = "code", reupload_selected: bool = False,
    selected_numbers: str = "", drive_subfolder: str = "",
    telegram_hashtag: str = "",
) -> None:
    """Entry point called by the small GitHub-backed Colab launcher cell."""
    drive_subfolder = normalize_transfer_subfolder(drive_subfolder)
    telegram_hashtag = normalize_transfer_hashtag(telegram_hashtag)
    if direction == "drive_to_telegram" and drive_subfolder:
        raise ValueError("اسم فولدر Drive الفرعي متاح فقط في Telegram → Drive.")
    if direction == "telegram_to_drive" and telegram_hashtag:
        raise ValueError("الهاشتاج متاح فقط في Drive → Telegram.")
    if login_method not in {"code", "qr"}:
        raise ValueError("طريقة تسجيل دخول Telegram غير معروفة.")
    source, destination = normalize_transfer_inputs(direction, source, destination)
    if reupload_selected:
        if direction != "drive_to_telegram" or preview:
            raise ValueError("إعادة الرفع المحدد متاحة فقط مع تنفيذ Drive → Telegram.")
        parse_file_numbers(selected_numbers, 200)
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
                    _run_transfer(
                        service, direction, source, destination, max_files, recursive,
                        preview, login_method, reupload_selected, selected_numbers,
                        drive_subfolder, telegram_hashtag,
                    )
                )
            print(f"\n🏁 نجاح {result['ok']} • تخطي {result['skipped']} • فشل {result['failed']} • {time.time()-started:.0f} ثانية")
            if not preview:
                save_json(APP_DIR / "last_run_summary.json", {
                    "direction": direction, "finished": int(time.time()),
                    "reupload_selected": bool(reupload_selected),
                    "selected_numbers": selected_numbers if reupload_selected else "",
                    "drive_subfolder": drive_subfolder if direction == "telegram_to_drive" else "",
                    "telegram_hashtag": telegram_hashtag if direction == "drive_to_telegram" else "",
                    **result,
                })
        except KeyboardInterrupt:
            print("\n⏹️ تم إيقاف التشغيل. الملفات الأصلية لم تُمس.")
        except Exception as exc:
            print(f"\n❌ تعذر بدء النقل: {type(exc).__name__}: {exc}")
            raise
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            show_log_controls(log_path)
