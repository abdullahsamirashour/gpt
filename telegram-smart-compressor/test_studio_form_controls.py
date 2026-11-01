# -*- coding: utf-8 -*-
"""Native Colab Form tests. No widgets, Telegram session or Google Drive needed."""
import ast
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
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
            "آخر تشغيل" in form_defaults["مصدر_الإعدادات"]
        )
        drive = "".join(NOTEBOOK["cells"][3]["source"])
        telegram = "".join(NOTEBOOK["cells"][5]["source"])
        self.assertIn('direction="drive_to_telegram"', drive)
        self.assertIn('direction="telegram_to_drive"', telegram)

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
