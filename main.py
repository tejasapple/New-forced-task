import asyncio
import random
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message, ChatJoinRequest
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
            "fsub": [], 
            "fsub_style": "primary" # Added FSub Style Configuration
        })

# --- NEW: Dynamic Batch Regenerator ---
async def regenerate_batch(batch_id, update_dict=None):
    """
    जब भी कोई बैच अपडेट होता है (कलर, लिंक आदि), 
    तो यह एक नया सुरक्षित बैच लिंक बना देता है और पुराने को हटा देता है।
    """
    if update_dict is None:
        update_dict = {}
        
    base = batch_id.split("_rev")[0]
    new_batch_id = f"{base}_rev{random.randint(10000, 99999)}"
    update_dict["batch_id"] = new_batch_id
    
    # Update batches collection
    await batches_col.update_one({"batch_id": batch_id}, {"$set": update_dict})
    
    # Update users joined array (ताकि पुराने यूज़र्स का डेटा खराब न हो)
    await users_col.update_many({"joined_batches": batch_id}, {"$set": {"joined_batches.$": new_batch_id}})
    
    # Rename referral key
    await users_col.update_many(
        {f"batches.{batch_id}": {"$exists": True}},
        {"$rename": {f"batches.{batch_id}": f"batches.{new_batch_id}"}}
    )
    
    return new_batch_id

# --- Helper Function for Safe Edits (Prevents Bot Freezing) ---
async def safe_edit(callback_query: CallbackQuery, text: str, reply_markup=None):
    """अगर टेक्स्ट सेम हो, तो यह टेलीग्राम का MessageNotModified एरर बाईपास कर देगा (बोट लॉक नहीं होगा)"""
    try:
        if reply_markup:
            await callback_query.edit_message_text(text, reply_markup=reply_markup)
        else:
            await callback_query.edit_message_text(text)
    except Exception:
        pass 

# --- Helper Function for Link Parsing (Supports @username) ---
def format_telegram_link(link: str) -> str:
    link = link.strip()
    if link.startswith("@"):
        return f"https://t.me/{link[1:]}"
    return link

# --- Helper Function to Normalize Kurigram Styles ---
def normalize_style(style_str: str) -> str:
    """Kurigram और पुराने कलर लॉजिक के लिए सही कलर नेम्स सुनिश्चित करता है"""
    if not style_str: return "primary"
    s = str(style_str).strip().lower()
    if s in ["success", "green"]: return "success"
    if s in ["danger", "red"]: return "danger"
    if s in ["default", "grey", "secondary", "white"]: return "secondary"
    if s in ["primary", "blue"]: return "primary"
    return "primary"

# --- Helper Function for Styled Buttons (100% Error Proof) ---
def create_btn(text, callback_data=None, url=None, style="primary"):
    kwargs = {"text": text}
    if callback_data:
        kwargs["callback_data"] = callback_data
    if url:
        kwargs["url"] = url
        
    safe_style = normalize_style(style)
    
    # पुरानी फाइल का इंटीग्रेटेड कलर लॉजिक: api_kwargs सपोर्ट
    if safe_style != "secondary":
        kwargs["api_kwargs"] = {"style": safe_style}
        
    try:
        # अगर लाइब्रेरी सीधे api_kwargs सपोर्ट करती है
        return InlineKeyboardButton(**kwargs)
    except TypeError:
        # अगर लाइब्रेरी natively सपोर्ट नहीं करती है, तो फॉलबैक लॉजिक
        if "api_kwargs" in kwargs:
            del kwargs["api_kwargs"]
        try:
            # Kurigram/Modified Pyrogram के लिए 
            return InlineKeyboardButton(**kwargs, style=safe_style)
        except TypeError:
            # अगर नार्मल Pyrogram है तो बिना एरर के डिफ़ॉल्ट बटन बनाएगा लेकिन एट्रिब्यूट सेव रखेगा
            btn = InlineKeyboardButton(**kwargs)
            setattr(btn, "style", safe_style) 
            return btn

