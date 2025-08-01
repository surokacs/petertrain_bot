import logging
import json
import random
import re
import smtplib
from datetime import datetime
from email.mime.text import MIMEText

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

from dotenv import load_dotenv
load_dotenv()

import os

API_TOKEN = os.getenv('API_TOKEN')
PAYMENT_PROVIDER_TOKEN = os.getenv('PAYMENT_PROVIDER_TOKEN')
EMAIL_SENDER = os.getenv('EMAIL_SENDER')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')
ADMINS = [1371340477, 812859554]  # ← твой Telegram ID

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())


class Order(StatesGroup):
    waiting_for_email = State()
    waiting_for_payment = State()


def load_products():
    with open("data/products.json", "r", encoding="utf-8") as f:
        return json.load(f)


def save_order(order):
    try:
        with open("data/orders.json", "r", encoding="utf-8") as f:
            orders = json.load(f)
    except FileNotFoundError:
        orders = []

    orders.append(order)
    with open("data/orders.json", "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=4)


def generate_order_id():
    return random.randint(100000, 999999)


@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    products = load_products()
    keyboard = types.InlineKeyboardMarkup()
    for i, product in enumerate(products):
        keyboard.add(types.InlineKeyboardButton(
            text=f"{product['name']} — {product['price'] // 100}₽",
            callback_data=f"product_{i}"
        ))
    await message.answer("Выберите билет:", reply_markup=keyboard)


@dp.callback_query_handler(lambda c: c.data.startswith("product_"))
async def select_product(callback_query: types.CallbackQuery, state: FSMContext):
    index = int(callback_query.data.split("_")[1])
    product = load_products()[index]

    await state.update_data(product=product)
    await bot.send_message(callback_query.from_user.id, "Введите ваш email:")
    await Order.waiting_for_email.set()
    await callback_query.answer()


@dp.message_handler(state=Order.waiting_for_email)
async def enter_email(message: types.Message, state: FSMContext):
    email = message.text.strip()
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        await message.reply("❌ Неверный email. Повторите ввод:")
        return

    data = await state.get_data()
    product = data['product']
    order_id = generate_order_id()

    await state.update_data(email=email, order_id=order_id)

    await bot.send_invoice(
        chat_id=message.chat.id,
        title=product["name"],
        description=product["description"],
        payload=str(order_id),
        provider_token=PAYMENT_PROVIDER_TOKEN,
        currency="RUB",
        prices=[types.LabeledPrice(label=product["name"], amount=product["price"])]
    )

    await Order.waiting_for_payment.set()


@dp.pre_checkout_query_handler(lambda query: True)
async def pre_checkout_query(pre_checkout_q: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)


@dp.message_handler(content_types=types.ContentType.SUCCESSFUL_PAYMENT, state=Order.waiting_for_payment)
async def process_payment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    product = data['product']
    email = data['email']
    order_id = data['order_id']
    total = message.successful_payment.total_amount // 100

    order = {
        "order_id": order_id,
        "email": email,
        "item": product['name'],
        "price": total,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    save_order(order)

    # Отправка чека на почту
    try:
        msg = MIMEText(
            f"Спасибо за покупку!\n\n"
            f"Номер заказа: {order_id}\n"
            f"Товар: {product['name']}\n"
            f"Сумма: {total}₽"
        )
        msg["Subject"] = f"Чек заказа №{order_id}"
        msg["From"] = EMAIL_SENDER
        msg["To"] = email

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)

    except Exception as e:
        logging.error(f"Ошибка при отправке email: {e}")

    await message.answer(f"✅ Оплата прошла успешно!\nНомер заказа: {order_id}")
    await state.finish()


@dp.message_handler(commands=['admin'])
async def admin_view(message: types.Message):
    if message.from_user.id not in ADMINS:
        await message.reply("⛔ Нет доступа")
        return

    try:
        with open("data/orders.json", "r", encoding="utf-8") as f:
            orders = json.load(f)
    except FileNotFoundError:
        await message.answer("Заказов пока нет.")
        return

    if not orders:
        await message.answer("Нет заказов.")
        return

    for order in orders[-10:]:
        text = (
            f"📦 Заказ #{order['order_id']}\n"
            f"📧 Email: {order['email']}\n"
            f"🎟 Товар: {order['item']}\n"
            f"💰 Сумма: {order['price']}₽\n"
            f"🕒 Время: {order['time']}"
        )
        await message.answer(text)


if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)