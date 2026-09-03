from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import sqlite3

# --- 1. Bot Configuration ---
API_ID = "YOUR_API_ID"        # my.telegram.org से लें
API_HASH = "YOUR_API_HASH"    # my.telegram.org से लें
BOT_TOKEN = "YOUR_BOT_TOKEN"  # BotFather से लें
ADMIN_ID = 123456789          # अपना टेलीग्राम User ID डालें

app = Client("my_referral_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- 2. Database Setup ---
conn = sqlite3.connect("bot_data.db", check_same_thread=False)
cursor = conn.cursor()

# Batches Table (एडमिन द्वारा सेट की गई लिंक्स और टेक्स्ट के लिए)
cursor.execute('''CREATE TABLE IF NOT EXISTS batches 
                  (batch_id TEXT PRIMARY KEY, eng_text TEXT, hin_text TEXT, unlock_link TEXT, req_shares INTEGER)''')

# Users Table (रेफरल ट्रैक करने के लिए)
cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                  (user_id INTEGER, batch_id TEXT, referrals INTEGER)''')
conn.commit()

# --- 3. Start & Referral Logic ---
@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    user_id = message.from_user.id
    args = message.text.split(" ")

    # डिफ़ॉल्ट स्टार्ट मैसेज
    if len(args) == 1:
        await message.reply_text("Welcome! Please use a valid batch link to start.")
        return

    # Deep Linking Logic (e.g., /start batch1_9876543)
    start_data = args[1] 
    
    try:
        # चेक करें कि लिंक में बैच आईडी और रेफर करने वाले का आईडी है या नहीं
        if "_" in start_data:
            batch_id, referrer_id = start_data.split("_")
            referrer_id = int(referrer_id)
            
            # खुद को रेफर करने से रोकना
            if referrer_id != user_id:
                # Referrer का काउंट बढ़ाना (यहाँ आप चेक लगा सकते हैं कि नया यूज़र है या पुराना)
                cursor.execute("SELECT referrals FROM users WHERE user_id=? AND batch_id=?", (referrer_id, batch_id))
                ref_data = cursor.fetchone()
                if ref_data:
                    new_count = ref_data[0] + 1
                    cursor.execute("UPDATE users SET referrals=? WHERE user_id=? AND batch_id=?", (new_count, referrer_id, batch_id))
                else:
                    cursor.execute("INSERT INTO users VALUES (?, ?, ?)", (referrer_id, batch_id, 1))
                conn.commit()
        else:
            batch_id = start_data

        # बैच की जानकारी डेटाबेस से निकालना
        cursor.execute("SELECT eng_text, hin_text, unlock_link, req_shares FROM batches WHERE batch_id=?", (batch_id,))
        batch_info = cursor.fetchone()

        if not batch_info:
            await message.reply_text("❌ This batch does not exist.")
            return

        eng_text, hin_text, unlock_link, req_shares = batch_info
        
        # यूज़र का खुद का डेटा बनाना अगर नहीं है
        cursor.execute("SELECT referrals FROM users WHERE user_id=? AND batch_id=?", (user_id, batch_id))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO users VALUES (?, ?, ?)", (user_id, batch_id, 0))
            conn.commit()

        # मैसेज टेक्स्ट तैयार करना
        full_text = f"{eng_text}\n\n{hin_text}"
        
        # शेयर लिंक बनाना
        bot_username = (await app.get_me()).username
        share_url = f"https://t.me/share/url?url=https://t.me/{bot_username}?start={batch_id}_{user_id}&text=Join%20this%20awesome%20group!"

        # बटन्स तैयार करना
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Share (शेयर करें)", url=share_url)],
            [InlineKeyboardButton("🔓 Unlock Group", callback_data=f"unlock_{batch_id}")]
        ])

        await message.reply_text(full_text, reply_markup=buttons)

    except Exception as e:
        await message.reply_text("❌ Invalid Link.")
        print(e)

# --- 4. Unlock Button Logic (Pop-up System) ---
@app.on_callback_query(filters.regex(r"^unlock_"))
async def unlock_button(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    batch_id = callback_query.data.split("_")[1]

    # डेटाबेस से चेक करना कि कितने शेयर (रेफरल) हुए हैं और कितने चाहिए
    cursor.execute("SELECT referrals FROM users WHERE user_id=? AND batch_id=?", (user_id, batch_id))
    user_refs = cursor.fetchone()[0]

    cursor.execute("SELECT req_shares, unlock_link FROM batches WHERE batch_id=?", (batch_id,))
    batch_data = cursor.fetchone()
    req_shares, unlock_link = batch_data[0], batch_data[1]

    if user_refs >= req_shares:
        # अनलॉक सफल! नया बटन दिखाना
        success_btn = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Access Granted - Click Here", url=unlock_link)]])
        await callback_query.edit_message_text("🎉 Congratulations! You have successfully unlocked the group.", reply_markup=success_btn)
    else:
        # पॉप-अप अलर्ट (Pop-up Notification)
        remaining = req_shares - user_refs
        alert_text = f"❌ Access Denied!\n\nYou need {remaining} more shares (referrals) to unlock this."
        await callback_query.answer(alert_text, show_alert=True) # show_alert=True स्क्रीन पर पॉप-अप लाएगा

# --- 5. Admin Panel Commands ---
@app.on_message(filters.command("addbatch") & filters.user(ADMIN_ID))
async def add_batch(client, message):
    # Syntax: /addbatch batch_name | Req_Shares | Unlock_Link | Eng_Text | Hin_Text
    try:
        data = message.text.split("|")
        batch_id = data[0].split(" ")[1].strip()
        req_shares = int(data[1].strip())
        unlock_link = data[2].strip()
        eng_text = data[3].strip()
        hin_text = data[4].strip()

        cursor.execute("INSERT OR REPLACE INTO batches VALUES (?, ?, ?, ?, ?)", (batch_id, eng_text, hin_text, unlock_link, req_shares))
        conn.commit()
        
        bot_username = (await app.get_me()).username
        await message.reply_text(f"✅ Batch '{batch_id}' added successfully!\n\n🔗 Master Link: `https://t.me/{bot_username}?start={batch_id}`")
    except Exception as e:
        await message.reply_text("❌ Syntax Error. Use:\n`/addbatch batch_name | 5 | https://t.me/link | English Text | Hindi Text`")

@app.on_message(filters.command("stats") & filters.user(ADMIN_ID))
async def check_stats(client, message):
    cursor.execute("SELECT batch_id, COUNT(user_id), SUM(referrals) FROM users GROUP BY batch_id")
    stats = cursor.fetchall()
    
    text = "📊 **Admin Statistics**\n\n"
    for row in stats:
        text += f"**Batch:** `{row[0]}`\n👥 Total Users: {row[1]}\n🔗 Total Shares (Referrals): {row[2] or 0}\n\n"
    
    await message.reply_text(text)

# Run the bot
if __name__ == "__main__":
    print("Bot is running...")
    app.run()
