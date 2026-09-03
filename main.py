import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from pyrogram.errors import UserNotParticipant
from motor.motor_asyncio import AsyncIOMotorClient

# --- 1. Bot Configuration ---
API_ID = "31660355"
API_HASH = "78292fcf0b3c508b3257e9dda9728df4"
BOT_TOKEN = "8686602550:AAEFJlK2QIkE5Dfi52QADj_j0EXtrU57QoI"
ADMINS = [7121137252] # अपना Admin ID डालें
MONGO_URI = "mongodb+srv://Mrxtejas7:Mrxtejas@cluster0.11rnf4k.mongodb.net/?appName=Cluster0"

app = Client("advanced_referral_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- 2. MongoDB Setup & Globals ---
db_client = AsyncIOMotorClient(MONGO_URI)
db = db_client["ReferralBotDB"]
users_col = db["users"]
batches_col = db["batches"]
settings_col = db["settings"] 

admin_states = {} # Admin states track करने के लिए (e.g. Broadcasting)

async def init_settings():
    if not await settings_col.find_one({"_id": "config"}):
        await settings_col.insert_one({
            "_id": "config", 
            "vip_link": "https://t.me/YourDefaultVIP", 
            "fsub_channel": "-100XXXXXXXXX", 
            "fsub_link": "https://t.me/YourChannel"
        })

# --- 3. Force Subscribe Checker ---
async def check_fsub(client, user_id):
    config = await settings_col.find_one({"_id": "config"})
    fsub_channel = config.get("fsub_channel")
    
    if not fsub_channel or fsub_channel == "-100XXXXXXXXX":
        return True 

    try:
        await client.get_chat_member(fsub_channel, user_id)
        return True
    except UserNotParticipant:
        return False
    except Exception as e:
        print(f"FSub Error: {e}") 
        return True 

# --- 4. Start & FSub / Referral Logic ---
@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    user_id = message.from_user.id
    args = message.text.split(" ")
    
    # 1. User Database Entry (Upgraded with joined_batches tracking)
    user_data = await users_col.find_one({"user_id": user_id})
    if not user_data:
        user_data = {"user_id": user_id, "is_banned": False, "total_referrals": 0, "referred_by": None, "batches": {}, "joined_batches": []}
        await users_col.insert_one(user_data)

    if user_data.get("is_banned"):
        return await message.reply_text("🚫 You are banned from using this bot.")

    # 2. Force Subscribe Check
    is_joined = await check_fsub(client, user_id)
    if not is_joined:
        config = await settings_col.find_one({"_id": "config"})
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Join Channel (जरुरी है)", url=config["fsub_link"])],
            [InlineKeyboardButton("✅ I have Joined", url=f"https://t.me/{(await app.get_me()).username}?start={args[1] if len(args) > 1 else ''}")]
        ])
        return await message.reply_text("🚨 **पहले हमारे मेन चैनल को ज्वाइन करें!**\nचैनल ज्वाइन करने के बाद ही आप बोट का इस्तेमाल कर पाएंगे।", reply_markup=btn)

    if len(args) == 1:
        return await message.reply_text("Welcome! Please use a valid batch link to start.")

    # 3. Handle Batch ID and Referral
    start_data = args[1] 
    try:
        if "_" in start_data:
            batch_id, referrer_id = start_data.split("_")
            referrer_id = int(referrer_id)
            
            if referrer_id != user_id and user_data.get("referred_by") is None:
                await users_col.update_one({"user_id": user_id}, {"$set": {"referred_by": referrer_id}})
                await users_col.update_one(
                    {"user_id": referrer_id}, 
                    {"$inc": {"total_referrals": 1, f"batches.{batch_id}": 1}}
                )
        else:
            batch_id = start_data

        # 4. Fetch Batch Data & Isolate User
        batch_info = await batches_col.find_one({"batch_id": batch_id})
        if not batch_info:
            return await message.reply_text("❌ This batch does not exist or has expired.")

        # Update that this user interacted with this specific batch (for batch broadcasting)
        await users_col.update_one({"user_id": user_id}, {"$addToSet": {"joined_batches": batch_id}})

        eng_text, hin_text = batch_info['eng_text'], batch_info['hin_text']
        bot_username = (await app.get_me()).username
        share_url = f"https://t.me/share/url?url=https://t.me/{bot_username}?start={batch_id}_{user_id}&text=Join%20this%20awesome%20group!"

        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Share with friends", url=share_url)],
            [InlineKeyboardButton(f"🔓 Unlock {batch_id}", callback_data=f"unlock_{batch_id}")],
            [InlineKeyboardButton("💎 Buy VIP (Direct Access)", callback_data="buy_vip")]
        ])

        await message.reply_text(f"{eng_text}\n\n{hin_text}", reply_markup=buttons)

    except Exception as e:
        await message.reply_text("❌ Invalid Link Format.")

