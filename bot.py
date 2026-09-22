import os
import logging
import asyncio
import time
import random
import re
import base64
import aiohttp
from aiohttp import web
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, idle, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery, ChatJoinRequest, ChatMemberUpdated
from pyrogram.errors import FloodWait, UserNotParticipant, ChatAdminRequired

# --- LOGGING & CONFIG ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
API_ID = os.getenv('API_ID', '0')      
API_HASH = os.getenv('API_HASH', '')    
PORT = int(os.environ.get("PORT", 10000))

# Destination Group ID jahan link send hoga (Group 2)
BYPASS_DEST_GROUP = os.getenv('BYPASS_DEST_GROUP', '-1003746599873') 

# ⚠️ LINKS AUR BOT USERNAME
FILE_CAPTION_LINK = "https://t.me/+rG8nfdrvV2FlN2M1"       
UPDATE_CHANNEL_LINK = "https://t.me/+rG8nfdrvV2FlN2M1"   
BOT_USERNAME = "KDL143bot" 

# ⚠️ FORCE SUB CHANNELS
FSUB_CHANNEL_1 = -1002800172814  
FSUB_CHANNEL_2 = -1003684601193  
FSUB_LINK_1 = "https://t.me/+HIq_6zg3fRE2MDE1"   
FSUB_LINK_2 = "https://t.me/K_CDRAMAUPDATES"                             

# Initialize Client
bot = Client("filter_batch_bot", api_id=int(API_ID), api_hash=API_HASH, bot_token=BOT_TOKEN, parse_mode=enums.ParseMode.HTML)

# --- MONGODB SETUP ---
client = AsyncIOMotorClient(MONGO_URI)
db = client.bot_database
settings_col = db.chat_settings

# --- STATE MANAGERS & MEMORY ---
LINK_REGEX = re.compile(r'https://t\.me/(?:c/)?(.*)/(\d+)')
user_states = {}
user_links = {} # TeraBox Links Memory

def encode_id(chat_id, first_id, last_id):
    raw = f"{chat_id}:{first_id}:{last_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

def decode_id(token):
    try:
        padding = 4 - (len(token) % 4)
        if padding != 4: token += "=" * padding
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        return raw.split(":")
    except: return None

# ==========================================
# TERABOX HELPER FUNCTIONS (Merged from Bot 1)
# ==========================================
async def safe_reply(message, text, parse_mode=None, **kwargs):
    try:
        await message.reply_text(text, parse_mode=parse_mode, **kwargs)
    except FloodWait as e:
        print(f"Spam Limit Hit! Sleeping for {e.value} seconds...")
        await asyncio.sleep(e.value + 2)
        await message.reply_text(text, parse_mode=parse_mode, **kwargs)
    except Exception as e:
        await message.reply_text(f"❌ Post banane me error aaya: {e}")

async def fetch_terabox_title(url):
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            async with session.get(url, headers=headers, timeout=10) as response:
                html = await response.text()
                match = re.search(r'<meta property="og:title" content="([^"]+)"', html, re.IGNORECASE)
                if match:
                    return match.group(1)
                match2 = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
                if match2:
                    return match2.group(1)
    except Exception as e:
        print(f"Scraping Error: {e}")
    return ""

def extract_info(filename):
    title = "Unknown Drama"
    year = "2024"
    
    clean_text = re.sub(r'(https?://[^\s]+)', '', filename, flags=re.IGNORECASE)
    clean_text = re.sub(r'[a-zA-Z0-9.-]+\.com', '', clean_text, flags=re.IGNORECASE)
    clean_text = re.sub(r'(Shared via TeraBox.*|TeraBox.*)', '', clean_text, flags=re.IGNORECASE|re.DOTALL)
    clean_text = clean_text.strip()
    
    year_match = re.search(r'\b((?:19|20)\d{2})\b', clean_text)
    if year_match:
        year = year_match.group(1)
        
    found_langs = []
    for lang in ['Hindi', 'Korean', 'English', 'Chinese', 'Japanese', 'Tamil', 'Telugu', 'Malayalam']:
        if re.search(lang, clean_text, re.IGNORECASE):
            found_langs.append(lang.upper())
            
    if found_langs:
        main_lang = f"[{found_langs[0]}]"
        audio_tags = " + ".join([f"#{l}" for l in found_langs])
    else:
        main_lang = ""
        audio_tags = "#UNKNOWN"

    title_match = re.search(r'(.*?)(?:_?S\d+EP|_?EP| S\d+EP| EP|_?(?:19|20)\d{2})', clean_text, re.IGNORECASE)
    if title_match:
        raw_title = title_match.group(1)
        raw_title = re.sub(r'^@[A-Za-z0-9]+_', '', raw_title.strip()) 
        raw_title = raw_title.replace('_', ' ').replace('.', ' ').strip()
        
        if len(raw_title) > 2:
            title = raw_title.title()
            
    return title, year, main_lang, audio_tags

# --- HELPER FUNCTIONS ---
async def get_chat_data(chat_id: str):
    data = await settings_col.find_one({"chat_id": chat_id})
    return data if data else {"chat_id": chat_id, "filters": {}, "cleanup": [], "welcome_msg": None, "left_msg": None}

async def update_chat_data(chat_id: str, update_dict: dict):
    await settings_col.update_one({"chat_id": chat_id}, {"$set": update_dict}, upsert=True)

