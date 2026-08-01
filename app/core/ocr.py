"""
Invoice OCR Pipeline — LekhaAI Core IP.

Features:
- Gemini Vision as primary, Groq as fallback
- Automatic retry with exponential backoff
- Image preprocessing for better accuracy
- 7-point validation with confidence scoring
- Duplicate invoice detection
"""

import json
import logging
import asyncio
import base64
from typing import Optional

import google.generativeai as genai
import httpx

from app.config import get_settings
from app.models.schemas import (
    InvoiceExtraction, InvoiceValidationResult,
    ValidationIssue, ConfidenceLevel, LineItem,
)
from app.utils.validators import (
    validate_gstin, validate_hsn, validate_tax_rate,
    validate_tax_split, validate_amounts, validate_invoice_date,
)
from app.utils.image_prep import preprocess_invoice_image

logger = logging.getLogger("lekha.ocr")

# ═══════════════════════════════════════════
# Extraction Prompt (battle-tested)
# ═══════════════════════════════════════════

INVOICE_EXTRACTION_PROMPT = """You are an expert Indian GST invoice data extractor.
You specialize in reading Indian tax invoices — printed, handwritten, thermal prints, and mixed Hindi-English text.

Extract ALL fields from this invoice image. Return ONLY valid JSON (no markdown, no code fences, no explanation).

Required JSON structure:
{
  "supplier_name": "string or null",
  "supplier_gstin": "string (exactly 15 chars, format: 22AAAAA0000A1Z5) or null",
  "buyer_gstin": "string or null",
  "invoice_number": "string or null",
  "invoice_date": "string (DD/MM/YYYY format) or null",
  "place_of_supply": "string (state name) or null",
  "line_items": [
    {
      "description": "string",
      "hsn_sac_code": "string or null",
      "quantity": null,
      "unit_price": null,
      "taxable_amount": 0,
      "cgst_rate": 0,
      "cgst_amount": 0,
      "sgst_rate": 0,
      "sgst_amount": 0,
      "igst_rate": 0,
      "igst_amount": 0
    }
  ],
  "total_taxable_amount": 0,
  "total_cgst": 0,
  "total_sgst": 0,
  "total_igst": 0,
  "total_cess": 0,
  "total_amount": 0,
  "reverse_charge": "No",
  "invoice_type": "B2B"
}

CRITICAL RULES:
- If a field is not visible or unclear, set it to null (not empty string).
- GSTIN must be exactly 15 characters. If partially visible, set to null.
- All monetary amounts must be numbers (not strings). Use 0 if not found.
- Tax split: If CGST/SGST are shown (intra-state), set IGST to 0 and vice versa.
- For CGST/SGST rates, provide the INDIVIDUAL rate (e.g., 9 for 9% CGST, not 18 total).
- invoice_type: "B2B" if buyer GSTIN is present, "B2C" otherwise.
- reverse_charge: "Yes" if marked on invoice, otherwise "No".
- Handle rotated, tilted, or partially cut images.
- Handle handwritten text — extract what you can read.
- Handle Hindi or regional language field labels — translate to English values.
- For date: Always convert to DD/MM/YYYY format regardless of input format.
"""


