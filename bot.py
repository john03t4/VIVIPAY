import logging
import sqlite3
import random
import os
import threading
import telegram
from flask import Flask
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    ContextTypes, MessageHandler, filters, ConversationHandler,
    TypeHandler, ApplicationHandlerStop
)

# --- CONFIGURATION ---
TOKEN = os.getenv("BOT_TOKEN", "8784656617:AAFh3VgWv6wmkQ9d-z6vdhDLlNQYCDecE1I")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8473662465"))
NEWBIE_BONUS = 200
REFERRAL_REWARD = 200
MIN_WITHDRAW = 100
USDT_RATE = 103

# Indian Banks for Withdrawal
INDIAN_BANKS = [
    "Paytm Payments Bank", "State Bank of India", "HDFC Bank", "ICICI Bank",
    "Axis Bank", "Punjab National Bank", "Bank of Baroda", "Canara Bank",
    "Union Bank of India", "Kotak Mahindra Bank", "PhonePe"
]

# Required Channels/Groups
REQUIRED_CHATS = ["@VIVIPAY02", "@VIVIPAY5", "@VIVIPAY6", "@VIVIPAY7"]

# Conversation States
DEP_AMOUNT, DEP_PROOF = range(2)
SUPPORT_MSG = 3
ADD_PLAN_NAME, ADD_PLAN_COST, ADD_PLAN_PERC, ADD_PLAN_DAYS = range(4, 8)
WITHDRAW_METHOD, WITHDRAW_AMOUNT, WITHDRAW_ADDRESS, WITHDRAW_BANK = range(8, 12)

# --- DATABASE ENGINE ---
def db_query(query, params=(), fetch=False):
    # Added timeout to prevent 'database is locked' crashes
    with sqlite3.connect('vivipay_pro.db', timeout=20) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        if fetch:
            return cursor.fetchall()
        conn.commit()

def init_db():
    db_query('''CREATE TABLE IF NOT EXISTS users 
                (user_id TEXT PRIMARY KEY, referrer_id TEXT, 
                 balance REAL DEFAULT 0, newbie_claimed INTEGER DEFAULT 0,
                 total_withdrawn REAL DEFAULT 0, is_verified INTEGER DEFAULT 0)''')
    
    db_query('''CREATE TABLE IF NOT EXISTS plans 
                (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, cost REAL, 
                 daily_percent REAL, duration_days INTEGER)''')

    db_query('''CREATE TABLE IF NOT EXISTS investments 
                (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, plan_id INTEGER, 
                 amount REAL, start_date DATETIME, status TEXT DEFAULT 'active')''')

    if not db_query("SELECT id FROM plans", fetch=True):
        db_query("INSERT INTO plans (name, cost, daily_percent, duration_days) VALUES (?, ?, ?, ?)", ("Starter", 200.0, 30, 5))
        db_query("INSERT INTO plans (name, cost, daily_percent, duration_days) VALUES (?, ?, ?, ?)", ("Premium", 500.0, 25, 10))

    try: db_query("ALTER TABLE users ADD COLUMN last_daily_bonus TEXT")
    except: pass
    try: db_query("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")
    except: pass

# --- KEEP-ALIVE SERVER (FOR RENDER) ---
app_web = Flask('')

@app_web.route('/')
def home():
    return "Bot is alive!"

def run_web():
    # Render provides the PORT environment variable automatically
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run_web)
    t.start()

# --- MEMBERSHIP CHECK ---
async def check_membership(user_id, context: ContextTypes.DEFAULT_TYPE):
    for chat in REQUIRED_CHATS:
        try:
            member = await context.bot.get_chat_member(chat_id=chat, user_id=user_id)
            # member.status can be 'creator', 'administrator', 'member', 'restricted', 'left', 'kicked'
            if member.status in ['left', 'kicked', 'None']:
                return False
        except Exception as e:
            logging.error(f"Error checking {chat}: {e}")
            return False
    return True

