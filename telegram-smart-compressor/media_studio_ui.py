# -*- coding: utf-8 -*-
"""Media Lite Studio: responsive, bilingual Colab widgets, backed by Drive.

The module never uploads secrets and never runs the compressor until the
user explicitly clicks Run. It only stores validated encoding preferences.
"""
from __future__ import annotations

from datetime import datetime
from html import escape


def show_studio(engine):
    try:
        import ipywidgets as w
        from IPython.display import clear_output, display
    except ImportError as exc:
        raise RuntimeError("الواجهة تحتاج ipywidgets داخل Google Colab.") from exc

    # Built-in Colab core widgets work normally; enable if the runtime supports
    # this manager. The fallback is the original #@param notebook form.
    try:
        from google.colab import output
        output.enable_custom_widget_manager()
    except (ImportError, AttributeError):
        pass

    drive_ready = True
    mount_error = ""
    try:
        engine.mount_drive()
        record = engine.load_studio_preferences()
    except Exception as exc:
        drive_ready = False
        mount_error = str(getattr(exc, "message", exc))
        record = {
            "default": dict(engine.STUDIO_FACTORY_SETTINGS),
            "last_used": dict(engine.STUDIO_FACTORY_SETTINGS),
            "last_saved_at": None,
            "default_saved_at": None,
        }

    selected = engine.normalize_studio_settings(record["last_used"])
    WIDE = w.Layout(width="100%", margin="0 0 8px 0")
    BUTTON = w.Layout(width="100%", height="45px", margin="4px 0")
    container = w.Layout(width="100%", max_width="840px", padding="10px 6px", margin="0 auto")
    separate = w.Layout(width="100%", padding="10px 12px", border="1px solid #e2e8f0",
                        margin="6px 0 12px 0")

    def title_html(title, subtitle):
        return w.HTML(
            f'<div dir="rtl" style="font-family:system-ui,Tahoma,sans-serif;padding:6px 2px 10px">'
            f'<div style="font-size:16px;font-weight:750;color:#0f172a">{title}</div>'
            f'<div style="font-size:12px;line-height:1.65;color:#64748b">{subtitle}</div></div>',
            layout=WIDE,
        )

    header = w.HTML(
        '<div dir="rtl" style="font-family:system-ui,Tahoma,sans-serif;'
        'background:linear-gradient(110deg,#0b1735,#16408a 70%,#265bab);'
        'padding:20px 22px;border-radius:16px;color:white">'
        '<div style="font-size:11px;letter-spacing:.6px;opacity:.75">'
        'MEDIA LITE · STUDIO 1.0</div>'
        '<div style="font-size:25px;font-weight:800;margin:5px 0">🗜️ اضغطها</div>'
        '<div style="font-size:13px;opacity:.9;line-height:1.7">'
        'اضغط محاضراتك وفيديوهاتك بسهولة — أو افتح الإعدادات المتقدمة للتحكم الكامل.</div>'
        '</div>', layout=WIDE,
    )

    status = w.HTML(layout=WIDE)
    if drive_ready:
        status.value = (
            '<div dir="rtl" style="font-size:12px;color:#166534;padding:4px 1px">'
            '✓ Google Drive متصل · تم تحميل آخر إعداداتك المحفوظة'
            '</div>'
            if record.get("last_saved_at") else
            '<div dir="rtl" style="font-size:12px;color:#475569;padding:4px 1px">'
            '☁ Google Drive متصل · أول تشغيل بالإعدادات المقترحة'
            '</div>'
        )
    else:
        status.value = (
            '<div dir="rtl" style="font-size:12px;color:#b45309;padding:6px 1px">'
            '⚠ لم يتم توصيل Google Drive: ' + escape(mount_error) +
            ' · جرّب إعادة تشغيل الخلية بعد تفويض Drive.</div>'
        )

    source = w.ToggleButtons(
        options=[("✨ تلقائي", "auto"), ("🎧 صوت فقط", "audio"), ("🎬 فيديو", "video")],
        value=selected["source"], description="", layout=WIDE,
        style={"button_width": "120px"},
    )
    audio_presets = w.Dropdown(
        options=[
            ("🎙️ محاضرات — كلام واضح (موصى به)", "speech"),
            ("🔎 وضوح أعلى", "clear"),
            ("🪶 أصغر حجم ممكن", "tiny"),
            ("🎵 موسيقى / صوت غني", "music"),
        ],
        value=selected["audio_preset"], description="", layout=WIDE,
    )
    video_presets = w.Dropdown(
        options=[
            ("⚖️ متوازن — مناسب لمعظم الفيديوهات", "balanced"),
            ("📝 شرائح وشرح — كتابة أوضح", "presentation"),
            ("🪶 حجم صغير جدًا", "small"),
            ("⚡ أسرع معالجة", "fast"),
        ],
        value=selected["video_preset"], description="", layout=WIDE,
    )
    audio_codec = w.Dropdown(
        options=[("OGG · Opus — أصغر حجم وكلام واضح", "opus"),
                 ("M4A · AAC — توافق واسع", "aac"),
                 ("MP3 — تشغيل على أغلب الأجهزة", "mp3")],
        value=selected["audio_codec"], description="الصيغة", layout=WIDE,
        style={"description_width": "115px"},
    )
    audio_kbps = w.Dropdown(
        options=[(f"{x} kbps", x) for x in (8, 12, 16, 24, 32, 48, 64, 96, 128, 160, 192)],
        value=selected["audio_kbps"], description="جودة الصوت", layout=WIDE,
        style={"description_width": "115px"},
    )
    audio_channels = w.Dropdown(
        options=[("Mono — كلام", "mono"), ("Stereo — موسيقى", "stereo")],
        value=selected["audio_channels"], description="القنوات", layout=WIDE,
        style={"description_width": "115px"},
    )
    audio_sample_rate = w.Dropdown(
        options=[("تلقائي", "auto"), ("12 kHz", "12000"), ("16 kHz", "16000"),
                 ("24 kHz", "24000"), ("48 kHz", "48000")],
        value=selected["audio_sample_rate"], description="Sample Rate", layout=WIDE,
        style={"description_width": "115px"},
    )
    audio_noise = w.Dropdown(
        options=[("بدون تعديل — آمن", "off"), ("إزالة ضوضاء خفيفة", "light")],
        value=selected["audio_noise"], description="الضوضاء", layout=WIDE,
        style={"description_width": "115px"},
    )
    video_codec = w.Dropdown(
        options=[("H.264 — التوافق الأفضل", "h264"), ("H.265 — حجم أقل أحيانًا", "h265")],
        value=selected["video_codec"], description="Codec", layout=WIDE,
        style={"description_width": "115px"},
    )
    video_resolution = w.Dropdown(
        options=[("الحفاظ على المصدر", "source"), ("480p", "480"), ("540p", "540"),
                 ("720p", "720"), ("1080p", "1080")],
        value=selected["video_resolution"], description="الدقة", layout=WIDE,
        style={"description_width": "115px"},
    )
    video_fps = w.Dropdown(
        options=[("ذكي حسب الحركة", "auto"), ("الأصلية", "source"),
                 ("12 FPS", "12"), ("15 FPS", "15"), ("24 FPS", "24"),
                 ("30 FPS", "30")],
        value=selected["video_fps"], description="الفريمات", layout=WIDE,
        style={"description_width": "115px"},
    )
    video_crf = w.IntSlider(
        value=selected["video_crf"], min=18, max=35, step=1,
        description="جودة CRF", continuous_update=False,
        readout=True, layout=WIDE, style={"description_width": "115px"},
    )
    video_speed = w.Dropdown(
        options=[("سريع", "fast"), ("متوازن", "balanced"), ("أبطأ / ضغط أفضل", "slow")],
        value=selected["video_speed"], description="سرعة الترميز", layout=WIDE,
        style={"description_width": "115px"},
    )
    video_audio_kbps = w.Dropdown(
        options=[(f"{x} kbps", x) for x in (32, 40, 48, 64, 96, 128, 160, 192)],
        value=selected["video_audio_kbps"], description="صوت الفيديو",
        layout=WIDE, style={"description_width": "115px"},
    )
    video_rate_mode = w.ToggleButtons(
        options=[("🎚️ جودة ثابتة", "quality"), ("🎯 حجم محدد", "target")],
        value=selected["video_rate_mode"], description="", layout=WIDE,
        style={"button_width": "150px"},
    )
    video_target_mb = w.BoundedIntText(
        value=selected["video_target_mb"], min=4, max=4096, step=1,
        description="الحجم (MiB)", layout=WIDE,
        style={"description_width": "115px"},
    )
    skip_if_no_saving = w.Checkbox(
        value=selected["skip_if_no_saving"],
        description="تخطي الملف لو الناتج مش هيقل عن الأصل بشكل مفيد",
        indent=False, layout=WIDE,
    )

    audio_quick = w.VBox([
        title_html("إعداد صوت المحاضرات", "لو الملف صوتي، هتتطبق إعدادات الصوت المختارة."),
        audio_presets,
    ], layout=separate)
    video_quick = w.VBox([
        title_html("إعداد الفيديو", "للفيديو: متوازن، شروحات شاشة، أصغر حجم، أو أسرع معالجة."),
        video_presets,
    ], layout=separate)

    audio_advanced = w.VBox([
        title_html("ضبط الصوت", "Opus هو الأفضل للمحاضرات الصغيرة. MP3 وAAC للتوافق. "
                   "رفع الـBitrate قد يزيد الوضوح والحجم."),
        audio_codec, audio_kbps, audio_channels, audio_sample_rate, audio_noise,
    ], layout=w.Layout(width="100%", padding="7px"))
    video_advanced = w.VBox([
        title_html("ضبط الفيديو", "CRF الأقل يعطي جودة أعلى وحجمًا أكبر عادةً. "
                   "الحجم المستهدف يستخدم H.264 بمرحلتي ترميز."),
        video_codec, video_resolution, video_fps,
        video_rate_mode, video_crf, video_target_mb,
        video_speed, video_audio_kbps,
    ], layout=w.Layout(width="100%", padding="7px"))

    advanced = w.Accordion(children=(audio_advanced, video_advanced),
                           selected_index=None, layout=WIDE)
    advanced.set_title(0, "🎧 إعدادات الصوت المتقدمة")
    advanced.set_title(1, "🎬 إعدادات الفيديو المتقدمة")

    summary = w.HTML(layout=WIDE)
    saving_hint = w.HTML(
        '<div dir="rtl" style="font-size:12px;color:#64748b;line-height:1.75">'
        '💾 <b>تشغيل:</b> يحفظ آخر إعدادات مستخدمة تلقائيًا في Google Drive. '
        '<b>Save as Default:</b> يثبت هذه الإعدادات كمرجع ترجع له وقت ما تحب.'
        '</div>', layout=WIDE,
    )
    run_button = w.Button(
        description="▶ تشغيل الضغط وحفظ آخر إعدادات", button_style="primary",
        icon="play", layout=BUTTON, tooltip="ابدأ ضغط الملفات الموجودة في قناة تيليجرام",
    )
    pin_button = w.Button(
        description="💾 Save as Default | ثبّت كافتراضي", button_style="success",
        icon="save", layout=BUTTON, tooltip="احفظ إعداداتك المفضلة على Google Drive",
    )
    restore_button = w.Button(
        description="↩ استرجاع الإعدادات الافتراضية المحفوظة", button_style="",
        icon="undo", layout=BUTTON, tooltip="يرجع الإعدادات فقط؛ لا يحذف الملفات ولا يبدأ الضغط",
    )
    run_output = w.Output(layout=w.Layout(width="100%", border="1px solid #e2e8f0",
                                          padding="8px", margin="8px 0 0"))

    changing = {"presets": False, "visibility": False}

    def collect():
        return {
            "source": source.value,
            "audio_preset": audio_presets.value,
            "audio_codec": audio_codec.value,
            "audio_kbps": audio_kbps.value,
            "audio_channels": audio_channels.value,
            "audio_sample_rate": audio_sample_rate.value,
            "audio_noise": audio_noise.value,
            "video_preset": video_presets.value,
            "video_codec": video_codec.value,
            "video_resolution": video_resolution.value,
            "video_fps": video_fps.value,
            "video_crf": video_crf.value,
            "video_speed": video_speed.value,
            "video_audio_kbps": video_audio_kbps.value,
            "video_rate_mode": video_rate_mode.value,
            "video_target_mb": video_target_mb.value,
            "skip_if_no_saving": skip_if_no_saving.value,
        }

    controls = {
        "source": source, "audio_preset": audio_presets, "audio_codec": audio_codec,
        "audio_kbps": audio_kbps, "audio_channels": audio_channels,
        "audio_sample_rate": audio_sample_rate, "audio_noise": audio_noise,
        "video_preset": video_presets, "video_codec": video_codec,
        "video_resolution": video_resolution, "video_fps": video_fps,
        "video_crf": video_crf, "video_speed": video_speed,
        "video_audio_kbps": video_audio_kbps, "video_rate_mode": video_rate_mode,
        "video_target_mb": video_target_mb, "skip_if_no_saving": skip_if_no_saving,
    }

    def display_status(message, good=True):
        status.value = (
            '<div dir="rtl" style="font-size:12px;'
            f'color:{"#166534" if good else "#b45309"};padding:6px 1px;line-height:1.6">'
            + escape(str(message)) + '</div>'
        )

    def apply_values(values):
        changing["presets"] = True
        try:
            for key, control in controls.items():
                control.value = values[key]
        finally:
            changing["presets"] = False
        refresh()

    def refresh(*_):
        # Do not show irrelevant controls; in Auto both profiles are active.
        value = source.value
        audio_quick.layout.display = "none" if value == "video" else ""
        video_quick.layout.display = "none" if value == "audio" else ""
        visible_advanced = (
            (audio_advanced,) if value == "audio" else
            (video_advanced,) if value == "video" else
            (audio_advanced, video_advanced)
        )
        if advanced.children != visible_advanced:
            advanced.children = visible_advanced
            advanced.selected_index = None
            for i, child in enumerate(visible_advanced):
                advanced.set_title(i, "🎧 إعدادات الصوت المتقدمة" if child is audio_advanced
                                   else "🎬 إعدادات الفيديو المتقدمة")
        target = video_rate_mode.value == "target"
        video_target_mb.layout.display = "" if target else "none"
        video_crf.layout.display = "none" if target else ""
        video_codec.disabled = target
        if target and video_codec.value != "h264":
            video_codec.value = "h264"
        if audio_codec.value == "mp3" and audio_kbps.value < 32:
            audio_kbps.value = 32

        active = collect()
        media = "صوت وفيديو" if value == "auto" else ("صوت" if value == "audio" else "فيديو")
        audio_line = (f"{active['audio_codec'].upper()} · {active['audio_kbps']} kbps · "
                      f"{'Mono' if active['audio_channels']=='mono' else 'Stereo'}")
        video_line = (
            f"{active['video_codec'].upper()} · {active['video_resolution']}p · "
            + (f"حجم {active['video_target_mb']} MiB" if target else
               f"CRF {active['video_crf']}")
        )
        if active["video_resolution"] == "source":
            video_line = video_line.replace("sourcep", "الدقة الأصلية")
        summary.value = (
            '<div dir="rtl" style="font-family:system-ui,Tahoma,sans-serif;'
            'border:1px solid #cbd5e1;background:#f8fafc;border-radius:12px;'
            'padding:13px 15px;line-height:1.9;font-size:12px;color:#334155">'
            '<div style="font-weight:750;color:#0f172a;font-size:13px">معاينة الإعدادات</div>'
            f'نوع الملف: <b>{escape(media)}</b>'
            + (f'<div>🎧 {escape(audio_line)}</div>' if value != "video" else "")
            + (f'<div>🎬 {escape(video_line)}</div>' if value != "audio" else "")
            + '<div style="color:#64748b">حجم الناتج الفعلي يعتمد على مدة وجودة الملف الأصلي.</div>'
            '</div>'
        )

    def preset_changed(group, new):
        if changing["presets"]:
            return
        updates = (
            engine.STUDIO_AUDIO_PRESETS[new] if group == "audio"
            else engine.STUDIO_VIDEO_PRESETS[new]
        )
        changing["presets"] = True
        try:
            for key, value in updates.items():
                controls[key].value = value
        finally:
            changing["presets"] = False
        refresh()

    audio_presets.observe(lambda change: preset_changed("audio", change["new"]), names="value")
    video_presets.observe(lambda change: preset_changed("video", change["new"]), names="value")
    for key, control in controls.items():
        control.observe(refresh, names="value")
    refresh()

    def ensure_drive():
        engine.mount_drive()

    def save_as_default(_):
        try:
            values = engine.normalize_studio_settings(collect())
            ensure_drive()
            engine.save_studio_preferences(values, save_default=True)
            display_status("✓ تم تثبيت إعداداتك كافتراضي في Google Drive — تقدر تسترجعها في أي وقت.")
        except Exception as exc:
            display_status("تعذر حفظ الافتراضي: " + str(exc), False)

    def restore_default(_):
        try:
            ensure_drive()
            snapshot = engine.load_studio_preferences()["default"]
            apply_values(snapshot)
            display_status("↩ رجّعنا الإعدادات الافتراضية المحفوظة. اضغط تشغيل أو Save as Default لو عايز تحفظ أي تعديل.")
        except Exception as exc:
            display_status("تعذر استرجاع الإعدادات: " + str(exc), False)

    def run_clicked(_):
        if run_button.disabled:
            return
        try:
            options = engine.normalize_studio_settings(collect())
            ensure_drive()
            engine.save_studio_preferences(options)
        except Exception as exc:
            display_status("تعذر بدء التشغيل أو حفظ الإعدادات: " + str(exc), False)
            return

        engine.apply_studio_settings(options)
        display_status("✓ اتسجلت آخر إعدادات تشغيل تلقائيًا في Google Drive. جاري المعالجة...")
        run_button.disabled = True
        pin_button.disabled = True
        restore_button.disabled = True
        try:
            with run_output:
                clear_output(wait=True)
                print("🗜️ Media Lite Studio — الإعدادات الحالية محفوظة على Drive.")
                engine.run()
        except Exception as exc:
            display_status("حدث خطأ أثناء التشغيل: " + str(exc), False)
        finally:
            run_button.disabled = False
            pin_button.disabled = False
            restore_button.disabled = False

    run_button.on_click(run_clicked)
    pin_button.on_click(save_as_default)
    restore_button.on_click(restore_default)

    dashboard = w.VBox([
        header, status,
        title_html("① نوع الملف", "تلقائي يميّز الصوت والفيديو في قناة Telegram ويختار الإعدادات المناسبة."),
        source,
        audio_quick, video_quick,
        title_html("② اختياري: Advanced Settings",
                   "اضغط على القسم الذي تريد تعديله. لو مش محتاج تفاصيل تقنية، سيب الإعدادات كما هي."),
        advanced,
        skip_if_no_saving,
        summary, saving_hint, run_button, pin_button, restore_button,
        title_html("③ سجل التشغيل", "هتظهر هنا أوقات التحميل والضغط والرفع وأي ملاحظات أثناء المعالجة."),
        run_output,
        w.HTML('<div dir="rtl" style="font-size:11px;color:#64748b;margin:8px 0">'
               'ملف الإعدادات في MyDrive/Telegram_Extreme_Compressor/Media_Lite_Studio/preferences_v1.json. '
               'لا تُحفظ كلمات المرور أو جلسات Telegram داخل ملف الإعدادات.</div>'),
    ], layout=container)
    display(dashboard)
    return dashboard
