# -*- coding: utf-8 -*-
"""Credential-free tests for the optional Telegram -> Drive parallel downloader."""
import asyncio
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('transfer_engine', Path(__file__).with_name('transfer_engine.py'))
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)


class GetFileRequest:
    def __init__(self, location, offset, limit):
        self.location, self.offset, self.limit = location, offset, limit


class InvokeWithLayerRequest:
    def __init__(self, *args):
        self.args = args


class ExportAuthorizationRequest:
    def __init__(self, dc):
        self.dc = dc


class ImportAuthorizationRequest:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeSender:
    registry = []

    def __init__(self, auth_key, loggers):
        self.auth_key = auth_key or 'temporary-auth-key'
        self.closed = False
        self.registry.append(self)

    async def connect(self, _connection):
        pass

    async def send(self, request):
        assert isinstance(request, InvokeWithLayerRequest)

    async def disconnect(self):
        self.closed = True


class FakeClient:
    _connection = staticmethod(lambda *args, **kwargs: None)
    _log, _proxy = None, None

    def __init__(self, payload, dc=1, failing_offset=None, short_offset=None):
        self._init_request = SimpleNamespace(query='original')
        self._borrow_sender_lock = asyncio.Lock()
        self.session = SimpleNamespace(dc_id=1, auth_key='session-auth-key')
        self.payload, self.dc = payload, dc
        self.failing_offset, self.short_offset = failing_offset, short_offset
        self.offsets, self.auth_exports = [], []

    async def _get_dc(self, dc_id):
        return SimpleNamespace(id=dc_id, ip_address='127.0.0.1', port=443)

    async def _call(self, sender, request):
        assert isinstance(request, GetFileRequest)
        self.offsets.append(request.offset)
        await asyncio.sleep(0)
        if request.offset == self.failing_offset:
            raise ConnectionError('injected chunk failure')
        data = self.payload[request.offset:request.offset + request.limit]
        if request.offset == self.short_offset:
            data = data[:-1]
        return SimpleNamespace(bytes=data)

    async def __call__(self, request):
        self.auth_exports.append(request.dc)
        return SimpleNamespace(id=4, bytes=b'opaque')


class FloodWaitError(Exception):
    pass


class NativeClient:
    def __init__(self, payload):
        self.payload = payload
        self.download_count = 0

    async def download_media(self, msg, file, progress_callback):
        self.download_count += 1
        Path(file).write_bytes(self.payload)
        progress_callback(len(self.payload), len(self.payload))
        return file


class FakeParallel:
    def __init__(self, data, failure=None):
        self.data, self.failure = data, failure
        self.calls = 0

    async def download(self, msg, path, size, progress):
        self.calls += 1
        path.write_bytes(self.data)
        if self.failure:
            raise self.failure
        progress(size, size)


