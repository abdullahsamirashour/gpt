# -*- coding: utf-8 -*-
"""Backend for the first cell's *native Colab #@param form*.

No ipywidgets or custom JavaScript are imported. The notebook passes form
values explicitly; this module validates, loads/saves Drive preferences and
dispatches the requested action. All media encoding remains in engine.py.
"""
from __future__ import annotations

import json
import time


FORM_SOURCE = ("last", "form", "default")
FORM_ACTION = ("run", "save_default", "preview")

AUDIO_PRESETS = {
    "محاضرات واضحة (24 kbps)": "speech",
    "وضوح أعلى (32 kbps)": "clear",
    "أصغر حجم (8 kbps)": "tiny",
    "موسيقى / صوت غني (96 kbps)": "music",
}
VIDEO_PRESETS = {
    "متوازن": "balanced",
    "شرح وكتابة أوضح": "presentation",
    "أصغر حجم": "small",
    "أسرع معالجة": "fast",
}
# Display labels explain the *output*, not which input media are accepted.
CONTENT_KINDS = {
    "تلقائي": "auto",  # Compatibility with older saved notebooks.
    "صوت فقط": "audio",
    "فيديو فقط": "video",
    "تلقائي — حسب الملف": "auto",
    "🎧 صوت فقط — حتى الفيديو يتحول لصوت": "audio",
    "🎬 فيديو — احتفظ بالصورة": "video",
}
AUDIO_CODECS = {"Opus / OGG": "opus", "AAC / M4A": "aac", "MP3": "mp3"}
VIDEO_CODECS = {"H.264 (الأكثر توافقًا)": "h264", "H.265 (HEVC)": "h265"}
RATE_MODES = {"جودة CRF": "quality", "Bitrate يدوي": "bitrate", "حجم مستهدف": "target"}
ENCODE_SPEED = {"سريع": "fast", "متوازن": "balanced", "أبطأ / ملف أصغر غالبًا": "slow"}


def _option(mapping, value, field):
    try:
        return mapping[value]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"اختيار غير معروف في خانة {field}: {value!r}") from exc


def collect_form_settings(engine, values):
    """Build validated Studio settings from the visible Colab fields.

    A false Advanced switch means *all* advanced values in that section are
    ignored: the selected quick preset wins. This prevents hidden or stale
    dropdown values silently overriding quick presets.
    """
    base = dict(engine.STUDIO_FACTORY_SETTINGS)
    base["source"] = _option(CONTENT_KINDS, values["content_kind"], "نوع المحتوى")
    audio_preset = _option(AUDIO_PRESETS, values["audio_preset"], "وضع الصوت")
    video_preset = _option(VIDEO_PRESETS, values["video_preset"], "وضع الفيديو")
    base["audio_preset"] = audio_preset
    base["video_preset"] = video_preset
    base.update(engine.STUDIO_AUDIO_PRESETS[audio_preset])
    base.update(engine.STUDIO_VIDEO_PRESETS[video_preset])
    base["skip_if_no_saving"] = values["skip_if_no_saving"]

    if values["advanced_audio"]:
        base.update({
            "audio_codec": _option(AUDIO_CODECS, values["audio_codec"], "صيغة الصوت"),
            "audio_kbps": int(values["audio_kbps"]),
            "audio_channels": str(values["audio_channels"]).lower(),
            "audio_sample_rate": values["audio_sample_rate"],
            "audio_noise": str(values["audio_noise"]).lower(),
        })
    if values["advanced_video"]:
        base.update({
            "video_codec": _option(VIDEO_CODECS, values["video_codec"], "صيغة الفيديو"),
            "video_resolution": values["video_resolution"],
            "video_fps": values["video_fps"],
            "video_rate_mode": _option(RATE_MODES, values["video_rate_mode"], "حجم الفيديو"),
            "video_crf": int(values["video_crf"]),
            "video_bitrate_kbps": int(values["video_bitrate_kbps"]),
            "video_target_mb": int(values["video_target_mb"]),
            "video_speed": _option(ENCODE_SPEED, values["video_speed"], "سرعة الضغط"),
            "video_audio_kbps": int(values["video_audio_kbps"]),
        })
    # Studio downloads are intentionally fixed at six until user benchmarks.
    base["download_connections"] = "6"
    return engine.normalize_studio_settings(base)


def pick_studio_settings(engine, source, values):
    """Return settings and their origin; never imply form fields are applied
    when the user selected saved Drive settings instead."""
    if source not in FORM_SOURCE:
        raise ValueError("مصدر الإعدادات غير معروف.")
    if source == "form":
        return collect_form_settings(engine, values), "حقول الفورم"

    record = engine.load_studio_preferences()
    exists = engine.studio_settings_path().exists()
    if source == "last":
        raw = record["last_used"]
        origin = "آخر إعدادات تشغيل على Drive" if exists and record.get("last_saved_at") else "الإعدادات الأولية — لا توجد إعدادات تشغيل محفوظة بعد"
    else:
        raw = record["default"]
        origin = "الإعدادات المثبتة على Drive" if exists and record.get("default_saved_at") else "الإعدادات الأولية — لم يتم تثبيت Default بعد"
    chosen = dict(raw)
    chosen["download_connections"] = "6"
    return engine.normalize_studio_settings(chosen), origin


