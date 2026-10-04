import os
import re
import time
import base64
import asyncio
import logging
import aiohttp
from aiohttp import web
from pyrogram import Client, filters, enums, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, ChatJoinRequest
from pyrogram.handlers import MessageHandler, ChatJoinRequestHandler
from pyrogram.errors import FloodWait, UserIsBlocked, PeerIdInvalid, RPCError
from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- ENV VARIABLES ---
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
MASTER_TOKEN = os.environ.get("MASTER_TOKEN", "")
MONGO_URI = os.environ.get("MONGO_URI", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0")) 
PORT = int(os.environ.get("PORT", 10000))

# --- DATABASE SETUP ---
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client.clone_factory
clones_db = db.clones        
settings_db = db.settings    
users_db = db.users          
pending_reqs_db = db.pending_requests 

user_states = {}             
active_clones = {} 
master_terabox_links = {} # 🔹 TERABOX SIRF MASTER BOT KE LIYE

# --- HELPER FUNCTIONS ---
async def check_admin(client: Client, msg: Message) -> bool:
    user_id = msg.from_user.id
    if user_id == OWNER_ID: return True
    bot_id = client.me.id
    config = await settings_db.find_one({"bot_id": bot_id})
    if config and user_id in config.get("admins", []): return True
    return False 

def encode_data(data: str) -> str:
    return base64.urlsafe_b64encode(data.encode()).decode().rstrip("=")

def decode_data(token: str) -> str:
    padding = 4 - (len(token) % 4)
    if padding != 4: token += "=" * padding
    return base64.urlsafe_b64decode(token.encode()).decode()

async def generate_final_link(client: Client, token_type: str, token: str) -> str:
    bot_id = client.me.id
    config = await settings_db.find_one({"bot_id": bot_id}) or {}
    custom_domain = config.get("custom_domain", None)
    if custom_domain:
        if custom_domain.endswith('/'): custom_domain = custom_domain[:-1]
        return f"{custom_domain}?start={token_type}_{token}"
    return f"https://t.me/{client.me.username}?start={token_type}_{token}"


# ==========================================
# TERABOX SCRAPING FUNCTIONS (USED BY MASTER)
# ==========================================
async def safe_reply(message, text, parse_mode=None, **kwargs):
    try: await message.reply_text(text, parse_mode=parse_mode, **kwargs)
    except FloodWait as e:
        await asyncio.sleep(e.value + 2)
        await message.reply_text(text, parse_mode=parse_mode, **kwargs)
    except: pass

async def fetch_terabox_title(url):
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"User-Agent": "Mozilla/5.0"}
            async with session.get(url, headers=headers, timeout=10) as response:
                html = await response.text()
                match = re.search(r'<meta property="og:title" content="([^"]+)"', html, re.IGNORECASE)
                if match: return match.group(1)
                match2 = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE)
                if match2: return match2.group(1)
    except: pass
    return ""

def extract_info(filename):
    title, year = "Unknown Drama", "2024"
    clean_text = re.sub(r'(https?://[^\s]+)', '', filename, flags=re.IGNORECASE)
    clean_text = re.sub(r'[a-zA-Z0-9.-]+\.com', '', clean_text, flags=re.IGNORECASE)
    clean_text = re.sub(r'(Shared via TeraBox.*|TeraBox.*)', '', clean_text, flags=re.IGNORECASE|re.DOTALL).strip()
    
    year_match = re.search(r'\b((?:19|20)\d{2})\b', clean_text)
    if year_match: year = year_match.group(1)
        
    found_langs = [l.upper() for l in ['Hindi', 'Korean', 'English', 'Chinese', 'Japanese', 'Tamil', 'Telugu', 'Malayalam'] if re.search(l, clean_text, re.IGNORECASE)]
    main_lang = f"[{found_langs[0]}]" if found_langs else ""
    audio_tags = " + ".join([f"#{l}" for l in found_langs]) if found_langs else "#UNKNOWN"

    title_match = re.search(r'(.*?)(?:_?S\d+EP|_?EP| S\d+EP| EP|_?(?:19|20)\d{2})', clean_text, re.IGNORECASE)
    if title_match:
        raw_title = re.sub(r'^@[A-Za-z0-9]+_', '', title_match.group(1).strip()).replace('_', ' ').replace('.', ' ').strip()
        if len(raw_title) > 2: title = raw_title.title()
    return title, year, main_lang, audio_tags


