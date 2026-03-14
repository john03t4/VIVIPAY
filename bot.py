import logging
import sqlite3
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    ContextTypes, MessageHandler, filters, ConversationHandler
)

# --- CONFIGURATION ---
TOKEN = "8784656617:AAEli7-M09i9yncX4QWXWG8nUpbUvD7KeWA"
ADMIN_ID = 8473662465  
NEWBIE_BONUS = 200
REFERRAL_REWARD = 200

# Required Channels/Groups
REQUIRED_CHATS = ["@VIVIPAY02", "@VIVIPAY5", "@VIVIPAY6", "@VIVIPAY7"]

# Conversation States
DEP_AMOUNT, DEP_PROOF = range(2)
SUPPORT_MSG = 3
ADD_PLAN_NAME, ADD_PLAN_COST, ADD_PLAN_PERC, ADD_PLAN_DAYS = range(4, 8)

# --- DATABASE ENGINE ---
def db_query(query, params=(), fetch=False):
    with sqlite3.connect('vivipay_pro.db') as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        if fetch:
            return cursor.fetchall()
        conn.commit()

def init_db():
    db_query('''CREATE TABLE IF NOT EXISTS users 
                (user_id TEXT PRIMARY KEY, referrer_id TEXT, 
                 balance REAL DEFAULT 0, newbie_claimed INTEGER DEFAULT 0,
                 total_withdrawn REAL DEFAULT 0)''')
    
    db_query('''CREATE TABLE IF NOT EXISTS plans 
                (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, cost REAL, 
                 daily_percent REAL, duration_days INTEGER)''')

    db_query('''CREATE TABLE IF NOT EXISTS investments 
                (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, plan_id INTEGER, 
                 amount REAL, start_date DATETIME, status TEXT DEFAULT 'active')''')

    # Seed initial plans if the table is empty
    if not db_query("SELECT id FROM plans", fetch=True):
        db_query("INSERT INTO plans (name, cost, daily_percent, duration_days) VALUES (?, ?, ?, ?)", ("Starter", 200.0, 30, 5))
        db_query("INSERT INTO plans (name, cost, daily_percent, duration_days) VALUES (?, ?, ?, ?)", ("Premium", 500.0, 25, 10))

# --- MEMBERSHIP CHECK ---
async def check_membership(user_id, context: ContextTypes.DEFAULT_TYPE):
    for chat in REQUIRED_CHATS:
        try:
            member = await context.bot.get_chat_member(chat_id=chat, user_id=user_id)
            if member.status in ['left', 'kicked', 'None']:
                return False
        except Exception:
            return False
    return True