# --- UI COMPONENTS ---
def get_main_menu(user_id):
    res = db_query("SELECT newbie_claimed FROM users WHERE user_id = ?", (str(user_id),), fetch=True)
    claimed = res[0][0] if res else 0

    keyboard = [
        [KeyboardButton("📈 Investment Plans"), KeyboardButton("💳 Deposit")],
        [KeyboardButton("💸 Withdraw"), KeyboardButton("💰 My Wallet")],
        [KeyboardButton("📂 My Investments"), KeyboardButton("👥 Refer & Earn")],
        [KeyboardButton("📅 Daily Bonus")]
    ]
    
    last_row = [KeyboardButton("🛠 Support")]
    if claimed == 0:
        last_row.insert(0, KeyboardButton("🎁 Claim Newbie Bonus"))
    keyboard.append(last_row)

    if user_id == ADMIN_ID:
        keyboard.append([KeyboardButton("➕ Add Plan")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_join_keyboard():
    keyboard = []
    # Create buttons for all required chats
    for chat in REQUIRED_CHATS:
        clean_name = chat.replace("@", "")
        keyboard.append([InlineKeyboardButton(f"📢 Join {chat}", url=f"https://t.me/{clean_name}")])
    
    # Add the Verify button at the bottom
    keyboard.append([InlineKeyboardButton("🔄 Verify Membership", callback_data="check_subs")])
    return InlineKeyboardMarkup(keyboard)

# --- COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Safety: Ensure update.message exists (prevents crash on rare update types)
    if not update.message:
        return

    user_id = str(update.effective_user.id)
    referrer = context.args[0] if context.args else None

    # Check if user already exists
    res = db_query("SELECT balance FROM users WHERE user_id = ?", (user_id,), fetch=True)
    if not res:
        # Avoid self-referral
        ref_to_insert = referrer if referrer != user_id else None
        db_query("INSERT INTO users (user_id, referrer_id) VALUES (?, ?)", (user_id, ref_to_insert))
        bal = 0
    else: 
        bal = res[0][0]

    # Membership Check
    if not await check_membership(user_id, context):
        try:
            await update.message.reply_text(
                "⚠️ *Access Denied!*\n\nYou must join all our official channels and groups to use this bot.", 
                reply_markup=get_join_keyboard(), 
                parse_mode='Markdown'
            )
        except telegram.error.Forbidden:
            # User blocked the bot immediately, just exit silently
            return
        except Exception as e:
            logging.error(f"Error sending membership prompt: {e}")
        return

    # Final Welcome Message with Safety Net
    try:
        await update.message.reply_text(
            f"💎 *VIVI PAY PREMIER*\n💰 *Balance:* ₹{bal}\n━━━━━━━━━━━━━━", 
            reply_markup=get_main_menu(update.effective_user.id), 
            parse_mode='Markdown'
        )
    except telegram.error.Forbidden:
        # Prevent crash if user blocks bot during the start process
        logging.warning(f"User {user_id} blocked bot during /start")
    except Exception as e:
        logging.error(f"Failed to send start menu to {user_id}: {e}")

# --- BROADCAST COMMAND ---
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: 
        return
        
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
        
    msg = ' '.join(context.args)
    users = db_query("SELECT user_id FROM users", fetch=True)
    
    if not users:
        await update.message.reply_text("No users found.")
        return

    await update.message.reply_text(f"🚀 Starting broadcast to {len(users)} users...")
    
    count = 0
    for user in users:
        try:
            await context.bot.send_message(
                chat_id=user[0], 
                text=f"📢 *ANNOUNCEMENT*\n\n{msg}", 
                parse_mode='Markdown'
            )
            count += 1
        except telegram.error.Forbidden:
            # User blocked the bot, just skip them
            continue 
        except telegram.error.RetryAfter as e:
            # Telegram rate limit hit. Wait the required seconds and retry.
            import asyncio
            await asyncio.sleep(e.retry_after)
            await context.bot.send_message(chat_id=user[0], text=f"📢 *ANNOUNCEMENT*\n\n{msg}", parse_mode='Markdown')
            count += 1
        except Exception as e:
            logging.error(f"Broadcast failed for user {user[0]}: {e}")
            continue # Move to next user regardless of error
            
    await update.message.reply_text(f"✅ Broadcast Complete! Sent to {count} users.")

# --- BAN SYSTEM ---
async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not context.args:
        await update.message.reply_text("Usage: /ban <user_id>")
        return
    uid = context.args[0]
    db_query("UPDATE users SET is_banned = 1 WHERE user_id = ?", (uid,))
    await update.message.reply_text(f"⛔ User {uid} banned.")

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    
    users = db_query("SELECT user_id, balance FROM users", fetch=True)
    if not users:
        await update.message.reply_text("📂 No users found.")
        return

    msg = "👥 *User List:*\n"
    for u in users:
        msg += f"ID: `{u[0]}` | Bal: ₹{u[1]}\n"
    
    if len(msg) > 4000:
        for x in range(0, len(msg), 4000):
            await update.message.reply_text(msg[x:x+4000], parse_mode='Markdown')
    else:
        await update.message.reply_text(msg, parse_mode='Markdown')

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not context.args:
        await update.message.reply_text("Usage: /unban <user_id>")
        return
    uid = context.args[0]
    db_query("UPDATE users SET is_banned = 0 WHERE user_id = ?", (uid,))
    await update.message.reply_text(f"✅ User {uid} unbanned.")

async def ban_enforcer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user: return
    user_id = str(update.effective_user.id)
    if int(user_id) == ADMIN_ID: return
    
    res = db_query("SELECT is_banned FROM users WHERE user_id = ?", (user_id,), fetch=True)
    if res and res[0][0] == 1:
        if update.message:
            await update.message.reply_text("⛔ *You are banned from using this bot.*", parse_mode='Markdown')
        elif update.callback_query:
            await update.callback_query.answer("⛔ You are banned!", show_alert=True)
        raise ApplicationHandlerStop

# --- GLOBAL ERROR HANDLER ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and prevent the bot from crashing."""
    logging.error(f"Exception while handling an update: {context.error}")
    
    # If a user blocked the bot, we don't need to do anything, just ignore it
    if "Forbidden" in str(context.error):
        return

    # For other errors, you can optionally notify yourself
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=f"⚠️ Bot Error: {context.error}")
    except:
        pass

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

    # Ensure user exists before checking balance
    if not db_query("SELECT user_id FROM users WHERE user_id = ?", (user_id,), fetch=True):
        db_query("INSERT INTO users (user_id) VALUES (?)", (user_id,))

    plan = plan_res[0]
    user_bal = db_query("SELECT balance FROM users WHERE user_id = ?", (user_id,), fetch=True)[0][0]
    if user_bal >= plan[1]:
        db_query("UPDATE users SET balance = balance - ? WHERE user_id = ?", (plan[1], user_id))
        db_query("INSERT INTO investments (user_id, plan_id, amount, start_date) VALUES (?, ?, ?, ?)", (user_id, plan_id, plan[1], datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')))
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

# --- CALLBACKS (VERIFY, APPROVE, DELETE, CLAIM) ---
async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    data = query.data
    
    if data == "check_subs":
        if await check_membership(user_id, context):
            # Check verification and process referral reward
            user_data = db_query("SELECT is_verified, referrer_id FROM users WHERE user_id = ?", (user_id,), fetch=True)
            if user_data and user_data[0][0] == 0:
                db_query("UPDATE users SET is_verified = 1 WHERE user_id = ?", (user_id,))
                ref_id = user_data[0][1]
                if ref_id:
                    db_query("UPDATE users SET balance = balance + ? WHERE user_id = ?", (REFERRAL_REWARD, ref_id))
                    try:
                        await context.bot.send_message(
                            chat_id=ref_id,
                            text=f"🎊 *New Referral Verified!*\n\nUser: {query.from_user.full_name}\nReward: ₹{REFERRAL_REWARD} added to your balance!",
                            parse_mode='Markdown'
                        )
                    except telegram.error.Forbidden:
                        print(f"User {ref_id} has blocked the bot. Skipping...")
                    except Exception as e:
                        print(f"Could not send message to {ref_id}: {e}")
            
            await query.edit_message_text("✅ Verified Successfully! Click /start to access the main menu.")
        else:
            await query.answer("❌ You still haven't joined all channels!", show_alert=True)

    elif data.startswith("approve_"):
        await query.answer()
        _, uid, amt = data.split("_")
        db_query("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amt, uid))
        try: 
            await context.bot.send_message(uid, f"🎉 *Deposit Approved!*\n₹{amt} has been added to your balance.", parse_mode='Markdown')
        except telegram.error.Forbidden:
            print(f"User {uid} has blocked the bot. Skipping...")
        except Exception as e:
            print(f"Could not send message to {uid}: {e}")
        await query.message.delete()
        
    elif data.startswith("reject_"):
        await query.answer()
        uid = data.split("_")[1]
        try: 
            await context.bot.send_message(uid, "❌ *Deposit Rejected!* Proof not accepted.", parse_mode='Markdown')
        except telegram.error.Forbidden:
            print(f"User {uid} has blocked the bot. Skipping...")
        except Exception as e:
            print(f"Could not send message to {uid}: {e}")
        await query.message.delete()
        
    elif data.startswith("delplan_"):
        await query.answer()
        if int(user_id) == ADMIN_ID:
            p_id = data.split("_")[1]
            db_query("DELETE FROM plans WHERE id = ?", (p_id,))
            await query.edit_message_text("🗑️ *Plan Deleted Successfully.*", parse_mode='Markdown')
        
    elif data.startswith("claim_"):
        await query.answer()
        inv_id = data.split("_")[1]
        inv = db_query("SELECT i.amount, p.daily_percent, p.duration_days FROM investments i JOIN plans p ON i.plan_id = p.id WHERE i.id = ? AND i.status = 'active'", (inv_id,), fetch=True)
        if inv:
            amt, perc, days = inv[0]
            total = amt + (amt * (perc/100) * days)
            db_query("UPDATE users SET balance = balance + ? WHERE user_id = ?", (total, user_id))
            db_query("UPDATE investments SET status = 'completed' WHERE id = ?", (inv_id,))
            await query.edit_message_text(f"🎊 *Success!* ₹{total} added to wallet.", parse_mode='Markdown')

    elif data.startswith("wd_approve_"):
        await query.answer()
        parts = data.split("_")
        uid, amt = parts[2], float(parts[3])
        db_query("UPDATE users SET total_withdrawn = total_withdrawn + ? WHERE user_id = ?", (amt, uid))
        try: 
            await context.bot.send_message(uid, f"✅ *Withdrawal Approved!*\n₹{amt} has been sent to your account.", parse_mode='Markdown')
        except telegram.error.Forbidden:
            print(f"User {uid} has blocked the bot. Skipping...")
        except Exception as e:
            print(f"Could not send message to {uid}: {e}")
        await query.message.delete()
        
    elif data.startswith("wd_reject_"):
        await query.answer()
        parts = data.split("_")
        uid, amt = parts[2], float(parts[3])
        db_query("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amt, uid))
        try: 
            await context.bot.send_message(uid, f"❌ *Withdrawal Rejected!*\n₹{amt} has been refunded to your wallet.", parse_mode='Markdown')
        except telegram.error.Forbidden:
            print(f"User {uid} has blocked the bot. Skipping...")
        except Exception as e:
            print(f"Could not send message to {uid}: {e}")
        await query.message.delete()

# --- ADMIN ADD PLAN ---
async def start_add_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    await update.message.reply_text("➕ *Add New Plan*\nEnter Plan Name:", parse_mode='Markdown')
    return ADD_PLAN_NAME

async def get_plan_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_plan_name'] = update.message.text
    await update.message.reply_text("Enter Cost (INR):")
    return ADD_PLAN_COST

async def get_plan_cost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_plan_cost'] = update.message.text
    await update.message.reply_text("Enter Daily %:")
    return ADD_PLAN_PERC

async def get_plan_perc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_plan_perc'] = update.message.text
    await update.message.reply_text("Enter Duration (Days):")
    return ADD_PLAN_DAYS

async def get_plan_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_query("INSERT INTO plans (name, cost, daily_percent, duration_days) VALUES (?, ?, ?, ?)", 
             (context.user_data['new_plan_name'], context.user_data['new_plan_cost'], context.user_data['new_plan_perc'], update.message.text))
    await update.message.reply_text("✅ Plan Added!", reply_markup=get_main_menu(ADMIN_ID))
    return ConversationHandler.END

# --- DEPOSIT & SUPPORT ---
async def start_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    res = db_query("SELECT balance FROM users WHERE user_id = ?", (user_id,), fetch=True)
    if not res:
        db_query("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        bal = 0.0
    else: bal = res[0][0]
        
    if bal < 1: 
        await update.message.reply_text("❌ Insufficient balance to withdraw.")
        return ConversationHandler.END
        
    keyboard = [[KeyboardButton("🇮🇳 INR (UPI)"), KeyboardButton("💲 USDT (TRC20)")]]
    await update.message.reply_text(f"💸 *Withdraw Funds*\nBalance: ₹{bal}\nMin: ₹{MIN_WITHDRAW} | 1 USDT = ₹{USDT_RATE}\n\nSelect withdrawal method:", 
                                    reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
                                    parse_mode='Markdown')
    return WITHDRAW_METHOD

async def get_withdraw_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    method = update.message.text
    if method not in ["🇮🇳 INR (UPI)", "💲 USDT (TRC20)"]:
        await update.message.reply_text("❌ Please select a valid method using the buttons.")
        return WITHDRAW_METHOD
    
    context.user_data['wd_method'] = method
    if "INR" in method:
        keyboard = [[KeyboardButton(bank)] for bank in INDIAN_BANKS]
        await update.message.reply_text("🏦 Please select your bank:",
                                        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True))
        return WITHDRAW_BANK
    else: # USDT
        await update.message.reply_text("💰 Enter amount to withdraw (INR):") 
        return WITHDRAW_AMOUNT

async def get_withdraw_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bank = update.message.text
    if bank not in INDIAN_BANKS:
        keyboard = [[KeyboardButton(b)] for b in INDIAN_BANKS]
        await update.message.reply_text("❌ Please select a valid bank using the buttons provided.",
                                        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True))
        return WITHDRAW_BANK

    context.user_data['wd_bank'] = bank
    await update.message.reply_text("💰 Enter amount to withdraw (INR):") 
    return WITHDRAW_AMOUNT

async def get_withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    try: amount = float(update.message.text)
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number.")
        return WITHDRAW_AMOUNT
        
    res = db_query("SELECT balance FROM users WHERE user_id = ?", (user_id,), fetch=True)
    bal = res[0][0] if res else 0
    
    if amount < MIN_WITHDRAW:
        await update.message.reply_text(f"❌ Minimum withdrawal amount is ₹{MIN_WITHDRAW}.")
        return WITHDRAW_AMOUNT
        
    if amount > bal:
        await update.message.reply_text(f"❌ Insufficient balance. You have ₹{bal}.")
        return WITHDRAW_AMOUNT
    if amount <= 0: return WITHDRAW_AMOUNT
        
    context.user_data['wd_amount'] = amount
    method = context.user_data['wd_method']
    prompt = "💲 Enter your USDT (TRC20) Address:"
    if "INR" in method:
        bank = context.user_data['wd_bank']
        prompt = f"🇮🇳 Enter your UPI ID / Account Details for {bank}:"
    await update.message.reply_text(prompt)
    return WITHDRAW_ADDRESS

async def get_withdraw_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    address = update.message.text
    amount = context.user_data['wd_amount']
    method = context.user_data['wd_method']
    
    db_query("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
    
    admin_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Approve", callback_data=f"wd_approve_{user_id}_{amount}"), 
         InlineKeyboardButton("❌ Reject", callback_data=f"wd_reject_{user_id}_{amount}")]
    ])
    
    display_amt = f"₹{amount}"
    if "USDT" in method:
        display_amt += f" (~{round(amount / USDT_RATE, 2)} USDT)"
    
    details_line = f"Details: `{address}`"
    if "INR" in method:
        bank = context.user_data['wd_bank']
        details_line = f"Bank: {bank}\nDetails: `{address}`"

    msg = f"💸 *NEW WITHDRAWAL REQUEST*\nUser: {update.effective_user.full_name} (`{user_id}`)\nAmount: {display_amt}\nMethod: {method}\n{details_line}"
    await context.bot.send_message(chat_id=ADMIN_ID, text=msg, reply_markup=admin_kb, parse_mode='Markdown')
    
    await update.message.reply_text("✅ *Withdrawal Requested!* Please wait for admin approval.", reply_markup=get_main_menu(user_id), parse_mode='Markdown')
    return ConversationHandler.END

async def start_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("� *Deposit Funds*\nEnter amount in INR:", parse_mode='Markdown')
    return DEP_AMOUNT

async def get_deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['temp_amount'] = update.message.text
    await update.message.reply_text(f"✨ UPI: `7599183692@mbk` \nAmt: ₹{update.message.text}\n📸 *Send screenshot.*", parse_mode='Markdown')
    return DEP_PROOF

async def get_deposit_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not update.message.photo:
        await update.message.reply_text("❌ Please send a photo.")
        return DEP_PROOF
    photo_id = update.message.photo[-1].file_id
    admin_kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user.id}_{context.user_data['temp_amount']}"), InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user.id}")]])
    await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo_id, caption=f"🔔 *DEPOSIT*\nUser: {user.full_name}\nID: `{user.id}`\nAmt: ₹{context.user_data['temp_amount']}", reply_markup=admin_kb)
    await update.message.reply_text("✅ *Proof Sent!* Admin will verify.", reply_markup=get_main_menu(user.id))
    return ConversationHandler.END

