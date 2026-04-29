import os
import json
import logging
import asyncio
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from aiogram.client.bot import DefaultBotProperties
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, FSInputFile
from aiogram.enums.chat_member_status import ChatMemberStatus
from aiogram.enums.chat_type import ChatType
from aiogram.enums.parse_mode import ParseMode
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# Session login imports
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    FloodWaitError,
    PhoneCodeExpiredError
)

logging.basicConfig(level=logging.WARNING)
logging.getLogger('aiogram').setLevel(logging.WARNING)

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
OWNER_ID = int(os.getenv('OWNER_ID', '0'))  # Add owner ID in .env

if not BOT_TOKEN:
    raise RuntimeError('⚠️ BOT_TOKEN not set in .env')

# Session credentials
API_ID = int(os.getenv('API_ID', '0'))
API_HASH = os.getenv('API_HASH', '')

DATA_DIR = Path('data')
DATA_DIR.mkdir(exist_ok=True)

ACCEPTED_FN = DATA_DIR / 'accepted_users.json'
GROUPS_FN = DATA_DIR / 'groups.json'
LIMITS_FN = DATA_DIR / 'limits.json'
SESSIONS_FN = DATA_DIR / 'sessions.json'

CATBOX_VIDEO_URL = 'https://files.catbox.moe/kg7jcs.mp4'

# FSM States
class LoginStates(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_password = State()

def load_json(path: Path, default=None):
    if not path.exists():
        path.write_text(json.dumps(default or {}, indent=2))
    return json.loads(path.read_text())

def save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2))

groups = load_json(GROUPS_FN, {})
limits = load_json(LIMITS_FN, {})
accepted_users = set(load_json(ACCEPTED_FN, []))
user_sessions = load_json(SESSIONS_FN, {})

# Sessions folder for session files
SESSIONS_DIR = Path('sessions')
SESSIONS_DIR.mkdir(exist_ok=True)

# Rate limiter to avoid bans
class RateLimiter:
    """Simple rate limiter to avoid Telegram bans"""
    def __init__(self, max_calls: int = 30, period: int = 60):
        self.max_calls = max_calls
        self.period = period
        self.calls = []
    
    async def acquire(self):
        """Wait if rate limit exceeded"""
        import time
        now = time.time()
        self.calls = [t for t in self.calls if now - t < self.period]
        if len(self.calls) >= self.max_calls:
            wait_time = self.period - (now - self.calls[0])
            if wait_time > 0:
                await asyncio.sleep(wait_time)
        self.calls.append(time.time())

rate_limiter = RateLimiter()

# Store active login sessions
active_login_sessions = {}


PRIVACY_POLICY_TEXT = (
    "╭━━━━━━━━━━━━━━━━━━━━━━━━━━━━╮\n"
    "┃  🤖 <b>MD AUTO REQUEST</b>\n"
    "┃━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "┃\n"
    "┃  📌 <b>Features:</b>\n"
    "┃  • Auto-join groups/channels\n"
    "┃  • Bulk approve requests\n"
    "┃  • Fast & efficient\n"
    "┃\n"
    "┃  🛡️ <b>Privacy Policy:</b>\n"
    "┃  • No credentials stored\n"
    "┃  • You control target links\n"
    "┃  • No spam/abuse\n"
    "┃\n"
    "┃  👨🏻‍💻 <b>Dev:</b> 『⛥ MD TECH HACKER ⛥』\n"
    "┃\n"
    "╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
)

WELCOME_TEXT = (
    "╭━━━━━━━━━━━━━━━━━━━━━━━━━━━━╮\n"
    "┃  🤖 <b>MD AUTO REQUEST</b>\n"
    "┃━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "┃\n"
    "┃  📌 <b>What this bot does:</b>\n"
    "┃  • Auto-join groups/channels\n"
    "┃  • Bulk approve join requests\n"
    "┃  • Manage access easily\n"
    "┃\n"
    "┃  ⚡ <b>Quick Start:</b>\n"
    "┃  1️⃣ /login - Connect account\n"
    "┃  2️⃣ /approve - Approve requests\n"
    "┃  3️⃣ /cmds - All commands\n"
    "┃\n"
    "┃  👨🏻‍💻 <b>Dev:</b> 『⛥ MD TECH HACKER ⛥』\n"
    "┃  📢 <b>Channel:</b> @MD_TECH_BOTS\n"
    "┃\n"
    "╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
)

APPROVE_WELCOME_TEXT = (
    "╭━━━━━━━━━━━━━━━━━━━━━━━━━━━━╮\n"
    "┃  ⚡ <b>BULK APPROVAL</b>\n"
    "┃━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "┃\n"
    "┃  🔑 <b>Login</b> - Connect account\n"
    "┃  🚪 <b>Logout</b> - Disconnect\n"
    "┃  ✅ <b>Approve</b> - Bulk approve\n"
    "┃\n"
    "┃  📝 <b>Commands:</b>\n"
    "┃  <code>/approve chat_id</code>\n"
    "┃  <code>/approve all chat_id</code>\n"
    "┃  <code>/approve count(n) chat_id</code>\n"
    "┃\n"
    "┃  📢 @MD_TECH_BOTS\n"
    "┃\n"
    "╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯"
)

bot_username_cache = None

async def get_bot_username():
    global bot_username_cache
    if bot_username_cache is None:
        me = await bot.get_me()
        bot_username_cache = me.username
    return bot_username_cache

async def get_welcome_kb() -> InlineKeyboardMarkup:
    username = await get_bot_username()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='➕ Add to Group', url=f'https://t.me/{username}?startgroup=true')],
        [InlineKeyboardButton(text='➕ Add to Channel', url=f'https://t.me/{username}?startchannel=start')],
        [InlineKeyboardButton(text='📢 Join My Channel', url='https://t.me/MD_TECH_BOTS')],
    ])

