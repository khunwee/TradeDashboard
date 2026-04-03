# =============================================================================
# reports.py — Report Generation: PDF, Email, Scheduled Reports
# =============================================================================
from datetime import datetime, timezone, timedelta
from typing import Optional, List
import io
import logging

from sqlalchemy.orm import Session
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm, cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak
)
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

from models import Account, ClosedTrade, DailyStats, ScheduledReport, User
from calculations import (
    load_closed_trades, load_daily_stats,
    calc_symbol_breakdown, calc_direction_analysis,
)

logger = logging.getLogger(__name__)

# ── Color Palette ─────────────────────────────────────────────────────────────
DARK_BG    = colors.HexColor("#0D0D0D")
CARD_BG    = colors.HexColor("#141414")
ACCENT     = colors.HexColor("#00D4AA")
ACCENT2    = colors.HexColor("#00A882")
GREEN      = colors.HexColor("#22C55E")
RED        = colors.HexColor("#EF4444")
YELLOW     = colors.HexColor("#F59E0B")
TEXT_LIGHT = colors.HexColor("#E0E0E0")
TEXT_GREY  = colors.HexColor("#9CA3AF")
BORDER     = colors.HexColor("#2A2A2A")


def _pct_color(val: float):
    return GREEN if val >= 0 else RED


def _currency(val: float, symbol: str = "$") -> str:
    sign = "+" if val >= 0 else ""
    return f"{sign}{symbol}{val:,.2f}"


def _pct(val: float) -> str:
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.2f}%"


# =============================================================================
# PDF REPORT GENERATION
# =============================================================================