def show_settings(settings, origin):
    """Compact text summary that works in Colab mobile and classic output."""
    print(f"📋 مصدر الإعدادات: {origin}")
    output_labels = {
        "auto": "تلقائي: الفيديو يفضل فيديو والصوت يفضل صوت",
        "audio": "🎧 صوت فقط: أي فيديو يتحول لملف صوت من غير صورة",
        "video": "🎬 فيديو: الحفاظ على الصورة في الملفات المرئية",
    }
    print(f"🎯 الناتج: {output_labels[settings['source']]} | Telegram: 6 اتصالات تنزيل")
    audio_kbps = settings["audio_kbps"]
    print(f"🎧 صوت: {settings['audio_codec'].upper()} | {audio_kbps} kbps | {settings['audio_channels']}")
    print(f"📦 حجم ساعة تسجيل متوقع: حوالي {audio_kbps * 0.45:.1f} MB للصوت (تقريبي؛ يختلف مع VBR).")
    if settings["source"] == "audio":
        print("🎞️ → 🎧 الفيديوهات هتخرج ملفات صوت فقط؛ لا ترميز فيديو ولا H.264/CRF.")
        print("🎬 إعدادات الفيديو مش هتتطبق في الوضع ده.")
    else:
        video_mode = settings["video_rate_mode"]
        video_quality = (
            f"CRF {settings['video_crf']}" if video_mode == "quality" else
            f"{settings['video_bitrate_kbps']} kbps" if video_mode == "bitrate" else
            f"{settings['video_target_mb']} MiB مستهدف"
        )
        resolution = ("الأصلية" if settings["video_resolution"] == "source"
                      else settings["video_resolution"] + "p")
        print(
            f"🎬 فيديو: {settings['video_codec'].upper()} | "
            f"{resolution} | {settings['video_fps']} FPS | {video_quality}"
        )
    print("💾 الإعدادات المحفوظة تخص الضغط فقط؛ لا تحتوي جلسات Telegram أو كلمات المرور.")


def perform_form_action(engine, action, source, values):
    """Run/Save Default/Preview. Native Colab's Interrupt button stops runs."""
    if action not in FORM_ACTION:
        raise ValueError("الأمر المطلوب غير معروف.")
    drive_started = time.perf_counter()
    engine.mount_drive()
    print(f"⏱️ توصيل Google Drive: {time.perf_counter() - drive_started:.2f} ثانية")
    settings_started = time.perf_counter()
    settings, origin = pick_studio_settings(engine, source, values)
    print(f"⏱️ قراءة الإعدادات: {time.perf_counter() - settings_started:.2f} ثانية")
    show_settings(settings, origin)
    if source != "form":
        print("ℹ️ وضع استرجاع Drive: إعدادات الصوت والفيديو الظاهرة في الفورم مش بتتطبق.")
        # A stale Colab tab can still show the old 'Last used' default.
        # Never allow an explicit audio-only choice to start H.264/CRF video
        # work silently just because the source selector was not switched.
        requested_kind = _option(CONTENT_KINDS, values["content_kind"], "نوع المحتوى")
        if action in {"run", "save_default"} and requested_kind != "auto" and requested_kind != settings["source"]:
            raise ValueError(
                "نوع المحتوى الظاهر في الفورم مختلف عن المحفوظ على Drive. "
                "لن نبدأ ضغط فيديو بدل صوت بالغلط. "
                "اختار «📝 تطبيق حقول الفورم الظاهرة» من «مصدر الإعدادات»، "
                "أو غيّر نوع المحتوى الظاهر ليتوافق مع المحفوظ."
            )
    if action == "preview":
        print("👁️ معاينة فقط: لا ضغط، ولا تغيير في تفضيلات Drive.")
        return {"action": action, "settings": settings}
    if action == "save_default":
        save_started = time.perf_counter()
        engine.save_studio_preferences(settings, save_default=True)
        print(f"⏱️ إعدادات Drive: {time.perf_counter() - save_started:.2f} ثانية")
        print("✅ Save as Default: الإعدادات المفضلة جاهزة على Google Drive.")
        print("↩️ لاسترجاعها بعد كده اختار «الإعدادات المثبتة ⭐» من مصدر الإعدادات.")
        return {"action": action, "settings": settings}

    save_started = time.perf_counter()
    engine.save_studio_preferences(settings)
    engine.apply_studio_settings(settings)
    print(f"⏱️ حفظ الإعدادات: {time.perf_counter() - save_started:.2f} ثانية")
    print("✅ آخر إعدادات التشغيل جاهزة على Google Drive؛ مفيش إعادة كتابة لو لم تتغير.")
    print("⏹️ للإيقاف: اضغط مربع إيقاف الخلية في Colab أو Runtime → Interrupt execution.")
    engine.run()
    return {"action": action, "settings": settings}
