import asyncio
import logging
import os
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    LabeledPrice,
    PreCheckoutQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import config
from db import Database
from keyboards import *
from services import CryptoPay, AccessManager, safe_copy_message


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=getattr(
        logging,
        os.getenv("LOG_LEVEL", "INFO").upper()
    ),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

log = logging.getLogger("gifts-intelligence")


# ============================================================
# BOT / SERVICES
# ============================================================

bot = Bot(
    config.BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()

db = Database(
    config.SUPABASE_URL,
    config.SUPABASE_KEY
)

crypto = CryptoPay(
    config.CRYPTOBOT_API_TOKEN,
    config.CRYPTOBOT_API_BASE
)

access = AccessManager(
    bot,
    db,
    config.SIGNAL_CHANNEL_ID
)


# ============================================================
# ADMIN
# ============================================================

ADMIN_CHAT_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_CHAT_IDS", "").split(",")
    if x.strip().lstrip("-").isdigit()
}


def is_admin(user) -> bool:
    return bool(
        user.username
        and user.username.lower() in config.ADMIN_USERNAMES
    )


async def notify_admin_chats(text: str):
    for chat_id in ADMIN_CHAT_IDS:
        try:
            await bot.send_message(
                chat_id,
                text
            )
        except Exception as e:
            log.warning(
                "Admin notification failed for %s: %s",
                chat_id,
                e
            )


# ============================================================
# FSM
# ============================================================

class SupportState(StatesGroup):
    waiting_message = State()


class AdminState(StatesGroup):
    waiting_broadcast = State()
    waiting_find = State()
    waiting_grant = State()
    waiting_reply = State()


# ============================================================
# USERS
# ============================================================

async def ensure_user(user):
    result = await db.upsert_user(user)

    if (
        user.username
        and user.username.lower() in config.ADMIN_USERNAMES
    ):
        ADMIN_CHAT_IDS.add(user.id)

    return result


# ============================================================
# REFERRALS
# ============================================================

async def register_referral(
    referred_user,
    start_arg: str | None,
):
    """
    Регистрирует реферала при первом /start.

    Формат:
        /start ref_<telegram_id>

    Повторно существующую связь не создаём.
    Сам себя пригласить нельзя.
    """

    if not start_arg:
        return None

    if not start_arg.startswith("ref_"):
        return None

    try:
        referrer_tg_id = int(start_arg[4:])
    except (ValueError, TypeError):
        return None

    if referrer_tg_id == referred_user.id:
        return None

    referrer = await db.get_user(referrer_tg_id)

    if not referrer:
        return None

    referred = await db.get_user(referred_user.id)

    if not referred:
        return None

    def find_existing():
        return (
            db.client
            .table("referrals")
            .select("*")
            .eq("referred_id", referred["id"])
            .limit(1)
            .execute()
            .data
        )

    existing = await db._run(find_existing)

    if existing:
        return existing[0]

    def create():
        return (
            db.client
            .table("referrals")
            .insert({
                "referrer_id": referrer["id"],
                "referred_id": referred["id"],
                "status": "registered",
            })
            .execute()
            .data
        )

    rows = await db._run(create)

    if rows:
        log.info(
            "Referral registered: referrer=%s referred=%s",
            referrer_tg_id,
            referred_user.id,
        )
        return rows[0]

    return None


async def get_referral_stats(user_db_id):
    """
    Возвращает количество приглашённых пользователей,
    которые совершили свой первый успешный платёж.
    """

    def query():
        return (
            db.client
            .table("referrals")
            .select("id", count="exact")
            .eq("referrer_id", user_db_id)
            .eq("status", "paid")
            .execute()
        )

    result = await db._run(query)

    return result.count or 0


