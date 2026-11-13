"""Load OANDA settings from .env and refuse to run against anything but practice."""
import os

from dotenv import load_dotenv


def load_config():
    load_dotenv()
    token = os.getenv("OANDA_TOKEN")
    account_id = os.getenv("OANDA_ACCOUNT_ID")
    env = os.getenv("OANDA_ENV")

    if not token or not account_id:
        raise RuntimeError("OANDA_TOKEN and OANDA_ACCOUNT_ID must be set in .env")
    # Safety rule: this bot only ever talks to the practice (demo) server.
    if env != "practice":
        raise RuntimeError(f'OANDA_ENV must be "practice", got {env!r}')

    return {"token": token, "account_id": account_id, "env": env}
