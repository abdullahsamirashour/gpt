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
        self.assertEqual(counts, [22, 7, 8, 12, 19])
        self.assertEqual(sum(counts), 68)

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


if __name__ == "__main__":
    unittest.main()