# --- 3. UI Admin Panel Helper ---
async def send_admin_panel(message_or_callback):
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add New Batch", callback_data="add_batch_start")],
        [InlineKeyboardButton("📦 Manage Batches", callback_data="admin_batches"),
         InlineKeyboardButton("📊 Live Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("📢 Broadcast Message", callback_data="admin_bcast_menu")],
        [InlineKeyboardButton("⚙️ FSub Settings (Colors & Links)", callback_data="admin_settings")]
    ])
    text = "🛠 **Admin Control Panel**\n\nस्वागत है! यहाँ से आप पूरे बोट को कंट्रोल कर सकते हैं। नीचे दिए गए बटनों का इस्तेमाल करें:"
    
    if isinstance(message_or_callback, Message):
        await message_or_callback.reply_text(text, reply_markup=btn)
    else:
        await safe_edit(message_or_callback, text, reply_markup=btn)

# --- 4. Auto Approve Join Requests (अगर कोई रिक्वेस्ट डालता है तो) ---
@app.on_chat_join_request()
async def auto_approve_requests(client, message: ChatJoinRequest):
    try:
        await client.approve_chat_join_request(message.chat.id, message.from_user.id)
    except Exception as e:
        print(f"Auto Approve Error: {e}")

# --- 5. Strict Telegram Force Subscribe Checker (Multiple Channels - Optimized) ---
async def check_fsub(client, user_id):
    if user_id in ADMINS:
        return [] 
    
    config = await settings_col.find_one({"_id": "config"})
    fsubs = config.get("fsub", [])
    
    if not fsubs:
        return [] 

    async def check_single_channel(ch):
        try:
            raw_id = ch["id"]
            if str(raw_id).lstrip('-').isdigit():
                channel_id = int(raw_id)
            else:
                channel_id = str(raw_id)
                
            await client.get_chat_member(channel_id, user_id)
            return None
        except UserNotParticipant:
            return ch
        except Exception as e:
            print(f"FSub Error for {ch['id']}: {e}")
            return None 

    results = await asyncio.gather(*(check_single_channel(ch) for ch in fsubs))
    not_joined = [res for res in results if res is not None]
            
    return not_joined

async def send_fsub_message(message, start_data, not_joined):
    text = (
        "**Access Restricted! 🚫**\n\n"
        "**बॉट का उपयोग करने के लिए कृपया हमारे सभी अपडेट चैनल्स को ज्वाइन करें। (अगर चैनल प्राइवेट है, तो रिक्वेस्ट सेंड करें, बोट ऑटो-एक्सेप्ट कर लेगा)**\n\n"
        "**Please join all our update channels to use this bot.**"
    )
    
    cb_data = f"checkfsub_{start_data}" if start_data else "checkfsub_none"
    
    # Get Global FSub Color
    config = await settings_col.find_one({"_id": "config"})
    fsub_style = config.get("fsub_style", "primary")
    
    buttons = []
    for idx, ch in enumerate(not_joined):
        buttons.append([create_btn(f"📢 Join Channel {idx+1}", url=ch["link"], style=fsub_style)])
        
    buttons.append([create_btn("🔄 Try Again", callback_data=cb_data, style="success")])
    
    btn = InlineKeyboardMarkup(buttons)
    
    if isinstance(message, Message):
        await message.reply_text(text, reply_markup=btn)
    else:
        await safe_edit(message, text, reply_markup=btn)

# --- 6. Start & Batch Logic ---
@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    user_id = message.from_user.id
    args = message.text.split(" ")
    
    if user_id in ADMINS and len(args) == 1:
        if user_id in admin_states:
            del admin_states[user_id] 
        return await send_admin_panel(message)

    user_data = await users_col.find_one({"user_id": user_id})
    if not user_data:
        user_data = {
            "user_id": user_id, 
            "is_banned": False, 
            "total_referrals": 0, 
            "referred_by": None, 
            "batches": {}, 
            "joined_batches": [],
            "join_date": datetime.now() 
        }
        await users_col.insert_one(user_data)

    if user_data.get("is_banned"):
        return await message.reply_text("🚫 You are banned from using this bot.")

    start_data = args[1] if len(args) > 1 else ""

    not_joined = await check_fsub(client, user_id)
    if not_joined:
        return await send_fsub_message(message, start_data, not_joined)

    if start_data:
        await process_batch_start(client, message, user_id, start_data)
    else:
        await message.reply_text("👋 Welcome! Please use a valid batch link to start.")