async def get_approve_kb(user_id: int) -> InlineKeyboardMarkup:
    username = await get_bot_username()
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text='🔑 Login', callback_data='approve_login'),
            InlineKeyboardButton(text='🚪 Logout', callback_data='approve_logout')
        ],
        [
            InlineKeyboardButton(text='✅ Approve All', callback_data='approve_all_info')
        ],
        [
            InlineKeyboardButton(text='👥 Add to Group', url=f'https://t.me/{username}?startgroup=true'),
            InlineKeyboardButton(text='📢 Add to Channel', url=f'https://t.me/{username}?startchannel=start')
        ]
    ])

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML))

dp = Dispatcher(storage=MemoryStorage())

def save_state():
    save_json(ACCEPTED_FN, list(accepted_users))
    save_json(GROUPS_FN, groups)
    save_json(LIMITS_FN, limits)
    save_json(SESSIONS_FN, user_sessions)

async def is_chat_admin(chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
    except Exception:
        return False

async def get_chat_name(chat_id: int) -> str:
    cid = str(chat_id)
    if cid in groups:
        return groups[cid]
    try:
        chat = await bot.get_chat(chat_id)
        title = chat.title or str(chat_id)
        groups[cid] = title
        save_state()
        return title
    except Exception:
        return str(chat_id)

REQUIRED_CHANNEL = os.getenv('REQUIRED_CHANNEL', '')

async def is_member_of_channel(user_id: int, channel_username: str = None) -> bool:
    channel_username = channel_username or REQUIRED_CHANNEL
    if not channel_username:
        return True
    try:
        member = await bot.get_chat_member(chat_id=channel_username, user_id=user_id)
        return member.status != 'left'
    except Exception:
        return False

async def get_user_client(user_id: int):
    """Get user's Telethon client if session exists"""
    user_session = user_sessions.get(str(user_id))
    if not user_session or not user_session.get('is_active'):
        return None
    
    session_string = user_session.get('session_string')
    if not session_string:
        return None
    
    try:
        client = TelegramClient(
            StringSession(session_string),
            API_ID,
            API_HASH,
            device_model="MD Auto Request",
            system_version="1.0",
            app_version="1.0"
        )
        await client.connect()
        if await client.is_user_authorized():
            return client
        else:
            await client.disconnect()
            return None
    except Exception as e:
        logging.error(f"Client connection error for user {user_id}: {e}")
        return None

@dp.my_chat_member()
async def track_chat(evt: types.ChatMemberUpdated):
    if evt.new_chat_member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
        cid = str(evt.chat.id)
        groups[cid] = evt.chat.title or cid
        save_state()

def send_welcome_to_user(user_id: int):
    async def _send():
        gif = CATBOX_VIDEO_URL
        kb = await get_welcome_kb()
        await bot.send_video(
            chat_id=user_id,
            video=gif,
            caption=WELCOME_TEXT,
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
        )
    return asyncio.create_task(_send())

@dp.message(CommandStart(deep_link=True))
@dp.message(CommandStart())
async def cmd_start(msg: types.Message):
    if msg.chat.type != ChatType.PRIVATE:
        return
    
    uid = msg.from_user.id
    if not await is_member_of_channel(uid):
        await msg.answer(
            "❌ You must join our channel @MD_TECH_BOTS to use this bot.\n"
            "Please join first and then try again.",
            parse_mode=None,
        )
        return
    
    if uid not in accepted_users:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='✅ Accept & Continue', callback_data='accept_privacy')]
        ])
        await msg.answer(PRIVACY_POLICY_TEXT, reply_markup=kb, parse_mode=None)
    else:
        send_welcome_to_user(uid)

@dp.callback_query(lambda c: c.data == 'accept_privacy')
async def accept_privacy(cb: CallbackQuery):
    uid = cb.from_user.id
    accepted_users.add(uid)
    save_state()
    await cb.answer('✅ Accepted & Continued!')
    await cb.message.delete()
    send_welcome_to_user(uid)

@dp.callback_query(lambda c: c.data == 'login_cancel')
async def login_cancel_callback(cb: CallbackQuery, state: FSMContext):
    """Handle login cancel callback"""
    uid = cb.from_user.id
    
    # Cleanup login session
    session_info = active_login_sessions.pop(uid, None)
    if session_info:
        client = session_info.get('client')
        if client:
            try:
                await client.disconnect()
            except:
                pass
    
    await state.clear()
    await cb.message.edit_text("❌ Login cancelled.", reply_markup=None)
    await cb.answer("Cancelled")

@dp.message(Command('login'))
async def cmd_login(msg: types.Message, state: FSMContext):
    """Handle /login command"""
    if msg.chat.type != ChatType.PRIVATE:
        return
    
    uid = msg.from_user.id
    if not await is_member_of_channel(uid):
        await msg.answer(
            "❌ You must join our channel @MD_TECH_BOTS to use this bot.\n"
            "Please join first and then try again.",
            parse_mode=None,
        )
        return
    
    if str(uid) in user_sessions and user_sessions[str(uid)].get('is_active', False):
        await msg.answer("ℹ️ You are already logged in. Use /logout to logout first.", parse_mode=None)
        return
    
    # Ask for phone number with share button
    phone_keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text='📱 Share Phone Number', request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    
    await msg.answer(
        "📱 Please share your phone number to login:\n\n"
        "\"You can either:\"\n"
        "\"1. Click the '📱 Share Phone Number' button below\"\n"
        "\"2. Or type your phone number in international format (e.g., +1234567890)\"\n\n"
        "👨🏻‍💻 Dev : 『⛥ MD TECH HACKER ⛥』",
        reply_markup=phone_keyboard,
        parse_mode=None
    )
    # Also send an inline cancel button in case they want to cancel easily
    await msg.answer(
        "👇 Click below to cancel logic process:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel Login", callback_data="login_cancel")]])
    )
    
    await state.set_state(LoginStates.waiting_for_phone)