async def is_admin(client, msg: Message) -> bool:
    if msg.sender_chat and str(msg.sender_chat.id) == str(msg.chat.id): return True
    try:
        member = await client.get_chat_member(msg.chat.id, msg.from_user.id)
        return member.status in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except: return False

def get_greeting():
    ist_time = time.time() + (5.5 * 3600)
    hour = time.gmtime(ist_time).tm_hour
    if hour < 12: return "ɢᴏᴏᴅ ᴍᴏʀɴɪɴɢ 🌞"
    elif hour < 17: return "ɢᴏᴏᴅ ᴀꜰᴛᴇʀɴᴏᴏɴ 🌤️"
    elif hour < 20: return "ɢᴏᴏᴅ ᴇᴠᴇɴɪɴɢ 🌥️"
    else: return "ɢᴏᴏᴅ ɴɪɢʜᴛ 🌙"

# --- SMART FSUB MISSING CHANNELS FINDER ---
async def get_missing_channels(client, user_id):
    missing = []
    channels = [
        {"id": FSUB_CHANNEL_1, "link": FSUB_LINK_1, "name": "Channel 1"},
        {"id": FSUB_CHANNEL_2, "link": FSUB_LINK_2, "name": "Channel 2"}
    ]
    for ch in channels:
        try:
            target_chat = int(ch["id"])
            member = await client.get_chat_member(target_chat, user_id)
            if member.status in [enums.ChatMemberStatus.BANNED, enums.ChatMemberStatus.LEFT]:
                missing.append(ch)
        except UserNotParticipant:
            missing.append(ch)
        except ChatAdminRequired:
            logging.error(f"❌ BOT ADMIN NAHI HAI Channel {ch['id']} mein!")
            missing.append(ch)
        except Exception as e:
            logging.error(f"⚠️ FSUB Cache/API Error for {ch['id']}: {e}")
            pass
    return missing
    
# --- FSUB TRY AGAIN BUTTON HANDLER ---
@bot.on_callback_query(filters.regex(r"^fsub_(.*)"))
async def fsub_callback(client: Client, call: CallbackQuery):
    token = call.matches[0].group(1)
    missing_channels = await get_missing_channels(client, call.from_user.id)
    if missing_channels:
        return await call.answer("❌ Please join all required channels first!", show_alert=True)
    
    await call.message.delete()
    call.message.from_user = call.from_user
    call.message.command = ["start", f"batch_{token}"]
    await cmd_start(client, call.message)

# ==========================================
# 1. BATCH GENERATOR & CUSTOM TIME COMMANDS
# ==========================================
@bot.on_message(filters.command("batch") & filters.private)
async def cmd_batch(client: Client, msg: Message):
    user_states[msg.from_user.id] = {"state": "waiting_for_first"}
    await msg.reply_text("<b>Forward The Batch First Message From your Batch Channel (With Forward Tag).. or Give Me Batch First Message link from your batch channel</b>")

@bot.on_message(filters.command("cancel") & filters.private)
async def cmd_cancel(client: Client, msg: Message):
    user_states.pop(msg.from_user.id, None)
    await msg.reply_text("❌ Process cancelled.")

@bot.on_message(filters.command("settime") & filters.private)
async def cmd_set_time(client: Client, msg: Message):
    args = msg.text.split()
    if len(args) < 2:
        return await msg.reply_text("❌ <b>Format:</b> <code>/settime <seconds></code>\n\n<b>Example:</b>\n• <code>/settime 60</code> (1 Minute)\n• <code>/settime 600</code> (10 Minutes)\n• <code>/settime 3600</code> (1 Hour)")
    
    try:
        seconds = int(args[1])
        if seconds < 5:
            return await msg.reply_text("❌ Kripya kam se kam 5 seconds ka time set karein!")
        
        await settings_col.update_one(
            {"chat_id": "GLOBAL_CONFIG"},
            {"$set": {"batch_delete_time": seconds}},
            upsert=True
        )
        
        time_text = f"{seconds} seconds"
        if seconds >= 60:
            time_text = f"{seconds // 60} minutes"
            
        await msg.reply_text(f"✅ <b>Batch Delete Time successfully update ho gaya hai!</b>\nAb se /batch link ki saari files <b>{time_text}</b> ke baad automatically delete ho jayengi.")
    except ValueError:
        await msg.reply_text("❌ Kripya ek valid number daalein (sirf digits/numbers)!")