async def process_referral_payment(
    referred_user_db_id,
    payment_id,
):
    """
    Вызывается после успешной оплаты.

    Засчитывает только первый платёж приглашённого.
    После каждой тройки оплативших друзей выдаёт +30 дней.
    """

    def find_referral():
        return (
            db.client
            .table("referrals")
            .select("*")
            .eq("referred_id", referred_user_db_id)
            .limit(1)
            .execute()
            .data
        )

    rows = await db._run(find_referral)

    if not rows:
        return None

    referral = rows[0]

    # Уже засчитан ранее.
    if referral.get("status") == "paid":
        return None

    # Засчитываем первый успешный платёж.
    def mark_paid():
        return (
            db.client
            .table("referrals")
            .update({
                "status": "paid",
                "first_payment_id": payment_id,
                "paid_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("id", referral["id"])
            .execute()
            .data
        )

    await db._run(mark_paid)

    referrer_id = referral["referrer_id"]

    # Сколько теперь оплативших друзей.
    paid_count = await get_referral_stats(referrer_id)

    # Награда только за каждую новую тройку:
    # 3, 6, 9, 12...
    if paid_count < 3 or paid_count % 3 != 0:
        return {
            "referrer_id": referrer_id,
            "paid_count": paid_count,
            "reward": False,
        }

    # Проверяем, выдавалась ли уже награда за это количество.
    def find_reward():
        return (
            db.client
            .table("referral_rewards")
            .select("*")
            .eq("user_id", referrer_id)
            .eq("referrals_count", paid_count)
            .limit(1)
            .execute()
            .data
        )

    reward_exists = await db._run(find_reward)

    if reward_exists:
        return {
            "referrer_id": referrer_id,
            "paid_count": paid_count,
            "reward": False,
        }

    # Получаем Telegram ID реферера.
    referrer = await db.get_user_by_db_id(referrer_id)

    if not referrer:
        return {
            "referrer_id": referrer_id,
            "paid_count": paid_count,
            "reward": False,
        }

    telegram_id = referrer["telegram_id"]

    # Выдаём 30 дней.
    #
    # Используем тариф 1m как механизм создания/продления
    # подписки. В config.PLANS он должен существовать.
    if "1m" not in config.PLANS:
        log.error(
            "Referral reward cannot be granted: plan '1m' not found"
        )
        return {
            "referrer_id": referrer_id,
            "paid_count": paid_count,
            "reward": False,
        }

    try:
        invite = await access.create_invite(
            telegram_id,
            "1m"
        )

        subscription = await db.manual_subscription(
            telegram_id,
            "1m",
            invite
        )

        if not subscription:
            log.error(
                "Failed to create referral subscription for %s",
                telegram_id,
            )
            return {
                "referrer_id": referrer_id,
                "paid_count": paid_count,
                "reward": False,
            }

        # Фиксируем выданную награду.
        def create_reward():
            return (
                db.client
                .table("referral_rewards")
                .insert({
                    "user_id": referrer_id,
                    "referrals_count": paid_count,
                    "reward_days": 30,
                })
                .execute()
                .data
            )

        await db._run(create_reward)

        log.info(
            "Referral reward granted: user=%s referrals=%s days=30",
            telegram_id,
            paid_count,
        )

        try:
            await bot.send_message(
                telegram_id,
                "🎉 <b>Реферальная награда!</b>\n\n"
                f"Твои друзья принесли <b>{paid_count}</b> оплативших пользователей.\n\n"
                "💎 Тебе начислено <b>30 дней</b> подписки!",
            )
        except Exception as e:
            log.warning(
                "Failed to notify referrer %s: %s",
                telegram_id,
                e,
            )

        await notify_admin_chats(
            "🎁 <b>Реферальная награда</b>\n\n"
            f"👤 Пользователь: <code>{telegram_id}</code>\n"
            f"👥 Оплативших друзей: <b>{paid_count}</b>\n"
            "💎 Начислено: <b>30 дней</b>"
        )

        return {
            "referrer_id": referrer_id,
            "paid_count": paid_count,
            "reward": True,
        }

    except Exception:
        log.exception(
            "Referral reward error for user %s",
            telegram_id,
        )

        return {
            "referrer_id": referrer_id,
            "paid_count": paid_count,
            "reward": False,
        }


async def get_bot_username() -> str:
    me = await bot.get_me()
    return me.username


async def referral_keyboard():
    """
    Клавиатура реферального раздела.
    """

    username = await get_bot_username()

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔗 Пригласить друга",
                    url=f"https://t.me/{username}?start=ref_{_current_referrer_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data="back"
                )
            ],
        ]
    )


# Временная переменная используется только для формирования
# URL непосредственно перед отправкой клавиатуры.
_current_referrer_id = 0


async def build_referral_keyboard(telegram_id: int):
    username = await get_bot_username()

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔗 Пригласить друга",
                    url=f"https://t.me/{username}?start=ref_{telegram_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data="back"
                )
            ],
        ]
    )


# ============================================================
# /START
# ============================================================

@dp.message(CommandStart(deep_link=True))
async def start_deep_link(
    message: Message,
    command,
):
    user = await ensure_user(message.from_user)

    start_arg = command.args

    if start_arg:
        await register_referral(
            message.from_user,
            start_arg
        )

    sub = await db.get_active_subscription(user["id"])

    status = ""

    if sub:
        status = (
            f"\n\n💎 Подписка активна до "
            f"<b>{sub['expires_at'][:10]}</b>."
        )

    await message.answer(
        "💎 <b>Gifts Intelligence</b>\n\n"
        "Автоматический мониторинг рынка Telegram NFT Gifts.\n\n"
        "📡 <b>Gifts Intelligence</b> — потенциально выгодные сделки "
        "и арбитражные сигналы\n"
        "🔍 <b>NFT-Tracker</b> — поиск подарков по модели, фону, "
        "номеру и другим параметрам\n\n"
        "⚠️ Сигнал не является гарантией прибыли. Перед покупкой "
        "самостоятельно проверяйте реальные цены, конкурентов, "
        "историю продаж и ликвидность"
        + status,
        reply_markup=main_menu(),
    )


