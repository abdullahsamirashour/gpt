# -*- coding: utf-8 -*-
"""Media Lite Studio offline settings, codec command, and UX smoke tests."""
import importlib.util
import asyncio
import json
import os
import sys
import shutil
import subprocess
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

os.environ["TSC_TEST_MODE"] = "1"
ENGINE = Path(__file__).with_name("engine.py")
spec = importlib.util.spec_from_file_location("tsc_studio_engine_tests", ENGINE)
engine = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = engine
spec.loader.exec_module(engine)


class StudioPreferencesTests(unittest.TestCase):
    def tearDown(self):
        engine.ACTIVE_STUDIO_SETTINGS = None
        engine.reset_stop()

    def test_factory_values_are_validated_and_legacy_mode_is_unchanged(self):
        d = engine.normalize_studio_settings({})
        self.assertEqual(d["audio_codec"], "opus")
        self.assertEqual(d["audio_kbps"], 24)
        self.assertEqual(d["video_codec"], "h264")
        self.assertEqual(d["video_crf"], 28)
        self.assertIsNone(engine.ACTIVE_STUDIO_SETTINGS)
        self.assertEqual(engine.resolve_profile(SimpleNamespace(
            file=SimpleNamespace(mime_type="video/mp4"), video=True)), "VIDEO_BALANCED")

    def test_validation_rejects_injected_codec_and_invalid_quality(self):
        bads = [
            {"audio_codec": "opus;rm -rf /"},
            {"video_codec": "shell"},
            {"audio_kbps": True},
            {"audio_kbps": 500},
            {"video_crf": 0},
            {"video_rate_mode": "target", "video_codec": "h265"},
            {"audio_codec": "mp3", "audio_kbps": 8},
        ]
        for value in bads:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    engine.normalize_studio_settings(value)

    def test_drive_autosave_and_pin_are_independent_and_secrets_free(self):
        old_base = engine.BASE_DIR
        with tempfile.TemporaryDirectory() as td:
            engine.BASE_DIR = Path(td)
            try:
                factory = engine.normalize_studio_settings({})
                first = {**factory, "audio_codec": "aac", "audio_kbps": 64}
                record = engine.save_studio_preferences(first, save_default=True)
                self.assertEqual(record["default"]["audio_codec"], "aac")
                self.assertEqual(record["last_used"]["audio_kbps"], 64)
                followup = {**factory, "audio_codec": "mp3", "audio_kbps": 96}
                engine.save_studio_preferences(followup)
                loaded = engine.load_studio_preferences()
                self.assertEqual(loaded["last_used"]["audio_codec"], "mp3")
                self.assertEqual(loaded["default"]["audio_codec"], "aac")
                self.assertEqual(loaded["default"]["audio_kbps"], 64)
                path = engine.studio_settings_path()
                self.assertTrue(path.exists())
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("api_hash", text)
                self.assertNotIn("session", text)
                self.assertFalse(path.with_name(path.name + ".tmp").exists())
            finally:
                engine.BASE_DIR = old_base

    def test_corrupt_drive_preferences_do_not_break_ui(self):
        old_base = engine.BASE_DIR
        with tempfile.TemporaryDirectory() as td:
            engine.BASE_DIR = Path(td)
            try:
                path = engine.studio_settings_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{"version":1,"default":{"video_codec":"malicious"},"last_used":{"audio_kbps":999999}}', encoding="utf-8")
                loaded = engine.load_studio_preferences()
                self.assertEqual(loaded["default"], engine.STUDIO_FACTORY_SETTINGS)
                self.assertEqual(loaded["last_used"], engine.STUDIO_FACTORY_SETTINGS)
            finally:
                engine.BASE_DIR = old_base

    def test_studio_auto_detects_video_and_audio_without_affecting_legacy_reruns(self):
        settings = engine.apply_studio_settings(engine.STUDIO_FACTORY_SETTINGS)
        vid = SimpleNamespace(file=SimpleNamespace(mime_type="video/mp4"), video=True, audio=None, voice=None)
        aud = SimpleNamespace(file=SimpleNamespace(mime_type="audio/mpeg"), video=None, audio=True, voice=None)
        self.assertEqual(engine.resolve_profile(vid), "VIDEO_CUSTOM")
        self.assertEqual(engine.resolve_profile(aud), "AUDIO_CUSTOM")
        self.assertEqual(engine.resolve_profile(vid, "VIDEO_FAST"), "VIDEO_FAST")
        self.assertEqual(engine.parse_rerun_command("1"), {"profile": None, "target_mb": None})
        self.assertEqual(engine.parse_rerun_command("3")["profile"], "AUDIO_TINY")

    def test_audio_advanced_ffmpeg_settings_are_used_without_changing_old_tiny_mode(self):
        info = engine.MediaInfo(duration=10, has_audio=True)
        found = []
        with patch.object(engine, "_select_audio_stream", return_value={"index": 1, "channels": 1, "peak": -12}), \
             patch.object(engine, "_audio_output_peak", return_value=-12), \
             patch.object(engine, "run_ffmpeg", side_effect=lambda cmd, *_: found.append(cmd)):
            engine.encode_audio(Path("/tmp/input.ogg"), Path("/tmp/output.ogg"), info)
            settings = engine.normalize_studio_settings({
                "audio_codec": "mp3", "audio_kbps": 64,
                "audio_channels": "stereo", "audio_noise": "light",
            })
            engine.encode_audio(Path("/tmp/input.ogg"), Path("/tmp/output.mp3"),
                                info, settings=settings)
        old, modern = found
        self.assertIn("8k", old)
        self.assertIn("12000", old)
        self.assertIn("libopus", old)
        self.assertIn("libmp3lame", modern)
        self.assertIn("64k", modern)
        self.assertIn("afftdn=nr=10", " ".join(modern))
        self.assertIn("2", modern)

    def test_video_advanced_quality_and_target_build_safe_ffmpeg_commands(self):
        info = engine.MediaInfo(duration=60, width=1920, height=1080,
                                fps=30, has_video=True, has_audio=True)
        recorded = []
        with tempfile.TemporaryDirectory() as td:
            with patch.object(engine, "TMP_DIR", Path(td)), \
                 patch.object(engine, "run_ffmpeg", side_effect=lambda cmd, *_: recorded.append(cmd)):
                quality = engine.normalize_studio_settings({
                    "video_codec": "h265", "video_crf": 25,
                    "video_resolution": "1080", "video_fps": "24",
                    "video_rate_mode": "quality",
                })
                result = engine.encode_video(Path("/tmp/input.mp4"), Path(td) / "quality.mp4",
                                             info, "VIDEO_CUSTOM", None, False, settings=quality)
                self.assertEqual(result, "STUDIO_CPU")
                self.assertIn("libx265", recorded[0])
                self.assertIn("25", recorded[0])
                self.assertIn("hvc1", recorded[0])
                recorded.clear()
                direct_bitrate = engine.normalize_studio_settings({
                    "video_rate_mode": "bitrate",
                    "video_bitrate_kbps": 1800,
                    "video_fps": "24",
                })
                engine.encode_video(Path("/tmp/input.mp4"), Path(td) / "bitrate.mp4",
                                    info, "VIDEO_CUSTOM", None, False,
                                    settings=direct_bitrate)
                self.assertIn("1800k", recorded[0])
                self.assertIn("-maxrate", recorded[0])
                recorded.clear()
                target = engine.normalize_studio_settings({
                    "video_codec": "h264", "video_rate_mode": "target",
                    "video_target_mb": 25, "video_fps": "15",
                })
                result = engine.encode_video(Path("/tmp/input.mp4"), Path(td) / "target.mp4",
                                             info, "VIDEO_CUSTOM", None, False, settings=target)
                self.assertEqual(result, "STUDIO_TARGET")
                self.assertEqual(len(recorded), 2)
                self.assertIn("libx264", recorded[0])
                self.assertIn("1", recorded[0])
                self.assertIn("2", recorded[1])

    def test_audio_presets_favour_speech_and_keep_legacy_extreme_mode(self):
        self.assertEqual(engine.STUDIO_AUDIO_PRESETS["tiny"]["audio_kbps"], 8)
        self.assertEqual(engine.STUDIO_AUDIO_PRESETS["speech"]["audio_kbps"], 24)
        self.assertEqual(engine.STUDIO_VIDEO_PRESETS["presentation"]["video_resolution"], "1080")




    def test_download_is_fixed_six_in_studio_but_legacy_launchers_keep_auto(self):
        size = int(87.9 * 1024**2)
        self.assertEqual(engine.transfer_connection_count(size), 18)
        self.assertEqual(engine.normalize_studio_settings({})["download_connections"], "6")
        for old_value in ("auto", "8", "18", "4", "6"):
            engine.apply_studio_settings({"download_connections": old_value})
            self.assertEqual(engine.ACTIVE_STUDIO_SETTINGS["download_connections"], "6")
            self.assertEqual(engine.transfer_connection_count(size), 6)
        with self.assertRaises(ValueError):
            engine.normalize_studio_settings({"download_connections": "999"})

    def test_single_progress_html_widget_is_updated_in_place(self):
        import ipywidgets as widgets
        with patch("IPython.display.display") as display:
            progress = engine.LiveProgress("تنزيل", "⬇️")
            progress.update(10, 100, force=True)
            self.assertEqual(display.call_count, 1)
            widget = display.call_args.args[0]
            self.assertIsInstance(widget, widgets.HTML)
            self.assertIn("10.0%", widget.value)
            progress.update(45, 100, force=True)
            progress.update(75, 100, force=True)
            self.assertIn("75.0%", widget.value)
            self.assertEqual(display.call_count, 1)
            progress.finish(100)
            self.assertIn("✅", widget.value)
            self.assertEqual(display.call_count, 1)
            self.assertIs(progress.widget, widget)


    def test_interactive_ui_can_pin_run_and_restore_settings(self):
        """Widget buttons actually persist last-used and pinned-default separately."""
        import ipywidgets as widgets
        ui_path = Path(__file__).with_name("media_studio_ui.py")
        ui_spec = importlib.util.spec_from_file_location("tsc_ui_tests", ui_path)
        studio = importlib.util.module_from_spec(ui_spec)
        ui_spec.loader.exec_module(studio)

        original_base = engine.BASE_DIR
        with tempfile.TemporaryDirectory() as td:
            engine.BASE_DIR = Path(td)
            try:
                with patch.object(engine, "mount_drive", return_value=None), \
                     patch.object(engine, "run_async", new_callable=AsyncMock, return_value={"stopped": False, "error": None}) as execute, \
                     patch("IPython.display.display"):
                    dashboard = studio.show_studio(engine)
                    buttons = [
                        item for item in dashboard.children
                        if isinstance(item, widgets.Button)
                    ]
                    run_button = next(item for item in buttons if "تشغيل الضغط" in item.description)
                    pin_button = next(item for item in buttons if "Save as Default" in item.description)
                    restore_button = next(item for item in buttons if "استرجاع" in item.description)
                    accordion = next(
                        item for item in dashboard.children
                        if isinstance(item, widgets.Accordion)
                    )
                    self.assertEqual(accordion.selected_index, None)
                    self.assertEqual(len(accordion.children), 3)
                    audio_controls = accordion.children[0].children
                    codec = next(item for item in audio_controls
                                 if getattr(item, "description", "") == "الصيغة")
                    bitrate = next(item for item in audio_controls
                                   if getattr(item, "description", "") == "جودة الصوت")

                    # Responsive UX regression: hiding video leaves audio visible.
                    mode = next(item for item in dashboard.children
                                if isinstance(item, widgets.ToggleButtons))
                    quick_audio, quick_video = dashboard.children[4], dashboard.children[5]
                    mode.value = "audio"
                    self.assertNotEqual(quick_audio.layout.display, "none")
                    self.assertEqual(quick_video.layout.display, "none")
                    mode.value = "video"
                    self.assertEqual(quick_audio.layout.display, "none")
                    self.assertNotEqual(quick_video.layout.display, "none")
                    mode.value = "auto"
                    self.assertEqual(len(accordion.children), 3)

                    video_widgets = accordion.children[1].children
                    rate_mode = next(item for item in video_widgets
                                     if isinstance(item, widgets.ToggleButtons))
                    video_bitrate = next(item for item in video_widgets
                                         if getattr(item, "description", "") == "Video kbps")
                    video_crf = next(item for item in video_widgets
                                     if getattr(item, "description", "") == "جودة CRF")
                    rate_mode.value = "bitrate"
                    self.assertNotEqual(video_bitrate.layout.display, "none")
                    self.assertEqual(video_crf.layout.display, "none")
                    self.assertNotEqual(codec.layout.display, "none")
                    rate_mode.value = "quality"

                    connections = next(item for item in accordion.children[2].children
                                       if getattr(item, "description", "") == "تنزيل Telegram")
                    self.assertTrue(connections.disabled)
                    self.assertEqual(connections.value, "6")
                    codec.value = "mp3"
                    bitrate.value = 64
                    pin_button.click()
                    self.assertEqual(engine.load_studio_preferences()["default"]["download_connections"], "6")
                    self.assertEqual(engine.load_studio_preferences()["default"]["audio_kbps"], 64)

                    codec.value = "opus"
                    bitrate.value = 24
                    async def press_run():
                        run_button.click()
                        await asyncio.sleep(0.05)
                    asyncio.run(press_run())
                    execute.assert_awaited_once()
                    stored = engine.load_studio_preferences()
                    self.assertEqual(stored["last_used"]["audio_codec"], "opus")
                    self.assertEqual(stored["default"]["audio_codec"], "mp3")
                    restore_button.click()
                    self.assertEqual(codec.value, "mp3")
                    self.assertEqual(bitrate.value, 64)
                    self.assertEqual(connections.value, "6")
            finally:
                engine.BASE_DIR = original_base



    def test_stop_button_is_clickable_during_an_active_async_run(self):
        import ipywidgets as widgets
        path = Path(__file__).with_name("media_studio_ui.py")
        spec = importlib.util.spec_from_file_location("tsc_ui_stop_test", path)
        ui = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ui)

        async def fake_long_run():
            while not engine.stop_requested():
                await asyncio.sleep(0.01)
            return {"stopped": True, "error": None}

        original_base = engine.BASE_DIR
        with tempfile.TemporaryDirectory() as td:
            engine.BASE_DIR = Path(td)
            try:
                with patch.object(engine, "mount_drive", return_value=None), \
                     patch.object(engine, "run_async", side_effect=fake_long_run), \
                     patch("IPython.display.display"):
                    dashboard = ui.show_studio(engine)
                    buttons = [x for x in dashboard.children if isinstance(x, widgets.Button)]
                    run = next(x for x in buttons if "تشغيل الضغط" in x.description)
                    stop = next(x for x in buttons if "إيقاف التشغيل" in x.description)
                    pin = next(x for x in buttons if "Save as Default" in x.description)
                    self.assertTrue(stop.disabled)

                    async def action():
                        run.click()
                        self.assertTrue(run.disabled)
                        self.assertFalse(stop.disabled)
                        self.assertTrue(pin.disabled)
                        await asyncio.sleep(0.03)
                        stop.click()
                        self.assertTrue(engine.stop_requested())
                        self.assertTrue(stop.disabled)
                        await asyncio.sleep(0.05)

                    asyncio.run(action())
                    self.assertFalse(run.disabled)
                    self.assertTrue(stop.disabled)
                    self.assertFalse(pin.disabled)
                    self.assertIn("تم إيقاف التشغيل", dashboard.children[1].value)
            finally:
                engine.BASE_DIR = original_base

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg unavailable")
    def test_stop_terminates_running_encoder_without_damaging_source(self):
        engine.reset_stop()
        errors = []
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "source.txt"
            src.write_text("keep-original-untouched")
            target = Path(td) / "encoding.ogg"
            cmd = [
                "ffmpeg", "-re", "-f", "lavfi",
                "-i", "sine=frequency=600:sample_rate=48000",
                "-t", "12", "-c:a", "libopus", str(target),
            ]

            def worker():
                try:
                    engine.run_ffmpeg(cmd, 12, "test-stop")
                except BaseException as exc:
                    errors.append(exc)

            worker_thread = threading.Thread(target=worker)
            worker_thread.start()
            started = time.monotonic()
            while time.monotonic() - started < 4:
                with engine._SUBPROCESS_LOCK:
                    active = bool(engine._ACTIVE_SUBPROCESSES)
                if active:
                    break
                time.sleep(0.05)
            self.assertTrue(active, "FFmpeg did not start in time")
            engine.request_stop()
            worker_thread.join(5)
            self.assertFalse(worker_thread.is_alive(), "FFmpeg ignored Stop")
            self.assertTrue(errors)
            self.assertIsInstance(errors[0], engine.StopRequested)
            self.assertEqual(src.read_text(), "keep-original-untouched")
        engine.reset_stop()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg unavailable")
    def test_real_ffmpeg_media_studio_encode_smoke(self):
        """Encode short synthetic media without cloud or Telegram credentials."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15",
                "-f", "lavfi", "-i", "sine=frequency=750:sample_rate=44100",
                "-t", "1.3", "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac", "-b:a", "96k", str(source),
            ], check=True, timeout=30)
            info = engine.ffprobe(source)
            self.assertTrue(info.has_video and info.has_audio)

            for codec, suffix, kbps in (
                ("opus", ".ogg", 24), ("aac", ".m4a", 64), ("mp3", ".mp3", 64)
            ):
                settings = engine.normalize_studio_settings({
                    "audio_codec": codec,
                    "audio_kbps": kbps,
                    "audio_sample_rate": "24000",
                })
                dst = root / ("audio-" + codec + suffix)
                engine.encode_audio(source, dst, info, settings=settings)
                self.assertTrue(dst.exists() and dst.stat().st_size > 300)
                self.assertTrue(engine.ffprobe(dst).has_audio)

            options = engine.normalize_studio_settings({
                "video_rate_mode": "bitrate",
                "video_bitrate_kbps": 700,
                "video_resolution": "480",
                "video_fps": "15",
            })
            output = root / "advanced-video.mp4"
            with patch.object(engine, "TMP_DIR", root):
                result = engine.encode_video(
                    source, output, info, "VIDEO_CUSTOM", None, False, settings=options
                )
            self.assertEqual(result, "STUDIO_CPU")
            self.assertTrue(output.exists() and output.stat().st_size > 500)
            self.assertTrue(engine.ffprobe(output).has_video)


if __name__ == "__main__":
    unittest.main()