def generate_account_pdf(
    db: Session,
    account: Account,
    from_date: Optional[datetime] = None,
    to_date:   Optional[datetime] = None,
    title:     str = "Performance Report",
    logo_path: Optional[str] = None,
) -> bytes:
    """
    Generate a professional PDF performance report for an account.
    Returns bytes suitable for HTTP response or email attachment.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15*mm, rightMargin=15*mm,
        topMargin=15*mm, bottomMargin=15*mm,
    )

    styles = getSampleStyleSheet()
    story  = []

    # Custom styles
    h1_style = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=22, textColor=ACCENT, spaceAfter=4)
    h2_style = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=13, textColor=TEXT_LIGHT, spaceAfter=6, spaceBefore=12)
    h3_style = ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=10, textColor=TEXT_GREY, spaceAfter=3)
    body_style = ParagraphStyle("body", fontName="Helvetica", fontSize=9, textColor=TEXT_LIGHT, spaceAfter=4)
    right_style = ParagraphStyle("right", fontName="Helvetica", fontSize=9, textColor=TEXT_LIGHT, alignment=TA_RIGHT)
    label_style = ParagraphStyle("label", fontName="Helvetica", fontSize=8, textColor=TEXT_GREY)

    # ── Header ─────────────────────────────────────────────────────────────────
    account_label = account.label or account.account_number
    story.append(Paragraph(f"MT4/MT5 Trading Dashboard", h3_style))
    story.append(Paragraph(title, h1_style))
    story.append(Paragraph(f"{account_label} | {account.broker_name or account.broker_server}", body_style))

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if from_date and to_date:
        period = f"{from_date.strftime('%Y-%m-%d')} – {to_date.strftime('%Y-%m-%d')}"
    else:
        period = "All Time"
    story.append(Paragraph(f"Period: {period} | Generated: {date_str}", label_style))
    story.append(HRFlowable(width="100%", thickness=1, color=ACCENT))
    story.append(Spacer(1, 8*mm))

    # ── Key Metrics Grid ───────────────────────────────────────────────────────
    story.append(Paragraph("Key Performance Metrics", h2_style))

    metrics = [
        ["Balance", _currency(account.balance), "Equity", _currency(account.equity)],
        ["Total Growth", _pct(account.growth_pct or 0), "Annualized Return", _pct(account.annualized_return or 0)],
        ["Profit Factor", f"{account.profit_factor:.2f}", "Win Rate", _pct(account.win_rate or 0)],
        ["Max Drawdown", _currency(account.max_drawdown_abs or 0), "Max DD %", _pct(account.max_drawdown_pct or 0)],
        ["Sharpe Ratio", f"{account.sharpe_ratio:.2f}" if account.sharpe_ratio else "N/A",
         "Sortino Ratio", f"{account.sortino_ratio:.2f}" if account.sortino_ratio else "N/A"],
        ["Calmar Ratio", f"{account.calmar_ratio:.2f}" if account.calmar_ratio else "N/A",
         "Recovery Factor", f"{account.recovery_factor:.2f}"],
        ["Total Trades", str(account.total_trades or 0), "Expectancy", _currency(account.expectancy or 0)],
        ["Avg Daily Profit", _currency(account.avg_daily_profit or 0),
         "Avg Hold Time", f"{account.avg_hold_time_min:.0f} min" if account.avg_hold_time_min else "N/A"],
        ["Best Win", _currency(account.largest_win or 0), "Worst Loss", _currency(account.largest_loss or 0)],
        ["Max Win Streak", str(account.max_consecutive_wins or 0),
         "Max Loss Streak", str(account.max_consecutive_losses or 0)],
    ]

    col_w = [A4[0] / 2 - 20*mm] * 4
    tbl = Table(
        [[Paragraph(str(c), body_style) for c in row] for row in metrics],
        colWidths=[45*mm, 45*mm, 45*mm, 45*mm],
    )
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CARD_BG),
        ("TEXTCOLOR",  (0, 0), (-1, -1), TEXT_LIGHT),
        ("TEXTCOLOR",  (0, 0), (0, -1), TEXT_GREY),   # label col
        ("TEXTCOLOR",  (2, 0), (2, -1), TEXT_GREY),   # label col
        ("FONTNAME",   (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE",   (0, 0), (-1, -1), 9),
        ("GRID",       (0, 0), (-1, -1), 0.5, BORDER),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [CARD_BG, colors.HexColor("#1A1A1A")]),
        ("PADDING",    (0, 0), (-1, -1), 6),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 10*mm))

    # ── Symbol Breakdown ───────────────────────────────────────────────────────
    trades_df = load_closed_trades(db, account.id)
    symbols   = calc_symbol_breakdown(trades_df)

    if symbols:
        story.append(Paragraph("Symbol Performance", h2_style))
        sym_data = [["Symbol", "Trades", "Win Rate", "Net Profit", "Profit Factor", "Total Lots"]]
        for s in symbols[:20]:
            sym_data.append([
                s["symbol"],
                str(s["trade_count"]),
                f"{s['win_rate']:.1f}%",
                _currency(s["net_profit"]),
                f"{s['profit_factor']:.2f}",
                f"{s['total_lots']:.2f}",
            ])

        sym_tbl = Table(sym_data, colWidths=[40*mm, 25*mm, 28*mm, 35*mm, 30*mm, 28*mm])
        sym_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), ACCENT2),
            ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME",   (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE",   (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [CARD_BG, colors.HexColor("#1A1A1A")]),
            ("TEXTCOLOR",  (0, 1), (-1, -1), TEXT_LIGHT),
            ("GRID",       (0, 0), (-1, -1), 0.4, BORDER),
            ("PADDING",    (0, 0), (-1, -1), 5),
        ]))
        story.append(sym_tbl)
        story.append(Spacer(1, 10*mm))

    # ── Recent Trades ──────────────────────────────────────────────────────────
    recent_trades = db.query(ClosedTrade).filter(
        ClosedTrade.account_id == account.id
    ).order_by(ClosedTrade.close_time.desc()).limit(30).all()

    if recent_trades:
        story.append(PageBreak())
        story.append(Paragraph("Recent Trade History (Last 30 Trades)", h2_style))

        trade_data = [["Ticket", "Symbol", "Type", "Lots", "Open", "Close", "Net P/L", "Duration", "Close Time"]]
        for t in recent_trades:
            dur = f"{t.duration_min:.0f}m" if t.duration_min else ""
            pl_str = _currency(t.net_profit)
            trade_data.append([
                str(t.ticket), t.symbol, t.trade_type.value.upper(),
                f"{t.lots:.2f}", f"{t.open_price:.5f}", f"{t.close_price:.5f}",
                pl_str, dur,
                t.close_time.strftime("%m-%d %H:%M") if t.close_time else "",
            ])

        trade_tbl = Table(trade_data, colWidths=[22*mm, 28*mm, 16*mm, 14*mm, 22*mm, 22*mm, 24*mm, 16*mm, 22*mm])
        trade_tbl.setStyle(TableStyle([
            ("BACKGROUND",  (0, 0), (-1, 0), ACCENT2),
            ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
            ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME",    (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE",    (0, 0), (-1, -1), 7),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [CARD_BG, colors.HexColor("#1A1A1A")]),
            ("TEXTCOLOR",   (0, 1), (-1, -1), TEXT_LIGHT),
            ("GRID",        (0, 0), (-1, -1), 0.3, BORDER),
            ("PADDING",     (0, 0), (-1, -1), 4),
        ]))
        story.append(trade_tbl)

    # ── Footer ─────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 10*mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER))
    story.append(Paragraph(
        f"Generated by MT4/MT5 Trading Dashboard | {date_str} | All calculations use real-time data",
        ParagraphStyle("footer", fontName="Helvetica", fontSize=7, textColor=TEXT_GREY, alignment=TA_CENTER),
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer.read()


# =============================================================================
# DAILY REPORT EMAIL
# =============================================================================

async def generate_daily_report_email(db: Session, report: ScheduledReport):
    """Send daily performance report email."""
    from alerts import send_email_alert

    user = db.query(User).filter(User.id == report.user_id).first()
    if not user:
        return

    account_ids = report.account_ids or []
    if not account_ids:
        accounts = db.query(Account).filter(
            Account.owner_id == report.user_id,
            Account.is_active == True,
        ).all()
    else:
        accounts = db.query(Account).filter(Account.id.in_(account_ids)).all()

    for account in accounts:
        label   = account.label or account.account_number
        subject = f"Daily Report: {label} — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"

        message = (
            f"Daily Performance Report — {label}\n\n"
            f"Balance: ${account.balance:,.2f}\n"
            f"Equity:  ${account.equity:,.2f}\n"
            f"Today's P/L: ${account.profit_today:+,.2f}\n"
            f"Drawdown: {account.max_drawdown_pct:.2f}%\n"
            f"Open Orders: {account.open_orders_count}\n"
        )

        try:
            # Generate PDF attachment
            pdf_bytes = generate_account_pdf(
                db, account, title=f"Daily Report — {label}"
            )
            # In a full implementation, attach PDF to email
            # For now, send HTML email
            await send_email_alert(user.email, subject, message, account)
            logger.info(f"Daily report sent for account {label}")
        except Exception as e:
            logger.error(f"Failed to send daily report for {label}: {e}")

        report.last_sent_at = datetime.now(timezone.utc)
        db.commit()
