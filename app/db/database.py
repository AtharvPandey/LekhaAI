"""
SQLite Database Layer for LekhaAI.

Uses SQLite for local persistence — zero external dependency.
Easy to migrate to Supabase/PostgreSQL later.
All operations are async-safe using aiosqlite.
"""

import sqlite3
import json
import logging
from datetime import datetime, date
from typing import Optional
from pathlib import Path

logger = logging.getLogger("lekha.db")

DB_PATH = Path("data/lekha.db")


def _ensure_db_dir():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory."""
    _ensure_db_dir()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_database():
    """Create all tables if they don't exist."""
    _ensure_db_dir()
    conn = get_connection()
    cursor = conn.cursor()

    cursor.executescript("""
    -- ═══════════════════════════════════════
    -- Businesses (shopkeepers)
    -- ═══════════════════════════════════════
    CREATE TABLE IF NOT EXISTS businesses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gstin TEXT UNIQUE NOT NULL,
        business_name TEXT DEFAULT '',
        owner_name TEXT DEFAULT '',
        phone_whatsapp TEXT NOT NULL,
        state_name TEXT DEFAULT '',
        pan TEXT DEFAULT '',
        ca_id INTEGER,
        plan_type TEXT DEFAULT 'free_trial',
        gst_filing_frequency TEXT DEFAULT 'monthly',
        composition_scheme INTEGER DEFAULT 0,
        language TEXT DEFAULT 'hinglish',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- ═══════════════════════════════════════
    -- Invoices (the core data)
    -- ═══════════════════════════════════════
    CREATE TABLE IF NOT EXISTS invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id INTEGER NOT NULL,
        invoice_type TEXT DEFAULT 'purchase',
        supplier_name TEXT,
        supplier_gstin TEXT,
        invoice_number TEXT,
        invoice_date TEXT,
        place_of_supply TEXT,
        total_taxable_amount REAL DEFAULT 0,
        total_cgst REAL DEFAULT 0,
        total_sgst REAL DEFAULT 0,
        total_igst REAL DEFAULT 0,
        total_cess REAL DEFAULT 0,
        total_amount REAL DEFAULT 0,
        reverse_charge TEXT DEFAULT 'No',
        b2b_type TEXT DEFAULT 'B2B',
        hsn_codes TEXT DEFAULT '[]',
        line_items_json TEXT DEFAULT '[]',
        ocr_confidence REAL DEFAULT 0,
        ocr_raw_json TEXT DEFAULT '{}',
        human_verified INTEGER DEFAULT 0,
        duplicate_of INTEGER,
        return_period TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (business_id) REFERENCES businesses(id)
    );

    -- ═══════════════════════════════════════
    -- Chartered Accountants
    -- ═══════════════════════════════════════
    CREATE TABLE IF NOT EXISTS chartered_accountants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        ca_number TEXT,
        phone TEXT NOT NULL,
        email TEXT,
        firm_name TEXT,
        plan_type TEXT DEFAULT 'free_trial',
        max_clients INTEGER DEFAULT 30,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- ═══════════════════════════════════════
    -- GST Monthly Summaries (cached)
    -- ═══════════════════════════════════════
    CREATE TABLE IF NOT EXISTS gst_summaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id INTEGER NOT NULL,
        return_period TEXT NOT NULL,
        total_sales REAL DEFAULT 0,
        total_purchases REAL DEFAULT 0,
        output_cgst REAL DEFAULT 0,
        output_sgst REAL DEFAULT 0,
        output_igst REAL DEFAULT 0,
        input_cgst REAL DEFAULT 0,
        input_sgst REAL DEFAULT 0,
        input_igst REAL DEFAULT 0,
        net_tax_payable REAL DEFAULT 0,
        itc_claimed REAL DEFAULT 0,
        invoice_count INTEGER DEFAULT 0,
        mismatch_count INTEGER DEFAULT 0,
        report_generated_at TIMESTAMP,
        ca_reviewed INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (business_id) REFERENCES businesses(id),
        UNIQUE(business_id, return_period)
    );

    -- ═══════════════════════════════════════
    -- Conversation Sessions
    -- ═══════════════════════════════════════
    CREATE TABLE IF NOT EXISTS sessions (
        phone_number TEXT PRIMARY KEY,
        state TEXT DEFAULT 'new',
        business_id INTEGER,
        gstin TEXT,
        pending_invoice_json TEXT,
        language TEXT DEFAULT 'hinglish',
        last_message_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (business_id) REFERENCES businesses(id)
    );

    -- ═══════════════════════════════════════
    -- Audit Log (compliance requirement)
    -- ═══════════════════════════════════════
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id INTEGER,
        phone_number TEXT,
        action TEXT NOT NULL,
        details TEXT DEFAULT '{}',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- ═══════════════════════════════════════
    -- Indexes for fast queries
    -- ═══════════════════════════════════════
    CREATE INDEX IF NOT EXISTS idx_invoices_business ON invoices(business_id);
    CREATE INDEX IF NOT EXISTS idx_invoices_period ON invoices(return_period);
    CREATE INDEX IF NOT EXISTS idx_invoices_supplier_gstin ON invoices(supplier_gstin);
    CREATE INDEX IF NOT EXISTS idx_businesses_phone ON businesses(phone_whatsapp);
    CREATE INDEX IF NOT EXISTS idx_businesses_gstin ON businesses(gstin);
    CREATE INDEX IF NOT EXISTS idx_audit_business ON audit_log(business_id);
    """)

    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")