# ==========================================
# 🔹 CLONE BOT LOGIC (CLEAN & FAST) 🔹
# ==========================================
async def clone_start(client: Client, msg: Message):
    user_id, bot_id = msg.from_user.id, client.me.id
    user_states.pop(f"{bot_id}_{user_id}", None)
    await users_db.update_one({"bot_id": bot_id, "user_id": user_id}, {"$set": {"name": msg.from_user.first_name}}, upsert=True)
    
    if len(msg.command) <= 1: return 
    param = msg.command[1]
    
    if param.startswith("batch_") or param.startswith("genlink_") or param.startswith("pbatch_") or param.startswith("pgenlink_"):
        is_permanent = param.startswith("pbatch_") or param.startswith("pgenlink_")
        data = decode_data(param.split("_", 1)[1]).split(":")
        chat_id, first_id = int(data[0]), int(data[1])
        last_id = int(data[2]) if ("batch" in param) else first_id
        
        wait_msg = await msg.reply_text("⏳ <i>Sending your files, please wait...</i>", parse_mode=enums.ParseMode.HTML)
        config = await settings_db.find_one({"bot_id": bot_id}) or {}
        
        sent_msgs = []
        for m_id in range(first_id, last_id + 1):
            try:
                tg_msg = await client.get_messages(chat_id, m_id)
                if tg_msg.empty: continue
                if config.get("caption_on", True) and (tg_msg.document or tg_msg.video or tg_msg.audio):
                    raw_fname = getattr(tg_msg.document or tg_msg.video or tg_msg.audio, 'file_name', None) or '🎬 Movie/Series File'
                    cap = f"<b><a href='{config.get('caption_link', 'https://t.me/KOREAN_DRAMA_GT')}'>{str(raw_fname).replace('<', '&lt;').replace('>', '&gt;').replace('&', '&amp;')}</a></b>\n\n<b>{config.get('watermark', '⚜️ Powered By : @GTKOREANDRAMA')}</b>"
                    sent = await client.copy_message(msg.chat.id, chat_id, m_id, caption=cap, parse_mode=enums.ParseMode.HTML)
                else: sent = await client.copy_message(msg.chat.id, chat_id, m_id)
                sent_msgs.append(sent.id)
                await asyncio.sleep(0.05) 
            except FloodWait as e: await asyncio.sleep(e.value + 1)
            except: pass
        
        await wait_msg.delete()
        if sent_msgs and not is_permanent:
            d_time = config.get("delete_time", 900)
            alert = await msg.reply_text(f"⚠️ <u><b>Important:</b></u>\n\n<i>All Messages will be deleted after <b>{d_time//60 if d_time>=60 else d_time} {'minutes' if d_time>=60 else 'seconds'}</b>. Save them!</i>", parse_mode=enums.ParseMode.HTML)
            await asyncio.sleep(d_time)
            try: await client.delete_messages(msg.chat.id, sent_msgs + [alert.id])
            except: pass

async def link_generator(client: Client, msg: Message):
    if not await check_admin(client, msg): return
    cmd = msg.command[0]
    state_key = f"{client.me.id}_{msg.from_user.id}"
    user_states.pop(state_key, None)
    
    modes = {
        "genlink": "✅ **Auto-Delete GenLink Mode Active**",
        "pgenlink": "✅ **Permanent GenLink Mode Active**",
        "batch": "✅ **Auto-Delete Batch Mode Active**\nForward FIRST message of batch.",
        "pbatch": "✅ **Permanent Batch Mode Active**\nForward FIRST message of batch."
    }
    
    if cmd in modes:
        user_states[state_key] = {"type": cmd, "step": "wait_msg" if "genlink" in cmd else "wait_first"}
        await msg.reply_text(f"{modes[cmd]}\n(Send /cancel to abort)")
    elif cmd == "cancel":
        await msg.reply_text("✅ Action cancelled.")

