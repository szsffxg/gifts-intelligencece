import asyncio

from datetime import datetime, timezone, timedelta

from supabase import create_client, Client


class Database:

    def __init__(self, url: str, key: str):
        self.client: Client = create_client(url, key)

    async def _run(self, fn):
        return await asyncio.to_thread(fn)

    # =========================================================
    # USERS
    # =========================================================

    async def upsert_user(self, tg_user):
        def op():
            data = {
                "telegram_id": tg_user.id,
                "username": tg_user.username,
                "first_name": tg_user.first_name,
                "last_name": tg_user.last_name,
                "last_activity": datetime.now(timezone.utc).isoformat(),
            }

            return (
                self.client
                .table("users")
                .upsert(data, on_conflict="telegram_id")
                .execute()
                .data[0]
            )

        return await self._run(op)

    async def get_user(self, telegram_id: int):
        def op():
            rows = (
                self.client
                .table("users")
                .select("*")
                .eq("telegram_id", telegram_id)
                .limit(1)
                .execute()
                .data
            )

            return rows[0] if rows else None

        return await self._run(op)

    async def get_user_by_db_id(self, user_id: str):
        def op():
            rows = (
                self.client
                .table("users")
                .select("*")
                .eq("id", user_id)
                .limit(1)
                .execute()
                .data
            )

            return rows[0] if rows else None

        return await self._run(op)

    async def list_users(self):
        def op():
            return (
                self.client
                .table("users")
                .select("*")
                .eq("is_banned", False)
                .order("created_at")
                .execute()
                .data
            )

        return await self._run(op)

    # =========================================================
    # REFERRALS
    # =========================================================

    async def create_referral(
        self,
        referrer_id: str,
        referred_id: str
    ):
        """
        Создаёт реферала.

        Один пользователь может быть приглашён
        только один раз благодаря unique(referred_id).
        """

        if referrer_id == referred_id:
            return None

        def op():
            existing = (
                self.client
                .table("referrals")
                .select("*")
                .eq("referred_id", referred_id)
                .limit(1)
                .execute()
                .data
            )

            if existing:
                return existing[0]

            result = (
                self.client
                .table("referrals")
                .insert({
                    "referrer_id": referrer_id,
                    "referred_id": referred_id,
                    "status": "registered",
                })
                .execute()
                .data
            )

            return result[0] if result else None

        return await self._run(op)

    async def get_referral_by_referred(self, referred_id: str):
        def op():
            rows = (
                self.client
                .table("referrals")
                .select("*")
                .eq("referred_id", referred_id)
                .limit(1)
                .execute()
                .data
            )

            return rows[0] if rows else None

        return await self._run(op)

    async def get_referral_stats(self, user_id: str):
        """
        Возвращает:
        paid  - сколько уникальных рефералов оплатили
        rewarded - сколько рефералов уже использовано
                    для получения наград
        progress - прогресс до следующей награды
        """

        def op():
            referrals = (
                self.client
                .table("referrals")
                .select("id")
                .eq("referrer_id", user_id)
                .eq("status", "paid")
                .execute()
                .data
            )

            rewards = (
                self.client
                .table("referral_rewards")
                .select("referrals_count")
                .eq("user_id", user_id)
                .execute()
                .data
            )

            paid = len(referrals)

            rewarded = sum(
                int(x.get("referrals_count") or 0)
                for x in rewards
            )

            progress = paid - rewarded

            return {
                "paid": paid,
                "rewarded": rewarded,
                "progress": progress,
            }

        return await self._run(op)

    async def mark_referral_paid(
        self,
        referred_user_id: str,
        payment_id: str
    ):
        """
        Помечает реферала как оплатившего.

        Возвращает реферальную запись только если
        она действительно была зарегистрирована.
        """

        def op():
            rows = (
                self.client
                .table("referrals")
                .select("*")
                .eq("referred_id", referred_user_id)
                .eq("status", "registered")
                .limit(1)
                .execute()
                .data
            )

            if not rows:
                return None

            referral = rows[0]

            updated = (
                self.client
                .table("referrals")
                .update({
                    "status": "paid",
                    "first_payment_id": payment_id,
                    "paid_at": datetime.now(timezone.utc).isoformat(),
                })
                .eq("id", referral["id"])
                .eq("status", "registered")
                .execute()
                .data
            )

            return updated[0] if updated else None

        return await self._run(op)

    async def create_referral_reward(
        self,
        user_id: str,
        referrals_count: int,
        reward_days: int = 30
    ):
        def op():
            return (
                self.client
                .table("referral_rewards")
                .insert({
                    "user_id": user_id,
                    "referrals_count": referrals_count,
                    "reward_days": reward_days,
                })
                .execute()
                .data[0]
            )

        return await self._run(op)

    # =========================================================
    # DEMO
    # =========================================================

    async def start_demo(self, user_id: str):
        def op():
            active = (
                self.client
                .table("demo_users")
                .select("*")
                .eq("user_id", user_id)
                .eq("active", True)
                .limit(1)
                .execute()
                .data
            )

            if active:
                return active[0]

            completed = (
                self.client
                .table("demo_users")
                .select("*")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
                .data
            )

            if completed:
                return None

            return (
                self.client
                .table("demo_users")
                .insert({"user_id": user_id})
                .execute()
                .data[0]
            )

        return await self._run(op)

    async def get_active_demos(self):
        def op():
            return (
                self.client
                .table("demo_users")
                .select("*, users(*)")
                .eq("active", True)
                .lt("signals_received", 3)
                .execute()
                .data
            )

        return await self._run(op)

    async def increment_demo(self, demo_id: str, count: int):
        def op():
            new_count = count + 1

            payload = {
                "signals_received": new_count
            }

            if new_count >= 3:
                payload["active"] = False
                payload["completed_at"] = (
                    datetime.now(timezone.utc).isoformat()
                )

            return (
                self.client
                .table("demo_users")
                .update(payload)
                .eq("id", demo_id)
                .execute()
                .data[0]
            )

        return await self._run(op)

    # =========================================================
    # PAYMENTS
    # =========================================================

    async def create_payment(self, data):
        return await self._run(
            lambda: self.client
            .table("payments")
            .insert(data)
            .execute()
            .data[0]
        )

    async def get_payment_by_crypto_id(self, invoice_id: int):
        def op():
            rows = (
                self.client
                .table("payments")
                .select("*")
                .eq("cryptobot_invoice_id", invoice_id)
                .limit(1)
                .execute()
                .data
            )

            return rows[0] if rows else None

        return await self._run(op)

    async def mark_payment_paid(self, payment_id: str, **fields):
        def op():
            payload = {
                "status": "paid",
                "paid_at": datetime.now(timezone.utc).isoformat(),
                **fields,
            }

            return (
                self.client
                .table("payments")
                .update(payload)
                .eq("id", payment_id)
                .execute()
                .data[0]
            )

        return await self._run(op)

    # =========================================================
    # SUBSCRIPTIONS
    # =========================================================

    async def get_active_subscription(self, user_id: str):
        def op():
            rows = (
                self.client
                .table("subscriptions")
                .select("*")
                .eq("user_id", user_id)
                .eq("status", "active")
                .order("expires_at", desc=True)
                .limit(1)
                .execute()
                .data
            )

            return rows[0] if rows else None

        return await self._run(op)

    async def create_or_extend_subscription(
        self,
        user_id: str,
        plan_code: str,
        payment_method: str,
        invite_link: str
    ):
        def op():
            now = datetime.now(timezone.utc)

            rows = (
                self.client
                .table("subscriptions")
                .select("*")
                .eq("user_id", user_id)
                .eq("status", "active")
                .order("expires_at", desc=True)
                .limit(1)
                .execute()
                .data
            )

            if rows:
                current = rows[0]

                current_expiry = datetime.fromisoformat(
                    current["expires_at"].replace("Z", "+00:00")
                )

                start = max(now, current_expiry)

            else:
                current = None
                start = now

            days = {
                "1m": 30,
                "3m": 90,
                "6m": 180,
            }[plan_code]

            expires = start + timedelta(days=days)

            if current:
                return (
                    self.client
                    .table("subscriptions")
                    .update({
                        "plan_code": plan_code,
                        "payment_method": payment_method,
                        "starts_at": start.isoformat(),
                        "expires_at": expires.isoformat(),
                        "status": "active",
                        "invite_link": invite_link,
                        "updated_at": now.isoformat(),
                    })
                    .eq("id", current["id"])
                    .execute()
                    .data[0]
                )

            return (
                self.client
                .table("subscriptions")
                .insert({
                    "user_id": user_id,
                    "plan_code": plan_code,
                    "payment_method": payment_method,
                    "starts_at": start.isoformat(),
                    "expires_at": expires.isoformat(),
                    "status": "active",
                    "invite_link": invite_link,
                })
                .execute()
                .data[0]
            )

        return await self._run(op)

    async def list_expired_subscriptions(self):
        def op():
            now = datetime.now(timezone.utc).isoformat()

            return (
                self.client
                .table("subscriptions")
                .select("*")
                .eq("status", "active")
                .lt("expires_at", now)
                .execute()
                .data
            )

        return await self._run(op)

    async def expire_subscription(self, subscription_id: str):
        return await self._run(
            lambda: self.client
            .table("subscriptions")
            .update({
                "status": "expired",
                "updated_at": datetime.now(timezone.utc).isoformat()
            })
            .eq("id", subscription_id)
            .execute()
            .data
        )

    # =========================================================
    # STATS
    # =========================================================

    async def stats(self):
        def op():
            users = (
                self.client
                .table("users")
                .select("id", count="exact")
                .execute()
                .count or 0
            )

            active = (
                self.client
                .table("subscriptions")
                .select("id", count="exact")
                .eq("status", "active")
                .execute()
                .count or 0
            )

            demo = (
                self.client
                .table("demo_users")
                .select("id", count="exact")
                .eq("active", True)
                .execute()
                .count or 0
            )

            paid = (
                self.client
                .table("payments")
                .select("amount,currency,status")
                .eq("status", "paid")
                .execute()
                .data
            )

            stars = sum(
                float(x["amount"])
                for x in paid
                if x["currency"] == "XTR"
            )

            usdt = sum(
                float(x["amount"])
                for x in paid
                if x["currency"] == "USDT"
            )

            return {
                "users": users,
                "active": active,
                "demo": demo,
                "stars": stars,
                "usdt": usdt,
            }

        return await self._run(op)

    # =========================================================
    # SUPPORT
    # =========================================================

    async def open_support(self, user_id: str):
        def op():
            rows = (
                self.client
                .table("support_threads")
                .select("*")
                .eq("user_id", user_id)
                .eq("status", "open")
                .limit(1)
                .execute()
                .data
            )

            if rows:
                return rows[0]

            return (
                self.client
                .table("support_threads")
                .insert({
                    "user_id": user_id
                })
                .execute()
                .data[0]
            )

        return await self._run(op)

    async def add_support_message(
        self,
        thread_id,
        user_id,
        direction,
        message_type,
        text,
        telegram_message_id
    ):
        return await self._run(
            lambda: self.client
            .table("support_messages")
            .insert({
                "thread_id": thread_id,
                "user_id": user_id,
                "direction": direction,
                "message_type": message_type,
                "text": text,
                "telegram_message_id": telegram_message_id,
            })
            .execute()
            .data[0]
        )

    async def get_open_support_threads(self):
        def op():
            return (
                self.client
                .table("support_threads")
                .select("*, users(*)")
                .eq("status", "open")
                .order("updated_at", desc=True)
                .execute()
                .data
            )

        return await self._run(op)

    # =========================================================
    # BROADCAST
    # =========================================================

    async def create_broadcast(self, admin_username, text):
        return await self._run(
            lambda: self.client
            .table("broadcasts")
            .insert({
                "admin_username": admin_username,
                "text": text
            })
            .execute()
            .data[0]
        )

    async def finish_broadcast(
        self,
        broadcast_id,
        sent,
        failed
    ):
        return await self._run(
            lambda: self.client
            .table("broadcasts")
            .update({
                "sent": sent,
                "failed": failed
            })
            .eq("id", broadcast_id)
            .execute()
            .data
        )

    # =========================================================
    # BAN
    # =========================================================

    async def set_banned(self, telegram_id, banned: bool):
        return await self._run(
            lambda: self.client
            .table("users")
            .update({
                "is_banned": banned
            })
            .eq("telegram_id", telegram_id)
            .execute()
            .data
        )

    # =========================================================
    # MANUAL SUBSCRIPTION
    # =========================================================

    async def manual_subscription(
        self,
        telegram_id,
        plan_code,
        invite_link
    ):
        user = await self.get_user(telegram_id)

        if not user:
            return None

        return await self.create_or_extend_subscription(
            user["id"],
            plan_code,
            "manual",
            invite_link
        )
