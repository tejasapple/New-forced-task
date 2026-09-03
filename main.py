import asyncio
import aiofiles
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from motor.motor_asyncio import AsyncIOMotorClient

# --- 1. Bot Configuration ---
API_ID = "YOUR_API_ID"
API_HASH = "YOUR_API_HASH"
BOT_TOKEN = "YOUR_BOT_TOKEN"
ADMINS = [123456789] # List of Admin IDs
MONGO_URI = "mongodb+srv://<user>:<password>@cluster.mongodb.net/?retryWrites=true&w=majority"
VIP_GROUP_LINK = "https://t.me/+YourVIPGroupLink"

app = Client("advanced_referral_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- 2. MongoDB Setup (Motor) ---
db_client = AsyncIOMotorClient(MONGO_URI)
db = db_client["ReferralBotDB"]
users_col = db["users"]
batches_col = db["batches"]

# --- 3. Start & Multi-level Referral Logic ---
@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    user_id = message.from_user.id
    args = message.text.split(" ")
    
    # Save user globally if new
    if not await users_col.find_one({"user_id": user_id}):
        await users_col.insert_one({"user_id": user_id, "is_banned": False, "total_referrals": 0})

    # Check Ban Status
    user_data = await users_col.find_one({"user_id": user_id})
    if user_data.get("is_banned"):
        return await message.reply_text("🚫 You are banned from using this bot.")

    if len(args) == 1:
        return await message.reply_text("Welcome! Please use a valid batch link to start.")

    start_data = args[1] 
    
    try:
        if "_" in start_data:
            batch_id, referrer_id = start_data.split("_")
            referrer_id = int(referrer_id)
            
            if referrer_id != user_id:
                # Anti-fake referral check can be added here
                referrer_data = await users_col.find_one({"user_id": referrer_id})
                if referrer_data:
                    await users_col.update_one({"user_id": referrer_id}, {"$inc": {"total_referrals": 1, f"batches.{batch_id}": 1}})
        else:
            batch_id = start_data

        batch_info = await batches_col.find_one({"batch_id": batch_id})
        if not batch_info:
            return await message.reply_text("❌ This batch does not exist or has expired.")

        # Update User's Batch Tracking
        await users_col.update_one({"user_id": user_id}, {"$set": {f"joined_batches.{batch_id}": True}})

        eng_text = batch_info['eng_text']
        hin_text = batch_info['hin_text']
        
        bot_username = (await app.get_me()).username
        share_url = f"https://t.me/share/url?url=https://t.me/{bot_username}?start={batch_id}_{user_id}&text=Join%20this%20awesome%20group!"

        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Share (शेयर करें)", url=share_url)],
            [InlineKeyboardButton("🔓 Free Unlock", callback_data=f"unlock_{batch_id}")],
            [InlineKeyboardButton("💎 Buy VIP (Direct Access)", callback_data="buy_vip")]
        ])

        await message.reply_text(f"{eng_text}\n\n{hin_text}", reply_markup=buttons)

    except Exception as e:
        await message.reply_text("❌ Invalid Link.")
        print(f"Error: {e}")

# --- 4. Callbacks (Unlock & VIP) ---
@app.on_callback_query(filters.regex(r"^unlock_"))
async def unlock_button(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    batch_id = callback_query.data.split("_")[1]

    user_data = await users_col.find_one({"user_id": user_id})
    batch_data = await batches_col.find_one({"batch_id": batch_id})
    
    if not batch_data:
        return await callback_query.answer("Batch Expired!", show_alert=True)

    req_shares = batch_data['req_shares']
    user_refs = user_data.get("batches", {}).get(batch_id, 0)

    if user_refs >= req_shares:
        success_btn = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Access Granted", url=batch_data['unlock_link'])]])
        await callback_query.edit_message_text("🎉 Group Unlocked successfully!", reply_markup=success_btn)
    else:
        remaining = req_shares - user_refs
        await callback_query.answer(f"❌ Denied!\nYou need {remaining} more shares.", show_alert=True)

@app.on_callback_query(filters.regex(r"^buy_vip$"))
async def vip_button(client, callback_query: CallbackQuery):
    vip_btn = InlineKeyboardMarkup([[InlineKeyboardButton("👑 Go to VIP Group", url=VIP_GROUP_LINK)]])
    await callback_query.edit_message_text("🌟 **VIP Access**\n\nClick below to access the VIP section directly!", reply_markup=vip_btn)

# --- 5. GUI Admin Panel ---
@app.on_message(filters.command("admin") & filters.user(ADMINS))
async def admin_panel(client, message):
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats"), InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("➕ Add Batch", callback_data="admin_addbatch"), InlineKeyboardButton("⚙️ Manage Users", callback_data="admin_users")]
    ])
    await message.reply_text("🛠 **Advanced Admin Control Panel**\nSelect an option below:", reply_markup=buttons)

@app.on_callback_query(filters.regex(r"^admin_"))
async def admin_callbacks(client, callback_query: CallbackQuery):
    action = callback_query.data.split("_")[1]
    
    if action == "stats":
        total_users = await users_col.count_documents({})
        total_batches = await batches_col.count_documents({})
        await callback_query.edit_message_text(f"📊 **Live Stats**\n\n👥 Total Users: {total_users}\n📦 Active Batches: {total_batches}\n\nUse /admin to go back.")
    
    elif action == "addbatch":
        await callback_query.answer("Use command: /addbatch name|req_shares|link|eng|hin", show_alert=True)
        
    elif action == "broadcast":
        await callback_query.answer("Reply to any message with /broadcast to send it to all users.", show_alert=True)

# --- 6. Broadcast System (Aiofiles Ready) ---
@app.on_message(filters.command("broadcast") & filters.user(ADMINS) & filters.reply)
async def broadcast_msg(client, message):
    msg_to_send = message.reply_to_message
    users = users_col.find({})
    success, failed = 0, 0
    
    await message.reply_text("📢 Broadcast started...")
    async for user in users:
        try:
            await msg_to_send.copy(user['user_id'])
            success += 1
            await asyncio.sleep(0.05) # Prevent FloodWait
        except:
            failed += 1
            
    await message.reply_text(f"✅ Broadcast Complete!\n\nSuccess: {success}\nFailed: {failed}")

# --- 7. Batch Management Commands ---
@app.on_message(filters.command("addbatch") & filters.user(ADMINS))
async def add_batch(client, message):
    try:
        data = message.text.split("|")
        batch_id = data[0].split(" ")[1].strip()
        req_shares = int(data[1].strip())
        unlock_link = data[2].strip()
        eng = data[3].strip()
        hin = data[4].strip()

        await batches_col.update_one(
            {"batch_id": batch_id},
            {"$set": {"eng_text": eng, "hin_text": hin, "unlock_link": unlock_link, "req_shares": req_shares}},
            upsert=True
        )
        bot_username = (await app.get_me()).username
        await message.reply_text(f"✅ Batch '{batch_id}' added!\n🔗 Link: `https://t.me/{bot_username}?start={batch_id}`")
    except Exception as e:
        await message.reply_text("❌ Error. Format: `/addbatch name | 5 | link | eng | hin`")

if __name__ == "__main__":
    print("🚀 Advanced Bot is starting...")
    app.run()