# ═══════════════════════════════════════════
# Business Operations
# ═══════════════════════════════════════════

def create_business(gstin: str, phone: str, state_name: str = "", pan: str = "") -> int:
    """Create a new business. Returns the business ID."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            """INSERT INTO businesses (gstin, phone_whatsapp, state_name, pan)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(gstin) DO UPDATE SET
                   phone_whatsapp=excluded.phone_whatsapp,
                   updated_at=CURRENT_TIMESTAMP""",
            (gstin, phone, state_name, pan)
        )
        conn.commit()
        # Get the ID
        row = conn.execute("SELECT id FROM businesses WHERE gstin=?", (gstin,)).fetchone()
        return row["id"]
    finally:
        conn.close()


def get_business_by_phone(phone: str) -> Optional[dict]:
    """Find a business by WhatsApp phone number."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM businesses WHERE phone_whatsapp=?", (phone,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_business_by_gstin(gstin: str) -> Optional[dict]:
    """Find a business by GSTIN."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM businesses WHERE gstin=?", (gstin,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ═══════════════════════════════════════════
# Invoice Operations
# ═══════════════════════════════════════════

def save_invoice(business_id: int, invoice_data: dict, confidence: float = 0) -> int:
    """Save an invoice to the database. Returns invoice ID."""
    now = datetime.now()
    # Calculate return period (YYYYMM)
    inv_date = invoice_data.get("invoice_date", "")
    if inv_date:
        try:
            parts = inv_date.split("/")
            if len(parts) == 3:
                return_period = f"{parts[2]}{parts[1]}"
            else:
                return_period = now.strftime("%Y%m")
        except Exception:
            return_period = now.strftime("%Y%m")
    else:
        return_period = now.strftime("%Y%m")

    # Extract HSN codes
    hsn_codes = []
    for item in invoice_data.get("line_items", []):
        if isinstance(item, dict) and item.get("hsn_sac_code"):
            hsn_codes.append(item["hsn_sac_code"])

    conn = get_connection()
    try:
        cursor = conn.execute(
            """INSERT INTO invoices
               (business_id, supplier_name, supplier_gstin, invoice_number,
                invoice_date, place_of_supply, total_taxable_amount,
                total_cgst, total_sgst, total_igst, total_cess, total_amount,
                reverse_charge, b2b_type, hsn_codes, line_items_json,
                ocr_confidence, ocr_raw_json, return_period)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                business_id,
                invoice_data.get("supplier_name", ""),
                invoice_data.get("supplier_gstin", ""),
                invoice_data.get("invoice_number", ""),
                invoice_data.get("invoice_date", ""),
                invoice_data.get("place_of_supply", ""),
                invoice_data.get("total_taxable_amount", 0),
                invoice_data.get("total_cgst", 0),
                invoice_data.get("total_sgst", 0),
                invoice_data.get("total_igst", 0),
                invoice_data.get("total_cess", 0),
                invoice_data.get("total_amount", 0),
                invoice_data.get("reverse_charge", "No"),
                invoice_data.get("invoice_type", "B2B"),
                json.dumps(hsn_codes),
                json.dumps(invoice_data.get("line_items", []), default=str),
                confidence,
                json.dumps(invoice_data, default=str),
                return_period,
            )
        )
        conn.commit()
        invoice_id = cursor.lastrowid
        # Audit log
        log_action(business_id, "", "invoice_saved", {"invoice_id": invoice_id})
        return invoice_id
    finally:
        conn.close()