async def process_batch_start(client, message_or_callback, user_id, start_data):
    try:
        user_data = await users_col.find_one({"user_id": user_id})
        
        # Backward compatibility for _, but new structure uses -
        if "-" in start_data:
            batch_id, referrer_id = start_data.rsplit("-", 1)
            referrer_id = int(referrer_id)
        elif "_" in start_data and start_data.rsplit("_", 1)[1].isdigit():
            batch_id, referrer_id = start_data.rsplit("_", 1)
            referrer_id = int(referrer_id)
        else:
            batch_id = start_data
            referrer_id = None

        if referrer_id and referrer_id != user_id and user_data.get("referred_by") is None:
            await users_col.update_one({"user_id": user_id}, {"$set": {"referred_by": referrer_id}})
            await users_col.update_one(
                {"user_id": referrer_id}, 
                {"$inc": {"total_referrals": 1, f"batches.{batch_id}": 1}}
            )

        batch_info = await batches_col.find_one({"batch_id": batch_id})
        if not batch_info:
            text = "❌ This batch link is old, has been updated, or does not exist."
            if isinstance(message_or_callback, Message):
                return await message_or_callback.reply_text(text)
            else:
                return await safe_edit(message_or_callback, text)

        await users_col.update_one({"user_id": user_id}, {"$addToSet": {"joined_batches": batch_id}})

        bot_username = (await app.get_me()).username
        
        # New link creation structure avoids _ clash
        share_url = f"https://t.me/share/url?url=https://t.me/{bot_username}?start={batch_id}-{user_id}&text=Unlock%20this%20exclusive%20group!"
        
        req_shares = batch_info['req_shares']

        final_text = (
            f"**To get the new channel/link, please share this bot with {req_shares} people and click unlock.**\n\n"
            f"**नया चैनल/लिंक पाने के लिए कृपया इस बॉट को {req_shares} लोगों के साथ शेयर करें और अनलॉक पर दबाएं।**"
        )

        style_share = batch_info.get("style_share", "primary")
        style_unlock = batch_info.get("style_unlock", "secondary") 
        style_vip = batch_info.get("style_vip", "success") 

        buttons = [
            [create_btn(f"📤 Share with {req_shares} friends", url=share_url, style=style_share)],
            [create_btn(f"🔓 Unlock", callback_data=f"unlock_{batch_id}", style=style_unlock)]
        ]
        
        if batch_info.get("vip_link") and batch_info["vip_link"] != "none":
            buttons.append([create_btn("💎 Buy VIP+", url=batch_info["vip_link"], style=style_vip)])

        if isinstance(message_or_callback, Message):
            await message_or_callback.reply_text(final_text, reply_markup=InlineKeyboardMarkup(buttons))
        else:
            await safe_edit(message_or_callback, final_text, reply_markup=InlineKeyboardMarkup(buttons))

    except Exception as e:
        print(f"Start Processing Error: {e}")
        text = "❌ Invalid Link Format or Batch Expired."
        if isinstance(message_or_callback, Message):
            await message_or_callback.reply_text(text)
        else:
            await safe_edit(message_or_callback, text)