# ==========================================
# 2. STATE MANAGER FOR PRIVATE CHAT
# ==========================================
@bot.on_message(filters.private & ~filters.command(["start", "batch", "help", "cancel", "setwelcome", "setleft", "offwelcome", "offleft", "settime", "set", "post"]))
async def private_state_manager(client: Client, msg: Message):
    user_id = msg.from_user.id
    state_data = user_states.get(user_id)
    if not state_data: return

    state = state_data["state"]

    if state == "waiting_for_forward":
        if not msg.forward_from_chat or msg.forward_from_chat.type != enums.ChatType.CHANNEL:
            return await msg.reply_text("❌ This is not a forwarded message from a channel.")
        
        channel_id = str(msg.forward_from_chat.id)
        channel_title = msg.forward_from_chat.title
        try:
            member = await client.get_chat_member(chat_id=int(channel_id), user_id=user_id)
            if member.status not in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]:
                return await msg.reply_text("❌ You are not an Admin of this channel!")
        except Exception:
            return await msg.reply_text("❌ Please make the bot an Admin in your channel first.")

        msg_type, action = state_data['msg_type'], state_data['action']
        if action == "off":
            await update_chat_data(channel_id, {msg_type: "OFF"})
            await msg.reply_text(f"✅ The message for '{channel_title}' has been turned <b>OFF</b>.")
            return user_states.pop(user_id, None)

        state_data.update({"state": "waiting_for_text", "channel_id": channel_id, "channel_title": channel_title})
        await msg.reply_text(f"✅ Channel verified: <b>{channel_title}</b>\n\n📝 Now, type and send your new custom Message.")

    elif state == "waiting_for_text":
        if not msg.text: return await msg.reply_text("❌ Please send text only.")
        await update_chat_data(state_data['channel_id'], {state_data['msg_type']: msg.text})
        await msg.reply_text(f"✅ Message successfully set:\n\n{msg.text}")
        user_states.pop(user_id, None)

    elif state == "waiting_for_first":
        msg_id, chat_id = None, None
        if msg.forward_from_chat and msg.forward_from_chat.type == enums.ChatType.CHANNEL:
            chat_id = str(msg.forward_from_chat.id)
            msg_id = msg.forward_from_message_id
        elif msg.text and "t.me" in msg.text:
            match = LINK_REGEX.search(msg.text)
            if match:
                chat_id_or_username = match.group(1)
                msg_id = int(match.group(2))
                chat_id = f"-100{chat_id_or_username}" if chat_id_or_username.isdigit() else chat_id_or_username

        if not msg_id or not chat_id:
            return await msg.reply_text("❌ Invalid Input! Please forward a message or send a valid Telegram message link.")

        state_data.update({"state": "waiting_for_last", "chat_id": chat_id, "first_id": msg_id})
        await msg.reply_text("<b>Forward The Batch Last Message From Your Batch Channel (With Forward Tag).. or Give Me Batch last message link from your batch channel</b>")

    elif state == "waiting_for_last":
        msg_id = None
        if msg.forward_from_chat and msg.forward_from_chat.type == enums.ChatType.CHANNEL:
            msg_id = msg.forward_from_message_id
        elif msg.text and "t.me" in msg.text:
            match = LINK_REGEX.search(msg.text)
            if match: msg_id = int(match.group(2))

        if not msg_id:
            return await msg.reply_text("❌ Invalid Input! Please forward a message or send a valid Telegram message link.")

        chat_id, first_id, last_id = state_data['chat_id'], state_data['first_id'], msg_id
        if first_id > last_id: first_id, last_id = last_id, first_id

        token = encode_id(chat_id, first_id, last_id)
        
        batch_link = f"https://gtkoreandrama.kdlbot.workers.dev?start=batch_{token}"
        
        await msg.reply_text(f"✅ <b>Here is your Batch Link:</b>\n\n<code>{batch_link}</code>")
        user_states.pop(user_id, None)

# --- CHANNEL DM SETUP MANAGEMENT ---
@bot.on_message(filters.command(["setwelcome", "setleft", "offwelcome", "offleft"]) & filters.private)
async def start_setting_msg(client: Client, msg: Message):
    cmd = msg.command[0]
    msg_type = "left_msg" if cmd in ["setleft", "offleft"] else "welcome_msg"
    action = "off" if cmd.startswith("off") else "set"
    user_states[msg.from_user.id] = {"state": "waiting_for_forward", "msg_type": msg_type, "action": action}
    await msg.reply_text("📢 Please <b>Forward</b> any message from your Channel here.")


