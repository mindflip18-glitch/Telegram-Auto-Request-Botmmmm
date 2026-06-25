import os
import json
import logging
import asyncio
from aiohttp import web
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums.parse_mode import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# Logging setup
logging.basicConfig(level=logging.INFO)

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')

if not BOT_TOKEN:
    raise RuntimeError('⚠️ BOT_TOKEN not set in .env')

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# --- JSON DATABASE SETUP ---
SETTINGS_FILE = 'settings.json'

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_settings(data):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(data, f, indent=4)

chat_settings = load_settings()

# --- FSM STATES SETUP ---
class MsgSetup(StatesGroup):
    waiting_for_forward = State()
    waiting_for_text = State()

WELCOME_TEXT = (
    "╭━━━━━━━━━━━━━━━━━━━━━━━━━━━━╮\n"
    "┃  🤖 <b>SAFE AUTO REQUEST BOT</b>\n"
    "┃━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "┃\n"
    "┃  📌 <b>What this bot does:</b>\n"
    "┃  • Auto-join groups/channels\n"
    "┃  • Custom Welcome & Left Messages\n"
    "┃\n"
    "┃  ⚡ <b>Commands:</b>\n"
    "┃  /setleft - Set goodbye message\n"
    "┃  /setwelcome - Set welcome message\n"
    "┃  /cancel - Cancel process\n"
    "┃\n"
    "╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
)

async def get_welcome_kb(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='➕ Add to Group', url=f'https://t.me/{bot_username}?startgroup=true')],
        [InlineKeyboardButton(text='➕ Add to Channel', url=f'https://t.me/{bot_username}?startchannel=start')]
    ])

@dp.message(CommandStart())
async def cmd_start(msg: types.Message, state: FSMContext):
    if msg.chat.type != "private": return
    await state.clear()
    me = await bot.get_me()
    kb = await get_welcome_kb(me.username)
    await msg.answer(WELCOME_TEXT, reply_markup=kb)

# 👇 STEP 1: Command trigger
@dp.message(Command("setleft", "setwelcome"))
async def start_setting_msg(msg: types.Message, state: FSMContext):
    if msg.chat.type != "private": return
    
    # Check kya set karna hai (left ya welcome)
    msg_type = "left_msg" if msg.text.startswith("/setleft") else "welcome_msg"
    
    await state.update_data(msg_type=msg_type)
    await state.set_state(MsgSetup.waiting_for_forward)
    
    await msg.answer(
        "📢 <b>STEP 1: Channel Select Karein</b>\n\n"
        "Kripya apne us Channel se koi bhi ek message yahan <b>Forward</b> karein, jiske liye aap message set karna chahte hain.\n\n"
        "<i>(Cancel karne ke liye /cancel bhejein)</i>"
    )

# 👇 STEP 2: Forwarded message receive karna
@dp.message(MsgSetup.waiting_for_forward)
async def process_forwarded_msg(msg: types.Message, state: FSMContext):
    # Check agar user ne channel se forward kiya hai
    if not msg.forward_origin or msg.forward_origin.type != 'channel':
        await msg.answer("❌ Ye kisi channel ka forwarded message nahi hai. Kripya channel se ek message forward karein (ya /cancel likhein).")
        return

    channel_id = str(msg.forward_origin.chat.id)
    channel_title = msg.forward_origin.chat.title

    # Security check: Admin hai ya nahi?
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=msg.from_user.id)
        if member.status not in ['administrator', 'creator']:
            await msg.answer(f"❌ Aap '{channel_title}' ke Admin nahi hain! Kisi aur channel ka message forward karein.")
            return
    except Exception:
        await msg.answer(f"❌ Error! Pehle bot ko '{channel_title}' mein Admin banayein, uske baad message forward karein.")
        return

    # Data save karo aur agle step par bhejo
    await state.update_data(channel_id=channel_id, channel_title=channel_title)
    await state.set_state(MsgSetup.waiting_for_text)

    data = await state.get_data()
    msg_type_text = "Left (Goodbye)" if data['msg_type'] == "left_msg" else "Welcome"

    await msg.answer(
        f"✅ <b>Channel mil gaya:</b> {channel_title}\n\n"
        f"📝 <b>STEP 2: Message Bhejein</b>\n\n"
        f"Ab apna naya <b>{msg_type_text} Message</b> yahan type karke send karein.\n"
        "<i>(Aap emoji, links aur formatting ka use kar sakte hain)</i>"
    )

# 👇 STEP 3: Custom message receive karna aur save karna
@dp.message(MsgSetup.waiting_for_text)
async def process_custom_msg(msg: types.Message, state: FSMContext):
    if not msg.text:
        await msg.answer("❌ Kripya text message send karein.")
        return

    data = await state.get_data()
    channel_id = data['channel_id']
    msg_type = data['msg_type']
    channel_title = data['channel_title']

    if channel_id not in chat_settings:
        chat_settings[channel_id] = {}

    # msg.html_text use kiya taaki bold/links waisa hi save ho
    chat_settings[channel_id][msg_type] = msg.html_text 
    save_settings(chat_settings)

    msg_type_text = "Left" if msg_type == "left_msg" else "Welcome"

    await msg.answer(
        f"✅ <b>Badhai ho!</b> 🎉\n\n"
        f"'{channel_title}' ke liye aapka <b>{msg_type_text} Message</b> set ho gaya hai:\n\n"
        f"{msg.html_text}"
    )
    await state.clear()

# 👇 CANCEL COMMAND (Process rokne ke liye)
@dp.message(Command("cancel"))
async def cmd_cancel(msg: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return
    await state.clear()
    await msg.answer("❌ Process cancel kar diya gaya hai.")

# 👇 AUTO APPROVE & CUSTOM WELCOME
@dp.chat_join_request()
async def auto_approve_join_request(update: types.ChatJoinRequest):
    user_id = update.from_user.id
    chat_id = str(update.chat.id)
    
    if chat_id in chat_settings and 'welcome_msg' in chat_settings[chat_id]:
        try:
            await bot.send_message(chat_id=user_id, text=chat_settings[chat_id]['welcome_msg'])
        except Exception:
            pass 
    
    try:
        await update.approve()
    except Exception as e:
        logging.error(f"Failed to approve user: {e}")

# 👇 CUSTOM LEFT MESSAGE HANDLER
@dp.chat_member()
async def on_chat_member_update(update: types.ChatMemberUpdated):
    user = update.from_user
    chat_id = str(update.chat.id)

    if update.old_chat_member.status in ['member', 'administrator'] and update.new_chat_member.status in ['left', 'kicked']:
        default_left = (
            "🌟 ALL DRAMA DIRECT FILES AVAILABLE 🗃️\n\n"
            "https://t.me/+amS1Q3R4_Qg5NjU1\n"
            "https://t.me/+amS1Q3R4_Qg5NjU1"
        )
        
        final_msg = chat_settings.get(chat_id, {}).get('left_msg', default_left)
        
        try:
            await bot.send_message(chat_id=user.id, text=final_msg)
        except Exception:
            pass 

# 👇 DUMMY WEB SERVER (Render ke liye)
async def handle_ping(request):
    return web.Response(text="Bot is running beautifully! 🚀")

async def start_dummy_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

async def main():
    await start_dummy_server()
    logging.info("🤖 Safe Auto-Approve Bot is running...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped.")
