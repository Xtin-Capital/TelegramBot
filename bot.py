import logging
import os
from datetime import time, date, timedelta
import pytz
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from supabase import create_client, AsyncClient

# Environment Variables
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", "0"))
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
TIMEZONE = pytz.timezone("Asia/Kolkata")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

supabase: AsyncClient = None

async def post_init(application: Application):
    global supabase
    # Uses the correct v2.4+ syntax for async Supabase clients
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY, is_async=True)
    logger.info("Supabase connected successfully.")

async def join_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    today = date.today().isoformat()
    
    res = await supabase.table('users').select('*').eq('user_id', user.id).execute()
    if res.data:
        await update.message.reply_text("You are already registered.")
        return
        
    await supabase.table('users').insert({
        "user_id": user.id,
        "username": user.username or user.first_name,
        "joined_date": today
    }).execute()
    
    await update.message.reply_text(f"Welcome to the 90-Day Trading Challenge, @{user.username or user.first_name}! Post a screenshot every day to survive.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    today = date.today().isoformat()
    message_id = update.message.message_id
    
    res = await supabase.table('users').select('status').eq('user_id', user.id).execute()
    if not res.data or res.data[0]['status'] != 'active':
        return
        
    check_upload = await supabase.table('uploads').select('*').eq('user_id', user.id).eq('upload_date', today).execute()
    if check_upload.data:
        return
        
    try:
        await supabase.table('uploads').insert({
            "user_id": user.id,
            "upload_date": today,
            "message_id": message_id
        }).execute()
        
        await supabase.table('users').update({"consecutive_warnings": 0}).eq('user_id', user.id).execute()
        await update.message.reply_text(f"Day logged for @{user.username or user.first_name}. Keep it up!", reply_to_message_id=message_id)
    except Exception as e:
        logger.error(f"Error logging photo: {e}")

async def evening_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    today = date.today().isoformat()
    users_res = await supabase.table('users').select('user_id, username').eq('status', 'active').execute()
    if not users_res.data:
        return
        
    uploads_res = await supabase.table('uploads').select('user_id').eq('upload_date', today).execute()
    uploaded_ids = {row['user_id'] for row in uploads_res.data}
    
    missing = [u for u in users_res.data if u['user_id'] not in uploaded_ids]
    
    if missing:
        tags = " ".join([f"@{u['username']}" for u in missing])
        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID, 
            text=f"⚠️ Reminder: It's 8:00 PM! Upload your trading screenshot before midnight to avoid a warning.\n{tags}"
        )

async def midnight_check_job(context: ContextTypes.DEFAULT_TYPE):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    ninety_days_ago = (date.today() - timedelta(days=90)).isoformat()
    
    users_res = await supabase.table('users').select('user_id, username, consecutive_warnings, joined_date').eq('status', 'active').execute()
    if not users_res.data:
        return
        
    uploads_res = await supabase.table('uploads').select('user_id').eq('upload_date', yesterday).execute()
    uploaded_ids = {row['user_id'] for row in uploads_res.data}
    
    for u in users_res.data:
        user_id = u['user_id']
        username = u['username']
        
        if user_id not in uploaded_ids:
            new_warnings = u['consecutive_warnings'] + 1
            if new_warnings >= 3:
                await supabase.table('users').update({"status": 'eliminated', "consecutive_warnings": new_warnings}).eq('user_id', user_id).execute()
                await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=f"🚨 @{username} has missed 3 consecutive days and is ELIMINATED.")
            else:
                await supabase.table('users').update({"consecutive_warnings": new_warnings}).eq('user_id', user_id).execute()
                await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=f"⚠️ @{username} missed yesterday's upload. Warning {new_warnings}/3.")
        else:
            if u['joined_date'] <= ninety_days_ago:
                await supabase.table('users').update({"status": 'winner'}).eq('user_id', user_id).execute()
                await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=f"🏆 CONGRATULATIONS @{username}! You completed the 90-Day Challenge!")

def main():
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    application.add_handler(CommandHandler("join_challenge", join_challenge))
    application.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.GROUPS, handle_photo))

    application.job_queue.run_daily(evening_reminder_job, time=time(hour=20, minute=0, tzinfo=TIMEZONE))
    application.job_queue.run_daily(midnight_check_job, time=time(hour=0, minute=1, tzinfo=TIMEZONE))

    RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")
    
    if RENDER_URL:
        PORT = int(os.getenv("PORT", "10000"))
        logger.info(f"Starting Webhook on port {PORT} at {RENDER_URL}")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"{RENDER_URL}/{BOT_TOKEN}"
        )
    else:
        logger.info("Starting in polling mode...")
        application.run_polling()

if __name__ == "__main__":
    main()