# --- 7. Normal User Callbacks (FSub, Unlock) ---
@app.on_callback_query(filters.regex(r"^checkfsub_"))
async def check_fsub_callback(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    start_data = callback_query.data.replace("checkfsub_", "")

    not_joined = await check_fsub(client, user_id)
    if not_joined:
        return await callback_query.answer("❌ कृपया पहले सभी चैनल ज्वाइन करें या रिक्वेस्ट सेंड करें!", show_alert=True)
    
    await callback_query.answer() 
    if start_data and start_data != "none":
        await process_batch_start(client, callback_query, user_id, start_data)
    else:
        await safe_edit(callback_query, "👋 Welcome! Please use a valid batch link to start.")

@app.on_callback_query(filters.regex(r"^unlock_"))
async def unlock_button(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    batch_id = callback_query.data.replace("unlock_", "")

    if await check_fsub(client, user_id):
        return await callback_query.answer("❌ पहले सभी टेलीग्राम चैनल ज्वाइन करें!", show_alert=True)

    user_data = await users_col.find_one({"user_id": user_id})
    batch_data = await batches_col.find_one({"batch_id": batch_id})
    
    if not batch_data:
        return await callback_query.answer("Batch Expired or Removed!", show_alert=True)

    req_shares = batch_data['req_shares']
    user_refs = user_data.get("batches", {}).get(batch_id, 0)

    if user_refs >= req_shares:
        await callback_query.answer()
        success_btn = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Enter Group / Channel", url=batch_data['unlock_link'])]])
        await safe_edit(callback_query, f"🎉 **Congratulations!**\nBatch Unlocked successfully. Click below to join:", reply_markup=success_btn)
    else:
        remaining = req_shares - user_refs
        await callback_query.answer(f"❌ Access Restricted!\n\nYou need {remaining} more shares to unlock this batch.", show_alert=True)

# ==========================================
# --- 8. BUTTON-BASED ADMIN SYSTEM ---
# ==========================================

@app.on_callback_query(filters.regex(r"^admin_") | filters.regex(r"^add_batch_start$") | filters.regex(r"^edit") | filters.regex(r"^add_fsub") | filters.regex(r"^remove_fsub") | filters.regex(r"^del_fsub_") | filters.regex(r"^btnselect_") | filters.regex(r"^setstyle_") | filters.regex(r"^setfsubstyle_"))
async def admin_callbacks(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in ADMINS:
        return await callback_query.answer("You are not an admin!", show_alert=True)

    data = callback_query.data

    if data == "admin_panel":
        await callback_query.answer()
        if user_id in admin_states:
            del admin_states[user_id] 
        await send_admin_panel(callback_query)

    elif data == "admin_stats":
        await callback_query.answer()
        total_users = await users_col.count_documents({})
        total_batches = await batches_col.count_documents({})
        
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_users = await users_col.count_documents({"join_date": {"$gte": today_start}})
        
        shared_users = await users_col.count_documents({"referred_by": {"$ne": None}})
        
        batches = await batches_col.find().to_list(length=None)
        batch_stats = ""
        for b in batches:
            b_count = await users_col.count_documents({"joined_batches": b['batch_id']})
            batch_stats += f"▪️ **{b['batch_id']}**: {b_count} users\n"

        text = (
            f"📊 **Live Stats Dashboard**\n\n"
            f"👥 **Total Active Users:** {total_users}\n"
            f"📈 **Today's New Users:** {today_users}\n"
            f"🔗 **Users from Shares:** {shared_users}\n"
            f"📦 **Total Active Batches:** {total_batches}\n\n"
            f"📋 **Batch-wise Activity:**\n{batch_stats if batch_stats else 'No active batches'}"
        )
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="admin_panel")]])
        await safe_edit(callback_query, text, reply_markup=btn)

    elif data == "admin_batches":
        await callback_query.answer()
        batches = await batches_col.find().to_list(length=100)
        buttons = []
        for b in batches:
            buttons.append([InlineKeyboardButton(f"📦 Batch: {b['batch_id']}", callback_data=f"admin_viewbatch_{b['batch_id']}")])
        buttons.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="admin_panel")])
        await safe_edit(callback_query, "📦 **Manage Batches**\nSelect a batch to view details:", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("admin_viewbatch_"):
        await callback_query.answer()
        batch_id = data.replace("admin_viewbatch_", "")
        b = await batches_col.find_one({"batch_id": batch_id})
        if not b:
            return await callback_query.answer("Batch Not Found!", show_alert=True)
            
        bot_username = (await app.get_me()).username
        batch_users = await users_col.count_documents({"joined_batches": batch_id})
        
        text = (f"🔍 **Batch Details: {batch_id}**\n\n"
                f"👥 Users in this batch: {batch_users}\n"
                f"🎯 Req Shares: {b['req_shares']}\n"
                f"🔗 Unlock Link: {b['unlock_link']}\n"
                f"💎 Buy VIP+ Link: {b.get('vip_link', 'Not Set')}\n\n"
                f"🚀 **Direct Link:** `https://t.me/{bot_username}?start={batch_id}`")
        
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Edit Unlock Link", callback_data=f"editlink_{batch_id}"),
             InlineKeyboardButton("💎 Edit Buy VIP+ Link", callback_data=f"editvip_{batch_id}")],
            [InlineKeyboardButton("👥 Edit Share Count", callback_data=f"editreq_{batch_id}")],
            [InlineKeyboardButton("🎨 Edit Button Styles", callback_data=f"admin_btnstyles_{batch_id}")],
            [InlineKeyboardButton("📢 Broadcast to this Batch", callback_data=f"admin_bcast_{batch_id}")],
            [InlineKeyboardButton("🗑 Delete Batch", callback_data=f"admin_delbatch_{batch_id}")],
            [InlineKeyboardButton("🔙 Back to Batches", callback_data="admin_batches")]
        ])
        await safe_edit(callback_query, text, reply_markup=btn)
        
    elif data.startswith("admin_btnstyles_"):
        await callback_query.answer()
        batch_id = data.replace("admin_btnstyles_", "")
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Share Button", callback_data=f"btnselect_share_{batch_id}")],
            [InlineKeyboardButton("🔓 Unlock Button", callback_data=f"btnselect_unlock_{batch_id}")],
            [InlineKeyboardButton("💎 Buy VIP+ Button", callback_data=f"btnselect_vip_{batch_id}")],
            [InlineKeyboardButton("🔙 Back to Batch", callback_data=f"admin_viewbatch_{batch_id}")]
        ])
        await safe_edit(callback_query, f"🎨 **Edit Button Styles for {batch_id}**\n\nकिस बटन का स्टाइल/कलर बदलना है?", reply_markup=btn)

    elif data.startswith("btnselect_"):
        await callback_query.answer()
        _, btn_type, batch_id = data.split("_", 2)
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔵 Primary", callback_data=f"setstyle_{btn_type}_primary_{batch_id}")],
            [InlineKeyboardButton("🟢 Success (Green)", callback_data=f"setstyle_{btn_type}_success_{batch_id}")],
            [InlineKeyboardButton("🔴 Danger (Red)", callback_data=f"setstyle_{btn_type}_danger_{batch_id}")],
            [InlineKeyboardButton("⚪️ Default (Grey)", callback_data=f"setstyle_{btn_type}_secondary_{batch_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data=f"admin_btnstyles_{batch_id}")]
        ])
        await safe_edit(callback_query, f"🎨 **Select Style for '{btn_type.capitalize()}' Button:**", reply_markup=btn)

    elif data.startswith("setstyle_"):
        _, btn_type, style, batch_id = data.split("_", 3)
        db_field = f"style_{btn_type}"
        
        # LINK REGENERATION CALLED HERE
        new_batch_id = await regenerate_batch(batch_id, {db_field: style})
        await callback_query.answer(f"Style updated! New Secure Batch Link Generated.", show_alert=True)
        
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Share Button", callback_data=f"btnselect_share_{new_batch_id}")],
            [InlineKeyboardButton("🔓 Unlock Button", callback_data=f"btnselect_unlock_{new_batch_id}")],
            [InlineKeyboardButton("💎 Buy VIP+ Button", callback_data=f"btnselect_vip_{new_batch_id}")],
            [InlineKeyboardButton("🔙 Back to Batch", callback_data=f"admin_viewbatch_{new_batch_id}")]
        ])
        await safe_edit(callback_query, f"✅ **{btn_type.capitalize()}** Button set to **{style.capitalize()}**\n\n🚀 **नया बैच लिंक बन गया है (पुराना लिंक अब काम नहीं करेगा)!**\n\n🎨 **Edit Button Styles**", reply_markup=btn)

    elif data.startswith("editlink_"):
        await callback_query.answer()
        batch_id = data.replace("editlink_", "")
        admin_states[user_id] = {"action": "edit_batch_link", "batch_id": batch_id}
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"admin_viewbatch_{batch_id}")]])
        await safe_edit(callback_query, f"🔗 **Edit Unlock Link for {batch_id}**\n\n👉 Send the new Unlock Link (You can use @username):", reply_markup=btn)

    elif data.startswith("editvip_"):
        await callback_query.answer()
        batch_id = data.replace("editvip_", "")
        admin_states[user_id] = {"action": "edit_batch_vip", "batch_id": batch_id}
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"admin_viewbatch_{batch_id}")]])
        await safe_edit(callback_query, f"💎 **Edit Buy VIP+ Link for {batch_id}**\n\n👉 Send the new Buy VIP+ direct link or @username:", reply_markup=btn)

    elif data.startswith("editreq_"):
        await callback_query.answer()
        batch_id = data.replace("editreq_", "")
        admin_states[user_id] = {"action": "edit_batch_req", "batch_id": batch_id}
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"admin_viewbatch_{batch_id}")]])
        await safe_edit(callback_query, f"👥 **Edit Shares for {batch_id}**\n\n👉 Send the new Share Count (Numbers only):", reply_markup=btn)

    elif data.startswith("admin_delbatch_"):
        batch_id = data.replace("admin_delbatch_", "")
        await batches_col.delete_one({"batch_id": batch_id})
        await callback_query.answer("Batch Deleted Successfully!", show_alert=True)
        await send_admin_panel(callback_query)

    elif data == "admin_bcast_menu":
        await callback_query.answer()
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Broadcast to ALL Users", callback_data="admin_bcast_all")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="admin_panel")]
        ])
        await safe_edit(callback_query, "📢 **Broadcast Menu**\n\nChoose target audience. For specific batch, go to 'Manage Batches'.", reply_markup=btn)

    elif data.startswith("admin_bcast_") and data != "admin_bcast_menu":
        await callback_query.answer()
        target = data.replace("admin_bcast_", "")
        admin_states[user_id] = {"action": "broadcast", "target": target}
        
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]])
        await safe_edit(callback_query, f"📝 **Broadcast Mode Active**\n\nTarget: **{target}**\n\n👉 Now send the message (Text, Photo, Video) you want to broadcast.", reply_markup=btn)

    # --- Batch Creation (4 Steps with VIP) ---
    elif data == "add_batch_start":
        await callback_query.answer()
        admin_states[user_id] = {"action": "add_batch_name"}
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]])
        await safe_edit(callback_query, "➕ **Create New Batch (Step 1/4)**\n\n👉 Send the **Name / ID** of the batch (e.g. `Movie1`, `BatchA`):", reply_markup=btn)

    # --- Multiple Settings UI (With FSub Color) ---
    elif data == "admin_settings":
        await callback_query.answer()
        config = await settings_col.find_one({"_id": "config"})
        fsubs = config.get("fsub", [])
        fsub_style = config.get("fsub_style", "primary")
        
        text = f"⚙️ **Multi-FSub Settings**\n\n**Current Channels: {len(fsubs)}**\n\n"
        for idx, f in enumerate(fsubs):
            text += f"{idx+1}. ID: `{f['id']}`\n"
            
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add FSub Channel", callback_data="add_fsub_start")],
            [InlineKeyboardButton("🗑 Remove FSub Channel", callback_data="remove_fsub_menu")],
            [InlineKeyboardButton(f"🎨 FSub Button Color ({fsub_style.capitalize()})", callback_data="admin_fsub_color")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="admin_panel")]
        ])
        await safe_edit(callback_query, text, reply_markup=btn)
        
    elif data == "admin_fsub_color":
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔵 Primary", callback_data="setfsubstyle_primary")],
            [InlineKeyboardButton("🟢 Success (Green)", callback_data="setfsubstyle_success")],
            [InlineKeyboardButton("🔴 Danger (Red)", callback_data="setfsubstyle_danger")],
            [InlineKeyboardButton("⚪️ Default (Grey)", callback_data="setfsubstyle_secondary")],
            [InlineKeyboardButton("🔙 Back", callback_data="admin_settings")]
        ])
        await safe_edit(callback_query, "🎨 **Select Color Style for FSub Buttons:**", reply_markup=btn)

    elif data.startswith("setfsubstyle_"):
        style = data.split("_")[1]
        await settings_col.update_one({"_id": "config"}, {"$set": {"fsub_style": style}})
        await callback_query.answer(f"FSub Style updated to {style.capitalize()}!", show_alert=True)
        
        config = await settings_col.find_one({"_id": "config"})
        fsubs = config.get("fsub", [])
        text = f"⚙️ **Multi-FSub Settings**\n\n**Current Channels: {len(fsubs)}**\n\n"
        for idx, f in enumerate(fsubs):
            text += f"{idx+1}. ID: `{f['id']}`\n"
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add FSub Channel", callback_data="add_fsub_start")],
            [InlineKeyboardButton("🗑 Remove FSub Channel", callback_data="remove_fsub_menu")],
            [InlineKeyboardButton(f"🎨 FSub Button Color ({style.capitalize()})", callback_data="admin_fsub_color")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="admin_panel")]
        ])
        await safe_edit(callback_query, text, reply_markup=btn)
        
    elif data == "add_fsub_start":
        await callback_query.answer()
        admin_states[user_id] = {"action": "add_fsub_id"}
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_settings")]])
        await safe_edit(callback_query, "👉 Send the new **Telegram FSub Channel ID or Username** (e.g., `-10012345678` or `@mychannel`):\n*(Note: बोट को इस चैनल में एडमिन होना ज़रूरी है)*", reply_markup=btn)
        
    elif data == "add_fsub_link":
        fsub_id = admin_states[admin_id]["fsub_id"]
        fsub_link = format_telegram_link(message.text)
        
        config = await settings_col.find_one({"_id": "config"})
        fsubs = config.get("fsub", [])
        fsubs.append({"id": fsub_id, "link": fsub_link})
        
        await settings_col.update_one({"_id": "config"}, {"$set": {"fsub": fsubs}})
        del admin_states[admin_id]
        
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Settings", callback_data="admin_settings")]])
        await message.reply_text("✅ नया FSub Channel सफलतापूर्वक जोड़ दिया गया है!", reply_markup=btn)

