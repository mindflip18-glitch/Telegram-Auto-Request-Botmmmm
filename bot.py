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

# --- FSM STATES ---
class MsgSetup(StatesGroup):
    waiting_for_forward = State()
    waiting_for_text = State()

WELCOME_TEXT = (
    "╭━━━━━━━━━━━━━━━━━━━━━━━━━━━━╮\n"
    "┃  🤖 <b>SAFE AUTO REQUEST BOT</b>\n"
    "┃━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "┃\n"
    "┃  📌 <b>Commands:</b>\n"
    "┃  /help - Learn how to use this bot\n"
    "┃  /setwelcome - Set welcome message\n"
    "┃  /setleft - Set goodbye message\n"
    "┃  /offwelcome - Turn OFF welcome\n"
    "┃  /offleft - Turn OFF goodbye\n"
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

# 👇 HELP COMMAND
@dp.message(Command("help"))
async def cmd_help(msg: types.Message):
    if msg.chat.type != "private": return
    help_text = (
        "📖 <b>How to Setup Custom Messages:</b>\n\n"
        "<b>1. To SET a message:</b>\n"
        "• Send <code>/setwelcome</code> or <code>/setleft</code>\n"
        "• Forward any message from your channel to this bot.\n"
        "• Type and send your new custom text message.\n\n"
        "<b>2. To Turn OFF a message:</b>\n"
        "• Send <code>/offwelcome</code> or <code>/offleft</code>\n"
        "• Forward any message from your channel to this bot.\n\n"
        "<i>Note: You must be an Admin in the channel, and the bot must also be added as an Admin with invite permissions.</i>"
    )
    await msg.answer(help_text)

# 👇 STEP 1: Command trigger (Set & Off)
@dp.message(Command("setleft", "setwelcome", "offleft", "offwelcome"))
async def start_setting_msg(msg: types.Message, state: FSMContext):
    if msg.chat.type != "private": return
    
    cmd = msg.text.split()[0]
    msg_type = "left_msg" if cmd in ["/setleft", "/offleft"] else "welcome_msg"
    action = "off" if cmd.startswith("/off") else "set"
    
    await state.update_data(msg_type=msg_type, action=action)
    await state.set_state(MsgSetup.waiting_for_forward)
    
    await msg.answer("📢 Please <b>Forward</b> any message from your Channel here.")

# 👇 STEP 2: Forwarded message check
@dp.message(MsgSetup.waiting_for_forward)
async def process_forwarded_msg(msg: types.Message, state: FSMContext):
    if not msg.forward_origin or msg.forward_origin.type != 'channel':
        await msg.answer("❌ This is not a forwarded message from a channel. Please try again or send /cancel.")
        return

    channel_id = str(msg.forward_origin.chat.id)
    channel_title = msg.forward_origin.chat.title

    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=msg.from_user.id)
        if member.status not in ['administrator', 'creator']:
            await msg.answer("❌ You are not an Admin of this channel!")
            return
    except Exception:
        await msg.answer("❌ Please make the bot an Admin in your channel first.")
        return

    data = await state.get_data()
    msg_type = data['msg_type']
    action = data['action']
    msg_type_text = "Left" if msg_type == "left_msg" else "Welcome"

    if channel_id not in chat_settings:
        chat_settings[channel_id] = {}

    if action == "off":
        chat_settings[channel_id][msg_type] = "OFF"
        save_settings(chat_settings)
        await msg.answer(f"✅ The {msg_type_text} Message for '{channel_title}' has been turned <b>OFF</b>.")
        await state.clear()
        return

    await state.update_data(channel_id=channel_id, channel_title=channel_title)
    await state.set_state(MsgSetup.waiting_for_text)
    await msg.answer(f"✅ Channel verified: <b>{channel_title}</b>\n\n📝 Now, type and send your new <b>{msg_type_text} Message</b>.")

# 👇 STEP 3: Save custom text
@dp.message(MsgSetup.waiting_for_text)
async def process_custom_msg(msg: types.Message, state: FSMContext):
    if not msg.text: 
        await msg.answer("❌ Please send text only.")
        return

    data = await state.get_data()
    channel_id = data['channel_id']
    msg_type = data['msg_type']
    
    chat_settings[channel_id][msg_type] = msg.html_text 
    save_settings(chat_settings)

    await msg.answer(f"✅ Message successfully set:\n\n{msg.html_text}")
    await state.clear()

@dp.message(Command("cancel"))
async def cmd_cancel(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer("❌ Process cancelled.")

# 👇 AUTO APPROVE & WELCOME MSG
@dp.chat_join_request()
async def auto_approve_join_request(update: types.ChatJoinRequest):
    user_id = update.from_user.id
    chat_id = str(update.chat.id)
    
    welcome_msg = chat_settings.get(chat_id, {}).get('welcome_msg')
    if welcome_msg and welcome_msg != "OFF":
        try:
            await bot.send_message(chat_id=user_id, text=welcome_msg)
        except Exception:
            pass 
    
    try:
        await update.approve()
    except Exception as e:
        logging.error(f"Failed to approve: {e}")

# 👇 LEFT MSG
@dp.chat_member()
async def on_chat_member_update(update: types.ChatMemberUpdated):
    user = update.from_user
    chat_id = str(update.chat.id)

    if update.old_chat_member.status in ['member', 'administrator'] and update.new_chat_member.status in ['left', 'kicked']:
        default_left = "🌟 ALL DRAMA DIRECT FILES AVAILABLE 🗃️\n\nhttps://t.me/+amS1Q3R4_Qg5NjU1\nhttps://t.me/+amS1Q3R4_Qg5NjU1"
        
        final_msg = chat_settings.get(chat_id, {}).get('left_msg', default_left)
        
        if final_msg != "OFF":
            try:
                await bot.send_message(chat_id=user.id, text=final_msg)
            except Exception:
                pass 

# 👇 SERVER
async def handle_ping(request): return web.Response(text="Running!")
async def start_dummy_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 8080)))
    await site.start()

async def main():
    await start_dummy_server()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
