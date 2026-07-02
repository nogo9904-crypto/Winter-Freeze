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
TOKEN = "8885362273:AAH2ffqduoMoydNRmQB3UuZfbRAEeklXUvY"
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
    waiting_for_bot_deleter_target = State()  # Новый для Bot Deleter
    waiting_for_bot_deleter_type = State()
    waiting_for_sherlock_target = State()
    waiting_for_au_target = State()
    waiting_for_au_reason = State()
    waiting_for_au_confirm = State()
    waiting_for_mirror_token = State() # Состояние для ввода токена зеркала
    waiting_for_email_type = State() # Новый: выбор типа жалобы
    waiting_for_email_subject = State() # Состояние для темы письма
    waiting_for_email_text = State() # Состояние для текста письма
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
async def init_db():
    async with db_lock:
        if not os.path.exists(DB_FILE):
            with open(DB_FILE, "w", encoding="utf-8") as f:
                f.write("id|name|sub_until|reports|tokens\n")
        # Создаем файл с почтами, если его нет
        if not os.path.exists(EMAILS_FILE):
            with open(EMAILS_FILE, "w", encoding="utf-8") as f:
                pass

async def get_users():
    users = {}
    async with db_lock:
        if not os.path.exists(DB_FILE): return users
        with open(DB_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
            if len(lines) <= 1: return users
            for line in lines[1:]:
                parts = line.strip().split("|")
                if len(parts) >= 4:
                    # Обработка старых баз данных
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

# --- СИСТЕМА ЗЕРКАЛ ---
async def start_mirror_bot(token: str):
    """Запускает новое зеркало и устанавливает ему имя"""
    mirror_bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
    try:
        user_bot = await mirror_bot.get_me()
        
        # Устанавливаем имя боту
      

        # Запускаем поллинг
        task = asyncio.create_task(main_dp.start_polling(mirror_bot))
        active_mirrors[token] = {"task": task, "bot": mirror_bot}
        return True
    except Exception as e:
        logger.error(f"Ошибка динамического запуска зеркала: {e}")
        await mirror_bot.session.close()
        return False

async def stop_mirror_bot(token: str):
    """Останавливает бота-зеркало"""
    if token in active_mirrors:
        try:
            mirror_data = active_mirrors[token]
            mirror_data["task"].cancel()
            await mirror_data["bot"].session.close()
            del active_mirrors[token]
            logger.info(f"Зеркало {token[:10]}... остановлено.")
        except Exception as e:
            logger.error(f"Ошибка при остановке зеркала: {e}")

async def load_all_mirrors():
    """Загружает зеркала из БД и возвращает список объектов Bot"""
    users = await get_users()
    bots_to_poll = []
    for uid, data in users.items():
        for token in data.get('tokens', []):
            if token and token not in active_mirrors:
                mirror_bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
                try:
                    await mirror_bot.get_me()
                    # Устанавливаем имя при старте
                    active_mirrors[token] = {"bot": mirror_bot}
                    bots_to_poll.append(mirror_bot)
                except Exception as e:
                    logger.error(f"Ошибка подготовки {token[:10]}: {e}")
                    await mirror_bot.session.close()
    return bots_to_poll

def get_current_bot(token: str) -> Bot:
    """Получает объект бота по токену для обратной связи в воркерах"""
    if token == TOKEN:
        return main_bot
    return active_mirrors.get(token, {}).get("bot", main_bot)

# --- КЛАВИАТУРЫ ---
def get_main_menu(user_id) -> InlineKeyboardMarkup:
    kb = [[InlineKeyboardButton(text="🔧 Функционал", callback_data="func")]]
    kb.append([InlineKeyboardButton(text="❄️ Канал", url=CHANNEL_URL), InlineKeyboardButton(text="👤 Профиль", callback_data="profile")])
    kb.append([InlineKeyboardButton(text="💎 Купить подписку", callback_data="buy_sub")])
    if user_id in ALLOWED_USERS:
        kb.append([InlineKeyboardButton(text="⚙️ Админ-панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_admin_menu() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text="➕ Выдать саб", callback_data="adm_sub"), InlineKeyboardButton(text="➖ Забрать саб", callback_data="adm_unsub")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats"), InlineKeyboardButton(text="🪞 Зеркала", callback_data="adm_mirrors")],
        [InlineKeyboardButton(text="📢 Рассылка всем", callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_confirm_menu() -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton(text="✅ Да", callback_data="au_confirm_yes"),
            InlineKeyboardButton(text="❌ Нет", callback_data="au_confirm_no")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# Новый: клавиатура для выбора типа email
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

# Новая клавиатура для Bot Deleter
def get_bot_deleter_menu() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text="👤 Персональные данные", callback_data="bot_deleter_personal")],
        [InlineKeyboardButton(text="💊 Пав", callback_data="bot_deleter_pav")],
        [InlineKeyboardButton(text="🚫 Сносеры", callback_data="bot_deleter_snos")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_func")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- ЛОГИКА ОТПРАВКИ EMAIL ---
def send_single_email_sync(sender_email, sender_password, smtp_server, subject, body, to_email="Abuse@telegram.org"):
    """Синхронная функция для отправки письма через SMTP"""
    try:
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        server = smtplib.SMTP_SSL(smtp_server, 465)
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки email {sender_email}: {e}")
        return False

async def process_email_sending(subject, body, user_id, username, to_email="Abuse@telegram.org"):
    success = 0
    failed = 0
    try:
        with open(EMAILS_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return 0, 0

    for line in lines:
        line = line.strip()
        if not line: continue
        parts = line.split(":", 2)
        if len(parts) < 3: continue
        
        email_addr, pwd, smtp_server = parts[0].strip(), parts[1].strip(), parts[2].strip()
        res = await asyncio.to_thread(send_single_email_sync, email_addr, pwd, smtp_server, subject, body, to_email)
        if res: success += 1
        else: failed += 1
        await asyncio.sleep(0.5)
    
    # ЛОГ ПОСЛЕ ЗАВЕРШЕНИЯ
    log_text = (
        f"📧 <b>Email Report Завершен</b>\n\n"
        f"👤 Отправитель: @{username} (ID: <code>{user_id}</code>)\n"
        f"📝 Тема: {subject}\n"
        f"📧 To: {to_email}\n"
        f"✅ Успешно: {success}\n"
        f"❌ Ошибок: {failed}"
    )
    await send_log("mail", log_text)
    return success, failed

# --- НОВАЯ ЛОГИКА BOT DELETER (бывший Sherlock) ---
async def run_bot_deleter(target_bot: str, reason, report_text: str, user_id: int, username: str):
    session_files = [f for f in os.listdir('.') if f.endswith('.session') and "AU_report" not in f]
    if not session_files: return 0, 0
    success, failed = 0, 0
    
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
            logger.error(f"Report error for {sess_name}: {e}")
            failed += 1
        finally: await client.disconnect()
    
    if success > 0:
        await add_report_stat(user_id)
        # ЛОГ
        log_text = (
            f"🗑 <b>Bot Deleter</b>\n\n"
            f"👤 Пользователь: @{username}\n"
            f"🎯 Цель: {target_bot}\n"
            f"✅ Аккаунтов сработало: {success}"
        )
        await send_log("sherlock", log_text)  # оставляем тот же топик
    return success, failed

# --- ЛОГИКА AU REPORT ---
async def send_au_message(client, text, delay=1.5):
    try:
        await asyncio.sleep(delay)
        await client.send_message("@AUReportBot", text)
        return True
    except Exception as e:
        logger.error(f"Ошибка AU Send: {e}")
        return False

async def find_au_session():
    au_folder = "AU_report"
    if not os.path.exists(au_folder): return None, None
    files = [f for f in os.listdir(au_folder) if f.endswith('.session')]
    if not files: return None, None
    return os.path.join(au_folder, files[0]), files[0].replace('.session', '')

async def au_report_worker():
    global au_report_busy
    while True:
        if not au_report_busy and not au_report_queue.empty():
            au_report_busy = True
            user_id, target_link, reason_text, source_bot_token = await au_report_queue.get()
            
            session_path, session_name = await find_au_session()
            bot_obj = get_current_bot(source_bot_token)
            
            if not session_path:
                await bot_obj.send_message(user_id, "❌ Ошибка: В папке AU_report нет сессий.")
            else:
                client = TelegramClient(session_path.replace('.session', ''), API_ID, API_HASH)
                try:
                    await client.connect()
                    if not await client.is_user_authorized():
                        await bot_obj.send_message(user_id, "❌ Ошибка: AU сессия не авторизована.")
                    else:
                        await bot_obj.send_message(user_id, f"🔄 AU Report запущен для {target_link}...")
                        
                        # Команды боту @AUReportBot
                        await send_au_message(client, "#старт", 0.5)
                        await send_au_message(client, "/start", 1)
                        await send_au_message(client, target_link, 2)
                        await send_au_message(client, "Other", 2)
                        await send_au_message(client, reason_text, 2)
                        await send_au_message(client, "Proceed without documentation", 2)
                        await send_au_message(client, "Confirm", 2)
                        await send_au_message(client, "#стоп", 1)
                        
                        await bot_obj.send_message(user_id, f"✅ Жалоба на {target_link} отправлена!")
                        await add_report_stat(user_id)
                        
                        # Логирование в топик AU (16)
                        log_text = (
                            f"🛡 <b>AU Report Отправлен</b>\n\n"
                            f"👤 Пользователь ID: <code>{user_id}</code>\n"
                            f"🔗 Ссылка: {target_link}\n"
                            f"💬 Текст жалобы: <i>{reason_text}</i>\n"
                            f"📁 Сессия: {session_name}"
                        )
                        await send_log("au_report", log_text)
                        
                except Exception as e:
                    logger.error(f"AU Worker error: {e}")
                    await bot_obj.send_message(user_id, f"❌ Ошибка AU: {e}")
                finally:
                    await client.disconnect()
            
            au_report_busy = False
            au_report_queue.task_done()
        await asyncio.sleep(1)


@router.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or "No Username"
    first_name = message.from_user.first_name or "User"
    
    # Получаем текущих пользователей для проверки на "новизну"
    users = await get_users()
    is_new = user_id not in users
    
    await register_user(user_id, first_name)
    kb = get_main_menu(user_id)

    if is_new:
        log_text = (
            f"🆕 <b>Новый пользователь!</b>\n\n"
            f"👤 Имя: <b>{first_name}</b>\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"🔗 Юзернейм: @{username}"
        )
        await send_log("new_user", log_text)

    if os.path.exists(IMAGE_PATH):
        await message.answer_photo(FSInputFile(IMAGE_PATH), caption="<b>Winter Freeze Bot</b>", reply_markup=kb)
    else:
        await message.answer("<b>Winter Freeze Bot</b>", reply_markup=kb)



@router.callback_query(F.data == "adm_mirrors")
async def adm_mirrors_list(call: CallbackQuery):
    if call.from_user.id not in ALLOWED_USERS:
        return await call.answer("У вас нет прав!", show_alert=True)

    if not active_mirrors:
        text = "🪞 <b>Список зеркал пуст.</b>"
    else:
        text = "🪞 <b>Активные зеркала:</b>\n\n"
        for i, (token, data) in enumerate(active_mirrors.items(), 1):
            try:
                bot_info = await data["bot"].get_me()
                text += f"{i}. <code>{bot_info.first_name}</code> — @{bot_info.username}\n"
            except Exception:
                text += f"{i}. Ошибка получения данных для токена <code>{token[:10]}...</code>\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
    ])
    
    if call.message.photo:
        await call.message.edit_caption(caption=text, reply_markup=kb)
    else:
        await call.message.edit_text(text, reply_markup=kb)

@router.callback_query(F.data == "back_main")
async def back_main(call: CallbackQuery, state: FSMContext):
    await state.clear()
    if call.message.photo:
        await call.message.edit_caption(caption="<b>Главное меню</b> ❄️", reply_markup=get_main_menu(call.from_user.id))
    else:
        await call.message.edit_text("<b>Главное меню</b> ❄️", reply_markup=get_main_menu(call.from_user.id))

@router.callback_query(F.data == "func")
async def func_menu(call: CallbackQuery):
    if not await has_sub(call.from_user.id):
        return await call.answer("🚫 Доступ только по подписке!", show_alert=True)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Bot Deleter", callback_data="bot_deleter_start")],  # Переименовано
        [InlineKeyboardButton(text="📢 AU Report", callback_data="au_start")],
        [InlineKeyboardButton(text="📧 Email Report", callback_data="email_start")],
        [InlineKeyboardButton(text="🗑 Telegraph Deleter", callback_data="telegraph_start")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")]
    ])
    if call.message.photo:
        await call.message.edit_caption(caption="🔧 <b>Инструменты</b>", reply_markup=kb)
    else:
        await call.message.edit_text("🔧 <b>Инструменты</b>", reply_markup=kb)

# --- МОДИФИЦИРОВАННАЯ ЛОГИКА: EMAIL REPORT (добавлены кнопки назад) ---
@router.callback_query(F.data == "email_start")
async def email_start(call: CallbackQuery, state: FSMContext):
    if not os.path.exists(EMAILS_FILE) or os.stat(EMAILS_FILE).st_size == 0:
        return await call.answer("❌ Файл emails.txt пуст или не существует!", show_alert=True)
        
    text = "📧 <b>Email Report</b>\n\nВыберите тип жалобы:"
    if call.message.photo:
        await call.message.edit_caption(caption=text, reply_markup=get_email_type_menu())
    else:
        await call.message.edit_text(text, reply_markup=get_email_type_menu())
    await state.set_state(UserStates.waiting_for_email_type)

@router.callback_query(F.data.startswith("email_type_"))
async def handle_email_type(call: CallbackQuery, state: FSMContext):
    type_map = {
        "email_type_cp": ("ЦП", "stopCA@telegram.org"),
        "email_type_personal": ("Персональные данные", "encarregado@tailor.com.br"),
        "email_type_porn": ("Порн контент", "imagebasedabuse@eSafety.gov.au"),
        "email_type_spam": ("Рассылка", "Spam@telegram.org"),
        "email_type_all": ("Все типы", "abuse@telegram.org")
    }
    
    if call.data in type_map:
        complaint_type, to_email = type_map[call.data]
        await state.update_data(email_type=complaint_type, to_email=to_email)
        
        kb_back = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="email_start")]])
        text = f"📧 <b>Email Report</b> - {complaint_type}\n\nВведите тему письма:"
        if call.message.photo:
            await call.message.edit_caption(caption=text, reply_markup=kb_back)
        else:
            await call.message.edit_text(text, reply_markup=kb_back)
        await state.set_state(UserStates.waiting_for_email_subject)
    else:
        await call.answer("Неизвестный тип")

