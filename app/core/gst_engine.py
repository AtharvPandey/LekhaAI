"""
GST Calculation Engine.

Handles:
- GSTR-3B summary generation (tax computation)
- GSTR-1 table-wise breakup
- ITC calculation
- Due date tracking with reminders
- Return period management
"""

import logging
from datetime import datetime, date, timedelta
from typing import Optional

from app.db.database import get_monthly_stats, get_invoices_by_period, get_supplier_summary

logger = logging.getLogger("lekha.gst")


# ═══════════════════════════════════════════
# Return Period Helpers
# ═══════════════════════════════════════════

def current_return_period() -> str:
    """Get current return period in YYYYMM format."""
    now = datetime.now()
    return now.strftime("%Y%m")


def previous_return_period() -> str:
    """Get previous month's return period."""
    now = datetime.now()
    if now.month == 1:
        return f"{now.year - 1}12"
    return f"{now.year}{now.month - 1:02d}"


def format_period_display(period: str) -> str:
    """Convert '202607' to 'July 2026'."""
    try:
        dt = datetime.strptime(period, "%Y%m")
        return dt.strftime("%B %Y")
    except ValueError:
        return period


# ═══════════════════════════════════════════
# GSTR-3B Summary
# ═══════════════════════════════════════════

def generate_gstr3b_summary(business_id: int, return_period: str = None) -> dict:
    """
    Generate GSTR-3B summary for a business.

    Returns complete tax computation:
    - Output tax (on sales)
    - Input tax credit (on purchases)
    - Net tax payable
    """
    if not return_period:
        return_period = current_return_period()

    stats = get_monthly_stats(business_id, return_period)

    if not stats or stats.get("invoice_count", 0) == 0:
        return {
            "return_period": return_period,
            "period_display": format_period_display(return_period),
            "has_data": False,
            "invoice_count": 0,
        }

    # For now we only track purchase invoices (input side)
    # Sales invoices will be added in Phase 2
    input_cgst = stats.get("total_cgst", 0)
    input_sgst = stats.get("total_sgst", 0)
    input_igst = stats.get("total_igst", 0)
    total_itc = input_cgst + input_sgst + input_igst

    return {
        "return_period": return_period,
        "period_display": format_period_display(return_period),
        "has_data": True,
        "invoice_count": stats.get("invoice_count", 0),
        "total_purchase_value": round(stats.get("total_amount", 0), 2),
        "total_taxable_value": round(stats.get("total_taxable", 0), 2),
        # Input Tax Credit (purchases)
        "input_cgst": round(input_cgst, 2),
        "input_sgst": round(input_sgst, 2),
        "input_igst": round(input_igst, 2),
        "total_itc": round(total_itc, 2),
        "total_cess": round(stats.get("total_cess", 0), 2),
        # Output tax (sales) - placeholder until sales invoices are added
        "output_cgst": 0,
        "output_sgst": 0,
        "output_igst": 0,
        "total_output_tax": 0,
        # Net payable
        "net_cgst": round(0 - input_cgst, 2),
        "net_sgst": round(0 - input_sgst, 2),
        "net_igst": round(0 - input_igst, 2),
        "net_payable": round(0 - total_itc, 2),
    }


# ═══════════════════════════════════════════
# WhatsApp Formatted Summary
# ═══════════════════════════════════════════

def format_summary_for_whatsapp(business_id: int, return_period: str = None) -> str:
    """Generate a WhatsApp-formatted monthly summary."""
    summary = generate_gstr3b_summary(business_id, return_period)

    if not summary["has_data"]:
        period = summary.get("period_display", "this month")
        return (
            f"📊 *{period} — No Data*\n\n"
            "Abhi tak koi invoice process nahi hua.\n"
            "Invoice ki photo bhejein! 📸"
        )

    period = summary["period_display"]
    lines = [
        f"📊 *GST Summary — {period}*",
        f"{'─' * 28}",
        "",
        f"📥 *Purchase Invoices:* {summary['invoice_count']}",
        f"💰 *Total Value:* ₹{summary['total_purchase_value']:,.2f}",
        f"📄 *Taxable Value:* ₹{summary['total_taxable_value']:,.2f}",
        "",
        "📋 *Input Tax Credit (ITC):*",
        f"  CGST: ₹{summary['input_cgst']:,.2f}",
        f"  SGST: ₹{summary['input_sgst']:,.2f}",
        f"  IGST: ₹{summary['input_igst']:,.2f}",
        f"  *Total ITC: ₹{summary['total_itc']:,.2f}*",
    ]

    if summary["total_cess"] > 0:
        lines.append(f"  Cess: ₹{summary['total_cess']:,.2f}")

    lines.extend([
        "",
        "⚠️ *Note:* Sales data not included yet.",
        "Yeh summary filing-ready nahi hai.",
        "Apne CA se verify karwayein.",
    ])

    return "\n".join(lines)