async def message_state_handler(client: Client, msg: Message):
    state_key = f"{client.me.id}_{msg.from_user.id}"
    if msg.text and msg.text.startswith('/'): 
        user_states.pop(state_key, None)
        return 
        
    if state_key not in user_states: return
    state = user_states[state_key]
    
    if state["step"] in ["wait_msg", "wait_first"]:
        if not msg.forward_from_chat: return await msg.reply_text("❌ Please forward a message from a channel!")
        chat_id, msg_id = msg.forward_from_chat.id, msg.forward_from_message_id
        
        if "genlink" in state["type"]:
            token = encode_data(f"{chat_id}:{msg_id}")
            link = await generate_final_link(client, state["type"], token)
            is_perm = "Permanent" if "p" in state["type"] else "Auto-Delete"
            await msg.reply_text(f"✅ **Single Link ({is_perm}):**\n`{link}`", parse_mode=enums.ParseMode.MARKDOWN)
            del user_states[state_key]
        else:
            state.update({"step": "wait_last", "chat_id": chat_id, "first_id": msg_id})
            await msg.reply_text("✅ First message saved! Now forward the LAST message of the batch.")
            
    elif state["step"] == "wait_last":
        if not msg.forward_from_chat: return await msg.reply_text("❌ Please forward from a channel!")
        chat_id, first_id, msg_id = state["chat_id"], state["first_id"], msg.forward_from_message_id
        if first_id > msg_id: first_id, msg_id = msg_id, first_id
        token = encode_data(f"{chat_id}:{first_id}:{msg_id}")
        link = await generate_final_link(client, state["type"], token)
        is_perm = "Permanent" if "p" in state["type"] else "Auto-Delete"
        await msg.reply_text(f"✅ **Batch Link ({is_perm}):**\n`{link}`", parse_mode=enums.ParseMode.MARKDOWN)
        del user_states[state_key]