@router.message(UserStates.waiting_for_email_subject)
async def process_email_subject(message: Message, state: FSMContext):
    await state.update_data(email_subject=message.text)
    kb_back = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="email_start")]])
    await message.answer("📝 Отлично! Теперь введите текст письма:", reply_markup=kb_back)
    await state.set_state(UserStates.waiting_for_email_text)

@router.message(UserStates.waiting_for_email_text)
async def process_email_text(message: Message, state: FSMContext):
    body = message.text
    data = await state.get_data()
    subject = data.get("email_subject")
    to_email = data.get("to_email", "Abuse@telegram.org")
    
    msg = await message.answer(f"⏳ начинаю отправку жалоб, ожидайте завершения")
    
    # Запускаем процесс отправки писем
    success, failed = await process_email_sending(subject, body, message.from_user.id, message.from_user.username or "Unknown", to_email)
    
    # Отправляем результаты пользователю
    result_text = (
        f"✅ <b>Репорты завершены!</b>\n\n"
        f"📬 <b>Успешно отправлено:</b> {success}\n"
        f"❌ <b>Не успешно:</b> {failed}"
    )
    
    # Увеличиваем счетчик репортов
    if success > 0:
        users = await get_users()
        if message.from_user.id in users:
            users[message.from_user.id]["reports"] += success
            await save_users(users)
            
    await msg.edit_text(result_text)
    await state.clear()

