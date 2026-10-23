# -*- coding: utf-8 -*-
"""Offline regression tests: no Google/Telegram credentials required."""
import importlib.util
import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import patch
import json
import tempfile
import unittest
from pathlib import Path

ENGINE = Path(__file__).resolve().with_name("transfer_engine.py")
spec = importlib.util.spec_from_file_location("media_lite_transfer_engine", ENGINE)
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)


class Reply:
    def __init__(self, payload):
        self.payload = payload

    def execute(self, **_kwargs):
        return self.payload


class FakeFiles:
    def __init__(self, metas=None, children=None):
        self.metas = metas or {}
        self.children = children or {}
        self.queries = []

    def get(self, fileId, **_kwargs):
        return Reply(self.metas[fileId])

    def list(self, q, **kwargs):
        self.queries.append(q)
        for fid, pages in self.children.items():
            if f"'{fid}' in parents" in q:
                page = kwargs.get("pageToken")
                return Reply(pages[int(page)] if page else pages[0])
        return Reply({"files": []})


class FakeDrive:
    def __init__(self, files):
        self.client = files

    def files(self):
        return self.client


class TransferEngineTests(unittest.TestCase):
    def test_no_overlapping_transfer_sessions(self):
        old_dir, old_lock = transfer.APP_DIR, transfer.LOCK_PATH
        try:
            with tempfile.TemporaryDirectory() as td:
                transfer.APP_DIR = Path(td)
                transfer.LOCK_PATH = transfer.APP_DIR / "run.lock"
                with transfer.single_run():
                    self.assertTrue(transfer.LOCK_PATH.exists())
                    with self.assertRaises(RuntimeError):
                        with transfer.single_run():
                            pass
                self.assertFalse(transfer.LOCK_PATH.exists())
        finally:
            transfer.APP_DIR, transfer.LOCK_PATH = old_dir, old_lock

    def test_log_export_is_opt_in(self):
        old_dir = transfer.APP_DIR
        try:
            with tempfile.TemporaryDirectory() as td:
                transfer.APP_DIR = Path(td) / "transfer"
                source = Path(td) / "run.log"
                source.write_text("one run", encoding="utf-8")
                exported = transfer.export_run_log(source)
                self.assertEqual(exported.read_text(encoding="utf-8"), "one run")
                self.assertTrue(source.exists())
        finally:
            transfer.APP_DIR = old_dir

    def test_drive_id_parsing_and_untrusted_hosts(self):
        fid = "1abcdefghijklmnopqrstuvwxyzABCDE"
        valid = [
            fid,
            f"https://drive.google.com/file/d/{fid}/view?usp=sharing",
            f"https://drive.google.com/drive/folders/{fid}",
            f"https://drive.google.com/drive/u/0/folders/{fid}",
            f"https://drive.google.com/drive/u/2/folders/{fid}",
            f"https://drive.google.com/file/u/1/d/{fid}/view",
            f"https://docs.google.com/document/u/0/d/{fid}/edit",
            f"https://drive.google.com/open?id={fid}",
            f"https://docs.google.com/document/d/{fid}/edit",
            f"https://docs.google.com/spreadsheets/d/{fid}/edit",
        ]
        for source in valid:
            self.assertEqual(transfer.parse_drive_id(source), fid)
        for invalid in [
            "", "https://evil.google.com/file/d/" + fid,
            "https://drive.google.com.evil.test/file/d/" + fid,
            "http://drive.google.com/file/d/" + fid,
            "some path on disk",
        ]:
            with self.assertRaises(ValueError):
                transfer.parse_drive_id(invalid)

    def test_fingerprint_changes_when_content_changes(self):
        original = {"id": "file01", "md5Checksum": "abc", "size": "50", "modifiedTime": "2026-01-01"}
        changed = {**original, "md5Checksum": "def"}
        self.assertEqual(transfer.transfer_token(original), transfer.transfer_token(dict(original)))
        self.assertNotEqual(transfer.transfer_token(original), transfer.transfer_token(changed))
        self.assertRegex(transfer.transfer_token(original), r"^[0-9a-f]{18}$")

    def test_atomic_state_and_independent_directions(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "nested" / "state.json"
            data = transfer.new_state()
            data["telegram_to_drive"]["-100_55"] = {"drive_id": "xyz"}
            transfer.save_json(path, data)
            self.assertEqual(transfer.load_json(path, {})["telegram_to_drive"], data["telegram_to_drive"])
            self.assertFalse((path.parent / "state.json.tmp").exists())
            self.assertEqual(transfer.state_section(data, "drive_to_telegram"), {})

    def test_segment_reader_does_not_leak_bytes_from_neighbors(self):
        content = bytes(range(256)) * 20
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "data.bin"
            path.write_bytes(content)
            pieces = []
            for start, length in [(0, 1800), (1800, 1800), (3600, len(content) - 3600)]:
                with transfer.SegmentReader(path, start, length, "part") as segment:
                    self.assertEqual(segment.read(250), content[start:start + 250])
                    self.assertEqual(segment.tell(), 250)
                    self.assertEqual(segment.seek(0), 0)
                    self.assertEqual(segment.read(), content[start:start + length])
                    self.assertEqual(segment.read(10), b"")
                    segment.seek(-20, 2)
                    self.assertEqual(segment.read(1000), content[start + length - 20:start + length])
                    segment.seek(0)
                    pieces.append(segment.read())
            self.assertEqual(b"".join(pieces), content)

    def test_recursive_listing_pagination_and_shortcuts(self):
        root = "abcdefghijklmnopqrst"
        child = "uvwxyzabcdefghijklmn"
        fs = FakeFiles(
            metas={root: {"id": root, "name": "root", "mimeType": "application/vnd.google-apps.folder"}},
            children={
                root: [
                    {"files": [
                        {"id": "doc01", "name": "Intro.pdf", "mimeType": "application/pdf", "size": "4"},
                        {"id": child, "name": "Week 1", "mimeType": "application/vnd.google-apps.folder"},
                    ], "nextPageToken": "1"},
                    {"files": [
                        {"id": "shortcut", "name": "Link", "mimeType": "application/vnd.google-apps.shortcut"},
                    ]},
                ],
                child: [{"files": [
                    {"id": "doc02", "name": "Slides.pptx", "mimeType": "application/vnd.openxmlformats-officedocument.presentationml.presentation", "size": "10"},
                ]}],
            },
        )
        drive = FakeDrive(fs)
        shallow = transfer.list_drive_sources(drive, root, recursive=False)
        deep = transfer.list_drive_sources(drive, root, recursive=True)
        self.assertEqual([x["relative_path"] for x in shallow], ["Intro.pdf"])
        self.assertEqual(
            [x["relative_path"] for x in deep], ["Intro.pdf", "Week 1/Slides.pptx"]
        )

    def test_drive_to_telegram_sends_bounded_parts_and_manifest(self):
        class FakeTelegram:
            def __init__(self):
                self.messages = []
            async def iter_messages(self, _channel, limit=0):
                if False:
                    yield None
            async def get_messages(self, _channel, limit=0):
                return []
            async def send_file(self, _channel, file, **kwargs):
                if isinstance(file, str):
                    size = Path(file).stat().st_size
                else:
                    size = len(file.read())
                result = SimpleNamespace(id=100 + len(self.messages), file=SimpleNamespace(size=size))
                self.messages.append((result, kwargs.get("caption", ""), size))
                return result

        payload = b"abcdefgh"
        source_id = "abcdefghijklmnopqrstuvwxyzABCDE"
        meta = {
            "id": source_id, "name": "archive.bin", "mimeType": "application/octet-stream",
            "size": str(len(payload)), "modifiedTime": "2026-10-01T00:00:00Z",
            "md5Checksum": hashlib.md5(payload).hexdigest(),
        }
        telegram = FakeTelegram()
        state = transfer.new_state()
        old_root, old_state, old_limit = transfer.TEMP_ROOT, transfer.STATE_PATH, transfer.MAX_TG_PART
        try:
            with tempfile.TemporaryDirectory() as td:
                transfer.TEMP_ROOT = Path(td)
                transfer.STATE_PATH = Path(td) / "state.json"
                transfer.MAX_TG_PART = 3
                def fake_download(_service, _meta, path):
                    path.write_bytes(payload)
                    return len(payload), hashlib.sha256(payload).hexdigest()
                service = FakeDrive(FakeFiles(metas={source_id: meta}))
                with patch.object(transfer, "download_drive_file", fake_download):
                    result = asyncio.run(transfer.transfer_drive_to_telegram(
                        telegram, object(), service, state, source_id, True, 20, False
                    ))
                    self.assertEqual(result["ok"], 1)
                    self.assertEqual([m[2] for m in telegram.messages[:3]], [3, 3, 2])
                    self.assertEqual(len(telegram.messages), 4)  # 3 parts and manifest
                    token = transfer.transfer_token(meta)
                    self.assertTrue(state["drive_to_telegram"][token]["done"])
                    repeat = asyncio.run(transfer.transfer_drive_to_telegram(
                        telegram, object(), service, state, source_id, True, 20, False
                    ))
                    self.assertEqual(repeat["skipped"], 1)
                    self.assertEqual(len(telegram.messages), 4)
        finally:
            transfer.TEMP_ROOT, transfer.STATE_PATH, transfer.MAX_TG_PART = old_root, old_state, old_limit

    def test_telegram_to_drive_does_not_copy_drive_origin_messages(self):
        class FakeTelegram:
            def __init__(self):
                self.messages = [
                    SimpleNamespace(
                        id=22, message="MLTX:0123456789abcdefab:1/1",
                        file=SimpleNamespace(size=4, name="ignore.mp4", ext=".mp4")
                    ),
                    SimpleNamespace(
                        id=21, message="Original upload",
                        file=SimpleNamespace(size=4, name="lecture.mp4", ext=".mp4")
                    ),
                ]
            async def iter_messages(self, _channel, limit=0):
                for msg in self.messages:
                    yield msg
            async def download_media(self, _message, file, progress_callback):
                Path(file).write_bytes(b"1234")
                progress_callback(4, 4)
                return file

        old_root, old_state = transfer.TEMP_ROOT, transfer.STATE_PATH
        try:
            with tempfile.TemporaryDirectory() as td:
                transfer.TEMP_ROOT = Path(td)
                transfer.STATE_PATH = Path(td) / "state.json"
                state = transfer.new_state()
                channel = SimpleNamespace(id=12, title="Lecture Archive")
                with patch("telethon.utils.get_peer_id", return_value=-10012), \
                    patch.object(transfer, "ensure_child_folder", return_value="dest-folder"), \
                    patch.object(transfer, "existing_drive_copy", return_value=None), \
                    patch.object(transfer, "upload_drive_file", return_value={"id": "drive-file01"}):
                    result = asyncio.run(transfer.transfer_telegram_to_drive(
                        FakeTelegram(), channel, object(), state, "dest", 20, False
                    ))
                    self.assertEqual(result["ok"], 1)
                    self.assertEqual(len(state["telegram_to_drive"]), 1)
                    self.assertIn(transfer.origin_key(-10012, 21), state["telegram_to_drive"])
                    self.assertNotIn(transfer.origin_key(-10012, 22), state["telegram_to_drive"])
        finally:
            transfer.TEMP_ROOT, transfer.STATE_PATH = old_root, old_state

    def test_file_export_support_and_provenance_tag(self):
        self.assertEqual(transfer.EXPORT_TYPES["application/vnd.google-apps.document"][1], ".pdf")
        tag = "📁 folder/notes.pdf\nMLTX:0123456789abcdefab:2/3"
        match = transfer.TAG_PATTERN.search(tag)
        self.assertEqual((match.group(1), match.group(2), match.group(3)), ("0123456789abcdefab", "2", "3"))
        self.assertEqual(transfer.clean_filename("../notes.pdf"), "notes.pdf")


if __name__ == "__main__":
    unittest.main()
