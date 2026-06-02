import os
import re
import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from aiogram import Bot, Dispatcher, F, types
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder

import yt_dlp
from yt_dlp.utils import DownloadError

# ==================== إعدادات التسجيل ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== إعدادات البوت ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN environment variable not set")

MAX_SIZE_MB = float(os.getenv("MAX_SIZE_MB", "50"))
MAX_SIZE_BYTES = int(MAX_SIZE_MB * 1024 * 1024)
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "4"))  # عدد التنزيلات المتزامنة
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "true").lower() == "true"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==================== نظام التخزين المؤقت ====================
class DownloadCache:
    """تخزين مؤقت للفيديوهات عشان ما نعيد التنزيل"""
    def __init__(self, cache_dir: Path = Path("./cache")):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(exist_ok=True)
        self._cache: Dict[str, Path] = {}
        self._load_cache()
    
    def _load_cache(self):
        """تحميل الكاش من المجلد"""
        for file in self.cache_dir.glob("*"):
            if file.is_file():
                # اسم الملف هو المعرف
                self._cache[file.stem] = file
    
    def get(self, video_id: str) -> Optional[Path]:
        """جلب فيديو من الكاش"""
        if video_id in self._cache:
            path = self._cache[video_id]
            if path.exists():
                logger.info(f"Cache hit: {video_id}")
                return path
        return None
    
    def set(self, video_id: str, file_path: Path) -> Path:
        """تخزين فيديو في الكاش"""
        cached_path = self.cache_dir / f"{video_id}{file_path.suffix}"
        if not cached_path.exists():
            file_path.rename(cached_path)
        self._cache[video_id] = cached_path
        logger.info(f"Cached: {video_id}")
        return cached_path
    
    def cleanup(self, max_age_days: int = 7):
        """تنظيف الكاش القديم"""
        now = datetime.now().timestamp()
        for video_id, path in list(self._cache.items()):
            age_days = (now - path.stat().st_mtime) / 86400
            if age_days > max_age_days:
                path.unlink(missing_ok=True)
                del self._cache[video_id]
                logger.info(f"Cleaned cache: {video_id}")

cache = DownloadCache() if CACHE_ENABLED else None

# ==================== نظام التنزيل الاحترافي ====================
@dataclass
class DownloadResult:
    """نتيجة عملية التنزيل"""
    file_path: Path
    title: str
    duration: int
    filesize: int
    video_id: str
    webpage_url: str
    thumbnail: Optional[str] = None