# ==================== TELEGRAPH DELETER ====================

TELEGRAPH_EMAIL = "controleinterno@cmsilveiras.sp.gov.br"
TELEGRAPH_PASSWORD = "cmsilveiras2024"
TELEGRAPH_SMTP = "mail.cmsilveiras.sp.gov.br"

TELEGRAPH_TEMPLATES = [
    {
        "subject": "Urgent: Removal of illegal content containing personal data",
        "body": "Dear Telegram Abuse Team,\n\nThe article at {link} contains personal data (passport details, full name, photos, etc.) without consent of the person. This violates privacy laws of many countries and Telegram Terms of Service.\n\nI request immediate deletion of this material.\n\nThank you."
    },
    {
        "subject": "Report: Personal information leak on Telegra.ph",
        "body": "Hello,\n\nPlease remove the following Telegra.ph article: {link}\nIt publishes confidential personal information. This is a serious violation of GDPR, Russian 152-FZ and Telegram TOS.\nRequesting block and deletion."
    },
    {
        "subject": "Illegal publication of private data",
        "body": "The page {link} discloses personal and sensitive information of citizens. This material must be removed immediately in accordance with Telegram rules and international law."
    },
    {
        "subject": "Takedown request - Personal details exposed",
        "body": "Telegram Support,\n\nI report the article {link} for publishing private personal data. Please delete it as soon as possible."
    },
    {
        "subject": "Violation of privacy - Telegra.ph article",
        "body": "Dear team,\nThe following link {link} contains doxxing and personal information. This is prohibited by law and your Terms of Service. Request deletion."
    },
    {
        "subject": "Request for immediate removal of personal data",
        "body": "Hello,\nPlease delete the Telegra.ph post: {link}\nIt violates privacy rights and contains personal data without authorization."
    },
    {
        "subject": "Abuse report: Personal information",
        "body": "The article at {link} is spreading personal data. I demand its complete removal under Telegram's community standards."
    },
    {
        "subject": "Urgent deletion request - Doxxing",
        "body": "Telegram Abuse,\n\nLink: {link}\nThis page publishes doxxing materials. Immediate action required."
    },
    {
        "subject": "Personal data violation report",
        "body": "Please remove {link} — it contains sensitive personal information violating applicable laws and Telegram TOS."
    },
    {
        "subject": "Takedown: Unauthorized personal information",
        "body": "I request the deletion of the following Telegra.ph article: {link}\nReason: unauthorized disclosure of personal data."
    }
]