async def start_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🛠 *Support*\nWrite your message below:", parse_mode='Markdown')
    return SUPPORT_MSG

async def forward_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=ADMIN_ID, text=f"📩 *SUPPORT*\nFrom: {update.effective_user.id}\n\n{update.message.text}")
    await update.message.reply_text("✅ Sent.")
    return ConversationHandler.END

# --- TEXT BUTTONS ---
async def handle_text_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    t = update.message.text
    u_id = str(update.effective_user.id)
    
    # Ensure user exists (prevents crash if Render wiped the DB)
    if not db_query("SELECT user_id FROM users WHERE user_id = ?", (u_id,), fetch=True):
        db_query("INSERT INTO users (user_id) VALUES (?)", (u_id,))
        
    if t == "💰 My Wallet":
        res = db_query("SELECT balance, total_withdrawn FROM users WHERE user_id = ?", (u_id,), fetch=True)
        if res:
            bal, withdrawn = res[0]
            await update.message.reply_text(f"💳 *Wallet Info*\nBalance: ₹{bal}\nWithdrawn: ₹{withdrawn}", parse_mode='Markdown')
        else:
            await update.message.reply_text("🔄 Please type /start to initialize your wallet.", parse_mode='Markdown')
    elif t == "🎁 Claim Newbie Bonus":
        res = db_query("SELECT newbie_claimed FROM users WHERE user_id = ?", (u_id,), fetch=True)
        status = res[0][0] if res else 1 # Treat as claimed if missing to prevent errors
        if status == 0:
            db_query("UPDATE users SET balance = balance + ?, newbie_claimed = 1 WHERE user_id = ?", (NEWBIE_BONUS, u_id))
            await update.message.reply_text(f"✅ ₹{NEWBIE_BONUS} bonus added!", reply_markup=get_main_menu(u_id))
        else: await update.message.reply_text("❌ Already claimed.")
    elif t == "📅 Daily Bonus":
        res = db_query("SELECT last_daily_bonus FROM users WHERE user_id = ?", (u_id,), fetch=True)
        if not res: return
        last = res[0][0]
        now = datetime.now()
        
        if last:
            last_dt = datetime.strptime(last, '%Y-%m-%d %H:%M:%S.%f')
            if now < last_dt + timedelta(hours=24):
                rem = (last_dt + timedelta(hours=24)) - now
                hours, remainder = divmod(rem.seconds, 3600)
                minutes, _ = divmod(remainder, 60)
                await update.message.reply_text(f"⏳ *Daily Bonus Cooldown*\nCome back in {hours}h {minutes}m.", parse_mode='Markdown')
                return

        amt = random.randint(20, 100)
        db_query("UPDATE users SET balance = balance + ?, last_daily_bonus = ? WHERE user_id = ?", (amt, now.strftime('%Y-%m-%d %H:%M:%S.%f'), u_id))
        await update.message.reply_text(f"🎁 *Daily Bonus Claimed!*\nYou received ₹{amt} added to your wallet.", parse_mode='Markdown')
    elif t == "👥 Refer & Earn":
        link = f"https://t.me/{(await context.bot.get_me()).username}?start={u_id}"
        await update.message.reply_text(f"👥 Earn ₹{REFERRAL_REWARD} per verified referral!\nLink: `{link}`", parse_mode='Markdown')