# --- 5. Callbacks (Unlock & VIP) ---
@app.on_callback_query(filters.regex(r"^unlock_"))
async def unlock_button(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    batch_id = callback_query.data.split("_")[1]

    if not await check_fsub(client, user_id):
        return await callback_query.answer("❌ पहले चैनल ज्वाइन करें!", show_alert=True)

    user_data = await users_col.find_one({"user_id": user_id})
    batch_data = await batches_col.find_one({"batch_id": batch_id})
    
    if not batch_data:
        return await callback_query.answer("Batch Expired!", show_alert=True)

    req_shares = batch_data['req_shares']
    user_refs = user_data.get("batches", {}).get(batch_id, 0)

    if user_refs >= req_shares:
        success_btn = InlineKeyboardMarkup([[InlineKeyboardButton(f"✅ Enter {batch_id} Group", url=batch_data['unlock_link'])]])
        await callback_query.edit_message_text(f"🎉 **Congratulations!** {batch_id} Unlocked successfully!", reply_markup=success_btn)
    else:
        remaining = req_shares - user_refs
        await callback_query.answer(f"❌ Denied!\nYou have {user_refs}/{req_shares} shares for this batch.\nYou need {remaining} more.", show_alert=True)

@app.on_callback_query(filters.regex(r"^buy_vip$"))
async def vip_button(client, callback_query: CallbackQuery):
    config = await settings_col.find_one({"_id": "config"})
    vip_link = config.get("vip_link", "Contact Admin")
    
    vip_btn = InlineKeyboardMarkup([[InlineKeyboardButton("👑 Go to VIP Group", url=vip_link)]])
    await callback_query.edit_message_text("🌟 **VIP Access**\n\nClick below to access the VIP section directly without sharing!", reply_markup=vip_btn)


# ==========================================
# --- 6. NEW ADMIN UI & DASHBOARD SYSTEM ---
# ==========================================

@app.on_message(filters.command("admin") & filters.user(ADMINS))
async def admin_panel(client, message):
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Live Stats", callback_data="admin_stats"),
         InlineKeyboardButton("📦 Manage Batches", callback_data="admin_batches")],
        [InlineKeyboardButton("📢 Broadcast Message", callback_data="admin_bcast_menu")],
        [InlineKeyboardButton("⚙️ Settings Help", callback_data="admin_settings")]
    ])
    await message.reply_text("🛠 **Admin Control Panel**\nWelcome to your dashboard. Select an option:", reply_markup=btn)

@app.on_callback_query(filters.regex(r"^admin_"))
async def admin_callbacks(client, callback_query: CallbackQuery):
    data = callback_query.data

    if data == "admin_panel":
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Live Stats", callback_data="admin_stats"),
             InlineKeyboardButton("📦 Manage Batches", callback_data="admin_batches")],
            [InlineKeyboardButton("📢 Broadcast Message", callback_data="admin_bcast_menu")],
            [InlineKeyboardButton("⚙️ Settings Help", callback_data="admin_settings")]
        ])
        await callback_query.edit_message_text("🛠 **Admin Control Panel**\nWelcome to your dashboard. Select an option:", reply_markup=btn)

    elif data == "admin_stats":
        total_users = await users_col.count_documents({})
        total_batches = await batches_col.count_documents({})
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]])
        await callback_query.edit_message_text(f"📊 **Live Stats Dashboard**\n\n👥 Total Users: {total_users}\n📦 Active Batches: {total_batches}", reply_markup=btn)

    elif data == "admin_batches":
        batches = await batches_col.find().to_list(length=100)
        buttons = []
        for b in batches:
            buttons.append([InlineKeyboardButton(f"📦 Batch: {b['batch_id']}", callback_data=f"admin_viewbatch_{b['batch_id']}")])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
        
        await callback_query.edit_message_text("📦 **Select a Batch to manage:**\n*(To add a new batch, use /addbatch command)*", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("admin_viewbatch_"):
        batch_id = data.split("_")[2]
        b = await batches_col.find_one({"batch_id": batch_id})
        bot_username = (await app.get_me()).username
        
        # Count users who interacted with this batch
        batch_users = await users_col.count_documents({"joined_batches": batch_id})
        
        text = (f"🔍 **Batch Details: {batch_id}**\n\n"
                f"👥 Users in this batch: {batch_users}\n"
                f"🎯 Req Shares: {b['req_shares']}\n"
                f"🔗 Unlock Link: {b['unlock_link']}\n"
                f"🚀 Master Link: `https://t.me/{bot_username}?start={batch_id}`")
        
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Broadcast ONLY to this Batch", callback_data=f"admin_bcast_{batch_id}")],
            [InlineKeyboardButton("🔙 Back to Batches", callback_data="admin_batches")]
        ])
        await callback_query.edit_message_text(text, reply_markup=btn)

    elif data == "admin_bcast_menu":
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Broadcast to ALL Users", callback_data="admin_bcast_all")],
            [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]
        ])
        await callback_query.edit_message_text("📢 **Broadcast Menu**\n\nChoose target audience. For specific batch, go to 'Manage Batches' and select a batch.", reply_markup=btn)

    elif data.startswith("admin_bcast_"):
        target = data.replace("admin_bcast_", "")
        admin_id = callback_query.from_user.id
        admin_states[admin_id] = {"action": "broadcast", "target": target}
        
        target_name = "ALL USERS" if target == "all" else f"BATCH: {target}"
        await callback_query.edit_message_text(f"📝 **Broadcast Mode Active**\n\nTarget: **{target_name}**\n\n👉 Now send the message (Text, Photo, Video) you want to broadcast.\n\nSend /cancel to abort.")

    elif data == "admin_settings":
        text = ("⚙️ **Settings Help & Commands**\n\n"
                "• Add Batch:\n`/addbatch name | req | link | eng | hin`\n"
                "• Set VIP:\n`/setvip https://link`\n"
                "• Set FSub:\n`/setfsub -100123 https://link`\n"
                "• Ban/Unban:\n`/ban UserID` & `/unban UserID`")
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]])
        await callback_query.edit_message_text(text, reply_markup=btn)