async def admin_settings(client: Client, msg: Message):
    if not await check_admin(client, msg): return
    bot_id, cmd = client.me.id, msg.command[0]
    user_states.pop(f"{bot_id}_{msg.from_user.id}", None)
    
    if cmd == "caption":
        val = msg.command[1].lower() == "on" if len(msg.command)>1 else True
        await settings_db.update_one({"bot_id": bot_id}, {"$set": {"caption_on": val}}, upsert=True)
        await msg.reply_text(f"✅ Global Caption set to **{'ON' if val else 'OFF'}**")
    elif cmd == "settime":
        try:
            sec = int(msg.command[1])
            await settings_db.update_one({"bot_id": bot_id}, {"$set": {"delete_time": sec}}, upsert=True)
            await msg.reply_text(f"✅ Auto-Delete timer set to {sec//60 if sec>=60 else sec} {'minutes' if sec>=60 else 'seconds'}.")
        except: await msg.reply_text("Usage: `/settime 900`")
    elif cmd == "setapprove":
        try:
            delay = int(msg.command[1])
            await settings_db.update_one({"bot_id": bot_id}, {"$set": {"approve_delay": delay}}, upsert=True)
            await msg.reply_text(f"✅ Auto-Approve delay set to: {delay//3600 if delay>=3600 else (delay//60 if delay>=60 else delay)} {'hours' if delay>=3600 else ('minutes' if delay>=60 else 'seconds')}")
        except: await msg.reply_text("Usage: `/setapprove 14400`")
    elif cmd == "setdomain" and len(msg.command) > 1:
        domain = msg.command[1].lower()
        if domain == "off":
            await settings_db.update_one({"bot_id": bot_id}, {"$set": {"custom_domain": None}}, upsert=True)
            await msg.reply_text("✅ Custom Domain removed.")
        else:
            if not domain.startswith("http"): domain = "https://" + domain
            await settings_db.update_one({"bot_id": bot_id}, {"$set": {"custom_domain": domain}}, upsert=True)
            await msg.reply_text(f"✅ Custom Domain saved!\n{domain}")
    elif cmd == "setlink" and len(msg.command) > 1:
        await settings_db.update_one({"bot_id": bot_id}, {"$set": {"caption_link": msg.command[1]}}, upsert=True)
        await msg.reply_text(f"✅ Caption Link updated!")
    elif cmd == "setwatermark" and len(msg.command) > 1:
        await settings_db.update_one({"bot_id": bot_id}, {"$set": {"watermark": msg.text.split(None, 1)[1]}}, upsert=True)
        await msg.reply_text(f"✅ Watermark updated!")
    elif cmd == "setdm":
        if not msg.reply_to_message: return await msg.reply_text("Reply to a formatted message to set it as Welcome DM.")
        await settings_db.update_one({"bot_id": bot_id}, {"$set": {"dm_msg_id": msg.reply_to_message.id, "dm_chat_id": msg.chat.id}}, upsert=True)
        await msg.reply_text("✅ Welcome DM successfully saved!")
    elif cmd == "addadmin" and len(msg.command) > 1:
        try:
            await settings_db.update_one({"bot_id": bot_id}, {"$addToSet": {"admins": int(msg.command[1])}}, upsert=True)
            await msg.reply_text(f"✅ Admin `{msg.command[1]}` added!")
        except: pass
    elif cmd == "deladmin" and len(msg.command) > 1:
        try:
            await settings_db.update_one({"bot_id": bot_id}, {"$pull": {"admins": int(msg.command[1])}})
            await msg.reply_text(f"🗑 Admin removed.")
        except: pass

async def broadcast(client: Client, msg: Message):
    if not await check_admin(client, msg): return
    user_states.pop(f"{client.me.id}_{msg.from_user.id}", None)
    if not msg.reply_to_message: return await msg.reply_text("Reply to a message to broadcast.")
    bot_id = client.me.id
    users = await users_db.find({"bot_id": bot_id}).to_list(length=None)
    status = await msg.reply_text(f"🚀 Broadcasting to {len(users)} users...")
    success, failed = 0, 0
    for u in users:
        try:
            await client.copy_message(u["user_id"], msg.chat.id, msg.reply_to_message.id)
            success += 1
            await asyncio.sleep(0.1) 
        except FloodWait as e: await asyncio.sleep(e.value)
        except:
            failed += 1
            await users_db.delete_one({"bot_id": bot_id, "user_id": u["user_id"]})
    await status.edit_text(f"✅ **Broadcast Complete**\nSuccess: {success}\nFailed/Blocked: {failed}")

async def auto_approve_join(client: Client, req: ChatJoinRequest):
    bot_id = client.me.id
    try:
        config = await settings_db.find_one({"bot_id": bot_id}) or {}
        delay = config.get("approve_delay", 0)
        if delay > 0:
            await pending_reqs_db.insert_one({"bot_id": bot_id, "chat_id": req.chat.id, "user_id": req.from_user.id, "first_name": req.from_user.first_name, "execute_at": time.time() + delay})
            return 
        await req.approve()
        await users_db.update_one({"bot_id": bot_id, "user_id": req.from_user.id}, {"$set": {"name": req.from_user.first_name}}, upsert=True)
        if config and config.get("dm_msg_id"): await client.copy_message(req.from_user.id, config["dm_chat_id"], config["dm_msg_id"])
    except: pass