# ==========================================
# 3. START COMMAND WITH FSUB & TIMED SENDER
# ==========================================
@bot.on_message(filters.command("start") & filters.private)
async def cmd_start(client: Client, msg: Message):
    user_states.pop(msg.from_user.id, None)
    
    if len(msg.command) > 1 and msg.command[1].startswith("batch_"):
        token = msg.command[1].replace("batch_", "")
        
        missing_channels = await get_missing_channels(client, msg.from_user.id)
        if missing_channels:
            user_link = f"<a href='tg://user?id={msg.from_user.id}'>{msg.from_user.first_name}</a>"
            fsub_text = f"<i>Hey {user_link}\n\nPlease Join My Update Channel(s) To Use Me!</i>"
            
            fsub_buttons = []
            for ch in missing_channels:
                fsub_buttons.append([InlineKeyboardButton(f"Join {ch['name']}", url=ch["link"])])
            
            fsub_buttons.append([InlineKeyboardButton("♻️ Try Again", callback_data=f"fsub_{token}")])
            
            return await msg.reply_text(fsub_text, reply_markup=InlineKeyboardMarkup(fsub_buttons))
        
        data = decode_id(token)
        
        if data and len(data) == 3:
            chat_id, first_id, last_id = data[0], int(data[1]), int(data[2])
            try: chat_id = int(chat_id)
            except ValueError: pass

            wait_msg = await msg.reply_text("⏳ <i>Sending your files, please wait...</i>")
            
            global_config = await settings_col.find_one({"chat_id": "GLOBAL_CONFIG"})
            delete_delay = global_config.get("batch_delete_time", 600) if global_config else 600
            
            vip_button = InlineKeyboardMarkup([
                [InlineKeyboardButton("📌 JOIN UPDATE CHANNEL 📌", url=UPDATE_CHANNEL_LINK)]
            ])
            
            sent_ids = []
            for m_id in range(first_id, last_id + 1):
                try:
                    tg_msg = await client.get_messages(chat_id, m_id)
                    if tg_msg.empty: continue
                    
                    if tg_msg.document or tg_msg.video or tg_msg.audio:
                        file_name = "🎬 Movie/Series File"
                        if tg_msg.document and tg_msg.document.file_name: 
                            file_name = tg_msg.document.file_name
                        elif tg_msg.video and tg_msg.video.file_name: 
                            file_name = tg_msg.video.file_name
                        elif tg_msg.audio and tg_msg.audio.file_name: 
                            file_name = tg_msg.audio.file_name
                        
                        vip_caption = (
                            f"<b><a href='{FILE_CAPTION_LINK}'>{file_name}</a></b>\n\n"
                            f"<b>⚜️ Powered By : @GTKOREANDRAMA</b>"
                        )
                        
                        sent = await client.copy_message(
                            chat_id=msg.chat.id,
                            from_chat_id=chat_id,
                            message_id=m_id,
                            caption=vip_caption,
                            reply_markup=vip_button
                        )
                        sent_ids.append(sent.id)
                    else:
                        sent = await client.copy_message(
                            chat_id=msg.chat.id,
                            from_chat_id=chat_id,
                            message_id=m_id
                        )
                        sent_ids.append(sent.id)
                    
                    await asyncio.sleep(0.5)
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                except Exception:
                    pass
            
            await wait_msg.delete()

            if sent_ids:
                time_text = f"{delete_delay} seconds"
                if delete_delay >= 60:
                    time_text = f"{delete_delay // 60} minutes"
                
                alert_text = (
                    "⚠️ <u><b>Important:</b></u>\n\n"
                    f"<i>All Messages will be deleted after <b>{time_text}</b>. Please save or forward these "
                    "messages to your <b>personal saved messages</b> to avoid losing them!</i>"
                )
                
                alert_button = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📟 UPDATE CHANNEL", url=UPDATE_CHANNEL_LINK)]
                ])
                
                alert = await msg.reply_text(
                    text=alert_text,
                    reply_markup=alert_button
                )
                sent_ids.append(alert.id)
                
                user_chat_id = str(msg.chat.id)
                user_data = await get_chat_data(user_chat_id)
                cleanup_items = user_data.get('cleanup', [])
                
                for s_id in sent_ids:
                    cleanup_items.append({
                        "chat_id": msg.chat.id,
                        "message_id": s_id,
                        "delete_at": time.time() + delete_delay,
                        "action": "delete"
                    })
                await update_chat_data(user_chat_id, {"cleanup": cleanup_items})
            return
            
    me = await client.get_me()
    greeting = get_greeting()
    user_name = msg.from_user.first_name.upper() if msg.from_user.first_name else "USER"
    bot_name = me.first_name.upper() if me.first_name else "BOT"
    
    caption = (
        f"🚩 <b>JAI SHRI RAM</b> 🚩\n\n"
        f"<b>HEY {user_name}</b>, <b>{greeting}</b>\n\n"
        f"🤖 <b>ɪ ᴀᴍ {bot_name}, ᴛʜᴇ ᴍᴏꜱᴛ ᴘᴏᴡᴇʀꜰᴜʟ ᴀᴜᴛᴏ ꜰɪʟᴛᴇʀ ʙᴏᴛ ᴡɪᴛʜ ᴘʀᴇᴍɪᴜᴍ ꜰᴇᴀᴛᴜʀᴇꜱ.</b>"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('🔰 ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜpun 🔰', url=f'https://t.me/{me.username}?startgroup=true')],
        [InlineKeyboardButton('ʜᴇʟᴘ 📢', callback_data='help_menu'), InlineKeyboardButton('ᴀʙᴏᴜᴛ 📖', callback_data='about_menu')],
        [InlineKeyboardButton('ᴛᴏᴘ ꜱᴇᴀʀᴄʜɪɴɢ ⭐', callback_data='top_search'), InlineKeyboardButton('ᴜᴘɢʀᴀᴅᴇ 🎟️', callback_data='upgrade_menu')],
        [InlineKeyboardButton('➕ ᴀᴅᴅ ᴛᴏ ᴄʜᴀɴɴᴇʟ ➕', url=f'https://t.me/{me.username}?startchannel=start')]
    ])
    IMAGE_URL = "https://images.unsplash.com/photo-1534447677768-be436bb09401?w=800"
    try: await msg.reply_photo(photo=IMAGE_URL, caption=caption, reply_markup=kb)
    except: await msg.reply_text(caption, reply_markup=kb)

