import os
import json
import sqlite3
import datetime
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp
import functions_framework
import flask

TOKEN = "8625235055:AAGWsXUMyA-CIrenKuVqqPUxfpzj9_YZczw"
bot = telebot.TeleBot(TOKEN)
ADMIN_ID = 8300128103

# ===== إنشاء قاعدة البيانات =====
conn = sqlite3.connect('/tmp/bot_stats.db', check_same_thread=False)  # مهم: المسار /tmp للقراءة/الكتابة في GCF
c = conn.cursor()

c.execute('''CREATE TABLE IF NOT EXISTS users
             (user_id INTEGER PRIMARY KEY, 
              username TEXT,
              first_name TEXT,
              last_name TEXT,
              first_seen TIMESTAMP,
              last_seen TIMESTAMP,
              downloads_count INTEGER DEFAULT 0)''')

c.execute('''CREATE TABLE IF NOT EXISTS downloads
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER,
              url TEXT,
              download_time TIMESTAMP,
              status TEXT)''')
conn.commit()

# ===== دوال التسجيل =====
def register_user(message):
    user_id = message.from_user.id
    username = message.from_user.username or "لا يوجد"
    first_name = message.from_user.first_name or ""
    last_name = message.from_user.last_name or ""
    now = datetime.datetime.now()
    
    c.execute('''INSERT OR IGNORE INTO users 
                 (user_id, username, first_name, last_name, first_seen, last_seen, downloads_count)
                 VALUES (?, ?, ?, ?, ?, ?, 0)''',
              (user_id, username, first_name, last_name, now, now))
    
    c.execute('''UPDATE users SET 
                 last_seen = ?,
                 username = ?,
                 first_name = ?,
                 last_name = ?
                 WHERE user_id = ?''',
              (now, username, first_name, last_name, user_id))
    conn.commit()

def log_download(user_id, url, status):
    c.execute('''INSERT INTO downloads (user_id, url, download_time, status)
                 VALUES (?, ?, ?, ?)''',
              (user_id, url, datetime.datetime.now(), status))
    if status == "success":
        c.execute('''UPDATE users SET downloads_count = downloads_count + 1 
                     WHERE user_id = ?''', (user_id,))
    conn.commit()

