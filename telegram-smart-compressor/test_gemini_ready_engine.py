from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import gemini_ready_engine as gr


@pytest.fixture(scope="session")
def sample_audio(tmp_path_factory):
    root = tmp_path_factory.mktemp("samples")
    path = root / "Long Lecture.ogg"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi",
        "-i", "sine=frequency=405:sample_rate=24000:duration=180",
        "-f", "lavfi", "-i", "anoisesrc=color=pink:sample_rate=24000:amplitude=0.02:duration=180",
        "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first",
        "-c:a", "libopus", "-b:a", "24k", "-ac", "1", str(path),
    ], check=True)
    assert 400_000 < path.stat().st_size < 1_000_000
    return path


def test_part_count_and_names():
    assert gr._required_part_count(8_000_000, 9_000_000) == 1
    assert gr._required_part_count(9_000_000, 9_000_000) == 2
    assert gr._required_part_count(13_000_000, 9_000_000) == 2
    assert gr._required_part_count(20_000_000, 9_000_000) == 3
    assert gr._output_names("Ortho 1", "opus", 1) == ["Ortho 1.ogg"]
    assert gr._output_names("Ortho 1", "opus", 3) == ["Ortho 1-pt01.ogg", "Ortho 1-pt02.ogg", "Ortho 1-pt03.ogg"]


@pytest.mark.parametrize("max_bytes", [50_000, 80_000, 140_000])
def test_real_ogg_split_balanced(sample_audio, tmp_path, max_bytes):
    parts = gr.split_audio(sample_audio, tmp_path, max_bytes)
    assert len(parts) > 1
    assert all(0 < p.stat().st_size < max_bytes for p in parts)
    assert max(p.stat().st_size for p in parts) / min(p.stat().st_size for p in parts) < 1.12
    assert abs(sum(gr._duration(p) for p in parts) - gr._duration(sample_audio)) < 0.5
    assert all(gr._duration(p) > 0 for p in parts)


def test_short_remains_unchanged(sample_audio, tmp_path):
    assert gr.split_audio(sample_audio, tmp_path, 9_000_000) == [sample_audio]
    assert not tmp_path.joinpath("segment_0000.ogg").exists()


def test_duration_cap(sample_audio, tmp_path):
    parts = gr.split_audio(sample_audio, tmp_path, 9_000_000, max_minutes=1)
    assert len(parts) >= 4
    assert all(gr._duration(p) < 60.1 for p in parts)
    assert abs(sum(gr._duration(p) for p in parts) - gr._duration(sample_audio)) < 0.5


@pytest.mark.parametrize("codec,suffix,params", [
    ("libmp3lame", ".mp3", ["-b:a", "64k"]),
    ("aac", ".m4a", ["-b:a", "48k"]),
])
def test_other_audio_formats(sample_audio, tmp_path, codec, suffix, params):
    encoded = tmp_path / ("input" + suffix)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(sample_audio),
                    "-c:a", codec, *params, str(encoded)], check=True)
    parts = gr.split_audio(encoded, tmp_path / "parts", 140_000)
    assert len(parts) > 1
    assert all(p.suffix == suffix and 0 < p.stat().st_size < 140_000 for p in parts)
    assert abs(sum(gr._duration(p) for p in parts) - gr._duration(encoded)) < 1.0


def test_links_and_media_filters():
    assert gr._urls("https://a.io/f.mp3; https://a.io/f.mp3\nhttps://b.io/x") == [
        "https://a.io/f.mp3", "https://b.io/x"]
    with pytest.raises(ValueError):
        gr._urls("ftp://server/file.mp3")
    assert gr._is_media("lecture.ogg", "application/octet-stream")
    assert gr._is_media("lecture", "audio/ogg")
    assert not gr._is_media("paper.pdf", "application/pdf")
    assert not gr._is_media("document", "application/vnd.google-apps.document", True)


class FakeRequest:
    def __init__(self, factory):
        self.factory = factory

    def execute(self, **kwargs):
        return self.factory()


class FakeDriveFiles:
    def __init__(self, service):
        self.s = service

    def get(self, fileId, **kwargs):
        return FakeRequest(lambda: self.s.remote[fileId].copy())


class FakeService:
    def __init__(self):
        self.remote = {}

    def files(self):
        return FakeDriveFiles(self)