@dp.message(CommandStart())
async def start(message: Message):
    user = await ensure_user(message.from_user)

    sub = await db.get_active_subscription(user["id"])

    status = ""

    if sub:
        status = (
            f"\n\n💎 Подписка активна до "
            f"<b>{sub['expires_at'][:10]}</b>."
        )

    await message.answer(
        "💎 <b>Gifts Intelligence</b>\n\n"
        "Автоматический мониторинг рынка Telegram NFT Gifts.\n\n"
        "📡 <b>Gifts Intelligence</b> — потенциально выгодные сделки "
        "и арбитражные сигналы\n"
        "🔍 <b>NFT-Tracker</b> — поиск подарков по модели, фону, "
        "номеру и другим параметрам\n\n"
        "⚠️ Сигнал не является гарантией прибыли. Перед покупкой "
        "самостоятельно проверяйте реальные цены, конкурентов, "
        "историю продаж и ликвидность"
        + status,
        reply_markup=main_menu(),
    )


# ============================================================
# ADMIN COMMAND
# ============================================================

@dp.message(Command("admin"))
async def admin_cmd(message: Message):
    await ensure_user(message.from_user)

    if not is_admin(message.from_user):
        await message.answer("⛔ Доступ запрещён")
        return

    await message.answer(
        "👑 <b>Gifts Intelligence — Admin Panel</b>",
        reply_markup=admin_menu()
    )


# ============================================================
# BACK
# ============================================================

@dp.callback_query(F.data == "back")
async def back(call: CallbackQuery):
    await call.answer()

    await call.message.edit_text(
        "💎 <b>Gifts Intelligence</b>\n\n"
        "Выберите действие:",
        reply_markup=main_menu()
    )


# ============================================================
# REFERRALS
# ============================================================

@dp.callback_query(F.data == "referral")
async def referral(call: CallbackQuery):
    await call.answer()

    user = await ensure_user(call.from_user)

    paid_count = await get_referral_stats(user["id"])

    keyboard = await build_referral_keyboard(
        call.from_user.id
    )

    await call.message.edit_text(
        "👥 <b>Рефералы</b>\n\n"
        f"Оплативших друзей: <b>{paid_count}</b>\n\n"
        "За каждые 3 оплаты друзей ты получаешь "
        "<b>30 дней подписки</b>.",
        reply_markup=keyboard
    )


# ============================================================
# PLANS
# ============================================================

@dp.callback_query(F.data == "plans")
async def plans(call: CallbackQuery):
    await call.answer()

    await call.message.edit_text(
        "💎 <b>Выберите подписку</b>\n\n"
        "📡 Полный доступ к Gifts Intelligence\n"
        "🔍 NFT-Tracker\n"
        "⚡ Все новые сигналы\n\n"
        "⚠️ Перед каждой сделкой проверяйте рынок самостоятельно",
        reply_markup=plans_menu()
    )


@dp.callback_query(F.data.startswith("plan:"))
async def plan_details(call: CallbackQuery):
    await call.answer()

    code = call.data.split(":")[1]
    plan = config.PLANS[code]

    await call.message.edit_text(
        f"💎 <b>{plan.title}</b>\n\n"
        f"⭐ Telegram Stars: <b>{plan.stars}</b>\n"
        f"₿ USDT: <b>{plan.usdt:g}</b>\n\n"
        "Что входит:\n"
        "📡 Все новые Gifts Intelligence сигналы\n"
        "🔍 NFT-Tracker\n"
        "🔐 Доступ в приватный канал\n\n"
        "Выберите способ оплаты:",
        reply_markup=payment_menu(code)
    )


# ============================================================
# STARS PAYMENT
# ============================================================