# ===== المعالجات حق البوت =====
@bot.message_handler(commands=['start'])
def send_welcome(message):
    register_user(message)
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    btn = KeyboardButton("📥 تحميل فيديو")
    markup.add(btn)
    
    welcome_msg = """🎥 *مرحباً بك في بوت تحميل الفيديوهات*

هذا البوت هو أحد تطبيقات *مجتمع خدمات* التابع لمجموعة *KinTec*

⚡ *ميزات البوت:*
• تحميل فيديوهات يوتيوب، تيك توك، فيسبوك، انستغرام وتويتر
• جودة عالية MP4
• سريع ومجاني

🔗 *للانضمام لمجتمع خدمات على واتس أب:*
https://chat.whatsapp.com/ITMBBGPrNUx2uf5s002Hrh

👇 اضغط على زر التحميل وأرسل الرابط"""
    
    bot.reply_to(message, welcome_msg, reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(commands=['stats', 'state'])
def show_stats_menu(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ هذا الأمر للمشرف فقط")
        return
    
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📊 إحصائيات سريعة", callback_data="quick"),
        InlineKeyboardButton("👥 المستخدمين", callback_data="users"),
        InlineKeyboardButton("📥 التحميلات", callback_data="downloads"),
        InlineKeyboardButton("🏆 الأكثر نشاطاً", callback_data="top")
    )
    
    bot.send_message(
        message.chat.id,
        "🎛 *لوحة التحكم*\n\nاختر نوع الإحصائيات:",
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: True)
def handle_stats_buttons(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ غير مصرح", show_alert=True)
        return
    
    bot.answer_callback_query(call.id)
    
    if call.data == "quick":
        c.execute("SELECT COUNT(*) FROM users")
        total_users = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM users WHERE date(last_seen) = date('now')")
        active_today = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM users WHERE date(first_seen) = date('now')")
        new_today = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM downloads WHERE date(download_time) = date('now')")
        downloads_today = c.fetchone()[0] or 0
        
        c.execute("SELECT COUNT(*) FROM downloads")
        total_downloads = c.fetchone()[0] or 0
        
        c.execute("SELECT SUM(downloads_count) FROM users")
        successful = c.fetchone()[0] or 0
        
        success_rate = 0
        if total_downloads > 0:
            c.execute("SELECT COUNT(*) FROM downloads WHERE status='success'")
            success_count = c.fetchone()[0] or 0
            success_rate = round((success_count / total_downloads) * 100, 1)
        
        stats = f"""📊 *إحصائيات سريعة*

👥 *المستخدمين:*
• إجمالي المستخدمين: {total_users}
• مستخدمين جدد اليوم: {new_today}
• نشطاء اليوم: {active_today}

📥 *التحميلات:*
• تحميلات اليوم: {downloads_today}
• إجمالي التحميلات: {total_downloads}
• نسبة النجاح: {success_rate}%
• التحميلات الناجحة: {successful}"""
        
        bot.send_message(call.message.chat.id, stats, parse_mode="Markdown")
    
    elif call.data == "users":
        c.execute("SELECT COUNT(*) FROM users")
        total = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM users WHERE date(last_seen) = date('now')")
        today = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM users WHERE julianday('now') - julianday(last_seen) <= 7")
        week = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM users WHERE downloads_count = 0")
        no_downloads = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM users WHERE downloads_count >= 5")
        power_users = c.fetchone()[0]
        
        c.execute("SELECT AVG(downloads_count) FROM users")
        avg_downloads = round(c.fetchone()[0] or 0, 1)
        
        stats = f"""👥 *إحصائيات المستخدمين*

📊 *الإحصائيات العامة:*
• إجمالي المستخدمين: {total}
• نشطاء اليوم: {today}
• نشطاء الأسبوع: {week}

📌 *التصنيف:*
• مستخدمين جدد (لم يحملوا): {no_downloads}
• مستخدمين نشيطين (5+ تحميل): {power_users}
• متوسط التحميلات لكل مستخدم: {avg_downloads}"""
        
        bot.send_message(call.message.chat.id, stats, parse_mode="Markdown")
    
    elif call.data == "downloads":
        c.execute("SELECT COUNT(*) FROM downloads")
        total = c.fetchone()[0] or 0
        
        c.execute("SELECT COUNT(*) FROM downloads WHERE status='success'")
        success = c.fetchone()[0] or 0
        
        c.execute("SELECT COUNT(*) FROM downloads WHERE status='failed'")
        failed = c.fetchone()[0] or 0
        
        success_rate = round((success / total * 100), 1) if total > 0 else 0
        
        c.execute("SELECT COUNT(*) FROM downloads WHERE date(download_time) = date('now')")
        today = c.fetchone()[0] or 0
        
        c.execute("SELECT COUNT(*) FROM downloads WHERE julianday('now') - julianday(download_time) <= 7")
        week = c.fetchone()[0] or 0
        
        c.execute('''SELECT 
                     SUM(CASE WHEN url LIKE '%youtube%' OR url LIKE '%youtu.be%' THEN 1 ELSE 0 END) as youtube,
                     SUM(CASE WHEN url LIKE '%tiktok%' THEN 1 ELSE 0 END) as tiktok,
                     SUM(CASE WHEN url LIKE '%facebook%' OR url LIKE '%fb.com%' THEN 1 ELSE 0 END) as facebook,
                     SUM(CASE WHEN url LIKE '%instagram%' THEN 1 ELSE 0 END) as instagram,
                     SUM(CASE WHEN url LIKE '%twitter%' OR url LIKE '%x.com%' THEN 1 ELSE 0 END) as twitter
                     FROM downloads''')
        youtube, tiktok, facebook, instagram, twitter = c.fetchone()
        
        stats = f"""📥 *إحصائيات التحميلات*

📊 *الإجمالي:*
• كل التحميلات: {total}
• ناجحة: {success}
• فاشلة: {failed}
• نسبة النجاح: {success_rate}%

📅 *حسب الفترة:*
• تحميلات اليوم: {today}
• تحميلات الأسبوع: {week}

🌐 *حسب الموقع:*
• يوتيوب: {youtube or 0}
• تيك توك: {tiktok or 0}
• فيسبوك: {facebook or 0}
• انستغرام: {instagram or 0}
• تويتر: {twitter or 0}"""
        
        bot.send_message(call.message.chat.id, stats, parse_mode="Markdown")
    
    elif call.data == "top":
        c.execute('''SELECT username, first_name, downloads_count 
                     FROM users 
                     WHERE downloads_count > 0
                     ORDER BY downloads_count DESC 
                     LIMIT 10''')
        top_users = c.fetchall()
        
        if top_users:
            stats = "🏆 *أفضل 10 مستخدمين نشاطاً*\n\n"
            for i, (username, first_name, count) in enumerate(top_users, 1):
                if username and username != "لا يوجد":
                    stats += f"{i}. @{username} - {count} تحميل\n"
                else:
                    name = first_name if first_name else "مستخدم"
                    stats += f"{i}. {name} - {count} تحميل\n"
        else:
            stats = "📊 لا يوجد مستخدمين قاموا بالتحميل بعد"
        
        bot.send_message(call.message.chat.id, stats, parse_mode="Markdown")
        
        c.execute("SELECT COUNT(DISTINCT user_id) FROM downloads WHERE status='success'")
        unique_users = c.fetchone()[0] or 0
        
        extra = f"\n📊 *معلومة إضافية:*\n• عدد المستخدمين الذين حملوا فيديوهات: {unique_users}"
        bot.send_message(call.message.chat.id, extra, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📥 تحميل فيديو")
def ask_for_link(message):
    register_user(message)
    bot.send_message(message.chat.id, "🔗 أرسل لي رابط الفيديو الآن:")

@bot.message_handler(func=lambda m: True)
def download_video(message):
    if message.text.startswith('/') or message.text == "📥 تحميل فيديو":
        return
    
    register_user(message)
    url = message.text
    waiting_msg = bot.send_message(message.chat.id, "⏳ جاري التحميل... الرجاء الانتظار")
    
    try:
        ydl_opts = {
            'format': 'best[ext=mp4]/best',
            'outtmpl': '/tmp/video.%(ext)s',  # مهم: التخزين في /tmp
            'quiet': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        video_file = None
        for file in os.listdir('/tmp'):
            if file.startswith('video.') and (file.endswith('.mp4') or file.endswith('.webm')):
                video_file = os.path.join('/tmp', file)
                break
        
        if video_file:
            with open(video_file, 'rb') as f:
                bot.send_video(
                    message.chat.id, 
                    f, 
                    caption="✅ تم التحميل بنجاح!\n\n"
                            "🌐 هذا البوت من تطبيقات *مجتمع خدمات* - *KinTec*",
                    supports_streaming=True,
                    parse_mode="Markdown"
                )
            
            os.remove(video_file)
            bot.delete_message(message.chat.id, waiting_msg.message_id)
            log_download(message.from_user.id, url, "success")
        else:
            bot.edit_message_text("❌ لم يتم العثور على الفيديو", message.chat.id, waiting_msg.message_id)
            log_download(message.from_user.id, url, "failed")
            
    except Exception as e:
        bot.edit_message_text("❌ فشل التحميل... تأكد من الرابط وحاول مرة أخرى", 
                            message.chat.id, waiting_msg.message_id)
        log_download(message.from_user.id, url, "failed")

# ===== الدالة الرئيسية لـ Google Cloud Functions =====
@functions_framework.http
def telegram_webhook(request: flask.Request) -> flask.typing.ResponseReturnValue:
    """دالة الـ webhook التي تستقبل تحديثات تيليغرام"""
    if request.method == "POST":
        update = telebot.types.Update.de_json(request.get_json(force=True))
        bot.process_new_updates([update])
        return "OK", 200
    return "البوت شغال!", 200