class FakeTransfer:
    def __init__(self, root):
        self.BASE_DIR = root / "Telegram_Extreme_Compressor"
        self.existing = {}
        self.folders = {}
        self.uploads = 0
        self.downloads = 0

    def ensure_child_folder(self, service, root, name, cache):
        key = (root, name)
        if key not in self.folders:
            self.folders[key] = "folder" + str(len(self.folders) + 1)
        return self.folders[key]

    def find_child_folder(self, service, root, name):
        return self.folders.get((root, name), "")

    def download_drive_file(self, service, meta, dest):
        self.downloads += 1
        shutil.copyfile(meta["local_fixture"], dest)

    def existing_drive_copy(self, service, folder_id, key, size):
        saved = self.existing.get((folder_id, key))
        return saved if saved and saved["size"] == size else None

    def upload_drive_file(self, service, path, folder_id, name, key):
        self.uploads += 1
        md5 = hashlib.md5(path.read_bytes()).hexdigest()
        remote_id = "remote" + str(self.uploads)
        result = {"id": remote_id, "name": name, "size": path.stat().st_size,
                  "md5Checksum": md5, "parents": [folder_id], "trashed": False}
        service.remote[remote_id] = result
        self.existing[(folder_id, key)] = result
        return result


class FakeEngine:
    @staticmethod
    def ffprobe(path):
        duration = gr._duration(path)
        return SimpleNamespace(has_audio=True, duration=duration)

    @staticmethod
    def encode_audio(src, dst, info, settings):
        suffix = gr.OUTPUT_SUFFIXES[settings["audio_codec"]]
        codec = {"opus": "libopus", "mp3": "libmp3lame", "aac": "aac"}[settings["audio_codec"]]
        assert dst.suffix == suffix
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src),
                        "-c:a", codec, "-b:a", str(settings["audio_kbps"]) + "k", str(dst)], check=True)


def test_upload_verification_resume_and_manifest_safety(sample_audio, tmp_path, monkeypatch):
    monkeypatch.setattr(gr, "WORK_DIR", tmp_path / "scratch")
    service = FakeService()
    transfer = FakeTransfer(tmp_path)
    state_file = tmp_path / "persistence" / "state.json"
    state = gr._read_state(state_file)
    meta = {"local_fixture": sample_audio, "name": "Ortho 1.ogg", "id": "file123", "size": str(sample_audio.stat().st_size)}
    job = {"kind": "drive", "key": "drive:123", "name": "Ortho 1.ogg", "size": sample_audio.stat().st_size, "meta": meta}
    settings = {"audio_codec": "opus", "audio_kbps": 24}
    options = {"settings": settings, "setting_id": "settings123", "max_bytes": 145_000,
               "max_minutes": 0, "max_files": 10, "force": False, "delete_tg": False, "batch": "Ortho Tests"}

    result = asyncio.run(gr._process_many([job], transfer, FakeEngine, None, service,
                                           state, state_file, options))
    assert result["ok"] == 1 and result["failed"] == 0
    assert len(state["items"]) == 1
    entry = next(iter(state["items"].values()))
    assert entry["done"]
    assert len(entry["parts"]) >= 2
    assert all(x["size"] < 145_000 for x in entry["parts"])
    assert all("-pt" in x["name"] for x in entry["parts"])
    prior_uploads, prior_downloads = transfer.uploads, transfer.downloads
    again = asyncio.run(gr._process_many([job], transfer, FakeEngine, None, service,
                                          state, state_file, options))
    assert again["skipped"] == 1 and transfer.uploads == prior_uploads and transfer.downloads == prior_downloads
    entry["parts"][0]["md5"] = "deadbeef"
    assert not gr._record_verified(service, entry)


class FakeTGClient:
    def __init__(self):
        self.deleted = []

    async def delete_messages(self, channel, ids):
        self.deleted.extend(ids)


@pytest.mark.asyncio
async def test_never_delete_before_remote_verification():
    service = FakeService()
    client = FakeTGClient()
    channel = SimpleNamespace(creator=True)
    message = SimpleNamespace(id=44)
    record = {"folder_id": "folder123", "parts": [{"id": "missing", "size": 25, "md5": "xx"}]}
    assert not await gr._maybe_delete(client, channel, message, service, record)
    assert client.deleted == []


