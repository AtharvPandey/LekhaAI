"""
LekhaAI Test Suite.

Run: pytest tests/ -v
Run single: pytest tests/test_all.py::test_gstin_valid -v
"""

import pytest
import json
import os
import sys
from datetime import date, datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.validators import (
    validate_gstin, validate_hsn, validate_tax_rate,
    validate_tax_split, validate_amounts, validate_invoice_date,
    parse_indian_date, get_state_from_gstin, _verify_gstin_checksum,
    GSTIN_CHARS,
)
from app.models.schemas import (
    InvoiceExtraction, InvoiceValidationResult, LineItem,
    ConfidenceLevel, ConversationState, WhatsAppMessage,
)


# ═══════════════════════════════════════════
# Helper: Compute valid GSTIN for testing
# ═══════════════════════════════════════════

def _compute_check_digit(gstin_14: str) -> str:
    total = 0
    for i, char in enumerate(gstin_14):
        idx = GSTIN_CHARS.index(char)
        factor = 2 if (i + 1) % 2 == 0 else 1
        product = idx * factor
        total += (product // 36) + (product % 36)
    remainder = total % 36
    return GSTIN_CHARS[(36 - remainder) % 36]


def make_valid_gstin(state_code: str = "29", pan: str = "AABCU9603R", entity: str = "1") -> str:
    """Generate a valid GSTIN with correct checksum."""
    base = f"{state_code}{pan}{entity}Z"
    return base + _compute_check_digit(base)


# ═══════════════════════════════════════════
# GSTIN VALIDATION TESTS
# ═══════════════════════════════════════════

class TestGSTINValidation:

    def test_valid_gstin(self):
        gstin = make_valid_gstin()
        result = validate_gstin(gstin)
        assert result["valid"] is True
        assert result["state"] == "Karnataka"
        assert result["pan"] == "AABCU9603R"
        assert result["errors"] == []

    def test_valid_gstin_different_states(self):
        # Delhi
        gstin = make_valid_gstin("07")
        r = validate_gstin(gstin)
        assert r["valid"] is True
        assert r["state"] == "Delhi"

        # Maharashtra
        gstin = make_valid_gstin("27")
        r = validate_gstin(gstin)
        assert r["valid"] is True
        assert r["state"] == "Maharashtra"

        # Tamil Nadu
        gstin = make_valid_gstin("33")
        r = validate_gstin(gstin)
        assert r["valid"] is True
        assert r["state"] == "Tamil Nadu"

    def test_empty_gstin(self):
        r = validate_gstin("")
        assert r["valid"] is False
        assert "empty" in r["errors"][0].lower()

    def test_none_gstin(self):
        r = validate_gstin(None)
        assert r["valid"] is False

    def test_short_gstin(self):
        r = validate_gstin("29AABCU")
        assert r["valid"] is False
        assert "15 characters" in r["errors"][0]

    def test_long_gstin(self):
        r = validate_gstin("29AABCU9603R1ZJX")
        assert r["valid"] is False

    def test_invalid_format(self):
        r = validate_gstin("XXXXXXXXXXX1234")
        assert r["valid"] is False

    def test_invalid_state_code(self):
        r = validate_gstin("99AABCU9603R1ZJ")
        assert r["valid"] is False
        assert any("state" in e.lower() for e in r["errors"])

    def test_wrong_checksum(self):
        gstin = make_valid_gstin()
        # Change last character to invalidate checksum
        wrong = gstin[:-1] + ("A" if gstin[-1] != "A" else "B")
        r = validate_gstin(wrong)
        assert r["valid"] is False
        assert any("checksum" in e.lower() for e in r["errors"])

    def test_lowercase_gstin(self):
        gstin = make_valid_gstin().lower()
        r = validate_gstin(gstin)
        assert r["valid"] is True  # Should auto-uppercase

    def test_gstin_with_spaces(self):
        gstin = f"  {make_valid_gstin()}  "
        r = validate_gstin(gstin)
        assert r["valid"] is True  # Should strip

    def test_get_state_from_gstin(self):
        assert get_state_from_gstin("29AABCU") == "Karnataka"
        assert get_state_from_gstin("07XXXXX") == "Delhi"
        assert get_state_from_gstin("XX") is None


# ═══════════════════════════════════════════
# HSN CODE VALIDATION TESTS
# ═══════════════════════════════════════════

class TestHSNValidation:

    def test_valid_2digit_hsn(self):
        r = validate_hsn("84")
        assert r["valid"] is True
        assert r["type"] == "HSN"
        assert r["chapter"] == "Machinery"

    def test_valid_4digit_hsn(self):
        r = validate_hsn("8471")
        assert r["valid"] is True
        assert r["type"] == "HSN"

    def test_valid_6digit_hsn(self):
        r = validate_hsn("847130")
        assert r["valid"] is True

    def test_valid_8digit_hsn(self):
        r = validate_hsn("84713010")
        assert r["valid"] is True

    def test_sac_code(self):
        r = validate_hsn("998314")
        assert r["valid"] is True
        assert r["type"] == "SAC"
        assert r["chapter"] == "Services"

    def test_invalid_length(self):
        r = validate_hsn("123")  # 3 digits — invalid
        assert r["valid"] is False
        assert "2, 4, 6, or 8" in r["errors"][0]

    def test_non_numeric(self):
        r = validate_hsn("84AB")
        assert r["valid"] is False

    def test_empty_hsn(self):
        r = validate_hsn("")
        assert r["valid"] is False

    def test_known_chapters(self):
        assert validate_hsn("30")["chapter"] == "Pharmaceuticals"
        assert validate_hsn("85")["chapter"] == "Electrical equipment"
        assert validate_hsn("87")["chapter"] == "Vehicles"


# ═══════════════════════════════════════════
# TAX RATE VALIDATION TESTS
# ═══════════════════════════════════════════

class TestTaxRateValidation:

    def test_valid_rates(self):
        for rate in [0, 0.25, 3, 5, 12, 18, 28]:
            assert validate_tax_rate(rate) is True

    def test_invalid_rates(self):
        for rate in [1, 2, 7, 10, 15, 20, 25]:
            assert validate_tax_rate(rate) is False

    def test_intra_state_split(self):
        r = validate_tax_split(cgst=9, sgst=9, igst=0)
        assert r["valid"] is True
        assert r["supply_type"] == "intra"

    def test_inter_state_split(self):
        r = validate_tax_split(cgst=0, sgst=0, igst=18)
        assert r["valid"] is True
        assert r["supply_type"] == "inter"

    def test_invalid_both_present(self):
        r = validate_tax_split(cgst=9, sgst=9, igst=18)
        assert r["valid"] is False

    def test_unequal_cgst_sgst(self):
        r = validate_tax_split(cgst=9, sgst=6, igst=0)
        assert r["valid"] is False

    def test_exempt_supply(self):
        r = validate_tax_split(cgst=0, sgst=0, igst=0)
        assert r["valid"] is True
        assert r["supply_type"] == "exempt"

    def test_non_standard_rate(self):
        r = validate_tax_split(cgst=7.5, sgst=7.5, igst=0)
        assert r["valid"] is False


# ═══════════════════════════════════════════
# AMOUNT VALIDATION TESTS
# ═══════════════════════════════════════════

class TestAmountValidation:

    def test_correct_amounts(self):
        r = validate_amounts(10000, 900, 900, 0, 0, 11800)
        assert r["valid"] is True
        assert r["difference"] == 0

    def test_with_igst(self):
        r = validate_amounts(10000, 0, 0, 1800, 0, 11800)
        assert r["valid"] is True

    def test_with_cess(self):
        r = validate_amounts(10000, 900, 900, 0, 100, 11900)
        assert r["valid"] is True

    def test_within_tolerance(self):
        # ₹1 rounding difference should pass
        r = validate_amounts(10000, 900, 900, 0, 0, 11801)
        assert r["valid"] is True

    def test_beyond_tolerance(self):
        r = validate_amounts(10000, 900, 900, 0, 0, 12000)
        assert r["valid"] is False
        assert r["difference"] == 200

    def test_zero_amounts(self):
        r = validate_amounts(0, 0, 0, 0, 0, 0)
        assert r["valid"] is True


# ═══════════════════════════════════════════
# DATE PARSING TESTS
# ═══════════════════════════════════════════

class TestDateParsing:

    def test_dd_mm_yyyy_slash(self):
        d = parse_indian_date("15/07/2026")
        assert d == date(2026, 7, 15)

    def test_dd_mm_yyyy_dash(self):
        d = parse_indian_date("15-07-2026")
        assert d == date(2026, 7, 15)

    def test_dd_mm_yyyy_dot(self):
        d = parse_indian_date("15.07.2026")
        assert d == date(2026, 7, 15)

    def test_dd_mm_yy(self):
        d = parse_indian_date("15/07/26")
        assert d == date(2026, 7, 15)

    def test_iso_format(self):
        d = parse_indian_date("2026-07-15")
        assert d == date(2026, 7, 15)

    def test_dd_mon_yyyy(self):
        d = parse_indian_date("15 Jul 2026")
        assert d == date(2026, 7, 15)

    def test_dd_month_yyyy(self):
        d = parse_indian_date("15 July 2026")
        assert d == date(2026, 7, 15)

    def test_dd_mon_yyyy_dash(self):
        d = parse_indian_date("15-Jul-2026")
        assert d == date(2026, 7, 15)

    def test_invalid_date(self):
        d = parse_indian_date("not a date")
        assert d is None

    def test_empty_date(self):
        d = parse_indian_date("")
        assert d is None

    def test_none_date(self):
        d = parse_indian_date(None)
        assert d is None

    def test_date_with_spaces(self):
        d = parse_indian_date("  15/07/2026  ")
        assert d == date(2026, 7, 15)

    def test_future_date_validation(self):
        r = validate_invoice_date("01/01/2099")
        assert r["valid"] is False
        assert "future" in r["errors"][0].lower()

    def test_valid_date_validation(self):
        r = validate_invoice_date("15/07/2026")
        assert r["valid"] is True
        assert r["parsed_date"] == date(2026, 7, 15)


# ═══════════════════════════════════════════
# PYDANTIC SCHEMA TESTS
# ═══════════════════════════════════════════

class TestSchemas:

    def test_line_item_defaults(self):
        item = LineItem()
        assert item.taxable_amount == 0
        assert item.cgst_rate == 0
        assert item.hsn_sac_code is None

    def test_invoice_extraction_from_dict(self):
        data = {
            "supplier_name": "Sharma Traders",
            "supplier_gstin": "29AABCU9603R1ZJ",
            "total_amount": 11800,
            "total_taxable_amount": 10000,
            "total_cgst": 900,
            "total_sgst": 900,
        }
        inv = InvoiceExtraction(**data)
        assert inv.supplier_name == "Sharma Traders"
        assert inv.total_amount == 11800
        assert inv.total_igst == 0  # Default

    def test_invoice_extraction_with_line_items(self):
        inv = InvoiceExtraction(
            supplier_name="Test",
            total_amount=1000,
            line_items=[
                LineItem(description="Item 1", taxable_amount=500, cgst_rate=9, cgst_amount=45),
                LineItem(description="Item 2", taxable_amount=500, hsn_sac_code="8471"),
            ]
        )
        assert len(inv.line_items) == 2
        assert inv.line_items[1].hsn_sac_code == "8471"

    def test_validation_result(self):
        result = InvoiceValidationResult(
            data=InvoiceExtraction(total_amount=1000),
            confidence_score=95,
            confidence_level=ConfidenceLevel.HIGH,
        )
        assert result.needs_review is False
        assert result.confidence_score == 95

    def test_whatsapp_message(self):
        msg = WhatsAppMessage(
            from_number="919876543210",
            message_id="wamid.xxx",
            timestamp="123",
            message_type="text",
            text="Hello"
        )
        assert msg.from_number == "919876543210"
        assert msg.image_id is None


# ═══════════════════════════════════════════
# DATABASE TESTS
# ═══════════════════════════════════════════

class TestDatabase:

    @pytest.fixture(autouse=True)
    def setup_test_db(self, tmp_path):
        """Use temporary database for tests."""
        import app.db.database as db_module
        db_module.DB_PATH = tmp_path / "test_lekha.db"
        db_module.init_database()
        yield
        # Cleanup happens automatically with tmp_path

    def test_init_database(self):
        from app.db.database import get_connection
        conn = get_connection()
        # Check tables exist
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t["name"] for t in tables]
        assert "businesses" in table_names
        assert "invoices" in table_names
        assert "sessions" in table_names
        assert "audit_log" in table_names
        conn.close()

    def test_create_business(self):
        from app.db.database import create_business, get_business_by_phone
        biz_id = create_business("29AABCU9603R1ZJ", "919876543210", "Karnataka", "AABCU9603R")
        assert biz_id > 0

        biz = get_business_by_phone("919876543210")
        assert biz is not None
        assert biz["gstin"] == "29AABCU9603R1ZJ"
        assert biz["state_name"] == "Karnataka"

    def test_duplicate_business_upserts(self):
        from app.db.database import create_business, get_business_by_phone
        id1 = create_business("29AABCU9603R1ZJ", "919876543210")
        id2 = create_business("29AABCU9603R1ZJ", "919876543210")
        # Should upsert, not create duplicate
        assert id1 == id2

    def test_save_and_get_invoice(self):
        from app.db.database import create_business, save_invoice, get_monthly_stats
        biz_id = create_business("29AABCU9603R1ZJ", "919876543210")

        inv_data = {
            "supplier_name": "Test Supplier",
            "supplier_gstin": "29XXXXX1234X1ZY",
            "invoice_number": "INV001",
            "invoice_date": "15/07/2026",
            "total_taxable_amount": 10000,
            "total_cgst": 900,
            "total_sgst": 900,
            "total_amount": 11800,
            "line_items": [],
        }
        inv_id = save_invoice(biz_id, inv_data, confidence=95.0)
        assert inv_id > 0

        # Check stats
        stats = get_monthly_stats(biz_id, "202607")
        assert stats["invoice_count"] == 1
        assert stats["total_amount"] == 11800

    def test_duplicate_invoice_detection(self):
        from app.db.database import (
            create_business, save_invoice, check_duplicate_invoice
        )
        biz_id = create_business("29AABCU9603R1ZJ", "919876543210")

        inv_data = {
            "supplier_gstin": "29XXXXX1234X1ZY",
            "invoice_number": "INV001",
            "total_amount": 11800,
        }
        save_invoice(biz_id, inv_data)

        # Check duplicate
        dup = check_duplicate_invoice(biz_id, "29XXXXX1234X1ZY", "INV001", 11800)
        assert dup is not None

        # Different amount — not duplicate
        dup = check_duplicate_invoice(biz_id, "29XXXXX1234X1ZY", "INV001", 12000)
        assert dup is None

    def test_session_persistence(self):
        from app.db.database import save_session, get_session, create_business
        biz_id = create_business("29AABCU9603R1ZJ", "919876543210")
        save_session("919876543210", "ready", biz_id, "29AABCU9603R1ZJ")

        session = get_session("919876543210")
        assert session is not None
        assert session["state"] == "ready"
        assert session["gstin"] == "29AABCU9603R1ZJ"

    def test_session_update(self):
        from app.db.database import save_session, get_session, create_business
        biz_id = create_business("29AABCU9603R1ZJ", "919876543210")
        save_session("919876543210", "new")
        save_session("919876543210", "ready", biz_id, "29AABCU9603R1ZJ")

        session = get_session("919876543210")
        assert session["state"] == "ready"
        assert session["business_id"] == 1

    def test_multiple_invoices_stats(self):
        from app.db.database import create_business, save_invoice, get_monthly_stats
        biz_id = create_business("29AABCU9603R1ZJ", "919876543210")

        for i in range(5):
            save_invoice(biz_id, {
                "invoice_date": "15/07/2026",
                "total_taxable_amount": 1000 * (i + 1),
                "total_cgst": 90 * (i + 1),
                "total_sgst": 90 * (i + 1),
                "total_amount": 1180 * (i + 1),
            })

        stats = get_monthly_stats(biz_id, "202607")
        assert stats["invoice_count"] == 5
        assert stats["total_amount"] == 1180 * (1 + 2 + 3 + 4 + 5)

    def test_delete_invoice(self):
        from app.db.database import create_business, save_invoice, delete_invoice, get_monthly_stats
        biz_id = create_business("29AABCU9603R1ZJ", "919876543210")
        inv_id = save_invoice(biz_id, {"total_amount": 1000, "invoice_date": "15/07/2026"})

        assert delete_invoice(inv_id, biz_id) is True
        stats = get_monthly_stats(biz_id, "202607")
        assert stats["invoice_count"] == 0

    def test_audit_log(self):
        from app.db.database import log_action, get_connection
        log_action(1, "919876543210", "test_action", {"key": "value"})

        conn = get_connection()
        row = conn.execute("SELECT * FROM audit_log WHERE action='test_action'").fetchone()
        conn.close()
        assert row is not None
        assert json.loads(row["details"])["key"] == "value"