class ParallelTransferTests(unittest.TestCase):
    def setUp(self):
        modules = {
            'telethon': types.ModuleType('telethon'),
            'telethon.network': types.ModuleType('telethon.network'),
            'telethon.tl': types.ModuleType('telethon.tl'),
            'telethon.tl.functions': types.ModuleType('telethon.tl.functions'),
            'telethon.tl.functions.auth': types.ModuleType('telethon.tl.functions.auth'),
            'telethon.tl.functions.upload': types.ModuleType('telethon.tl.functions.upload'),
            'telethon.tl.alltlobjects': types.ModuleType('telethon.tl.alltlobjects'),
        }
        modules['telethon'].utils = SimpleNamespace(get_input_location=lambda msg: (msg.dc, 'location'))
        modules['telethon.network'].MTProtoSender = FakeSender
        modules['telethon.tl.alltlobjects'].LAYER = 1
        modules['telethon.tl.functions'].InvokeWithLayerRequest = InvokeWithLayerRequest
        modules['telethon.tl.functions.auth'].ExportAuthorizationRequest = ExportAuthorizationRequest
        modules['telethon.tl.functions.auth'].ImportAuthorizationRequest = ImportAuthorizationRequest
        modules['telethon.tl.functions.upload'].GetFileRequest = GetFileRequest
        self.patcher = patch.dict(sys.modules, modules)
        self.patcher.start()
        FakeSender.registry.clear()

    def tearDown(self):
        self.patcher.stop()

    def test_threshold_is_five_mebibytes_not_twenty(self):
        self.assertEqual(transfer.TG_PARALLEL_MIN_BYTES, 5 * 1024 * 1024)
        self.assertEqual(transfer.TG_PARALLEL_CONNECTIONS, 6)
        self.assertEqual(transfer.TG_PARALLEL_PART_BYTES, 512 * 1024)

    def test_parallel_home_dc_reassembles_full_file(self):
        payload = (b'abc0123456' * 750_000)[:6_345_678]
        client = FakeClient(payload)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'output.bin'
            asyncio.run(transfer.TelegramParallelDownloader(client).download(
                SimpleNamespace(dc=1), path, len(payload), lambda *_: None))
            self.assertEqual(path.read_bytes(), payload)
        self.assertEqual(len(FakeSender.registry), 6)
        self.assertTrue(all(sender.closed for sender in FakeSender.registry))
        self.assertEqual(sorted(client.offsets), list(range(0, len(payload), 512 * 1024)))

    def test_foreign_dc_auth_is_cached_only_in_memory(self):
        payload = b'z' * 5_500_000
        client = FakeClient(payload, dc=2)
        downloader = transfer.TelegramParallelDownloader(client)
        with tempfile.TemporaryDirectory() as td:
            for number in range(2):
                asyncio.run(downloader.download(SimpleNamespace(dc=2),
                    Path(td) / f'output-{number}', len(payload), lambda *_: None))
        self.assertEqual(client.auth_exports, [2])
        self.assertEqual(client._init_request.query, 'original')
        self.assertTrue(all(sender.closed for sender in FakeSender.registry))

    def test_short_chunk_rejects_and_deletes_partial_file(self):
        payload = b'x' * 6_000_000
        client = FakeClient(payload, short_offset=524288)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'partial'
            with self.assertRaisesRegex(IOError, 'received'):
                asyncio.run(transfer.TelegramParallelDownloader(client).download(
                    SimpleNamespace(dc=1), path, len(payload), lambda *_: None))
            self.assertFalse(path.exists())
        self.assertTrue(all(sender.closed for sender in FakeSender.registry))

    def test_failed_chunk_cancels_and_deletes_partial_file(self):
        payload = b'x' * 6_000_000
        client = FakeClient(payload, failing_offset=524288)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'partial'
            with self.assertRaises(ConnectionError):
                asyncio.run(transfer.TelegramParallelDownloader(client).download(
                    SimpleNamespace(dc=1), path, len(payload), lambda *_: None))
            self.assertFalse(path.exists())
        self.assertTrue(all(sender.closed for sender in FakeSender.registry))

    def test_native_path_for_small_files(self):
        payload = b'x' * 4096
        client, parallel = NativeClient(payload), FakeParallel(payload)
        with tempfile.TemporaryDirectory() as td:
            asyncio.run(transfer.fetch_telegram_message(client, None, None,
                Path(td) / 'small', len(payload), parallel))
        self.assertEqual((client.download_count, parallel.calls), (1, 0))

    def test_parallel_for_exactly_five_mebibytes(self):
        payload = b'x' * (5 * 1024 * 1024)
        client, parallel = NativeClient(payload), FakeParallel(payload)
        with tempfile.TemporaryDirectory() as td:
            asyncio.run(transfer.fetch_telegram_message(client, None, None,
                Path(td) / 'large', len(payload), parallel))
        self.assertEqual((client.download_count, parallel.calls), (0, 1))

    def test_parallel_failure_discards_partial_before_native_retry(self):
        payload = b'x' * (5 * 1024 * 1024)
        client, parallel = NativeClient(payload), FakeParallel(b'bad', OSError('injected'))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'large'
            asyncio.run(transfer.fetch_telegram_message(client, None, None, path,
                                                       len(payload), parallel))
            self.assertEqual(path.read_bytes(), payload)
        self.assertEqual((client.download_count, parallel.calls), (1, 1))

    def test_flood_wait_never_triggers_immediate_native_retry(self):
        payload = b'x' * (5 * 1024 * 1024)
        client, parallel = NativeClient(payload), FakeParallel(b'partial', FloodWaitError('retry later'))
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FloodWaitError):
                asyncio.run(transfer.fetch_telegram_message(client, None, None,
                    Path(td) / 'large', len(payload), parallel))
        self.assertEqual(client.download_count, 0)


if __name__ == '__main__':
    unittest.main()