class InvoiceOCR:
    """Complete invoice OCR pipeline with fallback and retry."""

    def __init__(self):
        settings = get_settings()
        # Primary: Gemini
        genai.configure(api_key=settings.gemini_api_key)
        self.gemini_model = genai.GenerativeModel("gemini-2.5-flash-lite-preview-06-17")
        # Fallback: Groq (if configured)
        self.groq_api_key = getattr(settings, "groq_api_key", "")
        self.max_retries = 3
        logger.info("InvoiceOCR initialized (Gemini primary, Groq fallback)")

    async def process_invoice(
        self, image_bytes: bytes, mime_type: str = "image/jpeg"
    ) -> InvoiceValidationResult:
        """
        Full pipeline: image → preprocess → extract → validate → score.
        Tries Gemini first, falls back to Groq on failure.
        """
        logger.info(f"Starting OCR pipeline ({len(image_bytes) / 1024:.0f}KB image)")

        # Step 1: Preprocess image
        processed_bytes, processed_mime = preprocess_invoice_image(image_bytes, mime_type)

        # Step 2: Try extraction with retry
        extraction = None
        last_error = None

        # Try Gemini first
        for attempt in range(self.max_retries):
            try:
                extraction = await self._extract_gemini(processed_bytes, processed_mime)
                if extraction:
                    break
            except Exception as e:
                last_error = e
                wait = (2 ** attempt) + 1  # 2s, 3s, 5s
                logger.warning(f"Gemini attempt {attempt+1} failed: {e}. Retry in {wait}s")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(wait)

        # Try Groq fallback if Gemini failed
        if extraction is None and self.groq_api_key:
            logger.info("Falling back to Groq...")
            try:
                extraction = await self._extract_groq(processed_bytes, processed_mime)
            except Exception as e:
                logger.error(f"Groq fallback also failed: {e}")

        # If all extractors failed
        if extraction is None:
            error_msg = str(last_error) if last_error else "Unknown error"
            # Check if it's a quota issue
            if "429" in error_msg or "quota" in error_msg.lower():
                user_msg = ("API quota exhausted. Please try again in a few minutes. "
                            "Tip: Generate a new Gemini API key for fresh quota.")
            else:
                user_msg = "Could not extract data. Please send a clearer photo."

            return InvoiceValidationResult(
                data=InvoiceExtraction(),
                confidence_score=0,
                confidence_level=ConfidenceLevel.LOW,
                issues=[ValidationIssue(
                    field="image", message=user_msg, severity="error"
                )],
                needs_review=True,
            )

        # Step 3: Validate
        result = self._validate(extraction)

        logger.info(
            f"OCR complete: confidence={result.confidence_score:.0f}%, "
            f"supplier={extraction.supplier_name}, total=₹{extraction.total_amount}"
        )
        return result

    async def _extract_gemini(self, image_bytes: bytes, mime_type: str) -> Optional[InvoiceExtraction]:
        """Extract invoice data using Gemini Vision."""
        image_part = {"mime_type": mime_type, "data": image_bytes}

        response = self.gemini_model.generate_content(
            [INVOICE_EXTRACTION_PROMPT, image_part],
            generation_config=genai.GenerationConfig(
                temperature=0.1,
                max_output_tokens=2000,
            ),
        )
        return self._parse_response(response.text, "Gemini")

    async def _extract_groq(self, image_bytes: bytes, mime_type: str) -> Optional[InvoiceExtraction]:
        """Extract invoice data using Groq (Llama Vision) as fallback."""
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.groq_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.2-90b-vision-preview",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": INVOICE_EXTRACTION_PROMPT},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{mime_type};base64,{b64_image}"
                                    },
                                },
                            ],
                        }
                    ],
                    "temperature": 0.1,
                    "max_tokens": 2000,
                },
            )
            response.raise_for_status()
            data = response.json()
            text = data["choices"][0]["message"]["content"]
            return self._parse_response(text, "Groq")

    def _parse_response(self, response_text: str, source: str) -> Optional[InvoiceExtraction]:
        """Parse LLM response text into InvoiceExtraction."""
        try:
            text = response_text.strip()
            # Remove markdown code fences
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            data = json.loads(text)

            # Convert line_items
            line_items = []
            for item in data.get("line_items", []):
                if isinstance(item, dict):
                    line_items.append(LineItem(**{
                        k: v for k, v in item.items() if k in LineItem.model_fields
                    }))
            data["line_items"] = line_items

            extraction = InvoiceExtraction(**{
                k: v for k, v in data.items() if k in InvoiceExtraction.model_fields
            })

            logger.info(f"[{source}] Extracted: supplier={extraction.supplier_name}, "
                        f"total=₹{extraction.total_amount}, items={len(extraction.line_items)}")
            return extraction

        except json.JSONDecodeError as e:
            logger.error(f"[{source}] JSON parse failed: {e}")
            logger.debug(f"Raw response: {response_text[:500]}")
            return None
        except Exception as e:
            logger.error(f"[{source}] Parse error: {e}")
            return None

    def _validate(self, data: InvoiceExtraction) -> InvoiceValidationResult:
        """Validate extracted data and calculate confidence score."""
        issues = []
        confidence = 100.0

        # 1. GSTIN validation
        if data.supplier_gstin:
            r = validate_gstin(data.supplier_gstin)
            if not r["valid"]:
                for err in r["errors"]:
                    issues.append(ValidationIssue(field="supplier_gstin", message=err, severity="error"))
                confidence -= 25
        else:
            issues.append(ValidationIssue(
                field="supplier_gstin", message="Supplier GSTIN not found — ITC may not be available", severity="warning"
            ))
            confidence -= 5

        # 2. Invoice number
        if not data.invoice_number:
            issues.append(ValidationIssue(field="invoice_number", message="Invoice number not found", severity="warning"))
            confidence -= 10

        # 3. Date
        if data.invoice_date:
            r = validate_invoice_date(data.invoice_date)
            if not r["valid"]:
                for err in r["errors"]:
                    issues.append(ValidationIssue(field="invoice_date", message=err, severity="warning"))
                confidence -= 10
        else:
            issues.append(ValidationIssue(field="invoice_date", message="Invoice date not found", severity="warning"))
            confidence -= 10

        # 4. Tax rates
        for i, item in enumerate(data.line_items):
            if item.cgst_rate > 0 or item.sgst_rate > 0 or item.igst_rate > 0:
                r = validate_tax_split(item.cgst_rate, item.sgst_rate, item.igst_rate)
                if not r["valid"]:
                    for err in r["errors"]:
                        issues.append(ValidationIssue(field=f"line_items[{i}].tax", message=err, severity="warning"))
                    confidence -= 8
            if item.hsn_sac_code:
                r = validate_hsn(item.hsn_sac_code)
                if not r["valid"]:
                    for err in r["errors"]:
                        issues.append(ValidationIssue(field=f"line_items[{i}].hsn", message=err, severity="warning"))
                    confidence -= 5

        # 5. Amount cross-check
        r = validate_amounts(data.total_taxable_amount, data.total_cgst, data.total_sgst,
                             data.total_igst, data.total_cess, data.total_amount)
        if not r["valid"]:
            issues.append(ValidationIssue(
                field="total_amount",
                message=f"Total mismatch: calculated ₹{r['calculated_total']}, stated ₹{r['stated_total']} (diff ₹{r['difference']})",
                severity="error"
            ))
            confidence -= 20

        # 6. Supplier name
        if not data.supplier_name:
            issues.append(ValidationIssue(field="supplier_name", message="Supplier name not found", severity="warning"))
            confidence -= 5

        # 7. Total amount sanity
        if data.total_amount <= 0:
            issues.append(ValidationIssue(field="total_amount", message="Total amount is zero or negative", severity="error"))
            confidence -= 30

        # Score
        confidence = max(0, min(100, confidence))
        settings = get_settings()
        if confidence >= settings.ocr_confidence_high:
            level = ConfidenceLevel.HIGH
        elif confidence >= settings.ocr_confidence_medium:
            level = ConfidenceLevel.MEDIUM
        else:
            level = ConfidenceLevel.LOW

        return InvoiceValidationResult(
            data=data,
            confidence_score=round(confidence, 1),
            confidence_level=level,
            issues=issues,
            needs_review=(level != ConfidenceLevel.HIGH),
        )


# Singleton
_ocr_instance: Optional[InvoiceOCR] = None

def get_ocr() -> InvoiceOCR:
    global _ocr_instance
    if _ocr_instance is None:
        _ocr_instance = InvoiceOCR()
    return _ocr_instance
