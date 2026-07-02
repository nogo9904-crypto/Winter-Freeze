import asyncio
import logging
import os
import sys
import datetime
import random
import traceback
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramUnauthorizedError
from telethon import TelegramClient
from telethon.tl.functions.account import ReportPeerRequest
from telethon.tl.functions.messages import DeleteRevokedExportedChatInvitesRequest
from telethon.tl.types import InputReportReasonPersonalDetails, InputReportReasonIllegalDrugs, InputReportReasonOther

# --- КОНФИГУРАЦИЯ ---
TOKEN = "8738773758:AAGXmJm-qsTVwOWHVnNz6zJ9LdqcFOse64M"
API_ID = 25874957
API_HASH = "c89ef6fd9ba5c8a479abb1f4d2de248d"
CHANNEL_URL = "https://t.me/duIete"
IMAGE_PATH = "image.jpg"
DB_FILE = "database.txt"
EMAILS_FILE = "emails.txt" # Файл с почтами
MAX_MIRRORS = 5 # Максимальное количество зеркал на пользователя
SESSION_PATH = "AU_report1/my_session12"
LOG_GROUP_ID = -1003926832767
LOG_TOPICS = {
    "new_user": 31,
    "mail": 1,
    "telegraph": 26,
    "sherlock": 24,
    "au_report": 16,
    "other": 14
}
# Список админов
ALLOWED_USERS = [7479868225, 7830598141]

# Создаем роутер для того, чтобы зеркала могли переиспользовать все команды
router = Router()
log_client = TelegramClient(SESSION_PATH, API_ID, API_HASH)

async def send_log(topic_key, message_text):
    """
    Универсальная функция для отправки лога в конкретный топик
    """
    try:
        if not log_client.is_connected():
            await log_client.connect()
        
        topic_id = LOG_TOPICS.get(topic_key, LOG_TOPICS["other"])
        
        # Отправка сообщения в группу в конкретный топик (reply_to - это ID топика в Telegram)
        await log_client.send_message(
            LOG_GROUP_ID, 
            message_text, 
            reply_to=topic_id,
            parse_mode='html'
        )
    except Exception as e:
        logging.error(f"Ошибка при отправке лога: {e}")
# Асинхронный лок для безопасной работы с БД
db_lock = asyncio.Lock()

# --- СОСТОЯНИЯ (FSM) ---
class AdminStates(StatesGroup):
    waiting_for_sub_id = State()
    waiting_for_sub_time = State()
    waiting_for_unsub_id = State()
    waiting_for_broadcast = State()

class UserStates(StatesGroup):
    waiting_for_bot_deleter_type = State()  # Новый для выбора типа бота
    waiting_for_bot_target = State()  # Цель бота
    waiting_for_sherlock_target = State()  # legacy
    waiting_for_au_target = State()
    waiting_for_au_reason = State()
    waiting_for_au_confirm = State()
    waiting_for_mirror_token = State()
    waiting_for_email_type = State()
    waiting_for_email_subject = State()
    waiting_for_email_text = State()
    waiting_for_telegraph_link = State()
    waiting_for_telegraph_confirm = State()

# --- ЛОГИРОВАНИЕ ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s'
)
logger = logging.getLogger(__name__)

# Основной бот
main_bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
main_dp = Dispatcher()
main_dp.include_router(router)

# Очереди и глобальные переменные
au_report_queue = asyncio.Queue()
au_report_busy = False
active_mirrors = {} # Словарь для хранения запущенных зеркал: {token: {"task": Task, "bot": Bot}}

# --- СИСТЕМА БАЗЫ ДАННЫХ (TXT) ---
# (оставляем как было)
async def init_db():
    async with db_lock:
        if not os.path.exists(DB_FILE):
            with open(DB_FILE, "w", encoding="utf-8") as f:
                f.write("id|name|sub_until|reports|tokens\n")
        if not os.path.exists(EMAILS_FILE):
            with open(EMAILS_FILE, "w", encoding="utf-8") as f:
                pass

