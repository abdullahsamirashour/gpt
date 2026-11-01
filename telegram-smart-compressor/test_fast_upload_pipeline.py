# -*- coding: utf-8 -*-
"""Offline tests for six sender uploads and Drive prefetch; no real credentials."""
import asyncio
import hashlib
import importlib.util
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "media_lite_parallel_upload", Path(__file__).with_name("transfer_engine.py"))
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)


class SavePart:
    def __init__(self, *values):
        self.part_index = values[1]
        self.data = values[-1]


class Handle:
    def __init__(self, fid, parts, name, checksum=None):
        self.parts, self.name, self.checksum = parts, name, checksum


class Sender:
    instances = []

    def __init__(self, auth_key, loggers):
        self.closed = False
        self.instances.append(self)

    async def connect(self, _connection):
        await asyncio.sleep(0)

    async def disconnect(self):
        self.closed = True


class Client:
    _connection = staticmethod(lambda *a, **k: None)
    _log, _proxy = None, None

    def __init__(self, fail_index=None):
        self.session = SimpleNamespace(dc_id=1, auth_key="simulated")
        self.fail_index = fail_index
        self.requests = []
        self.active = 0
        self.max_active = 0
        self.senders = set()

    async def _get_dc(self, dc_id):
        return SimpleNamespace(id=dc_id, ip_address="127.0.0.1", port=443)

    async def _call(self, sender, request):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(.001)
            if request.part_index == self.fail_index:
                raise ConnectionError("simulated Telegram failure")
            self.requests.append(request)
            self.senders.add(sender)
            return True
        finally:
            self.active -= 1


