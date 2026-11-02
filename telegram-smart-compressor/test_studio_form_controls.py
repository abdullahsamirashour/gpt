# -*- coding: utf-8 -*-
"""Native Colab Form tests. No widgets, Telegram session or Google Drive needed."""
import ast
import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["TSC_TEST_MODE"] = "1"
ROOT = Path(__file__).parent


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


engine = load_module("tsc_native_form_engine", ROOT / "engine.py")
forms = load_module("tsc_native_form_controls", ROOT / "studio_form_controls.py")
with (ROOT / "Smart_Compressor.ipynb").open(encoding="utf-8") as handle:
    NOTEBOOK = json.load(handle)
CODE = "".join(NOTEBOOK["cells"][1]["source"])
PARAM_CODE = CODE[:CODE.index("\nimport os\n")]
form_defaults = {}
exec(compile(PARAM_CODE, "native-form-fields", "exec"), form_defaults)

FORM = {
    "content_kind": form_defaults["نوع_المحتوى"],
    "audio_preset": form_defaults["وضع_الصوت"],
    "video_preset": form_defaults["وضع_الفيديو"],
    "skip_if_no_saving": form_defaults["تخطي_الملف_بدون_توفير_مفيد"],
    "advanced_audio": form_defaults["تفعيل_Advanced_للصوت"],
    "audio_codec": form_defaults["صيغة_الصوت"],
    "audio_kbps": form_defaults["جودة_الصوت_kbps"],
    "audio_channels": form_defaults["قنوات_الصوت"],
    "audio_sample_rate": form_defaults["Sample_Rate_Hz"],
    "audio_noise": form_defaults["إزالة_الضوضاء"],
    "advanced_video": form_defaults["تفعيل_Advanced_للفيديو"],
    "video_codec": form_defaults["صيغة_الفيديو"],
    "video_resolution": form_defaults["دقة_الفيديو"],
    "video_fps": form_defaults["فريمات_الفيديو"],
    "video_rate_mode": form_defaults["نوع_التحكم_في_الجودة"],
    "video_crf": form_defaults["جودة_CRF"],
    "video_bitrate_kbps": form_defaults["Video_Bitrate_kbps"],
    "video_target_mb": form_defaults["الحجم_المستهدف_MiB"],
    "video_speed": form_defaults["سرعة_ترميز_الفيديو"],
    "video_audio_kbps": form_defaults["صوت_الفيديو_kbps"],
}


