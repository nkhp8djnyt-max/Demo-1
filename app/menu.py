"""The fictional BrewBook menu and the option price rules.

Prices live here and in the database only. The Mini App shows them, but the
server recomputes every total from these rules, so a tampered cart can't
change what the customer is charged."""
import math

CATEGORIES = [
    ("coffee", "Coffee"),
    ("not-coffee", "Not coffee"),
    ("bakery", "Bakery"),
]

SIZES = {"S": ("Small", 0), "M": ("Medium", 50), "L": ("Large", 100)}
DEFAULT_SIZE = "M"
MILKS = {
    "whole": ("Whole milk", 0),
    "oat": ("Oat", 60),
    "almond": ("Almond", 60),
    "none": ("No milk", 0),
}

# id, category, name, description, price (cents, for size S / single), sizes, milk, default milk, icon
MENU = [
    ("ember-espresso", "coffee", "Ember Espresso", "Double shot of our house roast, notes of cocoa and dried cherry.", 280, False, False, None, "espresso"),
    ("velvet-flat-white", "coffee", "Velvet Flat White", "Ristretto with silky microfoam. Small and strong.", 390, True, True, "whole", "cup"),
    ("burnt-sugar-latte", "coffee", "Burnt Sugar Latte", "Latte with house caramel made from toasted brown sugar.", 450, True, True, "whole", "latte"),
    ("slow-drip-cold-brew", "coffee", "Slow Drip Cold Brew", "Steeped for 18 hours, served over ice. Splash of milk optional.", 420, True, True, "none", "iced"),
    ("hazel-cappuccino", "coffee", "Hazel Cappuccino", "Classic cappuccino with a whisper of roasted hazelnut.", 410, True, True, "whole", "cup"),
    ("moss-matcha-latte", "not-coffee", "Moss Matcha Latte", "Stone-ground matcha whisked with steamed milk.", 470, True, True, "oat", "latte"),
    ("spiced-chai", "not-coffee", "Spiced Chai", "Black tea simmered with cardamom, ginger and clove.", 430, True, True, "whole", "tea"),
    ("dark-cocoa", "not-coffee", "Dark Cocoa", "70% chocolate melted into steamed milk. Not too sweet.", 400, True, True, "whole", "cup"),
    ("hibiscus-cooler", "not-coffee", "Hibiscus Cooler", "Iced hibiscus and berry tea with a squeeze of lime.", 380, True, False, None, "iced"),
    ("cardamom-bun", "bakery", "Cardamom Bun", "Knotted brioche bun, rolled in cardamom sugar.", 350, False, False, None, "bun"),
    ("almond-croissant", "bakery", "Almond Croissant", "Twice-baked butter croissant with almond cream.", 390, False, False, None, "croissant"),
    ("banana-walnut-loaf", "bakery", "Banana Walnut Loaf", "Thick slice, toasted, with a little salted butter.", 320, False, False, None, "loaf"),
]


class OptionError(ValueError):
    pass


def unit_price(item, size: str | None, milk: str | None) -> tuple[int, str | None, str | None]:
    """Price in cents for one unit, plus the normalised options. Raises OptionError."""
    cents = item.price_cents
    if item.has_sizes:
        size = size or DEFAULT_SIZE
        if size not in SIZES:
            raise OptionError(f"unknown size {size!r}")
        cents += SIZES[size][1]
    elif size not in (None, ""):
        raise OptionError(f"{item.name} has no sizes")
    else:
        size = None
    if item.has_milk:
        milk = milk or item.default_milk
        if milk not in MILKS:
            raise OptionError(f"unknown milk {milk!r}")
        cents += MILKS[milk][1]
    elif milk not in (None, ""):
        raise OptionError(f"{item.name} has no milk options")
    else:
        milk = None
    return cents, size, milk


def to_stars(cents: int, rate: float) -> int:
    return max(1, math.ceil(cents * rate / 100 - 1e-9))


def money(cents: int, currency: str = "USD") -> str:
    symbol = {"USD": "$", "EUR": "€"}.get(currency)
    amount = f"{cents / 100:.2f}"
    return f"{symbol}{amount}" if symbol else f"{amount} {currency}"


def line_label(name: str, size: str | None, milk: str | None) -> str:
    parts = [name]
    if size:
        parts.append(SIZES[size][0])
    if milk and milk != "none":
        parts.append(MILKS[milk][0].lower())
    return " · ".join(parts)
