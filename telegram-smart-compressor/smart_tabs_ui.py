# -*- coding: utf-8 -*-
"""Media Lite Smart Tabs UI for Colab.

All 68 original #@param fields are preserved (same underlying values).
UI construction is lazy and side-effect-free: only pressing the native
Run cell executes any transfer, encoding, deletion or Drive mounting.
Engines are loaded from one pinned Git commit per runtime.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import types
import urllib.error
import urllib.request
from html import escape

FIELD_SCHEMA = json.loads(r'''{"compress":[{"key":"العملية","type":"choice","default":"▶ تشغيل الضغط","choices":["▶ تشغيل الضغط","💾 Save as Default — حفظ فقط","👁️ معاينة الإعدادات فقط"]},{"key":"مصدر_الإعدادات","type":"choice","default":"📝 تطبيق حقول الفورم الظاهرة","choices":["📝 تطبيق حقول الفورم الظاهرة","☁️ استرجاع آخر تشغيل (يتجاهل الحقول الظاهرة)","⭐ استرجاع الافتراضي المثبّت (يتجاهل الحقول الظاهرة)"]},{"key":"نوع_المحتوى","type":"choice","default":"تلقائي — حسب الملف","choices":["تلقائي — حسب الملف","🎧 صوت فقط — حتى الفيديو يتحول لصوت","🎬 فيديو — احتفظ بالصورة"]},{"key":"وضع_الصوت","type":"choice","default":"محاضرات واضحة (24 kbps)","choices":["محاضرات واضحة (24 kbps)","وضوح أعلى (32 kbps)","أصغر حجم (8 kbps)","موسيقى / صوت غني (96 kbps)"]},{"key":"وضع_الفيديو","type":"choice","default":"متوازن","choices":["متوازن","شرح وكتابة أوضح","أصغر حجم","أسرع معالجة"]},{"key":"تخطي_الملف_بدون_توفير_مفيد","type":"boolean","default":true},{"key":"تفعيل_Advanced_للصوت","type":"boolean","default":false},{"key":"صيغة_الصوت","type":"choice","default":"Opus / OGG","choices":["Opus / OGG","AAC / M4A","MP3"]},{"key":"جودة_الصوت_kbps","type":"choice","default":"24","choices":["8","12","16","24","32","48","64","96","128","160","192"]},{"key":"قنوات_الصوت","type":"choice","default":"Mono","choices":["Mono","Stereo"]},{"key":"Sample_Rate_Hz","type":"choice","default":"24000","choices":["auto","12000","16000","24000","48000"]},{"key":"إزالة_الضوضاء","type":"choice","default":"Off","choices":["Off","Light"]},{"key":"تفعيل_Advanced_للفيديو","type":"boolean","default":false},{"key":"صيغة_الفيديو","type":"choice","default":"H.264 (الأكثر توافقًا)","choices":["H.264 (الأكثر توافقًا)","H.265 (HEVC)"]},{"key":"دقة_الفيديو","type":"choice","default":"720","choices":["source","480","540","720","1080"]},{"key":"فريمات_الفيديو","type":"choice","default":"auto","choices":["auto","source","12","15","24","30"]},{"key":"نوع_التحكم_في_الجودة","type":"choice","default":"جودة CRF","choices":["جودة CRF","Bitrate يدوي","حجم مستهدف"]},{"key":"جودة_CRF","type":"slider","default":28,"min":18,"max":35,"step":1},{"key":"Video_Bitrate_kbps","type":"integer","default":1400},{"key":"الحجم_المستهدف_MiB","type":"integer","default":80},{"key":"سرعة_ترميز_الفيديو","type":"choice","default":"متوازن","choices":["سريع","متوازن","أبطأ / ملف أصغر غالبًا"]},{"key":"صوت_الفيديو_kbps","type":"choice","default":"64","choices":["32","40","48","64","96","128","160","192"]}],"drive_tg":[{"key":"رابط_Drive","type":"string","default":""},{"key":"هاشتاج_الملفات","type":"string","default":""},{"key":"عدد_الملفات","type":"integer","default":0},{"key":"ارقام_الملفات_لإعادة_الرفع","type":"string","default":""},{"key":"شامل_المجلدات_الفرعية","type":"boolean","default":true},{"key":"طريقة_الدخول","type":"choice","default":"QR بدون كود","choices":["QR بدون كود","كود داخل Telegram"]},{"key":"التشغيل","type":"choice","default":"معاينة فقط","choices":["معاينة فقط","تنفيذ النقل","إعادة رفع المحدد"]}],"tg_drive":[{"key":"قناة_Telegram_المصدر","type":"string","default":""},{"key":"فولدر_Drive_للحفظ","type":"string","default":""},{"key":"اسم_الفولدر_الفرعي","type":"string","default":""},{"key":"اعادة_نسخ_المنقول_سابقا","type":"boolean","default":false},{"key":"حذف_رسائل_Telegram_بعد_التحقق","type":"boolean","default":true},{"key":"عدد_الملفات","type":"integer","default":0},{"key":"طريقة_الدخول","type":"choice","default":"QR بدون كود","choices":["QR بدون كود","كود داخل Telegram"]},{"key":"التشغيل","type":"choice","default":"معاينة فقط","choices":["معاينة فقط","تنفيذ النقل"]}],"web":[{"key":"روابط_الفيديو","type":"string","default":""},{"key":"جودة_الفيديو","type":"choice","default":"الأصلية","choices":["الأصلية","1080p","720p","480p"]},{"key":"صيغة_الناتج","type":"choice","default":"🎬 فيديو MP4 (H.264/AAC — Telegram Preview)","choices":["🎬 فيديو MP4 (H.264/AAC — Telegram Preview)","🎞️ فيديو WebM (VP9/Opus)","🎵 صوت MP3","🎵 صوت M4A (AAC)","🎵 صوت OGG (Opus)","📦 أرشيف ZIP للملف الأصلي","📄 الملف الأصلي بدون تحويل"]},{"key":"Bitrate_الصوت_kbps","type":"choice","default":"96","choices":["24","32","48","64","96","128","160","192"]},{"key":"OGG_بدون_إعادة_ترميز_إن_أمكن","type":"boolean","default":false},{"key":"جودة_تحويل_MP4_CRF","type":"slider","default":20,"min":18,"max":32,"step":1},{"key":"مكان_الحفظ","type":"choice","default":"Google Drive","choices":["Google Drive","Telegram","Colab فقط"]},{"key":"عرض_Telegram","type":"choice","default":"▶️ معاينة ومشغّل (عند دعم الصيغة)","choices":["▶️ معاينة ومشغّل (عند دعم الصيغة)","📄 ملف Document بدون معاينة"]},{"key":"مجلد_فرعي","type":"string","default":""},{"key":"دخول_Telegram","type":"choice","default":"QR بدون كود","choices":["QR بدون كود","كود داخل Telegram"]},{"key":"استخراج_متقدم_لروابط_Awesome","type":"boolean","default":true},{"key":"تحديث_yt_dlp","type":"boolean","default":false}],"gemini":[{"key":"العملية","type":"choice","default":"👁️ معاينة فقط","choices":["👁️ معاينة فقط","▶ تجهيز ورفع إلى Drive"]},{"key":"المصدر","type":"choice","default":"📣 Telegram — قناة Media Lite","choices":["📣 Telegram — قناة Media Lite","📁 Google Drive Folder","🔗 Google Drive Links","🌐 Internet URLs"]},{"key":"روابط_المصدر","type":"string","default":""},{"key":"اسم_الدفعة","type":"string","default":""},{"key":"عدد_الملفات","type":"integer","default":10},{"key":"شامل_مجلدات_Drive_الفرعية","type":"boolean","default":true},{"key":"وضع_الصوت","type":"choice","default":"محاضرات واضحة (24 kbps)","choices":["محاضرات واضحة (24 kbps)","وضوح أعلى (32 kbps)","أصغر حجم (8 kbps)","موسيقى / صوت غني (96 kbps)"]},{"key":"اقصى_حجم_للجزء_MB","type":"number","default":9},{"key":"اقصى_مدة_للجزء_بالدقائق","type":"integer","default":0},{"key":"اختيار_ملفات_Telegram","type":"choice","default":"الملفات الجديدة غير المعالجة","choices":["الملفات الجديدة غير المعالجة","أحدث عدد من الملفات"]},{"key":"حذف_رسائل_Telegram_بعد_التحقق","type":"boolean","default":true},{"key":"طريقة_دخول_Telegram","type":"choice","default":"QR بدون كود","choices":["QR بدون كود","كود داخل Telegram"]},{"key":"إعادة_معالجة_المكتمل","type":"boolean","default":false},{"key":"تفعيل_Advanced_Audio","type":"boolean","default":false},{"key":"صيغة_الصوت","type":"choice","default":"Opus / OGG","choices":["Opus / OGG","AAC / M4A","MP3"]},{"key":"Audio_Bitrate_kbps","type":"choice","default":"24","choices":["8","12","16","24","32","48","64","96","128","160","192"]},{"key":"قنوات_الصوت","type":"choice","default":"Mono","choices":["Mono","Stereo"]},{"key":"Audio_Sample_Rate_Hz","type":"choice","default":"24000","choices":["auto","12000","16000","24000","48000"]},{"key":"إزالة_الضوضاء","type":"choice","default":"Off","choices":["Off","Light"]}]}''')
REPO = "abdullahsamirashour/gpt"
REL = "telegram-smart-compressor"
TABS = ("compress", "drive_tg", "tg_drive", "web", "gemini")
LABELS = {
    "compress": "🗜️ Smart Compressor",
    "drive_tg": "☁️ Drive → Telegram",
    "tg_drive": "📥 Telegram → Drive",
    "web": "🌐 Internet Downloader",
    "gemini": "🎧 Gemini Ready",
}
GROUPS = {
    "compress": [
        ("Quick Settings", ["العملية", "نوع_المحتوى", "وضع_الصوت", "وضع_الفيديو",
                            "تخطي_الملف_بدون_توفير_مفيد"]),
        ("🎧 Advanced Audio", ["تفعيل_Advanced_للصوت", "صيغة_الصوت", "جودة_الصوت_kbps",
                               "قنوات_الصوت", "Sample_Rate_Hz", "إزالة_الضوضاء"]),
        ("🎬 Advanced Video", ["تفعيل_Advanced_للفيديو", "صيغة_الفيديو", "دقة_الفيديو",
                               "فريمات_الفيديو", "نوع_التحكم_في_الجودة",
                               "جودة_CRF", "Video_Bitrate_kbps", "الحجم_المستهدف_MiB",
                               "سرعة_ترميز_الفيديو", "صوت_الفيديو_kbps"]),
        ("☁️ Saved Settings", ["مصدر_الإعدادات"]),
    ],
    "drive_tg": [
        ("Quick Settings", ["رابط_Drive", "التشغيل", "عدد_الملفات",
                            "ارقام_الملفات_لإعادة_الرفع"]),
        ("⚙️ More Options", ["هاشتاج_الملفات", "شامل_المجلدات_الفرعية", "طريقة_الدخول"]),
    ],
    "tg_drive": [
        ("Quick Settings", ["قناة_Telegram_المصدر", "فولدر_Drive_للحفظ",
                            "اسم_الفولدر_الفرعي", "التشغيل",
                            "حذف_رسائل_Telegram_بعد_التحقق"]),
        ("⚙️ More Options", ["عدد_الملفات", "اعادة_نسخ_المنقول_سابقا", "طريقة_الدخول"]),
    ],
    "web": [
        ("Quick Settings", ["روابط_الفيديو", "جودة_الفيديو", "صيغة_الناتج", "مكان_الحفظ"]),
        ("🎵 Advanced Audio", ["Bitrate_الصوت_kbps", "OGG_بدون_إعادة_ترميز_إن_أمكن"]),
        ("🎞️ Advanced Video", ["جودة_تحويل_MP4_CRF"]),
        ("📤 Destination Options", ["عرض_Telegram", "مجلد_فرعي", "دخول_Telegram"]),
        ("🔧 Troubleshooting", ["استخراج_متقدم_لروابط_Awesome", "تحديث_yt_dlp"]),
    ],
    "gemini": [
        ("Quick Settings", ["العملية", "المصدر", "روابط_المصدر", "عدد_الملفات",
                            "وضع_الصوت", "اقصى_حجم_للجزء_MB"]),
        ("📁 Batch & Limits", ["اسم_الدفعة", "اقصى_مدة_للجزء_بالدقائق",
                              "شامل_مجلدات_Drive_الفرعية"]),
        ("📣 Telegram Options", ["اختيار_ملفات_Telegram",
                                 "حذف_رسائل_Telegram_بعد_التحقق",
                                 "طريقة_دخول_Telegram"]),
        ("🎧 Advanced Audio", ["تفعيل_Advanced_Audio", "صيغة_الصوت",
                               "Audio_Bitrate_kbps", "قنوات_الصوت",
                               "Audio_Sample_Rate_Hz", "إزالة_الضوضاء"]),
        ("♻️ Reprocessing", ["إعادة_معالجة_المكتمل"]),
    ],
}
FRIENDLY = {
    "العملية": "العملية",
    "مصدر_الإعدادات": "مصدر إعدادات الضغط",
    "نوع_المحتوى": "نوع المحتوى",
    "وضع_الصوت": "وضع الصوت",
    "وضع_الفيديو": "وضع الفيديو",
    "تخطي_الملف_بدون_توفير_مفيد": "تخطي الملفات لو مفيش توفير مفيد",
    "تفعيل_Advanced_للصوت": "تفعيل تخصيص الصوت بدل Preset",
    "تفعيل_Advanced_للفيديو": "تفعيل تخصيص الفيديو بدل Preset",
    "تفعيل_Advanced_Audio": "تفعيل تخصيص الصوت بدل Preset",
    "صيغة_الصوت": "صيغة الصوت",
    "جودة_الصوت_kbps": "Audio Bitrate",
    "Audio_Bitrate_kbps": "Audio Bitrate",
    "قنوات_الصوت": "Channels",
    "إزالة_الضوضاء": "Noise Reduction",
    "صيغة_الفيديو": "Video Codec",
    "دقة_الفيديو": "Resolution",
    "فريمات_الفيديو": "FPS",
    "نوع_التحكم_في_الجودة": "Quality Mode",
    "جودة_CRF": "Video CRF",
    "Video_Bitrate_kbps": "Video Bitrate (kbps)",
    "الحجم_المستهدف_MiB": "Target Size (MiB)",
    "سرعة_ترميز_الفيديو": "Encoding Speed",
    "صوت_الفيديو_kbps": "Video Audio Bitrate",
    "رابط_Drive": "رابط ملف أو فولدر Drive",
    "هاشتاج_الملفات": "هاشتاج الملفات — اختياري",
    "عدد_الملفات": "عدد الملفات (0 = الكل)",
    "ارقام_الملفات_لإعادة_الرفع": "أرقام الملفات لإعادة الرفع",
    "شامل_المجلدات_الفرعية": "تضمين المجلدات الفرعية",
    "طريقة_الدخول": "Telegram Login",
    "التشغيل": "طريقة التشغيل",
    "قناة_Telegram_المصدر": "قناة Telegram — فارغة = Media Lite",
    "فولدر_Drive_للحفظ": "فولدر Drive — فارغ = الافتراضي",
    "اسم_الفولدر_الفرعي": "اسم فولدر فرعي — اختياري",
    "اعادة_نسخ_المنقول_سابقا": "إعادة نسخ الملفات المنقولة سابقًا",
    "حذف_رسائل_Telegram_بعد_التحقق": "حذف رسائل Telegram بعد نجاح الرفع والتحقق",
    "روابط_الفيديو": "روابط التحميل (Batch)",
    "جودة_الفيديو": "جودة التنزيل من المصدر",
    "صيغة_الناتج": "صيغة الملف الناتج",
    "Bitrate_الصوت_kbps": "Audio Bitrate (kbps)",
    "OGG_بدون_إعادة_ترميز_إن_أمكن": "OGG Stream Copy (Opus فقط)",
    "جودة_تحويل_MP4_CRF": "MP4 Conversion CRF",
    "مكان_الحفظ": "مكان الحفظ",
    "عرض_Telegram": "عرض الملف على Telegram",
    "مجلد_فرعي": "اسم فولدر فرعي داخل Drive",
    "دخول_Telegram": "Telegram Login",
    "استخراج_متقدم_لروابط_Awesome": "Advanced Awesome Screenshot Fallback",
    "تحديث_yt_dlp": "تحديث yt-dlp في هذا التشغيل",
    "المصدر": "مصدر التسجيلات",
    "روابط_المصدر": "روابط Drive أو الإنترنت",
    "اسم_الدفعة": "اسم الدفعة (اختياري)",
    "شامل_مجلدات_Drive_الفرعية": "شامل فولدرات Drive الفرعية",
    "اقصى_حجم_للجزء_MB": "حد حجم كل جزء (MB)",
    "اقصى_مدة_للجزء_بالدقائق": "أقصى مدة للجزء بالدقائق (0 = بدون حد)",
    "اختيار_ملفات_Telegram": "اختيار ملفات Telegram",
    "طريقة_دخول_Telegram": "Telegram Login",
    "إعادة_معالجة_المكتمل": "إعادة معالجة المكتمل",
}
VIDEO_HINTS = {
    "متوازن": "⚖️ متوازن · 720p · CRF 28 · FPS تلقائي",
    "شرح وكتابة أوضح": "📝 شرح وكتابة أوضح · 1080p · CRF 24 · 15 FPS",
    "أصغر حجم": "🪶 أصغر حجم · 540p · CRF 32 · 12 FPS",
    "أسرع معالجة": "⚡ أسرع معالجة · 720p · CRF 30 · 24 FPS",
}
HELP = {
    "compress": ("الـPreset يحدد الإعدادات تلقائيًا. فعّل Advanced Audio أو Advanced Video "
                 "من قسمه فقط لو هتغير Codec/Bitrate/CRF. القيم المتقدمة لا تتطبق "
                 "عند ترك مفتاح التخصيص مقفول. CRF أقل = جودة أعلى وحجم أكبر غالبًا."),
    "drive_tg": ("المعاينة لا تنقل ملفات. لإعادة الرفع: اختر إعادة رفع المحدد واكتب أرقام "
                 "ملفات ظهرت في آخر معاينة. 0 = كل الملفات الجديدة."),
    "tg_drive": ("اسم الفولدر الفرعي يحفظ الملفات مباشرة داخله. خيار الحذف مُفعّل افتراضيًا "
                 "لكنه لا يعمل في المعاينة؛ عند تنفيذ النقل تُحذف الرسالة نهائيًا فقط "
                 "بعد رفع الملف والتحقق منه على Drive. إعادة النسخ والحذف لا يجتمعان."),
    "web": ("Batch يدعم حتى 100 رابط. جودة المصدر مختلفة عن CRF للتحويل. "
            "Audio Bitrate يظهر فقط للصيغ الصوتية، وMP4 CRF يظهر فقط لتحويل MP4. "
            "خيارات Telegram تظهر عند اختيار Telegram فقط."),
    "gemini": ("يجهّز التسجيلات من Telegram أو Drive أو الإنترنت ويقسم الأجزاء عند الحاجة "
               "لأقل من حجم MB المحدد، ويحفظ على Drive. اختر Preview قبل التنفيذ. "
               "الحذف يخص Telegram فقط وبعد التحقق."),
}


def validate_schema():
    """A regression guard against dropping any of the original 68 controls."""
    expected = {"compress": 22, "drive_tg": 7, "tg_drive": 8, "web": 12, "gemini": 19}
    for tab, count in expected.items():
        keys = [entry["key"] for entry in FIELD_SCHEMA[tab]]
        declared = [key for _, group in GROUPS[tab] for key in group]
        if len(keys) != count or len(keys) != len(set(keys)) or set(keys) != set(declared):
            raise RuntimeError("Smart UI field mismatch for " + tab)


class SmartTabs:
    def __init__(self):
        validate_schema()
        import ipywidgets as w
        self.w = w
        self.controls = {}
        self.rows = {}
        self.advanced = {}
        self.status = {}
        self.tab = w.Tab(layout=w.Layout(width="100%"))
        panels = []
        for tab in TABS:
            controls = {}
            rows = {}
            self.controls[tab] = controls
            self.rows[tab] = rows
            for field in FIELD_SCHEMA[tab]:
                control = self._control(tab, field)
                controls[field["key"]] = control
                rows[field["key"]] = self._row(field["key"], control)
            basic = GROUPS[tab][0][1]
            quick = [self._title("Quick Settings", HELP[tab], compact=True)]
            quick.extend(rows[key] for key in basic)
            extra = []
            for title, names in GROUPS[tab][1:]:
                extra.append((title, w.VBox([rows[key] for key in names],
                                             layout=w.Layout(width="100%", padding="6px"))))
            accordion = w.Accordion(children=[group for _, group in extra],
                                    selected_index=None, layout=w.Layout(width="100%"))
            for idx, (name, _) in enumerate(extra):
                accordion.set_title(idx, name)
            self.advanced[tab] = (accordion, extra)
            status = w.HTML(layout=w.Layout(width="100%"))
            self.status[tab] = status
            help_box = w.Accordion(
                children=[w.HTML('<div dir="rtl" style="padding:10px;line-height:1.75">' +
                                 escape(HELP[tab]) + '</div>')],
                selected_index=None,
                layout=w.Layout(width="100%"),
            )
            help_box.set_title(0, "📖 Help — شرح مختصر")
            panel = w.VBox(
                [self._title(LABELS[tab], "عدّل الاختيارات ثم اضغط ▶ على خلية التشغيل أسفل الواجهة"),
                 *quick, accordion, status, help_box],
                layout=w.Layout(width="100%", padding="10px 6px"),
            )
            panels.append(panel)
        self.tab.children = tuple(panels)
        for index, title in enumerate(("🗜️ Compress", "☁️ Drive→TG", "📥 TG→Drive",
                                       "🌐 Download", "🎧 Gemini")):
            self.tab.set_title(index, title)
        self._bind_dynamic()
        self._refresh()
        self.main = w.VBox([
            w.HTML('<div dir="rtl" style="background:#142d55;color:white;padding:16px;'
                   'border-radius:12px;font-family:Arial,sans-serif">'
                   '<b style="font-size:20px">🗜️ Media Lite — Smart Control Center</b>'
                   '<div style="font-size:12px;opacity:.9;margin-top:5px">'
                   '5 أدوات مستقلة · Quick + Advanced · جميع الإعدادات محفوظة</div></div>'),
            self.tab,
            w.HTML('<div dir="rtl" style="font-size:12px;color:#687589;padding:8px">'
                   '▶ التشغيل والإيقاف من خلية Colab الأصلية اللي تحت. '
                   'الـTabs لا تنقل ولا تضغط ولا تحذف ملفات بمجرد تغيير الاختيارات.</div>'),
        ], layout=w.Layout(width="100%", max_width="900px"))
        self._commit = None

    def _title(self, title, subtitle, compact=False):
        size = 14 if compact else 17
        return self.w.HTML(
            '<div dir="rtl" style="font-family:Arial,Tahoma,sans-serif;padding:6px 0">'
            '<b style="font-size:' + str(size) + 'px">' + escape(title) + '</b>'
            '<div style="color:#64748b;font-size:12px;line-height:1.55">' +
            escape(subtitle) + '</div></div>'
        )

    def _control(self, tab, spec):
        w = self.w
        key, kind, value = spec["key"], spec["type"], spec["default"]
        layout = w.Layout(width="100%", margin="0 0 5px 0")
        if kind == "choice":
            options = [(VIDEO_HINTS.get(item, item) if tab == "compress" and key == "وضع_الفيديو"
                        else str(item), item) for item in spec["choices"]]
            return w.Dropdown(options=options, value=value, layout=layout)
        if kind == "boolean":
            return w.Checkbox(value=value, description=FRIENDLY.get(key, key),
                              indent=False, layout=layout)
        if kind == "slider":
            return w.IntSlider(value=int(value), min=int(spec["min"]), max=int(spec["max"]),
                               step=int(spec.get("step", 1)), continuous_update=False,
                               layout=layout)
        if kind == "integer":
            return w.IntText(value=int(value), layout=layout)
        if kind == "number":
            return w.FloatText(value=float(value), layout=layout)
        if key in {"روابط_الفيديو", "روابط_المصدر"}:
            return w.Textarea(value=str(value), rows=3, placeholder="الصق الروابط هنا",
                              layout=layout)
        return w.Text(value=str(value), placeholder="اختياري" if not value else "",
                      layout=layout)

    def _row(self, key, control):
        if isinstance(control, self.w.Checkbox):
            return control
        label = FRIENDLY.get(key, key.replace("_", " "))
        return self.w.VBox([
            self.w.HTML('<div dir="rtl" style="font:600 12px Arial,Tahoma;'
                        'color:#334155;margin:3px 0">' + escape(label) + '</div>'),
            control,
        ], layout=self.w.Layout(width="100%", margin="0 0 7px 0"))

    def val(self, tab, key):
        return self.controls[tab][key].value

    def _show(self, tab, key, yes):
        self.rows[tab][key].layout.display = "" if yes else "none"

    def _set_sections(self, tab, enabled):
        accordion, all_sections = self.advanced[tab]
        chosen = [(name, group) for name, group in all_sections if name in enabled]
        if tuple(accordion.children) != tuple(group for _, group in chosen):
            accordion.selected_index = None
            accordion.children = tuple(group for _, group in chosen)
            for i, (name, _) in enumerate(chosen):
                accordion.set_title(i, name)

    def _bind_dynamic(self):
        def watch(tab, key):
            self.controls[tab][key].observe(lambda change: self._refresh(), names="value")
        for tab, keys in {
            "compress": ["نوع_المحتوى", "وضع_الفيديو", "نوع_التحكم_في_الجودة",
                         "تفعيل_Advanced_للصوت", "تفعيل_Advanced_للفيديو",
                         "مصدر_الإعدادات"],
            "drive_tg": ["التشغيل"],
            "tg_drive": ["اعادة_نسخ_المنقول_سابقا",
                         "حذف_رسائل_Telegram_بعد_التحقق", "التشغيل"],
            "web": ["صيغة_الناتج", "مكان_الحفظ"],
            "gemini": ["المصدر", "العملية"],
        }.items():
            for key in keys:
                watch(tab, key)

    def _refresh(self):
        try:
            kind = self.val("compress", "نوع_المحتوى")
            audio_only = kind.startswith("🎧")
            video_only = kind.startswith("🎬")
            self._show("compress", "وضع_الصوت", not video_only)
            self._show("compress", "وضع_الفيديو", not audio_only)
            groups = {"☁️ Saved Settings"}
            if not video_only:
                groups.add("🎧 Advanced Audio")
            if not audio_only:
                groups.add("🎬 Advanced Video")
            self._set_sections("compress", groups)
            mode = self.val("compress", "نوع_التحكم_في_الجودة")
            self._show("compress", "جودة_CRF", mode == "جودة CRF")
            self._show("compress", "Video_Bitrate_kbps", mode == "Bitrate يدوي")
            self._show("compress", "الحجم_المستهدف_MiB", mode == "حجم مستهدف")
            self.status["compress"].value = (
                '<div dir="rtl" style="font-size:12px;color:#475569">🎬 ' +
                escape(VIDEO_HINTS.get(self.val("compress", "وضع_الفيديو"), "")) +
                '<br>☁️ الإعدادات المحفوظة لا تغيّر حقول الفورم الظاهرة تلقائيًا.</div>'
            )
            self._show("drive_tg", "ارقام_الملفات_لإعادة_الرفع",
                       self.val("drive_tg", "التشغيل") == "إعادة رفع المحدد")
            deleting = self.val("tg_drive", "حذف_رسائل_Telegram_بعد_التحقق")
            recopy = self.val("tg_drive", "اعادة_نسخ_المنقول_سابقا")
            preview = self.val("tg_drive", "التشغيل") == "معاينة فقط"
            line = ("⚠️ لا يمكن جمع حذف الرسائل مع إعادة نسخ القديم." if deleting and recopy
                    else "👁️ المعاينة لا تحذف أي رسالة." if preview
                    else "⚠️ التنفيذ سيحذف رسائل Telegram نهائيًا بعد التحقق." if deleting
                    else "✅ رسائل Telegram الأصلية هتفضل موجودة.")
            self.status["tg_drive"].value = (
                '<div dir="rtl" style="color:#a16207;font-size:12px">' + escape(line) + '</div>'
            )
            fmt = self.val("web", "صيغة_الناتج")
            dest = self.val("web", "مكان_الحفظ")
            audio_fmt = any(x in fmt for x in ("صوت MP3", "صوت M4A", "صوت OGG"))
            self._show("web", "Bitrate_الصوت_kbps", audio_fmt)
            self._show("web", "OGG_بدون_إعادة_ترميز_إن_أمكن", "صوت OGG" in fmt)
            self._show("web", "جودة_تحويل_MP4_CRF", "فيديو MP4" in fmt)
            self._show("web", "عرض_Telegram", dest == "Telegram")
            self._show("web", "دخول_Telegram", dest == "Telegram")
            self._show("web", "مجلد_فرعي", dest == "Google Drive")
            relevant = {"🔧 Troubleshooting"}
            if audio_fmt:
                relevant.add("🎵 Advanced Audio")
            if "فيديو MP4" in fmt:
                relevant.add("🎞️ Advanced Video")
            if dest in {"Telegram", "Google Drive"}:
                relevant.add("📤 Destination Options")
            self._set_sections("web", relevant)
            src = self.val("gemini", "المصدر")
            is_telegram = src.startswith("📣")
            self._show("gemini", "روابط_المصدر", not is_telegram)
            self._show("gemini", "شامل_مجلدات_Drive_الفرعية", src == "📁 Google Drive Folder")
            self._set_sections("gemini", {"📁 Batch & Limits", "🎧 Advanced Audio",
                                           "♻️ Reprocessing"} |
                              ({"📣 Telegram Options"} if is_telegram else set()))
            self.status["gemini"].value = (
                '<div dir="rtl" style="font-size:12px;color:#64748b">'
                '👁️ المعاينة لا تضغط ولا ترفع ولا تحذف ملفات.</div>'
            )
        except Exception as exc:
            raise RuntimeError("تعذر تحديث Smart UI: " + str(exc)) from exc

    def snapshot(self, tab=None):
        """Take a synchronous snapshot; never read controls during a long task."""
        tab = TABS[self.tab.selected_index or 0] if tab is None else tab
        return tab, {key: control.value for key, control in self.controls[tab].items()}

    def display(self):
        from IPython.display import display
        display(self.main)
        return self.main

    def execute_selected(self):
        tab, v = self.snapshot()
        print("🎛️ " + LABELS[tab] + " | تجهيز التشغيل...")
        if tab == "tg_drive" and v["اعادة_نسخ_المنقول_سابقا"] and v["حذف_رسائل_Telegram_بعد_التحقق"]:
            raise ValueError("لا يمكن تفعيل إعادة نسخ القديم والحذف معًا.")
        if tab == "tg_drive" and v["التشغيل"] != "معاينة فقط" and v["حذف_رسائل_Telegram_بعد_التحقق"]:
            print("⚠️ تنبيه: حذف رسائل Telegram بعد النقل والتحقق مفعّل.")
        commit = self._sha()
        if tab == "compress":
            engine = self._component(commit, "engine.py", ui_only=True)
            form = self._component(commit, "studio_form_controls.py")
            values = {
                "content_kind": v["نوع_المحتوى"],
                "audio_preset": v["وضع_الصوت"], "video_preset": v["وضع_الفيديو"],
                "skip_if_no_saving": v["تخطي_الملف_بدون_توفير_مفيد"],
                "advanced_audio": v["تفعيل_Advanced_للصوت"],
                "audio_codec": v["صيغة_الصوت"], "audio_kbps": v["جودة_الصوت_kbps"],
                "audio_channels": v["قنوات_الصوت"], "audio_sample_rate": v["Sample_Rate_Hz"],
                "audio_noise": v["إزالة_الضوضاء"],
                "advanced_video": v["تفعيل_Advanced_للفيديو"],
                "video_codec": v["صيغة_الفيديو"], "video_resolution": v["دقة_الفيديو"],
                "video_fps": v["فريمات_الفيديو"],
                "video_rate_mode": v["نوع_التحكم_في_الجودة"],
                "video_crf": v["جودة_CRF"], "video_bitrate_kbps": v["Video_Bitrate_kbps"],
                "video_target_mb": v["الحجم_المستهدف_MiB"],
                "video_speed": v["سرعة_ترميز_الفيديو"],
                "video_audio_kbps": v["صوت_الفيديو_kbps"],
            }
            actions = {"▶ تشغيل الضغط": "run",
                       "💾 Save as Default — حفظ فقط": "save_default",
                       "👁️ معاينة الإعدادات فقط": "preview"}
            sources = {"📝 تطبيق حقول الفورم الظاهرة": "form",
                       "☁️ استرجاع آخر تشغيل (يتجاهل الحقول الظاهرة)": "last",
                       "⭐ استرجاع الافتراضي المثبّت (يتجاهل الحقول الظاهرة)": "default"}
            result = form.perform_form_action(engine, actions[v["العملية"]],
                                              sources[v["مصدر_الإعدادات"]], values)
        elif tab in {"drive_tg", "tg_drive"}:
            transfer = self._component(commit, "transfer_engine.py")
            if tab == "drive_tg":
                result = transfer.run_transfer(
                    direction="drive_to_telegram",
                    source=v["رابط_Drive"].strip(), destination="",
                    max_files=v["عدد_الملفات"], recursive=v["شامل_المجلدات_الفرعية"],
                    preview=v["التشغيل"] == "معاينة فقط",
                    login_method="qr" if v["طريقة_الدخول"] == "QR بدون كود" else "code",
                    reupload_selected=v["التشغيل"] == "إعادة رفع المحدد",
                    selected_numbers=v["ارقام_الملفات_لإعادة_الرفع"].strip(),
                    telegram_hashtag=v["هاشتاج_الملفات"].strip(),
                )
            else:
                result = transfer.run_transfer(
                    direction="telegram_to_drive",
                    source=v["قناة_Telegram_المصدر"].strip(),
                    destination=v["فولدر_Drive_للحفظ"].strip(),
                    max_files=v["عدد_الملفات"], recursive=True,
                    preview=v["التشغيل"] == "معاينة فقط",
                    login_method="qr" if v["طريقة_الدخول"] == "QR بدون كود" else "code",
                    drive_subfolder=v["اسم_الفولدر_الفرعي"].strip(),
                    delete_after_transfer=v["حذف_رسائل_Telegram_بعد_التحقق"],
                    recopy_previously_transferred=v["اعادة_نسخ_المنقول_سابقا"],
                )
        elif tab == "web":
            web = self._component(commit, "web_video_downloader.py")
            formats = {
                "🎬 فيديو MP4 (H.264/AAC — Telegram Preview)": "mp4",
                "🎞️ فيديو WebM (VP9/Opus)": "webm",
                "🎵 صوت MP3": "mp3", "🎵 صوت M4A (AAC)": "m4a",
                "🎵 صوت OGG (Opus)": "ogg", "📦 أرشيف ZIP للملف الأصلي": "zip",
                "📄 الملف الأصلي بدون تحويل": "original",
            }
            result = web.run(
                v["روابط_الفيديو"], destination=v["مكان_الحفظ"],
                subfolder=v["مجلد_فرعي"], quality=v["جودة_الفيديو"],
                browser_fallback=v["استخراج_متقدم_لروابط_Awesome"],
                update_ytdlp=v["تحديث_yt_dlp"],
                telegram_login="qr" if v["دخول_Telegram"] == "QR بدون كود" else "code",
                repo_commit=commit, output_format=formats[v["صيغة_الناتج"]],
                telegram_display=("player" if v["عرض_Telegram"].startswith("▶️") else "document"),
                audio_kbps=int(v["Bitrate_الصوت_kbps"]),
                video_crf=int(v["جودة_تحويل_MP4_CRF"]),
                fast_opus_copy=bool(v["OGG_بدون_إعادة_ترميز_إن_أمكن"]),
            )
        else:
            gemini = self._component(commit, "gemini_ready_engine.py")
            sources = {"📣 Telegram — قناة Media Lite": "telegram",
                       "📁 Google Drive Folder": "drive_folder",
                       "🔗 Google Drive Links": "drive_links",
                       "🌐 Internet URLs": "internet"}
            presets = {"محاضرات واضحة (24 kbps)": "speech",
                       "وضوح أعلى (32 kbps)": "clear",
                       "أصغر حجم (8 kbps)": "tiny",
                       "موسيقى / صوت غني (96 kbps)": "music"}
            result = gemini.run(
                source=sources[v["المصدر"]], links=v["روابط_المصدر"].strip(),
                action="preview" if v["العملية"] == "👁️ معاينة فقط" else "process",
                batch_name=v["اسم_الدفعة"].strip(), max_files=v["عدد_الملفات"],
                recursive=v["شامل_مجلدات_Drive_الفرعية"],
                tg_mode="new" if v["اختيار_ملفات_Telegram"] ==
                            "الملفات الجديدة غير المعالجة" else "recent",
                delete_telegram=v["حذف_رسائل_Telegram_بعد_التحقق"],
                login_method="qr" if v["طريقة_دخول_Telegram"] == "QR بدون كود" else "code",
                preset=presets[v["وضع_الصوت"]],
                advanced_audio=v["تفعيل_Advanced_Audio"],
                audio_codec={"Opus / OGG": "opus", "AAC / M4A": "aac",
                             "MP3": "mp3"}[v["صيغة_الصوت"]],
                audio_kbps=int(v["Audio_Bitrate_kbps"]),
                channels="mono" if v["قنوات_الصوت"] == "Mono" else "stereo",
                sample_rate=v["Audio_Sample_Rate_Hz"],
                noise="off" if v["إزالة_الضوضاء"] == "Off" else "light",
                max_part_mb=v["اقصى_حجم_للجزء_MB"],
                max_minutes=v["اقصى_مدة_للجزء_بالدقائق"],
                force_reprocess=v["إعادة_معالجة_المكتمل"], repo_commit=commit,
            )
        print("ℹ️ انتهى تشغيل الأداة؛ راجع ملخص النتائج وأي أخطاء أعلاه.")
        return result

    def _sha(self):
        if self._commit:
            return self._commit
        target = "refs/heads/main"
        try:
            out = subprocess.run(
                ["git", "ls-remote", "https://github.com/" + REPO + ".git", target],
                capture_output=True, text=True, timeout=15,
            )
            sha = out.stdout.strip().split()[0] if out.returncode == 0 and out.stdout.strip() else ""
            if not re.fullmatch(r"[a-f0-9]{40}", sha):
                raise ValueError("git ls-remote returned no SHA")
        except Exception:
            req = urllib.request.Request(
                "https://api.github.com/repos/" + REPO + "/git/ref/heads/main",
                headers={"User-Agent": "Media-Lite-Smart-Tabs", "Cache-Control": "no-cache"},
            )
            with urllib.request.urlopen(req, timeout=25) as reply:
                sha = json.load(reply)["object"]["sha"]
        if not re.fullmatch(r"[a-f0-9]{40}", sha):
            raise RuntimeError("تعذر التحقق من إصدار GitHub.")
        self._commit = sha
        print("📦 GitHub engine commit: " + sha[:10])
        return sha

    def _component(self, commit, filename, ui_only=False):
        """Cache components per commit; same commit for a whole selected run."""
        base = Path("/content/media_lite_smart_tabs")
        base.mkdir(parents=True, exist_ok=True)
        local = base / filename
        marker = base / (filename + ".commit")
        source = None
        if local.is_file() and marker.is_file():
            try:
                if marker.read_text(encoding="ascii").strip() == commit:
                    source = local.read_text(encoding="utf-8")
            except OSError:
                source = None
        if not source:
            url = "https://raw.githubusercontent.com/" + REPO + "/" + commit + "/" + REL + "/" + filename
            req = urllib.request.Request(url, headers={"User-Agent": "Media-Lite-Smart-Tabs"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                source = resp.read().decode("utf-8")
            if len(source) < 1000:
                raise RuntimeError("GitHub component incomplete: " + filename)
            temp = local.with_suffix(".tmp")
            temp.write_text(source, encoding="utf-8")
            os.replace(temp, local)
            marker.write_text(commit, encoding="ascii")
        name = "smart_tabs_" + filename.replace(".", "_")
        module = types.ModuleType(name)
        module.__file__ = str(local)
        sys.modules[name] = module
        before = os.environ.get("TSC_UI_ONLY")
        if ui_only:
            os.environ["TSC_UI_ONLY"] = "1"
        try:
            exec(compile(source, str(local), "exec"), module.__dict__)
        finally:
            if ui_only:
                if before is None:
                    os.environ.pop("TSC_UI_ONLY", None)
                else:
                    os.environ["TSC_UI_ONLY"] = before
        return module


def launch():
    """Build UI without mounting Drive, installing packages or connecting Telegram."""
    ui = SmartTabs()
    ui.display()
    return ui
