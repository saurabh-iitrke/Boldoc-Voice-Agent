"""SMS delivery through Plivo (DLT registration is the documented production path).

One message, at most 160 GSM-7 characters, our own domain (no shorteners, so carriers and people can trust it).
"""
from __future__ import annotations

from typing import Optional

import httpx

GSM7 = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿"
           "abcdefghijklmnopqrstuvwxyzäöñüà")
LIMIT = 160


class SmsError(RuntimeError):
    def __init__(self, code: str, retryable: bool, message: str = ""):
        super().__init__(f"{code}: {message}")
        self.code, self.retryable = code, retryable


def compose(title: str, url: str, purpose: str = "initial") -> str:
    """'BolDoc: "Leave Application" is ready. Open: https://…/d/…' (title dropped if it isn't GSM-7)."""
    verb = "was updated" if purpose == "updated" else "is ready"
    clean = " ".join((title or "").split())
    if not clean or any(c not in GSM7 for c in clean):
        clean = ""
    for t in (clean, clean[:40].rstrip() + "...", ""):
        head = f'BolDoc: "{t}" {verb}.' if t and t != "..." else f"BolDoc: your document {verb}."
        text = f"{head} Open: {url}"
        if len(text) <= LIMIT:
            return text
    return f"BolDoc: {url}"[:LIMIT]


class PlivoSms:
    def __init__(self, auth_id: str, auth_token: str, src: str, callback_url: str,
                 client: Optional[httpx.AsyncClient] = None):
        self.auth_id, self.auth_token, self.src, self.callback_url = auth_id, auth_token, src, callback_url
        self.client = client or httpx.AsyncClient(timeout=15)

    async def send(self, dst_e164: str, text: str) -> str:
        """Returns Plivo's message UUID. Raises SmsError (retryable for 429/5xx/network)."""
        url = f"https://api.plivo.com/v1/Account/{self.auth_id}/Message/"
        body = {"src": self.src, "dst": dst_e164.lstrip("+"), "text": text, "url": self.callback_url,
                "method": "POST"}
        try:
            r = await self.client.post(url, json=body, auth=(self.auth_id, self.auth_token))
        except httpx.HTTPError as e:
            raise SmsError("network", True, type(e).__name__) from e
        if r.status_code in (200, 201, 202):
            uuids = (r.json() or {}).get("message_uuid") or []
            if uuids:
                return str(uuids[0])
            raise SmsError("no_message_uuid", False, r.text[:200])
        raise SmsError(f"http_{r.status_code}", r.status_code == 429 or r.status_code >= 500, r.text[:200])

    async def aclose(self) -> None:
        await self.client.aclose()
