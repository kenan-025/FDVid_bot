import os
import asyncio
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile
from aiogram.filters import CommandStart
import yt_dlp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 1. 🤖 إعدادات البوت
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in environment variables!")

# 2. 🗂️ مجلد مؤقت لتخزين الفيديوهات (راح يتنظف بعد كل استخدام)
DOWNLOAD_DIR = Path("./downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# 3. ⚡ إدارة المهام المتعددة (ThreadPoolExecutor)
# ده بيضمن إنو البوت ما يعلق وقت التحميل، ويقدر يخدم أكتر من مستخدم بنفس الوقت [citation:8].
class DownloaderService:
    def __init__(self, max_workers: int = 2):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    async def download(self, url: str):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._sync_download, url)

    def _sync_download(self, url: str):
        """هالدالة هي الجزء اللي بينفذ التحميل الفعلي."""
        ydl_opts = {
            'format': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best', # جودة 720p
            'outtmpl': str(DOWNLOAD_DIR / '%(title)s.%(ext)s'),
            'quiet': True,
            'noplaylist': True, # ما راح نحمل قوائم تشغيل
            'merge_output_format': 'mp4',
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                file_path = Path(ydl.prepare_filename(info))
                return file_path, info.get('title', 'Video')
        except Exception as e:
            logger.error(f"Download failed: {e}")
            raise e

# 4. 🚀 تشغيل البوت
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
downloader = DownloaderService()

@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer("🎬 أهلاً بك! أرسل لي رابط فيديو من يوتيوب، إنستغرام، تيك توك أو أي موقع وسأحمله لك.")

@dp.message(F.text)
async def download_handler(message: Message):
    url = message.text.strip()
    if not url.startswith(('http://', 'https://')):
        await message.answer("الرجاء إرسال رابط صحيح يبدأ بـ http:// أو https://")
        return

    status_msg = await message.answer("⏳ جاري تحميل الفيديو، الرجاء الانتظار...")
    try:
        # هنا بيتم استدعاء خدمة التحميل بشكل غير متزامن
        file_path, title = await downloader.download(url)
        
        # إرسال الفيديو للمستخدم
        video_file = FSInputFile(file_path)
        await message.answer_video(video_file, caption=f"✅ تم التحميل بنجاح:\n{title[:200]}")
        
        # تنظيف الملف من السيرفر بعد الإرسال
        file_path.unlink(missing_ok=True)
    except Exception as e:
        await message.answer(f"❌ فشل التحميل: {str(e)[:200]}")
        logger.error(f"Error: {e}")
    finally:
        await status_msg.delete()

async def main():
    logger.info("Starting bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
