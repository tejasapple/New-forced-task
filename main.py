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

# Admin states ट्रैक करने के लिए (UI Inputs के लिए)
admin_states = {} 

async def init_settings():
    if not await settings_col.find_one({"_id": "config"}):
        await settings_col.insert_one({
            "_id": "config", 
            "vip_link": "https://t.me/YourDefaultVIP", 
            "fsub_channel": "-100XXXXXXXXX", 
            "fsub_link": "https://t.me/YourChannel"
        })

# --- 3. UI Admin Panel Helper ---
async def send_admin_panel(message_or_callback):
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add New Batch", callback_data="add_batch_start")],
        [InlineKeyboardButton("📦 Manage Batches", callback_data="admin_batches"),
         InlineKeyboardButton("📊 Live Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("📢 Broadcast Message", callback_data="admin_bcast_menu")],
        [InlineKeyboardButton("⚙️ Bot Settings (FSub/VIP)", callback_data="admin_settings")]
    ])
    text = "🛠 **Admin Control Panel**\n\nस्वागत है! यहाँ से आप पूरे बोट को बिना कोई कमांड टाइप किए कंट्रोल कर सकते हैं। नीचे दिए गए बटनों का इस्तेमाल करें:"
    
    if isinstance(message_or_callback, Message):
        await message_or_callback.reply_text(text, reply_markup=btn)
    else:
        await message_or_callback.edit_message_text(text, reply_markup=btn)

# --- 4. Strict Telegram Force Subscribe Checker ---
async def check_fsub(client, user_id):
    if user_id in ADMINS:
        return True # Admin bypass
    
    config = await settings_col.find_one({"_id": "config"})
    fsub_channel = config.get("fsub_channel")
    
    # अगर चैनल सेट नहीं है, तो ट्रू रिटर्न करे
    if not fsub_channel or fsub_channel == "-100XXXXXXXXX":
        return True 

    try:
        # Convert to int properly if it's a numeric string like "-100..."
        channel_id = int(fsub_channel) if str(fsub_channel).lstrip('-').isdigit() else fsub_channel
        await client.get_chat_member(channel_id, user_id)
        return True
    except UserNotParticipant:
        return False
    except Exception as e:
        print(f"FSub Error: {e}")
        # अगर कोई और एरर आता है (जैसे बोट चैनल में एडमिन नहीं है), तो रोक देना बेहतर है ताकि एडमिन को पता चले
        return True 

# --- 5. Start & FSub Logic ---
async def send_fsub_message(message, start_data):
    config = await settings_col.find_one({"_id": "config"})
    fsub_link = config.get("fsub_link", "https://t.me/YourChannel")
    
    text = (
        "**Please join our update channel to use this bot.**\n\n"
        "**बॉट का उपयोग करने के लिए कृपया हमारे अपडेट चैनल को ज्वाइन करें।**"
    )
    
    # अगर स्टार्ट डेटा है तो उसे ट्राई अगेन में पास करें
    cb_data = f"checkfsub_{start_data}" if start_data else "checkfsub_none"
    
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=fsub_link)],
        [InlineKeyboardButton("🔄 Try Again", callback_data=cb_data)]
    ])
    
    if isinstance(message, Message):
        await message.reply_text(text, reply_markup=btn)
    else:
        await message.edit_message_text(text, reply_markup=btn)

@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    user_id = message.from_user.id
    args = message.text.split(" ")
    
    # 🌟 Auto Detect Admin & Open Panel (No parameters)
    if user_id in ADMINS and len(args) == 1:
        if user_id in admin_states:
            del admin_states[user_id] 
        return await send_admin_panel(message)

    # 1. Normal User DB Entry
    user_data = await users_col.find_one({"user_id": user_id})
    if not user_data:
        user_data = {"user_id": user_id, "is_banned": False, "total_referrals": 0, "referred_by": None, "batches": {}, "joined_batches": []}
        await users_col.insert_one(user_data)

    if user_data.get("is_banned"):
        return await message.reply_text("🚫 You are banned from using this bot.")

    start_data = args[1] if len(args) > 1 else ""

    # 2. Strict Force Subscribe Check (Telegram) - FSub First!
    is_joined = await check_fsub(client, user_id)
    if not is_joined:
        return await send_fsub_message(message, start_data)

    # 3. If Joined, Process the Batch Link
    if start_data:
        await process_batch_start(client, message, user_id, start_data)
    else:
        await message.reply_text("👋 Welcome! Please use a valid batch link to start.")

