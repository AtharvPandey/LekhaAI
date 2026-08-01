"""
Validators for Indian GST data.
GSTIN checksum, HSN codes, tax rates, amount cross-checks.
"""

import re
from datetime import datetime, date
from typing import Optional


# ═══════════════════════════════════════════
# GSTIN Validation
# ═══════════════════════════════════════════

# State codes: 01-37 + special codes
VALID_STATE_CODES = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana",
    "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh",
    "13": "Nagaland", "14": "Manipur", "15": "Mizoram",
    "16": "Tripura", "17": "Meghalaya", "18": "Assam",
    "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra & Nagar Haveli and Daman & Diu",
    "27": "Maharashtra", "29": "Karnataka", "30": "Goa",
    "31": "Lakshadweep", "32": "Kerala",
    "33": "Tamil Nadu", "34": "Puducherry", "35": "Andaman & Nicobar",
    "36": "Telangana", "37": "Andhra Pradesh",
    "38": "Ladakh",
    "96": "Foreign Country", "97": "Other Territory",
}

# Characters used in GSTIN checksum calculation
GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def validate_gstin(gstin: str) -> dict:
    """
    Validate a GSTIN (15-character Indian GST number).
    
    Format: SSPPPPPPPPPPEZC
    - SS: State code (01-37)
    - PPPPPPPPPP: PAN (10 chars)
    - E: Entity number (1-9, A-Z)
    - Z: Default 'Z'
    - C: Checksum character
    
    Returns: {"valid": bool, "state": str|None, "pan": str|None, "errors": list}
    """
    result = {"valid": False, "state": None, "pan": None, "errors": []}
    
    if not gstin:
        result["errors"].append("GSTIN is empty")
        return result
    
    gstin = gstin.strip().upper()
    
    # Length check
    if len(gstin) != 15:
        result["errors"].append(f"GSTIN must be 15 characters, got {len(gstin)}")
        return result
    
    # Format check: 2 digits + 10 alphanumeric (PAN) + 1 alphanumeric + Z + 1 alphanumeric
    pattern = r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$'
    if not re.match(pattern, gstin):
        result["errors"].append("GSTIN format is invalid")
        return result
    
    # State code validation
    state_code = gstin[:2]
    if state_code not in VALID_STATE_CODES:
        result["errors"].append(f"Invalid state code: {state_code}")
        return result
    result["state"] = VALID_STATE_CODES[state_code]
    
    # Extract PAN
    result["pan"] = gstin[2:12]
    
    # Checksum validation (Luhn mod 36 variant)
    try:
        checksum_valid = _verify_gstin_checksum(gstin)
        if not checksum_valid:
            result["errors"].append("GSTIN checksum failed")
            return result
    except Exception:
        result["errors"].append("Could not verify checksum")
        return result
    
    result["valid"] = True
    return result