def test_settings_follow_original_studio_defaults():
    class Original:
        STUDIO_FACTORY_SETTINGS = {"source": "auto", "audio_preset": "speech", "audio_codec": "opus",
                                   "audio_kbps": 24, "audio_channels": "mono", "audio_sample_rate": "24000",
                                   "audio_noise": "off", "skip_if_no_saving": True}
        STUDIO_AUDIO_PRESETS = {
            "speech": {"audio_codec": "opus", "audio_kbps": 24, "audio_channels": "mono",
                       "audio_sample_rate": "24000", "audio_noise": "off"},
            "clear": {"audio_codec": "opus", "audio_kbps": 32, "audio_channels": "mono",
                      "audio_sample_rate": "48000", "audio_noise": "off"},
        }
        @staticmethod
        def normalize_studio_settings(config):
            return config
    normal = gr._settings(Original, "speech", False, "mp3", 128, "stereo", "48000", "light")
    assert normal["audio_codec"] == "opus" and normal["audio_kbps"] == 24
    assert normal["audio_sample_rate"] == "24000"
    assert normal["skip_if_no_saving"] is False
    custom = gr._settings(Original, "speech", True, "mp3", 64, "mono", "24000", "off")
    assert custom["audio_codec"] == "mp3" and custom["audio_kbps"] == 64


def test_bad_types_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        gr._required_part_count(1_000_000, 5_000)
    with pytest.raises(ValueError):
        gr.split_audio(tmp_path / "missing.mp3", tmp_path / "out", 9_000_000)


def test_telegram_classifies_voice_and_unknown_extension():
    voice_file = SimpleNamespace(name="Lecture 123", size=1234567, mime_type="application/octet-stream")
    msg = SimpleNamespace(id=9, file=voice_file, audio=True, voice=False)
    items = gr._telegram_jobs([msg], channel_id=99)
    assert len(items) == 1
    assert items[0]["key"] == "telegram:99:9:1234567"
    assert items[0]["name"].startswith("Lecture 123")



def _smart_options(settings, max_bytes, mode="smart", max_minutes=0, advanced=False):
    settings = {
        "audio_preset": "speech", "audio_codec": "opus", "audio_kbps": 24,
        "audio_channels": "mono", "audio_sample_rate": "24000",
        "audio_noise": "off", **settings,
    }
    return {
        "settings": settings, "setting_id": gr._settings_signature(
            settings, max_bytes / 1_000_000, max_minutes, mode, advanced),
        "max_bytes": max_bytes, "max_minutes": max_minutes, "max_files": 10,
        "force": False, "delete_tg": False, "batch": "Ortho Tests",
        "preparation_mode": mode, "advanced_audio": advanced,
    }


def _local_job(path: Path, prefix="drive:fixture"):
    return {"kind": "drive", "key": prefix, "name": "Ortho Lesson" + path.suffix,
            "size": path.stat().st_size,
            "meta": {"id": prefix, "name": path.name,
                     "size": str(path.stat().st_size), "local_fixture": path}}


def test_smart_preserves_small_ogg_and_remote_md5(sample_audio, tmp_path, monkeypatch):
    monkeypatch.setattr(gr, "WORK_DIR", tmp_path / "work")
    transfer, service = FakeTransfer(tmp_path), FakeService()
    state_path = tmp_path / "state.json"
    state = gr._read_state(state_path)

    def should_not_encode(*_a, **_kw):
        raise AssertionError("Smart path should not invoke lossy encoder")

    monkeypatch.setattr(FakeEngine, "encode_audio", staticmethod(should_not_encode))
    job = _local_job(sample_audio)
    settings = {"audio_codec": "opus", "audio_kbps": 24}
    result = asyncio.run(gr._process_many(
        [job], transfer, FakeEngine, None, service, state, state_path,
        _smart_options(settings, 9_000_000)))
    assert result["ok"] == 1 and result["failed"] == 0
    entry = next(iter(state["items"].values()))
    assert entry["preparation"] == "kept"
    assert entry["parts"][0]["name"] == "Ortho Lesson.ogg"
    assert entry["parts"][0]["md5"] == hashlib.md5(sample_audio.read_bytes()).hexdigest()
    assert gr._record_verified(service, entry)
    assert transfer.uploads == 1 and transfer.downloads == 1