# --- CALLBACK MENUS ---
@bot.on_callback_query(~filters.regex(r"^fsub_(.*)"))
async def cb_handlers(client: Client, call: CallbackQuery):
    if call.data == "help_menu":
        help_text = (
            "📖 <b>Full Command & Feature Guide:</b>\n\n"
            "📢 <b>1. Channel DMs (Welcome/Goodbye):</b>\n• <code>/setwelcome</code> & <code>/setleft</code>\n• <code>/offwelcome</code> & <code>/offleft</code>\n\n"
            "🗃️ <b>2. Group Filters Management:</b>\n• <code>/addfilter [keyword] | [reply link]</code>\n• <code>/delfilter [keyword]</code>\n• <code>/delallfilters</code>\n• <code>/filters</code>\n\n"
            "⚡ <b>3. Premium Features (Auto-Active):</b>\n• <b>Auto-Approve:</b> Channel requests approved instantly.\n• <b>Exact Match:</b> Strict word boundary filter triggers.\n• <b>Big Emoji Reaction:</b> Pop-up animations on triggers.\n• <b>Auto-Edit:</b> Filter replies edit after 24 hours."
        )
        back_kb = InlineKeyboardMarkup([[InlineKeyboardButton('🔙 ʙᴀᴄᴋ', callback_data='start_menu')]])
        try: await call.message.edit_caption(caption=help_text, reply_markup=back_kb)
        except: pass
    elif call.data == "about_menu":
        me = await client.get_me()
        about_text = (
            f"🤖 <b>ᴀʙᴏᴜᴛ {me.first_name.upper()}</b>\n\n<b>• ᴅᴇᴠᴇʟᴏᴘᴇʀ:</b> Admin\n<b>• ʟᴀɴɢᴜᴀɢᴇ:</b> Python 3\n<b>• ꜰʀᴀᴍᴇᴡᴏʀᴋ:</b> Pyrogram\n<b>• ᴅᴀᴛᴀʙᴀꜱᴇ:</b> MongoDB\n\n<i>This bot provides powerful auto-request approval, dynamic EXACT keyword filtering with overwrite protection, and 24-hour auto-edit features for Telegram Groups & Channels.</i>"
        )
        back_kb = InlineKeyboardMarkup([[InlineKeyboardButton('🔙 ʙᴀᴄᴋ', callback_data='start_menu')]])
        try: await call.message.edit_caption(caption=about_text, reply_markup=back_kb)
        except: pass
    elif call.data == "top_search":
        await call.answer("⭐ Top Searching feature coming soon!", show_alert=True)
    elif call.data == "upgrade_menu":
        await call.answer("🎟️ Upgrade feature coming soon!", show_alert=True)
    elif call.data == "start_menu":
        me = await client.get_me()
        caption = f"🚩 <b>JAI SHRI RAM</b> 🚩\n\n<b>HEY {call.from_user.first_name.upper()}</b>, <b>{get_greeting()}</b>\n\n🤖 <b>ɪ ᴀᴍ {me.first_name.upper()}, ᴛʜᴇ ᴍᴏꜱᴛ ᴘᴏᴡᴇʀꜰᴜʟ ᴀᴜᴛᴏ ꜰɪʟᴛᴇʀ ʙᴏᴛ ᴡɪᴛʜ ᴘʀᴇᴍɪᴜᴍ ꜰᴇᴀᴛᴜʀᴇꜱ.</b>"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('🔰 ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ 🔰', url=f'https://t.me/{me.username}?startgroup=true')],
            [InlineKeyboardButton('ʜᴇʟᴘ 📢', callback_data='help_menu'), InlineKeyboardButton('ᴀʙᴏᴜᴛ 📖', callback_data='about_menu')],
            [InlineKeyboardButton('ᴛᴏᴘ ꜱᴇᴀʀᴄʜɪɴɢ ⭐', callback_data='top_search'), InlineKeyboardButton('... 🎟️', callback_data='upgrade_menu')],
            [InlineKeyboardButton('➕ ᴀᴅᴅ ᴛᴏ ᴄʜᴀɴɴᴇʟ ➕', url=f'https://t.me/{me.username}?startchannel=start')]
        ])
        try: await call.message.edit_caption(caption=caption, reply_markup=kb)
        except: pass
    elif call.data.startswith("filter_update_"):
        if not await is_admin(client, call.message):
            return await call.answer("❌ Sirf Admin hi approve kar sakte hain!", show_alert=True)
        action = call.data.split("_")[-1]
        chat_id = str(call.message.chat.id)
        chat_data = await get_chat_data(chat_id)
        pending = chat_data.get("pending_filter")
        
        if not pending: return await call.message.edit_text("❌ Session expired.")
        if action == "no":
            await settings_col.update_one({"chat_id": chat_id}, {"$unset": {"pending_filter": ""}})
            return await call.message.edit_text("❌ Update cancel kar diya gaya hai.")
        if action == "yes":
            kw, reply = pending["keyword"], pending["reply_text"]
            await settings_col.update_one({"chat_id": chat_id}, {"$set": {f"filters.{kw}": reply}, "$unset": {"pending_filter": ""}})
            await call.message.edit_text(f"✅ Filter <b>{kw}</b> successfully UPDATE ho gaya!")

@bot.on_message(filters.command("help") & filters.private)
async def cmd_help(client: Client, msg: Message):
    await msg.reply_text("📖 <b>Full Command Guide:</b>\n\n📢 <b>1. Channel DMs:</b>\n• <code>/setwelcome</code> & <code>/setleft</code>\n• <code>/offwelcome</code> & <code>/offleft</code>\n\n🗃️ <b>2. Group Filters:</b>\n• <code>/addfilter [word] | [reply]</code>\n• <code>/delfilter [word]</code>\n• <code>/filters</code>\n\n⏱️ <b>3. Custom Timer:</b>\n• <code>/settime <seconds></code>\n\n📁 <b>4. TeraBox Links:</b>\n• <code>/set</code> - Generate Episode List\n• <code>/post</code> - Generate Episode Posts")

