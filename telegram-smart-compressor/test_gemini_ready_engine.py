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