@dp.callback_query(F.data.startswith("paystars:"))
async def pay_stars(call: CallbackQuery):
    await call.answer()

    code = call.data.split(":")[1]
    plan = config.PLANS[code]

    user = await ensure_user(call.from_user)

    payload = f"stars:{user['id']}:{code}"

    await db.create_payment({
        "user_id": user["id"],
        "plan_code": code,
        "payment_method": "stars",
        "amount": plan.stars,
        "currency": "XTR",
        "status": "pending",
        "payload": payload,
    })

    invoice_message = await bot.send_invoice(
        chat_id=call.from_user.id,
        title=f"Gifts Intelligence — {plan.title}",
        description=(
            "Доступ к приватным NFT-сигналам "
            "и NFT-Tracker"
        ),
        payload=payload,
        currency="XTR",
        prices=[
            LabeledPrice(
                label=plan.title,
                amount=plan.stars
            )
        ],
        provider_token="",
    )

    await call.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⭐ Оплатить Stars",
                        callback_data=f"paystars:{code}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="₿ Оплатить USDT",
                        callback_data=f"paycrypto:{code}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="◀️ Назад",
                        callback_data=(
                            f"back_invoice:"
                            f"{invoice_message.message_id}"
                        )
                    )
                ],
            ]
        )
    )


@dp.callback_query(F.data.startswith("back_invoice:"))
async def back_invoice(call: CallbackQuery):
    await call.answer()

    invoice_message_id = int(
        call.data.split(":")[1]
    )

    try:
        await bot.delete_message(
            chat_id=call.from_user.id,
            message_id=invoice_message_id
        )
    except TelegramBadRequest:
        pass

    await call.message.edit_text(
        "💎 <b>Выберите подписку</b>\n\n"
        "📡 Полный доступ к Gifts Intelligence\n"
        "🔍 NFT-Tracker\n"
        "⚡ Все новые сигналы\n\n"
        "⚠️ Перед каждой сделкой проверяйте рынок самостоятельно",
        reply_markup=plans_menu()
    )


@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


# ============================================================
# STARS SUCCESSFUL PAYMENT
# ============================================================

@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    payment = message.successful_payment

    user = await ensure_user(
        message.from_user
    )

    def find_charge():
        return (
            db.client
            .table("payments")
            .select("*")
            .eq(
                "telegram_charge_id",
                payment.telegram_payment_charge_id
            )
            .limit(1)
            .execute()
            .data
        )

    existing = await db._run(find_charge)

    code = payment.invoice_payload.split(":")[-1]

    if existing and existing[0].get("status") == "paid":
        await message.answer(
            "✅ Этот платёж уже обработан",
            reply_markup=main_menu()
        )
        return

    if existing:
        await db.mark_payment_paid(
            existing[0]["id"],
            telegram_charge_id=(
                payment.telegram_payment_charge_id
            ),
        )

        payment_row = existing[0]

    else:
        payment_row = await db.create_payment({
            "user_id": user["id"],
            "plan_code": code,
            "payment_method": "stars",
            "amount": payment.total_amount,
            "currency": "XTR",
            "status": "paid",
            "telegram_charge_id": (
                payment.telegram_payment_charge_id
            ),
            "payload": payment.invoice_payload,
            "paid_at": datetime.now(
                timezone.utc
            ).isoformat(),
        })

    subscription, invite = await access.activate(
        message.from_user.id,
        code,
        "stars"
    )

    plan = config.PLANS[code]

    await message.answer(
        f"🎉 <b>Оплата прошла успешно!</b>\n\n"
        f"💎 Тариф: <b>{plan.title}</b>\n"
        f"⭐ Оплачено: <b>{plan.stars} Stars</b>\n"
        f"⏰ Доступ до: "
        f"<b>{subscription['expires_at'][:10]}</b>\n\n"
        "📡 <b>Gifts Intelligence</b>\n"
        "Нажмите кнопку ниже, чтобы войти "
        "в приватный канал\n\n"
        "🔍 <b>NFT-Tracker</b> — используйте отдельную кнопку",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔐 Войти в Gifts Intelligence",
                        url=invite
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔍 Открыть NFT-Tracker",
                        url=(
                            f"https://t.me/"
                            f"{config.TRACKER_BOT_USERNAME}"
                        )
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🏠 Главное меню",
                        callback_data="back"
                    )
                ],
            ]
        )
    )

    # ========================================================
    # REFERRAL
    # ========================================================

    referral_result = await process_referral_payment(
        user["id"],
        payment_row["id"]
    )

    await notify_admin_chats(
        f"💰 <b>Новая оплата Stars</b>\n\n"
        f"👤 @{message.from_user.username or 'no_username'} "
        f"({message.from_user.id})\n"
        f"💎 {plan.title}\n"
        f"⭐ {plan.stars}\n"
        f"⏰ до {subscription['expires_at'][:10]}"
    )

    if referral_result:
        log.info(
            "Referral payment processed: %s",
            referral_result
        )


# ============================================================
# CRYPTO PAYMENT
# ============================================================