def _verify_gstin_checksum(gstin: str) -> bool:
    """Verify GSTIN using the standard checksum algorithm."""
    total = 0
    for i, char in enumerate(gstin[:14]):
        idx = GSTIN_CHARS.index(char)
        # Multiply by factor based on position (odd=2, even=1 — 1-indexed)
        factor = 2 if (i + 1) % 2 == 0 else 1
        product = idx * factor
        total += (product // 36) + (product % 36)
    
    remainder = total % 36
    check_char = GSTIN_CHARS[(36 - remainder) % 36]
    return check_char == gstin[14]


def get_state_from_gstin(gstin: str) -> Optional[str]:
    """Extract state name from GSTIN."""
    if len(gstin) >= 2:
        return VALID_STATE_CODES.get(gstin[:2])
    return None


# ═══════════════════════════════════════════
# HSN Code Validation
# ═══════════════════════════════════════════

# Common HSN chapters (2-digit) for quick validation
HSN_CHAPTERS = {
    "01": "Live animals", "02": "Meat", "03": "Fish", "04": "Dairy",
    "07": "Vegetables", "08": "Fruits", "09": "Coffee, Tea, Spices",
    "10": "Cereals", "11": "Milling products", "15": "Fats & oils",
    "17": "Sugar", "19": "Bakery", "20": "Vegetables/Fruit preparations",
    "22": "Beverages", "24": "Tobacco", "25": "Salt, Cement",
    "27": "Mineral fuels, Petroleum", "28": "Chemicals",
    "30": "Pharmaceuticals", "32": "Dyes, Paints",
    "33": "Cosmetics", "34": "Soaps", "38": "Chemical products",
    "39": "Plastics", "40": "Rubber", "44": "Wood",
    "48": "Paper", "49": "Printed books", "52": "Cotton",
    "54": "Synthetic fibres", "61": "Knitted clothing",
    "62": "Woven clothing", "63": "Textile articles",
    "64": "Footwear", "68": "Stone, Cement products",
    "69": "Ceramic products", "70": "Glass",
    "72": "Iron & Steel", "73": "Iron/Steel articles",
    "74": "Copper", "76": "Aluminium",
    "82": "Tools", "83": "Metal articles",
    "84": "Machinery", "85": "Electrical equipment",
    "87": "Vehicles", "90": "Instruments",
    "94": "Furniture", "95": "Toys, Games", "96": "Miscellaneous",
}

# SAC code prefixes for services
SAC_PREFIXES = {"99"}


def validate_hsn(code: str) -> dict:
    """
    Validate an HSN/SAC code.
    HSN: 2, 4, 6, or 8 digits for goods.
    SAC: 6 digits starting with 99 for services.
    
    Returns: {"valid": bool, "type": "HSN"|"SAC", "chapter": str|None, "errors": list}
    """
    result = {"valid": False, "type": None, "chapter": None, "errors": []}
    
    if not code:
        result["errors"].append("HSN/SAC code is empty")
        return result
    
    code = code.strip()
    
    # Must be digits only
    if not code.isdigit():
        result["errors"].append("HSN/SAC code must contain only digits")
        return result
    
    # Check length (2, 4, 6, or 8 for HSN; typically 6 for SAC)
    if len(code) not in {2, 4, 6, 8}:
        result["errors"].append(f"HSN/SAC code must be 2, 4, 6, or 8 digits, got {len(code)}")
        return result
    
    # Check if SAC (services) — starts with 99
    if code[:2] == "99":
        result["type"] = "SAC"
        result["chapter"] = "Services"
        result["valid"] = True
        return result
    
    # Check HSN chapter
    chapter = code[:2]
    if chapter in HSN_CHAPTERS:
        result["chapter"] = HSN_CHAPTERS[chapter]
    
    result["type"] = "HSN"
    result["valid"] = True
    return result


# ═══════════════════════════════════════════
# Tax Rate Validation
# ═══════════════════════════════════════════

# Valid GST rates (as percentages)
VALID_GST_RATES = {0, 0.25, 3, 5, 12, 18, 28}

# Common cess rates
VALID_CESS_RATES = {0, 1, 3, 5, 12, 15, 22, 36, 65}


def validate_tax_rate(rate: float) -> bool:
    """Check if a GST rate is valid."""
    return rate in VALID_GST_RATES


def validate_tax_split(cgst: float, sgst: float, igst: float) -> dict:
    """
    Validate the tax split on an invoice.
    Rules:
    - Intra-state: CGST + SGST (equal halves), IGST = 0
    - Inter-state: IGST only, CGST = SGST = 0
    
    Returns: {"valid": bool, "supply_type": "intra"|"inter"|None, "errors": list}
    """
    result = {"valid": False, "supply_type": None, "errors": []}
    
    if igst > 0 and (cgst > 0 or sgst > 0):
        result["errors"].append("Cannot have both IGST and CGST/SGST on same invoice")
        return result
    
    if igst > 0:
        result["supply_type"] = "inter"
        if not validate_tax_rate(igst):
            result["errors"].append(f"IGST rate {igst}% is not a standard GST rate")
            return result
    elif cgst > 0 or sgst > 0:
        result["supply_type"] = "intra"
        if abs(cgst - sgst) > 0.01:
            result["errors"].append(f"CGST ({cgst}%) and SGST ({sgst}%) should be equal")
            return result
        total_rate = cgst + sgst
        if not validate_tax_rate(total_rate):
            result["errors"].append(f"Total GST rate {total_rate}% is not standard")
            return result
    else:
        result["supply_type"] = "exempt"
    
    result["valid"] = True
    return result


# ═══════════════════════════════════════════
# Amount Cross-Check
# ═══════════════════════════════════════════

def validate_amounts(
    taxable: float,
    cgst: float,
    sgst: float,
    igst: float,
    cess: float,
    total: float,
    tolerance: float = 2.0  # Rs 2 tolerance for rounding
) -> dict:
    """
    Cross-check that invoice amounts add up correctly.
    taxable + cgst + sgst + igst + cess = total (within tolerance)
    """
    calculated = taxable + cgst + sgst + igst + cess
    difference = abs(calculated - total)
    
    return {
        "valid": difference <= tolerance,
        "calculated_total": round(calculated, 2),
        "stated_total": total,
        "difference": round(difference, 2),
    }


# ═══════════════════════════════════════════
# Date Parsing (Indian formats)
# ═══════════════════════════════════════════

# Common Indian date formats
DATE_FORMATS = [
    "%d/%m/%Y",    # 15/07/2026
    "%d-%m-%Y",    # 15-07-2026
    "%d.%m.%Y",    # 15.07.2026
    "%d/%m/%y",    # 15/07/26
    "%d-%m-%y",    # 15-07-26
    "%Y-%m-%d",    # 2026-07-15 (ISO)
    "%d %b %Y",    # 15 Jul 2026
    "%d %B %Y",    # 15 July 2026
    "%d-%b-%Y",    # 15-Jul-2026
]


def parse_indian_date(date_str: str) -> Optional[date]:
    """
    Parse a date string in common Indian formats.
    Returns None if parsing fails.
    """
    if not date_str:
        return None
    
    date_str = date_str.strip()
    
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    
    return None


def validate_invoice_date(date_str: str) -> dict:
    """Validate an invoice date."""
    result = {"valid": False, "parsed_date": None, "errors": []}
    
    parsed = parse_indian_date(date_str)
    if not parsed:
        result["errors"].append(f"Could not parse date: '{date_str}'")
        return result
    
    result["parsed_date"] = parsed
    
    # Check if date is in the future
    if parsed > date.today():
        result["errors"].append("Invoice date is in the future")
        return result
    
    # Check if date is too old (more than 2 financial years)
    # Current FY starts April 1
    today = date.today()
    if today.month >= 4:
        fy_start = date(today.year - 2, 4, 1)
    else:
        fy_start = date(today.year - 3, 4, 1)
    
    if parsed < fy_start:
        result["errors"].append("Invoice date is more than 2 financial years old")
        return result
    
    result["valid"] = True
    return result
