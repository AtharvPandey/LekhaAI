"""
Conversation State Machine — LekhaAI.

Manages the complete WhatsApp conversation:
- New user onboarding with GSTIN verification
- Invoice photo → OCR → confirm/edit/cancel
- Edit flow for correcting extracted data
- Monthly summaries and reports
- Due date reminders
- Duplicate invoice detection
- Persistent sessions via SQLite
"""

import json
import logging
from datetime import datetime
from typing import Optional

from app.models.schemas import (
    WhatsAppMessage, ConversationState,
    InvoiceValidationResult, ConfidenceLevel, InvoiceExtraction,
)
from app.services.whatsapp import get_whatsapp
from app.core.ocr import get_ocr
from app.core.gst_engine import (
    format_summary_for_whatsapp, format_due_dates_for_whatsapp,
    format_supplier_summary_for_whatsapp, current_return_period,
)
from app.utils.validators import validate_gstin
from app.db.database import (
    init_database, create_business, get_business_by_phone,
    save_invoice, check_duplicate_invoice, get_monthly_stats,
    save_session as db_save_session, get_session as db_get_session,
    log_action, get_all_invoices_for_business,
)

logger = logging.getLogger("lekha.conversation")

# In-memory pending invoices (not persisted — lost on restart, but that's fine)
_pending_invoices: dict[str, InvoiceValidationResult] = {}