@dp.callback_query(F.data.startswith("paycrypto:"))
async def pay_crypto(call: CallbackQuery):
    await call.answer()

    code = call.data.split(":")[1]
    plan = config.PLANS[code]

    user = await ensure_user(
        call.from_user
    )

    payload = f"crypto:{user['id']}:{code}"

    invoice = await crypto.create_invoice(
        amount=plan.usdt,
        description=f"Gifts Intelligence — {plan.title}",
        payload=payload,
    )

    await db.create_payment({
        "user_id": user["id"],
        "plan_code": code,
        "payment_method": "cryptobot",
        "amount": plan.usdt,
        "currency": "USDT",
        "status": "pending",
        "cryptobot_invoice_id": invoice["invoice_id"],
        "payload": payload,
    })

    pay_url = (
        invoice.get("bot_invoice_url")
        or invoice.get("mini_app_invoice_url")
    )

    await call.message.edit_text(
        "₿ <b>Оплата через CryptoBot</b>\n\n"
        f"Тариф: <b>{plan.title}</b>\n"
        f"Сумма: <b>{plan.usdt:g} USDT</b>\n\n"
        "После оплаты бот автоматически выдаст доступ.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="₿ Оплатить в CryptoBot",
                        url=pay_url
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="◀️ Назад",
                        callback_data=f"plan:{code}"
                    )
                ],
            ]
        )
    )


# ============================================================
# DEMO
# ============================================================

@dp.callback_query(F.data == "demo")
async def demo(call: CallbackQuery):
    await call.answer()

    user = await ensure_user(
        call.from_user
    )

    active_sub = await db.get_active_subscription(
        user["id"]
    )

    if active_sub:
        await call.message.edit_text(
            "💎 У вас уже есть активная подписка.\n\n"
            "Вам доступны все сигналы в приватном канале",
            reply_markup=main_menu()
        )
        return

    result = await db.start_demo(
        user["id"]
    )

    if result is None:
        await call.message.edit_text(
            "🎁 <b>Демо уже использовано</b>\n\n"
            "Вы можете получить полный доступ "
            "к Gifts Intelligence",
            reply_markup=plans_menu()
        )
        return

    count = result["signals_received"]

    await call.message.edit_text(
        "🎁 <b>Демо активировано!</b>\n\n"
        "Вы получите следующие "
        "<b>3 новых сигнала</b> из Gifts Intelligence\n\n"
        f"Прогресс: <b>{count}/3</b>\n\n"
        "Ожидайте новые сигналы",
        reply_markup=main_menu()
    )


# ============================================================
# HOW
# ============================================================

@dp.callback_query(F.data == "how")
async def how(call: CallbackQuery):
    await call.answer()

    await call.message.edit_text(
        "📖 <b>Как работает Gifts Intelligence</b>\n\n"
        "1️⃣ Система отслеживает новые NFT-подарки\n"
        "2️⃣ Сравнивает их с похожими предложениями\n"
        "3️⃣ Анализирует потенциальную разницу в цене\n"
        "4️⃣ Выделяет интересные стратегии: Premium, "
        "Monochrome, Model Arbitrage и Special Numbers\n"
        "5️⃣ Подходящие возможности попадают в канал сигналов\n\n"
        "⚠️ Важно: один дорогой лот может быть выбросом. "
        "Перед покупкой проверяйте floor, несколько конкурентов, "
        "историю продаж и ликвидность",
        reply_markup=back_menu()
    )


# ============================================================
# FAQ
# ============================================================

@dp.callback_query(F.data == "faq")
async def faq(call: CallbackQuery):
    await call.answer()

    await call.message.edit_text(
        "❓ <b>FAQ</b>\n\n"
        "<b>Есть ли гарантия прибыли?</b>\n"
        "Нет. Сигнал показывает потенциальную возможность\n\n"
        "<b>Сколько длится демо?</b>\n"
        "Следующие 3 новых сигнала\n\n"
        "<b>Что входит в подписку?</b>\n"
        "Приватный канал Gifts Intelligence + NFT-Tracker\n\n"
        "<b>Что если подписка закончилась?</b>\n"
        "Доступ удаляется автоматически. "
        "Подписку можно продлить",
        reply_markup=back_menu()
    )


# ============================================================
# SUPPORT
# ============================================================

@dp.callback_query(F.data == "support")
async def support_start(
    call: CallbackQuery,
    state: FSMContext
):
    await call.answer()

    user = await ensure_user(
        call.from_user
    )

    await db.open_support(
        user["id"]
    )

    await state.set_state(
        SupportState.waiting_message
    )

    await call.message.edit_text(
        "💬 <b>Поддержка</b>\n\n"
        "Напишите сообщение одним сообщением. "
        "Оно будет передано администратору",
        reply_markup=back_menu()
    )