@dp.message(LoginStates.waiting_for_phone)
async def process_phone_number(msg: types.Message, state: FSMContext):
    """Process phone number input"""
    uid = msg.from_user.id
    
    # Check for commands
    if msg.text and msg.text.startswith('/'):
        if msg.text == '/cancel':
            await cmd_cancel(msg, state)
            return
        await state.clear()
        await msg.answer("⚠️ Login cancelled. Please send your command again.")
        return

    # Get phone number
    if msg.contact and msg.contact.phone_number:
        phone_number = msg.contact.phone_number
        if not phone_number.startswith("+"):
            phone_number = "+" + phone_number.lstrip("+")
    elif msg.text:
        phone_text = msg.text.strip()
        if not (phone_text.startswith("+") and phone_text[1:].replace(" ", "").isdigit()):
            await msg.answer(
                "❌ Invalid phone number format.\n"
                "Please use international format, e.g., +1234567890",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode=None
            )
            return
        phone_number = phone_text.replace(" ", "")
    else:
        await msg.answer("❌ Please provide a valid phone number.", parse_mode=None)
        return
    
    status_msg = await msg.answer("🔄 Connecting...", reply_markup=ReplyKeyboardRemove(), parse_mode=None)
    
    try:
        # Create Telethon client and send OTP
        client = TelegramClient(
            StringSession(),
            API_ID,
            API_HASH,
            device_model="MD Auto Request",
            system_version="1.0",
            app_version="1.0"
        )
        
        await client.connect()
        result = await client.send_code_request(phone_number)
        
        # Store client and phone_code_hash for verification
        active_login_sessions[uid] = {
            'client': client,
            'phone_number': phone_number,
            'phone_code_hash': result.phone_code_hash,
            'sent_at': datetime.now(timezone.utc).timestamp()
        }
        
        await state.update_data(phone_number=phone_number)
        await state.set_state(LoginStates.waiting_for_code)
        
        try:
            await status_msg.edit_text(
                f"✅ Code sent to {phone_number}\n\n"
                "📱 Check your Telegram app for the OTP code.\n"
                "Please enter the code you received in format `CODE_12345`:\n\n"
                "👨🏻‍💻 Dev : 『⛥ MD TECH HACKER ⛥』",
                parse_mode=ParseMode.MARKDOWN
            )
        except TelegramBadRequest:
            await bot.send_message(
                uid,
                f"✅ Code sent to {phone_number}\n\n"
                "📱 Check your Telegram app for the OTP code.\n"
                "Please enter the code you received in format `CODE_12345`:\n\n"
                "👨🏻‍💻 Dev : 『⛥ MD TECH HACKER ⛥』",
                parse_mode=ParseMode.MARKDOWN
            )
        
    except PhoneNumberInvalidError:
        try:
            await status_msg.edit_text("❌ Invalid phone number. Please try again with /login")
        except TelegramBadRequest:
            await bot.send_message(uid, "❌ Invalid phone number. Please try again with /login")
        await state.clear()
    except FloodWaitError as e:
        try:
            await status_msg.edit_text(f"❌ Too many attempts. Please wait {e.seconds} seconds and try again.")
        except TelegramBadRequest:
            await bot.send_message(uid, f"❌ Too many attempts. Please wait {e.seconds} seconds and try again.")
        await state.clear()
    except Exception as e:
        logging.error(f"Login error for {uid}: {e}")
        try:
            await status_msg.edit_text(f"❌ Error: {str(e)}")
        except TelegramBadRequest:
            await bot.send_message(uid, f"❌ Error: {str(e)}")
        await state.clear()

