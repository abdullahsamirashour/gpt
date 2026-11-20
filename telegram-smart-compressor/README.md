# 🗜️ اضغطها | Media Lite

[فتح النسخة المستقرة في Google Colab](https://colab.research.google.com/github/abdullahsamirashour/gpt/blob/main/telegram-smart-compressor/Smart_Compressor.ipynb)

أداة Colab لضغط الفيديو والصوت، وبخلية مستقلة لنقل الملفات بين Google Drive وTelegram في الاتجاهين.

## الاستخدام — Smart Tabs

1. شغّل **▶ خلية ①** لفتح Smart UI. على الكمبيوتر تظهر 5 Tabs؛ وعلى الشاشة الضيقة تظهر **قائمة منسدلة لاختيار الأداة** تلقائيًا.
2. اضبط Quick Settings، وافتح Advanced حسب الحاجة. الشرح وروابط Undo داخل **Help · Undo · إعدادات العرض** المقفول افتراضيًا. لو كشف الشاشة مش مناسب، اختر يدويًا **قائمة الموبايل** أو **تابات الكمبيوتر** من هناك.
3. اضغط **▶ خلية ②** للتنفيذ. الـProgress وسجلات المحركات الأصلية تظل في Output خلية التشغيل، وللإيقاف استخدم زر Stop الأصلي في Colab.
4. **Undo:** النسخة السابقة لتعديل الموبايل محفوظة في [فرع GitHub Backup](https://github.com/abdullahsamirashour/gpt/tree/backup/media-lite-pre-mobile-first-2026-11-20/telegram-smart-compressor)؛ و[الواجهة الكلاسيكية القديمة](https://colab.research.google.com/github/abdullahsamirashour/gpt/blob/backup/media-lite-pre-tabs-2026-11-20/telegram-smart-compressor/Smart_Compressor.ipynb) تظل بديلًا إن فشلت Widgets على جهازك.

**أمان:** Telegram → Drive يحتفظ بحقل اسم الفولدر الفرعي، وخيار حذف رسائل Telegram بعد التحقق ظاهر ومفعّل افتراضيًا بناءً على طلب المستخدم. لكن **وضع المعاينة هو الافتراضي ولا يحذف أي رسالة**. التنفيذ الفعلي هو فقط ما قد يحذف، بعد التحقق.

الأقسام التالية تشرح محركات التشغيل وخصائصها، حتى لو اتغير مكان الخيارات إلى Tabs.

## مشكلة توصيل Google Drive في خلية الضغط

الـNotebook فيها خلايا مستقلة، لكن Colab نفسها مشتركة بين الضغط والنقل. خلية الضغط تتعرّف الآن على **Drive المركّب بالفعل** من خلية النقل وتستخدمه بدل ما تطلب تفويضًا جديدًا.

- `E102`: Colab فشل في توصيل Google Drive نفسه؛ افتح **Files → Mount Drive** واسمح بالصلاحيات، أو جرّب جلسة/متصفحًا آخر لو خدمة تفويض Colab تعطلت.
- `E103`: Drive ظاهر لكن مجلد إعدادات Media Lite مش قابل للقراءة/الكتابة؛ افتح **Files** وتأكد إن `/content/drive/MyDrive` متاح.
- `E999`: خطأ غير متوقع آخر. لو Drive مش متاح، التفاصيل بتتسجل محليًا في مخرجات Colab، ومش هنقول إنها اتحفظت على Drive إلا لو الحفظ نجح فعلًا.

الكود الفعلي في GitHub (`engine.py`)؛ خلايا النوت بوك مجرد launchers. لا تحذف `Telegram_Extreme_Compressor` أو جلسة Telegram علشان تصلح مشكلة Mount.

## إعادة معالجة نتيجة قديمة

اعمل Reply على النتيجة واكتب:
- `1` — نفس الإعداد.
- `2` — أصغر حجم.
- `3` — صوت فقط.
- رقم مثل `80` — حجم مستهدف بالميجابايت.

## الهوية والمزامنة

في أول تشغيل بعد أي تحديث للهوية، الأداة تحاول تلقائيًا مزامنة القناة إلى النسخة الحالية:
- الاسم: **🗜️ اضغطها | Media Lite**
- الوصف المختصر
- صورة القناة
- الرسالة المثبتة مع الـBanner ورابط Colab

إذا فشل تحديث الصورة مؤقتًا، الضغط نفسه يكمل عادي ويتم المحاولة في التشغيل التالي.

## الأداء

- تنزيل Turbo متوازي Adaptive من 4 إلى 20 اتصالًا للملفات الكبيرة، مع fallback تلقائي.
- **رفع الملفات الناتجة من الضغط:** Telethon الأصلي المتسلسل؛ الرفع المتوازي التجريبي القديم اتشال لأنه سبق وسبب تلف ملفات.
- **Cell النقل من Drive إلى Telegram:** تحميل الملفات بأجزاء Telegram الرسمية الأكبر (512 KiB، بدل الحجم التلقائي 128 KiB للملفات حتى 100 MB)، بنفس Telethon وبدون كود رفع متوازٍ غير مضمون. التحسين يقلل عدد طلبات الرفع؛ مقدار التسريع الفعلي يعتمد على Colab وTelegram ومكان سيرفرات الحساب.
- **أسماء التسجيلات في قناة النقل:** لو اسم الملف على Drive من غير امتداد (مثل `Lec1`)، محرك النقل بيقرأ نوع الملف الحقيقي بعد تنزيله ويضيف الامتداد المناسب (مثل `.ogg` أو `.m4a` أو `.mp4`) قبل رفعه. بيحافظ على أي امتداد موجود وعلى الملف الأصلي بلا تعديل. لو النوع غير معروف بثقة، يسيب الاسم ويظهر تحذير بدل التخمين.
- **الملفات المرفوعة قديمًا:** التحديث لا يغير أسماء الرسائل الموجودة بالفعل على Telegram؛ حالة النقل تمنع إعادة رفع الملفات المكتملة تلقائيًا. لإصلاح أسماء رسائل قديمة، يلزم إجراء إعادة رفع محددة وآمنة، ولا نحذف أي رسالة تلقائيًا.
- Pipeline لتنزيل الملف التالي أثناء معالجة الحالي.
- Progress حي للتنزيل والضغط والرفع، ويعرض زمن كل مرحلة بدون تكديس أشرطة مكتملة.
- Smart sampling لضبط جودة الفيديو حسب طبيعة المحتوى.

## الخصوصية

بيانات Telegram والجلسة وحالة الملفات تحفظ في Google Drive الخاص بكل مستخدم ولا تُرفع إلى GitHub.


## 🧪 Encoder Benchmark

للمقارنة بين طرق الضغط فقط، استخدم النوت بوك المنفصلة:

[فتح Encoder Benchmark في Colab](https://colab.research.google.com/github/abdullahsamirashour/gpt/blob/main/telegram-smart-compressor/Encoder_Benchmark.ipynb)

النوت بوك لا تغيّر القناة ولا محرك الضغط الأساسي. الإصدار v1.1 يشخّص FFmpeg NVENC وNVDEC وFull GPU وNVEncC كلٌ بشكل مستقل، ويستخدم Warm-up + قياسات مكررة بالـMedian، ويعرض CPU/GPU/NVENC utilization، ثم يؤكد أفضل النتائج على مقطع أطول ويحفظ تقريرًا مفصلًا في Google Drive.


## 📦 نقل الملفات في الاتجاهين — داخل Tabs مستقلة

**مش محتاج Notebook جديدة:** [افتح نفس Media Lite على Colab](https://colab.research.google.com/github/abdullahsamirashour/gpt/blob/main/telegram-smart-compressor/Smart_Compressor.ipynb). في الواجهة الموحدة هتلاقي **تاب مستقل لكل اتجاه نقل**:

### ☁️ تشغيل: Drive → Telegram

1. اختار تاب **☁️ Drive → Telegram**، وبعد ضبط الخيارات شغّل خلية **② تنفيذ**.
2. **رابط Drive:** الصق رابط ملف أو فولدر Google Drive اللي عايز تبعته.
3. **عدد الملفات:** 0 = كل الملفات الجديدة، أو رقم محدد لدفعة واحدة. **شامل المجلدات الفرعية** مفعّل افتراضيًا.
4. **التشغيل:** ابدأ بـ«معاينة فقط»، وبعد ما تتأكد من الملفات اختار «تنفيذ النقل».

القناة **📁 Media Lite | File Transfer** بتتعمل **خاصة** تلقائيًا عند النقل الفعلي لو مش موجودة، أو يتستخدم الـChannel ID المحفوظ أو القناة المطابقة الموثوقة. قناة الضغط الأصلية منفصلة. الملفات الأصلية لا تُمسح ولا تُضغط، ويتم تسجيل مسار الفولدر في وصف الملف.

#### 🔁 إعادة رفع ملفات محددة (حتى لو اتنقلت قبل كده)

1. الصق نفس رابط فولدر Drive واضغط **معاينة فقط**. هيظهر جدول بأرقام كل الملفات مرتبة بالاسم، مع حالة «✅ مكتمل» أو «🆕 جديد».
2. في خانة **أرقام الملفات لإعادة الرفع** اكتب مثلًا `1-7` لإعادة أول سبعة من ثمانية، أو `1,3,5-8` لاختيار أرقام متفرقة. الحد 200 ملف للعملية الواحدة.
3. غيّر **التشغيل** إلى **إعادة رفع المحدد** وشغّل الخلية. **تنفيذ النقل** المعتاد يظل للملفات الجديدة فقط؛ العدد `0` لا يغيّر تحديد الإعادة.
4. الرسائل القديمة والملفات الأصلية تفضل موجودة. الإعادة تستخدم علامات نقل جديدة، وتسجل معرفات الرسائل السابقة في `history` بدل مسحها. لو العملية اتقطعت، أعد تشغيل نفس اختيار الأرقام من نفس الفولدر لاستكمال الملفات المتبقية في نفس المحاولة، بدون تكرار الملفات التي نجحت؛ بعد اكتمالها، اختيار «إعادة رفع المحدد» مرة أخرى ينشئ محاولة جديدة صريحة.

**تنبيه:** أرقام الملفات مرتبطة بترتيب عرض الفولدر الحالي. لو غيّرت محتويات الفولدر بين المعاينة والتنفيذ، اعمل معاينة جديدة قبل ما تختار الأرقام. لو مجلدك أكبر من 200 ملف، اختار فولدرًا فرعيًا أصغر. لا يتم حذف رسائل Telegram القديمة أو إعادة تسمية الملفات الأصلية تلقائيًا.

### 📥 تشغيل: Telegram → Drive

1. اختار تاب **📥 Telegram → Drive**، وبعد ضبط الخيارات شغّل خلية **② تنفيذ**.
2. **قناة Telegram المصدر (اختياري):** فاضية = قناة Media Lite للنقل؛ أو اكتب `@username`/معرف قناة تستطيع الوصول إليها.
3. **فولدر Drive للحفظ (اختياري):** فاضي = إنشاء/استخدام **Media Lite Transfers**؛ أو الصق رابط فولدر Drive موجود.
4. **عدد الملفات:** 0 = كل الملفات الجديدة، وأي رقم موجب من 1 إلى 200 = دفعة صغيرة. اختار المعاينة أو تنفيذ النقل.

**كل أداة مستقلة**، وتحمّل محرك النقل `telegram-smart-compressor/transfer_engine.py` من GitHub تلقائيًا، فلا يوجد منطق النقل داخل النوت بوك. لن تحتاج تشغّل أداة الضغط أو أداة الاتجاه الآخر.

### 🔐 أول مرة تسجّل دخول Telegram للنقل

- **الافتراضي: QR بدون كود.** عند طلب تسجيل الدخول، يعرض Colab رمز QR: افتح Telegram على جهاز مسجّل دخول → **الإعدادات → الأجهزة → ربط جهاز**، ثم امسح الرمز ووافق على طلب الدخول. لو Colab وTelegram على نفس الجهاز، جرّب رابط **فتح رابط الموافقة** الذي يظهر تحت الرمز. لو رمز QR انتهت صلاحيته يجدد نفسه أثناء التشغيل.
- **البديل: كود داخل Telegram.** رمز دخول حساب Telegram لا يلزم أن يكون SMS؛ غالبًا يصل في محادثة **Telegram** الرسمية الموثقة داخل التطبيق، وقد يصل إلى أي جهاز آخر مسجل دخول. تظهر نهاية رقم الحساب المسجل في الشاشة للتأكد من الحساب الصحيح. لا تُشارك الكود مع أي شخص.
- لا تُكرر طلب الأكواد بشكل متتالٍ لو مش بتوصل؛ تيليجرام ممكن يطبق حدودًا للمحاولات.
- **لو الجلسة موجودة بالفعل:** يتخطى المحرك تسجيل الدخول من الأساس ويحفظ جلسة النقل المنفصلة على Drive للاستخدام التالي. محرك النقل لا ينسخ ملف جلسة الضغط الأصلية؛ لأن تشغيل مفتاح جلسة واحد مرتين بالتوازي قد يُبطل الاثنين.

> ملاحظة: خلية المعاينة الافتراضية لا تنقل أي ملفات. في الاتجاه Telegram → Drive ممكن الأداة تحتاج إنشاء قناة النقل عند عدم وجودها وفق سلوك المحرك الحالي؛ ولعمل نقل فعلي لازم تسجيل الدخول إلى Telegram وتوصيل Drive. كل الاتجاهات تعتمد على جلسة Colab التفاعلية.

### 🔐 مشكلة Google Drive / Colab على الآيباد

إذا ظهر **MessageError** داخل `connect_drive`، فالخطأ حصل عند التفويض لـGoogle Drive **قبل تسجيل Telegram**، وليس في رابط الفولدر أو في الملفات.

- الأداة الآن **تتخطى `drive.mount()` لو Drive مربوط بالفعل**، وتحاول استخدام تصريح Drive API الموجود قبل ما تطلب تفويضًا جديدًا. مش مطلوب ربط Drive مرتين في كل تشغيل.
- إذا كان Drive غير مربوط، Colab لازم يسمح لك بتوصيله من الشريط الجانبي **Files → Mount Drive** والموافقة على الصلاحيات. إذا فشل Colab في `credential propagation` حتى هناك، المشكلة قد تكون من خدمة Google Colab نفسها ولا يمكن إصلاحها بكود Telegram؛ جرب نافذة Colab جديدة أو متصفحًا مختلفًا، وراجع إعدادات منع النوافذ والكوكيز.
- إذا نجح Mount وفشلت **صلاحية Google Drive API**، سيظهر سبب مستقل في رسالة الخطأ. تأكد أن التفويض منح الوصول إلى Google Drive باستخدام الحساب صاحب الفولدر.
- في وضع **معاينة فقط** لا يُطلب الدخول إلى Telegram في اتجاه Drive → Telegram، حتى لو كان خيار QR أو الكود ظاهرًا.

مراجع المشكلة على مستودع Google Colab: [#6137](https://github.com/googlecolab/colabtools/issues/6137) و[#6114](https://github.com/googlecolab/colabtools/issues/6114) (أكتوبر/سبتمبر 2026).

### الحفاظ على الملفات والاستكمال

- لا نحذف الملفات الأصلية من Telegram أو Google Drive، ولا نضغطها أثناء النقل.
- يتم حفظ حالة الاتجاهين منفصلة تحت Telegram_Extreme_Compressor/Media_Lite_Transfer/state_v1.json، وجلسة النقل في المجلد نفسه. ليست هناك رموز سرية في GitHub.
- عند نهاية التشغيل يظهر زر **Import logs to Drive** لحفظ سجل النقل عند الحاجة، كما في خلية الضغط؛ وفي المجلد نفسه يُحفظ ملخص آخر تشغيل فعلي.
- يحتفظ الرفع إلى Telegram بعلامة داخل وصف الملف تمنع عودته تلقائيًا إلى Drive، وتساعد على تجنب إعادة إرسال الأجزاء عند انقطاع التشغيل.
- في Cell النقل، بنرفع باستخدام `Telethon.upload_file(part_size_kb=512)` ثم `send_file` بنفس الملف المرفوع؛ وده **مش رفع متوازٍ**. الملفات الكبيرة المجزأة بتفضل تتحقق من حجم كل جزء وحالة رسالته، ولا تتغير بيانات الملف.
- تتحقق عمليات النقل من أحجام الملفات؛ يتحقق تنزيل Drive ورفعه كذلك باستخدام MD5 عندما يكون متاحًا.
- يستخدم Telegram حد أجزاء احترازيًا **1.9 GB**؛ الملفات الأكبر تُرسل كأجزاء خام دون فقد مع ملف JSON لإعادة تجميعها. إرسال ملف كبير كأجزاء لا يعني أن الأجزاء نفسها ملفات فيديو قابلة للتشغيل.
- يتم تصدير Google Docs إلى PDF، وSheets إلى XLSX، وSlides إلى PPTX قبل إرسالها. الأنواع الأخرى غير القابلة للتصدير تظهر كأخطاء واضحة.
- يعتمد نقل الملفات على مساحة Colab المتاحة وسياسة الاستخدام التفاعلي وحدود Google Drive وTelegram. نقل الملفات غير مضمون بعد انتهاء جلسة Colab، والملف الجاري قد يحتاج إعادة نقل؛ الملفات المكتملة لا تُكرر عادةً.

### اختبارات التطوير

ملف test_transfer_engine.py يغطي روابط Drive غير الموثوقة، وحالة النقل، وتقسيم الملف دون نسخه مرتين، واستكشاف المجلدات؛ ويعمل تلقائيًا في GitHub Actions مع بقية اختبارات الأداة.

## Gemini Ready | Smart Audio Preparation

Open [Media Lite Colab](https://colab.research.google.com/github/abdullahsamirashour/gpt/blob/main/telegram-smart-compressor/Smart_Compressor.ipynb), run cell **①**, choose **🎧 Gemini Ready**, and run cell **②** when ready. All 4 original sources are retained: Telegram transfer channel, Drive folder, Drive links, and Internet URLs.

### Audio Preparation Mode (default: Smart)

- **⚡ Smart:** inspect the **real audio codec, container, stream count, size and duration** before deciding. A compatible small MP3 / OGG / M4A (also small compatible WAV / FLAC / AIFF) stays the **original file, unchanged**. Compatible larger audio is split using FFmpeg stream-copy without lossy re-encoding. If there is a single supported audio track inside a video, the engine can extract it into a suitable audio container by stream copy. Unsupported or ambiguous sources use the established audio encoder. Unusually large high-bitrate recordings that would exceed 12 parts are compressed once to reduce transfer overhead.
- **🎧 Preserve Quality:** keep the original or stream-copy split, even for very large supported audio; only encode when necessary to satisfy codec, container, size or duration rules.
- **🎚️ Always Compress:** the original encode-first behavior, useful to enforce one uniform output codec and bitrate.
- **Advanced Audio:** explicitly enabling custom audio settings **always forces encoding**, including in Smart/Preserve. The chosen Preset (default Opus 24 kbps) is used **when encoding**, not on files left unchanged.

Saved output signatures include mode and advanced status, so changing the mode does not reuse the wrong output receipts. Importantly, **Telegram New** still treats a previously completed source under any mode as already processed, so switching defaults cannot accidentally reprocess and delete old archive messages. Force Reprocess stays an explicit option.

### Limits and safety

- The default size rule remains **strictly less than 9,000,000 bytes** (9 decimal MB, not MiB). Optional duration cap defaults to **0 = no cap**. Stream-copy parts are checked for byte size, duration and total coverage; failures fall back to normal encoding.
- The output suffix matches the actual audio. Example: a ready original Lecture.mp3 remains .mp3; AAC extracted losslessly from video is .m4a; a newly encoded speech recording is .ogg.
- Multi-audio-track sources are excluded from direct copying and use the existing encoder's safer audio selection; unsupported or falsely labeled audio is encoded.
- Drive outputs are saved under MyDrive/Telegram_Extreme_Compressor/Media_Lite_Studio/Gemini_Ready/<date>_<batch>/ with manifest.json. Resumable Drive uploads, size and MD5 checks, receipt reuse and source protection are unchanged.
- The Telegram deletion checkbox remains enabled by default, but Preview is the default operation and **never deletes**. After processing, source messages are deleted only when every part on Drive is verified by folder, size and MD5.
- **Fast Preview:** Drive and Internet previews skip the video-downloader module, FFmpeg capability checks and an unnecessary asyncio patch. They still read saved state and the exact audio presets.
- **Not automatically enabled:** server-side direct Drive copy or parallel processing across files. They need separate benchmarks and validation rather than bypassing source checks or changing concurrency prematurely.

### Tests and Undo

The Gemini regression suite includes FFmpeg tests for unchanged small audio, copy-only split, video AAC extraction, oversized WAV fallback, multi-audio safety, mode-isolated state, Preview initialization, Drive verification and never-delete-unverified behavior.

**Undo Gemini Smart:** restore the pre-change source from [this backup branch](https://github.com/abdullahsamirashour/gpt/tree/backup/media-lite-pre-gemini-smart-2026-11-20/telegram-smart-compressor). An authenticated Colab+Telegram+Drive test remains necessary for end-to-end provider-specific performance measurements.
