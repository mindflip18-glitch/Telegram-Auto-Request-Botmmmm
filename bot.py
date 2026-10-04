import os
import re
import time
import base64
import asyncio
import logging
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

# --- HELPER FUNCTIONS ---
async def check_admin(client: Client, msg: Message) -> bool:
    user_id = msg.from_user.id
    if user_id == OWNER_ID: return True
    
    bot_id = client.me.id
    config = await settings_db.find_one({"bot_id": bot_id})
    if config and user_id in config.get("admins", []):
        return True
        
    return False # SILENT IGNORE

def encode_data(data: str) -> str:
    return base64.urlsafe_b64encode(data.encode()).decode().rstrip("=")

def decode_data(token: str) -> str:
    padding = 4 - (len(token) % 4)
    if padding != 4: token += "=" * padding
    return base64.urlsafe_b64decode(token.encode()).decode()

# --- FORMAT URL HELPER ---
async def generate_final_link(client: Client, token_type: str, token: str) -> str:
    bot_id = client.me.id
    config = await settings_db.find_one({"bot_id": bot_id}) or {}
    custom_domain = config.get("custom_domain", None)
    
    if custom_domain:
        if custom_domain.endswith('/'): custom_domain = custom_domain[:-1]
        return f"{custom_domain}?start={token_type}_{token}"
    
    return f"https://t.me/{client.me.username}?start={token_type}_{token}"

# ==========================================
# CLONE BOT HANDLERS 
# ==========================================

async def clone_start(client: Client, msg: Message):
    user_id = msg.from_user.id
    bot_id = client.me.id
    
    user_states.pop(f"{bot_id}_{user_id}", None)
    await users_db.update_one({"bot_id": bot_id, "user_id": user_id}, {"$set": {"name": msg.from_user.first_name}}, upsert=True)
    
    if len(msg.command) <= 1: return 
        
    param = msg.command[1]
    
    if param.startswith("batch_") or param.startswith("genlink_") or param.startswith("pbatch_") or param.startswith("pgenlink_"):
        
        is_permanent = param.startswith("pbatch_") or param.startswith("pgenlink_")
        
        token = param.split("_", 1)[1]
        data = decode_data(token).split(":")
        
        chat_id = int(data[0])
        first_id = int(data[1])
        last_id = int(data[2]) if ("batch" in param) else first_id
        
        wait_msg = await msg.reply_text("⏳ <i>Sending your files, please wait...</i>", parse_mode=enums.ParseMode.HTML)
        
        config = await settings_db.find_one({"bot_id": bot_id}) or {}
        caption_on = config.get("caption_on", True)
        delete_delay = config.get("delete_time", 900)
        cap_link = config.get("caption_link", "https://t.me/KOREAN_DRAMA_GT")
        watermark = config.get("watermark", "⚜️ Powered By : @GTKOREANDRAMA")
        
        sent_msgs = []
        for m_id in range(first_id, last_id + 1):
            try:
                tg_msg = await client.get_messages(chat_id, m_id)
                if tg_msg.empty: continue
                
                if caption_on and (tg_msg.document or tg_msg.video or tg_msg.audio):
                    media_obj = tg_msg.document or tg_msg.video or tg_msg.audio
                    raw_fname = getattr(media_obj, 'file_name', None) or '🎬 Movie/Series File'
                    safe_fname = str(raw_fname).replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
                    cap = f"<b><a href='{cap_link}'>{safe_fname}</a></b>\n\n<b>{watermark}</b>"
                    sent = await client.copy_message(msg.chat.id, chat_id, m_id, caption=cap, parse_mode=enums.ParseMode.HTML)
                else:
                    sent = await client.copy_message(msg.chat.id, chat_id, m_id)
                
                sent_msgs.append(sent.id)
                await asyncio.sleep(0.05) 
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
            except Exception: pass
        
        await wait_msg.delete()
        
        if sent_msgs and not is_permanent:
            time_text = f"{delete_delay // 60} minutes" if delete_delay >= 60 else f"{delete_delay} seconds"
            alert_text = (
                "⚠️ <u><b>Important:</b></u>\n\n"
                f"<i>All Messages will be deleted after <b>{time_text}</b>. Please save or forward these "
                "messages to your <b>personal saved messages</b> to avoid losing them!</i>"
            )
            alert = await msg.reply_text(alert_text, parse_mode=enums.ParseMode.HTML)
            await asyncio.sleep(delete_delay)
            try: await client.delete_messages(msg.chat.id, sent_msgs + [alert.id])
            except: pass