@dp.message(SupportState.waiting_message)
async def support_message(
    message: Message,
    state: FSMContext
):
    user = await ensure_user(
        message.from_user
    )

    thread = await db.open_support(
        user["id"]
    )

    text = (
        message.text
        or message.caption
        or "[медиа]"
    )

    await db.add_support_message(
        thread["id"],
        user["id"],
        "user",
        "text",
        text,
        message.message_id
    )

    await state.clear()

    for admin_chat_id in ADMIN_CHAT_IDS:
        try:
            await bot.send_message(
                admin_chat_id,
                "🆘 <b>Новое обращение</b>\n\n"
                f"👤 @{message.from_user.username or 'no_username'}\n"
                f"ID: <code>{message.from_user.id}</code>\n\n"
                f"{text}",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="💬 Ответить",
                                callback_data=(
                                    f"reply:{message.from_user.id}"
                                )
                            )
                        ]
                    ]
                )
            )
        except Exception:
            pass

    await message.answer(
        "✅ Сообщение отправлено поддержке",
        reply_markup=main_menu()
    )


# ============================================================
# CHANNEL SIGNALS / DEMO
# ============================================================

@dp.channel_post()
async def channel_post(message: Message):
    if message.chat.id != config.SIGNAL_CHANNEL_ID:
        return

    demos = await db.get_active_demos()

    if not demos:
        return

    log.info(
        "New signal %s; active demos=%s",
        message.message_id,
        len(demos)
    )

    for demo in demos:
        user = demo.get("users")

        if not user:
            continue

        telegram_id = user["telegram_id"]

        try:
            await safe_copy_message(
                bot,
                telegram_id,
                message.chat.id,
                message.message_id
            )

            updated = await db.increment_demo(
                demo["id"],
                demo["signals_received"]
            )

            count = updated["signals_received"]

            if count < 3:
                await bot.send_message(
                    telegram_id,
                    f"🎁 Демо: <b>{count}/3</b> "
                    "сигналов получено.\n\n"
                    "Следующий сигнал придёт автоматически"
                )
            else:
                await bot.send_message(
                    telegram_id,
                    "🎉 <b>Демо завершено</b>\n\n"
                    "Вы увидели 3 реальных сигнала.\n\n"
                    "Хотите получать все новые сигналы?",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="💎 Купить полный доступ",
                                    callback_data="plans"
                                )
                            ],
                            [
                                InlineKeyboardButton(
                                    text="🏠 Главное меню",
                                    callback_data="back"
                                )
                            ],
                        ]
                    )
                )

        except (
            TelegramForbiddenError,
            TelegramBadRequest
        ) as e:
            log.warning(
                "Demo delivery failed to %s: %s",
                telegram_id,
                e
            )

        except Exception as e:
            log.exception(
                "Demo delivery error: %s",
                e
            )


# ============================================================
# ADMIN ACTIONS
# ============================================================

@dp.callback_query(F.data.startswith("admin:"))
async def admin_actions(
    call: CallbackQuery,
    state: FSMContext
):
    if not is_admin(call.from_user):
        await call.answer(
            "⛔ Доступ запрещён.",
            show_alert=True
        )
        return

    await call.answer()

    action = call.data.split(":")[1]

    if action == "stats":
        s = await db.stats()

        await call.message.edit_text(
            "📊 <b>Статистика</b>\n\n"
            f"👥 Пользователи: <b>{s['users']}</b>\n"
            f"💎 Активные подписки: <b>{s['active']}</b>\n"
            f"🎁 Активное демо: <b>{s['demo']}</b>\n\n"
            f"⭐ Stars: <b>{s['stars']:.0f}</b>\n"
            f"₿ USDT: <b>{s['usdt']:.2f}</b>",
            reply_markup=admin_menu()
        )

    elif action == "users":
        users = await db.list_users()

        text = "👥 <b>Пользователи</b>\n\n"

        for u in users[:30]:
            sub = await db.get_active_subscription(
                u["id"]
            )

            status = (
                f"💎 до {sub['expires_at'][:10]}"
                if sub
                else "—"
            )

            text += (
                f"• @{u.get('username') or 'no_username'} "
                f"— <code>{u['telegram_id']}</code> "
                f"— {status}\n"
            )

        if len(users) > 30:
            text += (
                f"\nПоказаны первые 30 из {len(users)}."
            )

        await call.message.edit_text(
            text,
            reply_markup=admin_menu()
        )

    elif action == "payments":
        s = await db.stats()

        await call.message.edit_text(
            "💳 <b>Платежи</b>\n\n"
            f"⭐ Всего Stars: {s['stars']:.0f}\n"
            f"₿ Всего USDT: {s['usdt']:.2f}\n\n"
            "Полная история хранится в "
            "Supabase → payments.",
            reply_markup=admin_menu()
        )

    elif action == "support":
        threads = await db.get_open_support_threads()

        if not threads:
            text = (
                "🆘 <b>Поддержка</b>\n\n"
                "Открытых обращений нет."
            )
        else:
            text = (
                "🆘 <b>Открытые обращения</b>\n\n"
            )

            for t in threads[:20]:
                u = t.get("users") or {}

                text += (
                    f"• @{u.get('username') or 'no_username'} "
                    f"— <code>{u.get('telegram_id')}</code>\n"
                )

        await call.message.edit_text(
            text,
            reply_markup=admin_menu()
        )

    elif action == "broadcast":
        await state.set_state(
            AdminState.waiting_broadcast
        )

        await call.message.edit_text(
            "📢 <b>Рассылка</b>\n\n"
            "Отправьте текст сообщения для рассылки.\n"
            "После этого бот покажет результат.",
            reply_markup=back_menu()
        )

    elif action == "find":
        await state.set_state(
            AdminState.waiting_find
        )

        await call.message.edit_text(
            "🔎 Отправьте Telegram ID пользователя.",
            reply_markup=back_menu()
        )

    elif action == "grant":
        await state.set_state(
            AdminState.waiting_grant
        )

        await call.message.edit_text(
            "➕ Введите: "
            "<code>telegram_id plan</code>\n\n"
            "Например: <code>123456789 1m</code>",
            reply_markup=back_menu()
        )


