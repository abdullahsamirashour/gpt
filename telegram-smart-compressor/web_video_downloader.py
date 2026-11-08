# -*- coding: utf-8 -*-
"""Media Lite: independent Colab downloader for authorized web video links.

Supports direct video/media URLs, yt-dlp-supported page URLs, and best-effort
Awesome Screenshot share pages. No DRM, account, or download restriction bypass.
Downloads to Colab disk first; copying to Drive is done after a complete file.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import html
from html.parser import HTMLParser
import importlib
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urljoin, urlparse, unquote

STAGING_ROOT = Path("/content/media_lite_url_download_staging")
LOCAL_ROOT = Path("/content/Media_Lite_URL_Downloads")
DRIVE_ROOT = Path("/content/drive/MyDrive/Media_Lite_Downloads")
MEDIA_EXTENSIONS = (".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi", ".ts", ".m3u8", ".mpd")
FILE_EXTENSIONS = (".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi", ".ts")
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"


def _install_if_needed(module, package, force=False):
    try:
        if not force:
            return importlib.import_module(module)
    except ImportError:
        pass
    print(f"📦 تجهيز {package.split('=')[0]}...")
    command = [
        sys.executable, "-m", "pip", "install", "-q",
        "--disable-pip-version-check", "--no-input",
    ]
    if force:
        command.append("--upgrade")
    command.append(package)
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"تعذر تثبيت {package}: {(result.stderr or '')[-700:]}")
    importlib.invalidate_caches()
    return importlib.import_module(module)


def _safe_name(name, fallback="video"):
    name = unquote(str(name or "")).strip().replace("\x00", "")
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name[:175].strip(" .") or fallback)


def _normal_urls(lines):
    """Accept a paste of links separated by newlines, spaces, commas or semicolons."""
    raw = re.split(r"[\s;؛،]+|,(?=https?://)", str(lines or "").strip())
    urls, seen = [], set()
    for item in raw:
        url = item.strip().strip(",،؛;")
        if not url:
            continue
        parsed = urlparse(url)
        if parsed.scheme not in ("https", "http") or not parsed.netloc:
            raise ValueError("كل عنصر لازم يكون رابط http أو https صحيح.")
        if url not in seen:
            urls.append(url)
            seen.add(url)
    if not urls:
        raise ValueError("الصق رابط فيديو واحدًا على الأقل.")
    if len(urls) > 100:
        raise ValueError("الحد الأقصى 100 رابط مختلف في كل Batch.")
    return urls


def _direct_media(url):
    return urlparse(url).path.lower().endswith(MEDIA_EXTENSIONS)


def _awesome(url):
    return (urlparse(url).hostname or "").lower() in {
        "awesomescreenshot.com", "www.awesomescreenshot.com",
    }


def _yt_format(quality):
    if quality == "1080p":
        return "bv*[height<=1080]+ba/b[height<=1080]/b"
    if quality == "720p":
        return "bv*[height<=720]+ba/b[height<=720]/b"
    if quality == "480p":
        return "bv*[height<=480]+ba/b[height<=480]/b"
    return "bv*+ba/b"


def _ensure_ffmpeg():
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    print("🔧 تجهيز FFmpeg لدمج الصوت والفيديو عند الحاجة...")
    for cmd in (
        ["apt-get", "-qq", "update"],
        ["apt-get", "-qq", "install", "-y", "ffmpeg"],
    ):
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode:
            raise RuntimeError("تعذر تجهيز FFmpeg في Colab.")


def _media_files(folder):
    return sorted(
        (x for x in folder.iterdir()
         if x.is_file() and x.suffix.lower() in FILE_EXTENSIONS and x.stat().st_size > 0),
        key=lambda x: x.name.lower(),
    )


class _QuietLogger:
    def debug(self, msg):
        pass

    def warning(self, msg):
        # Avoid displaying private link keys or expiring signed CDN tokens.
        if "cookie" in str(msg).lower():
            print("⚠️ الموقع قد يحتاج تسجيل دخول.")
        else:
            print("⚠️ yt-dlp: تعذر قراءة بعض بيانات الموقع.")

    def error(self, msg):
        pass


def _yt_download(url, folder, quality, referer=None, update_ytdlp=False):
    ytdlp = _install_if_needed("yt_dlp", "yt-dlp", force=update_ytdlp)
    _ensure_ffmpeg()
    last_log = [0.0]

    def hook(data):
        now = time.monotonic()
        if data.get("status") == "downloading" and now - last_log[0] >= 2.5:
            last_log[0] = now
            percent = str(data.get("_percent_str", "...")).strip()
            speed = str(data.get("_speed_str", "...")).strip()
            print(f"⬇️ تحميل: {percent} • {speed}")
        elif data.get("status") == "finished":
            print("✅ اكتمل التنزيل، جاري تجهيز الملف النهائي...")

    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer
    params = {
        "format": _yt_format(quality),
        "outtmpl": str(folder / "%(title).160B [%(id)s].%(ext)s"),
        "noplaylist": True,
        "continuedl": True,
        "retries": 6,
        "fragment_retries": 6,
        "concurrent_fragment_downloads": 4,
        "socket_timeout": 35,
        "quiet": True,
        "no_warnings": True,
        "logger": _QuietLogger(),
        "progress_hooks": [hook],
        "http_headers": headers,
        "overwrites": False,
    }
    before = {p.name for p in _media_files(folder)}
    with ytdlp.YoutubeDL(params) as ydl:
        result = ydl.download([url])
    if result != 0:
        raise RuntimeError("الموقع لم يُرجع ملف فيديو صالحًا.")
    found = _media_files(folder)
    if not found:
        raise RuntimeError("انتهى yt-dlp بدون وجود ملف فيديو قابل للحفظ.")
    return [p for p in found if p.name not in before] or found


def _direct_download(url, folder, referer=None):
    requests = _install_if_needed("requests", "requests")
    parsed = urlparse(url)
    if parsed.path.lower().endswith((".m3u8", ".mpd")):
        return _yt_download(url, folder, "الأصلية", referer=referer)

    partial = folder / "direct_video.part"
    have = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    if have:
        headers["Range"] = f"bytes={have}-"
    started = time.monotonic()
    with requests.get(url, headers=headers, stream=True,
                      timeout=(20, 180), allow_redirects=True) as resp:
        if resp.status_code == 416 and have:
            partial.unlink(missing_ok=True)
            return _direct_download(url, folder, referer=referer)
        resp.raise_for_status()
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "text/html" in ctype or "application/json" in ctype:
            raise RuntimeError("الرابط رجّع صفحة ويب بدل ملف فيديو.")
        if resp.status_code == 206 and have:
            mode = "ab"
            total = have + int(resp.headers.get("Content-Length") or 0)
            done = have
        else:
            mode = "wb"
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
        disposition = resp.headers.get("Content-Disposition") or ""
        name_match = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"([^\"]+)\"|filename=([^;]+)", disposition, re.I)
        name = next((part for part in name_match.groups() if part), "") if name_match else ""
        if not name:
            name = Path(urlparse(resp.url).path).name or Path(parsed.path).name
        name = _safe_name(name, "video")
        if not name.lower().endswith(FILE_EXTENSIONS):
            ext = mimetypes.guess_extension(ctype.split(";")[0].strip()) or ".mp4"
            name += ext if ext in FILE_EXTENSIONS else ".mp4"
        last_log = 0.0
        with partial.open(mode) as out:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                out.write(chunk)
                done += len(chunk)
                now = time.monotonic()
                if now - last_log >= 3:
                    last_log = now
                    if total:
                        print(f"⬇️ {done / 1048576:.1f}/{total / 1048576:.1f} MiB • {done / total * 100:.1f}%")
                    else:
                        print(f"⬇️ {done / 1048576:.1f} MiB")
    final = folder / name
    if final.exists() and final != partial:
        final = _unique_path(folder, name)
    partial.replace(final)
    print(f"✅ تنزيل مباشر: {final.stat().st_size / 1048576:.1f} MiB • {time.monotonic() - started:.1f}s")
    return [final]


class _MediaHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        props = dict(attrs)
        tag = tag.lower()
        if tag in ("video", "source", "a"):
            for field in ("src", "data-src", "href"):
                if props.get(field):
                    candidate = html.unescape(props[field])
                    if tag in ("video", "source") or _direct_media(candidate):
                        self.urls.append(candidate)
        if tag == "meta":
            prop = (props.get("property") or props.get("name") or "").lower()
            if prop in ("og:video", "og:video:url", "og:video:secure_url",
                        "twitter:player:stream", "twitter:player:stream:content_type"):
                value = props.get("content") or ""
                if value.startswith(("http://", "https://")):
                    self.urls.append(value)


def _page_media_candidates(page_url):
    requests = _install_if_needed("requests", "requests")
    resp = requests.get(page_url, headers={"User-Agent": USER_AGENT}, timeout=(20, 45))
    resp.raise_for_status()
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "html" not in ctype and "text" not in ctype:
        return [resp.url] if _direct_media(resp.url) else []
    if len(resp.content) > 6 * 1024 * 1024:
        raise RuntimeError("صفحة كبيرة جدًا ولا يمكن تحليلها بأمان.")
    page = resp.text
    parser = _MediaHTMLParser()
    parser.feed(page)

    cleaned = html.unescape(page.replace("\\/", "/").replace("\\u002F", "/"))
    for found in re.findall(r'https?://[^\s"\'<>\\]+', cleaned, flags=re.I):
        found = found.rstrip(",;)]}")
        if _direct_media(found) or re.search(r"\.(?:mp4|m3u8|webm|mov|mpd)(?:[?#]|$)", found, re.I):
            parser.urls.append(found)

    for matched in re.finditer(
        r'["\'](?:videoUrl|video_url|playbackUrl|playback_url|downloadUrl|download_url|fileUrl|file_url)["\']\s*:\s*["\']([^"\']+)',
        cleaned, re.I,
    ):
        parser.urls.append(matched.group(1))

    candidates = []
    seen = set()
    for candidate in parser.urls:
        candidate = html.unescape(str(candidate)).replace("\\/", "/")
        if candidate.startswith(("blob:", "data:")):
            continue
        candidate = urljoin(page_url, candidate)
        parts = urlparse(candidate)
        if parts.scheme not in ("https", "http") or not parts.netloc:
            continue
        if candidate != page_url and candidate not in seen:
            candidates.append(candidate)
            seen.add(candidate)
    return candidates[:20]


def _browser_media_candidates(page_url):
    """Inspect the rendered player, nested frames, and actual media requests.

    This is a best-effort public share-page extractor. It does not log in to
    unrelated accounts, bypass DRM, or override site download restrictions.
    """
    _install_if_needed("playwright", "playwright")
    from playwright.sync_api import sync_playwright

    def collect():
        captured = []
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            try:
                context = browser.new_context(user_agent=USER_AGENT)
                page = context.new_page()

                def add_url(value):
                    if not value or not isinstance(value, str):
                        return
                    value = html.unescape(value.strip().replace("\\/", "/"))
                    if value.startswith(("blob:", "data:", "javascript:")):
                        return
                    absolute = urljoin(page_url, value)
                    scheme = urlparse(absolute).scheme
                    if scheme in ("http", "https"):
                        captured.append(absolute)

                def record_request(request):
                    url = request.url
                    path = urlparse(url).path.lower()
                    if path.endswith(MEDIA_EXTENSIONS):
                        add_url(url)

                def record_response(response):
                    try:
                        media_type = (response.headers.get("content-type") or "").lower()
                        if (media_type.startswith(("video/", "audio/"))
                                or "application/vnd.apple.mpegurl" in media_type
                                or "application/x-mpegurl" in media_type
                                or "application/dash+xml" in media_type
                                or "application/octet-stream" in media_type
                                and urlparse(response.url).path.lower().endswith(FILE_EXTENSIONS)):
                            add_url(response.url)
                    except Exception:
                        return

                page.on("request", record_request)
                page.on("response", record_response)
                page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3500)

                # Each frame (including embedded players) may own its video tag.
                # Try ordinary video.play() and a non-navigating play button.
                for frame in list(page.frames):
                    try:
                        urls = frame.evaluate("""() => {
                          const nodes = Array.from(document.querySelectorAll(
                            'video, source, meta[property="og:video"], meta[property="og:video:url"]'
                          ));
                          return nodes.flatMap(el => [
                            el.currentSrc, el.src, el.getAttribute('src'),
                            el.getAttribute('data-src'), el.getAttribute('content')
                          ]).filter(Boolean);
                        }""")
                        for item in urls:
                            add_url(item)
                    except Exception:
                        pass
                    try:
                        frame.evaluate("""() => {
                          document.querySelectorAll('video').forEach(video => {
                            video.muted = true;
                            video.play().catch(() => {});
                          });
                        }""")
                    except Exception:
                        pass
                    try:
                        buttons = frame.locator(
                            'button[aria-label*="Play" i], '
                            'button[title*="Play" i], '
                            '[data-testid*="play" i]'
                        )
                        if buttons.count():
                            buttons.first.click(timeout=1600, no_wait_after=True)
                    except Exception:
                        pass

                page.wait_for_timeout(7000)
                for frame in list(page.frames):
                    try:
                        urls = frame.evaluate("""() => Array.from(
                          document.querySelectorAll('video, source')
                        ).flatMap(el => [el.currentSrc, el.src, el.getAttribute('src')])
                         .filter(Boolean)""")
                        for item in urls:
                            add_url(item)
                    except Exception:
                        pass

                unique = []
                seen = set()
                for candidate in captured:
                    if candidate not in seen and candidate != page_url:
                        unique.append(candidate)
                        seen.add(candidate)
                return unique[:35]
            finally:
                browser.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(collect).result(timeout=120)


def _ensure_browser():
    """Install browser *and Linux runtime libraries* if needed by Colab."""
    _install_if_needed("playwright", "playwright")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser_exists = Path(p.chromium.executable_path).exists()
    if browser_exists:
        return
    print("🌐 أول تشغيل فقط: تجهيز Chromium ومكتبات Linux اللازمة...")
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
        capture_output=True, text=True, timeout=360,
    )
    if result.returncode:
        details = (result.stderr or result.stdout or "").strip().splitlines()
        reason = details[-1] if details else "غير معروف"
        raise RuntimeError(f"فشل تثبيت متصفح Chromium: {reason[:250]}")


def _install_browser_linux_dependencies():
    """Repair missing system libraries if a cached browser cannot launch."""
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install-deps", "chromium"],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode:
        details = (result.stderr or result.stdout or "").strip().splitlines()
        reason = details[-1] if details else "غير معروف"
        raise RuntimeError(f"فشل تجهيز مكتبات تشغيل Chromium: {reason[:250]}")


def _unique_path(folder, filename):
    candidate = folder / filename
    if not candidate.exists():
        return candidate
    name, suffix = Path(filename).stem, Path(filename).suffix
    for idx in range(2, 1000):
        candidate = folder / f"{name} ({idx}){suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("تعذر اختيار اسم ملف جديد.")


def _save_files(paths, dest):
    final_files = []
    for path in paths:
        target = _unique_path(dest, _safe_name(path.name))
        # Local staging avoids writing incomplete downloads to mounted Drive.
        shutil.copy2(path, target)
        if target.stat().st_size != path.stat().st_size:
            target.unlink(missing_ok=True)
            raise RuntimeError("فشل التحقق من حجم الملف بعد نسخه إلى الوجهة.")
        final_files.append(target)
    return final_files


def _get_destination(destination, subfolder):
    if destination == "Google Drive":
        from google.colab import drive
        if not os.path.ismount("/content/drive"):
            drive.mount("/content/drive", force_remount=False)
        root = DRIVE_ROOT
    else:
        root = LOCAL_ROOT
    if subfolder.strip():
        root = root / _safe_name(subfolder, "Downloads")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _download_url(url, stage, quality, browser_fallback, update_ytdlp):
    if _direct_media(url):
        if urlparse(url).path.lower().endswith((".m3u8", ".mpd")):
            return _yt_download(url, stage, quality, update_ytdlp=update_ytdlp)
        return _direct_download(url, stage)

    errors = []
    try:
        return _yt_download(url, stage, quality, update_ytdlp=update_ytdlp)
    except Exception as exc:
        errors.append(type(exc).__name__)
        print("↪️ المحاولة الأولى لم تعثر على فيديو؛ جاري البحث داخل الصفحة.")

    try:
        candidates = _page_media_candidates(url)
    except Exception as exc:
        candidates = []
        errors.append(type(exc).__name__)

    # Try static page candidates first. A page may contain preview URLs that
    # fail; that must not prevent the JavaScript/browser fallback.
    for candidate in candidates:
        try:
            if _direct_media(candidate):
                if urlparse(candidate).path.lower().endswith((".m3u8", ".mpd")):
                    return _yt_download(candidate, stage, quality, referer=url)
                return _direct_download(candidate, stage, referer=url)
            return _yt_download(candidate, stage, quality, referer=url)
        except Exception as exc:
            errors.append(type(exc).__name__)

    if browser_fallback and _awesome(url):
        browser_candidates = []
        try:
            print("🌐 محاولة استخراج مصدر الفيديو من مشغل Awesome Screenshot...")
            _ensure_browser()
            try:
                browser_candidates = _browser_media_candidates(url)
            except Exception as exc:
                # A downloaded Chromium binary may lack shared libraries on Colab.
                detail = str(exc)
                if ("Host system is missing dependencies" in detail
                        or "error while loading shared libraries" in detail
                        or "libnss3" in detail):
                    print("🔧 Chromium موجود لكن مكتبات Linux ناقصة؛ جاري تجهيزها...")
                    _install_browser_linux_dependencies()
                    browser_candidates = _browser_media_candidates(url)
                else:
                    raise
            print(f"🔎 مشغل الصفحة: {len(browser_candidates)} رابط وسائط محتمل.")
        except Exception as exc:
            errors.append(type(exc).__name__)
            # Never print a signed URL or a private share key in an error.
            reason = _redact_error(str(exc), url)
            print(f"⚠️ فشل تشغيل متصفح استخراج الفيديو: {type(exc).__name__} — {reason[:280]}")

        for candidate in browser_candidates:
            try:
                if _direct_media(candidate):
                    if urlparse(candidate).path.lower().endswith((".m3u8", ".mpd")):
                        return _yt_download(candidate, stage, quality, referer=url)
                    return _direct_download(candidate, stage, referer=url)
                return _yt_download(candidate, stage, quality, referer=url)
            except Exception as exc:
                errors.append(type(exc).__name__)

    if _awesome(url):
        raise RuntimeError(
            "صفحة المشاركة لم تُرجع ملف فيديو يمكن تنزيله تلقائيًا. "
            "لو زر Download متاح لصاحب التسجيل، استخدمه أو انسخ رابط تنزيل الملف "
            "المباشر إلى هذه الخلية. بعض روابط المشاهدة لا تسمح بالتنزيل من حساب آخر."
        )
    raise RuntimeError("لم نتمكن من تنزيل فيديو من الرابط؛ جرّب رابط الملف المباشر أو موقعًا مدعومًا من yt-dlp.")



def _load_transfer_engine(commit):
    """Reuse the existing transfer channel, isolated session, and six-way uploader."""
    import types
    import urllib.request

    if not re.fullmatch(r"[0-9a-f]{40}", str(commit or "")):
        raise RuntimeError("تعذر تحديد إصدار محرك النقل المطابق للـNotebook.")
    cached = Path("/content/media_lite_url_transfer_engine.py")
    stamp = cached.with_suffix(".commit")
    source = None
    if cached.exists() and stamp.exists():
        try:
            if stamp.read_text(encoding="ascii").strip() == commit:
                saved = cached.read_text(encoding="utf-8")
                if len(saved) > 50000:
                    source = saved
        except (OSError, UnicodeError):
            pass
    if source is None:
        url = (f"https://raw.githubusercontent.com/abdullahsamirashour/gpt/"
               f"{commit}/telegram-smart-compressor/transfer_engine.py")
        request = urllib.request.Request(url, headers={"User-Agent": "Media-Lite-Web-Downloader"})
        with urllib.request.urlopen(request, timeout=45) as response:
            source = response.read().decode("utf-8")
        if len(source) < 50000:
            raise RuntimeError("محرك Telegram غير مكتمل؛ لن نرفع الملفات.")
        temp = cached.with_suffix(".tmp")
        temp.write_text(source, encoding="utf-8")
        os.replace(temp, cached)
        stamp_temp = stamp.with_suffix(".tmp")
        stamp_temp.write_text(commit, encoding="ascii")
        os.replace(stamp_temp, stamp)
    module = types.ModuleType("media_lite_url_transfer")
    module.__file__ = str(cached)
    exec(compile(source, str(cached), "exec"), module.__dict__)
    return module


async def _existing_transfer_channel(client, transfer, settings):
    """Locate the already-used private transfer channel; never create a channel."""
    from telethon import types

    def usable(channel):
        rights = getattr(channel, "admin_rights", None)
        can_post = bool(getattr(channel, "creator", False) or
                        getattr(rights, "post_messages", False))
        return bool(getattr(channel, "broadcast", False) and can_post
                    and not getattr(channel, "username", None))

    saved_id = settings.get("channel_id")
    if saved_id:
        try:
            existing = await client.get_entity(types.PeerChannel(int(saved_id)))
            if usable(existing) and getattr(existing, "title", None) == transfer.CHANNEL_TITLE:
                print(f"📣 قناة النقل المحفوظة: {transfer.CHANNEL_TITLE}")
                return existing
        except Exception:
            pass

    matches = []
    async for dialog in client.iter_dialogs():
        if dialog.name == transfer.CHANNEL_TITLE and usable(dialog.entity):
            matches.append(dialog.entity)
    if len(matches) != 1:
        raise RuntimeError(
            "تعذر تحديد قناة 📁 Media Lite | File Transfer الموجودة بشكل آمن. "
            "شغّل خلية Drive → Telegram الأصلية لتجهيز القناة/الجلسة، "
            "ثم أعد المحاولة هنا. لن ننشئ قناة جديدة تلقائيًا."
        )
    channel = matches[0]
    settings["channel_id"] = int(channel.id)
    transfer.save_json(transfer.TRANSFER_CONFIG, settings)
    print(f"📣 هنستخدم قناة النقل القديمة: {transfer.CHANNEL_TITLE}")
    return channel


async def _valid_telegram_receipt(client, channel, msg_id, expected_size):
    if not msg_id:
        return False
    # A network failure must not be interpreted as a missing message.
    msg = await client.get_messages(channel, ids=int(msg_id))
    return bool(msg and getattr(msg, "file", None) and
                int(getattr(msg.file, "size", 0) or 0) == expected_size)



# One output format per batch. Video MP4 is the default for Telegram playback.
FORMAT_ALIASES = {
    "mp4": "mp4", "webm": "webm", "mp3": "mp3",
    "m4a": "m4a", "ogg": "ogg", "zip": "zip", "original": "original",
}
AUDIO_MIME = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "ogg": "audio/ogg"}


def _probe_media(path):
    _ensure_ffmpeg()
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode:
        raise RuntimeError(f"FFprobe لم يتمكن من قراءة الملف: {(result.stderr or '')[-280:]}")
    try:
        info = __import__("json").loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError("تعذر قراءة معلومات الملف الناتج.") from exc
    video = next((x for x in info.get("streams", []) if x.get("codec_type") == "video"), None)
    audio = next((x for x in info.get("streams", []) if x.get("codec_type") == "audio"), None)
    duration = (info.get("format") or {}).get("duration")
    try:
        seconds = float(duration or (video or audio or {}).get("duration") or 0)
    except (ValueError, TypeError):
        seconds = 0.0
    return {
        "video": video, "audio": audio,
        "duration": max(0, int(seconds)),
        "width": int((video or {}).get("width") or 0),
        "height": int((video or {}).get("height") or 0),
    }


def _encode_media(src, dest, args):
    _ensure_ffmpeg()
    print(f"🔄 تجهيز {dest.suffix.upper().lstrip('.')} (بدون تغيير إعدادات الضغط الأساسية)...")
    started = time.monotonic()
    temp = dest.with_name(dest.stem + ".incomplete" + dest.suffix)
    temp.unlink(missing_ok=True)
    command = [
        "ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(src), *args, str(temp),
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode or not temp.exists() or temp.stat().st_size == 0:
        temp.unlink(missing_ok=True)
        raise RuntimeError(
            "فشل تحويل الملف بهذه الصيغة: " + (completed.stderr or "")[-650:]
        )
    os.replace(temp, dest)
    print(f"✅ تم التحويل خلال {time.monotonic() - started:.1f} ثانية")


def _prepare_file(src, output_dir, fmt, audio_kbps=96, video_crf=20):
    """Return the output file and its logical kind, keeping source intact."""
    import zipfile
    fmt = FORMAT_ALIASES.get(fmt, fmt)
    if fmt not in FORMAT_ALIASES:
        raise ValueError("صيغة الإخراج المطلوبة غير معروفة.")
    audio_kbps = int(audio_kbps)
    video_crf = int(video_crf)
    if not 24 <= audio_kbps <= 192 or not 18 <= video_crf <= 32:
        raise ValueError("قيمة Bitrate أو CRF خارج النطاق المسموح.")

    src = Path(src)
    if fmt == "original":
        return src, "original"

    ext = {
        "mp4": ".mp4", "webm": ".webm",
        "mp3": ".mp3", "m4a": ".m4a", "ogg": ".ogg", "zip": ".zip",
    }[fmt]
    output_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(
        f"{src.name}|{src.stat().st_size}|{fmt}|{audio_kbps}|{video_crf}".encode("utf-8")
    ).hexdigest()[:9]
    output = output_dir / f"{_safe_name(src.stem)[:100]}_{fingerprint}{ext}"
    if output.exists() and output.stat().st_size > 0:
        print(f"♻️ موجود من محاولة سابقة: {output.name}")
        return output, fmt

    if fmt == "zip":
        temp = output.with_name(output.name + ".incomplete")
        temp.unlink(missing_ok=True)
        try:
            with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                archive.write(src, arcname=_safe_name(src.name))
            os.replace(temp, output)
        finally:
            temp.unlink(missing_ok=True)
        print("📦 تم إنشاء ZIP من الملف الأصلي بدون إعادة ترميز.")
        return output, fmt

    streams = _probe_media(src)
    if fmt in ("mp4", "webm") and not streams["video"]:
        raise RuntimeError("الملف مفيهوش مسار فيديو للتحويل للصيغة المختارة.")
    if fmt in ("mp3", "m4a", "ogg") and not streams["audio"]:
        raise RuntimeError("الفيديو مفيهوش مسار صوتي؛ مش هنطلع ملف صامت.")

    video_codec = str((streams["video"] or {}).get("codec_name") or "").lower()
    audio_codec = str((streams["audio"] or {}).get("codec_name") or "").lower()
    if fmt == "mp4":
        # Stream copy/remux on H.264 + AAC avoids a needless lossy conversion.
        if video_codec == "h264" and audio_codec in ("aac", ""):
            opts = ["-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                    "-movflags", "+faststart"]
            print("⚡ الفيديو H.264/AAC بالفعل؛ إعادة تغليف MP4 بدون إعادة ضغط.")
        else:
            video_opts = (["-c:v", "copy"] if video_codec == "h264" else
                          ["-c:v", "libx264", "-preset", "veryfast", "-crf",
                           str(video_crf), "-pix_fmt", "yuv420p",
                           "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"])
            audio_opts = (["-c:a", "copy"] if audio_codec in ("aac", "") else
                          ["-c:a", "aac", "-b:a", "128k"])
            opts = ["-map", "0:v:0", "-map", "0:a:0?", *video_opts,
                    *audio_opts, "-movflags", "+faststart"]
    elif fmt == "webm":
        if src.suffix.lower() == ".webm" and video_codec in ("vp8", "vp9", "av1") and audio_codec in ("opus", "vorbis", ""):
            return src, "webm"
        opts = [
            "-map", "0:v:0", "-map", "0:a:0?",
            "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "5",
            "-crf", "30", "-b:v", "0", "-c:a", "libopus",
            "-b:a", f"{audio_kbps}k",
        ]
    else:
        codec = {
            "mp3": ["-c:a", "libmp3lame", "-b:a", f"{audio_kbps}k"],
            "m4a": ["-c:a", "aac", "-b:a", f"{audio_kbps}k"],
            "ogg": ["-c:a", "libopus", "-b:a", f"{audio_kbps}k", "-vbr", "on"],
        }[fmt]
        opts = ["-map", "0:a:0", "-vn", "-sn", "-dn", *codec]
    _encode_media(src, output, opts)
    return output, fmt


def _telegram_media_kind(file, fmt, show_as):
    """Only preview known playable formats; never pretend ZIP/WebM is MP4."""
    if show_as != "player":
        return "document"
    if fmt == "mp4":
        return "video"
    if fmt in AUDIO_MIME:
        return "audio"
    if fmt == "original":
        extension = file.suffix.lower()
        if extension in (".mp3", ".m4a", ".ogg"):
            return "audio"
        if extension == ".mp4":
            metadata = _probe_media(file)
            v = (metadata["video"] or {}).get("codec_name")
            a = (metadata["audio"] or {}).get("codec_name")
            if v == "h264" and a in ("aac", None):
                return "video"
    return "document"


def _generate_thumb(file):
    """Best-effort Telegram thumbnail for playable MP4."""
    thumb = file.with_name(file.stem + "_telegram_thumbnail.jpg")
    if thumb.exists() and 0 < thumb.stat().st_size < 200000:
        return str(thumb)
    result = subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
         "-ss", "00:00:01", "-i", str(file), "-frames:v", "1",
         "-vf", "scale=320:320:force_original_aspect_ratio=decrease",
         "-q:v", "7", str(thumb)],
        capture_output=True, text=True,
    )
    if result.returncode or not thumb.exists() or not (0 < thumb.stat().st_size < 200000):
        thumb.unlink(missing_ok=True)
        return None
    return str(thumb)


async def _send_playable_file(client, channel, transfer, uploader,
                              file, size, name, caption, progress, kind):
    """Reuse existing 6-connection uploader; send native Telegram video/audio."""
    from telethon.tl.types import (
        DocumentAttributeFilename, DocumentAttributeVideo, DocumentAttributeAudio,
    )

    handle = None
    if (uploader is not None and not uploader.disabled
            and size >= transfer.TG_UPLOAD_MIN_BYTES):
        try:
            handle = await uploader.upload_file(str(file), size, name, progress)
        except Exception as exc:
            if type(exc).__name__ in ("FloodWaitError", "FloodPremiumWaitError"):
                raise
            uploader.disabled = True
            await uploader.close()
            print("↪️ تعذر الرفع المتوازي؛ هنكمل بنفس مسار Telethon الاحتياطي.")

    if handle is None:
        handle = await client.upload_file(
            str(file), file_size=size, file_name=name,
            part_size_kb=transfer.UPLOAD_PART_KB, progress_callback=progress,
        )

    info = _probe_media(file)
    if kind == "video":
        thumb = _generate_thumb(file)
        args = dict(
            mime_type="video/mp4", force_document=False,
            supports_streaming=True,
            attributes=[
                DocumentAttributeFilename(name),
                DocumentAttributeVideo(
                    duration=info["duration"], w=max(1, info["width"]),
                    h=max(1, info["height"]), supports_streaming=True,
                ),
            ],
        )
        if thumb:
            args["thumb"] = thumb
    else:
        mimetype = AUDIO_MIME.get(file.suffix.lower().lstrip("."), "audio/mpeg")
        args = dict(
            mime_type=mimetype, force_document=False,
            attributes=[
                DocumentAttributeFilename(name),
                DocumentAttributeAudio(
                    duration=info["duration"], voice=False,
                    title=file.stem[:95], performer="",
                ),
            ],
        )
    return await client.send_file(channel, handle, caption=caption, **args)


async def _upload_telegram_video(client, channel, transfer, state,
                                 uploader, file, source_url, quality,
                                 fmt="mp4", show_as="player",
                                 audio_kbps=96, video_crf=20):
    """Upload files to the existing channel, with actual Telegram playback metadata."""
    import math
    import json

    file = Path(file)
    size = file.stat().st_size
    if not size:
        raise RuntimeError("الملف الناتج فارغ؛ لا يمكن رفعه.")
    name = transfer.clean_filename(file.name)
    kind = _telegram_media_kind(file, fmt, show_as)
    token = hashlib.sha256(
        f"{source_url}\n{name}\n{size}\n{quality}\n{fmt}\n{show_as}\n{audio_kbps}\n{video_crf}"
        .encode("utf-8")
    ).hexdigest()[:18]
    records = state.setdefault("web_video_uploads", {})
    receipt = records.get(token)
    if not isinstance(receipt, dict) or receipt.get("channel_id") != int(channel.id):
        receipt = {
            "channel_id": int(channel.id), "name": name,
            "size": size, "parts": {}, "done": False, "format": fmt,
            "telegram_display": kind,
        }
        records[token] = receipt
        transfer.save_json(transfer.STATE_PATH, state)

    parts_count = max(1, math.ceil(size / transfer.MAX_TG_PART))
    saved_parts = receipt.setdefault("parts", {})

    for number in range(1, parts_count + 1):
        offset = (number - 1) * transfer.MAX_TG_PART
        count = min(transfer.MAX_TG_PART, size - offset)
        recorded_id = saved_parts.get(str(number))
        if await _valid_telegram_receipt(client, channel, recorded_id, count):
            print(f"↪️ الجزء {number}/{parts_count} موجود بالقناة، مش هنكرره.")
            continue

        part_name = name if parts_count == 1 else f"{name}.part{number:03d}"
        marker = f"MLTX:{token}:{number}/{parts_count}"
        category = {"video": "🎬 فيديو", "audio": "🎧 صوت",
                    "document": "📁 ملف"}.get(kind, "📁 ملف")
        caption = f"🌐 {category} من الإنترنت\n{name[:300]}\n{marker}"
        progress = transfer.Progress(f"⬆️ Telegram [{number}/{parts_count}] {part_name}")

        if parts_count == 1 and kind in {"video", "audio"}:
            message = await _send_playable_file(
                client, channel, transfer, uploader, file, count, part_name,
                caption, progress, kind,
            )
        elif parts_count == 1:
            message = await transfer.send_transfer_document(
                client, channel, str(file), count, part_name, caption,
                progress, parallel_uploader=uploader,
            )
        else:
            if number == 1:
                print("ℹ️ الملف أكبر من حد Telegram؛ هيترفع كأجزاء Document مع ملف تجميع.")
            with transfer.SegmentReader(file, offset, count, part_name) as segment:
                message = await transfer.send_transfer_document(
                    client, channel, segment, count, part_name, caption,
                    progress, raw_part=True, parallel_uploader=uploader,
                )

        actual_size = int(getattr(getattr(message, "file", None), "size", 0) or 0)
        if actual_size and actual_size != count:
            raise RuntimeError("حجم الملف المرفوع لا يطابق حجم الجزء الأصلي.")
        saved_parts[str(number)] = int(message.id)
        receipt["done"] = False
        transfer.remember_outbound_telegram_id(state, channel, int(message.id))
        transfer.save_json(transfer.STATE_PATH, state)

    if parts_count > 1:
        manifest_id = receipt.get("manifest_id")
        expected_manifest_size = int(receipt.get("manifest_size", 0) or -1)
        if not await _valid_telegram_receipt(
            client, channel, manifest_id, expected_manifest_size
        ):
            manifest = {
                "format": "media-lite-parts-v1",
                "name": name, "size": size, "part_size": transfer.MAX_TG_PART,
                "parts": [
                    {"name": f"{name}.part{i:03d}",
                     "message_id": saved_parts[str(i)]}
                    for i in range(1, parts_count + 1)
                ],
                "restore": "Concatenate the .part files in ascending order (binary mode).",
            }
            file_path = file.parent / f"{name}.parts.json"
            file_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            try:
                note = await client.send_file(
                    channel, str(file_path), force_document=True,
                    caption=f"📋 ملف تجميع أجزاء الفيديو\nMLTX:{token}:manifest",
                )
            finally:
                file_path.unlink(missing_ok=True)
            receipt["manifest_id"] = int(note.id)
            receipt["manifest_size"] = int(
                getattr(getattr(note, "file", None), "size", 0) or 0
            )
            transfer.remember_outbound_telegram_id(state, channel, int(note.id))
            transfer.save_json(transfer.STATE_PATH, state)

    receipt["done"] = True
    receipt["uploaded_at"] = int(time.time())
    transfer.save_json(transfer.STATE_PATH, state)
    print(f"✅ اترفع للقناة: {name} ({size / 1048576:.1f} MiB) — {kind}")
    return file

def _stage_for_url(url, quality):
    # Source downloads are shared across output formats, but not across qualities.
    key = hashlib.sha256(f"{url}\n{quality}".encode("utf-8")).hexdigest()[:18]
    stage = STAGING_ROOT / key
    stage.mkdir(parents=True, exist_ok=True)
    return stage


def _source_files(url, stage, quality, browser_fallback):
    existing = _media_files(stage)
    if existing:
        print(f"♻️ ملف الفيديو الأصلي موجود من محاولة سابقة: {len(existing)} ملف.")
        return existing
    return _download_url(url, stage, quality, browser_fallback, False)


async def _telegram_flow(urls, transfer, quality, browser_fallback,
                         account, login_method, fmt, show_as,
                         audio_kbps, video_crf):
    import asyncio
    from telethon import TelegramClient

    state = transfer.load_json(transfer.STATE_PATH, transfer.new_state())
    settings = transfer.load_json(transfer.TRANSFER_CONFIG, {})
    if transfer.PERSISTENT_SESSION.exists():
        shutil.copy2(transfer.PERSISTENT_SESSION, transfer.LOCAL_SESSION)
    else:
        transfer.LOCAL_SESSION.unlink(missing_ok=True)

    client = TelegramClient(
        str(transfer.LOCAL_SESSION.with_suffix("")),
        int(account["api_id"]), account["api_hash"],
        connection_retries=5, request_retries=5,
    )
    uploader = None
    success, failed = [], 0
    try:
        await transfer.authorize_telegram(client, account["phone"], login_method)
        me = await client.get_me()
        expected = re.sub(r"\D", "", str(account["phone"]))
        actual = re.sub(r"\D", "", str(getattr(me, "phone", "")))
        if actual and expected != actual:
            raise RuntimeError("جلسة Telegram تخص حسابًا مختلفًا عن الحساب المحفوظ.")
        channel = await _existing_transfer_channel(client, transfer, settings)
        if transfer.TelegramParallelUploader.supported(client):
            uploader = transfer.TelegramParallelUploader(client)
            print("⚡ الرفع يستخدم محرك الـ6 اتصالات الموجود في نظام النقل.")
        else:
            print("ℹ️ الرفع المتوازي غير مدعوم في هذه الجلسة؛ Telethon سيستخدم المسار الاحتياطي.")
        print(f"📋 Batch: {len(urls)} رابط • صيغة {fmt} • العرض {show_as}")
        for idx, url in enumerate(urls, 1):
            print(f"\n━━━━━━━━━━ رابط {idx}/{len(urls)} ━━━━━━━━━━")
            print(f"🌐 الموقع: {urlparse(url).hostname}")
            stage = _stage_for_url(url, quality)
            try:
                # Download/transcode in a worker so Telegram's event loop stays responsive.
                files = await asyncio.to_thread(
                    _source_files, url, stage, quality, browser_fallback
                )
                if not files:
                    raise RuntimeError("لم ينتج أي ملف فيديو.")
                for file in files:
                    output, actual_fmt = await asyncio.to_thread(
                        _prepare_file, file, stage / "prepared",
                        fmt, audio_kbps, video_crf,
                    )
                    uploaded = await _upload_telegram_video(
                        client, channel, transfer, state, uploader, output,
                        url, quality, fmt=actual_fmt, show_as=show_as,
                        audio_kbps=audio_kbps, video_crf=video_crf,
                    )
                    success.append(uploaded.name)
                shutil.rmtree(stage, ignore_errors=True)
                print(f"✅ انتهى الرابط {idx}/{len(urls)}")
            except Exception as exc:
                failed += 1
                detail = _redact_error(str(exc), url)
                print(f"❌ فشل الرابط {idx}/{len(urls)}: {detail[:450]}")
                print("💾 الملفات المكتملة أو الجزئية المحلية باقية في Colab للاستكمال.")
        print(f"\n🏁 Batch: نجاح {len(success)} ملف • فشل {failed} رابط • إجمالي {len(urls)} روابط")
        print(f"📣 القناة: {transfer.CHANNEL_TITLE}")
        return success
    finally:
        if uploader is not None:
            await uploader.close()
        try:
            await client.disconnect()
        finally:
            if transfer.LOCAL_SESSION.exists():
                shutil.copy2(transfer.LOCAL_SESSION, transfer.PERSISTENT_SESSION)


def _redact_error(detail, original_url):
    detail = str(detail).replace(original_url, "[private video URL]")
    return re.sub(
        r"([?&](?:key|token|sig|signature|auth|access_token)=)[^&\s]+",
        r"\1[hidden]", detail, flags=re.I,
    )


def _run_telegram(urls, quality, browser_fallback,
                  update_ytdlp, login_method, repo_commit, fmt, show_as,
                  audio_kbps, video_crf):
    import asyncio
    if login_method not in {"qr", "code"}:
        raise ValueError("طريقة دخول Telegram غير معروفة.")
    if update_ytdlp:
        _install_if_needed("yt_dlp", "yt-dlp", force=True)

    # No duplicate video on Drive: only Telegram auth/session/state are persisted.
    from google.colab import drive
    if not os.path.ismount("/content/drive"):
        drive.mount("/content/drive", force_remount=False)
    transfer = _load_transfer_engine(repo_commit)
    transfer.APP_DIR.mkdir(parents=True, exist_ok=True)
    transfer.TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    transfer.ensure_dependencies()
    account = transfer.load_account_config()
    import nest_asyncio
    nest_asyncio.apply()
    print("📣 الوجهة: قناة Media Lite | File Transfer القديمة — بدون رفع نسخة إلى Drive.")
    with transfer.single_run():
        return asyncio.get_event_loop().run_until_complete(
            _telegram_flow(
                urls, transfer, quality, browser_fallback, account, login_method,
                fmt, show_as, audio_kbps, video_crf,
            )
        )


def run(urls_text, destination="Google Drive", subfolder="",
        quality="الأصلية", browser_fallback=True, update_ytdlp=False,
        telegram_login="qr", repo_commit=None, output_format="mp4",
        telegram_display="player", audio_kbps=96, video_crf=20):
    """Native Colab Forms entry point. Downloads every unique URL in the batch."""
    urls = _normal_urls(urls_text)
    fmt = FORMAT_ALIASES.get(output_format)
    if fmt is None:
        raise ValueError("صيغة إخراج غير معروفة.")
    if telegram_display not in {"player", "document"}:
        raise ValueError("طريقة عرض Telegram غير معروفة.")
    audio_kbps = int(audio_kbps)
    video_crf = int(video_crf)
    if not 24 <= audio_kbps <= 192 or not 18 <= video_crf <= 32:
        raise ValueError("Bitrate أو CRF خارج المدى المسموح.")
    print(f"🔗 Batch Download: {len(urls)} روابط مختلفة • الصيغة {fmt}")

    if destination == "Telegram":
        if subfolder.strip():
            print("ℹ️ اسم المجلد الفرعي خاص بـGoogle Drive فقط.")
        return _run_telegram(
            urls, quality, browser_fallback, update_ytdlp,
            telegram_login, repo_commit, fmt, telegram_display,
            audio_kbps, video_crf,
        )
    if destination not in {"Google Drive", "Colab فقط"}:
        raise ValueError("اختار Google Drive أو Telegram أو Colab فقط.")
    if update_ytdlp:
        _install_if_needed("yt_dlp", "yt-dlp", force=True)
    target = _get_destination(destination, subfolder)
    print(f"📁 مكان الحفظ: {target}")
    print(f"🎞️ الناتج: {fmt} | جودة المصدر: {quality}")
    successful, failed = [], 0
    for idx, url in enumerate(urls, 1):
        print(f"\n━━━━━━━━━━ رابط {idx}/{len(urls)} ━━━━━━━━━━")
        print(f"🌐 الموقع: {urlparse(url).hostname}")
        stage = _stage_for_url(url, quality)
        try:
            files = _source_files(url, stage, quality, browser_fallback)
            if not files:
                raise RuntimeError("لم ينتج أي ملف فيديو.")
            prepared = []
            for file in files:
                output, _kind = _prepare_file(
                    file, stage / "prepared", fmt, audio_kbps, video_crf
                )
                prepared.append(output)
            copies = _save_files(prepared, target)
            for copied in copies:
                print(f"✅ اتحفظ: {copied.name} ({copied.stat().st_size / 1048576:.1f} MiB)")
                successful.append(copied)
            shutil.rmtree(stage, ignore_errors=True)
            print(f"✅ انتهى الرابط {idx}/{len(urls)}")
        except Exception as exc:
            failed += 1
            detail = _redact_error(str(exc), url)
            print(f"❌ فشل الرابط {idx}/{len(urls)}: {detail[:450]}")
            print("💾 أي ملفات جزئية باقية في Colab للتكملة داخل نفس Runtime.")
    print(f"\n🏁 Batch: نجاح {len(successful)} ملف • فشل {failed} رابط • الإجمالي {len(urls)} روابط")
    print(f"📁 الملفات المكتملة: {target}")
    return successful
