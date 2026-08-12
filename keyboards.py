from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import PLANS


def main_menu():
    b = InlineKeyboardBuilder()

    b.button(
        text="🎁 Получить 3 бесплатных сигнала",
        callback_data="demo"
    )

    b.button(
        text="💎 Купить подписку",
        callback_data="plans"
    )

    b.button(
        text="🎁 Реферальная программа",
        callback_data="referral"
    )

    b.button(
        text="🔍 NFT-Tracker",
        url="https://t.me/fyvfhvfhyfbot"
    )

    b.button(
        text="📖 Как это работает",
        callback_data="how"
    )

    b.button(
        text="💬 Поддержка",
        callback_data="support"
    )

    b.adjust(1)

    return b.as_markup()


def back_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data="back"
                )
            ]
        ]
    )


def plans_menu():
    b = InlineKeyboardBuilder()

    for code, plan in PLANS.items():
        b.button(
            text=f"{plan.title} — ⭐ {plan.stars} / {plan.usdt:g} USDT",
            callback_data=f"plan:{code}"
        )

    b.button(
        text="◀️ Назад",
        callback_data="back"
    )

    b.adjust(1)

    return b.as_markup()


def payment_menu(plan_code):
    b = InlineKeyboardBuilder()

    b.button(
        text="⭐ Оплатить Stars",
        callback_data=f"paystars:{plan_code}"
    )

    b.button(
        text="₿ Оплатить USDT",
        callback_data=f"paycrypto:{plan_code}"
    )

    b.button(
        text="◀️ Назад",
        callback_data="plans"
    )

    b.adjust(1)

    return b.as_markup()


def referral_menu():
    b = InlineKeyboardBuilder()

    b.button(
        text="📨 Пригласить друга",
        callback_data="referral_link"
    )

    b.button(
        text="◀️ Назад",
        callback_data="back"
    )

    b.adjust(1)

    return b.as_markup()


def admin_menu():
    b = InlineKeyboardBuilder()

    b.button(
        text="📊 Статистика",
        callback_data="admin:stats"
    )

    b.button(
        text="👥 Пользователи",
        callback_data="admin:users"
    )

    b.button(
        text="💳 Последние платежи",
        callback_data="admin:payments"
    )

    b.button(
        text="🆘 Поддержка",
        callback_data="admin:support"
    )

    b.button(
        text="📢 Рассылка",
        callback_data="admin:broadcast"
    )

    b.button(
        text="➕ Выдать подписку",
        callback_data="admin:grant"
    )

    b.button(
        text="🔎 Найти пользователя",
        callback_data="admin:find"
    )

    b.button(
        text="◀️ Главное меню",
        callback_data="back"
    )

    b.adjust(2, 2, 1, 1, 1)

    return b.as_markup()