# --- MAIN ---
if __name__ == '__main__':
    init_db()
    keep_alive() # Start the web server in the background
    app = Application.builder().token(TOKEN).build()

    # Register the global error handler
    app.add_error_handler(error_handler)

    # Check for banned users before any other handler
    app.add_handler(TypeHandler(Update, ban_enforcer), group=-1)
    
    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex('^💳 Deposit$'), start_deposit),
            MessageHandler(filters.Regex('^💸 Withdraw$'), start_withdraw),
            MessageHandler(filters.Regex('^🛠 Support$'), start_support),
            MessageHandler(filters.Regex('^➕ Add Plan$'), start_add_plan)
        ],
        states={
            DEP_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_deposit_amount)],
            DEP_PROOF: [MessageHandler(filters.PHOTO, get_deposit_proof)],
            WITHDRAW_METHOD: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_withdraw_method)],
            WITHDRAW_BANK: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_withdraw_bank)],
            WITHDRAW_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_withdraw_amount)],
            WITHDRAW_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_withdraw_address)],
            SUPPORT_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, forward_to_admin)],
            ADD_PLAN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_plan_name)],
            ADD_PLAN_COST: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_plan_cost)],
            ADD_PLAN_PERC: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_plan_perc)],
            ADD_PLAN_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_plan_days)],
        },
        fallbacks=[],
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))
    app.add_handler(CommandHandler("users", list_users))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.Regex('^📈 Investment Plans$'), show_plans))
    app.add_handler(MessageHandler(filters.Regex('^📂 My Investments$'), my_investments))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_buttons))
    app.add_handler(CallbackQueryHandler(buy_plan, pattern="^buy_"))
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    
    print("Vivipay Pro is online.")
    app.run_polling()