class NativeStudioFormTests(unittest.TestCase):
    def setUp(self):
        engine.ACTIVE_STUDIO_SETTINGS = None
        engine.reset_stop()
        self.tmp = tempfile.TemporaryDirectory()
        self.original_base = engine.BASE_DIR
        engine.BASE_DIR = Path(self.tmp.name)
        self.mock_mount = patch.object(engine, "mount_drive", return_value=None)
        self.mock_mount.start()

    def tearDown(self):
        self.mock_mount.stop()
        engine.BASE_DIR = self.original_base
        engine.ACTIVE_STUDIO_SETTINGS = None
        engine.reset_stop()
        self.tmp.cleanup()

    def test_first_cell_is_native_form_without_widget_dashboard(self):
        self.assertEqual(len(NOTEBOOK["cells"]), 6)
        self.assertEqual(NOTEBOOK["cells"][1]["metadata"]["cellView"], "form")
        self.assertGreaterEqual(CODE.count("#@param"), 20)
        self.assertIn("FORM_REL = ", CODE)
        self.assertIn('form.perform_form_action(engine, actions[العملية]', CODE)
        self.assertNotIn("ui.show_studio(engine)", CODE)
        self.assertNotIn('UI_REL = ', CODE)
        self.assertNotIn("ipywidgets", CODE)
        self.assertIn("media_studio_ui.py", (ROOT / "media_studio_ui.py").name)
        compile(CODE, "native-colab-form-cell", "exec")
        self.assertTrue(
            "▶ تشغيل الضغط" in form_defaults["العملية"] and
            "تطبيق حقول الفورم الظاهرة" in form_defaults["مصدر_الإعدادات"]
        )
        drive = "".join(NOTEBOOK["cells"][3]["source"])
        telegram = "".join(NOTEBOOK["cells"][5]["source"])
        self.assertIn('direction="drive_to_telegram"', drive)
        self.assertIn('direction="telegram_to_drive"', telegram)

    def test_audio_only_choice_for_video_is_explicit_and_preserves_old_labels(self):
        self.assertIn("🎧 صوت فقط — حتى الفيديو يتحول لصوت", CODE)
        self.assertIn("🎧 صوت فقط — حتى الفيديو يتحول لصوت", forms.CONTENT_KINDS)
        for label in ("🎧 صوت فقط — حتى الفيديو يتحول لصوت", "صوت فقط"):
            settings = forms.collect_form_settings(
                engine, {**FORM, "content_kind": label})
            self.assertEqual(settings["source"], "audio")
            engine.apply_studio_settings(settings)
            video_message = SimpleNamespace(
                file=SimpleNamespace(mime_type="video/mp4"),
                video=True, audio=None, voice=None,
            )
            self.assertEqual(engine.resolve_profile(video_message), "AUDIO_CUSTOM")
        self.assertEqual(forms.collect_form_settings(engine, FORM)["source"], "auto")

    def test_native_form_default_applies_visible_audio_only_not_old_drive_last_used(self):
        # This reproduces the iPad screenshot: visible Audio Only but the old
        # selector silently selected a saved source=auto and encoded H264.
        engine.save_studio_preferences(
            {**engine.STUDIO_FACTORY_SETTINGS, "source": "auto",
             "video_crf": 28},
            save_default=True,
        )
        self.assertEqual(
            form_defaults["مصدر_الإعدادات"],
            "📝 تطبيق حقول الفورم الظاهرة",
        )
        chosen_form = {
            **FORM, "content_kind": "🎧 صوت فقط — حتى الفيديو يتحول لصوت",
            "advanced_audio": True, "audio_codec": "MP3", "audio_kbps": "64",
        }
        with patch.object(engine, "run") as run, \
             patch("builtins.print") as printed:
            result = forms.perform_form_action(engine, "run", "form", chosen_form)
        run.assert_called_once()
        self.assertEqual(result["settings"]["source"], "audio")
        self.assertEqual(result["settings"]["audio_codec"], "mp3")
        self.assertEqual(result["settings"]["audio_kbps"], 64)
        self.assertEqual(engine.ACTIVE_STUDIO_SETTINGS["source"], "audio")
        self.assertEqual(engine.load_studio_preferences()["last_used"]["source"], "audio")
        video_message = SimpleNamespace(
            video=True, audio=None, voice=None,
            file=SimpleNamespace(mime_type="video/mp4"),
        )
        audio_message = SimpleNamespace(
            video=None, audio=True, voice=None,
            file=SimpleNamespace(mime_type="audio/m4a"),
        )
        self.assertEqual(engine.resolve_profile(video_message), "AUDIO_CUSTOM")
        self.assertEqual(engine.resolve_profile(audio_message), "AUDIO_CUSTOM")
        rendered = "\n".join(
            str(a) for invocation in printed.call_args_list
            for a in invocation.args
        )
        self.assertIn("الفيديوهات هتخرج ملفات صوت فقط", rendered)
        self.assertNotIn("H264 |", rendered)
        self.assertNotIn("CRF 28", rendered)

    def test_old_notebook_tab_cannot_silently_run_h264_with_visible_audio_only(self):
        # Older notebook tabs use 'last' by default. Protect them too: fail
        # *before* engine.run, rather than waste hours on H264 processing.
        engine.save_studio_preferences(
            {**engine.STUDIO_FACTORY_SETTINGS, "source": "auto"})
        with patch.object(engine, "run") as run:
            with self.assertRaisesRegex(ValueError, "لن نبدأ ضغط فيديو بدل صوت"):
                forms.perform_form_action(engine, "run", "last", {
                    **FORM, "content_kind": "🎧 صوت فقط — حتى الفيديو يتحول لصوت",
                })
        run.assert_not_called()
        self.assertEqual(engine.ACTIVE_STUDIO_SETTINGS, None)
        # Preview remains allowed to inspect stored settings without encoding.
        with patch.object(engine, "run") as run:
            result = forms.perform_form_action(engine, "preview", "last", {
                **FORM, "content_kind": "🎧 صوت فقط — حتى الفيديو يتحول لصوت",
            })
        self.assertEqual(result["settings"]["source"], "auto")
        run.assert_not_called()

    def test_explicit_saved_mode_still_restores_audio_profile(self):
        saved = {**engine.STUDIO_FACTORY_SETTINGS, "source": "audio",
                 "audio_codec": "aac", "audio_kbps": 48}
        engine.save_studio_preferences(saved, save_default=True)
        with patch.object(engine, "run") as run:
            result = forms.perform_form_action(engine, "run", "last", FORM)
        run.assert_called_once()
        self.assertEqual(result["settings"]["source"], "audio")
        self.assertEqual(result["settings"]["audio_codec"], "aac")
        self.assertEqual(result["settings"]["audio_kbps"], 48)

    def test_delivery_filename_preserves_original_basename_and_changes_only_extension(self):
        for src, ext, expected in (
            ("Principles of general and regional anaesthesia.mp4", ".ogg",
             "Principles of general and regional anaesthesia.ogg"),
            ("Post OP pain management.mp4", ".mp3",
             "Post OP pain management.mp3"),
            ("محاضرة قلبية.mp4", ".m4a", "محاضرة قلبية.m4a"),
            ("Lecture.with.multiple.dots.m4a", ".ogg",
             "Lecture.with.multiple.dots.ogg"),
            ("Recording.mp4", ".mp4", "Recording.mp4"),
            ("telegram_489.ogg", ".ogg", "telegram_489.ogg"),
            ("123.mp4", ".ogg", "123.ogg"),
        ):
            with self.subTest(src=src, ext=ext):
                self.assertEqual(engine.delivery_filename(src, ext), expected)
        self.assertLessEqual(len(engine.delivery_filename("a" * 200 + ".mp4", ".ogg")), 180)
        with self.assertRaises(ValueError):
            engine.delivery_filename("lecture.mp4", ".exe")

    def test_form_hourly_size_table_uses_kilobits_not_kilobytes(self):
        self.assertIn("📊 حجم ساعة واحدة من الصوت", CODE)
        for kbps in (8, 16, 24, 32, 48, 64, 96, 128, 192):
            decimal_mb = kbps * 3600 / 8 / 1000
            line = f"#@markdown | {kbps} kbps | {decimal_mb:.1f} MB |"
            self.assertIn(line, CODE)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "Real FFmpeg is required")
    def test_real_video_is_converted_to_audio_only_mp3_and_uploaded_as_audio(self):
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            src = temp / "classroom.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15",
                "-f", "lavfi", "-i", "sine=frequency=650:sample_rate=44100",
                "-t", "1.25", "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac", "-b:a", "96k", str(src),
            ], check=True, timeout=30)

            settings = forms.collect_form_settings(engine, {
                **FORM,
                "content_kind": "🎧 صوت فقط — حتى الفيديو يتحول لصوت",
                "advanced_audio": True,
                "audio_codec": "MP3",
                "audio_kbps": "64",
                "audio_channels": "Mono",
                "audio_sample_rate": "24000",
            })
            engine.apply_studio_settings(settings)
            info = engine.ffprobe(src)
            self.assertTrue(info.has_audio and info.has_video)
            message = SimpleNamespace(
                id=777, file=SimpleNamespace(name="classroom.mp4", mime_type="video/mp4"),
                video=True, voice=False, audio=False,
            )
            captures = []

            class FakeClient:
                async def send_file(self, _channel, output, **kwargs):
                    output_info = engine.ffprobe(Path(output))
                    captures.append({
                        "suffix": Path(output).suffix,
                        "has_audio": output_info.has_audio,
                        "has_video": output_info.has_video,
                        "mime": kwargs.get("mime_type"),
                        "attrs": kwargs.get("attributes"),
                        "real_path_name": Path(output).name,
                        "reply_to": kwargs.get("reply_to"),
                    })
                    return SimpleNamespace(id=991)

                async def send_message(self, *args, **kwargs):
                    raise AssertionError("Video with audio should not fail")

            prepared = {
                "job": {"source": message, "command": None, "profile": None,
                        "target_mb": None, "rerun": False},
                "msg": message,
                "name": "Principles of general and regional anaesthesia.mp4", "src": src, "info": info,
                "started_at": 0.0, "download_seconds": 0.0,
                "probe_seconds": 0.0, "error": None,
            }
            state = engine.default_state()
            with patch.object(engine, "TMP_DIR", temp), \
                 patch.object(engine, "STATE_PATH", temp / "state.json"), \
                 patch.object(engine, "ERROR_LOG", temp / "error.log"):
                result = asyncio.run(engine.process_job(
                    FakeClient(), object(), state, prepared, False, 1, 1))
            self.assertTrue(result["ok"], result)
            self.assertEqual(len(captures), 1)
            self.assertEqual(captures[0]["suffix"], ".mp3")
            self.assertEqual(captures[0]["mime"], "audio/mpeg")
            self.assertTrue(captures[0]["has_audio"])
            self.assertFalse(captures[0]["has_video"])
            self.assertEqual(captures[0]["reply_to"], 777)
            from telethon import utils
            from telethon.tl.types import DocumentAttributeFilename
            attrs, _ = utils.get_attributes(
                str(src.parent / captures[0]["real_path_name"]),
                mime_type="audio/mpeg",
                attributes=captures[0]["attrs"],
            )
            sent_filename = next(
                attr.file_name for attr in attrs
                if isinstance(attr, DocumentAttributeFilename)
            )
            self.assertEqual(
                sent_filename,
                "Principles of general and regional anaesthesia.mp3",
            )
            self.assertNotIn("179360", sent_filename)
            self.assertNotIn("_compressed", sent_filename)
            self.assertIn("_compressed", captures[0]["real_path_name"])
            self.assertEqual(state["lineage"]["991"]["profile"], "AUDIO_CUSTOM")
            self.assertEqual(state["lineage"]["991"]["media_settings"]["source"], "audio")
            self.assertIn(777, state["processed_source_ids"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "Real FFmpeg is required")
    def test_real_audio_file_remains_audio_with_selected_opus_bitrate(self):
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            src = temp / "lecture.wav"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                "-t", "1.3", "-ac", "1", "-c:a", "pcm_s16le", str(src),
            ], check=True, timeout=30)
            chosen = {
                **FORM, "content_kind": "🎧 صوت فقط — حتى الفيديو يتحول لصوت",
                "advanced_audio": True, "audio_codec": "Opus / OGG",
                "audio_kbps": "32", "audio_sample_rate": "24000",
            }
            with patch.object(engine, "run"):
                forms.perform_form_action(engine, "run", "form", chosen)
            original_info = engine.ffprobe(src)
            self.assertTrue(original_info.has_audio)
            self.assertFalse(original_info.has_video)
            msg = SimpleNamespace(
                id=784, audio=True, video=False, voice=False,
                file=SimpleNamespace(mime_type="audio/wav", name="lecture.wav"),
            )
            uploads = []

            class FakeTelegram:
                async def send_file(self, channel, output, **kwargs):
                    path = Path(output)
                    result = engine.ffprobe(path)
                    uploads.append({
                        "suffix": path.suffix,
                        "audio": result.has_audio, "video": result.has_video,
                        "mime": kwargs["mime_type"],
                        "attrs": kwargs["attributes"],
                        "local_name": path.name,
                    })
                    return SimpleNamespace(id=995)

                async def send_message(self, *_args, **_kwargs):
                    raise AssertionError("Audio-to-audio must succeed")

            state = engine.default_state()
            prepared = {
                "job": {"source": msg, "command": None, "profile": None,
                        "target_mb": None, "rerun": False},
                "msg": msg, "name": "محاضرة قلبية.wav", "src": src,
                "info": original_info, "started_at": 0.0,
                "download_seconds": 0.0, "probe_seconds": 0.0,
                "error": None,
            }
            with patch.object(engine, "TMP_DIR", temp), \
                 patch.object(engine, "STATE_PATH", temp / "state.json"), \
                 patch.object(engine, "ERROR_LOG", temp / "error.log"):
                result = asyncio.run(engine.process_job(
                    FakeTelegram(), object(), state, prepared, False, 1, 1
                ))
            self.assertTrue(result["ok"], result)
            self.assertEqual(len(uploads), 1)
            self.assertEqual(uploads[0]["suffix"], ".ogg")
            self.assertTrue(uploads[0]["audio"])
            self.assertFalse(uploads[0]["video"])
            self.assertEqual(uploads[0]["mime"], "audio/ogg")
            from telethon.tl.types import DocumentAttributeFilename
            self.assertEqual(
                [a.file_name for a in uploads[0]["attrs"]
                 if isinstance(a, DocumentAttributeFilename)],
                ["محاضرة قلبية.ogg"],
            )
            self.assertIn("_compressed", uploads[0]["local_name"])
            self.assertEqual(state["lineage"]["995"]["profile"], "AUDIO_CUSTOM")
            self.assertEqual(state["lineage"]["995"]["media_settings"]["audio_kbps"], 32)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "Real FFmpeg is required")
    def test_silent_video_is_not_uploaded_or_marked_processed_as_audio(self):
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            src = temp / "silent.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10",
                "-t", "0.8", "-c:v", "libx264", "-preset", "ultrafast", str(src),
            ], check=True, timeout=30)
            engine.apply_studio_settings(forms.collect_form_settings(
                engine, {**FORM, "content_kind": "🎧 صوت فقط — حتى الفيديو يتحول لصوت"}))
            msg = SimpleNamespace(
                id=778, file=SimpleNamespace(name="silent.mp4", mime_type="video/mp4"),
                video=True, voice=False, audio=False,
            )
            errors = []

            class FakeClient:
                async def send_file(self, *_args, **_kwargs):
                    raise AssertionError("Silent video must never upload bogus audio")

                async def send_message(self, _channel, text, **_kwargs):
                    errors.append(text)

            state = engine.default_state()
            prepared = {
                "job": {"source": msg, "command": None, "profile": None,
                        "target_mb": None, "rerun": False},
                "msg": msg, "name": "silent.mp4", "src": src,
                "info": engine.ffprobe(src), "started_at": 0.0,
                "download_seconds": 0.0, "probe_seconds": 0.0, "error": None,
            }
            with patch.object(engine, "TMP_DIR", temp), \
                 patch.object(engine, "STATE_PATH", temp / "state.json"), \
                 patch.object(engine, "ERROR_LOG", temp / "error.log"):
                result = asyncio.run(engine.process_job(
                    FakeClient(), object(), state, prepared, False, 1, 1))
            self.assertFalse(result["ok"])
            self.assertFalse(state["processed_source_ids"])
            self.assertTrue(any("E412" in error for error in errors))

    def test_quick_presets_override_hidden_advanced_fields(self):
        chosen = {**FORM, "audio_preset": "أصغر حجم (8 kbps)",
                  "video_preset": "شرح وكتابة أوضح", "audio_codec": "MP3",
                  "audio_kbps": "192", "video_codec": "H.265 (HEVC)"}
        settings = forms.collect_form_settings(engine, chosen)
        self.assertEqual(settings["audio_codec"], "opus")
        self.assertEqual(settings["audio_kbps"], 8)
        self.assertEqual(settings["video_resolution"], "1080")
        self.assertEqual(settings["video_crf"], 24)
        self.assertEqual(settings["video_codec"], "h264")
        self.assertEqual(settings["download_connections"], "6")

    def test_all_advanced_audio_video_settings_are_wired(self):
        selected = {**FORM, "content_kind": "فيديو فقط",
                    "advanced_audio": True, "audio_codec": "MP3",
                    "audio_kbps": "96", "audio_channels": "Stereo",
                    "audio_sample_rate": "48000", "audio_noise": "Light",
                    "advanced_video": True, "video_codec": "H.265 (HEVC)",
                    "video_resolution": "1080", "video_fps": "30",
                    "video_rate_mode": "Bitrate يدوي", "video_bitrate_kbps": 2800,
                    "video_speed": "سريع", "video_audio_kbps": "128",
                    "skip_if_no_saving": False}
        settings = forms.collect_form_settings(engine, selected)
        self.assertEqual(settings["audio_codec"], "mp3")
        self.assertEqual(settings["audio_kbps"], 96)
        self.assertEqual(settings["audio_channels"], "stereo")
        self.assertEqual(settings["audio_noise"], "light")
        self.assertEqual(settings["video_codec"], "h265")
        self.assertEqual(settings["video_resolution"], "1080")
        self.assertEqual(settings["video_fps"], "30")
        self.assertEqual(settings["video_rate_mode"], "bitrate")
        self.assertEqual(settings["video_bitrate_kbps"], 2800)
        self.assertEqual(settings["video_audio_kbps"], 128)
        self.assertFalse(settings["skip_if_no_saving"])

    def test_target_size_requires_h264_and_invalid_mp3_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "H.264"):
            forms.collect_form_settings(engine, {
                **FORM, "advanced_video": True,
                "video_codec": "H.265 (HEVC)",
                "video_rate_mode": "حجم مستهدف",
            })
        with self.assertRaisesRegex(ValueError, "MP3"):
            forms.collect_form_settings(engine, {
                **FORM, "advanced_audio": True, "audio_codec": "MP3",
                "audio_kbps": "8",
            })

    def test_preview_does_not_write_drive_or_start_telegram(self):
        with patch.object(engine, "run") as run:
            result = forms.perform_form_action(engine, "preview", "form", FORM)
        self.assertEqual(result["action"], "preview")
        self.assertFalse(engine.studio_settings_path().exists())
        run.assert_not_called()

    def test_save_default_pins_without_telegram_login(self):
        with patch.object(engine, "run") as run:
            result = forms.perform_form_action(
                engine, "save_default", "form",
                {**FORM, "advanced_audio": True, "audio_kbps": "32"},
            )
        run.assert_not_called()
        self.assertEqual(result["action"], "save_default")
        saved = engine.load_studio_preferences()
        self.assertEqual(saved["default"]["audio_kbps"], 32)
        self.assertEqual(saved["last_used"]["audio_kbps"], 32)
        self.assertIsNotNone(saved["default_saved_at"])
        self.assertTrue(engine.studio_settings_path().exists())

    def test_last_used_survives_reopen_and_new_run(self):
        with patch.object(engine, "run") as run:
            forms.perform_form_action(
                engine, "run", "form",
                {**FORM, "advanced_audio": True,
                 "audio_codec": "AAC / M4A", "audio_kbps": "64"},
            )
            self.assertEqual(engine.ACTIVE_STUDIO_SETTINGS["audio_codec"], "aac")
            chosen = forms.perform_form_action(
                engine, "run", "last",
                {**FORM, "advanced_audio": True,
                 "audio_codec": "MP3", "audio_kbps": "192"},
            )
        self.assertEqual(run.call_count, 2)
        self.assertEqual(chosen["settings"]["audio_codec"], "aac")
        self.assertEqual(chosen["settings"]["audio_kbps"], 64)
        self.assertEqual(engine.load_studio_preferences()["last_used"]["audio_codec"], "aac")

    def test_saved_default_is_independent_of_last_used(self):
        with patch.object(engine, "run"):
            forms.perform_form_action(engine, "save_default", "form", {
                **FORM, "video_preset": "أصغر حجم"})
            forms.perform_form_action(engine, "run", "form", {
                **FORM, "video_preset": "شرح وكتابة أوضح"})
            selected = forms.perform_form_action(engine, "run", "default", FORM)
        self.assertEqual(selected["settings"]["video_resolution"], "540")
        self.assertEqual(engine.load_studio_preferences()["last_used"]["video_resolution"], "540")
        self.assertEqual(engine.load_studio_preferences()["default"]["video_resolution"], "540")

    def test_old_eighteen_connection_preferences_are_pinned_to_six(self):
        old = {**engine.STUDIO_FACTORY_SETTINGS, "download_connections": "18"}
        engine.save_studio_preferences(old, save_default=True)
        values, origin = forms.pick_studio_settings(engine, "last", FORM)
        self.assertEqual(values["download_connections"], "6")
        self.assertIn("Drive", origin)

    def test_first_time_saved_mode_falls_back_to_safe_factory(self):
        settings, origin = forms.pick_studio_settings(engine, "last", FORM)
        self.assertEqual(settings["audio_kbps"], 24)
        self.assertEqual(settings["video_codec"], "h264")
        self.assertIn("الأولية", origin)


if __name__ == "__main__":
    unittest.main()
