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
from pyrogram.errors import FloodWait
from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- ENV VARIABLES ---
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
MASTER_TOKEN = os.environ.get("MASTER_TOKEN", "")
MONGO_URI = os.environ.get("MONGO_URI", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0")) # Main Master Admin
PORT = int(os.environ.get("PORT", 10000))

# --- DATABASE SETUP ---
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client.clone_factory
clones_db = db.clones        
settings_db = db.settings    
users_db = db.users          
user_states = {}             

# --- HELPER FUNCTIONS ---
async def is_admin(client: Client, user_id: int) -> bool:
    if user_id == OWNER_ID: return True
    bot_id = client.me.id
    config = await settings_db.find_one({"bot_id": bot_id})
    return config and user_id in config.get("admins", [])

def encode_data(data: str) -> str:
    return base64.urlsafe_b64encode(data.encode()).decode().rstrip("=")

def decode_data(token: str) -> str:
    padding = 4 - (len(token) % 4)
    if padding != 4: token += "=" * padding
    return base64.urlsafe_b64decode(token.encode()).decode()

# ==========================================
# CLONE BOT HANDLERS 
# ==========================================

async def clone_start(client: Client, msg: Message):
    user_id = msg.from_user.id
    bot_id = client.me.id
    
    # User data save for Broadcast
    await users_db.update_one({"bot_id": bot_id, "user_id": user_id}, {"$set": {"name": msg.from_user.first_name}}, upsert=True)
    
    if len(msg.command) > 1:
        param = msg.command[1]
        
        if param.startswith("batch_") or param.startswith("genlink_"):
            token = param.split("_", 1)[1]
            data = decode_data(token).split(":")
            
            chat_id = int(data[0])
            first_id = int(data[1])
            last_id = int(data[2]) if param.startswith("batch_") else first_id
            
            wait_msg = await msg.reply_text("⏳ *Sending files blazingly fast...*", parse_mode=enums.ParseMode.MARKDOWN)
            config = await settings_db.find_one({"bot_id": bot_id})
            caption_on = config.get("caption_on", True) if config else True
            delete_delay = config.get("delete_time", 600) if config else 600
            
            sent_msgs = []
            for m_id in range(first_id, last_id + 1):
                try:
                    tg_msg = await client.get_messages(chat_id, m_id)
                    if tg_msg.empty: continue
                    
                    if caption_on and (tg_msg.document or tg_msg.video or tg_msg.audio):
                        fname = getattr(tg_msg.document or tg_msg.video or tg_msg.audio, 'file_name', '🎬 Movie File')
                        cap = f"<b>{fname}</b>\n\n<b>⚜️️ Powered By : @GTKOREANDRAMA</b>"
                        sent = await client.copy_message(msg.chat.id, chat_id, m_id, caption=cap, parse_mode=enums.ParseMode.HTML)
                    else:
                        sent = await client.copy_message(msg.chat.id, chat_id, m_id)
                    
                    sent_msgs.append(sent.id)
                    await asyncio.sleep(0.05) # SPEED INCREASED (was 0.5s)
                except FloodWait as e:
                    await asyncio.sleep(e.value + 1) # Auto-handle telegram limits
                except Exception: pass
            
            await wait_msg.delete()
            
            if sent_msgs:
                alert = await msg.reply_text(f"⚠️ *Files will be auto-deleted in {delete_delay//60} minutes!* Save them quickly.", parse_mode=enums.ParseMode.MARKDOWN)
                await asyncio.sleep(delete_delay)
                try:
                    await client.delete_messages(msg.chat.id, sent_msgs + [alert.id])
                except: pass
            return

    await msg.reply_text(f"Welcome to **{client.me.first_name}**! Send /batch or /genlink to generate links.", parse_mode=enums.ParseMode.MARKDOWN)


async def link_generator(client: Client, msg: Message):
    if not await is_admin(client, msg.from_user.id): return
    cmd = msg.command[0]
    state_key = f"{client.me.id}_{msg.from_user.id}"
    
    if cmd == "genlink":
        user_states[state_key] = {"type": "genlink", "step": "wait_msg"}
        await msg.reply_text("Forward the single file from your channel.\n(Send /cancel to abort)")
    elif cmd == "batch":
        user_states[state_key] = {"type": "batch", "step": "wait_first"}
        await msg.reply_text("Forward the FIRST message of the batch.\n(Send /cancel to abort)")
    elif cmd == "cancel":
        user_states.pop(state_key, None)
        await msg.reply_text("✅ Action cancelled.")


async def message_state_handler(client: Client, msg: Message):
    # Ignore text commands so they don't trigger "Please forward"
    if msg.text and msg.text.startswith('/'): return 
    
    user_id = msg.from_user.id
    bot_id = client.me.id
    state_key = f"{bot_id}_{user_id}"
    
    if state_key not in user_states: return
    state = user_states[state_key]
    
    if state["step"] == "wait_msg" or state["step"] == "wait_first":
        if not msg.forward_from_chat:
            return await msg.reply_text("❌ Please forward a message from a channel!")
        
        chat_id = msg.forward_from_chat.id
        msg_id = msg.forward_from_message_id
        
        if state["type"] == "genlink":
            token = encode_data(f"{chat_id}:{msg_id}")
            link = f"https://t.me/{client.me.username}?start=genlink_{token}"
            await msg.reply_text(f"✅ **Single GenLink:**\n`{link}`", parse_mode=enums.ParseMode.MARKDOWN)
            del user_states[state_key]
            
        elif state["type"] == "batch":
            state.update({"step": "wait_last", "chat_id": chat_id, "first_id": msg_id})
            await msg.reply_text("✅ First message saved! Now forward the LAST message of the batch.")
            
    elif state["step"] == "wait_last":
        if not msg.forward_from_chat:
            return await msg.reply_text("❌ Please forward from a channel!")
            
        msg_id = msg.forward_from_message_id
        chat_id = state["chat_id"]
        first_id = state["first_id"]
        
        if first_id > msg_id: first_id, msg_id = msg_id, first_id
        token = encode_data(f"{chat_id}:{first_id}:{msg_id}")
        link = f"https://t.me/{client.me.username}?start=batch_{token}"
        await msg.reply_text(f"✅ **Batch Link:**\n`{link}`", parse_mode=enums.ParseMode.MARKDOWN)
        del user_states[state_key]


async def admin_settings(client: Client, msg: Message):
    if not await is_admin(client, msg.from_user.id): return
    bot_id = client.me.id
    cmd = msg.command[0]
    
    if cmd == "caption":
        val = msg.command[1].lower() == "on" if len(msg.command)>1 else True
        await settings_db.update_one({"bot_id": bot_id}, {"$set": {"caption_on": val}}, upsert=True)
        await msg.reply_text(f"✅ Global Caption set to **{'ON' if val else 'OFF'}**")
        
    elif cmd == "settime":
        try:
            sec = int(msg.command[1])
            await settings_db.update_one({"bot_id": bot_id}, {"$set": {"delete_time": sec}}, upsert=True)
            await msg.reply_text(f"✅ Auto-Delete timer set to {sec} seconds.")
        except:
            await msg.reply_text("Usage: `/settime 600`")
            
    elif cmd == "setdm":
        if not msg.reply_to_message:
            return await msg.reply_text("Reply to a formatted message to set it as Welcome DM.")
        await settings_db.update_one({"bot_id": bot_id}, {"$set": {"dm_msg_id": msg.reply_to_message.id, "dm_chat_id": msg.chat.id}}, upsert=True)
        await msg.reply_text("✅ Welcome DM successfully saved!")
        
    elif cmd == "addadmin" and len(msg.command) > 1:
        await settings_db.update_one({"bot_id": bot_id}, {"$addToSet": {"admins": int(msg.command[1])}}, upsert=True)
        await msg.reply_text(f"✅ Admin {msg.command[1]} added successfully!")
        
    elif cmd == "deladmin" and len(msg.command) > 1:
        await settings_db.update_one({"bot_id": bot_id}, {"$pull": {"admins": int(msg.command[1])}})
        await msg.reply_text(f"🗑 Admin {msg.command[1]} removed.")


async def auto_approve_join(client: Client, req: ChatJoinRequest):
    bot_id = client.me.id
    try:
        await req.approve()
        await users_db.update_one({"bot_id": bot_id, "user_id": req.from_user.id}, {"$set": {"name": req.from_user.first_name}}, upsert=True)
        
        config = await settings_db.find_one({"bot_id": bot_id})
        if config and config.get("dm_msg_id"):
            await client.copy_message(req.from_user.id, config["dm_chat_id"], config["dm_msg_id"])
    except Exception as e:
        logging.error(f"Auto-approve failed: {e}")


async def broadcast(client: Client, msg: Message):
    if not await is_admin(client, msg.from_user.id): return
    if not msg.reply_to_message:
        return await msg.reply_text("Reply to a message to broadcast.")
    
    bot_id = client.me.id
    users = await users_db.find({"bot_id": bot_id}).to_list(length=None)
    
    status = await msg.reply_text(f"🚀 Broadcasting to {len(users)} users...")
    success, failed = 0, 0
    
    for u in users:
        try:
            await client.copy_message(u["user_id"], msg.chat.id, msg.reply_to_message.id)
            success += 1
            await asyncio.sleep(0.1) 
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except Exception:
            failed += 1
            await users_db.delete_one({"bot_id": bot_id, "user_id": u["user_id"]})
            
    await status.edit_text(f"✅ **Broadcast Complete**\nSuccess: {success}\nFailed/Blocked: {failed}")

# ==========================================
# MASTER BOT MANAGEMENT & ATTACH ROUTINES
# ==========================================

def attach_clone_handlers(bot: Client):
    bot.add_handler(MessageHandler(clone_start, filters.command("start") & filters.private))
    bot.add_handler(MessageHandler(link_generator, filters.command(["batch", "genlink", "cancel"])))
    bot.add_handler(MessageHandler(admin_settings, filters.command(["caption", "settime", "setdm", "addadmin", "deladmin"])))
    bot.add_handler(MessageHandler(broadcast, filters.command("broadcast")))
    bot.add_handler(MessageHandler(message_state_handler, filters.private))
    bot.add_handler(ChatJoinRequestHandler(auto_approve_join))

async def master_clone(client: Client, msg: Message):
    if msg.from_user.id != OWNER_ID: return
    
    if len(msg.command) < 2:
        return await msg.reply_text("Usage: `/clone [Bot_Token]`")
        
    token = msg.command[1]
    wait = await msg.reply_text("⏳ Booting up Clone...")
    
    try:
        new_bot = Client(f"clone_{token.split(':')[0]}", api_id=API_ID, api_hash=API_HASH, bot_token=token)
        await new_bot.start()
        bot_info = await new_bot.get_me()
        
        attach_clone_handlers(new_bot)
        
        # Save token to clones DB
        await clones_db.update_one({"bot_id": bot_info.id}, {"$set": {"token": token, "username": bot_info.username}}, upsert=True)
        # Make the creator an admin automatically in settings
        await settings_db.update_one({"bot_id": bot_info.id}, {"$addToSet": {"admins": msg.from_user.id}}, upsert=True)
        
        await wait.edit_text(f"✅ **Clone Factory Success!**\nBot: @{bot_info.username} is Live!\nYou have been set as Admin.")
        
    except Exception as e:
        await wait.edit_text(f"❌ Failed to start clone: {e}")


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
            logging.info(f"Clone @{c['username']} restarted successfully.")
        except Exception as e:
            logging.error(f"Error starting clone {c['bot_id']}: {e}")

async def main():
    if not MASTER_TOKEN or not MONGO_URI:
        logging.error("Missing Environment Variables!")
        return

    master = Client("master_factory", api_id=API_ID, api_hash=API_HASH, bot_token=MASTER_TOKEN)
    master.add_handler(MessageHandler(master_clone, filters.command("clone") & filters.private))
    
    await master.start()
    logging.info("Master Factory Bot is Online!")
    
    await boot_all_clones()
    await web_server()
    await idle()
    await master.stop()

if __name__ == "__main__":
    asyncio.run(main())
