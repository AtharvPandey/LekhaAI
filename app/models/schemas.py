"""Pydantic models for data validation throughout the app."""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import date, datetime
from enum import Enum


# ═══════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════

class InvoiceType(str, Enum):
    PURCHASE = "purchase"
    SALE = "sale"

class B2BType(str, Enum):
    B2B = "B2B"
    B2C = "B2C"

class ConfidenceLevel(str, Enum):
    HIGH = "high"       # > 90% — auto-save
    MEDIUM = "medium"   # 70-90% — ask user to verify
    LOW = "low"         # < 70% — ask for clearer photo

class ConversationState(str, Enum):
    NEW = "new"
    AWAITING_GSTIN = "awaiting_gstin"
    GSTIN_CONFIRMED = "gstin_confirmed"
    READY = "ready"                    # Normal operating state
    AWAITING_INVOICE_CONFIRM = "awaiting_invoice_confirm"
    AWAITING_EDIT_FIELD = "awaiting_edit_field"

class MatchStatus(str, Enum):
    MATCHED = "matched"
    MISMATCHED = "mismatched"
    MISSING = "missing"       # In your records but not in 2B
    EXTRA = "extra"           # In 2B but not in your records


# ═══════════════════════════════════════════
# Invoice OCR Models
# ═══════════════════════════════════════════

class LineItem(BaseModel):
    """A single line item from an invoice."""
    description: Optional[str] = None
    hsn_sac_code: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    taxable_amount: float = 0
    cgst_rate: float = 0
    cgst_amount: float = 0
    sgst_rate: float = 0
    sgst_amount: float = 0
    igst_rate: float = 0
    igst_amount: float = 0


class InvoiceExtraction(BaseModel):
    """Structured data extracted from an invoice image by Gemini Vision."""
    supplier_name: Optional[str] = None
    supplier_gstin: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    place_of_supply: Optional[str] = None
    line_items: list[LineItem] = []
    total_taxable_amount: float = 0
    total_cgst: float = 0
    total_sgst: float = 0
    total_igst: float = 0
    total_cess: float = 0
    total_amount: float = 0
    reverse_charge: str = "No"
    invoice_type: str = "B2B"


class ValidationIssue(BaseModel):
    """A single validation issue found in extracted data."""
    field: str
    message: str
    severity: str = "warning"  # warning, error


class InvoiceValidationResult(BaseModel):
    """Result of validating extracted invoice data."""
    data: InvoiceExtraction
    confidence_score: float = 100.0
    confidence_level: ConfidenceLevel = ConfidenceLevel.HIGH
    issues: list[ValidationIssue] = []
    needs_review: bool = False


# ═══════════════════════════════════════════
# Business / User Models
# ═══════════════════════════════════════════

class BusinessProfile(BaseModel):
    """A registered business (shopkeeper's profile)."""
    id: Optional[str] = None
    gstin: str
    business_name: str
    owner_name: Optional[str] = None
    phone_whatsapp: str
    ca_id: Optional[str] = None
    plan_type: str = "free_trial"
    gst_filing_frequency: str = "monthly"  # monthly or quarterly
    composition_scheme: bool = False
    created_at: Optional[datetime] = None


class CAProfile(BaseModel):
    """A chartered accountant profile."""
    id: Optional[str] = None
    name: str
    ca_number: Optional[str] = None
    phone: str
    email: Optional[str] = None
    firm_name: Optional[str] = None
    plan_type: str = "free_trial"
    max_clients: int = 30
    created_at: Optional[datetime] = None


# ═══════════════════════════════════════════
# GST Summary Models
# ═══════════════════════════════════════════

class GSTMonthlySummary(BaseModel):
    """Monthly GST filing summary for a business."""
    business_id: str
    return_period: str  # YYYYMM format (e.g., "202607")
    total_sales: float = 0
    total_purchases: float = 0
    output_cgst: float = 0
    output_sgst: float = 0
    output_igst: float = 0
    input_cgst: float = 0
    input_sgst: float = 0
    input_igst: float = 0
    net_cgst_payable: float = 0
    net_sgst_payable: float = 0
    net_igst_payable: float = 0
    total_tax_payable: float = 0
    itc_claimed: float = 0
    itc_mismatches: int = 0
    invoice_count_sales: int = 0
    invoice_count_purchases: int = 0


# ═══════════════════════════════════════════
# WhatsApp Message Models
# ═══════════════════════════════════════════

class WhatsAppMessage(BaseModel):
    """Parsed incoming WhatsApp message."""
    from_number: str
    message_id: str
    timestamp: str
    message_type: str  # text, image, document, button_reply, interactive
    text: Optional[str] = None
    image_id: Optional[str] = None
    document_id: Optional[str] = None
    button_payload: Optional[str] = None
    caption: Optional[str] = None


class ConversationContext(BaseModel):
    """Current state of a conversation with a user."""
    phone_number: str
    state: ConversationState = ConversationState.NEW
    business_id: Optional[str] = None
    gstin: Optional[str] = None
    pending_invoice: Optional[InvoiceValidationResult] = None
    last_message_at: Optional[datetime] = None
    language: str = "hinglish"  # hinglish, hindi, english
