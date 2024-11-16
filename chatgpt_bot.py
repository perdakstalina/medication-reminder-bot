import asyncio
import os
import requests
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime
from pytz import timezone
import aioschedule as schedule
import sqlite3
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.bot import Bot
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

# Загрузка переменных окружения из .env
load_dotenv()
API_TOKEN = os.getenv("API_TOKEN")
PORT = int(os.getenv("PORT", 8080))  # Порт, который Render ожидает (по умолчанию 8080)

# Указываем временную зону Москвы
moscow_tz = timezone("Europe/Moscow")

# Подключение к базе данных
db_path = os.getenv("DB_PATH", "medication_reminders.db")  # Используем путь из переменной среды, если указан
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Удаление вебхука перед запуском
response = requests.post(f"https://api.telegram.org/bot{API_TOKEN}/deleteWebhook")
if response.status_code == 200:
    print("Webhook удален успешно!")
else:
    print("Ошибка при удалении webhook:", response.text)

# Инициализация бота
default_properties = DefaultBotProperties(parse_mode="HTML")
bot = Bot(token=API_TOKEN, session=AiohttpSession(), default=default_properties)
dp = Dispatcher(storage=MemoryStorage())

# Создание таблицы для хранения напоминаний
cursor.execute("""
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    medication_name TEXT NOT NULL,
    reminder_time TEXT NOT NULL,
    sent INTEGER DEFAULT 0
)
""")
conn.commit()

# Состояния для FSM
class ReminderState(StatesGroup):
    medication_name = State()
    reminder_time = State()

# Клавиатуры
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить препарат", callback_data="add_reminder")],
        [InlineKeyboardButton(text="📋 Мои напоминания", callback_data="list_reminders")]
    ])

def back_button():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
    ])

def reminder_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

# Команды
@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_name = message.from_user.first_name or "друг"
    await message.answer(
        f"👋 Привет, <b>{user_name}</b>!\n\n"
        "Я помогу тебе не забыть принять лекарства вовремя. Выбери, что хочешь сделать:",
        reply_markup=main_menu()
    )

@dp.callback_query(F.data == "main_menu")
async def go_back_to_main_menu(call: types.CallbackQuery):
    await call.message.edit_text(
        "🏠 Вы вернулись в главное меню. Выберите действие:",
        reply_markup=main_menu()
    )

@dp.callback_query(F.data == "add_reminder")
async def add_reminder(call: types.CallbackQuery, state: FSMContext):
    await call.message.edit_text(
        "✍️ <b>Напишите название препарата</b>, который нужно принять:",
        reply_markup=back_button()
    )
    await state.set_state(ReminderState.medication_name)

@dp.message(ReminderState.medication_name)
async def set_medication_name(message: types.Message, state: FSMContext):
    await state.update_data(medication_name=message.text)
    await message.answer(
        "⏰ <b>Когда нужно напомнить?</b>\n"
        "Напишите время в формате <code>ЧЧ:ММ</code> (например, 14:30):",
        reply_markup=back_button()
    )
    await state.set_state(ReminderState.reminder_time)

@dp.message(ReminderState.reminder_time)
async def set_reminder_time(message: types.Message, state: FSMContext):
    try:
        reminder_time = datetime.strptime(message.text, "%H:%M").time()
        user_data = await state.get_data()
        
        cursor.execute(
            "INSERT INTO reminders (user_id, medication_name, reminder_time) VALUES (?, ?, ?)",
            (message.from_user.id, user_data['medication_name'], message.text)
        )
        conn.commit()

        await message.answer(
            f"✅ Напоминание для препарата <b>{user_data['medication_name']}</b> "
            f"на <b>{message.text}</b> успешно добавлено!",
            reply_markup=main_menu()
        )
        await state.clear()
    except ValueError:
        await message.answer(
            "⚠️ Неправильный формат времени. Попробуй ещё раз:\n"
            "Напишите время в формате <code>ЧЧ:ММ</code> (например, 14:30):",
            reply_markup=back_button()
        )

@dp.callback_query(F.data == "list_reminders")
async def list_reminders(call: types.CallbackQuery):
    cursor.execute("SELECT id, medication_name, reminder_time FROM reminders WHERE user_id = ?", (call.from_user.id,))
    reminders = cursor.fetchall()

    if reminders:
        response = "📋 <b>Ваши напоминания:</b>\n\n"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for reminder in reminders:
            response += f"💊 <b>{reminder[1]}</b> в <b>{reminder[2]}</b> (ID: {reminder[0]})\n"
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"❌ Удалить {reminder[1]} ({reminder[2]})",
                    callback_data=f"delete_{reminder[0]}"
                )
            ])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")])
        await call.message.edit_text(response, reply_markup=keyboard)
    else:
        await call.message.edit_text(
            "📭 <b>У вас пока нет напоминаний.</b>",
            reply_markup=main_menu()
        )

@dp.callback_query(lambda call: call.data.startswith("delete_"))
async def delete_reminder(call: types.CallbackQuery):
    reminder_id = int(call.data.split("_")[1])
    cursor.execute("DELETE FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, call.from_user.id))
    conn.commit()
    await call.message.edit_text(
        "✅ Напоминание удалено!",
        reply_markup=main_menu()
    ) 

async def send_reminders():
    # Получаем текущее время в МСК
    now = datetime.now(moscow_tz).strftime("%H:%M")
    print(f"Checking reminders at {now}")  # Логирование для диагностики

    cursor.execute("SELECT id, user_id, medication_name FROM reminders WHERE reminder_time = ? AND sent = 0", (now,))
    reminders = cursor.fetchall()

    for reminder in reminders:
        user_id = reminder[1]
        medication_name = reminder[2]
        await bot.send_message(
            user_id,
            f"🔔 <b>Напоминание:</b>\nПора принять <b>{medication_name}</b>!",
            reply_markup=reminder_menu()
        )
        cursor.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder[0],))
    conn.commit()

async def scheduler():
    while True:
        jobs = schedule.jobs
        for job in jobs:
            if asyncio.iscoroutinefunction(job.job_func):
                asyncio.create_task(job.job_func())
            else:
                job.job_func()
        await asyncio.sleep(1)

async def start_server():
    from aiohttp import web
    async def handle(request):
        return web.Response(text="Bot is running!")
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

async def main():
    asyncio.create_task(start_server())  # Запуск фейкового веб-сервера для Render
    schedule.every().minute.do(send_reminders)
    asyncio.create_task(scheduler())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