def format_supplier_summary_for_whatsapp(business_id: int, return_period: str = None) -> str:
    """Supplier-wise breakdown for WhatsApp."""
    if not return_period:
        return_period = current_return_period()

    suppliers = get_supplier_summary(business_id, return_period)

    if not suppliers:
        return "📊 No supplier data available for this period."

    period = format_period_display(return_period)
    lines = [
        f"📊 *Supplier Summary — {period}*",
        f"{'─' * 28}",
        "",
    ]

    for i, s in enumerate(suppliers[:10], 1):  # Top 10
        name = s.get("supplier_name", "Unknown") or "Unknown"
        gstin = s.get("supplier_gstin", "")
        count = s.get("invoice_count", 0)
        total = s.get("total_amount", 0)
        lines.append(f"{i}. *{name}*")
        if gstin:
            lines.append(f"   GSTIN: {gstin}")
        lines.append(f"   {count} invoices | ₹{total:,.2f}")
        lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════
# Due Date Tracking
# ═══════════════════════════════════════════

def get_upcoming_due_dates() -> list[dict]:
    """Get upcoming GST filing due dates."""
    now = datetime.now()
    dates = []

    # GSTR-1 (sales return) — 11th of next month
    # GSTR-3B (summary return) — 20th of next month
    for offset in range(1, 3):  # Next 2 months
        m = now.month + offset
        y = now.year
        if m > 12:
            m -= 12
            y += 1

        period = datetime(y, m, 1)
        period_name = period.strftime("%B %Y")

        gstr1_due = date(y, m, 11)
        gstr3b_due = date(y, m, 20)

        today = date.today()

        if gstr1_due >= today:
            days_left = (gstr1_due - today).days
            dates.append({
                "return": "GSTR-1",
                "description": "Sales return",
                "due_date": gstr1_due,
                "due_str": gstr1_due.strftime("%d %b %Y"),
                "days_left": days_left,
                "period": period_name,
                "urgent": days_left <= 3,
                "overdue": days_left < 0,
            })

        if gstr3b_due >= today:
            days_left = (gstr3b_due - today).days
            dates.append({
                "return": "GSTR-3B",
                "description": "Summary return",
                "due_date": gstr3b_due,
                "due_str": gstr3b_due.strftime("%d %b %Y"),
                "days_left": days_left,
                "period": period_name,
                "urgent": days_left <= 3,
                "overdue": days_left < 0,
            })

    return sorted(dates, key=lambda x: x["due_date"])


def format_due_dates_for_whatsapp() -> str:
    """Format due dates for WhatsApp."""
    dates = get_upcoming_due_dates()

    if not dates:
        return "📅 No upcoming due dates."

    lines = [
        "📅 *Upcoming GST Due Dates*",
        f"{'─' * 28}",
        "",
    ]

    for d in dates[:4]:  # Show next 4
        emoji = "🔴" if d["urgent"] else "📝"
        days = d["days_left"]
        if days == 0:
            urgency = "⚠️ TODAY!"
        elif days == 1:
            urgency = "⚠️ TOMORROW!"
        elif days <= 3:
            urgency = f"⚠️ {days} days left!"
        else:
            urgency = f"{days} days left"

        lines.append(f"{emoji} *{d['return']}* ({d['description']})")
        lines.append(f"   Due: {d['due_str']} — {urgency}")
        lines.append(f"   Period: {d['period']}")
        lines.append("")

    lines.append("💡 *Tip:* Sab invoices due date se 3 din pehle bhej do!")
    return "\n".join(lines)