def test_smart_splits_large_mp3_without_reencoding(sample_audio, tmp_path, monkeypatch):
    encoded = tmp_path / "original.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(sample_audio),
                    "-c:a", "libmp3lame", "-b:a", "64k", str(encoded)], check=True)
    monkeypatch.setattr(gr, "WORK_DIR", tmp_path / "work")
    transfer, service = FakeTransfer(tmp_path), FakeService()
    state_path = tmp_path / "state.json"
    state = gr._read_state(state_path)
    def should_not_encode(*_a, **_kw):
        raise AssertionError("Split should be stream-copy, not lossy encode")
    monkeypatch.setattr(FakeEngine, "encode_audio", staticmethod(should_not_encode))
    result = asyncio.run(gr._process_many(
        [_local_job(encoded)], transfer, FakeEngine, None, service, state, state_path,
        _smart_options({"audio_codec": "opus", "audio_kbps": 24}, 240_000)))
    assert result["ok"] == 1 and result["failed"] == 0
    entry = next(iter(state["items"].values()))
    assert entry["preparation"] == "kept"
    assert 2 <= len(entry["parts"]) <= gr.SMART_MAX_COPY_PARTS
    assert all(p["name"].endswith(".mp3") and p["size"] < 240_000
               for p in entry["parts"])
    assert gr._record_verified(service, entry)


def test_fast_audio_extraction_from_mp4_without_lossy_encode(tmp_path):
    src = tmp_path / "lecture.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=8:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=620:sample_rate=24000:duration=3",
                    "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-b:a", "64k",
                    str(src)], check=True, timeout=30)
    prepared, reason = gr._fast_prepare_audio(
        src, tmp_path, 9_000_000, 0, "smart", False, 24)
    assert prepared is not None and prepared.suffix == ".m4a"
    info = gr._media_probe(prepared)
    assert info["valid"] and not info["video"] and info["codec"] == "aac"
    assert "بدون إعادة ترميز" in reason
    assert abs(info["duration"] - gr._duration(src)) < 1.5


def test_smart_oversized_wav_falls_back_to_encoder(sample_audio, tmp_path):
    wav = tmp_path / "lecture.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(sample_audio),
                    "-t", "10", "-c:a", "pcm_s16le", str(wav)], check=True)
    chosen, reason = gr._fast_prepare_audio(
        wav, tmp_path, 100_000, 0, "smart", False, 24)
    assert chosen is None
    assert "الصيغة" in reason
    chosen_small, _ = gr._fast_prepare_audio(
        wav, tmp_path, 9_000_000, 0, "smart", False, 24)
    assert chosen_small == wav


def test_advanced_audio_and_always_mode_override_smart(sample_audio, tmp_path):
    ready, _ = gr._fast_prepare_audio(
        sample_audio, tmp_path, 9_000_000, 0, "smart", False, 24)
    assert ready == sample_audio
    manual, reason = gr._fast_prepare_audio(
        sample_audio, tmp_path, 9_000_000, 0, "smart", True, 24)
    assert manual is None and "Advanced" in reason
    legacy, reason = gr._fast_prepare_audio(
        sample_audio, tmp_path, 9_000_000, 0, "always", False, 24)
    assert legacy is None and "Always" in reason


def test_large_high_bitrate_chooses_smart_encode_but_preserve_copies(
        sample_audio, tmp_path):
    mp3 = tmp_path / "long.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(sample_audio),
                    "-c:a", "libmp3lame", "-b:a", "128k", str(mp3)], check=True)
    max_bytes = 150_000
    smart, msg = gr._fast_prepare_audio(
        mp3, tmp_path, max_bytes, 0, "smart", False, 24)
    original, _ = gr._fast_prepare_audio(
        mp3, tmp_path, max_bytes, 0, "preserve", False, 24)
    assert smart is None and "أجزاء" in msg
    assert original == mp3


def test_multi_audio_stream_prevents_unsafe_direct_copy(tmp_path):
    src = tmp_path / "twoaudio.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=500:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=700:duration=2",
                    "-map", "0:a", "-map", "1:a", "-c:a", "aac", str(src)],
                   check=True, timeout=30)
    selected, _ = gr._fast_prepare_audio(
        src, tmp_path, 9_000_000, 0, "smart", False, 24)
    assert selected is None