# --- Helper to process Batch Info ---
async def process_batch_start(client, message_or_callback, user_id, start_data):
    try:
        user_data = await users_col.find_one({"user_id": user_id})
        
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

        batch_info = await batches_col.find_one({"batch_id": batch_id})
        if not batch_info:
            text = "❌ This batch does not exist or has expired."
            if isinstance(message_or_callback, Message):
                return await message_or_callback.reply_text(text)
            else:
                return await message_or_callback.edit_message_text(text)

        await users_col.update_one({"user_id": user_id}, {"$addToSet": {"joined_batches": batch_id}})

        bot_username = (await app.get_me()).username
        share_url = f"https://t.me/share/url?url=https://t.me/{bot_username}?start={batch_id}_{user_id}&text=Unlock%20this%20exclusive%20group!"
        
        req_shares = batch_info['req_shares']

        # Fixed Standard Bilingual Message (No need to ask during batch setup)
        final_text = (
            f"**To get the new channel/link, please share this bot with {req_shares} people and click unlock.**\n\n"
            f"**नया चैनल/लिंक पाने के लिए कृपया इस बॉट को {req_shares} लोगों के साथ शेयर करें और अनलॉक पर दबाएं।**"
        )

        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"📤 Share with {req_shares} friends", url=share_url)],
            [InlineKeyboardButton(f"🔓 Unlock", callback_data=f"unlock_{batch_id}")],
            [InlineKeyboardButton("💎 Buy VIP (Direct Access)", callback_data="buy_vip")]
        ])

        if isinstance(message_or_callback, Message):
            await message_or_callback.reply_text(final_text, reply_markup=buttons)
        else:
            await message_or_callback.edit_message_text(final_text, reply_markup=buttons)

    except Exception as e:
        print(e)
        text = "❌ Invalid Link Format or Batch Expired."
        if isinstance(message_or_callback, Message):
            await message_or_callback.reply_text(text)
        else:
            await message_or_callback.edit_message_text(text)