# --- UI COMPONENTS ---
def get_main_menu(user_id):
    keyboard = [
        [KeyboardButton("📈 Investment Plans"), KeyboardButton("💳 Deposit")],
        [KeyboardButton("📂 My Investments"), KeyboardButton("💰 My Wallet")],
        [KeyboardButton("🎁 Claim Newbie Bonus"), KeyboardButton("👥 Refer & Earn")],
        [KeyboardButton("🛠 Support")]
    ]
    if user_id == ADMIN_ID:
        keyboard.append([KeyboardButton("➕ Add Plan")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# --- COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    referrer = context.args[0] if context.args else None

    if not await check_membership(user_id, context):
        keyboard = [[InlineKeyboardButton("📢 Join Channels", url="https://t.me/VIVIPAY02")], [InlineKeyboardButton("🔄 Verify", callback_data="check_subs")]]
        await update.message.reply_text("⚠️ *Access Denied!*\n\nYou must join all official groups to use this bot.", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return

    res = db_query("SELECT balance FROM users WHERE user_id = ?", (user_id,), fetch=True)
    if not res:
        db_query("INSERT INTO users (user_id, referrer_id) VALUES (?, ?)", (user_id, referrer if referrer != user_id else None))
        bal = 0
    else: bal = res[0][0]

    await update.message.reply_text(f"💎 *VIVI PAY PREMIER*\n💰 *Balance:* ₹{bal}\n━━━━━━━━━━━━━━", reply_markup=get_main_menu(update.effective_user.id), parse_mode='Markdown')

# --- INVESTMENT FLOW ---
async def show_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    plans = db_query("SELECT * FROM plans", fetch=True)
    if not plans:
        await update.message.reply_text("❌ No plans available currently.")
        return
        
    for p in plans:
        text = f"🔹 *{p[1]}*\nCost: ₹{p[2]}\nReturns: {p[3]}% daily for {p[4]} days"
        kb = [[InlineKeyboardButton(f"Invest ₹{p[2]}", callback_data=f"buy_{p[0]}")]]
        
        # Admin-only delete button
        if user_id == ADMIN_ID:
            kb.append([InlineKeyboardButton(f"🗑️ Delete {p[1]}", callback_data=f"delplan_{p[0]}")])
            
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def buy_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    plan_id = query.data.split("_")[1]
    await query.answer()
    
    plan_res = db_query("SELECT name, cost FROM plans WHERE id = ?", (plan_id,), fetch=True)
    if not plan_res:
        await query.edit_message_text("❌ This plan no longer exists.")
        return
        
    plan = plan_res[0]
    user_bal = db_query("SELECT balance FROM users WHERE user_id = ?", (user_id,), fetch=True)[0][0]
    
    if user_bal >= plan[1]:
        db_query("UPDATE users SET balance = balance - ? WHERE user_id = ?", (plan[1], user_id))
        db_query("INSERT INTO investments (user_id, plan_id, amount, start_date) VALUES (?, ?, ?, ?)", (user_id, plan_id, plan[1], datetime.now()))
        await query.edit_message_text(f"✅ *Investment Successful!* {plan[0]} is now active.")
    else:
        await query.edit_message_text("❌ *Insufficient Balance!* Please deposit funds.")

# --- MY INVESTMENTS ---
async def my_investments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    invs = db_query("SELECT i.id, i.amount, i.start_date, p.name, p.daily_percent, p.duration_days FROM investments i JOIN plans p ON i.plan_id = p.id WHERE i.user_id = ? AND i.status = 'active'", (user_id,), fetch=True)
    if not invs:
        await update.message.reply_text("📝 You have no active investments.")
        return
    text = "📂 *Your Active Investments*\n\n"
    keyboard = []
    for i in invs:
        inv_id, amt, s_date, p_name, p_perc, p_days = i
        start_dt = datetime.strptime(s_date, '%Y-%m-%d %H:%M:%S.%f')
        end_dt = start_dt + timedelta(days=p_days)
        total_payout = amt + (amt * (p_perc/100) * p_days)
        
        if datetime.now() >= end_dt:
            text += f"✅ *{p_name} READY*\nPayout: ₹{total_payout}\n\n"
            keyboard.append([InlineKeyboardButton(f"Claim ₹{total_payout}", callback_data=f"claim_{inv_id}")])
        else:
            rem = end_dt - datetime.now()
            text += f"⏳ *{p_name} IN PROGRESS*\nAmt: ₹{amt}\nEnds in: {rem.days}d {rem.seconds//3600}h\n\n"
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None, parse_mode='Markdown')

# --- CALLBACKS (APPROVE, DELETE, CLAIM, VERIFY) ---
async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    data = query.data
    await query.answer()
    
    if data.startswith("approve_"):
        _, uid, amt = data.split("_")
        db_query("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amt, uid))
        try: await context.bot.send_message(uid, f"🎉 *Deposit Approved!*\n₹{amt} has been added to your balance.", parse_mode='Markdown')
        except: pass
        await query.edit_message_caption("✅ Deposit Approved.")
        
    elif data.startswith("delplan_"):
        if int(user_id) != ADMIN_ID: return
        p_id = data.split("_")[1]
        db_query("DELETE FROM plans WHERE id = ?", (p_id,))
        await query.edit_message_text("🗑️ *Plan Deleted Successfully.*", parse_mode='Markdown')
        
    elif data.startswith("claim_"):
        inv_id = data.split("_")[1]
        inv = db_query("SELECT i.amount, p.daily_percent, p.duration_days FROM investments i JOIN plans p ON i.plan_id = p.id WHERE i.id = ? AND i.status = 'active'", (inv_id,), fetch=True)
        if inv:
            amt, perc, days = inv[0]
            total = amt + (amt * (perc/100) * days)
            db_query("UPDATE users SET balance = balance + ? WHERE user_id = ?", (total, user_id))
            db_query("UPDATE investments SET status = 'completed' WHERE id = ?", (inv_id,))
            await query.edit_message_text(f"🎊 *Success!* ₹{total} added to your wallet.", parse_mode='Markdown')

    elif data == "check_subs":
        if await check_membership(user_id, context):
            await query.edit_message_text("✅ Verified! Type /start to refresh the menu.")
        else:
            await query.answer("❌ You still haven't joined all channels!", show_alert=True)

# --- ADMIN ADD PLAN ---
async def start_add_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    await update.message.reply_text("➕ *Add New Plan*\n\nEnter Plan Name:", parse_mode='Markdown')
    return ADD_PLAN_NAME

async def get_plan_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_plan_name'] = update.message.text
    await update.message.reply_text("Enter Cost (INR):")
    return ADD_PLAN_COST

async def get_plan_cost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_plan_cost'] = update.message.text
    await update.message.reply_text("Enter Daily % (e.g., 30):")
    return ADD_PLAN_PERC

async def get_plan_perc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_plan_perc'] = update.message.text
    await update.message.reply_text("Enter Duration (Days):")
    return ADD_PLAN_DAYS

async def get_plan_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_query("INSERT INTO plans (name, cost, daily_percent, duration_days) VALUES (?, ?, ?, ?)", 
             (context.user_data['new_plan_name'], context.user_data['new_plan_cost'], context.user_data['new_plan_perc'], update.message.text))
    await update.message.reply_text("✅ Plan Added Successfully!", reply_markup=get_main_menu(ADMIN_ID))
    return ConversationHandler.END

# --- DEPOSIT FLOW ---
async def start_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💳 *Deposit Funds*\n\nEnter amount in INR:", parse_mode='Markdown')
    return DEP_AMOUNT

async def get_deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['temp_amount'] = update.message.text
    await update.message.reply_text(f"✨ *Payment Details*\n\nUPI: `7599183692@mbk` \nAmt: ₹{update.message.text}\n\n📸 *Send screenshot of payment now.*", parse_mode='Markdown')
    return DEP_PROOF

async def get_deposit_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    photo_id = update.message.photo[-1].file_id
    admin_kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user.id}_{context.user_data['temp_amount']}"), InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user.id}")]])
    await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo_id, caption=f"🔔 *DEPOSIT REQ*\nUser: {user.full_name}\nID: `{user.id}`\nAmt: ₹{context.user_data['temp_amount']}", reply_markup=admin_kb, parse_mode='Markdown')
    await update.message.reply_text("✅ *Proof Sent!* Admin will verify your deposit.", reply_markup=get_main_menu(user.id))
    return ConversationHandler.END