async def link_generator(client: Client, msg: Message):
    if not await check_admin(client, msg): return
    cmd = msg.command[0]
    state_key = f"{client.me.id}_{msg.from_user.id}"
    user_states.pop(state_key, None)
    
    if cmd == "genlink":
        user_states[state_key] = {"type": "genlink", "step": "wait_msg"}
        await msg.reply_text("✅ **Auto-Delete GenLink Mode Active**\nForward the single file from your channel.\n(Send /cancel to abort)")
    elif cmd == "pgenlink":
        user_states[state_key] = {"type": "pgenlink", "step": "wait_msg"}
        await msg.reply_text("✅ **Permanent GenLink Mode Active**\nForward the single file.\n(Send /cancel to abort)")
    elif cmd == "batch":
        user_states[state_key] = {"type": "batch", "step": "wait_first"}
        await msg.reply_text("✅ **Auto-Delete Batch Mode Active**\nForward the FIRST message of the batch.\n(Send /cancel to abort)")
    elif cmd == "pbatch":
        user_states[state_key] = {"type": "pbatch", "step": "wait_first"}
        await msg.reply_text("✅ **Permanent Batch Mode Active**\nForward the FIRST message of the batch.\n(Send /cancel to abort)")
    elif cmd == "cancel":
        await msg.reply_text("✅ Action cancelled. Bot is back to normal.")


async def message_state_handler(client: Client, msg: Message):
    state_key = f"{client.me.id}_{msg.from_user.id}"
    if msg.text and msg.text.startswith('/'): 
        user_states.pop(state_key, None)
        return 
        
    if state_key not in user_states: return
    state = user_states[state_key]
    
    if state["step"] == "wait_msg" or state["step"] == "wait_first":
        if not msg.forward_from_chat: return await msg.reply_text("❌ Please forward a message from a channel!")
        chat_id = msg.forward_from_chat.id
        msg_id = msg.forward_from_message_id
        
        if state["type"] in ["genlink", "pgenlink"]:
            token = encode_data(f"{chat_id}:{msg_id}")
            link = await generate_final_link(client, state["type"], token)
            is_perm = "Permanent" if "p" in state["type"] else "Auto-Delete"
            await msg.reply_text(f"✅ **Single Link ({is_perm}):**\n`{link}`", parse_mode=enums.ParseMode.MARKDOWN)
            del user_states[state_key]
            
        elif state["type"] in ["batch", "pbatch"]:
            state.update({"step": "wait_last", "chat_id": chat_id, "first_id": msg_id})
            await msg.reply_text("✅ First message saved! Now forward the LAST message of the batch.")
            
    elif state["step"] == "wait_last":
        if not msg.forward_from_chat: return await msg.reply_text("❌ Please forward from a channel!")
        msg_id = msg.forward_from_message_id
        chat_id = state["chat_id"]
        first_id = state["first_id"]
        
        if first_id > msg_id: first_id, msg_id = msg_id, first_id
        token = encode_data(f"{chat_id}:{first_id}:{msg_id}")
        link = await generate_final_link(client, state["type"], token)
        is_perm = "Permanent" if "p" in state["type"] else "Auto-Delete"
        await msg.reply_text(f"✅ **Batch Link ({is_perm}):**\n`{link}`", parse_mode=enums.ParseMode.MARKDOWN)
        del user_states[state_key]


