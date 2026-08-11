import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

def required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

def as_int(name: str, default: int | None = None) -> int:
    value = os.getenv(name)
    if value is None:
        if default is None:
            raise RuntimeError(f"Missing required environment variable: {name}")
        return default
    return int(value)

def as_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value is not None else default

@dataclass(frozen=True)
class Plan:
    code: str
    title: str
    months: int
    days: int
    stars: int
    usdt: float

BOT_TOKEN = required("BOT_TOKEN")
SIGNAL_CHANNEL_ID = int(required("SIGNAL_CHANNEL_ID"))
ADMIN_USERNAMES = {
    x.strip().lstrip("@").lower()
    for x in required("ADMIN_USERNAMES").split(",")
    if x.strip()
}
TRACKER_BOT_USERNAME = os.getenv("TRACKER_BOT_USERNAME", "fyvfhvfhyfbot").lstrip("@")

SUPABASE_URL = required("SUPABASE_URL")
SUPABASE_KEY = required("SUPABASE_KEY")

CRYPTOBOT_API_TOKEN = required("CRYPTOBOT_API_TOKEN")
CRYPTOBOT_API_BASE = os.getenv("CRYPTOBOT_API_BASE", "https://pay.crypt.bot").rstrip("/")

CRYPTO_POLL_SECONDS = as_int("CRYPTO_POLL_SECONDS", 15)
EXPIRY_CHECK_SECONDS = as_int("EXPIRY_CHECK_SECONDS", 300)

PLANS = {
    "1m": Plan("1m", "1 месяц", 1, 30, as_int("PLAN_1_MONTH_STARS", 300), as_float("PLAN_1_MONTH_USDT", 5)),
    "3m": Plan("3m", "3 месяца", 3, 90, as_int("PLAN_3_MONTHS_STARS", 600), as_float("PLAN_3_MONTHS_USDT", 10)),
    "6m": Plan("6m", "6 месяцев", 6, 180, as_int("PLAN_6_MONTHS_STARS", 1000), as_float("PLAN_6_MONTHS_USDT", 17)),
}
