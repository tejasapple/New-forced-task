import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import UserNotParticipant
from motor.motor_asyncio import AsyncIOMotorClient

# --- 1. Bot Configuration ---
API_ID = "YOUR_API_ID"
API_HASH = "YOUR_API_HASH"
BOT_TOKEN = "YOUR_BOT_TOKEN"
ADMINS = [123456789] # अपना Admin ID डालें
MONGO_URI = "mongodb+srv://<user>:<password>@cluster.mongodb.net/?retryWrites=true&w=majority"

app = Client("advanced_referral_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- 2. MongoDB Setup ---
db_client = AsyncIOMotorClient(MONGO_URI)
db = db_client["ReferralBotDB"]
users_col = db["users"]
batches_col = db["batches"]
settings_col = db["settings"] # VIP और FSub सेटिंग्स के लिए नया कलेक्शन

# डिफ़ॉल्ट सेटिंग्स इनिशियलाइज़ करना
async def init_settings():
    if not await settings_col.find_one({"_id": "config"}):
        await settings_col.insert_one({
            "_id": "config", 
            "vip_link": "https://t.me/YourDefaultVIP", 
            "fsub_channel": "-100XXXXXXXXX", # अपना चैनल ID डालें (-100 से शुरू)
            "fsub_link": "https://t.me/YourChannel"
        })

# --- 3. Force Subscribe Checker ---
async def check_fsub(client, user_id):
    config = await settings_col.find_one({"_id": "config"})
    fsub_channel = config.get("fsub_channel")
    fsub_link = config.get("fsub_link")
    
    if not fsub_channel or fsub_channel == "-100XXXXXXXXX":
        return True # अगर सेट नहीं है तो पास कर दो

    try:
        await client.get_chat_member(fsub_channel, user_id)
        return True
    except UserNotParticipant:
        return False
    except Exception as e:
        print(f"FSub Error: {e}") # अगर बोट चैनल में एडमिन नहीं है
        return True 

# --- 4. Start & FSub / Referral Logic ---
@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    user_id = message.from_user.id
    args = message.text.split(" ")
    
    # 1. User Database Entry
    user_data = await users_col.find_one({"user_id": user_id})
    if not user_data:
        user_data = {"user_id": user_id, "is_banned": False, "total_referrals": 0, "referred_by": None, "batches": {}}
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

    # 3. Handle Empty Start
    if len(args) == 1:
        return await message.reply_text("Welcome! Please use a valid batch link to start.")

    # 4. Deep Linking & Anti-Fake Referral Logic
    start_data = args[1] 
    try:
        if "_" in start_data:
            batch_id, referrer_id = start_data.split("_")
            referrer_id = int(referrer_id)
            
            # सिर्फ तभी रेफ़रल काउंट करें जब यूज़र नया हो और खुद को रेफर ना कर रहा हो
            if referrer_id != user_id and user_data.get("referred_by") is None:
                await users_col.update_one({"user_id": user_id}, {"$set": {"referred_by": referrer_id}})
                await users_col.update_one(
                    {"user_id": referrer_id}, 
                    {"$inc": {"total_referrals": 1, f"batches.{batch_id}": 1}}
                )
        else:
            batch_id = start_data

        # 5. Fetch Batch Data
        batch_info = await batches_col.find_one({"batch_id": batch_id})
        if not batch_info:
            return await message.reply_text("❌ This batch does not exist or has expired.")

        eng_text, hin_text = batch_info['eng_text'], batch_info['hin_text']
        bot_username = (await app.get_me()).username
        share_url = f"https://t.me/share/url?url=https://t.me/{bot_username}?start={batch_id}_{user_id}&text=Join%20this%20awesome%20group!"

        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Share with 5 friends", url=share_url)],
            [InlineKeyboardButton("🔓 Check Unlock Status", callback_data=f"unlock_{batch_id}")],
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

    # दोबारा FSub चेक करें ताकि लोग चीट ना कर सकें
    if not await check_fsub(client, user_id):
        return await callback_query.answer("❌ पहले चैनल ज्वाइन करें!", show_alert=True)

    user_data = await users_col.find_one({"user_id": user_id})
    batch_data = await batches_col.find_one({"batch_id": batch_id})
    
    if not batch_data:
        return await callback_query.answer("Batch Expired!", show_alert=True)

    req_shares = batch_data['req_shares']
    user_refs = user_data.get("batches", {}).get(batch_id, 0)

    if user_refs >= req_shares:
        success_btn = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Enter Group", url=batch_data['unlock_link'])]])
        await callback_query.edit_message_text("🎉 **Congratulations!** Group Unlocked successfully!", reply_markup=success_btn)
    else:
        remaining = req_shares - user_refs
        await callback_query.answer(f"❌ Denied!\nYou have {user_refs}/{req_shares} shares.\nYou need {remaining} more shares.", show_alert=True)

@app.on_callback_query(filters.regex(r"^buy_vip$"))
async def vip_button(client, callback_query: CallbackQuery):
    config = await settings_col.find_one({"_id": "config"})
    vip_link = config.get("vip_link", "Contact Admin")
    
    vip_btn = InlineKeyboardMarkup([[InlineKeyboardButton("👑 Go to VIP Group", url=vip_link)]])
    await callback_query.edit_message_text("🌟 **VIP Access**\n\nClick below to access the VIP section directly without sharing!", reply_markup=vip_btn)

# --- 6. Advanced Admin Commands ---
@app.on_message(filters.command("setvip") & filters.user(ADMINS))
async def set_vip(client, message):
    if len(message.command) < 2:
        return await message.reply_text("Use: `/setvip https://t.me/your_vip_link`")
    new_link = message.command[1]
    await settings_col.update_one({"_id": "config"}, {"$set": {"vip_link": new_link}}, upsert=True)
    await message.reply_text(f"✅ VIP Link updated to:\n{new_link}")

@app.on_message(filters.command("setfsub") & filters.user(ADMINS))
async def set_fsub(client, message):
    # Syntax: /setfsub -100123456789 https://t.me/JoinLink
    args = message.text.split(" ")
    if len(args) < 3:
        return await message.reply_text("Use: `/setfsub -100CHANNEL_ID https://t.me/ChannelLink`")
    
    await settings_col.update_one({"_id": "config"}, {"$set": {"fsub_channel": args[1], "fsub_link": args[2]}}, upsert=True)
    await message.reply_text(f"✅ Force Subscribe updated!\nID: {args[1]}\nLink: {args[2]}")

@app.on_message(filters.command("ban") & filters.user(ADMINS))
async def ban_user(client, message):
    if len(message.command) < 2: return
    target_id = int(message.command[1])
    await users_col.update_one({"user_id": target_id}, {"$set": {"is_banned": True}})
    await message.reply_text(f"🚫 User {target_id} has been banned.")

@app.on_message(filters.command("unban") & filters.user(ADMINS))
async def unban_user(client, message):
    if len(message.command) < 2: return
    target_id = int(message.command[1])
    await users_col.update_one({"user_id": target_id}, {"$set": {"is_banned": False}})
    await message.reply_text(f"✅ User {target_id} unbanned.")

# Batch and Broadcast commands remain same as previous, just properly routed
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
        await message.reply_text(f"✅ Batch '{batch_id}' added!\n🔗 Master Link: `https://t.me/{bot_username}?start={batch_id}`")
    except Exception as e:
        await message.reply_text("❌ Error. Format:\n`/addbatch name | 5 | link | eng | hin`")

@app.on_message(filters.command("stats") & filters.user(ADMINS))
async def get_stats(client, message):
    total_users = await users_col.count_documents({})
    total_batches = await batches_col.count_documents({})
    await message.reply_text(f"📊 **Live Stats Dashboard**\n\n👥 Total Users: {total_users}\n📦 Active Batches: {total_batches}")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_settings()) # Start hone se pehle settings load karega
    print("🚀 Ultra-Fast FSub Referral Bot is starting...")
    app.run()