# ==========================================
# 4. GROUP FILTERS MANAGEMENT
# ==========================================
@bot.on_message(filters.command("addfilter") & filters.group)
async def cmd_addfilter(client: Client, msg: Message):
    if not await is_admin(client, msg): return
    
    if "|" not in msg.text:
        return await msg.reply_text("❌ <b>Sahi Format:</b> <code>/addfilter keyword | reply link</code>\n\n<b>Example:</b>\n<code>/addfilter my demon k drama | https://t.me/ASKORENDRAMA/123</code>")
    
    text = msg.text.replace("/addfilter", "", 1).strip()
    keyword, reply_text = text.split("|", 1)
    keyword = keyword.strip().lower()
    reply_text = reply_text.strip()
    
    chat_id = str(msg.chat.id)
    chat_data = await get_chat_data(chat_id)
    
    if keyword in chat_data.get('filters', {}):
        await settings_col.update_one({"chat_id": chat_id}, {"$set": {"pending_filter": {"keyword": keyword, "reply_text": reply_text}}}, upsert=True)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Yes, Update", callback_data="filter_update_yes"), InlineKeyboardButton("❌ No, Cancel", callback_data="filter_update_no")]])
        await msg.reply_text(f"⚠️ <b>Wait!</b> '<code>{keyword}</code>' ka filter pehle se mojood hai. Overwrite karein?", reply_markup=kb)
    else:
        await settings_col.update_one({"chat_id": chat_id}, {"$set": {f"filters.{keyword}": reply_text}}, upsert=True)
        await msg.reply_text(f"✅ Naya Filter <b>{keyword}</b> successfully add ho gaya!")

@bot.on_message(filters.command("delfilter") & filters.group)
async def cmd_delfilter(client: Client, msg: Message):
    if not await is_admin(client, msg): return
    args = msg.text.split(maxsplit=1)
    if len(args) < 2: return
    await settings_col.update_one({"chat_id": str(msg.chat.id)}, {"$unset": {f"filters.{args[1].lower()}": ""}})
    await msg.reply_text(f"🗑️ Filter for <b>{args[1]}</b> deleted.")

@bot.on_message(filters.command("delallfilters") & filters.group)
async def cmd_delallfilters(client: Client, msg: Message):
    if not await is_admin(client, msg): return
    await settings_col.update_one({"chat_id": str(msg.chat.id)}, {"$set": {"filters": {}}})
    await msg.reply_text("🗑️ ✅ All active filters deleted.")

@bot.on_message(filters.command("filters") & filters.group)
async def cmd_filters(client: Client, msg: Message):
    if not await is_admin(client, msg): return
    chat_data = await get_chat_data(str(msg.chat.id))
    if chat_data.get('filters'):
        active_filters = "\n".join([f"• <code>{k}</code>" for k in chat_data['filters'].keys()])
        await msg.reply_text(f"📋 <b>Active Filters:</b>\n{active_filters}")
    else: await msg.reply_text("No active filters.")

# ==========================================
# 5. AUTO CHAT JOIN APPROVAL & DM LOGIC
# ==========================================
@bot.on_chat_join_request()
async def auto_approve_join_request(client: Client, request: ChatJoinRequest):
    user_id, chat_id = request.from_user.id, str(request.chat.id)
    chat_data = await get_chat_data(chat_id)
    welcome_msg = chat_data.get('welcome_msg')
    if welcome_msg and welcome_msg != "OFF":
        try: await client.send_message(chat_id=user_id, text=welcome_msg)
        except: pass 
    try: await request.approve()
    except Exception as e: logging.error(f"Failed to approve: {e}")

@bot.on_chat_member_updated()
async def on_chat_member_update(client: Client, update: ChatMemberUpdated):
    if not update.old_chat_member or not update.new_chat_member: return
    if (update.old_chat_member.status in [enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER] and 
        update.new_chat_member.status in [enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.RESTRICTED]):
        
        user = update.new_chat_member.user
        chat_id = str(update.chat.id)
        chat_data = await get_chat_data(chat_id)
        final_msg = chat_data.get('left_msg') 
        
        if final_msg and final_msg != "OFF":
            try: await client.send_message(chat_id=user.id, text=final_msg)
            except: pass

# --- DIRECT REACTION BYPASS FUNCTION ---
async def send_reaction_direct(chat_id, message_id, emoji):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setMessageReaction"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reaction": [{"type": "emoji", "emoji": emoji}]
    }
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(url, json=payload)
    except:
        pass