# ═══════════════════════════════════════════
# GST ENGINE TESTS
# ═══════════════════════════════════════════

class TestGSTEngine:

    def test_current_return_period(self):
        from app.core.gst_engine import current_return_period
        period = current_return_period()
        assert len(period) == 6
        assert period.isdigit()

    def test_format_period_display(self):
        from app.core.gst_engine import format_period_display
        assert "July" in format_period_display("202607")
        assert "2026" in format_period_display("202607")
        assert "January" in format_period_display("202601")

    def test_due_dates(self):
        from app.core.gst_engine import get_upcoming_due_dates
        dates = get_upcoming_due_dates()
        assert len(dates) > 0
        # Should have both GSTR-1 and GSTR-3B
        returns = [d["return"] for d in dates]
        assert "GSTR-1" in returns
        assert "GSTR-3B" in returns

    def test_due_dates_sorted(self):
        from app.core.gst_engine import get_upcoming_due_dates
        dates = get_upcoming_due_dates()
        for i in range(len(dates) - 1):
            assert dates[i]["due_date"] <= dates[i + 1]["due_date"]

    def test_summary_no_data(self, tmp_path):
        import app.db.database as db_module
        db_module.DB_PATH = tmp_path / "test_gst.db"
        db_module.init_database()

        from app.core.gst_engine import generate_gstr3b_summary
        summary = generate_gstr3b_summary(999, "202607")
        assert summary["has_data"] is False