class DownloaderService:
    """
    خدمة تنزيل احترافية
    تشتغل في thread منفصل عشان ما تعلق البوت [citation:3]
    """
    
    def __init__(self, download_dir: Path = Path("./downloads"), max_workers: int = 4):
        self._download_dir = download_dir
        self._download_dir.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="downloader-worker"
        )
        self._active_downloads: Dict[str, float] = {}
    
    async def download(
        self,
        url: str,
        quality: str = "best",
        progress_callback: Optional[callable] = None
    ) -> DownloadResult:
        """
        تنزيل فيديو من أي رابط
        
        quality: 'best', '720p', '480p', '360p', 'mp3'
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            self._sync_download,
            url,
            quality,
            progress_callback
        )
    
    def _sync_download(
        self,
        url: str,
        quality: str,
        progress_callback: Optional[callable] = None
    ) -> DownloadResult:
        """الجزء المتزامن من التنزيل (يشتغل في thread منفصل)"""
        
        # إعدادات الجودة حسب الطلب
        format_map = {
            'best': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            '1080p': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]',
            '720p': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]',
            '480p': 'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]',
            '360p': 'bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360][ext=mp4]',
            'mp3': 'bestaudio/best'
        }
        
        format_spec = format_map.get(quality, format_map['best'])
        
        # إعدادات yt-dlp
        ydl_opts: Dict[str, Any] = {
            'format': format_spec,
            'outtmpl': str(self._download_dir / '%(id)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'ignoreerrors': True,
            'retries': 5,
            'fragment_retries': 5,
            'concurrent_fragment_downloads': 4,
        }
        
        # إذا كان MP3
        if quality == 'mp3':
            ydl_opts.update({
                'extractaudio': True,
                'audioformat': 'mp3',
                'audioquality': 5,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }]
            })
        
        # إضافة progress hook
        if progress_callback:
            ydl_opts['progress_hooks'] = [progress_callback]
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # استخراج المعلومات
                info = ydl.extract_info(url, download=False)
                
                # التأكد من وجود video_id
                video_id = info.get('id', str(hash(url)))
                
                # التحقق من الكاش أولاً
                if cache and (cached := cache.get(video_id)):
                    return DownloadResult(
                        file_path=cached,
                        title=info.get('title', 'Video'),
                        duration=info.get('duration', 0),
                        filesize=cached.stat().st_size,
                        video_id=video_id,
                        webpage_url=info.get('webpage_url', url),
                        thumbnail=info.get('thumbnail')
                    )
                
                # تنزيل الفيديو
                ydl.download([url])
                file_path = Path(ydl.prepare_filename(info))
                
                # إذا كان MP3، تغيير الامتداد
                if quality == 'mp3':
                    mp3_path = file_path.with_suffix('.mp3')
                    if mp3_path.exists():
                        file_path = mp3_path
                
                # تخزين في الكاش
                if cache:
                    file_path = cache.set(video_id, file_path)
                
                return DownloadResult(
                    file_path=file_path,
                    title=info.get('title', 'Video'),
                    duration=info.get('duration', 0),
                    filesize=file_path.stat().st_size,
                    video_id=video_id,
                    webpage_url=info.get('webpage_url', url),
                    thumbnail=info.get('thumbnail')
                )
                
        except DownloadError as e:
            logger.error(f"Download error for {url}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            raise
    
    async def shutdown(self):
        """إغلاق الـ executor"""
        self._executor.shutdown(wait=True, cancel_futures=False)

downloader = DownloaderService(max_workers=MAX_WORKERS)

# ==================== دوال مساعدة ====================
def format_duration(seconds: int) -> str:
    """تحويل الثواني إلى صيغة mm:ss أو hh:mm:ss"""
    if not seconds:
        return "Unknown"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"

def format_size(bytes_size: int) -> str:
    """تحويل البايت إلى MB أو GB"""
    if bytes_size < 1024 * 1024:
        return f"{bytes_size / 1024:.1f}KB"
    elif bytes_size < 1024 * 1024 * 1024:
        return f"{bytes_size / (1024 * 1024):.1f}MB"
    else:
        return f"{bytes_size / (1024 * 1024 * 1024):.2f}GB"

# ==================== أزرار اختيار الجودة ====================
def get_quality_keyboard(url: str) -> InlineKeyboardMarkup:
    """إنشاء أزرار اختيار الجودة"""
    builder = InlineKeyboardBuilder()
    builder.button(text="🎬 1080p", callback_data=f"quality:1080p:{url[:100]}")
    builder.button(text="🎬 720p", callback_data=f"quality:720p:{url[:100]}")
    builder.button(text="🎬 480p", callback_data=f"quality:480p:{url[:100]}")
    builder.button(text="🎬 360p", callback_data=f"quality:360p:{url[:100]}")
    builder.button(text="🎵 MP3 (Audio)", callback_data=f"quality:mp3:{url[:100]}")
    builder.button(text="✨ Best Quality", callback_data=f"quality:best:{url[:100]}")
    builder.adjust(2, 2, 2)
    return builder.as_markup()

# ==================== أوامر البوت ====================
@dp.message(CommandStart())
async def start_command(message: Message):
    await message.answer(
        "🎬 **مرحباً بك في بوت التحميل الاحترافي!**\n\n"
        "أرسل لي رابط فيديو من أي موقع وسأقوم بتحميله لك.\n\n"
        "**المميزات:**\n"
        f"• يدعم أكثر من 1500 موقع (YouTube, TikTok, Instagram, Facebook, X, Pinterest, SoundCloud وغيرها)\n"
        f"• اختيار جودة التحميل (1080p → MP3)\n"
        f"• نظام تخزين مؤقت للتحميلات المتكررة\n"
        f"• تحميل متعدد المستخدمين في نفس الوقت\n"
        f"• الحد الأقصى: {int(MAX_SIZE_MB)}MB\n\n"
        "**للبدء:** أرسل الرابط وسأعطيك خيارات الجودة 📥",
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message(Command("stats"))
async def stats_command(message: Message):
    """إحصائيات البوت (للمطور فقط)"""
    # يمكن إضافة إحصائيات متقدمة هنا
    await message.answer(
        "📊 **إحصائيات البوت**\n\n"
        f"• الحد الأقصى للحجم: {int(MAX_SIZE_MB)}MB\n"
        f"• عدد التنزيلات المتزامنة: {MAX_WORKERS}\n"
        f"• التخزين المؤقت: {'مفعل' if CACHE_ENABLED else 'معطل'}",
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message(F.text & ~F.text.startswith('/'))
async def handle_url(message: Message):
    """معالجة الروابط المرسلة"""
    url = message.text.strip()
    
    # تحقق من صحة الرابط
    url_pattern = re.compile(r'https?://[^\s]+')
    if not url_pattern.match(url):
        await message.answer("❌ الرجاء إرسال رابط صحيح يبدأ بـ http:// أو https://")
        return
    
    # عرض أزرار اختيار الجودة
    await message.answer(
        "📥 **تم استلام الرابط!**\n\nاختر الجودة التي تريدها:",
        reply_markup=get_quality_keyboard(url),
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== معالجة اختيار الجودة ====================
@dp.callback_query(lambda c: c.data and c.data.startswith("quality:"))
async def process_quality(callback: types.CallbackQuery):
    """معالجة اختيار المستخدم للجودة"""
    _, quality, url = callback.data.split(":", 2)
    
    await callback.message.edit_text(
        f"⏳ **جاري تحميل الفيديو...**\n"
        f"🔧 الجودة المختارة: {quality}\n"
        f"📥 الرجاء الانتظار...",
        parse_mode=ParseMode.MARKDOWN
    )
    
    # إرسال إشعار بأن البوت عم يشتغل
    await callback.answer("جاري التحميل...")
    
    try:
        # تعريف progress hook داخل async function
        async def send_progress(percent: float, status: str):
            """إرسال تحديثات التحميل"""
            if percent > 0:
                await callback.message.edit_text(
                    f"⏳ **جاري التحميل...**\n"
                    f"📊 النسبة: {percent:.1f}%\n"
                    f"📥 {status}",
                    parse_mode=ParseMode.MARKDOWN
                )
        
        # تنزيل الفيديو
        result = await downloader.download(url, quality)
        
        # التحقق من الحجم
        if result.filesize > MAX_SIZE_BYTES:
            await callback.message.edit_text(
                f"❌ **الملف كبير جداً!**\n\n"
                f"حجم الملف: {format_size(result.filesize)}\n"
                f"الحد الأقصى: {int(MAX_SIZE_MB)}MB\n\n"
                f"جرب رابط آخر أو جودة أقل.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # إرسال الفيديو أو الصوت
        caption = f"🎬 **{result.title[:900]}**"
        if result.duration:
            caption += f"\n⏱️ المدة: {format_duration(result.duration)}"
        caption += f"\n💾 الحجم: {format_size(result.filesize)}"
        
        # إرسال حسب نوع الملف
        if quality == 'mp3':
            audio_file = FSInputFile(result.file_path)
            await callback.message.answer_audio(
                audio_file,
                caption=caption,
                title=result.title[:100],
                performer="Downloader Bot",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            video_file = FSInputFile(result.file_path)
            await callback.message.answer_video(
                video_file,
                caption=caption,
                supports_streaming=True,
                parse_mode=ParseMode.MARKDOWN
            )
        
        # حذف الرسالة القديمة
        await callback.message.delete()
        
        # تنظيف الملف المؤقت إذا لم يكن في الكاش
        if not cache:
            result.file_path.unlink(missing_ok=True)
        else:
            # فقط حذف إذا كان عمر الملف كبير
            pass
            
    except DownloadError as e:
        await callback.message.edit_text(
            f"❌ **فشل التحميل!**\n\n"
            f"السبب: {str(e)[:200]}\n\n"
            f"تأكد من صحة الرابط وحاول مرة أخرى.",
            parse_mode=ParseMode.MARKDOWN
        )
        logger.error(f"Download error: {e}")
    except Exception as e:
        await callback.message.edit_text(
            f"❌ **حدث خطأ غير متوقع!**\n\n"
            f"الخطأ: {str(e)[:200]}",
            parse_mode=ParseMode.MARKDOWN
        )
        logger.error(f"Unexpected error: {e}")

# ==================== تشغيل البوت ====================
async def main():
    """تشغيل البوت"""
    logger.info("Starting bot...")
    try:
        await dp.start_polling(bot)
    finally:
        await downloader.shutdown()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