async def background_approval_task():
    while True:
        try:
            now = time.time()
            pending = await pending_reqs_db.find({"execute_at": {"$lte": now}}).to_list(length=None)
            for req in pending:
                b_id, u_id, c_id = req["bot_id"], req["user_id"], req["chat_id"]
                if b_id in active_clones:
                    bot_client = active_clones[b_id]
                    try:
                        await bot_client.approve_chat_join_request(c_id, u_id)
                        await users_db.update_one({"bot_id": b_id, "user_id": u_id}, {"$set": {"name": req.get("first_name", "User")}}, upsert=True)
                        config = await settings_db.find_one({"bot_id": b_id})
                        if config and config.get("dm_msg_id"): await bot_client.copy_message(u_id, config["dm_chat_id"], config["dm_msg_id"])
                    except FloodWait as e: await asyncio.sleep(e.value + 1)
                    except: pass
                await pending_reqs_db.delete_one({"_id": req["_id"]})
        except: pass
        await asyncio.sleep(30) 


# ==========================================
# 👑 MASTER BOT LOGIC (TERABOX + CLONES) 👑
# ==========================================
async def master_clone(client: Client, msg: Message):
    if msg.from_user.id != OWNER_ID: return 
    if len(msg.command) < 2: return await msg.reply_text("Usage: `/clone [Bot_Token] [Optional_Admin_ID]`")
    token = msg.command[1]
    target_admin = int(msg.command[2]) if len(msg.command) > 2 else msg.from_user.id
    wait = await msg.reply_text("⏳ Booting up Clone...")
    try:
        new_bot = Client(f"clone_{token.split(':')[0]}", api_id=API_ID, api_hash=API_HASH, bot_token=token)
        await new_bot.start()
        bot_info = await new_bot.get_me()
        attach_clone_handlers(new_bot)
        active_clones[bot_info.id] = new_bot
        await clones_db.update_one({"bot_id": bot_info.id}, {"$set": {"token": token, "username": bot_info.username}}, upsert=True)
        await settings_db.update_one({"bot_id": bot_info.id}, {"$addToSet": {"admins": target_admin}}, upsert=True)
        await wait.edit_text(f"✅ **Clone Factory Success!**\nBot: @{bot_info.username} is Live!\nUser `{target_admin}` is set as Admin.")
    except Exception as e: await wait.edit_text(f"❌ Failed to start clone: {e}")

async def list_clones(client: Client, msg: Message):
    if msg.from_user.id != OWNER_ID: return 
    clones = await clones_db.find().to_list(length=None)
    if not clones: return await msg.reply_text("🤖 Koi bhi active clone bot nahi hai.")
    text = "🤖 **Active Clone Bots:**\n\n"
    for c in clones:
        text += f"• @{c.get('username', 'Unknown')} (ID: `{c['bot_id']}`) - {'🟢 Live' if c['bot_id'] in active_clones else '🔴 Offline'}\n"
    await msg.reply_text(text + "\nKisi bot ko delete karne ke liye type karein:\n`/delclone [bot_id]`")

async def delete_clone(client: Client, msg: Message):
    if msg.from_user.id != OWNER_ID: return 
    if len(msg.command) < 2: return await msg.reply_text("Usage: `/delclone [bot_id]`")
    try: target_id = int(msg.command[1])
    except: return await msg.reply_text("❌ Please ek valid Bot ID dalein.")
    if target_id in active_clones:
        try:
            await active_clones[target_id].stop()
            del active_clones[target_id]
        except: pass
    result = await clones_db.delete_one({"bot_id": target_id})
    if result.deleted_count > 0: await msg.reply_text(f"✅ Clone Bot (ID: `{target_id}`) ko disconnect kar diya gaya hai.")