@app.on_callback_query(filters.regex(r"^remove_fsub_menu$"))
async def remove_fsub_menu_callback(client, callback_query: CallbackQuery):
    await callback_query.answer()
    config = await settings_col.find_one({"_id": "config"})
    fsubs = config.get("fsub", [])
    if not fsubs:
        return await callback_query.answer("No FSub Channels to remove!", show_alert=True)
        
    buttons = []
    for idx, f in enumerate(fsubs):
        buttons.append([InlineKeyboardButton(f"❌ Remove Channel {idx+1} ({f['id']})", callback_data=f"del_fsub_{idx}")])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_settings")])
    
    await safe_edit(callback_query, "🗑 **Select FSub channel to remove:**", reply_markup=InlineKeyboardMarkup(buttons))
    
@app.on_callback_query(filters.regex(r"^del_fsub_"))
async def del_fsub_callback(client, callback_query: CallbackQuery):
    idx = int(callback_query.data.split("_")[2])
    config = await settings_col.find_one({"_id": "config"})
    fsubs = config.get("fsub", [])
    if 0 <= idx < len(fsubs):
        fsubs.pop(idx)
        await settings_col.update_one({"_id": "config"}, {"$set": {"fsub": fsubs}})
        
    await callback_query.answer("Channel removed!", show_alert=True)
    
    config = await settings_col.find_one({"_id": "config"})
    fsubs = config.get("fsub", [])
    fsub_style = config.get("fsub_style", "primary")
    text = f"⚙️ **Multi-FSub Settings**\n\n**Current Channels: {len(fsubs)}**\n\n"
    for i, f in enumerate(fsubs):
        text += f"{i+1}. ID: `{f['id']}`\n"
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add FSub Channel", callback_data="add_fsub_start")],
        [InlineKeyboardButton("🗑 Remove FSub Channel", callback_data="remove_fsub_menu")],
        [InlineKeyboardButton(f"🎨 FSub Button Color ({fsub_style.capitalize()})", callback_data="admin_fsub_color")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="admin_panel")]
    ])
    await safe_edit(callback_query, text, reply_markup=btn)