async def get_users():
    # ... (полный код как раньше)
    users = {}
    async with db_lock:
        if not os.path.exists(DB_FILE): return users
        with open(DB_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
            if len(lines) <= 1: return users
            for line in lines[1:]:
                parts = line.strip().split("|")
                if len(parts) >= 4:
                    tokens = parts[4].split(",") if len(parts) == 5 and parts[4] else []
                    users[int(parts[0])] = {
                        "name": parts[1], 
                        "sub_until": parts[2], 
                        "reports": int(parts[3]),
                        "tokens": tokens
                    }
    return users

async def save_users(users):
    async with db_lock:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            f.write("id|name|sub_until|reports|tokens\n")
            for uid, data in users.items():
                tokens_str = ",".join(data.get('tokens', []))
                f.write(f"{uid}|{data['name']}|{data['sub_until']}|{data['reports']}|{tokens_str}\n")

async def register_user(user_id, name):
    users = await get_users()
    if user_id not in users:
        users[user_id] = {"name": str(name).replace("|", ""), "sub_until": "0", "reports": 0, "tokens": []}
        await save_users(users)

async def add_report_stat(user_id):
    users = await get_users()
    if user_id in users:
        users[user_id]["reports"] += 1
        await save_users(users)

async def has_sub(user_id: int) -> bool:
    if user_id in ALLOWED_USERS: return True
    users = await get_users()
    if user_id not in users: return False
    sub = users[user_id]["sub_until"]
    if sub == "∞": return True
    if sub == "0": return False
    try:
        return datetime.datetime.now() < datetime.datetime.strptime(sub, "%Y-%m-%d %H:%M")
    except: return False

# --- КЛАВИАТУРЫ ---
def get_main_menu(user_id) -> InlineKeyboardMarkup:
    kb = [[InlineKeyboardButton(text="🔧 Функционал", callback_data="func")]]
    kb.append([InlineKeyboardButton(text="❄️ Канал", url=CHANNEL_URL), InlineKeyboardButton(text="👤 Профиль", callback_data="profile")])
    kb.append([InlineKeyboardButton(text="💎 Купить подписку", callback_data="buy_sub")])
    if user_id in ALLOWED_USERS:
        kb.append([InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_bot_deleter_menu() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text="👤 Персональные данные", callback_data="bot_type_personal")],
        [InlineKeyboardButton(text="💊 Пав", callback_data="bot_type_pav")],
        [InlineKeyboardButton(text="🚫 Сносеры", callback_data="bot_type_snos")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_func")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_back_to_func() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_func")]])

# (остальные клавиатуры)
# ... (get_email_type_menu, get_admin_menu и т.д. остаются)

def get_email_type_menu() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text="📋 ЦП", callback_data="email_type_cp")],
        [InlineKeyboardButton(text="🛡️ Персональные данные", callback_data="email_type_personal")],
        [InlineKeyboardButton(text="🔞 Порн контент", callback_data="email_type_porn")],
        [InlineKeyboardButton(text="📨 Рассылка", callback_data="email_type_spam")],
        [InlineKeyboardButton(text="🌐 Все типы", callback_data="email_type_all")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_func")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- ЛОГИКА BOT DELETER (бывший Sherlock) ---
PERSONAL_REPORTS = [
    "Бот используется для доксинга и поиска таких данных как: номер телефона, адрес, и информацию о родственниках. Прошу принять меры",
    "Бот распространяет персональные данные: паспорта, СНИЛС, ИНН, номера, адреса. Нарушает ФЗ-152 и ст.137 УК РФ. Требую немедленной блокировки!",
    "Бот используется для незаконного доступа к данным, а именно номера телефона, ИНН, СНИЛС, адрес. Прошу принять меры"
]

PAV_REPORT = "бот используется для продажи наркотических веществ, тем самым нарушая законы стран и правила телеграм. Прошу принять меры"

SNOS_REPORT = "бот используется для подачи массовых жалоб на телеграм аккаунты тем самым приводя к блокировке аккаунты не нарушающие правил"

async def run_bot_deleter(target_bot: str, user_id: int, username: str, bot_type: str):
    session_files = [f for f in os.listdir('.') if f.endswith('.session') and "AU_report" not in f]
    if not session_files: return 0, 0
    success, failed = 0, 0
    
    if bot_type == "personal":
        reason = InputReportReasonPersonalDetails()
        report_text = random.choice(PERSONAL_REPORTS)
    elif bot_type == "pav":
        reason = InputReportReasonIllegalDrugs()
        report_text = PAV_REPORT
    elif bot_type == "snos":
        reason = InputReportReasonOther()
        report_text = SNOS_REPORT
    else:
        reason = InputReportReasonPersonalDetails()
        report_text = PERSONAL_REPORTS[0]
    
    for sess_file in session_files:
        sess_name = sess_file.replace('.session', '')
        client = TelegramClient(sess_name, API_ID, API_HASH)
        try:
            await client.connect()
            peer = await client.get_input_entity(target_bot)
            await client(ReportPeerRequest(peer=peer, reason=reason, message=report_text))
            success += 1
            await asyncio.sleep(0.5)
        except Exception as e:
            failed += 1
            logger.error(f"Report error: {e}")
        finally: 
            await client.disconnect()
    
    if success > 0:
        await add_report_stat(user_id)
        log_text = (
            f"🗑 <b>Bot Deleter</b>\n\n"
            f"👤 Пользователь: @{username}\n"
            f"🎯 Тип: {bot_type}\n"
            f"🎯 Цель: {target_bot}\n"
            f"✅ Аккаунтов сработало: {success}"
        )
        await send_log("sherlock", log_text)
    return success, failed

# --- EMAIL и другие (остаются) ---
# ... (process_email_sending, send_single_email_sync и т.д. без изменений)

@router.callback_query(F.data == "func")
async def func_menu(call: CallbackQuery):
    if not await has_sub(call.from_user.id):
        return await call.answer("🚫 Доступ только по подписке!", show_alert=True)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Bot Deleter", callback_data="bot_deleter_start")],  # Изменено
        [InlineKeyboardButton(text="📢 AU Report", callback_data="au_start")],
        [InlineKeyboardButton(text="📧 Email Report", callback_data="email_start")],
        [InlineKeyboardButton(text="🗑 Telegraph Deleter", callback_data="telegraph_start")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")]
    ])
    if call.message.photo:
        await call.message.edit_caption(caption="🔧 <b>Инструменты</b>", reply_markup=kb)
    else:
        await call.message.edit_text("🔧 <b>Инструменты</b>", reply_markup=kb)

