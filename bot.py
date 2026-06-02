
    import os
    import re
    import asyncio
    import tempfile
    from contextlib import asynccontextmanager

    from aiogram import Bot, Dispatcher, F
    from aiogram.types import Message
    from aiogram.filters import CommandStart

    import yt_dlp

    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN environment variable is required")

    MAX_MB = float(os.getenv("MAX_MB", "50"))  # default 50MB (Bot API standard limit)
    MAX_SIZE = int(MAX_MB * 1024 * 1024)

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    URL_REGEX = re.compile(r"(https?://\S+)", re.IGNORECASE)

    @asynccontextmanager
    async def tempdir():
        d = tempfile.TemporaryDirectory()
        try:
            yield d.name
        finally:
            d.cleanup()

    def pick_progressive_under_limit(info: dict, max_size: int):
        """Pick a progressive (video+audio in one file) format under size limit if possible."""
        # In case of playlists, use first entry
        if 'entries' in info and info['entries']:
            info = info['entries'][0]
        fmts = info.get('formats') or []

        def fsize(fmt):
            return fmt.get('filesize') or fmt.get('filesize_approx') or 0

        progressive = []
        for f in fmts:
            ac = f.get('acodec')
            vc = f.get('vcodec')
            if (ac and ac != 'none') and (vc and vc != 'none'):
                progressive.append(f)
        # Prefer mp4, then smallest that fits under limit, keeping decent quality
        progressive.sort(key=lambda f: (
            0 if f.get('ext') == 'mp4' else 1,
            fsize(f) or 10**12,                 # smaller first
            -(f.get('height') or 0),            # higher resolution preferred when sizes equal
        ))
        viable = [f for f in progressive if fsize(f) and fsize(f) <= max_size]
        if viable:
            return viable[0]['format_id']
        # Fallback: best progressive mp4 even if size unknown
        for f in progressive:
            if f.get('ext') == 'mp4' and f.get('format_id'):
                return f['format_id']
        # Last resort: best
        return 'best'

    def human_mb(b: int) -> str:
        return f"{b/1024/1024:.1f}MB"

    @dp.message(CommandStart())
    async def start(message: Message):
        await message.answer(
            "أهلا 👋
"             f"ابعت لينك فيديو عام (يوتيوب/إنستجرام/فيسبوك/X/تيك توك/بينترست/ريديت). الحد: {int(MAX_MB)}MB.
"             "ملحوظة: قد يفشل تنزيل بعض الروابط لو كانت تحتاج دمج صوت/فيديو بدون FFmpeg."
        )

    @dp.message(F.text)
    async def handle(message: Message):
        m = URL_REGEX.search(message.text or '')
        if not m:
            return await message.answer('ابعت لينك صحيح لو سمحت.')
        url = m.group(1)
        await message.answer('جارٍ التحضير… ⏳')

        info_opts = {'quiet': True, 'skip_download': True}
        try:
            with yt_dlp.YoutubeDL(info_opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            return await message.answer(f'تعذّر قراءة الرابط: {e}')

        fmt_id = pick_progressive_under_limit(info, MAX_SIZE)

        async with tempdir() as d:
            dl_opts = {
                'quiet': True,
                'format': fmt_id,
                'noplaylist': True,
                'outtmpl': {
                    'default': os.path.join(d, '%(title).80s.%(ext)s')
                },
                # لا نستخدم دمج الصوت/الفيديو لتجنّب الحاجة لـ FFmpeg على Render
            }
            file_path = None
            try:
                with yt_dlp.YoutubeDL(dl_opts) as ydl:
                    res = ydl.extract_info(url, download=True)
                    file_path = ydl.prepare_filename(res)
                # تحقق من الحجم
                size = os.path.getsize(file_path)
                if size > MAX_SIZE:
                    return await message.answer(
                        f'الفيديو حجمه {human_mb(size)} أكبر من الحد ({human_mb(MAX_SIZE)}). جرّب رابط أقصر/جودة أقل.'
                    )
                caption = (res.get('title') or 'Video')[:1024]
                await bot.send_chat_action(message.chat.id, 'upload_video')
                await message.answer_video(open(file_path, 'rb'), caption=caption, supports_streaming=True)
            except Exception as e:
                await message.answer(f'حصل خطأ أثناء التحميل/الإرسال: {e}')
            finally:
                try:
                    if file_path and os.path.exists(file_path):
                        os.remove(file_path)
                except Exception:
                    pass

    async def main():
        await dp.start_polling(bot)

    if __name__ == '__main__':
        asyncio.run(main())
