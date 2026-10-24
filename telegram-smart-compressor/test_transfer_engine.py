# -*- coding: utf-8 -*-
"""Offline regression tests: no Google/Telegram credentials required."""
import importlib.util
import asyncio
import hashlib
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
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


def mocked_google_modules(authenticator, mounter, get_credentials, make_service):
    """Provide Colab and Google API test doubles without real login or network."""
    google = types.ModuleType("google")
    google.__path__ = []
    colab = types.ModuleType("google.colab")
    colab.__path__ = []
    colab.auth = SimpleNamespace(authenticate_user=authenticator)
    colab.drive = SimpleNamespace(mount=mounter)
    googleauth = types.ModuleType("google.auth")
    googleauth.default = get_credentials
    api = types.ModuleType("googleapiclient")
    api.__path__ = []
    discovery = types.ModuleType("googleapiclient.discovery")
    discovery.build = make_service
    return {
        "google": google, "google.colab": colab, "google.auth": googleauth,
        "googleapiclient": api, "googleapiclient.discovery": discovery,
    }


class TransferEngineTests(unittest.TestCase):
    def test_drive_auth_reuses_mounted_drive_and_existing_api_login(self):
        with tempfile.TemporaryDirectory() as td:
            api = MagicMock()
            api.about.return_value.get.return_value.execute.return_value = {
                "user": {"emailAddress": "student@gmail.com"}
            }
            mount = MagicMock(side_effect=AssertionError("Mount should not be requested again"))
            auth = MagicMock(side_effect=AssertionError("Auth should not be requested again"))
            default = MagicMock(return_value=(object(), None))
            build = MagicMock(return_value=api)
            modules = mocked_google_modules(auth, mount, default, build)
            with patch.dict(sys.modules, modules), \
                 patch.object(transfer.os.path, "ismount", return_value=True), \
                 patch.object(transfer, "BASE_DIR", Path(td) / "base"), \
                 patch.object(transfer, "APP_DIR", Path(td) / "base" / "transfer"), \
                 patch.object(transfer, "TEMP_ROOT", Path(td) / "tmp"):
                result = transfer.connect_drive()
            self.assertIs(result, api)
            mount.assert_not_called()
            auth.assert_not_called()
            default.assert_called_once_with(scopes=[transfer.DRIVE_SCOPE])
            self.assertTrue((Path(td) / "base" / "transfer").exists())

    def test_drive_auth_requests_api_permission_only_if_no_cached_login(self):
        with tempfile.TemporaryDirectory() as td:
            api = MagicMock()
            api.about.return_value.get.return_value.execute.return_value = {
                "user": {"emailAddress": "student@gmail.com"}
            }
            default = MagicMock(side_effect=[
                RuntimeError("No reusable API credential"),
                (object(), None),
            ])
            mount = MagicMock()
            auth = MagicMock()
            modules = mocked_google_modules(auth, mount, default, MagicMock(return_value=api))
            with patch.dict(sys.modules, modules), \
                 patch.object(transfer.os.path, "ismount", side_effect=[False, True]), \
                 patch.object(transfer, "BASE_DIR", Path(td) / "base"), \
                 patch.object(transfer, "APP_DIR", Path(td) / "base" / "transfer"), \
                 patch.object(transfer, "TEMP_ROOT", Path(td) / "tmp"):
                result = transfer.connect_drive()
            self.assertIs(result, api)
            mount.assert_called_once_with("/content/drive", force_remount=False)
            auth.assert_called_once_with()
            self.assertEqual(default.call_count, 2)

    def test_drive_mount_failure_is_identified_before_telegram_login(self):
        with tempfile.TemporaryDirectory() as td:
            mount = MagicMock(side_effect=RuntimeError("credential propagation was unsuccessful"))
            auth = MagicMock(side_effect=AssertionError("Must not authorize API after mount failed"))
            modules = mocked_google_modules(auth, mount, MagicMock(), MagicMock())
            with patch.dict(sys.modules, modules), \
                 patch.object(transfer.os.path, "ismount", return_value=False), \
                 patch.object(transfer, "BASE_DIR", Path(td) / "base"):
                with self.assertRaisesRegex(RuntimeError, "فشل توصيل Google Drive"):
                    transfer.connect_drive()
            auth.assert_not_called()

    def test_service_account_credentials_not_accepted_as_personal_drive(self):
        service = MagicMock()
        service.about.return_value.get.return_value.execute.return_value = {
            "user": {"emailAddress": "service@project.iam.gserviceaccount.com"}
        }
        with self.assertRaisesRegex(RuntimeError, "Google Drive API"):
            transfer._drive_service_for_signed_in_user(
                lambda scopes: (object(), None), lambda *a, **k: service
            )

    def test_qr_login_can_refresh_without_sms(self):
        images = []
        links = []
        fake_qrcode = types.ModuleType("qrcode")
        fake_qrcode.make = lambda url: images.append(url) or SimpleNamespace(get_image=lambda: "QR IMAGE")
        fake_ipython = types.ModuleType("IPython")
        fake_ipython.__path__ = []
        fake_display = types.ModuleType("IPython.display")
        fake_display.HTML = lambda value: value
        fake_display.display = lambda value: links.append(value)

        class FakeQr:
            url = "tg://login?token=private_login_token"
            recreations = 0
            attempts = 0
            async def wait(self):
                self.attempts += 1
                if self.attempts == 1:
                    raise asyncio.TimeoutError
                client.authorized = True
            async def recreate(self):
                self.recreations += 1
                self.url = "tg://login?token=renewed_token"

        class FakeTelegram:
            def __init__(self):
                self.authorized = False
                self.connected = False
                self.qr = FakeQr()
            async def connect(self):
                self.connected = True
            async def is_user_authorized(self):
                return self.authorized
            async def qr_login(self):
                return self.qr
            async def start(self, **_kwargs):
                raise AssertionError("QR flow must not request an SMS / app code")

        client = FakeTelegram()
        with patch.dict(sys.modules, {
            "qrcode": fake_qrcode,
            "IPython": fake_ipython,
            "IPython.display": fake_display,
        }):
            asyncio.run(transfer.authorize_telegram(client, "+201234567890", "qr"))
        self.assertTrue(client.connected)
        self.assertTrue(client.authorized)
        self.assertEqual(client.qr.recreations, 1)
        self.assertEqual(images, [
            "tg://login?token=private_login_token",
            "tg://login?token=renewed_token",
        ])
        self.assertEqual(len(links), 4)  # image and deep link for each QR

    def test_valid_session_skips_telegram_login_method(self):
        class AuthorizedClient:
            async def connect(self):
                pass
            async def is_user_authorized(self):
                return True
            async def start(self, **_kwargs):
                raise AssertionError("Must not re-login with existing auth")
            async def qr_login(self):
                raise AssertionError("Must not create QR with existing auth")

        for method in ("qr", "code"):
            asyncio.run(transfer.authorize_telegram(AuthorizedClient(), "+201234567890", method))
        with self.assertRaises(ValueError):
            asyncio.run(transfer.authorize_telegram(AuthorizedClient(), "+201234567890", "bad"))

    def test_drive_folder_link_in_either_form_field(self):
        fid = "1abcdefghijklmnopqrstuvwxyzABCDE"
        url = f"https://drive.google.com/drive/u/0/folders/{fid}"
        self.assertEqual(
            transfer.normalize_transfer_inputs("drive_to_telegram", url, ""), (url, "")
        )
        self.assertEqual(
            transfer.normalize_transfer_inputs("drive_to_telegram", "", url), (url, "")
        )
        self.assertEqual(
            transfer.normalize_transfer_inputs("telegram_to_drive", "", url), ("", url)
        )
        with self.assertRaisesRegex(ValueError, "رابط"):
            transfer.normalize_transfer_inputs("drive_to_telegram", "", "")
        with self.assertRaises(ValueError):
            transfer.normalize_transfer_inputs(
                "drive_to_telegram", url,
                "https://drive.google.com/drive/folders/2abcdefghijklmnopqrstuvwxyzABCDE",
            )
        with self.assertRaises(ValueError):
            transfer.normalize_transfer_inputs("drive_to_telegram", "", "not a Drive URL")

    def test_channel_state_resets_when_channel_is_recreated(self):
        old_path = transfer.STATE_PATH
        try:
            with tempfile.TemporaryDirectory() as td:
                transfer.STATE_PATH = Path(td) / "transfer_state.json"
                state = transfer.new_state()
                state["drive_to_telegram"] = {"somefile": {"done": True}}
                state["drive_to_telegram_channel_id"] = 111
                state["reupload_batches"] = {"old-channel-batch": {"completed": ["somefile"]}}
                transfer.bind_transfer_channel(state, 222)
                self.assertEqual(state["drive_to_telegram"], {})
                self.assertNotIn("reupload_batches", state)
                self.assertEqual(state["drive_to_telegram_channel_id"], 222)
                self.assertEqual(transfer.load_json(transfer.STATE_PATH, {})["drive_to_telegram"], {})
        finally:
            transfer.STATE_PATH = old_path

    def test_transfer_channel_is_reused_or_created_privately(self):
        def channel(channel_id):
            return SimpleNamespace(
                id=channel_id, title=transfer.CHANNEL_TITLE,
                creator=True, broadcast=True, username=None, admin_rights=None,
            )

        class Client:
            def __init__(self, dialogs, created_channel, cached=None):
                self.dialogs = dialogs
                self.created_channel = created_channel
                self.cached = cached
                self.requests = []

            async def get_entity(self, _peer):
                if self.cached is None:
                    raise ValueError("Not cached")
                return self.cached

            async def iter_dialogs(self):
                for entry in self.dialogs:
                    yield SimpleNamespace(name=transfer.CHANNEL_TITLE, entity=entry)

            async def __call__(self, request):
                self.requests.append(request)
                return SimpleNamespace(chats=[self.created_channel])

        old_path = transfer.TRANSFER_CONFIG
        try:
            with tempfile.TemporaryDirectory() as td:
                transfer.TRANSFER_CONFIG = Path(td) / "channel.json"
                existing = channel(314)
                c = Client([existing], channel(123))
                saved = {}
                entity = asyncio.run(transfer.ensure_channel(c, saved))
                self.assertEqual(entity.id, 314)
                self.assertEqual(saved["channel_id"], 314)
                self.assertFalse(c.requests)

                c = Client([], channel(123), cached=existing)
                entity = asyncio.run(transfer.ensure_channel(c, {"channel_id": 314}))
                self.assertEqual(entity.id, 314)
                self.assertFalse(c.requests)

                c = Client([], channel(888))
                saved = {}
                entity = asyncio.run(transfer.ensure_channel(c, saved))
                self.assertEqual(entity.id, 888)
                self.assertEqual(saved["channel_id"], 888)
                self.assertEqual(len(c.requests), 1)
                self.assertTrue(c.requests[0].broadcast)
                self.assertFalse(c.requests[0].megagroup)

                c = Client([channel(11), channel(12)], channel(999))
                with self.assertRaisesRegex(RuntimeError, "أكتر من قناة"):
                    asyncio.run(transfer.ensure_channel(c, {}))
                self.assertFalse(c.requests)
        finally:
            transfer.TRANSFER_CONFIG = old_path

    def test_limited_batches_advance_and_zero_transfers_every_remaining_file(self):
        root_id = "1abcdefghijklmnopqrstuvwxyzABCDE"
        files = [
            {"id": f"f{i}", "name": f"file{i}.txt", "mimeType": "text/plain",
             "size": "1", "modifiedTime": "2026-10-01T00:00:00Z"}
            for i in range(1, 4)
        ]
        service = FakeDrive(FakeFiles(
            metas={root_id: {"id": root_id, "mimeType": "application/vnd.google-apps.folder", "name": "root"}},
            children={root_id: [{"files": files}]},
        ))

        class Telegram:
            def __init__(self):
                self.sent = []
            async def iter_messages(self, _channel, limit=None):
                if False:
                    yield None
            async def upload_file(self, file, *, file_size, file_name, part_size_kb, progress_callback):
                self.assert_part(part_size_kb)
                self.sent.append((file_name, file_size))
                return SimpleNamespace(name=file_name, size=file_size)
            def assert_part(self, part_kb):
                if part_kb != 512:
                    raise AssertionError("Expected 512 KiB native part size")
            async def send_file(self, _channel, file, **_kw):
                return SimpleNamespace(id=len(self.sent), file=SimpleNamespace(size=file.size))

        tg = Telegram()
        state = transfer.new_state()
        old_state, old_root = transfer.STATE_PATH, transfer.TEMP_ROOT
        try:
            with tempfile.TemporaryDirectory() as td:
                transfer.STATE_PATH = Path(td) / "state.json"
                transfer.TEMP_ROOT = Path(td)
                def download_file(_drive, meta, filename):
                    value = meta["id"].encode()[:1]
                    Path(filename).write_bytes(value)
                    return len(value), hashlib.sha256(value).hexdigest()
                with patch.object(transfer, "download_drive_file", side_effect=download_file):
                    def run(limit):
                        return asyncio.run(transfer.transfer_drive_to_telegram(
                            tg, object(), service, state, root_id, True, limit, False
                        ))
                    self.assertEqual(run(1)["ok"], 1)
                    self.assertEqual(run(1)["ok"], 1)  # Must continue to the next file.
                    last = run(0)  # Unlimited; only one pending file remains.
                    self.assertEqual(last["ok"], 1)
                    self.assertEqual(last["skipped"], 2)
                    self.assertEqual(run(0)["skipped"], 3)
                    self.assertEqual(len(tg.sent), 3)
        finally:
            transfer.STATE_PATH, transfer.TEMP_ROOT = old_state, old_root

    def test_drive_preview_never_logs_in_to_telegram_or_creates_channel(self):
        root_id = "1abcdefghijklmnopqrstuvwxyzABCDE"
        service = FakeDrive(FakeFiles(metas={
            root_id: {"id": root_id, "name": "notes.pdf", "mimeType": "application/pdf", "size": "100"}
        }))
        with patch.object(transfer, "load_account_config", side_effect=AssertionError("Unexpected Telegram login")), \
             patch.object(transfer, "ensure_channel", side_effect=AssertionError("Unexpected channel creation")):
            result = asyncio.run(transfer._run_transfer(
                service, "drive_to_telegram", root_id, "", 0, True, True
            ))
            self.assertEqual(result["ok"], 0)

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
                self.uploads = []
            async def iter_messages(self, _channel, limit=0):
                if False:
                    yield None
            async def get_messages(self, _channel, limit=0):
                return []
            async def upload_file(self, file, *, file_size, file_name, part_size_kb, progress_callback):
                data = Path(file).read_bytes() if isinstance(file, str) else file.read()
                if len(data) != file_size:
                    raise AssertionError("A segment leaked bytes into a neighboring part")
                self.uploads.append((data, file_name, part_size_kb))
                return SimpleNamespace(name=file_name, size=len(data))
            async def send_file(self, _channel, file, **kwargs):
                if isinstance(file, str):
                    size = Path(file).stat().st_size  # manifest uses regular send_file
                else:
                    size = file.size  # Telegram accepts the native InputFile handle
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
                    self.assertEqual(b"".join(p[0] for p in telegram.uploads), payload)
                    self.assertEqual([p[2] for p in telegram.uploads], [512, 512, 512])
                    self.assertEqual([p[1] for p in telegram.uploads], [
                        "archive.bin.part001", "archive.bin.part002", "archive.bin.part003"
                    ])
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

    def test_native_512kb_uploader_passes_a_preuploaded_file_handle(self):
        class Client:
            def __init__(self):
                self.args = None
                self.uploaded = None
            async def upload_file(self, file, **kwargs):
                self.args = (file, kwargs)
                self.uploaded = SimpleNamespace(name=kwargs["file_name"], size=kwargs["file_size"])
                return self.uploaded
            async def send_file(self, channel, file, **kwargs):
                self.sent = (channel, file, kwargs)
                return SimpleNamespace(id=123, file=SimpleNamespace(size=file.size))

        client = Client()
        callback = lambda current, total: None
        result = asyncio.run(transfer.send_transfer_document(
            client, "target_channel", "some/file.mp4", 49000, "file.mp4",
            "MLTX:0123456789abcdefab:1/1", callback,
        ))
        self.assertEqual(result.id, 123)
        self.assertIs(client.sent[1], client.uploaded)
        self.assertEqual(client.args[1]["part_size_kb"], 512)
        self.assertEqual(client.args[1]["file_size"], 49000)
        self.assertIs(client.args[1]["progress_callback"], callback)
        self.assertTrue(client.sent[2]["force_document"])
        self.assertIsNone(client.sent[2]["mime_type"])
        self.assertEqual(client.sent[2]["attributes"][0].file_name, "file.mp4")

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

    def test_extensionless_recordings_use_actual_format_and_original_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            media = Path(td) / "Lec1"
            media.write_bytes(b"OggS" + b"\0" * 60)
            original = {"name": "Lec1", "mimeType": "application/octet-stream"}
            self.assertEqual(transfer.telegram_transfer_filename(original, media), "Lec1.ogg")
            self.assertEqual(original["name"], "Lec1")
            self.assertEqual(transfer.telegram_transfer_filename(
                {"name": "Lec1.mp4", "mimeType": "audio/ogg"}, media
            ), "Lec1.mp4")  # Do not overwrite any existing extension.
            self.assertEqual(transfer.telegram_transfer_filename(
                {"name": "Lec1", "mimeType": "application/vnd.google-apps.document"}, media
            ), "Lec1.pdf")
            media.write_bytes(b"%PDF-1.7\nfile")
            self.assertEqual(transfer.telegram_transfer_filename(
                {"name": "Slides", "mimeType": "application/octet-stream"}, media
            ), "Slides.pdf")
            media.write_bytes(b"unknown bytes and not a recognized container")
            with patch.object(transfer.subprocess, "run", side_effect=FileNotFoundError):
                self.assertEqual(transfer.telegram_transfer_filename(
                    {"name": "Unrecognized", "mimeType": "application/octet-stream"}, media
                ), "Unrecognized")

    def test_m4a_and_mp4_extension_distinguished_using_media_streams(self):
        with tempfile.TemporaryDirectory() as td:
            local = Path(td) / "Lec2"
            local.write_bytes(b"\0\0\0\x18ftypisom" + b"\0" * 24)
            for stream, expected in [("audio", "Lec2.m4a"), ("video", "Lec2.mp4")]:
                probe = SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({
                        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
                        "streams": [{"codec_type": stream}],
                    }),
                )
                with patch.object(transfer.subprocess, "run", return_value=probe) as run:
                    self.assertEqual(transfer.telegram_transfer_filename(
                        {"name": "Lec2", "mimeType": "application/octet-stream"}, local
                    ), expected)
                    self.assertEqual(run.call_args.kwargs["timeout"], 15)

    def test_webm_and_mkv_distinction_is_not_guessed_from_ffprobe_format_name(self):
        with tempfile.TemporaryDirectory() as td:
            media = Path(td) / "Lec3"
            probe = SimpleNamespace(returncode=0, stdout=json.dumps({
                "format": {"format_name": "matroska,webm"}, "streams": [{"codec_type": "video"}]
            }))
            with patch.object(transfer.subprocess, "run", return_value=probe):
                media.write_bytes(b"\x1a\x45\xdf\xa3" + b"matroska" + b"\0" * 16)
                self.assertEqual(transfer.inferred_extension(media, "application/octet-stream"), ".mkv")
                media.write_bytes(b"\x1a\x45\xdf\xa3" + b"webm" + b"\0" * 16)
                self.assertEqual(transfer.inferred_extension(media, "application/octet-stream"), ".webm")

    def test_extensionless_drive_file_is_named_correctly_in_telegram_upload(self):
        class FakeTelegram:
            def __init__(self):
                self.upload_names = []
            async def iter_messages(self, channel, limit):
                if False:
                    yield None
            async def upload_file(self, path, *, file_size, file_name, part_size_kb, progress_callback):
                self.upload_names.append(file_name)
                self.uploaded_bytes = Path(path).read_bytes()
                return SimpleNamespace(name=file_name, size=file_size)
            async def send_file(self, channel, handle, **kwargs):
                self.docname = kwargs["attributes"][0].file_name
                return SimpleNamespace(id=31, file=SimpleNamespace(size=handle.size))

        root = "123456789abcdefghijklmnopqrstuvwxyz"
        original = {
            "id": root, "name": "Lec1", "mimeType": "application/octet-stream",
            "size": "32", "modifiedTime": "2026-10-24T00:00:00Z",
        }
        tg = FakeTelegram()
        state = transfer.new_state()
        old_root, old_state = transfer.TEMP_ROOT, transfer.STATE_PATH
        try:
            with tempfile.TemporaryDirectory() as td:
                transfer.TEMP_ROOT = Path(td)
                transfer.STATE_PATH = Path(td) / "state.json"
                sample = b"OggS" + b"\0" * 28
                def download(_service, _meta, path):
                    path.write_bytes(sample)
                    return len(sample), hashlib.sha256(sample).hexdigest()
                with patch.object(transfer, "download_drive_file", side_effect=download):
                    first = asyncio.run(transfer.transfer_drive_to_telegram(
                        tg, object(), FakeDrive(FakeFiles(metas={root: original})),
                        state, root, True, 0, False
                    ))
                    self.assertEqual(first["ok"], 1)
                    self.assertEqual(tg.upload_names, ["Lec1.ogg"])
                    self.assertEqual(tg.docname, "Lec1.ogg")
                    self.assertEqual(tg.uploaded_bytes, sample)
                    self.assertEqual(
                        state["drive_to_telegram"][transfer.transfer_token(original)]["name"],
                        "Lec1.ogg",
                    )
                    second = asyncio.run(transfer.transfer_drive_to_telegram(
                        tg, object(), FakeDrive(FakeFiles(metas={root: original})),
                        state, root, True, 0, False
                    ))
                    self.assertEqual(second["skipped"], 1)
                    self.assertEqual(len(tg.upload_names), 1)
        finally:
            transfer.TEMP_ROOT, transfer.STATE_PATH = old_root, old_state

    def test_reupload_number_parser_is_precise_and_rejects_unsafe_ranges(self):
        self.assertEqual(transfer.parse_file_numbers("1-7", 8), list(range(1, 8)))
        self.assertEqual(transfer.parse_file_numbers("1,3,5-8", 8), [1, 3, 5, 6, 7, 8])
        self.assertEqual(transfer.parse_file_numbers("7، 1، 1", 8), [1, 7])
        for invalid in ["", "0", "9", "8-3", "1,,3", "all", "1;8", "-1", "1-300"]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                transfer.parse_file_numbers(invalid, 8)

    def test_selective_reupload_seven_of_eight_keeps_other_file_and_old_receipts(self):
        folder = "abcdefghijklmnopqrstuvwxyzABCDE"
        metadata = [
            {"id": f"source_file_{i:02d}", "name": f"Lec{i}",
             "mimeType": "application/octet-stream", "size": "5",
             "modifiedTime": "2026-10-01T00:00:00Z"}
            for i in range(1, 9)
        ]
        drive = FakeDrive(FakeFiles(
            metas={folder: {"id": folder, "name": "course",
                            "mimeType": "application/vnd.google-apps.folder"}},
            children={folder: [{"files": metadata}]},
        ))
        state = transfer.new_state()

        class Telegram:
            def __init__(self):
                self.old = []
                self.new = []
                self.payloads = []
                for idx, meta in enumerate(metadata, 1):
                    token = transfer.transfer_token(meta)
                    self.old.append(SimpleNamespace(
                        id=idx, message=f"📁 Lec{idx}\nMLTX:{token}:1/1",
                        file=SimpleNamespace(size=5)
                    ))
                    state["drive_to_telegram"][token] = {
                        "done": True, "parts": {"1": idx}, "name": f"Lec{idx}", "size": 5,
                    }
            async def iter_messages(self, _channel, limit=None):
                for msg in reversed(self.new + self.old):
                    yield msg
            async def upload_file(self, path, *, file_size, file_name, part_size_kb, progress_callback):
                payload = Path(path).read_bytes()
                self.payloads.append((payload, file_name))
                self.assert_size = (len(payload), file_size, part_size_kb)
                return SimpleNamespace(size=file_size, name=file_name)
            async def send_file(self, channel, file, **kwargs):
                msg = SimpleNamespace(
                    id=100 + len(self.new),
                    message=kwargs["caption"],
                    file=SimpleNamespace(size=file.size),
                )
                self.new.append(msg)
                return msg

        telegram = Telegram()
        old_root, old_state = transfer.TEMP_ROOT, transfer.STATE_PATH
        try:
            with tempfile.TemporaryDirectory() as td:
                transfer.TEMP_ROOT = Path(td)
                transfer.STATE_PATH = Path(td) / "state.json"
                def download_file(_service, meta, local):
                    payload = b"OggS" + bytes([int(meta["name"][3:])])
                    local.write_bytes(payload)
                    return len(payload), hashlib.sha256(payload).hexdigest()
                with patch.object(transfer, "download_drive_file", side_effect=download_file):
                    preview = asyncio.run(transfer.transfer_drive_to_telegram(
                        None, None, drive, state, folder, True, 0, True
                    ))
                    self.assertEqual(preview["skipped"], 8)
                    forced = asyncio.run(transfer.transfer_drive_to_telegram(
                        telegram, object(), drive, state, folder, True, 0, False,
                        reupload_selected=True, selected_numbers="1-7",
                    ))
                    self.assertEqual(forced["ok"], 7)
                    self.assertEqual(forced["failed"], 0)
                    self.assertEqual(len(telegram.new), 7)
                    self.assertEqual([name for _, name in telegram.payloads], [
                        f"Lec{i}.ogg" for i in range(1, 8)
                    ])
                    self.assertEqual(telegram.assert_size, (5, 5, 512))
                    self.assertEqual(len(telegram.old), 8)
                    for i, meta in enumerate(metadata, 1):
                        token = transfer.transfer_token(meta)
                        record = state["drive_to_telegram"][token]
                        if i < 8:
                            self.assertEqual(record["name"], f"Lec{i}.ogg")
                            self.assertEqual(record["history"][-1]["parts"]["1"], i)
                            self.assertGreater(record["parts"]["1"], 100 - 1)
                            self.assertNotIn("reupload", record)
                            self.assertNotIn(f"MLTX:{token}:1/1", telegram.new[i - 1].message)
                        else:
                            self.assertEqual(record["name"], "Lec8")
                            self.assertEqual(record["parts"]["1"], 8)
                            self.assertNotIn("history", record)
                    normal = asyncio.run(transfer.transfer_drive_to_telegram(
                        telegram, object(), drive, state, folder, True, 0, False
                    ))
                    self.assertEqual(normal["skipped"], 8)
                    self.assertEqual(len(telegram.new), 7)
        finally:
            transfer.TEMP_ROOT, transfer.STATE_PATH = old_root, old_state

    def test_interrupted_reupload_resumes_same_selection_without_duplicates(self):
        folder = "abcdefghijklmnopqrstuvwxyzABCDE"
        metadata = [
            {"id": f"file_{i}", "name": f"Lesson{i}.pdf", "mimeType": "application/pdf",
             "size": "5", "modifiedTime": "2026-10-01"}
            for i in range(1, 4)
        ]
        drive = FakeDrive(FakeFiles(
            metas={folder: {"id": folder, "name": "x",
                            "mimeType": "application/vnd.google-apps.folder"}},
            children={folder: [{"files": metadata}]},
        ))
        state = transfer.new_state()
        for idx, meta in enumerate(metadata, 1):
            state["drive_to_telegram"][transfer.transfer_token(meta)] = {
                "done": True, "parts": {"1": idx}, "name": meta["name"],
            }

        class Telegram:
            def __init__(self):
                self.new = []
                self.fail_once = True
            async def iter_messages(self, _channel, limit=None):
                for msg in reversed(self.new):
                    yield msg
            async def upload_file(self, path, *, file_size, file_name, part_size_kb, progress_callback):
                return SimpleNamespace(name=file_name, size=file_size)
            async def send_file(self, _channel, handle, **kwargs):
                if self.fail_once and "Lesson2" in handle.name:
                    self.fail_once = False
                    raise RuntimeError("transient Telegram failure")
                msg = SimpleNamespace(
                    id=100 + len(self.new), message=kwargs["caption"],
                    file=SimpleNamespace(size=handle.size)
                )
                self.new.append(msg)
                return msg

        client = Telegram()
        old_root, old_state = transfer.TEMP_ROOT, transfer.STATE_PATH
        try:
            with tempfile.TemporaryDirectory() as td:
                transfer.TEMP_ROOT = Path(td)
                transfer.STATE_PATH = Path(td) / "state.json"
                def download_file(_service, meta, local):
                    payload = b"%PDF-"
                    local.write_bytes(payload)
                    return len(payload), hashlib.sha256(payload).hexdigest()
                with patch.object(transfer, "download_drive_file", side_effect=download_file):
                    first = asyncio.run(transfer.transfer_drive_to_telegram(
                        client, object(), drive, state, folder, True, 0, False,
                        reupload_selected=True, selected_numbers="1-3",
                    ))
                    self.assertEqual((first["ok"], first["failed"]), (2, 1))
                    self.assertEqual(len(client.new), 2)
                    failed_record = state["drive_to_telegram"][transfer.transfer_token(metadata[1])]
                    self.assertTrue(failed_record["done"])  # Old working copy kept.
                    self.assertIn("reupload", failed_record)
                    second = asyncio.run(transfer.transfer_drive_to_telegram(
                        client, object(), drive, state, folder, True, 0, False,
                        reupload_selected=True, selected_numbers="1-3",
                    ))
                    self.assertEqual((second["ok"], second["skipped"], second["failed"]), (1, 2, 0))
                    self.assertEqual(len(client.new), 3)
                    self.assertTrue(next(iter(state["reupload_batches"].values()))["done"])
                    self.assertNotIn(
                        "reupload", state["drive_to_telegram"][transfer.transfer_token(metadata[1])]
                    )
                    self.assertEqual(
                        len({m.message.split("MLTX:")[-1] for m in client.new}), 3
                    )
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