# ═══════════════════════════════════════════
# INTEGRATION TESTS (no external APIs)
# ═══════════════════════════════════════════

class TestIntegration:

    def test_full_gstin_to_business_flow(self, tmp_path):
        """Simulate: GSTIN validation → business creation → invoice save → stats."""
        import app.db.database as db_module
        db_module.DB_PATH = tmp_path / "test_int.db"
        db_module.init_database()

        from app.db.database import create_business, save_invoice, get_monthly_stats

        # Step 1: Validate GSTIN
        gstin = make_valid_gstin("29")
        result = validate_gstin(gstin)
        assert result["valid"] is True

        # Step 2: Create business
        biz_id = create_business(gstin, "919876543210", result["state"], result["pan"])
        assert biz_id > 0

        # Step 3: Save invoices
        for i in range(3):
            save_invoice(biz_id, {
                "supplier_name": f"Supplier {i}",
                "invoice_date": f"1{i}/07/2026",
                "total_taxable_amount": 10000,
                "total_cgst": 900,
                "total_sgst": 900,
                "total_amount": 11800,
            })

        # Step 4: Check stats
        stats = get_monthly_stats(biz_id, "202607")
        assert stats["invoice_count"] == 3
        assert stats["total_cgst"] == 2700
        assert stats["total_sgst"] == 2700

    def test_invoice_extraction_model_roundtrip(self):
        """Test that InvoiceExtraction can serialize and deserialize."""
        inv = InvoiceExtraction(
            supplier_name="Test Corp",
            supplier_gstin="29AABCU9603R1ZJ",
            invoice_number="INV/2026/001",
            invoice_date="15/07/2026",
            total_taxable_amount=50000,
            total_cgst=4500,
            total_sgst=4500,
            total_amount=59000,
            line_items=[
                LineItem(description="Laptop", hsn_sac_code="8471",
                        taxable_amount=50000, cgst_rate=9, cgst_amount=4500,
                        sgst_rate=9, sgst_amount=4500),
            ]
        )

        # Serialize
        data = inv.model_dump()
        assert data["supplier_name"] == "Test Corp"
        assert len(data["line_items"]) == 1

        # Deserialize
        inv2 = InvoiceExtraction(**data)
        assert inv2.total_amount == 59000
        assert inv2.line_items[0].hsn_sac_code == "8471"

        # JSON roundtrip
        json_str = json.dumps(data, default=str)
        data2 = json.loads(json_str)
        inv3 = InvoiceExtraction(**data2)
        assert inv3.supplier_gstin == "29AABCU9603R1ZJ"