async def admin_settings(client: Client, msg: Message):
    if not await check_admin(client, msg): return
    bot_id = client.me.id
    cmd = msg.command[0]
    user_states.pop(f"{bot_id}_{msg.from_user.id}", None)
    
    if cmd == "caption":
        val = msg.command[1].lower() == "on" if len(msg.command)>1 else True
        await settings_db.update_one({"bot_id": bot_id}, {"$set": {"caption_on": val}}, upsert=True)
        await msg.reply_text(f"✅ Global Caption set to **{'ON' if val else 'OFF'}**")
        
    elif cmd == "settime":
        try:
            sec = int(msg.command[1])
            await settings_db.update_one({"bot_id": bot_id}, {"$set": {"delete_time": sec}}, upsert=True)
            time_txt = f"{sec//60} minutes" if sec >= 60 else f"{sec} seconds"
            await msg.reply_text(f"✅ Auto-Delete timer set to {time_txt}.")
        except:
            await msg.reply_text("Usage: `/settime 900` (for 15 minutes)")
            
    elif cmd == "setapprove":
        try:
            delay = int(msg.command[1])
            await settings_db.update_one({"bot_id": bot_id}, {"$set": {"approve_delay": delay}}, upsert=True)
            delay_txt = f"{delay//3600} hours" if delay >= 3600 else (f"{delay//60} minutes" if delay >= 60 else f"{delay} seconds")
            if delay == 0: delay_txt = "Instant (0 seconds)"
            await msg.reply_text(f"✅ Join Request Auto-Approve delay set to: **{delay_txt}**")
        except:
            await msg.reply_text("Usage: `/setapprove 14400` (for 4 hours delay)")
            
    elif cmd == "setdomain" and len(msg.command) > 1:
        domain_url = msg.command[1].lower()
        if domain_url == "off":
            await settings_db.update_one({"bot_id": bot_id}, {"$set": {"custom_domain": None}}, upsert=True)
            await msg.reply_text("✅ Custom Domain removed. Generating normal Telegram links now.")
        else:
            if not domain_url.startswith("http"):
                domain_url = "https://" + domain_url
            await settings_db.update_one({"bot_id": bot_id}, {"$set": {"custom_domain": domain_url}}, upsert=True)
            await msg.reply_text(f"✅ Custom Domain saved!\nAll new links will use: {domain_url}")
            
    elif cmd == "setlink" and len(msg.command) > 1:
        new_link = msg.command[1]
        await settings_db.update_one({"bot_id": bot_id}, {"$set": {"caption_link": new_link}}, upsert=True)
        await msg.reply_text(f"✅ Caption File Link updated to:\n{new_link}")
        
    elif cmd == "setwatermark" and len(msg.command) > 1:
        new_wm = msg.text.split(None, 1)[1]
        await settings_db.update_one({"bot_id": bot_id}, {"$set": {"watermark": new_wm}}, upsert=True)
        await msg.reply_text(f"✅ Watermark updated to:\n{new_wm}")
            
    elif cmd == "setdm":
        if not msg.reply_to_message: return await msg.reply_text("Reply to a formatted message to set it as Welcome DM.")
        await settings_db.update_one({"bot_id": bot_id}, {"$set": {"dm_msg_id": msg.reply_to_message.id, "dm_chat_id": msg.chat.id}}, upsert=True)
        await msg.reply_text("✅ Welcome DM successfully saved!")
        
    elif cmd == "addadmin" and len(msg.command) > 1:
        try:
            new_admin_id = int(msg.command[1])
            await settings_db.update_one({"bot_id": bot_id}, {"$addToSet": {"admins": new_admin_id}}, upsert=True)
            await msg.reply_text(f"✅ Admin `{new_admin_id}` added successfully!")
        except: pass
            
    elif cmd == "deladmin" and len(msg.command) > 1:
        try:
            target_admin_id = int(msg.command[1])
            await settings_db.update_one({"bot_id": bot_id}, {"$pull": {"admins": target_admin_id}})
            await msg.reply_text(f"🗑 Admin `{target_admin_id}` removed.")
        except: pass


async def auto_approve_join(client: Client, req: ChatJoinRequest):
    bot_id = client.me.id
    try:
        config = await settings_db.find_one({"bot_id": bot_id}) or {}
        delay = config.get("approve_delay", 0)
        
        if delay > 0:
            execute_at = time.time() + delay
            await pending_reqs_db.insert_one({
                "bot_id": bot_id, "chat_id": req.chat.id,
                "user_id": req.from_user.id, "first_name": req.from_user.first_name,
                "execute_at": execute_at
            })
            return 
            
        await req.approve()
        await users_db.update_one({"bot_id": bot_id, "user_id": req.from_user.id}, {"$set": {"name": req.from_user.first_name}}, upsert=True)
        if config and config.get("dm_msg_id"):
            await client.copy_message(req.from_user.id, config["dm_chat_id"], config["dm_msg_id"])
    except Exception as e:
        logging.error(f"Join Request Error: {e}")

async def background_approval_task():
    while True:
        try:
            now = time.time()
            pending_requests = await pending_reqs_db.find({"execute_at": {"$lte": now}}).to_list(length=None)
            for req in pending_requests:
                b_id = req["bot_id"]
                u_id = req["user_id"]
                c_id = req["chat_id"]
                if b_id in active_clones:
                    bot_client = active_clones[b_id]
                    try:
                        await bot_client.approve_chat_join_request(c_id, u_id)
                        await users_db.update_one({"bot_id": b_id, "user_id": u_id}, {"$set": {"name": req.get("first_name", "User")}}, upsert=True)
                        config = await settings_db.find_one({"bot_id": b_id})
                        if config and config.get("dm_msg_id"):
                            await bot_client.copy_message(u_id, config["dm_chat_id"], config["dm_msg_id"])
                    except FloodWait as e: await asyncio.sleep(e.value + 1)
                    except (UserIsBlocked, PeerIdInvalid, RPCError): pass 
                    except Exception: pass
                await pending_reqs_db.delete_one({"_id": req["_id"]})
        except Exception: pass
        await asyncio.sleep(30) 


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
        except Exception:
            failed += 1
            await users_db.delete_one({"bot_id": bot_id, "user_id": u["user_id"]})
            
    await status.edit_text(f"✅ **Broadcast Complete**\nSuccess: {success}\nFailed/Blocked: {failed}")