async def send_telegraph_report(link: str, user_id: int, username: str):
    template = random.choice(TELEGRAPH_TEMPLATES)
    subject = template["subject"]
    body = template["body"].format(link=link)

    try:
        msg = MIMEMultipart()
        msg['From'] = TELEGRAPH_EMAIL
        msg['To'] = "Abuse@telegram.org"
        msg['Subject'] = subject
        msg['X-Priority'] = '1'
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        server = smtplib.SMTP(TELEGRAPH_SMTP, 587)
        server.starttls()
        server.login(TELEGRAPH_EMAIL, TELEGRAPH_PASSWORD)
        server.send_message(msg)
        server.quit()

        # ЛОГ
        log_text = (
            f"🗑 <b>Telegraph Deleter</b>\n\n"
            f"👤 Пользователь: @{username} (ID: <code>{user_id}</code>)\n"
            f"🔗 Ссылка: {link}\n"
            f"✅ Статус: Отправлено"
        )
        await send_log("telegraph", log_text)
        return True
    except Exception as e:
        logger.error(f"Telegraph email error: {e}")
        return False

@router.callback_query(F.data == "telegraph_start")
async def telegraph_start(call: CallbackQuery, state: FSMContext):
    text = "🗑 <b>Telegraph Deleter</b>\n\nОтправьте ссылку на статью в формате:\n<code>https://telegra.ph/...</code>"
    if call.message.photo:
        await call.message.edit_caption(caption=text)
    else:
        await call.message.edit_text(text)
    await state.set_state(UserStates.waiting_for_telegraph_link)