# --- GROUP CHAT LISTENER (FILTERS + REACTION BYPASS) ---
@bot.on_message(filters.group & filters.text & ~filters.command([]))
async def group_filter_handler(client: Client, msg: Message):
    if msg.text.startswith('/'): return
    chat_id = str(msg.chat.id)
    chat_data = await get_chat_data(chat_id)
    
    for kw, reply in chat_data.get('filters', {}).items():
        pattern = r'\b' + re.escape(kw.lower()) + r'\b'
        if re.search(pattern, msg.text.lower()):
            
            emoji_list = ["🔥", "❤️", "👍", "🎉", "🍿", "💯", "🚀", "😍", "👏"]
            await send_reaction_direct(msg.chat.id, msg.id, random.choice(emoji_list))
            
            sent = await msg.reply_text(f"<b>{reply}</b>", disable_web_page_preview=True)
            
            new_cleanup = chat_data.get('cleanup', []) + [{"chat_id": sent.chat.id, "message_id": sent.id, "delete_at": time.time() + 86400}]
            await update_chat_data(chat_id, {"cleanup": new_cleanup})
            return 

# ==========================================
# 6. BYPASS LINK EXTRACTOR
# ==========================================
@bot.on_message(filters.chat(-1003994123332), group=1)
async def bypass_link_extractor(client: Client, msg: Message):
    try:
        content = msg.text or msg.caption
        if not content: return 
        
        match = re.search(r"Bypassed Link:.*?(https?://\S+)", content, re.IGNORECASE | re.DOTALL)
        
        if match:
            extracted_url = match.group(1).strip()
            logging.info(f"🔗 SUCCESS: Bypass Link mil gaya! -> {extracted_url}")
            
            if BYPASS_DEST_GROUP:
                try:
                    dest_chat_id = int(BYPASS_DEST_GROUP)
                    await client.send_message(
                        chat_id=dest_chat_id, 
                        text=extracted_url,
                        disable_web_page_preview=True
                    )
                    logging.info(f"✅ Link successfully forwarded to destination group.")
                except ValueError: pass
                except Exception as send_err: logging.error(f"❌ ERROR: Destination me error: {send_err}")
                
    except Exception as e:
        logging.error(f"❌ Bypass extractor module me error: {e}")

