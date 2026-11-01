# -*- coding: utf-8 -*-
"""Media Lite Studio: responsive, bilingual Colab widgets, backed by Drive.

The module never uploads secrets and never runs the compressor until the
user explicitly clicks Run. It only stores validated encoding preferences.
"""
from __future__ import annotations

import asyncio
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
    selected["download_connections"] = "6"  # temporarily fixed until benchmarking
    # Each widget owns an independent Layout. Sharing a Layout here makes
    # hiding one control accidentally hide unrelated controls (notably mobile).
    def wide_layout():
        return w.Layout(width="100%", margin="0 0 8px 0")

    def button_layout():
        return w.Layout(width="100%", height="45px", margin="4px 0")

    def section_layout():
        return w.Layout(width="100%", padding="10px 12px",
                        border="1px solid #e2e8f0", margin="6px 0 12px 0")

    container = w.Layout(width="100%", max_width="840px",
                         padding="10px 6px", margin="0 auto")

    def title_html(title, subtitle):
        return w.HTML(
            f'<div dir="rtl" style="font-family:system-ui,Tahoma,sans-serif;padding:6px 2px 10px">'
            f'<div style="font-size:16px;font-weight:750;color:#0f172a">{title}</div>'
            f'<div style="font-size:12px;line-height:1.65;color:#64748b">{subtitle}</div></div>',
            layout=wide_layout(),
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
        '</div>', layout=wide_layout(),
    )

    status = w.HTML(layout=wide_layout())
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
        value=selected["source"], description="", layout=wide_layout(),
        style={"button_width": "120px"},
    )
    download_connections = w.Dropdown(
        options=[("6 اتصالات — مثبت حاليًا", "6")],
        value="6", disabled=True, description="تنزيل Telegram",
        layout=wide_layout(), style={"description_width": "115px"},
    )
    audio_presets = w.Dropdown(
        options=[
            ("🎙️ محاضرات — كلام واضح (موصى به)", "speech"),
            ("🔎 وضوح أعلى", "clear"),
            ("🪶 أصغر حجم ممكن", "tiny"),
            ("🎵 موسيقى / صوت غني", "music"),
        ],
        value=selected["audio_preset"], description="", layout=wide_layout(),
    )
    video_presets = w.Dropdown(
        options=[
            ("⚖️ متوازن — مناسب لمعظم الفيديوهات", "balanced"),
            ("📝 شرائح وشرح — كتابة أوضح", "presentation"),
            ("🪶 حجم صغير جدًا", "small"),
            ("⚡ أسرع معالجة", "fast"),
        ],
        value=selected["video_preset"], description="", layout=wide_layout(),
    )
    audio_codec = w.Dropdown(
        options=[("OGG · Opus — أصغر حجم وكلام واضح", "opus"),
                 ("M4A · AAC — توافق واسع", "aac"),
                 ("MP3 — تشغيل على أغلب الأجهزة", "mp3")],
        value=selected["audio_codec"], description="الصيغة", layout=wide_layout(),
        style={"description_width": "115px"},
    )
    audio_kbps = w.Dropdown(
        options=[(f"{x} kbps", x) for x in (8, 12, 16, 24, 32, 48, 64, 96, 128, 160, 192)],
        value=selected["audio_kbps"], description="جودة الصوت", layout=wide_layout(),
        style={"description_width": "115px"},
    )
    audio_channels = w.Dropdown(
        options=[("Mono — كلام", "mono"), ("Stereo — موسيقى", "stereo")],
        value=selected["audio_channels"], description="القنوات", layout=wide_layout(),
        style={"description_width": "115px"},
    )
    audio_sample_rate = w.Dropdown(
        options=[("تلقائي", "auto"), ("12 kHz", "12000"), ("16 kHz", "16000"),
                 ("24 kHz", "24000"), ("48 kHz", "48000")],
        value=selected["audio_sample_rate"], description="Sample Rate", layout=wide_layout(),
        style={"description_width": "115px"},
    )
    audio_noise = w.Dropdown(
        options=[("بدون تعديل — آمن", "off"), ("إزالة ضوضاء خفيفة", "light")],
        value=selected["audio_noise"], description="الضوضاء", layout=wide_layout(),
        style={"description_width": "115px"},
    )
    video_codec = w.Dropdown(
        options=[("H.264 — التوافق الأفضل", "h264"), ("H.265 — حجم أقل أحيانًا", "h265")],
        value=selected["video_codec"], description="Codec", layout=wide_layout(),
        style={"description_width": "115px"},
    )
    video_resolution = w.Dropdown(
        options=[("الحفاظ على المصدر", "source"), ("480p", "480"), ("540p", "540"),
                 ("720p", "720"), ("1080p", "1080")],
        value=selected["video_resolution"], description="الدقة", layout=wide_layout(),
        style={"description_width": "115px"},
    )
    video_fps = w.Dropdown(
        options=[("ذكي حسب الحركة", "auto"), ("الأصلية", "source"),
                 ("12 FPS", "12"), ("15 FPS", "15"), ("24 FPS", "24"),
                 ("30 FPS", "30")],
        value=selected["video_fps"], description="الفريمات", layout=wide_layout(),
        style={"description_width": "115px"},
    )
    video_crf = w.IntSlider(
        value=selected["video_crf"], min=18, max=35, step=1,
        description="جودة CRF", continuous_update=False,
        readout=True, layout=wide_layout(), style={"description_width": "115px"},
    )
    video_bitrate_kbps = w.BoundedIntText(
        value=selected["video_bitrate_kbps"], min=100, max=20000, step=100,
        description="Video kbps", layout=wide_layout(),
        style={"description_width": "115px"},
    )
    video_speed = w.Dropdown(
        options=[("سريع", "fast"), ("متوازن", "balanced"), ("أبطأ / ضغط أفضل", "slow")],
        value=selected["video_speed"], description="سرعة الترميز", layout=wide_layout(),
        style={"description_width": "115px"},
    )
    video_audio_kbps = w.Dropdown(
        options=[(f"{x} kbps", x) for x in (32, 40, 48, 64, 96, 128, 160, 192)],
        value=selected["video_audio_kbps"], description="صوت الفيديو",
        layout=wide_layout(), style={"description_width": "115px"},
    )
    video_rate_mode = w.ToggleButtons(
        options=[("🎚️ جودة CRF", "quality"), ("📊 Bitrate", "bitrate"), ("🎯 حجم محدد", "target")],
        value=selected["video_rate_mode"], description="", layout=wide_layout(),
        style={"button_width": "150px"},
    )
    video_target_mb = w.BoundedIntText(
        value=selected["video_target_mb"], min=4, max=4096, step=1,
        description="الحجم (MiB)", layout=wide_layout(),
        style={"description_width": "115px"},
    )
    skip_if_no_saving = w.Checkbox(
        value=selected["skip_if_no_saving"],
        description="تخطي الملف لو الناتج مش هيقل عن الأصل بشكل مفيد",
        indent=False, layout=wide_layout(),
    )

    audio_quick = w.VBox([
        title_html("إعداد صوت المحاضرات", "لو الملف صوتي، هتتطبق إعدادات الصوت المختارة."),
        audio_presets,
    ], layout=section_layout())
    video_quick = w.VBox([
        title_html("إعداد الفيديو", "للفيديو: متوازن، شروحات شاشة، أصغر حجم، أو أسرع معالجة."),
        video_presets,
    ], layout=section_layout())

    audio_advanced = w.VBox([
        title_html("ضبط الصوت", "Opus هو الأفضل للمحاضرات الصغيرة. MP3 وAAC للتوافق. "
                   "رفع الـBitrate قد يزيد الوضوح والحجم."),
        audio_codec, audio_kbps, audio_channels, audio_sample_rate, audio_noise,
    ], layout=w.Layout(width="100%", padding="7px"))
    video_advanced = w.VBox([
        title_html("ضبط الفيديو", "CRF الأقل يعطي جودة أعلى وحجمًا أكبر عادةً. "
                   "الحجم المستهدف يستخدم H.264 بمرحلتي ترميز."),
        video_codec, video_resolution, video_fps,
        video_rate_mode, video_crf, video_bitrate_kbps, video_target_mb,
        video_speed, video_audio_kbps,
    ], layout=w.Layout(width="100%", padding="7px"))

    network_advanced = w.VBox([
        title_html(
            "⬇️ تنزيل Telegram — سرعة الاتصال",
            "ثبّتنا تنزيل Telegram على 6 اتصالات للملفات من 8 MiB مؤقتًا. "
            "ده غير حجم الأجزاء (512 KiB). المقارنة بين 6/8/18 هتبقى في اختبار لاحق؛ "
            "خلايا نقل Drive ↔ Telegram ليها محرك مستقل."
        ),
        download_connections,
    ], layout=w.Layout(width="100%", padding="7px"))

    advanced = w.Accordion(children=(audio_advanced, video_advanced, network_advanced),
                           selected_index=None, layout=wide_layout())
    advanced.set_title(0, "🎧 إعدادات الصوت المتقدمة")
    advanced.set_title(1, "🎬 إعدادات الفيديو المتقدمة")
    advanced.set_title(2, "⚡ تنزيل تيليجرام — اتصالات MTProto")

    summary = w.HTML(layout=wide_layout())
    saving_hint = w.HTML(
        '<div dir="rtl" style="font-size:12px;color:#64748b;line-height:1.75">'
        '💾 <b>تشغيل:</b> يحفظ آخر إعدادات مستخدمة تلقائيًا في Google Drive. '
        '<b>Save as Default:</b> يثبت هذه الإعدادات كمرجع ترجع له وقت ما تحب. '
        '⏹ <b>إيقاف:</b> يطلب إيقاف المهمة الحالية بأمان من غير مسح الملفات الأصلية.'
        '</div>', layout=wide_layout(),
    )
    run_button = w.Button(
        description="▶ تشغيل الضغط وحفظ آخر إعدادات", button_style="primary",
        icon="play", layout=button_layout(), tooltip="ابدأ ضغط الملفات الموجودة في قناة تيليجرام",
    )
    stop_button = w.Button(
        description="⏹ إيقاف التشغيل بأمان", button_style="danger",
        icon="stop", layout=button_layout(),
        tooltip="أوقف التنزيل أو FFmpeg أو الرفع، مع الاحتفاظ بالأصول والنتائج المكتملة",
        disabled=True,
    )
    pin_button = w.Button(
        description="💾 Save as Default | ثبّت كافتراضي", button_style="success",
        icon="save", layout=button_layout(), tooltip="احفظ إعداداتك المفضلة على Google Drive",
    )
    restore_button = w.Button(
        description="↩ استرجاع الإعدادات الافتراضية المحفوظة", button_style="",
        icon="undo", layout=button_layout(), tooltip="يرجع الإعدادات فقط؛ لا يحذف الملفات ولا يبدأ الضغط",
    )
    run_output = w.Output(layout=w.Layout(width="100%", border="1px solid #e2e8f0",
                                          padding="8px", margin="8px 0 0"))

    changing = {"presets": False, "visibility": False}

    def collect():
        return {
            "source": source.value,
            "download_connections": download_connections.value,
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
            "video_bitrate_kbps": video_bitrate_kbps.value,
            "video_speed": video_speed.value,
            "video_audio_kbps": video_audio_kbps.value,
            "video_rate_mode": video_rate_mode.value,
            "video_target_mb": video_target_mb.value,
            "skip_if_no_saving": skip_if_no_saving.value,
        }

    controls = {
        "source": source, "download_connections": download_connections,
        "audio_preset": audio_presets, "audio_codec": audio_codec,
        "audio_kbps": audio_kbps, "audio_channels": audio_channels,
        "audio_sample_rate": audio_sample_rate, "audio_noise": audio_noise,
        "video_preset": video_presets, "video_codec": video_codec,
        "video_resolution": video_resolution, "video_fps": video_fps,
        "video_crf": video_crf, "video_bitrate_kbps": video_bitrate_kbps,
        "video_speed": video_speed,
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
                control.value = "6" if key == "download_connections" else values[key]
        finally:
            changing["presets"] = False
        refresh()

    def refresh(*_):
        # Do not show irrelevant controls; in Auto both profiles are active.
        value = source.value
        audio_quick.layout.display = "none" if value == "video" else ""
        video_quick.layout.display = "none" if value == "audio" else ""
        visible_advanced = (
            (audio_advanced, network_advanced) if value == "audio" else
            (video_advanced, network_advanced) if value == "video" else
            (audio_advanced, video_advanced, network_advanced)
        )
        if advanced.children != visible_advanced:
            advanced.children = visible_advanced
            advanced.selected_index = None
            names = {
                audio_advanced: "🎧 إعدادات الصوت المتقدمة",
                video_advanced: "🎬 إعدادات الفيديو المتقدمة",
                network_advanced: "⚡ تنزيل تيليجرام — اتصالات MTProto",
            }
            for i, child in enumerate(visible_advanced):
                advanced.set_title(i, names[child])
        rate_mode = video_rate_mode.value
        target = rate_mode == "target"
        video_target_mb.layout.display = "" if target else "none"
        video_bitrate_kbps.layout.display = "" if rate_mode == "bitrate" else "none"
        video_crf.layout.display = "" if rate_mode == "quality" else "none"
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
               f"Bitrate {active['video_bitrate_kbps']} kbps" if rate_mode == "bitrate" else
               f"CRF {active['video_crf']}")
        )
        if active["video_resolution"] == "source":
            video_line = video_line.replace("sourcep", "الدقة الأصلية")
        download_label = (
            "تلقائي حسب حجم الملف" if active["download_connections"] == "auto"
            else f'{active["download_connections"]} اتصالات'
        )
        summary.value = (
            '<div dir="rtl" style="font-family:system-ui,Tahoma,sans-serif;'
            'border:1px solid #cbd5e1;background:#f8fafc;border-radius:12px;'
            'padding:13px 15px;line-height:1.9;font-size:12px;color:#334155">'
            '<div style="font-weight:750;color:#0f172a;font-size:13px">معاينة الإعدادات</div>'
            f'نوع الملف: <b>{escape(media)}</b>'
            + (f'<div>🎧 {escape(audio_line)}</div>' if value != "video" else "")
            + (f'<div>🎬 {escape(video_line)}</div>' if value != "audio" else "")
            + f'<div>⬇️ Telegram: {escape(download_label)}</div>'
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

    running = {"task": None}

    def stop_clicked(_):
        task = running["task"]
        if stop_button.disabled or task is None or task.done():
            return
        engine.request_stop()
        stop_button.disabled = True
        stop_button.description = "⏳ جاري الإيقاف والتنظيف..."
        display_status(
            "⏹ طلب الإيقاف اتسجل. جاري إيقاف التنزيل/الضغط بأمان "
            "وإغلاق أي عملية نشطة. النتائج المكتملة محفوظة.", False,
        )

    def run_clicked(_):
        if run_button.disabled or (
            running["task"] is not None and not running["task"].done()
        ):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            display_status(
                "Colab لا يوفّر Event Loop نشطًا لأزرار التشغيل. "
                "افتح النوتبوك على Colab وأعد تشغيل الخلية.", False
            )
            return

        try:
            options = engine.normalize_studio_settings(collect())
            options["download_connections"] = "6"
            ensure_drive()
            engine.save_studio_preferences(options)
        except Exception as exc:
            display_status("تعذر بدء التشغيل أو حفظ الإعدادات: " + str(exc), False)
            return

        engine.reset_stop()
        engine.apply_studio_settings(options)
        display_status("✓ اتحفظت آخر إعداداتك على Drive. التشغيل بدأ؛ زر الإيقاف متاح.")
        run_button.disabled = True
        stop_button.disabled = False
        stop_button.description = "⏹ إيقاف التشغيل بأمان"
        pin_button.disabled = True
        restore_button.disabled = True

        async def execute():
            try:
                with run_output:
                    clear_output(wait=True)
                    print("🗜️ Media Lite Studio — تنزيل Telegram بست اتصالات؛ إيقاف آمن متاح.")
                    result = await engine.run_async()
                if result.get("stopped"):
                    display_status(
                        "⏹ تم إيقاف التشغيل. الملفات الأصلية محفوظة، "
                        "والملفات التي لم تكتمل ستُعاد محاولتها في التشغيل القادم.",
                    )
                elif result.get("error"):
                    display_status(
                        f"⚠️ انتهى التشغيل بخطأ {result['error']}. راجع سجل التشغيل.",
                        False,
                    )
                else:
                    display_status("✓ انتهى التشغيل. راجع نتائج الملفات في سجل التشغيل.")
            except Exception as exc:
                display_status(
                    "تعذر إكمال التشغيل: " + str(exc), False,
                )
            finally:
                running["task"] = None
                run_button.disabled = False
                stop_button.disabled = True
                stop_button.description = "⏹ إيقاف التشغيل بأمان"
                pin_button.disabled = False
                restore_button.disabled = False

        # Schedule instead of calling engine.run() synchronously. This keeps
        # the widget event loop responsive to the Stop button during uploads.
        running["task"] = loop.create_task(execute())

    run_button.on_click(run_clicked)
    stop_button.on_click(stop_clicked)
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
        summary, saving_hint, run_button, stop_button, pin_button, restore_button,
        title_html("③ سجل التشغيل", "هتظهر هنا أوقات التحميل والضغط والرفع وأي ملاحظات أثناء المعالجة."),
        run_output,
        w.HTML('<div dir="rtl" style="font-size:11px;color:#64748b;margin:8px 0">'
               'ملف الإعدادات في MyDrive/Telegram_Extreme_Compressor/Media_Lite_Studio/preferences_v1.json. '
               'لا تُحفظ كلمات المرور أو جلسات Telegram داخل ملف الإعدادات.</div>'),
    ], layout=container)
    display(dashboard)
    return dashboard