@router.message(UserStates.waiting_for_telegraph_link)
async def process_telegraph_link(message: Message, state: FSMContext):
    link = message.text.strip()
    
    if not link.startswith("https://telegra.ph/"):
        return await message.answer("❌ Неверный формат! Ссылка должна начинаться с <code>https://telegra.ph/</code>", parse_mode="HTML")
    
    await state.update_data(telegraph_link=link)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Персональные данные", callback_data="tg_personal_data")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_func")]
    ])
    
    await message.answer(
        f"🔗 <b>Ссылка принята:</b>\n{link}\n\nВыберите причину удаления:",
        reply_markup=kb
    )
    await state.set_state(UserStates.waiting_for_telegraph_confirm)


@router.callback_query(F.data == "tg_personal_data")
async def telegraph_confirm_personal(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    link = data['telegraph_link']
    
    await call.message.edit_text("⏳ Отправляю запрос на удаление статьи...")
    
    success = await send_telegraph_report(link, call.from_user.id, call.from_user.username)
    
    if success:
        await call.message.edit_text("✅ <b>Запрос на удаление статьи успешно отправлен!</b>")
        await add_report_stat(call.from_user.id)
    else:
        await call.message.edit_text("❌ Не удалось отправить жалобу. Попробуйте позже.")
    
    await state.clear()


@router.callback_query(F.data == "back_to_func")
async def back_to_func(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await func_menu(call)  # возвращаем в меню инструментов

# --- НОВАЯ ЛОГИКА BOT DELETER ---
@router.callback_query(F.data == "bot_deleter_start")
async def bot_deleter_start(call: CallbackQuery, state: FSMContext):
    if call.message.photo:
        await call.message.edit_caption(caption="🗑 <b>Bot Deleter</b>\n\nВыберите тип бота:", reply_markup=get_bot_deleter_menu())
    else:
        await call.message.edit_text("🗑 <b>Bot Deleter</b>\n\nВыберите тип бота:", reply_markup=get_bot_deleter_menu())
    await state.set_state(UserStates.waiting_for_bot_deleter_type)

@router.callback_query(F.data.startswith("bot_deleter_"))
async def handle_bot_deleter_type(call: CallbackQuery, state: FSMContext):
    type_map = {
        "bot_deleter_personal": (InputReportReasonPersonalDetails(), [
            "Бот используется для доксинга и поиска таких данных как: номер телефона, адрес, и информацию о родственниках. Прошу принять меры",
            "Бот распространяет персональные данные: паспорта, СНИЛС, ИНН, номера, адреса. Нарушает ФЗ-152 и ст.137 УК РФ. Требую немедленной блокировки!",
            "Бот используется для незаконного доступа к данным, а именно номера телефона, ИНН, СНИЛС, адрес. Прошу принять меры"
        ]),
        "bot_deleter_pav": (InputReportReasonIllegalDrugs(), [
            "бот используется для продажи наркотических веществ, тем самым нарушая законы стран и правила телеграм. Прошу принять меры"
        ]),
        "bot_deleter_snos": (InputReportReasonOther(), [
            "бот используется для подачи массовых жалоб на телеграм аккаунты тем самым приводя к блокировке аккаунты не нарушающие правил"
        ])
    }
    
    if call.data in type_map:
        reason, texts = type_map[call.data]
        await state.update_data(bot_deleter_reason=reason, bot_deleter_texts=texts)
        
        kb_back = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_deleter_start")]])
        if call.message.photo:
            await call.message.edit_caption(caption="Введите юзернейм бота (@target_bot):", reply_markup=kb_back)
        else:
            await call.message.edit_text("Введите юзернейм бота (@target_bot):", reply_markup=kb_back)
        await state.set_state(UserStates.waiting_for_bot_deleter_target)
    else:
        await call.answer("Неизвестный тип")

@router.message(UserStates.waiting_for_bot_deleter_target)
async def bot_deleter_process(message: Message, state: FSMContext):
    target = message.text.strip()
    data = await state.get_data()
    reason = data['bot_deleter_reason']
    texts = data['bot_deleter_texts']
    report_text = random.choice(texts)
    
    await message.answer(f"⏳ Bot Deleter запущен на {target}...")
    s, f = await run_bot_deleter(target, reason, report_text, message.from_user.id, message.from_user.username or "Unknown")
    await message.answer(f"✅ Результат: Успешно {s}, Ошибок {f}")
    await state.clear()

# Логика AU запуск (оставлена без изменений)
@router.callback_query(F.data == "au_start")
async def au_start(call: CallbackQuery, state: FSMContext):
    if call.message.photo:
        await call.message.edit_caption(caption="Введите ссылку (t.me/username или @username):")
    else:
        await call.message.edit_text("Введите ссылку (t.me/username или @username):")
    await state.set_state(UserStates.waiting_for_au_target)

@router.message(UserStates.waiting_for_au_target)
async def au_target(message: Message, state: FSMContext):
    target = message.text.strip()
    
    if target.startswith("@"):
        target = f"t.me/{target[1:]}"
    elif target.startswith("https://t.me/"):
        target = target.replace("https://", "")
    elif target.startswith("http://t.me/"):
        target = target.replace("http://", "")

    await state.update_data(target=target)
    await message.answer(f"✅ Принято: <b>{target}</b>\n\n📝 Введите текст жалобы:")
    await state.set_state(UserStates.waiting_for_au_reason)

@router.message(UserStates.waiting_for_au_reason)
async def au_reason(message: Message, state: FSMContext):
    await state.update_data(reason=message.text)
    data = await state.get_data()
    
    confirm_text = (
        f"🎯 <b>Цель:</b> {data['target']}\n"
        f"📝 <b>Жалоба:</b> {data['reason']}\n\n"
        f"❓ <b>Отправить эту жалобу в очередь?</b>"
    )
    
    await message.answer(confirm_text, reply_markup=get_confirm_menu())
    await state.set_state(UserStates.waiting_for_au_confirm)

@router.callback_query(UserStates.waiting_for_au_confirm, F.data == "au_confirm_yes")
async def au_confirm_yes(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    # Передаем также токен бота, чтобы воркер знал, с какого бота отправлять ответ
    await au_report_queue.put((call.from_user.id, data['target'], data['reason'], call.bot.token))
    await call.message.edit_text("🚀 Успешно! Добавлено в очередь AU Report.")
    await state.clear()

@router.callback_query(UserStates.waiting_for_au_confirm, F.data == "au_confirm_no")
async def au_confirm_no(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("❌ Отправка жалобы отменена.")
    await state.clear()

# --- ПРОФИЛЬ И АДМИНКА ---
@router.callback_query(F.data == "profile")
async def profile(call: CallbackQuery, state: FSMContext):
    await state.clear()
    users = await get_users()
    u = users.get(call.from_user.id, {"sub_until": "0", "reports": 0, "tokens": []})
    sub = u['sub_until']
    is_sub = await has_sub(call.from_user.id)
    status = "Активна" if is_sub else "Нет"
    tokens = u.get("tokens", [])
    
    text = (f"👤 <b>Профиль</b>\n"
            f"ID: <code>{call.from_user.id}</code>\n"
            f"Подписка: {status} ({sub})\n"
            f"Репортов: {u['reports']}\n"
            f"Зеркал: {len(tokens)}/{MAX_MIRRORS}")
            
    kb = [
        [InlineKeyboardButton(text="➕ Создать зеркало", callback_data="add_mirror")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")]
    ]
    markup = InlineKeyboardMarkup(inline_keyboard=kb)
    
    if call.message.photo:
        await call.message.edit_caption(caption=text, reply_markup=markup)
    else:
        await call.message.edit_text(text, reply_markup=markup)

@router.callback_query(F.data == "admin_panel")
async def admin_panel(call: CallbackQuery):
    if call.from_user.id in ALLOWED_USERS:
        if call.message.photo:
            await call.message.edit_caption(caption="<b>🛠 Админка</b>", reply_markup=get_admin_menu())
        else:
            await call.message.edit_text("<b>🛠 Админка</b>", reply_markup=get_admin_menu())

@router.callback_query(F.data == "adm_broadcast")
async def adm_broadcast(call: CallbackQuery, state: FSMContext):
    if call.message.photo:
        await call.message.edit_caption(caption="Введите текст рассылки для ВСЕХ:")
    else:
        await call.message.edit_text("Введите текст рассылки для ВСЕХ:")
    await state.set_state(AdminStates.waiting_for_broadcast)

@router.message(AdminStates.waiting_for_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    users = await get_users()
    await message.answer(f"🚀 Начинаю рассылку на {len(users)} чел...")
    for uid in users.keys():
        try:
            await message.bot.send_message(uid, message.text)
            await asyncio.sleep(0.05)
        except: pass
    await message.answer("✅ Готово!")
    await state.clear()

@router.callback_query(F.data == "adm_sub")
async def adm_sub(call: CallbackQuery, state: FSMContext):
    if call.message.photo:
        await call.message.edit_caption(caption="Введите ID:")
    else:
        await call.message.edit_text("Введите ID:")
    await state.set_state(AdminStates.waiting_for_sub_id)

@router.message(AdminStates.waiting_for_sub_id)
async def adm_sub_id(message: Message, state: FSMContext):
    await state.update_data(sid=message.text)
    await message.answer("Дни (0 - навсегда):")
    await state.set_state(AdminStates.waiting_for_sub_time)

@router.callback_query(F.data == "buy_sub")
async def buy_subscription(call: CallbackQuery):
    text = (
        "💎 <b>Покупка подписки</b>\n\n"
        "Для приобретения доступа к боту, обратитесь к админам:\n\n"
        "1.@Peredaliky\n"
        "2.@MilitaryMonesy\n\n"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="@Peredaliky", url="https://t.me/Peredaliky")],
        [InlineKeyboardButton(text="@MilitaryMonesy", url="https://t.me/MilitaryMonesy")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")]
    ])

    if call.message.photo:
        await call.message.edit_caption(caption=text, reply_markup=kb)
    else:
        await call.message.edit_text(text, reply_markup=kb)

@router.message(AdminStates.waiting_for_sub_time)
async def adm_sub_time(message: Message, state: FSMContext):
    data = await state.get_data()
    users = await get_users()
    try:
        sid = int(data['sid'])
    except ValueError:
        return await message.answer("❌ Ошибка: ID должен быть числом.")
        
    if sid not in users: users[sid] = {"name": "User", "reports": 0, "tokens": []}
    if message.text == "0": users[sid]["sub_until"] = "∞"
    else:
        try:
            d = datetime.datetime.now() + datetime.timedelta(days=int(message.text))
            users[sid]["sub_until"] = d.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return await message.answer("❌ Ошибка: Дни должны быть числом.")
            
    await save_users(users)
    await message.answer("✅ Выдано!")
    await state.clear()

@router.callback_query(F.data == "adm_unsub")
async def adm_unsub(call: CallbackQuery, state: FSMContext):
    if call.message.photo:
        await call.message.edit_caption(caption="Введите ID пользователя для снятия подписки:")
    else:
        await call.message.edit_text("Введите ID пользователя для снятия подписки:")
    await state.set_state(AdminStates.waiting_for_unsub_id)

@router.message(AdminStates.waiting_for_unsub_id)
async def process_unsub(message: Message, state: FSMContext):
    try:
        sid = int(message.text)
        users = await get_users()
        if sid in users:
            users[sid]["sub_until"] = "0"
            await save_users(users)
            await message.answer(f"✅ Подписка у пользователя <code>{sid}</code> успешно забрана!")
        else:
            await message.answer("❌ Пользователь с таким ID не найден в базе данных.")
    except ValueError:
        await message.answer("❌ Ошибка: ID должен быть числом.")
    await state.clear()

@router.callback_query(F.data == "adm_stats")
async def adm_stats(call: CallbackQuery):
    users = await get_users()
    total_users = len(users)
    active_subs = 0
    for uid in users:
        if await has_sub(uid):
            active_subs += 1
    total_reports = sum(user.get("reports", 0) for user in users.values())
    total_mirrors = sum(len(user.get("tokens", [])) for user in users.values())
    
    text = (
        "📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"💎 Активных подписок: <b>{active_subs}</b>\n"
        f"📢 Всего отправлено репортов: <b>{total_reports}</b>\n"
        f"🪞 Запущено зеркал: <b>{total_mirrors}</b>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
    ])
    
    if call.message.photo:
        await call.message.edit_caption(caption=text, reply_markup=kb)
    else:
        await call.message.edit_text(text, reply_markup=kb)

# 6. КОРРЕКТНЫЙ MAIN (Для запуска лог-клиента)
async def main():
    await init_db()
    
    # Инициализируем клиент логов перед запуском ботов
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

