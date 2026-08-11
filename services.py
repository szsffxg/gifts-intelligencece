import aiohttp
from datetime import datetime, timezone
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

class CryptoPay:
    def __init__(self, token: str, base_url: str):
        self.token = token
        self.base_url = base_url

    async def request(self, method: str, data: dict | None = None):
        url = f"{self.base_url}/api/{method}"
        headers = {"Crypto-Pay-API-Token": self.token}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(url, json=data or {}) as resp:
                body = await resp.json()
                if not body.get("ok"):
                    raise RuntimeError(f"Crypto Pay error: {body}")
                return body["result"]

    async def get_me(self):
        return await self.request("getMe")

    async def create_invoice(self, amount: float, description: str, payload: str):
        return await self.request("createInvoice", {
            "asset": "USDT",
            "amount": f"{amount:.2f}",
            "description": description[:1024],
            "payload": payload,
        })

    async def get_invoices(self, invoice_ids=None, status="active"):
        data = {"status": status}
        if invoice_ids:
            data["invoice_ids"] = ",".join(str(x) for x in invoice_ids)
        return await self.request("getInvoices", data)

class AccessManager:
    def __init__(self, bot: Bot, db, channel_id: int):
        self.bot = bot
        self.db = db
        self.channel_id = channel_id

    async def create_invite(self, telegram_id: int, plan_code: str):
        link = await self.bot.create_chat_invite_link(
            chat_id=self.channel_id,
            name=f"Gifts-{telegram_id}-{plan_code}",
            member_limit=1,
        )
        return link.invite_link

    async def activate(self, telegram_id: int, plan_code: str, payment_method: str):
        user = await self.db.get_user(telegram_id)
        if not user:
            raise RuntimeError("User not found")

        old = await self.db.get_active_subscription(user["id"])
        if old and old.get("invite_link"):
            try:
                await self.bot.revoke_chat_invite_link(self.channel_id, old["invite_link"])
            except TelegramBadRequest:
                pass

        invite = await self.create_invite(telegram_id, plan_code)
        subscription = await self.db.create_or_extend_subscription(
            user["id"], plan_code, payment_method, invite
        )
        return subscription, invite

    async def expire(self, subscription):
        link = subscription.get("invite_link")
        if link:
            try:
                await self.bot.revoke_chat_invite_link(self.channel_id, link)
            except TelegramBadRequest:
                pass

        # Remove the member if present. Then unban so a later paid invite can work.
        user = await self.db.get_user_by_db_id(subscription["user_id"])
        if user:
            try:
                await self.bot.ban_chat_member(self.channel_id, user["telegram_id"])
                await self.bot.unban_chat_member(self.channel_id, user["telegram_id"], only_if_banned=True)
            except TelegramBadRequest:
                pass

        await self.db.expire_subscription(subscription["id"])

async def safe_copy_message(bot: Bot, target_chat_id: int, from_chat_id: int, message_id: int):
    return await bot.copy_message(
        chat_id=target_chat_id,
        from_chat_id=from_chat_id,
        message_id=message_id,
    )