# --- 6. Normal User Callbacks (FSub, Unlock & VIP) ---
@app.on_callback_query(filters.regex(r"^checkfsub_"))
async def check_fsub_callback(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    start_data = callback_query.data.replace("checkfsub_", "")

    is_joined = await check_fsub(client, user_id)
    if not is_joined:
        return await callback_query.answer("❌ Please join the channel first! / कृपया पहले चैनल ज्वाइन करें!", show_alert=True)

    if start_data and start_data != "none":
        await process_batch_start(client, callback_query, user_id, start_data)
    else:
        await callback_query.edit_message_text("👋 Welcome! Please use a valid batch link to start.")

@app.on_callback_query(filters.regex(r"^unlock_"))
async def unlock_button(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    batch_id = callback_query.data.split("_")[1]

    if not await check_fsub(client, user_id):
        return await callback_query.answer("❌ पहले टेलीग्राम चैनल ज्वाइन करें!", show_alert=True)

    user_data = await users_col.find_one({"user_id": user_id})
    batch_data = await batches_col.find_one({"batch_id": batch_id})
    
    if not batch_data:
        return await callback_query.answer("Batch Expired or Removed!", show_alert=True)

    req_shares = batch_data['req_shares']
    user_refs = user_data.get("batches", {}).get(batch_id, 0)

    if user_refs >= req_shares:
        success_btn = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Enter Group / Channel", url=batch_data['unlock_link'])]])
        await callback_query.edit_message_text(f"🎉 **Congratulations!**\nBatch Unlocked successfully. Click below to join:", reply_markup=success_btn)
    else:
        remaining = req_shares - user_refs
        await callback_query.answer(f"❌ Denied!\nYou have {user_refs}/{req_shares} shares.\nYou need {remaining} more shares to unlock.", show_alert=True)

@app.on_callback_query(filters.regex(r"^buy_vip$"))
async def vip_button(client, callback_query: CallbackQuery):
    config = await settings_col.find_one({"_id": "config"})
    vip_link = config.get("vip_link", "Contact Admin")
    vip_btn = InlineKeyboardMarkup([[InlineKeyboardButton("👑 Go to VIP Area", url=vip_link)]])
    await callback_query.edit_message_text("🌟 **VIP Access**\n\nClick below to access directly without sharing!", reply_markup=vip_btn)


# ==========================================
# --- 7. NEW BUTTON-BASED ADMIN SYSTEM ---
# ==========================================

@app.on_callback_query(filters.regex(r"^admin_") | filters.regex(r"^add_batch_start$") | filters.regex(r"^set_") | filters.regex(r"^edit"))
async def admin_callbacks(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in ADMINS:
        return await callback_query.answer("You are not an admin!", show_alert=True)

    data = callback_query.data

    if data == "admin_panel":
        if user_id in admin_states:
            del admin_states[user_id] 
        await send_admin_panel(callback_query)

    elif data == "admin_stats":
        total_users = await users_col.count_documents({})
        total_batches = await batches_col.count_documents({})
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="admin_panel")]])
        await callback_query.edit_message_text(f"📊 **Live Stats Dashboard**\n\n👥 Total Users: {total_users}\n📦 Active Batches: {total_batches}", reply_markup=btn)

    elif data == "admin_batches":
        batches = await batches_col.find().to_list(length=100)
        buttons = []
        for b in batches:
            buttons.append([InlineKeyboardButton(f"📦 Batch: {b['batch_id']}", callback_data=f"admin_viewbatch_{b['batch_id']}")])
        buttons.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="admin_panel")])
        await callback_query.edit_message_text("📦 **Manage Batches**\nSelect a batch to view details:", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("admin_viewbatch_"):
        batch_id = data.split("_")[2]
        b = await batches_col.find_one({"batch_id": batch_id})
        if not b:
            return await callback_query.answer("Batch Not Found!", show_alert=True)
            
        bot_username = (await app.get_me()).username
        batch_users = await users_col.count_documents({"joined_batches": batch_id})
        
        text = (f"🔍 **Batch Details: {batch_id}**\n\n"
                f"👥 Users in this batch: {batch_users}\n"
                f"🎯 Req Shares: {b['req_shares']}\n"
                f"🔗 Unlock Link: {b['unlock_link']}\n\n"
                f"🚀 **Direct Link:** `https://t.me/{bot_username}?start={batch_id}`")
        
        # New UI Buttons for Batch directly!
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Edit Unlock Link", callback_data=f"editlink_{batch_id}"),
             InlineKeyboardButton("👥 Edit Share Count", callback_data=f"editreq_{batch_id}")],
            [InlineKeyboardButton("📢 Broadcast to this Batch", callback_data=f"admin_bcast_{batch_id}")],
            [InlineKeyboardButton("🗑 Delete Batch", callback_data=f"admin_delbatch_{batch_id}")],
            [InlineKeyboardButton("🔙 Back to Batches", callback_data="admin_batches")]
        ])
        await callback_query.edit_message_text(text, reply_markup=btn)
        
    elif data.startswith("editlink_"):
        batch_id = data.split("_")[1]
        admin_states[user_id] = {"action": "edit_batch_link", "batch_id": batch_id}
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"admin_viewbatch_{batch_id}")]])
        await callback_query.edit_message_text(f"🔗 **Edit Link for {batch_id}**\n\n👉 Send the new Unlock Link:", reply_markup=btn)

    elif data.startswith("editreq_"):
        batch_id = data.split("_")[1]
        admin_states[user_id] = {"action": "edit_batch_req", "batch_id": batch_id}
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"admin_viewbatch_{batch_id}")]])
        await callback_query.edit_message_text(f"👥 **Edit Shares for {batch_id}**\n\n👉 Send the new Share Count (Numbers only):", reply_markup=btn)

    elif data.startswith("admin_delbatch_"):
        batch_id = data.split("_")[2]
        await batches_col.delete_one({"batch_id": batch_id})
        await callback_query.answer("Batch Deleted Successfully!", show_alert=True)
        await send_admin_panel(callback_query)

    elif data == "admin_bcast_menu":
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Broadcast to ALL Users", callback_data="admin_bcast_all")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="admin_panel")]
        ])
        await callback_query.edit_message_text("📢 **Broadcast Menu**\n\nChoose target audience. For specific batch, go to 'Manage Batches'.", reply_markup=btn)

    elif data.startswith("admin_bcast_"):
        target = data.replace("admin_bcast_", "")
        admin_states[user_id] = {"action": "broadcast", "target": target}
        
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]])
        await callback_query.edit_message_text(f"📝 **Broadcast Mode Active**\n\nTarget: **{target}**\n\n👉 Now send the message (Text, Photo, Video) you want to broadcast.", reply_markup=btn)

    # --- UI Based Batch Creation Trigger ---
    elif data == "add_batch_start":
        admin_states[user_id] = {"action": "add_batch_name"}
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]])
        await callback_query.edit_message_text("➕ **Create New Batch (Step 1/3)**\n\n👉 Send the **Name / ID** of the batch (e.g. `Movie1`, `BatchA`):", reply_markup=btn)

    # --- Settings UI ---
    elif data == "admin_settings":
        config = await settings_col.find_one({"_id": "config"})
        text = (f"⚙️ **Bot Settings**\n\n"
                f"**Current FSub ID:** `{config.get('fsub_channel')}`\n"
                f"**Current FSub Link:** {config.get('fsub_link')}\n"
                f"**Current VIP Link:** {config.get('vip_link')}\n\n"
                "क्या चेंज करना है चुनें:")
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔧 Set FSub Channel ID", callback_data="set_fsub_id")],
            [InlineKeyboardButton("🔗 Set FSub Link", callback_data="set_fsub_link")],
            [InlineKeyboardButton("💎 Set VIP Link", callback_data="set_vip_link")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="admin_panel")]
        ])
        await callback_query.edit_message_text(text, reply_markup=btn)
        
    elif data == "set_fsub_id":
        admin_states[user_id] = {"action": "set_fsub_id"}
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]])
        await callback_query.edit_message_text("👉 Send the new **Telegram FSub Channel ID** (e.g., `-10012345678`):\n*(Note: बोट को इस चैनल में एडमिन होना ज़रूरी है)*", reply_markup=btn)
        
    elif data == "set_fsub_link":
        admin_states[user_id] = {"action": "set_fsub_link"}
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]])
        await callback_query.edit_message_text("👉 Send the new **Telegram Channel Invite Link**:", reply_markup=btn)

    elif data == "set_vip_link":
        admin_states[user_id] = {"action": "set_vip_link"}
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]])
        await callback_query.edit_message_text("👉 Send the new **VIP Direct Link**:", reply_markup=btn)


