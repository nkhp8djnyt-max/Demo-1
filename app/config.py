"""All settings come from environment variables (or a local .env file).
Nothing secret is hard-coded; see .env.example for the full list."""
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str = ""
    # Public HTTPS URL of the Mini App, e.g. https://brewbook.up.railway.app/app/
    webapp_url: str = ""
    database_url: str = "sqlite+aiosqlite:///./brewbook.db"

    # Group chat where baristas get new orders with status buttons.
    barista_chat_id: int | None = None
    # Comma-separated Telegram user IDs allowed to use /orders.
    owner_ids: str = ""

    currency: str = "USD"
    # Telegram Stars price = ceil(USD price * rate). Set to 1 for cheap test payments.
    stars_per_usd: float = 50.0

    # Crypto Pay API (@CryptoBot / @CryptoTestnetBot). Empty token = demo payment fallback.
    cryptopay_token: str = ""
    cryptopay_network: str = "testnet"  # testnet | mainnet
    cryptopay_assets: str = "USDT,TON"

    # Shows a clearly labelled "demo payment" button when crypto is unavailable.
    demo_payments: bool = True
    # Lets the Mini App open in a normal browser as a guest (portfolio link, no Telegram).
    browser_demo: bool = False

    run_bot: bool = True
    init_data_max_age: int = 86400

    @field_validator("barista_chat_id", mode="before")
    @classmethod
    def _blank_is_none(cls, v):
        return None if v in ("", None) else v

    @property
    def owner_id_set(self) -> set[int]:
        return {int(x) for x in self.owner_ids.replace(" ", "").split(",") if x}

    @property
    def cryptopay_base_url(self) -> str:
        if self.cryptopay_network == "mainnet":
            return "https://pay.crypt.bot/api/"
        return "https://testnet-pay.crypt.bot/api/"


@lru_cache
def get_settings() -> Settings:
    return Settings()