# --- SUPPORT FLOW ---
async def start_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🛠 *Support*\nWrite your message for the Admin below:", parse_mode='Markdown')
    return SUPPORT_MSG

async def forward_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=ADMIN_ID, text=f"📩 *SUPPORT MSG*\nFrom: {update.effective_user.id}\n\n{update.message.text}")
    await update.message.reply_text("✅ Message sent to Admin.")
    return ConversationHandler.END

# --- BUTTON HANDLERS ---
async def handle_text_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    u_id = str(update.effective_user.id)
    if t == "💰 My Wallet":
        res = db_query("SELECT balance, total_withdrawn FROM users WHERE user_id = ?", (u_id,), fetch=True)[0]
        await update.message.reply_text(f"💳 *Wallet Info*\n\nBalance: ₹{res[0]}\nWithdrawn: ₹{res[1]}", parse_mode='Markdown')
    elif t == "🎁 Claim Newbie Bonus":
        status = db_query("SELECT newbie_claimed FROM users WHERE user_id = ?", (u_id,), fetch=True)[0][0]
        if status == 0:
            db_query("UPDATE users SET balance = balance + ?, newbie_claimed = 1 WHERE user_id = ?", (NEWBIE_BONUS, u_id))
            await update.message.reply_text(f"✅ ₹{NEWBIE_BONUS} Newbie Bonus added to your wallet!")
        else: await update.message.reply_text("❌ You have already claimed the bonus.")
    elif t == "👥 Refer & Earn":
        link = f"https://t.me/{(await context.bot.get_me()).username}?start={u_id}"
        await update.message.reply_text(f"👥 *Referral Program*\n\nInvite friends and earn ₹{REFERRAL_REWARD} per referral!\n\nLink: `{link}`", parse_mode='Markdown')

# --- MAIN ---
if __name__ == '__main__':
    init_db()
    app = Application.builder().token(TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex('^💳 Deposit$'), start_deposit),
            MessageHandler(filters.Regex('^🛠 Support$'), start_support),
            MessageHandler(filters.Regex('^➕ Add Plan$'), start_add_plan)
        ],
        states={
            DEP_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_deposit_amount)],
            DEP_PROOF: [MessageHandler(filters.PHOTO, get_deposit_proof)],
            SUPPORT_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, forward_to_admin)],
            ADD_PLAN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_plan_name)],
            ADD_PLAN_COST: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_plan_cost)],
            ADD_PLAN_PERC: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_plan_perc)],
            ADD_PLAN_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_plan_days)],
        },
        fallbacks=[],
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.Regex('^📈 Investment Plans$'), show_plans))
    app.add_handler(MessageHandler(filters.Regex('^📂 My Investments$'), my_investments))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_buttons))
    app.add_handler(CallbackQueryHandler(buy_plan, pattern="^buy_"))
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    
    print("Vivipay Pro is running...")
    app.run_polling()