# ═══════════════════════════════════════════
# EDGE CASE TESTS
# ═══════════════════════════════════════════

class TestEdgeCases:

    def test_gstin_all_state_codes(self):
        """Test that all valid state codes are accepted."""
        from app.utils.validators import VALID_STATE_CODES
        for code in VALID_STATE_CODES:
            gstin = make_valid_gstin(code)
            r = validate_gstin(gstin)
            assert r["valid"] is True, f"Failed for state code {code}"

    def test_amount_very_large(self):
        r = validate_amounts(99999999, 8999999.91, 8999999.91, 0, 0, 117999998.82)
        assert r["valid"] is True

    def test_amount_very_small(self):
        r = validate_amounts(0.50, 0.045, 0.045, 0, 0, 0.59)
        assert r["valid"] is True

    def test_hsn_with_leading_zeros(self):
        r = validate_hsn("01")
        assert r["valid"] is True
        assert r["chapter"] == "Live animals"

    def test_date_boundary_fy(self):
        # April 1 — start of FY
        r = validate_invoice_date("01/04/2026")
        assert r["valid"] is True

    def test_invoice_extraction_all_nulls(self):
        """Test extraction with all null fields."""
        inv = InvoiceExtraction()
        assert inv.supplier_name is None
        assert inv.total_amount == 0
        assert inv.line_items == []
