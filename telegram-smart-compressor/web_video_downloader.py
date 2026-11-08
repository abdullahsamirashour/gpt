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
    urls = [item.strip() for line in (lines or "").splitlines()
            for item in line.split() if item.strip()]
    if not urls:
        raise ValueError("الصق رابط فيديو واحدًا على الأقل.")
    if len(urls) > 30:
        raise ValueError("الحد الأقصى 30 رابط في التشغيل الواحد.")
    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme not in ("https", "http") or not parsed.netloc:
            raise ValueError("مسموح بروابط http أو https فقط.")
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
    """Optional slow fallback for JS players; no login, access or DRM bypass."""
    _install_if_needed("playwright", "playwright")
    from playwright.sync_api import sync_playwright

    def collect():
        captured = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                def record(request):
                    if _direct_media(request.url):
                        captured.append(request.url)
                page.on("request", record)
                page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3500)
                try:
                    page.locator("video").first.evaluate(
                        "(video) => { video.muted = true; video.play().catch(() => {}); }",
                        timeout=3000,
                    )
                except Exception:
                    pass
                page.wait_for_timeout(4500)
                try:
                    sources = page.locator("video, source").evaluate_all(
                        "(els) => els.flatMap(el => [el.currentSrc, el.src]).filter(Boolean)"
                    )
                    captured.extend(sources)
                except Exception:
                    pass
                try:
                    for el in page.locator('meta[property="og:video"],meta[property="og:video:url"]').all():
                        v = el.get_attribute("content")
                        if v:
                            captured.append(v)
                except Exception:
                    pass
                return list(dict.fromkeys(
                    urljoin(page_url, url) for url in captured
                    if url and not url.startswith(("blob:", "data:"))
                ))
            finally:
                browser.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(collect).result(timeout=90)


def _ensure_browser():
    _install_if_needed("playwright", "playwright")
    # Browser download is deferred until both generic and HTML extraction fail.
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True, text=True, timeout=240,
    )
    if result.returncode:
        raise RuntimeError("تعذر تجهيز متصفح استخراج الفيديو.")


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

    if not candidates and browser_fallback and _awesome(url):
        try:
            print("🌐 صفحة Awesome Screenshot ديناميكية؛ تجهيز متصفح استخراج اختياري...")
            _ensure_browser()
            candidates = _browser_media_candidates(url)
        except Exception as exc:
            errors.append(type(exc).__name__)
            print("⚠️ تعذر استخراج الفيديو باستخدام المتصفح.")

    for candidate in candidates:
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
            "تعذر العثور على ملف فيديو قابل للتحميل من رابط المشاركة. "
            "لو أنت صاحب الفيديو، افتحه في Awesome Screenshot واستخدم Download "
            "ونسخ رابط الملف المباشر إن أمكن. بعض الملفات لا تسمح بتنزيلها حسب الحساب."
        )
    raise RuntimeError("لم نتمكن من تنزيل فيديو من الرابط؛ جرّب رابط الملف المباشر أو موقعًا مدعومًا من yt-dlp.")


def run(urls_text, destination="Google Drive", subfolder="",
        quality="الأصلية", browser_fallback=True, update_ytdlp=False):
    """Entry point from the independent Colab Forms cell."""
    urls = _normal_urls(urls_text)
    target = _get_destination(destination, subfolder)
    print(f"📁 مكان الحفظ: {target}")
    print("🎞️ الجودة: الأصلية حيثما توفرت؛ لن نعيد ترميز الفيديو عمدًا.")
    print(f"🔗 عدد الروابط: {len(urls)}")
    success = []
    failed = 0
    for idx, url in enumerate(urls, 1):
        parsed = urlparse(url)
        print(f"\n━━━━━━━━━━ فيديو {idx}/{len(urls)} ━━━━━━━━━━")
        print(f"🌐 الموقع: {parsed.hostname or 'unknown'}")
        stage = STAGING_ROOT / hashlib.sha256(url.encode("utf-8")).hexdigest()[:18]
        stage.mkdir(parents=True, exist_ok=True)
        try:
            files = _download_url(url, stage, quality, browser_fallback, update_ytdlp)
            if not files:
                raise RuntimeError("لم ينتج أي ملف.")
            copied = _save_files(files, target)
            for file in copied:
                print(f"✅ اتحفظ: {file.name} ({file.stat().st_size / 1048576:.1f} MiB)")
                success.append(file)
            shutil.rmtree(stage, ignore_errors=True)
        except Exception as exc:
            failed += 1
            print(f"❌ لم يكتمل الفيديو {idx}: {str(exc)[:450]}")
            print("💾 أي ملفات جزئية باقية في Colab للتكملة داخل نفس الـRuntime.")
    print(f"\n✅ انتهى: {len(success)} ملف • فشل {failed} رابط")
    print(f"📁 الملفات المكتملة: {target}")
    return success