@dp.message(LoginStates.waiting_for_code)
async def process_verification_code(msg: types.Message, state: FSMContext):
    """Process OTP verification code"""
    uid = msg.from_user.id
    code_text = msg.text.strip()
    
    # Extract code (e.g., "CODE_12345" -> "12345")
    if '_' in code_text:
        parts = code_text.split('_')
        if len(parts) == 2 and parts[0].upper() == 'CODE':
            code = parts[1]
        else:
            code = code_text.replace('_', '')
    elif code_text.lower().startswith('code'):
        code = code_text[4:].lstrip('_')
    else:
        code = code_text
    
    code = code.replace(" ", "")
    
    if not code.isdigit():
        await msg.answer(
            "❌ Invalid code format.\n"
            "Please enter the code in format `CODE_12345`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    session_info = active_login_sessions.get(uid)
    if not session_info:
        await msg.answer("❌ Session expired. Please start again with /login", parse_mode=None)
        await state.clear()
        return
    
    # Check if code expired (3 minutes)
    sent_at = session_info.get('sent_at', 0)
    if (datetime.now(timezone.utc).timestamp() - sent_at) > 180:
        client = session_info.get('client')
        if client:
            try:
                await client.disconnect()
            except:
                pass
        active_login_sessions.pop(uid, None)
        await msg.answer("❌ Code expired. Please start again with /login", parse_mode=None)
        await state.clear()
        return
    
    status_msg = await msg.answer("🔄 Verifying code...", parse_mode=None)
    
    try:
        client = session_info['client']
        phone = session_info['phone_number']
        phone_code_hash = session_info['phone_code_hash']
        
        try:
            # Attempt sign-in with OTP
            user = await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            
            # Success! Save session
            session_str = client.session.save()
            
            user_sessions[str(uid)] = {
                'session_string': session_str,
                'phone_number': phone,
                'created_at': datetime.now().isoformat(),
                'is_active': True,
                'user_info': {
                    'id': user.id,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'username': user.username
                }
            }
            save_state()
            
            # Cleanup
            active_login_sessions.pop(uid, None)
            await state.clear()
            
            await status_msg.edit_text(
                f"✅ Login Successful!\n\n"
                f"📱 Phone: {phone}\n"
                f"👤 Name: {user.first_name or ''} {user.last_name or ''}\n"
                f"🆔 Username: @{user.username or 'None'}\n\n"
                "You can now use /approve commands.\n\n"
                "👨🏻‍💻 Dev : 『⛥ MD TECH HACKER ⛥』",
                parse_mode=None
            )
            
        except SessionPasswordNeededError:
            # 2FA required
            await state.set_state(LoginStates.waiting_for_password)
            await status_msg.edit_text(
                "🔐 Two-Step Verification\n\n"
                "Please enter your 2FA password in format `PASS_mypassword`:\n\n"
                "⚠️ Your password is case-sensitive.\n\n"
                "👨🏻‍💻 Dev : 『⛥ MD TECH HACKER ⛥』",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except PhoneCodeInvalidError:
            await status_msg.edit_text(
                "❌ Invalid code. Please try again in format `CODE_12345`:",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except PhoneCodeExpiredError:
            try:
                await client.disconnect()
            except:
                pass
            active_login_sessions.pop(uid, None)
            await status_msg.edit_text("❌ Code expired. Please start again with /login")
            await state.clear()
            
    except Exception as e:
        logging.error(f"Verification error for {uid}: {e}")
        await status_msg.edit_text(f"❌ Error: {str(e)}")
        await state.clear()

@dp.message(LoginStates.waiting_for_password)
async def process_2fa_password(msg: types.Message, state: FSMContext):
    """Process 2FA password"""
    uid = msg.from_user.id
    password_text = msg.text.strip()
    
    # Extract password (e.g., "PASS_mypassword" -> "mypassword")
    if '_' in password_text:
        parts = password_text.split('_', 1)
        if len(parts) == 2 and parts[0].upper() == 'PASS':
            password = parts[1]
        else:
            password = password_text.replace('_', '')
    elif password_text.lower().startswith('pass'):
        password = password_text[4:].lstrip('_')
    else:
        password = password_text
    
    session_info = active_login_sessions.get(uid)
    if not session_info:
        await msg.answer("❌ Session expired. Please start again with /login", parse_mode=None)
        await state.clear()
        return
    
    # Delete password message for security
    try:
        await msg.delete()
    except:
        pass
    
    status_msg = await bot.send_message(uid, "🔄 Verifying password...", parse_mode=None)
    
    try:
        client = session_info['client']
        phone = session_info['phone_number']
        
        # Verify 2FA password
        user = await client.sign_in(password=password)
        
        # Success! Save session
        session_str = client.session.save()
        
        user_sessions[str(uid)] = {
            'session_string': session_str,
            'phone_number': phone,
            'created_at': datetime.now().isoformat(),
            'is_active': True,
            'user_info': {
                'id': user.id,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'username': user.username
            }
        }
        save_state()
        
        # Cleanup
        active_login_sessions.pop(uid, None)
        await state.clear()
        
        await status_msg.edit_text(
            f"✅ Login Successful!\n\n"
            f"📱 Phone: {phone}\n"
            f"👤 Name: {user.first_name or ''} {user.last_name or ''}\n"
            f"🆔 Username: @{user.username or 'None'}\n"
            f"🔒 2FA: Enabled\n\n"
            "You can now use /approve commands.\n\n"
            "👨🏻‍💻 Dev : 『⛥ MD TECH HACKER ⛥』",
            parse_mode=None
        )
        
    except Exception as e:
        logging.error(f"2FA error for {uid}: {e}")
        await status_msg.edit_text(
            "❌ Invalid password. Please try again in format `PASS_mypassword`\n\n"
            "If you forgot your password, please /cancel and restart with /login",
            parse_mode=ParseMode.MARKDOWN
        )

@dp.message(Command('logout'))
async def cmd_logout(msg: types.Message, state: FSMContext):
    """Handle /logout command"""
    if msg.chat.type != ChatType.PRIVATE:
        return
    
    uid = msg.from_user.id
    await state.clear()
    
    if not await is_member_of_channel(uid):
        await msg.answer(
            "❌ You must join our channel @MD_TECH_BOTS to use this bot.\n"
            "Please join first and then try again.",
            parse_mode=None,
        )
        return
    
    if str(uid) in user_sessions:
        # Disconnect client if active
        client = await get_user_client(uid)
        if client:
            try:
                await client.disconnect()
            except:
                pass
        
        user_sessions[str(uid)]['is_active'] = False
        save_state()
        await msg.answer(
            "🚪 Logged out successfully!\n\n"
            "👨🏻‍💻 Dev : 『⛥ MD TECH HACKER ⛥』",
            parse_mode=None
        )
    else:
        await msg.answer("ℹ️ You are not logged in.", parse_mode=None)

@dp.message(Command('cancel'))
async def cmd_cancel(msg: types.Message, state: FSMContext):
    """Cancel any ongoing operation"""
    uid = msg.from_user.id
    
    # Cleanup login session
    session_info = active_login_sessions.pop(uid, None)
    if session_info:
        client = session_info.get('client')
        if client:
            try:
                await client.disconnect()
            except:
                pass
    
    await state.clear()
    await msg.answer("❌ Operation cancelled.", parse_mode=None)

@dp.message(Command('export'))
async def cmd_export(msg: types.Message):
    """Export all data files - Owner only"""
    if msg.from_user.id != OWNER_ID:
        await msg.answer("❌ This command is for owner only.", parse_mode=None)
        return
    
    try:
        await msg.answer("📦 Exporting data files...", parse_mode=None)
        
        # Send all data files
        files_to_export = [
            (ACCEPTED_FN, "Accepted Users"),
            (GROUPS_FN, "Groups"),
            (LIMITS_FN, "Limits/Modes"),
            (SESSIONS_FN, "User Sessions")
        ]
        
        for file_path, file_desc in files_to_export:
            if file_path.exists():
                try:
                    await bot.send_document(
                        msg.from_user.id,
                        FSInputFile(file_path),
                        caption=f"📄 {file_desc}\n👨🏻‍💻 Dev : 『⛥ MD TECH HACKER ⛥』"
                    )
                except Exception as e:
                    logging.error(f"Error sending {file_desc}: {e}")
        
        await msg.answer(
            "✅ Data export completed!\n\n"
            "👨🏻‍💻 Dev : 『⛥ MD TECH HACKER ⛥』",
            parse_mode=None
        )
    
    except Exception as e:
        await msg.answer(f"❌ Export error: {str(e)}", parse_mode=None)

@dp.message(Command('approve'))
async def cmd_approve(msg: types.Message):
    if msg.chat.type != ChatType.PRIVATE:
        return
    
    uid = msg.from_user.id
    if not await is_member_of_channel(uid):
        await msg.answer(
            "❌ You must join our channel @MD_TECH_BOTS to use this bot.\n"
            "Please join first and then try again.",
            parse_mode=None,
        )
        return
    
    # Parse command arguments
    args = msg.text.split()[1:] if len(msg.text.split()) > 1 else []
    
    if args and len(args) >= 2:
        mode = args[0]
        try:
            chat_id = int(args[1])
            
            if mode == 'all':
                await handle_approve_all(msg, chat_id, uid)
                return
            elif mode.startswith('count(') and mode.endswith(')'):
                count = int(mode[6:-1])
                await handle_approve_count(msg, chat_id, count, uid)
                return
        except ValueError:
            await msg.answer("❌ Invalid chat ID or count number.", parse_mode=None)
            return
    elif args and len(args) == 1:
        # Just chat_id provided, show UI for that chat
        try:
            chat_id = int(args[0])
            chat_name = await get_chat_name(chat_id)
            
            kb = await get_approve_kb(uid)
            is_logged_in = str(uid) in user_sessions and user_sessions[str(uid)].get('is_active', False)
            status_text = "🟢 Status: Logged In" if is_logged_in else "🔴 Status: Not Logged In"
            
            # Use HTML to avoid markdown parsing errors
            caption = (
                f"{APPROVE_WELCOME_TEXT}\n\n"
                f"📊 Target Chat: {chat_name}\n"
                f"🆔 Chat ID: {chat_id}\n\n"
                f"{status_text}"
            )
            
            await bot.send_video(
                chat_id=uid,
                video=CATBOX_VIDEO_URL,
                caption=caption,
                reply_markup=kb,
                parse_mode=None
            )
            return
        except ValueError:
            pass
    
    # Show approve UI with photo
    kb = await get_approve_kb(uid)
    
    # Check login status
    is_logged_in = str(uid) in user_sessions and user_sessions[str(uid)].get('is_active', False)
    status_text = "🟢 Status: Logged In" if is_logged_in else "🔴 Status: Not Logged In"
    
    await bot.send_video(
        chat_id=uid,
        video=CATBOX_VIDEO_URL,
        caption=APPROVE_WELCOME_TEXT + f"\n\n{status_text}",
        reply_markup=kb,
        parse_mode=None
    )

async def handle_approve_all(msg: types.Message, chat_id: int, user_id: int):
    """Handle /approve all <chat_id> command"""
    try:
        client = await get_user_client(user_id)
        if not client:
            await msg.answer("❌ Please login first using /login command.", parse_mode=None)
            return
        
        status_msg = await msg.answer("🔄 Processing requests...", parse_mode=None)
        
        try:
            from telethon.tl.functions.messages import GetChatInviteImportersRequest
            from telethon.tl.functions.messages import HideChatJoinRequestRequest
            from telethon.tl.types import InputUserEmpty
            
            chat = await client.get_entity(chat_id)
            
            # Get pending join requests with required parameters
            result = await client(GetChatInviteImportersRequest(
                peer=chat,
                requested=True,
                offset_date=0,
                offset_user=InputUserEmpty(),
                limit=100
            ))
            
            approved_count = 0
            for importer in result.importers:
                try:
                    await client(HideChatJoinRequestRequest(
                        peer=chat,
                        user_id=importer.user_id,
                        approved=True
                    ))
                    approved_count += 1
                    await asyncio.sleep(0.5)  # Rate limit protection
                except Exception as e:
                    logging.warning(f"Failed to approve user: {e}")
                    continue
            
            chat_name = await get_chat_name(chat_id)
            await status_msg.edit_text(
                f"✅ Approved {approved_count} members in {chat_name}\n\n"
                f"👨🏻‍💻 Dev : 『⛥ MD TECH HACKER ⛥』"
            )
            
        except Exception as e:
            await status_msg.edit_text(f"❌ Error accessing chat: {str(e)}")
            logging.error(f"Approve all error: {e}")
        
        await client.disconnect()
        
    except Exception as e:
        await msg.answer(f"❌ Error: {str(e)}", parse_mode=None)
        logging.error(f"Handle approve all error: {e}")

async def handle_approve_count(msg: types.Message, chat_id: int, count: int, user_id: int):
    """Handle /approve count(n) <chat_id> command"""
    try:
        client = await get_user_client(user_id)
        if not client:
            await msg.answer("❌ Please login first using /login command.", parse_mode=None)
            return
        
        status_msg = await msg.answer(f"🔄 Processing {count} requests...", parse_mode=None)
        
        try:
            from telethon.tl.functions.messages import GetChatInviteImportersRequest
            from telethon.tl.functions.messages import HideChatJoinRequestRequest
            from telethon.tl.types import InputUserEmpty
            
            chat = await client.get_entity(chat_id)
            
            # Get pending join requests with required parameters
            result = await client(GetChatInviteImportersRequest(
                peer=chat,
                requested=True,
                offset_date=0,
                offset_user=InputUserEmpty(),
                limit=count
            ))
            
            approved_count = 0
            for importer in result.importers[:count]:
                try:
                    await client(HideChatJoinRequestRequest(
                        peer=chat,
                        user_id=importer.user_id,
                        approved=True
                    ))
                    approved_count += 1
                    await asyncio.sleep(0.5)  # Rate limit protection
                except Exception as e:
                    logging.warning(f"Failed to approve user: {e}")
                    continue
            
            chat_name = await get_chat_name(chat_id)
            await status_msg.edit_text(
                f"✅ Approved {approved_count}/{count} members in {chat_name}\n\n"
                f"👨🏻‍💻 Dev : 『⛥ MD TECH HACKER ⛥』"
            )
            
        except Exception as e:
            await status_msg.edit_text(f"❌ Error accessing chat: {str(e)}")
            logging.error(f"Approve count error: {e}")
        
        await client.disconnect()
        
    except Exception as e:
        await msg.answer(f"❌ Error: {str(e)}", parse_mode=None)
        logging.error(f"Handle approve count error: {e}")

@dp.callback_query(lambda c: c.data.startswith('approve_'))
async def handle_approve_callbacks(cb: CallbackQuery, state: FSMContext):
    uid = cb.from_user.id
    data = cb.data
    
    if data == 'approve_login':
        await cb.answer()
        
        # Check if already logged in
        if str(uid) in user_sessions and user_sessions[str(uid)].get('is_active', False):
            await cb.answer("ℹ️ You are already logged in!", show_alert=True)
            return
        
        # Ask for phone number
        phone_keyboard = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text='📱 Share Phone Number', request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        
        await bot.send_message(
            uid,
            "📱 Please share your phone number to login:\n\n"
            "\"You can either:\"\n"
            "\"1. Click the '📱 Share Phone Number' button below\"\n"
            "\"2. Or type your phone number in international format (e.g., +1234567890)\"\n\n"
            "👨🏻‍💻 Dev : 『⛥ MD TECH HACKER ⛥』",
            reply_markup=phone_keyboard
        )
        
        await state.set_state(LoginStates.waiting_for_phone)
    
    elif data == 'approve_logout':
        await state.clear()
        if str(uid) in user_sessions:
            # Disconnect client
            client = await get_user_client(uid)
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
            
            user_sessions[str(uid)]['is_active'] = False
            save_state()
            kb = await get_approve_kb(uid)
            await cb.message.edit_caption(
                caption=APPROVE_WELCOME_TEXT + "\n\n🔴 Status: Logged Out",
                reply_markup=kb,
                parse_mode=None
            )
            await cb.answer("✅ Logged out!")
        else:
            await cb.answer("ℹ️ You are not logged in.")
    
    elif data == 'approve_all_info':
        await cb.answer(
            "📝 Commands:\n\n"
            "/approve (chat_id) — Show approval UI\n"
            "/approve all (chat_id) — Approve all\n"
            "/approve count(10) (chat_id) — Approve 10", 
            show_alert=True
        )

@dp.message(Command('cmds'))
async def cmds(msg: types.Message):
    uid = msg.from_user.id
    if not await is_member_of_channel(uid):
        await msg.answer(
            "❌ You must join our channel @MD_TECH_BOTS to use this bot.\n"
            "Please join first and then try again.",
            parse_mode=None,
        )
        return
    
    help_text = (
        "⚡ 𝗠𝗗 𝗔𝗨𝗧𝗢 𝗥𝗘𝗤𝗨𝗘𝗦𝗧 - 𝗖𝗼𝗺𝗺𝗮𝗻𝗱𝘀\n\n"
        "👤 𝗣𝗿𝗶𝘃𝗮𝘁𝗲 𝗖𝗼𝗺𝗺𝗮𝗻𝗱𝘀:\n"
        "• /start — Start the bot & accept privacy terms\n"
        "• /cmds — Show this help message\n"
        "• /login — Login to your account for approval feature\n"
        "• /logout — Logout from your account\n"
        "• /cancel — Cancel any ongoing operation\n"
        "• /export — Export all data (Owner only)\n\n"
        "⚡ 𝗔𝗽𝗽𝗿𝗼𝘃𝗮𝗹 𝗖𝗼𝗺𝗺𝗮𝗻𝗱𝘀:\n"
        "• /approve all (chat_id) — Approve all pending requests\n"
        "• /approve count(n) (chat_id) — Approve n members\n\n"
        "👥 𝗚𝗿𝗼𝘂𝗽 𝗖𝗼𝗺𝗺𝗮𝗻𝗱𝘀 (𝗔𝗱𝗺𝗶𝗻𝘀 𝗢𝗻𝗹𝘆):\n"
        "• /set_mode (mode) [value] — Configure mode\n"
        "  » limit (n) — Approve up to N users, then stop\n"
        "  » count (n) — Queue requests; approve when reaching N\n"
        "  » time (HH:MM) — Approve queued requests at IST time\n"
        "  » immediate — Approve everyone instantly\n"
        "• /reset — Reset all modes and counters\n"
        "• /status — Show mode status for group\n\n"
        "💡 𝗙𝗼𝗿𝗺𝗮𝘁 𝗘𝘅𝗮𝗺𝗽𝗹𝗲𝘀:\n"
        "• CODE_12345 for OTP verification\n"
        "• PASS_mypassword for 2FA password\n\n"
        "👨🏻‍💻 Dev : 『⛥ MD TECH HACKER ⛥』\n"
        "📢 Join: @MD_TECH_BOTS"
    )
    
    await msg.answer(help_text, parse_mode=None)

def escape_md(text: str) -> str:
    escape_chars = r'\_*[]()~`>#+-=|{}.!'
    return ''.join('\\' + c if c in escape_chars else c for c in text)

async def safe_approve_user(chat_id: int, user_id: int) -> bool:
    try:
        await bot.approve_chat_join_request(chat_id, user_id)
        return True
    except TelegramForbiddenError:
        return False
    except Exception as e:
        if hasattr(e, 'description') and 'USER_ALREADY_PARTICIPANT' in e.description:
            return False
        logging.warning(f"Approval error user {user_id} chat {chat_id}: {e}")
        return False

async def process_pending_requests(chat_id: int, cfg: dict):
    try:
        pending_requests = await bot.get_chat_join_requests(chat_id)
    except Exception:
        return
    
    if not pending_requests:
        return
    
    mode = cfg.get('mode')
    
    if mode == 'immediate':
        for req in pending_requests:
            approved = await safe_approve_user(chat_id, req.from_user.id)
            if approved:
                cfg['count'] = cfg.get('count', 0) + 1
                await send_approval_message(req)
    
    elif mode == 'limit':
        for req in pending_requests:
            if cfg.get('count', 0) < cfg.get('limit', 0):
                approved = await safe_approve_user(chat_id, req.from_user.id)
                if approved:
                    cfg['count'] = cfg.get('count', 0) + 1
                    await send_approval_message(req)
            else:
                break
    
    elif mode == 'count':
        pend = cfg.setdefault('pending', [])
        for req in pending_requests:
            if req.from_user.id not in pend:
                pend.append(req.from_user.id)
        
        if len(pend) >= cfg.get('limit', 0):
            for uid in pend:
                approved = await safe_approve_user(chat_id, uid)
            pend.clear()
            save_state()
    
    elif mode == 'time':
        pend = cfg.setdefault('pending', [])
        for req in pending_requests:
            if req.from_user.id not in pend:
                pend.append(req.from_user.id)
        save_state()

async def send_approval_message(req: types.ChatJoinRequest):
    chat_title = await get_chat_name(req.chat.id)
    name = req.from_user.full_name
    text = (
        f"Hey {name}!!\n"
        f"Your request to join {chat_title} has been approved.\n"
        "Join Request approval Services.\n"
        "Click /start for more info."
    )
    
    try:
        await bot.send_message(req.from_user.id, text, parse_mode=None)
    except TelegramForbiddenError:
        pass

@dp.message(Command('set_mode'))
async def set_mode(msg: types.Message):
    parts = msg.text.strip().split()[1:]
    
    # Check if command is in group/channel
    if msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
        if not await is_chat_admin(msg.chat.id, msg.from_user.id):
            await msg.answer('❌ Only admins can configure modes.', parse_mode=None)
            return
        
        if len(parts) < 1:
            await msg.answer(
                '⚡ 𝗦𝗲𝘁 𝗠𝗼𝗱𝗲 - 𝗨𝘀𝗮𝗴𝗲\n\n'
                'Usage: /set_mode (mode) [value]\n\n'
                '📝 Available Modes:\n'
                '• limit (n) — Approve up to N users, then stop\n'
                '• count (n) — Queue requests; approve when reaching N\n'
                '• time (HH:MM) — Approve queued requests at IST time\n'
                '• immediate — Approve everyone instantly\n\n'
                '💡 Examples:\n'
                '• /set_mode limit 50\n'
                '• /set_mode count 10\n'
                '• /set_mode time 14:30\n'
                '• /set_mode immediate',
                parse_mode=None
            )
            return
        
        target_id_str = str(msg.chat.id)
        mode_arg = parts[0]
        args = parts[1:]
    
    # Command in private chat
    else:
        if len(parts) < 2:
            await msg.answer(
                '⚡ 𝗦𝗲𝘁 𝗠𝗼𝗱𝗲 - 𝗨𝘀𝗮𝗴𝗲\n\n'
                'Usage: /set_mode (chat_id) (mode) [value]\n\n'
                '📝 Available Modes:\n'
                '• limit (n) — Approve up to N users, then stop\n'
                '• count (n) — Queue requests; approve when reaching N\n'
                '• time (HH:MM) — Approve queued requests at IST time\n'
                '• immediate — Approve everyone instantly\n\n'
                '💡 Examples:\n'
                '• /set_mode -1001234567890 limit 50\n'
                '• /set_mode -1001234567890 count 10\n'
                '• /set_mode -1001234567890 time 14:30\n'
                '• /set_mode -1001234567890 immediate',
                parse_mode=None
            )
            return
        
        target_id_str, mode_arg = parts[0], parts[1]
        args = parts[2:]
        
        try:
            target_id = int(target_id_str)
        except ValueError:
            await msg.answer('❌ Invalid chat_id. Must be an integer.', parse_mode=None)
            return
        
        if not await is_chat_admin(target_id, msg.from_user.id):
            await msg.answer("❌ You must be admin in the target chat to set mode.", parse_mode=None)
            return
        
        target_id_str = str(target_id)
    
    cfg = None
    
    if mode_arg == 'limit':
        if not args or not args[0].isdigit():
            await msg.answer('❌ Usage: /set_mode limit (number)', parse_mode=None)
            return
        cfg = {'mode': 'limit', 'limit': int(args[0]), 'count': 0}
    
    elif mode_arg == 'count':
        if not args or not args[0].isdigit():
            await msg.answer('❌ Usage: /set_mode count (number)', parse_mode=None)
            return
        cfg = {'mode': 'count', 'limit': int(args[0]), 'pending': []}
    
    elif mode_arg == 'time':
        if not args or not re.match(r'^\d{1,2}:\d{2}$', args[0]):
            await msg.answer('❌ Usage: /set_mode time (HH:MM) IST', parse_mode=None)
            return
        cfg = {'mode': 'time', 'end_time': args[0], 'pending': [], 'last_approved_time': None}
    
    elif mode_arg == 'immediate':
        cfg = {'mode': 'immediate', 'count': 0}
    
    else:
        await msg.answer('❌ Invalid mode. Available: limit, count, time, immediate.', parse_mode=None)
        return
    
    limits[target_id_str] = cfg
    save_state()
    
    if cfg['mode'] != 'time':
        await process_pending_requests(int(target_id_str), cfg)
    
    chat_name = await get_chat_name(int(target_id_str))
    resp = f"✅ Mode set to {mode_arg} for \"{chat_name}\""
    if 'limit' in cfg:
        resp += f": {cfg['limit']}"
    if 'end_time' in cfg:
        resp += f" until {cfg['end_time']} IST"
    
    await msg.answer(resp, parse_mode=None)

@dp.message(Command('reset'))
async def reset_command(msg: types.Message):
    parts = msg.text.strip().split()
    
    # Check if command is in group/channel
    if msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
        if not await is_chat_admin(msg.chat.id, msg.from_user.id):
            await msg.answer("❌ Only admins can reset modes.", parse_mode=None)
            return
        
        chat_id_str = str(msg.chat.id)
    
    # Command in private chat
    else:
        if len(parts) < 2:
            await msg.answer(
                "⚡ 𝗥𝗲𝘀𝗲𝘁 𝗠𝗼𝗱𝗲 - 𝗨𝘀𝗮𝗴𝗲\n\n"
                "Usage: /reset (chat_id)\n\n"
                "💡 Example:\n"
                "• /reset -1001234567890",
                parse_mode=None
            )
            return
        
        try:
            chat_id_int = int(parts[1])
            chat_id_str = str(chat_id_int)
        except ValueError:
            await msg.answer("❌ Invalid chat ID.", parse_mode=None)
            return
        
        try:
            if not await is_chat_admin(chat_id_int, msg.from_user.id):
                await msg.answer("❌ You must be admin in the target chat to reset modes.", parse_mode=None)
                return
        except Exception:
            await msg.answer("❌ Could not verify admin status. Are you admin in that chat?", parse_mode=None)
            return
    
    if chat_id_str in limits:
        limits.pop(chat_id_str)
        save_state()
        
        chat_name = await get_chat_name(int(chat_id_str))
        await msg.answer(f"🔄 Mode reset for chat \"{chat_name}\"", parse_mode=None)
    else:
        await msg.answer("ℹ️ No mode set for this chat.", parse_mode=None)
    
    dummy_cfg = {'mode': 'immediate'}
    await process_pending_requests(int(chat_id_str), dummy_cfg)

@dp.message(Command('status'))
async def status_command(msg: types.Message):
    parts = msg.text.strip().split()
    
    # Check if command is in group/channel
    if msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
        if not await is_chat_admin(msg.chat.id, msg.from_user.id):
            await msg.answer("❌ Only admins can check status.", parse_mode=None)
            return
        
        cid = str(msg.chat.id)
    
    # Command in private chat
    else:
        if len(parts) < 2:
            await msg.answer(
                "⚡ 𝗦𝘁𝗮𝘁𝘂𝘀 - 𝗨𝘀𝗮𝗴𝗲\n\n"
                "Usage: /status (chat_id)\n\n"
                "💡 Example:\n"
                "• /status -1001234567890",
                parse_mode=None
            )
            return
        
        cid = parts[1]
        try:
            int(cid)
        except ValueError:
            await msg.answer("❌ Invalid chat ID.", parse_mode=None)
            return
        
        try:
            if not await is_chat_admin(int(cid), msg.from_user.id):
                await msg.answer("❌ You must be admin in the target chat to check status.", parse_mode=None)
                return
        except Exception:
            await msg.answer("❌ Could not verify admin status. Are you admin in that chat?", parse_mode=None)
            return
    
    cfg = limits.get(cid)
    if not cfg:
        await msg.answer('ℹ️ No mode set for this chat.', parse_mode=None)
        return
    
    chat_name = await get_chat_name(int(cid))
    text = f"📊 𝗠𝗼𝗱𝗲 𝗦𝘁𝗮𝘁𝘂𝘀\n\nChat: \"{chat_name}\"\nMode: {cfg['mode']}"
    
    if cfg['mode'] == 'limit':
        text += f"\nProgress: {cfg.get('count',0)}/{cfg['limit']}"
    elif cfg['mode'] == 'count':
        text += f"\nPending: {len(cfg.get('pending',[]))}/{cfg['limit']}"
    elif cfg['mode'] == 'time':
        text += f"\nEnd Time: {cfg['end_time']} IST"
    
    await msg.answer(text, parse_mode=None)

@dp.chat_join_request()
async def handle_join_request(req: types.ChatJoinRequest):
    uid = req.from_user.id
    cid = str(req.chat.id)
    cfg = limits.get(cid, {})
    mode = cfg.get('mode')
    
    approved = False
    
    if mode == 'immediate':
        approved = await safe_approve_user(req.chat.id, uid)
        if approved:
            cfg['count'] = cfg.get('count', 0) + 1
    
    elif mode == 'limit' and cfg.get('count', 0) < cfg.get('limit', 0):
        approved = await safe_approve_user(req.chat.id, uid)
        if approved:
            cfg['count'] = cfg.get('count', 0) + 1
    
    elif mode == 'count':
        pend = cfg.setdefault('pending', [])
        pend.append(uid)
        
        if len(pend) >= cfg.get('limit', 0):
            for uid_ in pend:
                await safe_approve_user(req.chat.id, uid_)
            pend.clear()
            save_state()
    
    elif mode == 'time':
        pend = cfg.setdefault('pending', [])
        pend.append(uid)
        save_state()
    
    if approved:
        chat_title = await get_chat_name(req.chat.id)
        name = req.from_user.full_name
        approval_text = (
            f"Hey {name}!!\n"
            f"Your request to join {chat_title} has been approved.\n"
            "Join Request approval Services.\n"
            "Click /start for more info."
        )
        
        try:
            await bot.send_message(uid, approval_text)
        except TelegramForbiddenError:
            pass

async def time_mode_loop():
    while True:
        try:
            now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
            current_hm = now.strftime("%H:%M")
            
            for chat_id_str, cfg in limits.items():
                if cfg.get('mode') == 'time':
                    last_approved = cfg.get('last_approved_time')
                    if current_hm >= cfg['end_time']:
                        if last_approved != current_hm:
                            pend = cfg.get('pending', [])
                            for uid in pend:
                                try:
                                    await safe_approve_user(int(chat_id_str), uid)
                                except Exception:
                                    pass
                            
                            cfg['pending'] = []
                            cfg['last_approved_time'] = current_hm
                            save_state()
        
        except Exception as e:
            logging.warning(f"Error in time_mode_loop: {e}")
        
        await asyncio.sleep(60)

async def main():
    print('🤖 MD AUTO REQUEST BOT is started!')
    print('👨🏻‍💻 Dev : 『⛥ MD TECH HACKER ⛥』')
    
    await get_bot_username()
    
    for chat_id_str, cfg in limits.items():
        try:
            if cfg.get('mode') != 'time':
                await process_pending_requests(int(chat_id_str), cfg)
        except Exception as e:
            logging.warning(f"Error processing pending requests for chat {chat_id_str}: {e}")
    
    asyncio.create_task(time_mode_loop())
    
    # Register command menu
    from aiogram.types import BotCommand
    commands = [
        BotCommand(command="start", description="🏠 Start the bot"),
        BotCommand(command="cmds", description="📋 All commands"),
        BotCommand(command="login", description="🔑 Login account"),
        BotCommand(command="logout", description="🚪 Logout account"),
        BotCommand(command="approve", description="✅ Approve join requests"),
        BotCommand(command="limits", description="⚙️ View/set limits"),
        BotCommand(command="cancel", description="❌ Cancel operation"),
    ]
    await bot.set_my_commands(commands)
    print("✅ Bot commands menu registered!")
    
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
