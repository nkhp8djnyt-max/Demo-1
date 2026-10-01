"""Minimal Crypto Pay API client (https://help.crypt.bot/crypto-pay-api).

Testnet: invoices are paid with test coins in @CryptoTestnetBot, base URL
https://testnet-pay.crypt.bot/api/. Every call is a plain HTTPS request with
the app token in the Crypto-Pay-API-Token header."""
import hashlib
import hmac
import logging

import httpx

log = logging.getLogger(__name__)


class CryptoPayError(Exception):
    pass


class CryptoPay:
    def __init__(self, token: str, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.token = token
        self.base_url = base_url
        self._transport = transport

    async def _call(self, method: str, **params) -> dict | list:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=10, transport=self._transport,
                headers={"Crypto-Pay-API-Token": self.token},
            ) as client:
                r = await client.post(method, json={k: v for k, v in params.items() if v is not None})
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise CryptoPayError(f"{method}: {e.__class__.__name__}") from e
        if not data.get("ok"):
            raise CryptoPayError(f"{method}: {data.get('error')}")
        return data["result"]

    async def create_invoice(
        self, *, amount_cents: int, fiat: str, assets: str, description: str, payload: str
    ) -> dict:
        return await self._call(
            "createInvoice",
            currency_type="fiat", fiat=fiat, amount=f"{amount_cents / 100:.2f}",
            accepted_assets=assets, description=description[:1024], payload=payload,
            expires_in=3600, allow_comments=False, allow_anonymous=True,
        )

    async def get_invoice(self, invoice_id: int) -> dict | None:
        result = await self._call("getInvoices", invoice_ids=str(invoice_id))
        items = result.get("items", []) if isinstance(result, dict) else []
        return items[0] if items else None


def verify_webhook(token: str, body: bytes, signature: str | None) -> bool:
    """crypto-pay-api-signature = hex(HMAC_SHA256(key=SHA256(app token), body))."""
    if not token or not signature:
        return False
    secret = hashlib.sha256(token.encode()).digest()
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