# ==========================================
# 7. TERABOX LINK COLLECTOR & POST MANAGER (Group 2)
# ==========================================
@bot.on_message(filters.command("set") | filters.regex(r'(?i)^/?set$'))
async def sort_and_send_set(client: Client, message: Message):
    user_id = message.from_user.id
    
    if user_id in user_links and user_links[user_id]:
        await safe_reply(message, "⏳ **List ban rahi hai, thoda wait karein...**", parse_mode=enums.ParseMode.MARKDOWN)
        
        sorted_links = sorted(user_links[user_id], key=lambda x: x["ep"])
        first_raw_text = sorted_links[0]["raw_text"]
        title, year, main_lang, audio_tags = extract_info(first_raw_text)
        title = title.replace('<', '').replace('>', '')
        
        final_text = f"<b>🎥 {title} {main_lang} 720p</b>\n"
        final_text += "<b>━━━━━━━━━━━━━━━━━━━━</b>\n"
        final_text += '<b>⁉️ HOW TO DOWNLOAD / PLAY ⏯️ :- <a href="https://t.me/kcsjbvxdxdxcc/3">CLICK HERE</a></b>\n'
        final_text += "<b>━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        
        for item in sorted_links:
            final_text += f"<b>📁 EP {item['ep']}</b>\n<b>{item['url']}</b>\n\n"
            
        final_text += "<b>❤️‍🔥 Complete All Episodes ❤️‍🔥</b>\n\n"
        final_text += "<b>👉 Join Our Backup Channel 👈</b>\n"
        final_text += "<b>https://t.me/KOREAN_DRAMA_GT</b>"
        
        await safe_reply(message, final_text.strip(), parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
        user_links[user_id] = [] 
    else:
        await safe_reply(message, "Aapne abhi tak koi valid link nahi bheja hai.")

@bot.on_message(filters.command("post") | filters.regex(r'(?i)^/?post$'))
async def send_designed_post(client: Client, message: Message):
    user_id = message.from_user.id
    
    if user_id in user_links and user_links[user_id]:
        await safe_reply(message, "⏳ **Posts ban rahe hain, thoda wait karein...**", parse_mode=enums.ParseMode.MARKDOWN)
        
        sorted_links = sorted(user_links[user_id], key=lambda x: x["ep"])
        
        episodes_data = {}
        for item in sorted_links:
            ep = item['ep']
            if ep not in episodes_data:
                episodes_data[ep] = {"urls": [], "raw_text": item["raw_text"]}
            episodes_data[ep]["urls"].append(item['url'])
            
        for ep, data in episodes_data.items():
            urls = data["urls"]
            raw_text = data["raw_text"]
            
            title, year, main_lang, audio_tags = extract_info(raw_text)
            title = title.replace('<', '').replace('>', '') 
            
            link_480 = urls[0] if len(urls) > 0 else "#"
            link_720 = urls[1] if len(urls) > 1 else urls[0]
            
            final_text = f"""<b>🎬 {title} {main_lang}</b>
<b>📅 YEAR: {year}</b>
<b>💿 EPISODE:- {ep}</b>

<b>🔊 [ AMZN {audio_tags} ]</b>

<b>              🔮 TeraBox</b>
<b> ▬▬▬▬▬▬▬▬▬▬▬▬▬ </b>
<b>📁 480p ☞ <a href="{link_480}">CLICK HERE</a></b>

<b>📁 720p ☞ <a href="{link_720}">CLICK HERE</a></b>
<b> ▬▬▬▬▬▬▬▬▬▬▬▬▬ </b>
<b>✅ All Episode Uploaded</b>

<blockquote><b>🛑 TeraBox Ads Problem Solve:
                                       <a href="https://t.me/kcsjbvxdxdxcc/19?single">CLICK HERE</a></b></blockquote>

<b>🚨 Join Our Backup Channel:</b>
<b>👇 https://t.me/KOREAN_DRAMA_GT</b>"""
            
            await safe_reply(message, final_text, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
            await asyncio.sleep(2)
            
        user_links[user_id] = [] 
    else:
        await safe_reply(message, "Aapne abhi tak koi valid link nahi bheja hai.")

# Group 2 is explicitly used so it doesn't conflict with normal message handlers or group filters.
@bot.on_message((filters.text | filters.caption) & ~filters.command(["start", "batch", "cancel", "settime", "setwelcome", "setleft", "offwelcome", "offleft", "help", "addfilter", "delfilter", "delallfilters", "filters", "set", "post"]), group=2)
async def collect_links(client: Client, message: Message):
    # Skip processing if message is from a channel or user is busy in bot setup states
    if getattr(message, 'from_user', None) is None: return
    user_id = message.from_user.id
    if user_id in user_states: return 

    try:
        text = message.text or message.caption or ""
        text_lower = text.lower().strip()
        
        # Safely pass command overrides
        if text_lower in ['post', '/post']: return await send_designed_post(client, message)
        if text_lower in ['set', '/set']: return await sort_and_send_set(client, message)
            
        clean_url = None
        entities = getattr(message, 'entities', None) or getattr(message, 'caption_entities', None) or []
        for ent in entities:
            if getattr(ent, 'url', None):
                clean_url = ent.url
                break
                
        if not clean_url:
            url_match = re.search(r'(https?://[^\s]+|[a-zA-Z0-9.-]+\.com/s/[^\s]+)', text)
            if url_match:
                clean_url = url_match.group(1)
                if not clean_url.startswith('http'):
                    clean_url = 'https://' + clean_url
                    
        if not clean_url: return
            
        preview_text = ""
        if getattr(message, 'web_page', None):
            title = getattr(message.web_page, 'title', "") or ""
            desc = getattr(message.web_page, 'description', "") or ""
            preview_text = f"{title} {desc}"
            
        if not preview_text.strip():
            preview_text = await fetch_terabox_title(clean_url)
            
        combined_text = f"{text} {preview_text}"
        ep_match = re.search(r'(?:EP|Episode|S\d+EP)\s*0*(\d+)', combined_text, re.IGNORECASE)
        
        if ep_match:
            ep_number = int(ep_match.group(1))
            if user_id not in user_links: user_links[user_id] = []
                
            if not any(item['ep'] == ep_number for item in user_links[user_id]):
                user_links[user_id].append({"ep": ep_number, "url": clean_url, "raw_text": combined_text})
                await safe_reply(message, f"✅ EP {ep_number} add ho gaya!")
            else:
                await safe_reply(message, f"⚠️ EP {ep_number} pehle se added hai.")
        else:
            await safe_reply(message, f"❌ **EP Detect Nahi Hua!**\n\n**Bot ne ye padha:**\n`{combined_text[:100]}`", parse_mode=enums.ParseMode.MARKDOWN)
            
    except Exception as e:
        logging.error(f"Message Processing Error: {e}")

# --- BACKGROUND DYNAMIC CLEANUP TASK (EDIT FILTERS / DELETE BATCHES) ---
async def cleanup_task():
    while True:
        await asyncio.sleep(15) 
        async for chat in settings_col.find({"cleanup": {"$not": {"$size": 0}}}):
            valid = []
            for item in chat.get('cleanup', []):
                if time.time() >= item['delete_at']:
                    try:
                        if item.get("action") == "delete":
                            await bot.delete_messages(
                                chat_id=item['chat_id'], 
                                message_ids=item['message_id']
                            )
                        else:
                            await bot.edit_message_text(
                                chat_id=item['chat_id'], 
                                message_id=item['message_id'],
                                text="<b>💖 ᴊᴜꜱᴛ ꜱᴇɴᴅ ᴛʜᴇ ᴛɪᴛʟＥ, ᴀɴᴅ ɪ'ʟʟ ɢᴇᴛ ɪᴛ ꜰᴏʀ ʏᴏᴜ ɪɴꜱᴛᴀɴᴛʟʏ! 👇</b>"
                            )
                    except: pass
                else: 
                    valid.append(item)
            await update_chat_data(chat['chat_id'], {"cleanup": valid})

# --- RENDER WEB ALIVE SERVER (Original aiohttp from bot.py) ---
async def handle_ping(request): return web.Response(text="Pyrogram VIP Bot Active!")
async def start_dummy_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()

# --- MAIN ENGINE RUN ---
async def start_bot():
    if not BOT_TOKEN or not MONGO_URI or API_ID == '0' or not API_HASH:
        logging.error("❌ CRASH PREVENTED: Please add API_ID, API_HASH, BOT_TOKEN, and MONGO_URI in Render Environment Variables!")
        return
        
    await start_dummy_server()
    asyncio.create_task(cleanup_task())
    
    await bot.start()
    logging.info("🚀 Pyrogram VIP Unified Bot is Now Online & Running Perfectly!")
    await idle()
    await bot.stop()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_bot())