def test_mode_receipts_are_isolated_and_old_telegram_not_reprocessed():
    settings = {"audio_preset": "speech", "audio_codec": "opus", "audio_kbps": 24,
                "audio_channels": "mono", "audio_sample_rate": "24000", "audio_noise": "off"}
    signatures = {gr._settings_signature(settings, 9, 0, mode)
                  for mode in gr.SMART_MODES}
    assert len(signatures) == 3
    job = {"key": "telegram:12:5:10000"}
    state = {"items": {"prior": {"source_key": job["key"], "done": True,
                                "settings": "old-sig"}}}
    assert gr._done_in_any_mode(state, job)
    assert not gr._done(state, job, "new-smart-settings")


def test_preview_skips_web_and_ffmpeg_for_internet(tmp_path, monkeypatch):
    import contextlib
    import sys
    import types

    class PresetEngine:
        STUDIO_FACTORY_SETTINGS = {
            "source": "auto", "audio_preset": "speech", "audio_codec": "opus",
            "audio_kbps": 24, "audio_channels": "mono",
            "audio_sample_rate": "24000", "audio_noise": "off",
            "skip_if_no_saving": True,
        }
        STUDIO_AUDIO_PRESETS = {
            "speech": {"audio_codec": "opus", "audio_kbps": 24,
                       "audio_channels": "mono", "audio_sample_rate": "24000",
                       "audio_noise": "off"},
        }

        @staticmethod
        def normalize_studio_settings(config):
            return config

    class PreviewTransfer:
        BASE_DIR = tmp_path / "transfer"

        @staticmethod
        def connect_drive():
            return FakeService()

        @staticmethod
        def single_run():
            return contextlib.nullcontext()

    loaded = []
    def fake_component(commit, name):
        loaded.append(name)
        if name == "transfer_engine.py":
            return PreviewTransfer
        if name == "engine.py":
            return PresetEngine
        raise AssertionError("Preview unexpectedly loaded: " + name)

    monkeypatch.setattr(gr, "_component", fake_component)
    monkeypatch.setitem(sys.modules, "googleapiclient",
                        types.ModuleType("googleapiclient"))
    result = gr.run(source="internet", links="https://example.org/lecture.mp3",
                    action="preview", repo_commit="a" * 40)
    assert result["ok"] == 0 and result["failed"] == 0
    assert loaded == ["transfer_engine.py", "engine.py"]



def test_manifest_creation_and_update(tmp_path, monkeypatch):
    import sys
    import types

    monkeypatch.setattr(gr, "WORK_DIR", tmp_path / "work")
    library = types.ModuleType("googleapiclient")
    library.__path__ = []
    http = types.ModuleType("googleapiclient.http")

    class MediaFileUpload:
        def __init__(self, filename, **kwargs):
            self.filename = filename

    http.MediaFileUpload = MediaFileUpload
    monkeypatch.setitem(sys.modules, "googleapiclient", library)
    monkeypatch.setitem(sys.modules, "googleapiclient.http", http)

    class Files:
        def __init__(self):
            self.stored = {}
            self.actions = []

        def list(self, **kwargs):
            return FakeRequest(lambda: {"files": [{"id": "manifest_id"}] if self.stored else []})

        def create(self, body, media_body, **kwargs):
            return FakeRequest(lambda: self._write("create", media_body))

        def update(self, fileId, media_body, **kwargs):
            assert fileId == "manifest_id"
            return FakeRequest(lambda: self._write("update", media_body))

        def _write(self, action, upload):
            data = Path(upload.filename).read_bytes()
            self.stored["manifest_id"] = data
            self.actions.append(action)
            return {"id": "manifest_id", "size": len(data),
                    "md5Checksum": hashlib.md5(data).hexdigest()}

    files = Files()
    service = SimpleNamespace(files=lambda: files)
    state = {"items": {"entry1": {"done": True, "folder_id": "folderABC", "name": "Lecture.ogg",
                                  "completed_at": "2026-01-01", "parts": [
                                      {"name": "Lecture-pt01.ogg", "size": 18000, "duration": 10.1}]}}}
    gr._publish_manifest(service, state, "folderABC")
    assert files.actions == ["create"]
    obj = json.loads(files.stored["manifest_id"].decode())
    assert obj["files"][0]["outputs"][0]["name"] == "Lecture-pt01.ogg"
    gr._publish_manifest(service, state, "folderABC")
    assert files.actions == ["create", "update"]