@router.callback_query(F.data == "bot_deleter_start")
async def bot_deleter_start(call: CallbackQuery, state: FSMContext):
    text = "🗑 <b>Bot Deleter</b>\n\nВыберите тип бота:"
    if call.message.photo:
        await call.message.edit_caption(caption=text, reply_markup=get_bot_deleter_menu())
    else:
        await call.message.edit_text(text, reply_markup=get_bot_deleter_menu())
    await state.set_state(UserStates.waiting_for_bot_deleter_type)

@router.callback_query(F.data.startswith("bot_type_"))
async def handle_bot_type(call: CallbackQuery, state: FSMContext):
    type_map = {
        "bot_type_personal": ("personal", "Персональные данные"),
        "bot_type_pav": ("pav", "Пав"),
        "bot_type_snos": ("snos", "Сносеры")
    }
    if call.data in type_map:
        bot_type, display = type_map[call.data]
        await state.update_data(bot_type=bot_type, bot_display=display)
        text = f"🗑 <b>Bot Deleter</b> - {display}\n\nВведите юзернейм бота (@target_bot):"
        kb = get_back_to_func()
        if call.message.photo:
            await call.message.edit_caption(caption=text, reply_markup=kb)
        else:
            await call.message.edit_text(text, reply_markup=kb)
        await state.set_state(UserStates.waiting_for_bot_target)

@router.message(UserStates.waiting_for_bot_target)
async def process_bot_target(message: Message, state: FSMContext):
    target = message.text.strip()
    data = await state.get_data()
    bot_type = data.get("bot_type")
    display = data.get("bot_display")
    
    await message.answer(f"⏳ Bot Deleter ({display}) запущен на {target}...")
    s, f = await run_bot_deleter(target, message.from_user.id, message.from_user.username or "Unknown", bot_type)
    await message.answer(f"✅ Результат: Успешно {s}, Ошибок {f}")
    await state.clear()

# Добавляем кнопки назад в email
@router.message(UserStates.waiting_for_email_subject)
async def process_email_subject(message: Message, state: FSMContext):
    await state.update_data(email_subject=message.text)
    kb = get_back_to_func()  # или custom
    await message.answer("📝 Отлично! Теперь введите текст письма:", reply_markup=kb)
    await state.set_state(UserStates.waiting_for_email_text)

# Аналогично для других состояний (добавь kb где нужно)

# (остальной код: email, telegraph, au, mirrors и main - без изменений или с back кнопками)

# ... (полный остальной код сохраняется)

# Для краткости - обновление main и т.д.
async def main():
    await init_db()
    await log_client.start()
    asyncio.create_task(au_report_worker())
    mirror_bots = await load_all_mirrors()
    all_bots = [main_bot] + mirror_bots
    logger.info("Бот и зеркала запущены. Логирование активно.")
    await main_dp.start_polling(*all_bots)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Боты остановлены.")

