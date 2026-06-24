import os
import logging
import asyncio
from aiohttp import web  # 👈 Ye naya import add kiya hai
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums.parse_mode import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Logging setup
logging.basicConfig(level=logging.INFO)

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')

if not BOT_TOKEN:
    raise RuntimeError('⚠️ BOT_TOKEN not set in .env')

# Initialize Bot and Dispatcher
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

WELCOME_TEXT = (
    "╭━━━━━━━━━━━━━━━━━━━━━━━━━━━━╮\n"
    "┃  🤖 <b>SAFE AUTO REQUEST BOT</b>\n"
    "┃━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "┃\n"
    "┃  📌 <b>What this bot does:</b>\n"
    "┃  • Auto-join groups/channels\n"
    "┃  • Automatically approve join requests\n"
    "┃  • 100% Safe & Secure (No OTP required)\n"
    "┃\n"
    "┃  ⚡ <b>Quick Start:</b>\n"
    "┃  Just add me to your Group or Channel\n"
    "┃  as an Admin with 'Invite Users' rights!\n"
    "┃\n"
    "╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
)

async def get_welcome_kb(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='➕ Add to Group', url=f'https://t.me/{bot_username}?startgroup=true')],
        [InlineKeyboardButton(text='➕ Add to Channel', url=f'https://t.me/{bot_username}?startchannel=start')]
    ])

@dp.message(CommandStart())
async def cmd_start(msg: types.Message):
    """Handle /start command securely"""
    if msg.chat.type != "private":
        return
    
    me = await bot.get_me()
    kb = await get_welcome_kb(me.username)
    
    await msg.answer(WELCOME_TEXT, reply_markup=kb)

@dp.chat_join_request()
async def auto_approve_join_request(update: types.ChatJoinRequest):
    """Safely auto-approve join requests"""
    try:
        await update.approve()
        logging.info(f"Approved user {update.from_user.id} in chat {update.chat.id}")
    except Exception as e:
        logging.error(f"Failed to approve user: {e}")

# 👇 DUMMY WEB SERVER (Render ko khush rakhne ke liye)
async def handle_ping(request):
    return web.Response(text="Bot is running beautifully! 🚀")

async def start_dummy_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Render khud PORT environment variable set karta hai
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"Dummy web server started on port {port}")

async def main():
    # 1. Pehle dummy server start karo
    await start_dummy_server()
    
    # 2. Phir bot start karo
    logging.info("🤖 Safe Auto-Approve Bot is running...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped.")