# --- 9. STATE MACHINE (Handles text inputs) ---
@app.on_message(filters.private & filters.user(ADMINS) & ~filters.command(["start"]))
async def admin_state_machine(client, message: Message):
    admin_id = message.from_user.id
    if admin_id not in admin_states:
        return 

    state_info = admin_states[admin_id]
    action = state_info.get("action")
    
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

    elif action == "edit_batch_link":
        batch_id = state_info["batch_id"]
        new_link = format_telegram_link(message.text)
        
        # Link Regeneration Applied here
        new_batch_id = await regenerate_batch(batch_id, {"unlock_link": new_link})
        del admin_states[admin_id]
        
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Batch", callback_data=f"admin_viewbatch_{new_batch_id}")]])
        await message.reply_text(f"✅ Unlock Link updated!\n🚀 **New Batch ID Generated:** `{new_batch_id}` (Old link has been disabled)", reply_markup=btn)

    elif action == "edit_batch_vip":
        batch_id = state_info["batch_id"]
        new_link = format_telegram_link(message.text)
        
        new_batch_id = await regenerate_batch(batch_id, {"vip_link": new_link})
        del admin_states[admin_id]
        
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Batch", callback_data=f"admin_viewbatch_{new_batch_id}")]])
        await message.reply_text(f"✅ Buy VIP+ Link updated!\n🚀 **New Batch ID Generated:** `{new_batch_id}` (Old link has been disabled)", reply_markup=btn)

    elif action == "edit_batch_req":
        if not message.text.isdigit():
            return await message.reply_text("❌ Please send a valid number.")
        batch_id = state_info["batch_id"]
        
        new_batch_id = await regenerate_batch(batch_id, {"req_shares": int(message.text)})
        del admin_states[admin_id]
        
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Batch", callback_data=f"admin_viewbatch_{new_batch_id}")]])
        await message.reply_text(f"✅ Required shares updated!\n🚀 **New Batch ID Generated:** `{new_batch_id}` (Old link has been disabled)", reply_markup=btn)

    elif action == "add_batch_name":
        admin_states[admin_id]["batch_id"] = message.text.replace(" ", "_")
        admin_states[admin_id]["action"] = "add_batch_req"
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]])
        await message.reply_text("🎯 **(Step 2/4)**\nकितने रेफ़रल चाहिए इस बैच को अनलॉक करने के लिए? (सिर्फ नंबर लिखें, जैसे: `5`)", reply_markup=btn)
        
    elif action == "add_batch_req":
        if not message.text.isdigit():
            return await message.reply_text("❌ Please send a valid number (e.g. 5)")
        admin_states[admin_id]["req_shares"] = int(message.text)
        admin_states[admin_id]["action"] = "add_batch_link"
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]])
        await message.reply_text("🔗 **(Step 3/4)**\nअनलॉक होने के बाद यूज़र को कौन सा लिंक मिलना चाहिए? (यहाँ लिंक या @username भेजें)", reply_markup=btn)

    elif action == "add_batch_link":
        admin_states[admin_id]["unlock_link"] = format_telegram_link(message.text)
        admin_states[admin_id]["action"] = "add_batch_vip"
        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭ Skip Buy VIP+", callback_data="skip_vip")],
            [InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]
        ])
        await message.reply_text("💎 **(Step 4/4)**\nइस बैच के लिए Buy VIP+ डायरेक्ट लिंक भेजें। (अगर नहीं लगाना तो Skip पर क्लिक करें)", reply_markup=btn)

    elif action == "add_batch_vip" or (message.text.lower() == "skip" if message.text else False):
        vip_link = format_telegram_link(message.text) if message.text and message.text.lower() != "skip" else "none"
        data = admin_states[admin_id]
        batch_id = data["batch_id"]
        
        await batches_col.update_one(
            {"batch_id": batch_id},
            {"$set": {
                "req_shares": data["req_shares"],
                "unlock_link": data["unlock_link"],
                "vip_link": vip_link,
                "style_share": "primary", 
                "style_unlock": "secondary",
                "style_vip": "success"
            }},
            upsert=True
        )
        del admin_states[admin_id] 
        bot_username = (await app.get_me()).username
        link = f"https://t.me/{bot_username}?start={batch_id}"
        
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="admin_panel")]])
        await message.reply_text(f"✅ **Batch '{batch_id}' Successfully Created!**\n\n🎯 Req Shares: {data['req_shares']}\n💎 Buy VIP+: {'Yes' if vip_link != 'none' else 'No'}\n🚀 **Direct Batch Link:**\n`{link}`\n\nइस लिंक को आप सीधे भी शेयर कर सकते हैं।", reply_markup=btn)

    elif action == "add_fsub_id":
        admin_states[admin_id]["fsub_id"] = message.text
        admin_states[admin_id]["action"] = "add_fsub_link"
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_settings")]])
        await message.reply_text("🔗 अब इस चैनल का **Invite Link** या **Username** भेजें:", reply_markup=btn)
        
    elif action == "add_fsub_link":
        fsub_id = admin_states[admin_id]["fsub_id"]
        fsub_link = format_telegram_link(message.text)
        
        config = await settings_col.find_one({"_id": "config"})
        fsubs = config.get("fsub", [])
        fsubs.append({"id": fsub_id, "link": fsub_link})
        
        await settings_col.update_one({"_id": "config"}, {"$set": {"fsub": fsubs}})
        del admin_states[admin_id]
        
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Settings", callback_data="admin_settings")]])
        await message.reply_text("✅ नया FSub Channel सफलतापूर्वक जोड़ दिया गया है!", reply_markup=btn)

@app.on_callback_query(filters.regex(r"^skip_vip$"))
async def skip_vip_callback(client, callback_query: CallbackQuery):
    admin_id = callback_query.from_user.id
    if admin_id in admin_states and admin_states[admin_id].get("action") == "add_batch_vip":
        message = callback_query.message
        message.text = "none"
        message.from_user = callback_query.from_user
        await callback_query.message.delete()
        await admin_state_machine(client, message)

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_settings()) 
    print("🚀 Fully Automated Telegram Admin UI Bot is starting...")
    app.run()