# ============================================================
# ADMIN BROADCAST
# ============================================================

@dp.message(AdminState.waiting_broadcast)
async def admin_broadcast(
    message: Message,
    state: FSMContext
):
    if not is_admin(message.from_user):
        return

    users = await db.list_users()

    record = await db.create_broadcast(
        message.from_user.username or "",
        message.text or ""
    )

    sent = 0
    failed = 0

    for u in users:
        try:
            await bot.send_message(
                u["telegram_id"],
                message.text
            )

            sent += 1

        except Exception:
            failed += 1

        await asyncio.sleep(0.04)

    await db.finish_broadcast(
        record["id"],
        sent,
        failed
    )

    await state.clear()

    await message.answer(
        "📢 <b>Рассылка завершена</b>\n\n"
        f"✅ Отправлено: {sent}\n"
        f"❌ Ошибок: {failed}",
        reply_markup=admin_menu()
    )


# ============================================================
# ADMIN FIND USER
# ============================================================

@dp.message(AdminState.waiting_find)
async def admin_find(
    message: Message,
    state: FSMContext
):
    if not is_admin(message.from_user):
        return

    try:
        tg_id = int(
            message.text.strip()
        )

    except ValueError:
        await message.answer(
            "Введите числовой Telegram ID."
        )
        return

    user = await db.get_user(tg_id)

    await state.clear()

    if not user:
        await message.answer(
            "Пользователь не найден.",
            reply_markup=admin_menu()
        )
        return

    sub = await db.get_active_subscription(
        user["id"]
    )

    await message.answer(
        "👤 <b>Пользователь</b>\n\n"
        f"Username: @{user.get('username') or '—'}\n"
        f"ID: <code>{user['telegram_id']}</code>\n"
        f"Регистрация: {user['created_at'][:10]}\n"
        f"Подписка: "
        f"{sub['plan_code'] if sub else 'нет'}\n"
        f"До: "
        f"{sub['expires_at'][:10] if sub else '—'}",
        reply_markup=admin_menu()
    )


# ============================================================
# ADMIN GRANT
# ============================================================

@dp.message(AdminState.waiting_grant)
async def admin_grant(
    message: Message,
    state: FSMContext
):
    if not is_admin(message.from_user):
        return

    parts = message.text.split()

    if (
        len(parts) != 2
        or parts[1] not in config.PLANS
    ):
        await message.answer(
            "Формат: "
            "<code>telegram_id 1m</code>, "
            "<code>3m</code> или <code>6m</code>"
        )
        return

    try:
        tg_id = int(parts[0])
    except ValueError:
        await message.answer(
            "Telegram ID должен быть числом."
        )
        return

    code = parts[1]

    try:
        invite = await access.create_invite(
            tg_id,
            code
        )

        sub = await db.manual_subscription(
            tg_id,
            code,
            invite
        )

        if not sub:
            await message.answer(
                "Пользователь не найден."
            )
        else:
            await message.answer(
                "✅ Подписка выдана.",
                reply_markup=admin_menu()
            )

    except Exception as e:
        await message.answer(
            f"❌ Ошибка: {e}"
        )

    await state.clear()


# ============================================================
# ADMIN REPLY
# ============================================================

@dp.callback_query(F.data.startswith("reply:"))
async def admin_reply_button(
    call: CallbackQuery,
    state: FSMContext
):
    if not is_admin(call.from_user):
        await call.answer(
            "⛔",
            show_alert=True
        )
        return

    tg_id = int(
        call.data.split(":")[1]
    )

    await state.update_data(
        reply_to=tg_id
    )

    await state.set_state(
        AdminState.waiting_reply
    )

    await call.answer()

    await call.message.answer(
        f"💬 Напишите ответ пользователю "
        f"<code>{tg_id}</code>."
    )