# --- 8. STATE MACHINE (Handles all text inputs from Admin) ---
@app.on_message(filters.private & filters.user(ADMINS) & ~filters.command(["start"]))
async def admin_state_machine(client, message: Message):
    admin_id = message.from_user.id
    if admin_id not in admin_states:
        return 

    state_info = admin_states[admin_id]
    action = state_info.get("action")
    
    # --- Broadcast Logic ---
    if action == "broadcast":
        target = state_info["target"]
        del admin_states[admin_id] 
        
        await message.reply_text("🚀 Broadcast is starting... Please wait.")
        users = await users_col.find().to_list(length=None) if target == "all" else await users_col.find({"joined_batches": target}).to_list(length=None)
        
        success, failed = 0, 0
        for u in users:
            try:
                await message.copy(u["user_id"])
                success += 1
                await asyncio.sleep(0.05)
            except:
                failed += 1
                
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="admin_panel")]])
        return await message.reply_text(f"✅ **Broadcast Completed!**\n🎯 Target: {target}\n📩 Sent: {success}\n❌ Failed: {failed}", reply_markup=btn)

    # --- Edit Specific Batch Info ---
    elif action == "edit_batch_link":
        batch_id = state_info["batch_id"]
        await batches_col.update_one({"batch_id": batch_id}, {"$set": {"unlock_link": message.text}})
        del admin_states[admin_id]
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Batch", callback_data=f"admin_viewbatch_{batch_id}")]])
        await message.reply_text(f"✅ Unlock Link for '{batch_id}' updated!", reply_markup=btn)

    elif action == "edit_batch_req":
        if not message.text.isdigit():
            return await message.reply_text("❌ Please send a valid number.")
        batch_id = state_info["batch_id"]
        await batches_col.update_one({"batch_id": batch_id}, {"$set": {"req_shares": int(message.text)}})
        del admin_states[admin_id]
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Batch", callback_data=f"admin_viewbatch_{batch_id}")]])
        await message.reply_text(f"✅ Required shares for '{batch_id}' updated!", reply_markup=btn)

    # --- Add Batch Logic (Shortened to 3 Steps) ---
    elif action == "add_batch_name":
        admin_states[admin_id]["batch_id"] = message.text.replace(" ", "_")
        admin_states[admin_id]["action"] = "add_batch_req"
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]])
        await message.reply_text("🎯 **(Step 2/3)**\nकितने रेफ़रल चाहिए इस बैच को अनलॉक करने के लिए? (सिर्फ नंबर लिखें, जैसे: `5`)", reply_markup=btn)
        
    elif action == "add_batch_req":
        if not message.text.isdigit():
            return await message.reply_text("❌ Please send a valid number (e.g. 5)")
        admin_states[admin_id]["req_shares"] = int(message.text)
        admin_states[admin_id]["action"] = "add_batch_link"
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]])
        await message.reply_text("🔗 **(Step 3/3)**\nअनलॉक होने के बाद यूज़र को कौन सा लिंक मिलना चाहिए? (यहाँ लिंक भेजें)", reply_markup=btn)

    elif action == "add_batch_link":
        admin_states[admin_id]["unlock_link"] = message.text
        data = admin_states[admin_id]
        batch_id = data["batch_id"]
        
        await batches_col.update_one(
            {"batch_id": batch_id},
            {"$set": {
                "req_shares": data["req_shares"],
                "unlock_link": data["unlock_link"]
            }},
            upsert=True
        )
        del admin_states[admin_id] 
        bot_username = (await app.get_me()).username
        link = f"https://t.me/{bot_username}?start={batch_id}"
        
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="admin_panel")]])
        await message.reply_text(f"✅ **Batch '{batch_id}' Successfully Created!**\n\n🎯 Req Shares: {data['req_shares']}\n🚀 **Direct Batch Link:**\n`{link}`\n\nइस लिंक को आप सीधे भी शेयर कर सकते हैं।", reply_markup=btn)

    # --- Settings Logic ---
    elif action == "set_fsub_id":
        await settings_col.update_one({"_id": "config"}, {"$set": {"fsub_channel": message.text}}, upsert=True)
        del admin_states[admin_id]
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Settings", callback_data="admin_settings")]])
        await message.reply_text("✅ FSub Channel ID Updated!", reply_markup=btn)
        
    elif action == "set_fsub_link":
        await settings_col.update_one({"_id": "config"}, {"$set": {"fsub_link": message.text}}, upsert=True)
        del admin_states[admin_id]
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Settings", callback_data="admin_settings")]])
        await message.reply_text("✅ FSub Link Updated!", reply_markup=btn)

    elif action == "set_vip_link":
        await settings_col.update_one({"_id": "config"}, {"$set": {"vip_link": message.text}}, upsert=True)
        del admin_states[admin_id]
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Settings", callback_data="admin_settings")]])
        await message.reply_text("✅ VIP Link Updated!", reply_markup=btn)

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_settings()) 
    print("🚀 Fully Automated Telegram Admin UI Bot is starting...")
    app.run()
