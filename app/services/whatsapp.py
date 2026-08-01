"""
WhatsApp Cloud API client.
Handles sending text messages, interactive buttons, and downloading media.
"""

import logging
from typing import Optional
import httpx

from app.config import get_settings

logger = logging.getLogger("hisaab.whatsapp")

GRAPH_API_URL = "https://graph.facebook.com/v20.0"


class WhatsAppClient:
    """Client for Meta's WhatsApp Cloud API."""

    def __init__(self):
        self.settings = get_settings()
        self.phone_id = self.settings.whatsapp_phone_number_id
        self.token = self.settings.whatsapp_access_token
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def send_text(self, to: str, message: str) -> dict:
        """Send a plain text message."""
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": message},
        }
        return await self._send(payload)

    async def send_buttons(self, to: str, body: str, buttons: list[dict]) -> dict:
        """
        Send an interactive message with reply buttons.
        Max 3 buttons, each with id and title.
        
        buttons format: [{"id": "save", "title": "Save"}, ...]
        """
        button_objects = [
            {"type": "reply", "reply": {"id": btn["id"], "title": btn["title"][:20]}}
            for btn in buttons[:3]  # Max 3 buttons
        ]

        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body},
                "action": {"buttons": button_objects},
            },
        }
        return await self._send(payload)

    async def send_document(self, to: str, document_url: str, filename: str, caption: str = "") -> dict:
        """Send a document (PDF report, etc.)."""
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "document",
            "document": {
                "link": document_url,
                "filename": filename,
                "caption": caption,
            },
        }
        return await self._send(payload)

    async def mark_as_read(self, message_id: str) -> dict:
        """Mark a message as read (blue ticks)."""
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        return await self._send(payload)

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        """
        Download media (image/document) from WhatsApp.
        
        Returns: (file_bytes, mime_type)
        """
        # Step 1: Get the media URL
        url = f"{GRAPH_API_URL}/{media_id}"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers, timeout=15)
            response.raise_for_status()
            media_info = response.json()
            media_url = media_info["url"]
            mime_type = media_info.get("mime_type", "image/jpeg")

        # Step 2: Download the actual file
        async with httpx.AsyncClient() as client:
            response = await client.get(
                media_url,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=30,
            )
            response.raise_for_status()
            file_bytes = response.content

        logger.info(f"Downloaded media {media_id}: {len(file_bytes)} bytes, {mime_type}")
        return file_bytes, mime_type

    async def _send(self, payload: dict) -> dict:
        """Send a message via the WhatsApp Cloud API."""
        url = f"{GRAPH_API_URL}/{self.phone_id}/messages"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=payload,
                headers=self.headers,
                timeout=15,
            )

            if response.status_code != 200:
                logger.error(f"WhatsApp API error: {response.status_code} - {response.text}")
                response.raise_for_status()

            result = response.json()
            logger.debug(f"Message sent: {result}")
            return result


# ── Module-level singleton ──
_wa_client: Optional[WhatsAppClient] = None


def get_whatsapp() -> WhatsAppClient:
    global _wa_client
    if _wa_client is None:
        _wa_client = WhatsAppClient()
    return _wa_client
