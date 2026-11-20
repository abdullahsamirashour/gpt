# -*- coding: utf-8 -*-
"""Side-effect-free smoke/regression tests for Smart Tabs.
Run: python -m unittest discover -s telegram-smart-compressor -p test_smart_tabs_ui.py -v
"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("smart_tabs_ui", HERE / "smart_tabs_ui.py")
smart = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smart)


class SchemaTests(unittest.TestCase):
    def test_original_fields_preserved(self):
        smart.validate_schema()
        counts = [len(smart.FIELD_SCHEMA[k]) for k in smart.TABS]
        self.assertEqual(counts, [22, 7, 8, 12, 20])
        self.assertEqual(sum(counts), 69)
        self.assertEqual(len([x for x in smart.FIELD_SCHEMA["gemini"]
                              if x["key"] != "وضع_تجهيز_الصوت"]), 19)

    def test_video_presets_match_engine_factory(self):
        expected = {
            "متوازن": ("720p", "CRF 28"),
            "شرح وكتابة أوضح": ("1080p", "CRF 24"),
            "أصغر حجم": ("540p", "CRF 32"),
            "أسرع معالجة": ("720p", "CRF 30"),
        }
        for preset, fragments in expected.items():
            label = smart.VIDEO_HINTS[preset]
            self.assertTrue(all(fragment in label for fragment in fragments), label)

    def test_telegram_drive_delete_default_true(self):
        fields = {x["key"]: x for x in smart.FIELD_SCHEMA["tg_drive"]}
        self.assertIs(fields["حذف_رسائل_Telegram_بعد_التحقق"]["default"], True)
        self.assertEqual(fields["اسم_الفولدر_الفرعي"]["default"], "")
        self.assertEqual(fields["التشغيل"]["default"], "معاينة فقط")


class WidgetTests(unittest.TestCase):
    def setUp(self):
        self.ui = smart.SmartTabs()
        self.ui._sha = lambda: "a" * 40

    def test_init_does_not_load_engines(self):
        # Construction is side-effect-free. _sha is only used at execution time.
        self.assertIsNone(self.ui._commit)
        self.assertEqual(len(self.ui.tab.children), 5)

    def test_mobile_navigation_is_synced_with_tabs_and_execution(self):
        self.assertEqual(self.ui.mobile_nav.value, 0)
        self.ui.mobile_nav.value = 3
        self.assertEqual(self.ui.tab.selected_index, 3)
        self.assertEqual(self.ui.snapshot()[0], "web")
        self.assertIn("Internet Downloader", self.ui.help_text.value)

        self.ui.tab.selected_index = 4
        self.assertEqual(self.ui.mobile_nav.value, 4)
        self.assertEqual(self.ui.snapshot()[0], "gemini")
        self.assertIn("Gemini Ready", self.ui.help_text.value)

    def test_manual_display_modes_and_auto_reset(self):
        self.assertEqual(self.ui.view_mode.value, "auto")
        self.ui.view_mode.value = "compact"
        self.assertIn("ml-force-compact", self.ui.main._dom_classes)
        self.assertNotIn("ml-force-tabs", self.ui.main._dom_classes)
        self.ui.view_mode.value = "tabs"
        self.assertIn("ml-force-tabs", self.ui.main._dom_classes)
        self.assertNotIn("ml-force-compact", self.ui.main._dom_classes)
        self.ui.view_mode.value = "auto"
        self.assertNotIn("ml-force-tabs", self.ui.main._dom_classes)
        self.assertNotIn("ml-force-compact", self.ui.main._dom_classes)

    def test_mobile_touch_fields_and_short_video_preset_labels(self):
        self.assertIn("@media (max-width:760px)", smart.RESPONSIVE_CSS)
        self.assertIn("font-size:16px", smart.RESPONSIVE_CSS)
        self.assertIn("min-height:44px", smart.RESPONSIVE_CSS)
        self.assertEqual(self.ui.help_panel.selected_index, None)
        self.assertEqual(len(self.ui.mobile_nav.options), 5)
        video = self.ui.controls["compress"]["وضع_الفيديو"]
        self.assertEqual(video.layout.min_height, "44px")
        labels = [label for label, value in video.options]
        self.assertTrue(any("1080p" in x for x in labels))
        self.assertTrue(all(len(x) < 30 for x in labels))
        self.assertEqual(len(self.ui.tab.children), 5)

    def test_collapsed_help_contains_undo_and_classic_fallback(self):
        self.assertIn("نسخة احتياطية", self.ui.help_text.value)
        self.assertIn(smart.CLASSIC_COLAB, self.ui.help_text.value)
        self.assertIn(smart.UNDO_BACKUP, self.ui.help_text.value)
        self.assertIn("Help", self.ui.help_panel.get_title(0))

    def test_dynamic_compressor_and_presets(self):
        self.ui.controls["compress"]["نوع_المحتوى"].value = "🎧 صوت فقط — حتى الفيديو يتحول لصوت"
        self.assertEqual(self.ui.rows["compress"]["وضع_الفيديو"].layout.display, "none")
        self.assertEqual(self.ui.rows["compress"]["وضع_الصوت"].layout.display, "")
        self.ui.controls["compress"]["نوع_المحتوى"].value = "تلقائي — حسب الملف"
        self.assertEqual(self.ui.rows["compress"]["وضع_الفيديو"].layout.display, "")
        preset = self.ui.controls["compress"]["وضع_الفيديو"]
        self.assertEqual(len(preset.options), 4)
        self.assertIn("1080p", str(preset.options))

    def test_web_dynamic_options(self):
        c = self.ui.controls["web"]
        c["صيغة_الناتج"].value = "🎵 صوت OGG (Opus)"
        self.assertEqual(self.ui.rows["web"]["Bitrate_الصوت_kbps"].layout.display, "")
        self.assertEqual(self.ui.rows["web"]["OGG_بدون_إعادة_ترميز_إن_أمكن"].layout.display, "")
        self.assertEqual(self.ui.rows["web"]["جودة_تحويل_MP4_CRF"].layout.display, "none")
        c["مكان_الحفظ"].value = "Telegram"
        self.assertEqual(self.ui.rows["web"]["عرض_Telegram"].layout.display, "")
        self.assertEqual(self.ui.rows["web"]["مجلد_فرعي"].layout.display, "none")

    def test_telegram_to_drive_delete_and_subfolder_visible(self):
        self.assertEqual(self.ui.rows["tg_drive"]["اسم_الفولدر_الفرعي"].layout.display, None)
        self.assertEqual(self.ui.controls["tg_drive"]["حذف_رسائل_Telegram_بعد_التحقق"].value, True)

    def test_telegram_to_drive_dispatch(self):
        received = {}
        self.ui.tab.selected_index = 2
        c = self.ui.controls["tg_drive"]
        c["اسم_الفولدر_الفرعي"].value = "Surgery Lectures"
        c["التشغيل"].value = "تنفيذ النقل"
        self.ui._component = lambda *args, **kw: SimpleNamespace(
            run_transfer=lambda **kw: received.update(kw))
        self.ui.execute_selected()
        self.assertEqual(received["direction"], "telegram_to_drive")
        self.assertEqual(received["drive_subfolder"], "Surgery Lectures")
        self.assertEqual(received["delete_after_transfer"], True)
        self.assertEqual(received["preview"], False)

    def test_conflicting_delete_and_recopy_rejected(self):
        self.ui.tab.selected_index = 2
        c = self.ui.controls["tg_drive"]
        c["اعادة_نسخ_المنقول_سابقا"].value = True
        with self.assertRaises(ValueError):
            self.ui.execute_selected()

    def test_drive_to_telegram_preserves_reupload(self):
        received = {}
        self.ui.tab.selected_index = 1
        c = self.ui.controls["drive_tg"]
        c["رابط_Drive"].value = "https://drive.google.com/drive/folders/abcd"
        c["التشغيل"].value = "إعادة رفع المحدد"
        c["ارقام_الملفات_لإعادة_الرفع"].value = "1,3,5-7"
        c["هاشتاج_الملفات"].value = "#surgery"
        self.ui._component = lambda *args, **kw: SimpleNamespace(
            run_transfer=lambda **kw: received.update(kw))
        self.ui.execute_selected()
        self.assertTrue(received["reupload_selected"])
        self.assertEqual(received["selected_numbers"], "1,3,5-7")
        self.assertEqual(received["telegram_hashtag"], "#surgery")

    def test_compressor_uses_original_form_mapping(self):
        received = {}
        self.ui.tab.selected_index = 0
        c = self.ui.controls["compress"]
        c["وضع_الفيديو"].value = "شرح وكتابة أوضح"
        def fake_perform(engine, action, source, values):
            received.update({"action": action, "source": source, "values": values})
        self.ui._component = lambda commit, fn, ui_only=False: (
            SimpleNamespace(perform_form_action=fake_perform) if fn == "studio_form_controls.py"
            else SimpleNamespace())
        self.ui.execute_selected()
        self.assertEqual(received["action"], "run")
        self.assertEqual(received["source"], "form")
        self.assertEqual(received["values"]["video_preset"], "شرح وكتابة أوضح")

    def test_web_preserves_output_formats(self):
        received = {}
        self.ui.tab.selected_index = 3
        self.ui.controls["web"]["روابط_الفيديو"].value = "https://example.org/a.mp4"
        self.ui.controls["web"]["صيغة_الناتج"].value = "🎵 صوت OGG (Opus)"
        self.ui._component = lambda *args, **kw: SimpleNamespace(
            run=lambda urls, **kw: received.update({"urls": urls, **kw}))
        self.ui.execute_selected()
        self.assertEqual(received["output_format"], "ogg")
        self.assertEqual(received["destination"], "Google Drive")
        self.assertEqual(received["audio_kbps"], 96)

    def test_gemini_mode_choices_are_explicit_and_preserve_legacy(self):
        received = {}
        self.ui.tab.selected_index = 4
        mode = self.ui.controls["gemini"]["وضع_تجهيز_الصوت"]
        self.assertEqual(len(mode.options), 3)
        self.assertEqual(mode.value, "ذكي — تجنب الضغط غير الضروري")
        self.ui._component = lambda *args, **kwargs: SimpleNamespace(
            run=lambda **kw: received.update(kw))
        for label, backend in [
            ("ذكي — تجنب الضغط غير الضروري", "smart"),
            ("الحفاظ على الجودة — تجنب الضغط قدر الإمكان", "preserve"),
            ("ضغط دائم — النظام القديم", "always"),
        ]:
            mode.value = label
            self.ui.execute_selected()
            self.assertEqual(received["preparation_mode"], backend)

    def test_gemini_preserves_preview_and_deletion(self):
        received = {}
        self.ui.tab.selected_index = 4
        self.ui._component = lambda *args, **kw: SimpleNamespace(
            run=lambda **kw: received.update(kw))
        self.ui.execute_selected()
        self.assertEqual(received["action"], "preview")
        self.assertTrue(received["delete_telegram"])
        self.assertEqual(received["source"], "telegram")
        self.assertEqual(received["max_part_mb"], 9.0)
        self.assertEqual(received["preparation_mode"], "smart")
        self.assertFalse(received["advanced_audio"])


if __name__ == "__main__":
    unittest.main()