@dp.message(AdminState.waiting_reply)
async def admin_reply(
    message: Message,
    state: FSMContext
):
    if not is_admin(message.from_user):
        return

    data = await state.get_data()

    tg_id = data["reply_to"]

    try:
        await bot.send_message(
            tg_id,
            "💬 <b>Поддержка</b>\n\n"
            f"{message.text}"
        )

        await message.answer(
            "✅ Ответ отправлен.",
            reply_markup=admin_menu()
        )

    except Exception as e:
        await message.answer(
            f"❌ Не удалось отправить: {e}",
            reply_markup=admin_menu()
        )

    await state.clear()


# ============================================================
# CRYPTO PAYMENT LOOP
# ============================================================

async def crypto_loop():
    while True:
        try:
            invoices = await crypto.get_invoices(
                status="paid"
            )

            for invoice in invoices.get(
                "items",
                []
            ):
                if invoice.get("status") != "paid":
                    continue

                row = await db.get_payment_by_crypto_id(
                    invoice["invoice_id"]
                )

                if not row:
                    continue

                if row["status"] == "paid":
                    continue

                code = row["plan_code"]

                user = await db.get_user_by_db_id(
                    row["user_id"]
                )

                if not user:
                    continue

                telegram_id = user["telegram_id"]

                await db.mark_payment_paid(
                    row["id"]
                )

                subscription, invite = await access.activate(
                    telegram_id,
                    code,
                    "cryptobot"
                )

                plan = config.PLANS[code]

                await bot.send_message(
                    telegram_id,
                    "🎉 <b>Оплата прошла успешно!</b>\n\n"
                    f"💎 Тариф: <b>{plan.title}</b>\n"
                    f"₿ Оплачено: "
                    f"<b>{plan.usdt:g} USDT</b>\n"
                    f"⏰ Доступ до: "
                    f"<b>{subscription['expires_at'][:10]}</b>",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="🔐 Войти в Gifts Intelligence",
                                    url=invite
                                )
                            ],
                            [
                                InlineKeyboardButton(
                                    text="🔍 Открыть NFT-Tracker",
                                    url=(
                                        f"https://t.me/"
                                        f"{config.TRACKER_BOT_USERNAME}"
                                    )
                                )
                            ],
                            [
                                InlineKeyboardButton(
                                    text="🏠 Главное меню",
                                    callback_data="back"
                                )
                            ],
                        ]
                    )
                )

                # ====================================================
                # REFERRAL
                # ====================================================

                referral_result = await process_referral_payment(
                    user["id"],
                    row["id"]
                )

                await notify_admin_chats(
                    "💰 <b>Новая оплата CryptoBot</b>\n\n"
                    f"👤 ID: <code>{telegram_id}</code>\n"
                    f"💎 {plan.title}\n"
                    f"₿ {plan.usdt:g} USDT\n"
                    f"⏰ до {subscription['expires_at'][:10]}"
                )

                if referral_result:
                    log.info(
                        "Crypto referral processed: %s",
                        referral_result
                    )

        except Exception:
            log.exception(
                "Crypto payment loop error"
            )

        await asyncio.sleep(
            config.CRYPTO_POLL_SECONDS
        )


# ============================================================
# EXPIRY LOOP
# ============================================================

async def expiry_loop():
    while True:
        try:
            expired = await db.list_expired_subscriptions()

            for sub in expired:
                await access.expire(sub)

                user = await db.get_user_by_db_id(
                    sub["user_id"]
                )

                if user:
                    try:
                        await bot.send_message(
                            user["telegram_id"],
                            "⏰ <b>Подписка Gifts Intelligence закончилась.</b>\n\n"
                            "Доступ к приватному каналу отключён.\n\n"
                            "Продлить подписку можно здесь:",
                            reply_markup=plans_menu()
                        )

                    except Exception:
                        pass

        except Exception:
            log.exception(
                "Expiry loop error"
            )

        await asyncio.sleep(
            config.EXPIRY_CHECK_SECONDS
        )


# ============================================================
# MAIN
# ============================================================

async def main():
    log.info(
        "Starting Gifts Intelligence"
    )

    await crypto.get_me()

    await asyncio.gather(
        dp.start_polling(
            bot,
            allowed_updates=(
                dp.resolve_used_update_types()
                + ["channel_post"]
            ),
        ),
        crypto_loop(),
        expiry_loop(),
    )


if __name__ == "__main__":
    asyncio.run(main())
