"""
WhatsApp Webhook Endpoints.
Handles Meta's webhook verification and incoming message events.
"""

import logging
from fastapi import APIRouter, Request, Query, HTTPException, BackgroundTasks

from app.config import get_settings
from app.models.schemas import WhatsAppMessage
from app.services.conversation import get_conversation_handler

logger = logging.getLogger("hisaab.webhooks")
router = APIRouter(tags=["webhooks"])


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """
    WhatsApp webhook verification endpoint.
    Meta sends a GET request to verify our webhook URL.
    """
    settings = get_settings()

    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        logger.info("Webhook verified successfully")
        return int(hub_challenge)

    logger.warning(f"Webhook verification failed: mode={hub_mode}")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receive incoming WhatsApp messages.
    Meta sends a POST request for every message/event.
    
    We return 200 immediately and process the message in the background
    to avoid webhook timeout (Meta expects response within 5 seconds).
    """
    body = await request.json()

    # Extract message data from Meta's webhook payload
    messages = _extract_messages(body)

    for msg in messages:
        logger.info(f"Received message: from={msg.from_number}, type={msg.message_type}")
        # Process in background to respond to webhook quickly
        background_tasks.add_task(_process_message, msg)

    # Always return 200 to acknowledge receipt
    return {"status": "ok"}


async def _process_message(message: WhatsAppMessage):
    """Process a single message through the conversation handler."""
    try:
        handler = get_conversation_handler()
        await handler.handle_message(message)
    except Exception as e:
        logger.error(f"Error processing message from {message.from_number}: {e}", exc_info=True)


def _extract_messages(body: dict) -> list[WhatsAppMessage]:
    """
    Parse Meta's webhook payload into our WhatsAppMessage objects.
    
    Meta's payload structure (simplified):
    {
      "entry": [{
        "changes": [{
          "value": {
            "messages": [{
              "from": "919876543210",
              "id": "wamid.xxx",
              "timestamp": "1234567890",
              "type": "text" | "image" | "interactive",
              "text": {"body": "..."},
              "image": {"id": "media-id", "caption": "..."},
              "interactive": {"type": "button_reply", "button_reply": {"id": "...", "title": "..."}}
            }]
          }
        }]
      }]
    }
    """
    messages = []

    try:
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                
                # Skip status updates (delivery receipts, read receipts)
                if "messages" not in value:
                    continue

                for msg_data in value.get("messages", []):
                    msg_type = msg_data.get("type", "unknown")
                    
                    msg = WhatsAppMessage(
                        from_number=msg_data.get("from", ""),
                        message_id=msg_data.get("id", ""),
                        timestamp=msg_data.get("timestamp", ""),
                        message_type=msg_type,
                    )

                    # Parse based on type
                    if msg_type == "text":
                        msg.text = msg_data.get("text", {}).get("body", "")

                    elif msg_type == "image":
                        msg.image_id = msg_data.get("image", {}).get("id", "")
                        msg.caption = msg_data.get("image", {}).get("caption", "")

                    elif msg_type == "document":
                        msg.document_id = msg_data.get("document", {}).get("id", "")

                    elif msg_type == "interactive":
                        interactive = msg_data.get("interactive", {})
                        if interactive.get("type") == "button_reply":
                            msg.message_type = "button_reply"
                            msg.button_payload = interactive.get("button_reply", {}).get("id", "")

                    if msg.from_number:
                        messages.append(msg)

    except Exception as e:
        logger.error(f"Error parsing webhook body: {e}", exc_info=True)

    return messages