# --- 7. Broadcast Message Receiver Logic ---
@app.on_message(filters.private & filters.user(ADMINS), group=1)
async def broadcast_receiver(client, message: Message):
    admin_id = message.from_user.id
    
    if admin_id in admin_states and admin_states[admin_id].get("action") == "broadcast":
        if message.text == "/cancel":
            del admin_states[admin_id]
            return await message.reply_text("✅ Broadcast cancelled.")
        
        target = admin_states[admin_id]["target"]
        del admin_states[admin_id] # State clear karein
        
        await message.reply_text("🚀 Broadcast is starting... Please wait.")
        
        if target == "all":
            users = await users_col.find().to_list(length=None)
        else:
            users = await users_col.find({"joined_batches": target}).to_list(length=None)
        
        success, failed = 0, 0
        for u in users:
            try:
                await message.copy(u["user_id"])
                success += 1
                await asyncio.sleep(0.05) # Rate limit protection
            except Exception:
                failed += 1
                
        await message.reply_text(f"✅ **Broadcast Completed!**\n🎯 Target: {target}\n📩 Sent: {success}\n❌ Failed: {failed}")


# --- 8. Old Essential Admin Commands (Intact) ---
@app.on_message(filters.command("setvip") & filters.user(ADMINS))
async def set_vip(client, message):
    if len(message.command) < 2: return await message.reply_text("Use: `/setvip link`")
    new_link = message.command[1]
    await settings_col.update_one({"_id": "config"}, {"$set": {"vip_link": new_link}}, upsert=True)
    await message.reply_text(f"✅ VIP Link updated.")

@app.on_message(filters.command("setfsub") & filters.user(ADMINS))
async def set_fsub(client, message):
    args = message.text.split(" ")
    if len(args) < 3: return await message.reply_text("Use: `/setfsub -100ID Link`")
    await settings_col.update_one({"_id": "config"}, {"$set": {"fsub_channel": args[1], "fsub_link": args[2]}}, upsert=True)
    await message.reply_text(f"✅ FSub updated!")

@app.on_message(filters.command("ban") & filters.user(ADMINS))
async def ban_user(client, message):
    if len(message.command) < 2: return
    await users_col.update_one({"user_id": int(message.command[1])}, {"$set": {"is_banned": True}})
    await message.reply_text(f"🚫 Banned.")

@app.on_message(filters.command("unban") & filters.user(ADMINS))
async def unban_user(client, message):
    if len(message.command) < 2: return
    await users_col.update_one({"user_id": int(message.command[1])}, {"$set": {"is_banned": False}})
    await message.reply_text(f"✅ Unbanned.")

@app.on_message(filters.command("addbatch") & filters.user(ADMINS))
async def add_batch(client, message):
    try:
        data = message.text.split("|")
        batch_id = data[0].split(" ")[1].strip()
        req_shares = int(data[1].strip())
        unlock_link = data[2].strip()
        eng, hin = data[3].strip(), data[4].strip()

        await batches_col.update_one(
            {"batch_id": batch_id},
            {"$set": {"eng_text": eng, "hin_text": hin, "unlock_link": unlock_link, "req_shares": req_shares}},
            upsert=True
        )
        bot_username = (await app.get_me()).username
        await message.reply_text(f"✅ Batch '{batch_id}' added!\n🔗 Link: `https://t.me/{bot_username}?start={batch_id}`\n\n👉 Now you can view this in /admin panel.")
    except Exception as e:
        await message.reply_text("❌ Error. Format:\n`/addbatch name | 5 | link | eng | hin`")

@app.on_message(filters.command("stats") & filters.user(ADMINS))
async def get_stats(client, message):
    await admin_panel(client, message) # Route to new UI

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_settings()) 
    print("🚀 Ultra-Fast FSub Referral Bot with UI is starting...")
    app.run()
