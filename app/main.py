"""One process runs everything: the FastAPI API, the Mini App static files
and the bot (long polling). Set RUN_BOT=false to run only the web part."""
import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import router as api_router
from app.bot.handlers import create_router
from app.config import Settings, get_settings
from app.cryptopay import CryptoPay
from app.db import create_schema, make_engine, make_sessionmaker
from app.orders import Notifier
from app.seed import seed_menu

log = logging.getLogger("brewbook")
WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"


def build_dispatcher(settings: Settings, sessionmaker, notifier: Notifier) -> Dispatcher:
    dp = Dispatcher(settings=settings, sessionmaker=sessionmaker, notifier=notifier)
    dp.include_router(create_router())
    return dp


async def setup_bot_ui(bot: Bot, settings: Settings) -> None:
    await bot.set_my_commands([
        BotCommand(command="start", description="Open the menu"),
        BotCommand(command="orders", description="Recent orders (owner)"),
    ])
    if settings.webapp_url:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Menu", web_app=WebAppInfo(url=settings.webapp_url)))


def create_app(settings: Settings | None = None, bot: Bot | None = None, cryptopay: CryptoPay | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        await create_schema(engine)
        sm = make_sessionmaker(engine)
        await seed_menu(sm)
        own_bot = bot is None and bool(settings.bot_token)
        app.state.bot = bot or (Bot(settings.bot_token, default=DefaultBotProperties(parse_mode="HTML")) if own_bot else None)
        app.state.sessionmaker = sm
        app.state.cryptopay = cryptopay or (
            CryptoPay(settings.cryptopay_token, settings.cryptopay_base_url) if settings.cryptopay_token else None
        )
        app.state.crypto_failed = set()
        app.state.notifier = Notifier(app.state.bot, settings)
        app.state.dispatcher = build_dispatcher(settings, sm, app.state.notifier)
        polling = None
        if own_bot and settings.run_bot:
            with suppress(Exception):
                await setup_bot_ui(app.state.bot, settings)
            polling = asyncio.create_task(app.state.dispatcher.start_polling(app.state.bot, handle_signals=False))
        log.info("BrewBook up: bot=%s crypto=%s browser_demo=%s",
                 bool(app.state.bot), bool(app.state.cryptopay), settings.browser_demo)
        yield
        if polling:
            await app.state.dispatcher.stop_polling()
            polling.cancel()
            with suppress(asyncio.CancelledError):
                await polling
        if own_bot:
            await app.state.bot.session.close()
        await engine.dispose()

    app = FastAPI(title="BrewBook (demo)", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.include_router(api_router)

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/app/")

    app.mount("/app", StaticFiles(directory=WEBAPP_DIR, html=True), name="webapp")
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