# ==========================================
# MASTER BOT MANAGEMENT & CLONE CONTROLS
# ==========================================

def attach_clone_handlers(bot: Client):
    bot.add_handler(MessageHandler(clone_start, filters.command("start") & filters.private))
    bot.add_handler(MessageHandler(link_generator, filters.command(["batch", "genlink", "pbatch", "pgenlink", "cancel"])))
    bot.add_handler(MessageHandler(admin_settings, filters.command(["caption", "settime", "setlink", "setwatermark", "setdm", "setapprove", "setdomain", "addadmin", "deladmin"])))
    bot.add_handler(MessageHandler(broadcast, filters.command("broadcast")))
    bot.add_handler(MessageHandler(message_state_handler, filters.private))
    bot.add_handler(ChatJoinRequestHandler(auto_approve_join))

async def master_clone(client: Client, msg: Message):
    if msg.from_user.id != OWNER_ID: return 
    if len(msg.command) < 2: return await msg.reply_text("Usage: `/clone [Bot_Token] [Optional_Admin_ID]`")
        
    token = msg.command[1]
    target_admin = msg.from_user.id
    if len(msg.command) > 2:
        try: target_admin = int(msg.command[2])
        except: pass
            
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
    except Exception as e:
        await wait.edit_text(f"❌ Failed to start clone: {e}")

async def list_clones(client: Client, msg: Message):
    if msg.from_user.id != OWNER_ID: return 
    clones = await clones_db.find().to_list(length=None)
    if not clones: return await msg.reply_text("🤖 Koi bhi active clone bot nahi hai.")
        
    text = "🤖 **Active Clone Bots:**\n\n"
    for c in clones:
        status = "🟢 Live" if c['bot_id'] in active_clones else "🔴 Offline"
        text += f"• @{c.get('username', 'Unknown')} (ID: `{c['bot_id']}`) - {status}\n"
    text += "\nKisi bot ko delete karne ke liye type karein:\n`/delclone [bot_id]`"
    await msg.reply_text(text)

async def delete_clone(client: Client, msg: Message):
    if msg.from_user.id != OWNER_ID: return 
    if len(msg.command) < 2: return await msg.reply_text("Usage: `/delclone [bot_id]`")
    try: target_id = int(msg.command[1])
    except: return await msg.reply_text("❌ Please ek valid Bot ID dalein (Numbers only).")
        
    if target_id in active_clones:
        try:
            await active_clones[target_id].stop()
            del active_clones[target_id]
        except Exception: pass
            
    result = await clones_db.delete_one({"bot_id": target_id})
    if result.deleted_count > 0: await msg.reply_text(f"✅ Clone Bot (ID: `{target_id}`) ko disconnect kar diya gaya hai.")
    else: await msg.reply_text("❌ Ye Bot ID database mein nahi mili.")

# ==========================================
# SERVER & STARTUP ROUTINES
# ==========================================

async def web_server():
    async def handle(request): return web.Response(text="Master-Clone Engine Live!")
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

async def boot_all_clones():
    clones = await clones_db.find().to_list(length=None)
    for c in clones:
        try:
            bot = Client(f"clone_{c['bot_id']}", api_id=API_ID, api_hash=API_HASH, bot_token=c['token'])
            await bot.start()
            attach_clone_handlers(bot)
            active_clones[c['bot_id']] = bot
            logging.info(f"Clone @{c.get('username', 'Bot')} restarted successfully.")
        except Exception as e:
            logging.error(f"Error starting clone {c['bot_id']}: {e}")

async def main():
    if not MASTER_TOKEN or not MONGO_URI:
        logging.error("Missing Environment Variables!")
        return

    master = Client("master_factory", api_id=API_ID, api_hash=API_HASH, bot_token=MASTER_TOKEN)
    
    # Master Start is REMOVED as requested. Back to completely silent.
    master.add_handler(MessageHandler(master_clone, filters.command("clone") & filters.private))
    master.add_handler(MessageHandler(list_clones, filters.command("clones") & filters.private))
    master.add_handler(MessageHandler(delete_clone, filters.command("delclone") & filters.private))
    
    await master.start()
    logging.info("Master Factory Bot is Online!")
    
    await boot_all_clones()
    asyncio.create_task(background_approval_task())
    
    await web_server()
    await idle()
    await master.stop()

if __name__ == "__main__":
    asyncio.run(main())
