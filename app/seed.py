"""Create the tables and load the fictional menu. Safe to run repeatedly:
`python -m app.seed` updates existing items in place."""
import asyncio

from app.config import get_settings
from app.db import create_schema, make_engine, make_sessionmaker
from app.menu import MENU
from app.models import MenuItem


async def seed_menu(sessionmaker) -> int:
    async with sessionmaker() as s:
        for sort, (id_, cat, name, desc, price, sizes, milk, default_milk, icon) in enumerate(MENU):
            await s.merge(MenuItem(
                id=id_, category=cat, name=name, description=desc, price_cents=price,
                has_sizes=sizes, has_milk=milk, default_milk=default_milk, icon=icon,
                sort=sort, available=True,
            ))
        await s.commit()
    return len(MENU)


async def main() -> None:
    engine = make_engine(get_settings().database_url)
    await create_schema(engine)
    n = await seed_menu(make_sessionmaker(engine))
    await engine.dispose()
    print(f"Seeded {n} menu items")


if __name__ == "__main__":
    asyncio.run(main())