class FastUploadTests(unittest.TestCase):
    def setUp(self):
        fake = {name: types.ModuleType(name) for name in (
            "telethon", "telethon.network", "telethon.tl", "telethon.tl.functions",
            "telethon.tl.functions.upload", "telethon.tl.types")}
        fake["telethon"].helpers = SimpleNamespace(generate_random_long=lambda: 123456)
        fake["telethon.network"].MTProtoSender = Sender
        fake["telethon.tl"].functions = SimpleNamespace(upload=SimpleNamespace(
            SaveBigFilePartRequest=SavePart, SaveFilePartRequest=SavePart))
        fake["telethon.tl"].types = SimpleNamespace(InputFile=Handle, InputFileBig=Handle)
        fake["telethon.tl.types"].DocumentAttributeFilename = (
            lambda name: SimpleNamespace(file_name=name))
        self.modules = patch.dict(sys.modules, fake)
        self.modules.start()
        Sender.instances.clear()

    def tearDown(self):
        self.modules.stop()

    def test_configured_six_connections_each_direction(self):
        self.assertEqual(transfer.TG_PARALLEL_CONNECTIONS, 6)
        self.assertEqual(transfer.TG_UPLOAD_CONNECTIONS, 6)
        self.assertEqual(transfer.TG_UPLOAD_PART_BYTES, 512 * 1024)
        self.assertEqual(transfer.TG_UPLOAD_MIN_BYTES, 5 * 1024 * 1024)

    def test_six_upload_senders_and_correct_small_file_md5(self):
        content = os.urandom(6_010_007)
        client = Client()
        uploader = transfer.TelegramParallelUploader(client)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sample.bin"
            path.write_bytes(content)

            async def run():
                try:
                    return await uploader.upload_file(str(path), len(content),
                                                      "sample.bin", lambda *_: None)
                finally:
                    await uploader.close()
            handle = asyncio.run(run())
        pieces = sorted(client.requests, key=lambda r: r.part_index)
        self.assertEqual(b"".join(r.data for r in pieces), content)
        self.assertEqual(handle.checksum, hashlib.md5(content).hexdigest())
        self.assertEqual(len(client.senders), 6)
        self.assertEqual(client.max_active, 6)
        self.assertTrue(all(s.closed for s in Sender.instances))

    def test_reuses_six_senders_across_files_and_big_handle(self):
        content = b"Z" * 11_000_001
        client = Client()
        uploader = transfer.TelegramParallelUploader(client)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "big.bin"
            path.write_bytes(content)

            async def run():
                try:
                    first = await uploader.upload_file(str(path), len(content),
                                                       "big.bin", lambda *_: None)
                    second = await uploader.upload_file(str(path), len(content),
                                                        "big.bin", lambda *_: None)
                    return first, second
                finally:
                    await uploader.close()
            first, second = asyncio.run(run())
        self.assertIsNone(first.checksum)
        self.assertEqual(first.parts, second.parts)
        self.assertEqual(len(Sender.instances), 6)

    def test_segment_reader_uses_its_exact_byte_range(self):
        content = os.urandom(12_000_000)
        offset, length = 1_000_000, 6_000_011
        client = Client()
        uploader = transfer.TelegramParallelUploader(client)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "original.bin"
            path.write_bytes(content)

            async def run():
                try:
                    with transfer.SegmentReader(path, offset, length, "part001") as segment:
                        return await uploader.upload_file(segment, length,
                                                          "part001", lambda *_: None)
                finally:
                    await uploader.close()
            handle = asyncio.run(run())
        self.assertEqual(handle.checksum,
                         hashlib.md5(content[offset:offset+length]).hexdigest())
        self.assertEqual(b"".join(r.data for r in sorted(
            client.requests, key=lambda r: r.part_index)),
            content[offset:offset+length])

    def test_failed_parallel_upload_retries_native_before_sending_message(self):
        content = b"x" * (5 * 1024 * 1024)
        client = Client(fail_index=1)
        operations = []

        async def native_upload(payload, **kwargs):
            operations.append("native")
            return Handle(4, 10, "file.bin")

        async def send_file(channel, handle, **kwargs):
            operations.append("send")
            return SimpleNamespace(id=70, file=SimpleNamespace(size=len(content)))

        client.upload_file = native_upload
        client.send_file = send_file
        uploader = transfer.TelegramParallelUploader(client)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "file.bin"
            path.write_bytes(content)
            result = asyncio.run(transfer.send_transfer_document(
                client, object(), str(path), len(content), "file.bin", "caption",
                lambda *_: None, parallel_uploader=uploader))
        self.assertEqual(result.id, 70)
        self.assertEqual(operations, ["native", "send"])
        self.assertTrue(uploader.disabled)
        self.assertTrue(all(s.closed for s in Sender.instances))

    def test_next_drive_file_downloads_while_previous_uploads(self):
        payloads = {"one.bin": b"a" * 4096, "two.bin": b"b" * 8192}
        sources = [{"id": name, "name": name, "relative_path": name,
                    "size": str(len(payload)), "mimeType": "application/octet-stream",
                    "modifiedTime": "2026-10-31T00:00:00Z"}
                   for name, payload in payloads.items()]
        second_started = threading.Event()
        events = []

        def download(_service, meta, local):
            events.append("download:" + meta["name"])
            local.write_bytes(payloads[meta["name"]])
            if meta["name"] == "two.bin":
                second_started.set()
            return len(payloads[meta["name"]]), "safe-sha"

        async def cached(_client, _channel):
            return {}

        async def send(_client, _channel, file, size, name, caption, progress, **kwargs):
            events.append("upload-start:" + name)
            if name == "one.bin":
                await asyncio.wait_for(asyncio.to_thread(second_started.wait), timeout=5)
            events.append("upload-done:" + name)
            return SimpleNamespace(id=len(events), file=SimpleNamespace(size=size))

        with tempfile.TemporaryDirectory() as td:
            temp_root = Path(td)
            state = transfer.new_state()
            with patch.object(transfer, "TEMP_ROOT", temp_root), \
                 patch.object(transfer, "STATE_PATH", temp_root / "state.json"), \
                 patch.object(transfer, "list_drive_sources", return_value=sources), \
                 patch.object(transfer, "cached_part_messages", new=cached), \
                 patch.object(transfer, "download_drive_file", side_effect=download), \
                 patch.object(transfer, "telegram_transfer_filename", side_effect=lambda m, p: p.name), \
                 patch.object(transfer, "send_transfer_document", side_effect=send), \
                 patch.object(transfer, "ensure_child_folder", return_value="folder"):
                result = asyncio.run(transfer.transfer_drive_to_telegram(
                    object(), object(), object(), state, "root", True, 0, False))
            self.assertEqual((result["ok"], result["failed"]), (2, 0))
            self.assertEqual(len(state["drive_to_telegram"]), 2)
        self.assertLess(events.index("download:two.bin"),
                        events.index("upload-done:one.bin"))


if __name__ == "__main__":
    unittest.main()