class ConversationHandler:
    """Handles all incoming WhatsApp messages."""

    def __init__(self):
        self.wa = get_whatsapp()
        self.ocr = get_ocr()
        init_database()  # Ensure tables exist
        logger.info("ConversationHandler initialized with SQLite persistence")

    async def handle_message(self, message: WhatsAppMessage):
        """Main entry point — route by conversation state."""
        phone = message.from_number
        session = self._get_session(phone)
        state = session.get("state", "new")

        logger.info(f"Message from {phone}: type={message.message_type}, state={state}")

        # Mark as read (don't fail if this errors)
        try:
            await self.wa.mark_as_read(message.message_id)
        except Exception:
            pass  # Non-critical — don't crash over read receipts

        try:
            if state == "new":
                await self._handle_new_user(phone, message)
            elif state == "awaiting_gstin":
                await self._handle_gstin_input(phone, session, message)
            elif state == "gstin_confirmed":
                await self._handle_gstin_confirmation(phone, session, message)
            elif state == "ready":
                await self._handle_ready(phone, session, message)
            elif state == "awaiting_invoice_confirm":
                await self._handle_invoice_confirmation(phone, session, message)
            elif state == "awaiting_edit":
                await self._handle_edit_input(phone, session, message)
            else:
                # Unknown state — reset to ready if we have a business
                if session.get("business_id"):
                    self._save_session(phone, "ready", session.get("business_id"), session.get("gstin"))
                    await self._handle_ready(phone, session, message)
                else:
                    self._save_session(phone, "new")
                    await self._handle_new_user(phone, message)

        except Exception as e:
            logger.error(f"Error handling message from {phone}: {e}", exc_info=True)
            await self.wa.send_text(
                phone,
                "🙏 Sorry, kuch technical issue aa gaya.\n"
                "Please thodi der baad try karo."
            )

    # ═══════════════════════════════════════
    # Session Management (SQLite-backed)
    # ═══════════════════════════════════════

    def _get_session(self, phone: str) -> dict:
        session = db_get_session(phone)
        if session:
            return session
        return {"phone_number": phone, "state": "new"}

    def _save_session(self, phone: str, state: str, business_id: int = None, gstin: str = None):
        db_save_session(phone, state, business_id, gstin)

    # ═══════════════════════════════════════
    # State: NEW USER
    # ═══════════════════════════════════════

    async def _handle_new_user(self, phone: str, msg: WhatsAppMessage):
        """Welcome and ask for GSTIN."""
        # Check if user already exists (returning user)
        biz = get_business_by_phone(phone)
        if biz:
            self._save_session(phone, "ready", biz["id"], biz["gstin"])
            await self.wa.send_text(
                phone,
                f"🙏 Welcome back!\n\n"
                f"GSTIN: {biz['gstin']}\n"
                f"Invoice ki photo bhejein — main process kar dunga. 📸"
            )
            return

        welcome = (
            "🙏 Namaste! Main *LekhaAI* hoon — aapka AI GST assistant.\n\n"
            "Main aapki invoice processing aur GST compliance mein help karunga:\n\n"
            "📸 Invoice photo bhejo → data auto-extract\n"
            "📊 Monthly GST summary generate\n"
            "📅 Filing due date reminders\n"
            "🔍 GSTR-2B reconciliation\n\n"
            "Shuru karne ke liye, apna *GSTIN number* bhejein.\n"
            "_(Example: 29AABCU9603R1ZJ)_"
        )
        await self.wa.send_text(phone, welcome)
        self._save_session(phone, "awaiting_gstin")

    # ═══════════════════════════════════════
    # State: AWAITING GSTIN
    # ═══════════════════════════════════════

    async def _handle_gstin_input(self, phone: str, session: dict, msg: WhatsAppMessage):
        if msg.message_type != "text" or not msg.text:
            await self.wa.send_text(phone, "Please apna GSTIN number text mein bhejein (15 characters).")
            return

        gstin = msg.text.strip().upper()

        # Allow skip for testing
        if gstin.lower() in ("skip", "test"):
            self._save_session(phone, "ready")
            await self.wa.send_text(phone, "✅ Test mode! Invoice ki photo bhejein. 📸")
            return

        result = validate_gstin(gstin)
        if not result["valid"]:
            errors = ", ".join(result["errors"])
            await self.wa.send_text(
                phone,
                f"❌ GSTIN invalid: {errors}\n\n"
                "Please sahi GSTIN bhejein (15 characters).\n"
                "Format: 29AABCU9603R1ZJ"
            )
            return

        state_name = result["state"] or "Unknown"
        self._save_session(phone, "gstin_confirmed", gstin=gstin)

        await self.wa.send_buttons(
            phone,
            f"✅ *GSTIN Verified!*\n\n"
            f"*GSTIN:* {gstin}\n"
            f"*State:* {state_name}\n"
            f"*PAN:* {result['pan']}\n\n"
            f"Kya yeh sahi hai?",
            [
                {"id": "gstin_yes", "title": "✅ Haan, sahi hai"},
                {"id": "gstin_no", "title": "❌ Nahi, badlo"},
            ]
        )

    # ═══════════════════════════════════════
    # State: GSTIN CONFIRMED
    # ═══════════════════════════════════════

    async def _handle_gstin_confirmation(self, phone: str, session: dict, msg: WhatsAppMessage):
        payload = msg.button_payload or (msg.text.strip().lower() if msg.text else "")

        if payload in ("gstin_yes", "haan", "yes", "ha"):
            gstin = session.get("gstin", "")
            result = validate_gstin(gstin) if gstin else {"state": "", "pan": ""}

            # Create business in DB
            biz_id = create_business(
                gstin=gstin, phone=phone,
                state_name=result.get("state", ""),
                pan=result.get("pan", "")
            )
            self._save_session(phone, "ready", biz_id, gstin)
            log_action(biz_id, phone, "business_registered", {"gstin": gstin})

            await self.wa.send_text(
                phone,
                "✅ *Setup complete!*\n\n"
                "Ab aap mujhe invoice ki photo bhej sakte hain.\n"
                "Main automatically sab details extract kar dunga.\n\n"
                "📸 Invoice photo bhejein\n"
                "📊 *report* — monthly summary\n"
                "📅 *dates* — due dates\n"
                "❓ *help* — sab commands"
            )
        elif payload in ("gstin_no", "nahi", "no", "nhi"):
            self._save_session(phone, "awaiting_gstin")
            await self.wa.send_text(phone, "Koi baat nahi. Sahi GSTIN bhejein.")
        else:
            await self.wa.send_text(phone, "Please 'Haan' ya 'Nahi' mein jawab dein.")

    # ═══════════════════════════════════════
    # State: READY (main operating state)
    # ═══════════════════════════════════════

    async def _handle_ready(self, phone: str, session: dict, msg: WhatsAppMessage):
        # Image → process invoice
        if msg.message_type == "image" and msg.image_id:
            await self._process_invoice_image(phone, session, msg)
            return

        # Button replies
        if msg.message_type == "button_reply" and msg.button_payload:
            await self._handle_button(phone, session, msg.button_payload)
            return

        # Text commands
        if msg.message_type == "text" and msg.text:
            cmd = msg.text.strip().lower()

            if cmd in ("help", "madad", "commands", "menu"):
                await self._send_help(phone)
            elif cmd in ("report", "summary", "report bhejo", "gst"):
                await self._send_monthly_summary(phone, session)
            elif cmd in ("status", "kitne", "count"):
                await self._send_status(phone, session)
            elif cmd in ("dates", "due", "deadline", "due dates"):
                await self._send_due_dates(phone)
            elif cmd in ("suppliers", "supplier", "vendor"):
                await self._send_supplier_summary(phone, session)
            elif cmd in ("hi", "hello", "hey", "namaste", "hii"):
                await self.wa.send_text(
                    phone, "Namaste! 🙏 Invoice ki photo bhejein ya *help* likhein."
                )
            elif cmd in ("reset", "restart"):
                self._save_session(phone, "new")
                await self.wa.send_text(phone, "🔄 Session reset. Send 'Hi' to start again.")
            else:
                await self.wa.send_text(
                    phone,
                    "📸 Invoice ki photo bhejein — main data extract karunga.\n\n"
                    "Commands: *help* | *report* | *dates* | *status* | *suppliers*"
                )
            return

    # ═══════════════════════════════════════
    # Invoice Processing
    # ═══════════════════════════════════════

    async def _process_invoice_image(self, phone: str, session: dict, msg: WhatsAppMessage):
        """Download, preprocess, OCR, validate, and present."""
        await self.wa.send_text(phone, "📸 Invoice process ho raha hai... ⏳")

        try:
            # Download from WhatsApp
            image_bytes, mime_type = await self.wa.download_media(msg.image_id)

            # Run OCR pipeline (includes preprocessing + validation)
            result = await self.ocr.process_invoice(image_bytes, mime_type)

            # Check for duplicates
            biz_id = session.get("business_id")
            if biz_id and result.data.supplier_gstin and result.data.invoice_number:
                dup = check_duplicate_invoice(
                    biz_id, result.data.supplier_gstin,
                    result.data.invoice_number, result.data.total_amount
                )
                if dup:
                    await self.wa.send_text(
                        phone,
                        f"⚠️ *Duplicate Invoice Detected!*\n\n"
                        f"Yeh invoice pehle se save hai:\n"
                        f"Supplier: {dup.get('supplier_name', 'Unknown')}\n"
                        f"Invoice #: {dup.get('invoice_number', '')}\n"
                        f"Amount: ₹{dup.get('total_amount', 0):,.2f}\n\n"
                        f"Dubara save nahi karunga. Naya invoice bhejein."
                    )
                    return

            # Store pending result
            _pending_invoices[phone] = result
            self._save_session(phone, "awaiting_invoice_confirm",
                             session.get("business_id"), session.get("gstin"))

            # Send result
            await self._send_ocr_result(phone, result)

        except Exception as e:
            logger.error(f"Invoice processing error: {e}", exc_info=True)
            await self.wa.send_text(
                phone,
                "❌ Invoice process nahi ho paya.\n"
                "Ek clear, well-lit photo bhejein aur try karo."
            )

    async def _send_ocr_result(self, phone: str, result: InvoiceValidationResult):
        """Format and send OCR result."""
        data = result.data
        conf = result.confidence_score

        emoji = "✅" if conf >= 90 else "⚠️" if conf >= 70 else "❌"
        lines = [f"{emoji} *Invoice Data* (Confidence: {conf:.0f}%)\n"]

        if data.supplier_name:
            lines.append(f"🏢 *Supplier:* {data.supplier_name}")
        if data.supplier_gstin:
            lines.append(f"🔢 *GSTIN:* {data.supplier_gstin}")
        if data.invoice_number:
            lines.append(f"📝 *Invoice #:* {data.invoice_number}")
        if data.invoice_date:
            lines.append(f"📅 *Date:* {data.invoice_date}")

        lines.append("")
        if data.total_taxable_amount > 0:
            lines.append(f"💰 *Taxable:* ₹{data.total_taxable_amount:,.2f}")
        if data.total_cgst > 0:
            lines.append(f"   CGST: ₹{data.total_cgst:,.2f}")
        if data.total_sgst > 0:
            lines.append(f"   SGST: ₹{data.total_sgst:,.2f}")
        if data.total_igst > 0:
            lines.append(f"   IGST: ₹{data.total_igst:,.2f}")
        lines.append(f"💵 *Total: ₹{data.total_amount:,.2f}*")

        # HSN codes
        hsn_codes = [item.hsn_sac_code for item in data.line_items if item.hsn_sac_code]
        if hsn_codes:
            lines.append(f"\n📦 *HSN:* {', '.join(set(hsn_codes))}")

        # Issues
        errors = [i for i in result.issues if i.severity == "error"]
        warnings = [i for i in result.issues if i.severity == "warning"]
        if errors:
            lines.append(f"\n🔴 *Errors ({len(errors)}):*")
            for issue in errors[:3]:
                lines.append(f"  • {issue.message}")
        if warnings:
            lines.append(f"\n🟡 *Warnings ({len(warnings)}):*")
            for issue in warnings[:2]:
                lines.append(f"  • {issue.message}")

        body = "\n".join(lines)

        if result.confidence_level == ConfidenceLevel.LOW:
            await self.wa.send_text(
                phone,
                body + "\n\n❌ Confidence bahut kam hai.\n"
                "Clear photo bhejein ya manually details type karo."
            )
            self._save_session(phone, "ready",
                             self._get_session(phone).get("business_id"),
                             self._get_session(phone).get("gstin"))
        else:
            await self.wa.send_buttons(phone, body, [
                {"id": "inv_save", "title": "✅ Save"},
                {"id": "inv_edit", "title": "✏️ Edit"},
                {"id": "inv_cancel", "title": "❌ Cancel"},
            ])

    # ═══════════════════════════════════════
    # State: INVOICE CONFIRMATION
    # ═══════════════════════════════════════

    async def _handle_invoice_confirmation(self, phone: str, session: dict, msg: WhatsAppMessage):
        payload = msg.button_payload or (msg.text.strip().lower() if msg.text else "")

        if payload in ("inv_save", "save", "yes", "haan"):
            await self._save_confirmed_invoice(phone, session)

        elif payload in ("inv_cancel", "cancel", "no", "nahi"):
            _pending_invoices.pop(phone, None)
            self._save_session(phone, "ready", session.get("business_id"), session.get("gstin"))
            await self.wa.send_text(phone, "❌ Invoice cancel. Naya invoice bhejein! 📸")

        elif payload in ("inv_edit", "edit"):
            self._save_session(phone, "awaiting_edit", session.get("business_id"), session.get("gstin"))
            await self.wa.send_text(
                phone,
                "✏️ *Edit Mode*\n\n"
                "Kaunsa field edit karna hai? Aise likhein:\n\n"
                "• *amount 45000*\n"
                "• *gstin 29AABCU9603R1ZJ*\n"
                "• *date 15/07/2026*\n"
                "• *supplier Sharma Traders*\n"
                "• *invoice ST/2026/001*\n\n"
                "Ya *done* likhein edit khatam karne ke liye.\n"
                "Ya *cancel* likhein invoice cancel karne ke liye."
            )

        elif msg.message_type == "image" and msg.image_id:
            # New image — process instead
            self._save_session(phone, "ready", session.get("business_id"), session.get("gstin"))
            await self._process_invoice_image(phone, session, msg)

        else:
            await self.wa.send_text(phone, "Please *Save*, *Edit*, ya *Cancel* mein se choose karo.")

    # ═══════════════════════════════════════
    # State: EDIT MODE
    # ═══════════════════════════════════════

    async def _handle_edit_input(self, phone: str, session: dict, msg: WhatsAppMessage):
        """Handle field edits on pending invoice."""
        if msg.message_type != "text" or not msg.text:
            await self.wa.send_text(phone, "Please text mein edit bhejein. Example: *amount 45000*")
            return

        text = msg.text.strip()

        if text.lower() in ("done", "save", "ok"):
            await self._save_confirmed_invoice(phone, session)
            return

        if text.lower() == "cancel":
            _pending_invoices.pop(phone, None)
            self._save_session(phone, "ready", session.get("business_id"), session.get("gstin"))
            await self.wa.send_text(phone, "❌ Invoice cancel. Naya invoice bhejein! 📸")
            return

        # Parse edit command
        pending = _pending_invoices.get(phone)
        if not pending:
            self._save_session(phone, "ready", session.get("business_id"), session.get("gstin"))
            await self.wa.send_text(phone, "⚠️ Koi pending invoice nahi hai. Naya photo bhejein.")
            return

        parts = text.split(" ", 1)
        if len(parts) < 2:
            await self.wa.send_text(phone, "Format: *field_name value*\nExample: *amount 45000*")
            return

        field = parts[0].lower()
        value = parts[1].strip()
        data = pending.data

        # Apply edit
        edited = False
        if field in ("amount", "total", "total_amount"):
            try:
                data.total_amount = float(value.replace(",", "").replace("₹", ""))
                edited = True
            except ValueError:
                await self.wa.send_text(phone, "❌ Invalid amount. Sirf number likhein.")
                return

        elif field in ("taxable", "taxable_amount"):
            try:
                data.total_taxable_amount = float(value.replace(",", "").replace("₹", ""))
                edited = True
            except ValueError:
                await self.wa.send_text(phone, "❌ Invalid amount.")
                return

        elif field in ("cgst",):
            try:
                data.total_cgst = float(value.replace(",", "").replace("₹", ""))
                edited = True
            except ValueError:
                await self.wa.send_text(phone, "❌ Invalid amount.")
                return

        elif field in ("sgst",):
            try:
                data.total_sgst = float(value.replace(",", "").replace("₹", ""))
                edited = True
            except ValueError:
                await self.wa.send_text(phone, "❌ Invalid amount.")
                return

        elif field in ("igst",):
            try:
                data.total_igst = float(value.replace(",", "").replace("₹", ""))
                edited = True
            except ValueError:
                await self.wa.send_text(phone, "❌ Invalid amount.")
                return

        elif field in ("gstin", "supplier_gstin"):
            data.supplier_gstin = value.upper()
            edited = True

        elif field in ("supplier", "supplier_name", "name"):
            data.supplier_name = value
            edited = True

        elif field in ("date", "invoice_date"):
            data.invoice_date = value
            edited = True

        elif field in ("invoice", "invoice_number", "inv", "number"):
            data.invoice_number = value
            edited = True

        elif field in ("hsn",):
            if data.line_items:
                data.line_items[0].hsn_sac_code = value
            edited = True

        else:
            await self.wa.send_text(
                phone,
                f"❌ Unknown field: *{field}*\n\n"
                "Valid fields: amount, taxable, cgst, sgst, igst, gstin, supplier, date, invoice, hsn"
            )
            return

        if edited:
            _pending_invoices[phone] = pending
            await self.wa.send_text(
                phone,
                f"✅ *{field}* updated to *{value}*\n\n"
                "Aur edit? Ya *done* likhein save karne ke liye."
            )

    # ═══════════════════════════════════════
    # Save Invoice
    # ═══════════════════════════════════════

    async def _save_confirmed_invoice(self, phone: str, session: dict):
        """Save confirmed invoice to database."""
        pending = _pending_invoices.pop(phone, None)
        if not pending:
            self._save_session(phone, "ready", session.get("business_id"), session.get("gstin"))
            await self.wa.send_text(phone, "⚠️ Koi pending invoice nahi hai.")
            return

        biz_id = session.get("business_id")
        if not biz_id:
            # Save without business association (test mode)
            self._save_session(phone, "ready")
            await self.wa.send_text(phone, "✅ Invoice noted! (Test mode — no DB save)")
            return

        # Save to database
        invoice_data = pending.data.model_dump()
        invoice_id = save_invoice(biz_id, invoice_data, pending.confidence_score)

        # Get updated stats
        period = current_return_period()
        stats = get_monthly_stats(biz_id, period)

        self._save_session(phone, "ready", biz_id, session.get("gstin"))

        await self.wa.send_text(
            phone,
            f"✅ *Invoice #{invoice_id} saved!*\n\n"
            f"📊 *This month so far:*\n"
            f"📥 Invoices: {stats.get('invoice_count', 0)}\n"
            f"💰 Total: ₹{stats.get('total_amount', 0):,.2f}\n"
            f"📋 ITC: ₹{stats.get('total_cgst', 0) + stats.get('total_sgst', 0) + stats.get('total_igst', 0):,.2f}\n\n"
            f"Agle invoice ki photo bhejein! 📸"
        )

    # ═══════════════════════════════════════
    # Button Handler
    # ═══════════════════════════════════════

    async def _handle_button(self, phone: str, session: dict, payload: str):
        """Handle button reply payloads."""
        if payload in ("gstin_yes", "gstin_no"):
            await self._handle_gstin_confirmation(phone, session,
                WhatsAppMessage(from_number=phone, message_id="", timestamp="",
                                message_type="button_reply", button_payload=payload))
        elif payload.startswith("inv_"):
            await self._handle_invoice_confirmation(phone, session,
                WhatsAppMessage(from_number=phone, message_id="", timestamp="",
                                message_type="button_reply", button_payload=payload))
        else:
            await self.wa.send_text(phone, "Command not recognized. Type *help* for options.")

    # ═══════════════════════════════════════
    # Command Handlers
    # ═══════════════════════════════════════

    async def _send_help(self, phone: str):
        await self.wa.send_text(
            phone,
            "📋 *LekhaAI Commands:*\n\n"
            "📸 *Invoice photo* → auto data extraction\n"
            "📊 *report* → monthly GST summary\n"
            "📅 *dates* → upcoming due dates\n"
            "📈 *status* → invoice count & totals\n"
            "🏢 *suppliers* → supplier-wise breakdown\n"
            "🔄 *reset* → restart from beginning\n"
            "❓ *help* → yeh message\n\n"
            "Bas invoice ki photo bhejein — baaki sab automatic! 🚀"
        )

    async def _send_monthly_summary(self, phone: str, session: dict):
        biz_id = session.get("business_id")
        if not biz_id:
            await self.wa.send_text(phone, "⚠️ Pehle GSTIN register karo. 'Hi' bhejein.")
            return
        summary = format_summary_for_whatsapp(biz_id)
        await self.wa.send_text(phone, summary)

    async def _send_status(self, phone: str, session: dict):
        biz_id = session.get("business_id")
        period = current_return_period()
        if biz_id:
            stats = get_monthly_stats(biz_id, period)
            count = stats.get("invoice_count", 0)
            total = stats.get("total_amount", 0)
        else:
            count, total = 0, 0

        await self.wa.send_text(
            phone,
            f"📈 *Status:*\n\n"
            f"GSTIN: {session.get('gstin', 'Not set')}\n"
            f"Invoices this month: {count}\n"
            f"Total value: ₹{total:,.2f}"
        )

    async def _send_due_dates(self, phone: str):
        await self.wa.send_text(phone, format_due_dates_for_whatsapp())

    async def _send_supplier_summary(self, phone: str, session: dict):
        biz_id = session.get("business_id")
        if not biz_id:
            await self.wa.send_text(phone, "⚠️ Pehle GSTIN register karo.")
            return
        summary = format_supplier_summary_for_whatsapp(biz_id)
        await self.wa.send_text(phone, summary)


# Singleton
_handler: Optional[ConversationHandler] = None

def get_conversation_handler() -> ConversationHandler:
    global _handler
    if _handler is None:
        _handler = ConversationHandler()
    return _handler
