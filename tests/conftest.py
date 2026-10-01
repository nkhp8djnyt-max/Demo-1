import pytest
import httpx
from aiogram.types import Update

from app.auth import make_test_init_data
from app.config import Settings
from app.cryptopay import CryptoPay
from app.main import create_app
from devtools.fakes import BOT_TOKEN, FakeCryptoPay, make_bot

CUSTOMER = 4242
OTHER = 5151
OWNER = 1001
BARISTAS = -100555
CP_TOKEN = "12345:TESTNET-cryptopay-token"


def make_settings(tmp_path, **kw) -> Settings:
    base = dict(
        bot_token=BOT_TOKEN, database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        webapp_url="https://brewbook.example/app/", barista_chat_id=BARISTAS, owner_ids=str(OWNER),
        stars_per_usd=50, cryptopay_token=CP_TOKEN, demo_payments=True, browser_demo=False, run_bot=False,
    )
    base.update(kw)
    return Settings(_env_file=None, **base)


def auth(user_id: int, name: str = "Mia") -> dict:
    return {"Authorization": "tma " + make_test_init_data(BOT_TOKEN, user_id, name)}


class Env:
    """A running app with a fake Telegram and a fake Crypto Pay behind it."""

    def __init__(self, tmp_path, crypto=True, **kw):
        self.settings = make_settings(tmp_path, **({} if crypto else {"cryptopay_token": ""}) | kw)
        self.bot, self.tg = make_bot()
        self.cp = FakeCryptoPay(CP_TOKEN)
        client = CryptoPay(CP_TOKEN, self.settings.cryptopay_base_url, transport=self.cp.transport) if crypto else None
        self.app = create_app(self.settings, bot=self.bot, cryptopay=client)
        self._update_id = 0

    async def __aenter__(self):
        self._lifespan = self.app.router.lifespan_context(self.app)
        await self._lifespan.__aenter__()
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        return self

    async def __aexit__(self, *exc):
        await self.http.aclose()
        await self._lifespan.__aexit__(*exc)

    async def feed(self, **update) -> None:
        self._update_id += 1
        await self.app.state.dispatcher.feed_update(self.bot, Update(update_id=self._update_id, **update))

    async def order(self, user=CUSTOMER, lines=None, comment="") -> dict:
        lines = lines or [{"item_id": "burnt-sugar-latte", "qty": 2, "size": "L", "milk": "oat"},
                          {"item_id": "cardamom-bun", "qty": 1}]
        r = await self.http.post("/api/orders", json={"lines": lines, "comment": comment}, headers=auth(user))
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
async def env(tmp_path):
    async with Env(tmp_path) as e:
        yield e


@pytest.fixture
async def env_nocrypto(tmp_path):
    async with Env(tmp_path, crypto=False) as e:
        yield e