# --- MASTER TERABOX COMMANDS ---
async def master_tb_commands(client: Client, msg: Message):
    if msg.from_user.id != OWNER_ID: return
    cmd = msg.command[0].lower()
    
    if cmd == "clearlinks":
        master_terabox_links[OWNER_ID] = []
        return await msg.reply_text("✅ Terabox link memory cleared!")

    if OWNER_ID not in master_terabox_links or not master_terabox_links[OWNER_ID]:
        return await safe_reply(msg, "❌ Aapne abhi tak koi valid Terabox link nahi bheja hai.")

    sorted_links = sorted(master_terabox_links[OWNER_ID], key=lambda x: x["ep"])
    
    if cmd == "set":
        await safe_reply(msg, "⏳ **List ban rahi hai, thoda wait karein...**", parse_mode=enums.ParseMode.MARKDOWN)
        title, year, main_lang, audio_tags = extract_info(sorted_links[0]["raw_text"])
        final_text = f"<b>🎥 {title.replace('<', '').replace('>', '')} {main_lang} 720p</b>\n<b>━━━━━━━━━━━━━━━━━━━━</b>\n<b>⁉️ HOW TO DOWNLOAD / PLAY ⏯️ :- <a href='https://t.me/kcsjbvxdxdxcc/3'>CLICK HERE</a></b>\n<b>━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        for item in sorted_links: final_text += f"<b>📁 EP {item['ep']}</b>\n<b>{item['url']}</b>\n\n"
        final_text += "<b>❤️‍🔥 Complete All Episodes ❤️‍🔥</b>\n\n<b>👉 Join Our Backup Channel 👈</b>\n<b>https://t.me/KOREAN_DRAMA_GT</b>"
        await safe_reply(msg, final_text.strip(), parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
        master_terabox_links[OWNER_ID] = []

    elif cmd == "post":
        await safe_reply(msg, "⏳ **Posts ban rahe hain, thoda wait karein...**", parse_mode=enums.ParseMode.MARKDOWN)
        episodes_data = {}
        for item in sorted_links:
            ep = item['ep']
            if ep not in episodes_data: episodes_data[ep] = {"urls": [], "raw_text": item["raw_text"]}
            episodes_data[ep]["urls"].append(item['url'])
            
        for ep, data in episodes_data.items():
            title, year, main_lang, audio_tags = extract_info(data["raw_text"])
            urls = data["urls"]
            link_480 = urls[0] if len(urls) > 0 else "#"
            link_720 = urls[1] if len(urls) > 1 else urls[0]
            
            final_text = f"<b>🎬 {title.replace('<', '').replace('>', '')} {main_lang}</b>\n<b>📅 YEAR: {year}</b>\n<b>💿 EPISODE:- {ep}</b>\n\n<b>🔊 [ AMZN {audio_tags} ]</b>\n\n<b>              🔮 TeraBox</b>\n<b> ▬▬▬▬▬▬▬▬▬▬▬▬▬ </b>\n<b>📁 480p ☞ <a href='{link_480}'>CLICK HERE</a></b>\n\n<b>📁 720p ☞ <a href='{link_720}'>CLICK HERE</a></b>\n<b> ▬▬▬▬▬▬▬▬▬▬▬▬▬ </b>\n<b>✅ All Episode Uploaded</b>\n\n<blockquote><b>🛑 TeraBox Ads Problem Solve:\n                                       <a href='https://t.me/kcsjbvxdxdxcc/19?single'>CLICK HERE</a></b></blockquote>\n\n<b>🚨 Join Our Backup Channel:</b>\n<b>👇 https://t.me/KOREAN_DRAMA_GT</b>"
            await safe_reply(msg, final_text, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
            await asyncio.sleep(2)
        master_terabox_links[OWNER_ID] = []

# --- MASTER TERABOX COLLECTOR ---
async def master_tb_collector(client: Client, msg: Message):
    if msg.from_user.id != OWNER_ID: return
    if msg.text and msg.text.startswith('/'): return
    
    text = msg.text or msg.caption or ""
    clean_url = None
    entities = getattr(msg, 'entities', None) or getattr(msg, 'caption_entities', None) or []
    
    for ent in entities:
        if getattr(ent, 'url', None):
            clean_url = ent.url
            break
            
    if not clean_url:
        match = re.search(r'(https?://[^\s]+|[a-zA-Z0-9.-]+\.com/s/[^\s]+)', text)
        if match:
            clean_url = match.group(1)
            if not clean_url.startswith('http'): clean_url = 'https://' + clean_url
                
    if not clean_url: return 
        
    preview_text = f"{getattr(msg.web_page, 'title', '')} {getattr(msg.web_page, 'description', '')}" if getattr(msg, 'web_page', None) else ""
    if not preview_text.strip(): preview_text = await fetch_terabox_title(clean_url)
        
    combined_text = f"{text} {preview_text}"
    ep_match = re.search(r'(?:EP|Episode|S\d+EP)\s*0*(\d+)', combined_text, re.IGNORECASE)
    
    if ep_match:
        ep_num = int(ep_match.group(1))
        if OWNER_ID not in master_terabox_links: master_terabox_links[OWNER_ID] = []
        if not any(item['ep'] == ep_num for item in master_terabox_links[OWNER_ID]):
            master_terabox_links[OWNER_ID].append({"ep": ep_num, "url": clean_url, "raw_text": combined_text})
            await safe_reply(msg, f"✅ EP {ep_num} add ho gaya!")
        else: await safe_reply(msg, f"⚠️ EP {ep_num} pehle se added hai.")
    else:
        await safe_reply(msg, f"❌ **EP Detect Nahi Hua!**\n\n**Bot ne ye padha:**\n`{combined_text[:100]}`", parse_mode=enums.ParseMode.MARKDOWN)

# ==========================================
# ATTACHING HANDLERS
# ==========================================
def attach_clone_handlers(bot: Client):
    # CLONES KE PAAS SIRF BATCH AUR SETTINGS WALE HANDLER HAIN (NO TERABOX)
    bot.add_handler(MessageHandler(clone_start, filters.command("start") & filters.private))
    bot.add_handler(MessageHandler(link_generator, filters.command(["batch", "genlink", "pbatch", "pgenlink", "cancel"]) & filters.private))
    bot.add_handler(MessageHandler(admin_settings, filters.command(["caption", "settime", "setlink", "setwatermark", "setdm", "setapprove", "setdomain", "addadmin", "deladmin"]) & filters.private))
    bot.add_handler(MessageHandler(broadcast, filters.command("broadcast") & filters.private))
    bot.add_handler(MessageHandler(message_state_handler, filters.private))
    bot.add_handler(ChatJoinRequestHandler(auto_approve_join))

async def web_server():
    app = web.Application()
    app.router.add_get('/', lambda request: web.Response(text="Master-Clone Engine Live!"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()

async def main():
    if not MASTER_TOKEN or not MONGO_URI: return logging.error("Missing Environment Variables!")
    
    master = Client("master_factory", api_id=API_ID, api_hash=API_HASH, bot_token=MASTER_TOKEN)
    
    # 👑 MASTER BOT KE APNE HANDLERS 👑
    master.add_handler(MessageHandler(master_clone, filters.command("clone") & filters.private))
    master.add_handler(MessageHandler(list_clones, filters.command("clones") & filters.private))
    master.add_handler(MessageHandler(delete_clone, filters.command("delclone") & filters.private))
    
    # MASTER TERABOX HANDLERS
    master.add_handler(MessageHandler(master_tb_commands, filters.command(["set", "post", "clearlinks"]) & filters.private))
    master.add_handler(MessageHandler(master_tb_collector, (filters.text | filters.caption) & filters.private))
    
    await master.start()
    logging.info("Master Factory Bot is Online!")
    
    for c in await clones_db.find().to_list(length=None):
        try:
            bot = Client(f"clone_{c['bot_id']}", api_id=API_ID, api_hash=API_HASH, bot_token=c['token'])
            await bot.start()
            attach_clone_handlers(bot)
            active_clones[c['bot_id']] = bot
        except: pass
        
    asyncio.create_task(background_approval_task())
    await web_server()
    await idle()
    await master.stop()

if __name__ == "__main__":
    asyncio.run(main())