def check_duplicate_invoice(business_id: int, supplier_gstin: str,
                            invoice_number: str, total_amount: float) -> Optional[dict]:
    """Check if this invoice already exists (duplicate detection)."""
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT * FROM invoices
               WHERE business_id=? AND supplier_gstin=?
               AND invoice_number=? AND total_amount=?""",
            (business_id, supplier_gstin, invoice_number, total_amount)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_invoices_by_period(business_id: int, return_period: str) -> list[dict]:
    """Get all invoices for a business in a given return period."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT * FROM invoices
               WHERE business_id=? AND return_period=?
               ORDER BY created_at DESC""",
            (business_id, return_period)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_monthly_stats(business_id: int, return_period: str) -> dict:
    """Get aggregated stats for a month."""
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT
                COUNT(*) as invoice_count,
                COALESCE(SUM(total_amount), 0) as total_amount,
                COALESCE(SUM(total_taxable_amount), 0) as total_taxable,
                COALESCE(SUM(total_cgst), 0) as total_cgst,
                COALESCE(SUM(total_sgst), 0) as total_sgst,
                COALESCE(SUM(total_igst), 0) as total_igst,
                COALESCE(SUM(total_cess), 0) as total_cess
               FROM invoices
               WHERE business_id=? AND return_period=?""",
            (business_id, return_period)
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def get_all_invoices_for_business(business_id: int) -> list[dict]:
    """Get all invoices for a business."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM invoices WHERE business_id=? ORDER BY created_at DESC",
            (business_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_invoice(invoice_id: int, business_id: int) -> bool:
    """Delete an invoice. Returns True if deleted."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM invoices WHERE id=? AND business_id=?",
            (invoice_id, business_id)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# ═══════════════════════════════════════════
# Session Operations
# ═══════════════════════════════════════════

def save_session(phone: str, state: str, business_id: int = None,
                 gstin: str = None, pending_json: str = None, language: str = "hinglish"):
    """Save or update a conversation session."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO sessions (phone_number, state, business_id, gstin,
                   pending_invoice_json, language, last_message_at)
               VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(phone_number) DO UPDATE SET
                   state=excluded.state,
                   business_id=COALESCE(excluded.business_id, sessions.business_id),
                   gstin=COALESCE(excluded.gstin, sessions.gstin),
                   pending_invoice_json=excluded.pending_invoice_json,
                   language=excluded.language,
                   last_message_at=CURRENT_TIMESTAMP""",
            (phone, state, business_id, gstin, pending_json, language)
        )
        conn.commit()
    finally:
        conn.close()


def get_session(phone: str) -> Optional[dict]:
    """Get a conversation session."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM sessions WHERE phone_number=?", (phone,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ═══════════════════════════════════════════
# Audit Log
# ═══════════════════════════════════════════

def log_action(business_id: int, phone: str, action: str, details: dict = None):
    """Log an action for audit trail."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO audit_log (business_id, phone_number, action, details) VALUES (?, ?, ?, ?)",
            (business_id, phone, action, json.dumps(details or {}, default=str))
        )
        conn.commit()
    finally:
        conn.close()


# ═══════════════════════════════════════════
# Analytics Queries
# ═══════════════════════════════════════════

def get_supplier_summary(business_id: int, return_period: str) -> list[dict]:
    """Get invoice totals grouped by supplier."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT supplier_name, supplier_gstin,
                COUNT(*) as invoice_count,
                SUM(total_amount) as total_amount,
                SUM(total_cgst + total_sgst + total_igst) as total_tax
               FROM invoices
               WHERE business_id=? AND return_period=?
               GROUP BY supplier_gstin
               ORDER BY total_amount DESC""",
            (business_id, return_period)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_hsn_summary(business_id: int, return_period: str) -> list[dict]:
    """Get totals by HSN code for GSTR-1 HSN summary."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT hsn_codes, SUM(total_taxable_amount) as taxable,
                SUM(total_cgst) as cgst, SUM(total_sgst) as sgst,
                SUM(total_igst) as igst
               FROM invoices
               WHERE business_id=? AND return_period=?
               GROUP BY hsn_codes""",
            (business_id, return_period)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
