"""
backend/investor/statements.py

A statement of account for one investor over one period, as a PDF.

Every figure comes from the ledger and the NAV snapshots, the same sources as
the dashboard, so the statement and the app cannot disagree. The arithmetic
is printed as a reconciliation — opening value, money in, money out, fees,
investment result, closing value — so a reader can add it up themselves.
The investment result is the one derived line: it is whatever makes the
opening value reach the closing value once money in and out are accounted for.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from backend.investor import fund as fundmod
from backend.investor import nav as navmod
from backend.investor.models import (
    KIND_CORRECTION,
    KIND_FEE,
    KIND_REDEEM,
    KIND_SUBSCRIBE,
    Investor,
    UnitTransaction,
)

D = Decimal
LABEL = {KIND_SUBSCRIBE: "Units bought", KIND_REDEEM: "Units sold", KIND_FEE: "Fee",
         KIND_CORRECTION: "Correction"}


@dataclass
class Statement:
    investor_id: str
    name: str
    email: str | None
    period_start: date
    period_end: date
    opening_units: Decimal
    opening_nav: Decimal
    opening_value: Decimal
    money_in: Decimal
    money_out: Decimal
    fees: Decimal
    closing_units: Decimal
    closing_nav: Decimal
    closing_value: Decimal
    rows: list[dict]
    terms_version: int | None

    @property
    def result(self) -> Decimal:
        """Investment result: the change in value not explained by money moving.
        Fees are units cancelled, so they are already inside the closing value;
        adding them back shows the result BEFORE fees, and fees separately."""
        return navmod.money(self.closing_value - self.opening_value - self.money_in
                            + self.money_out + self.fees)

    @property
    def label(self) -> str:
        if self.period_start.day == 1 and (self.period_end + timedelta(days=1)).day == 1 \
                and self.period_start.month == self.period_end.month:
            return self.period_start.strftime("%B %Y")
        return f"{self.period_start:%d %b %Y} – {self.period_end:%d %b %Y}"


def _describe(t) -> str:
    if t.kind == KIND_FEE and t.note:
        # the fee job writes "management fee <period>" / "performance fee <period>"
        return t.note.split(" ")[0].capitalize() + " fee"
    return LABEL.get(t.kind, t.kind)


async def _units_at(session, investor_id: str, until: date) -> Decimal:
    return navmod.units((await session.execute(
        select(func.coalesce(func.sum(UnitTransaction.units), 0))
        .where(UnitTransaction.investor_id == investor_id,
               UnitTransaction.effective_date <= until))).scalar_one())


async def build(session, investor_id: str, period_start: date, period_end: date,
                *, name: str | None = None) -> Statement:
    inv = await session.get(Investor, investor_id)
    day_before = period_start - timedelta(days=1)
    o_units = await _units_at(session, investor_id, day_before)
    o_nav = await fundmod.latest_nav(session, day_before)
    c_units = await _units_at(session, investor_id, period_end)
    c_nav = await fundmod.latest_nav(session, period_end)
    txs = (await session.execute(
        select(UnitTransaction).where(UnitTransaction.investor_id == investor_id,
                                      UnitTransaction.effective_date >= period_start,
                                      UnitTransaction.effective_date <= period_end)
        .order_by(UnitTransaction.effective_date, UnitTransaction.id))).scalars().all()

    def total(kind):
        return navmod.money(sum((D(str(t.amount)) for t in txs if t.kind == kind), D("0")))

    return Statement(
        investor_id=investor_id, name=name or getattr(inv, "name", ""), email=getattr(inv, "email", None),
        period_start=period_start, period_end=period_end,
        opening_units=o_units, opening_nav=o_nav, opening_value=navmod.amount_for_units(o_units, o_nav),
        money_in=total(KIND_SUBSCRIBE), money_out=total(KIND_REDEEM), fees=total(KIND_FEE),
        closing_units=c_units, closing_nav=c_nav, closing_value=navmod.amount_for_units(c_units, c_nav),
        rows=[{"date": t.effective_date, "what": _describe(t), "units": D(str(t.units)),
               "nav": D(str(t.nav_per_unit)), "amount": D(str(t.amount))} for t in txs],
        terms_version=getattr(inv, "terms_version", None),
    )


# ── rendering ────────────────────────────────────────────────────────────────

def _font_dir() -> str | None:
    try:
        import matplotlib
        d = os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data", "fonts", "ttf")
        return d if os.path.exists(os.path.join(d, "DejaVuSans.ttf")) else None
    except Exception:
        return None


def _m(v) -> str:
    s = navmod.text(navmod.money(v))
    neg = s.startswith("-")
    i, f = s.lstrip("-").split(".")
    return f"{'-' if neg else ''}${int(i):,}.{f}"


def _u(v, dp=4) -> str:
    return f"{D(str(v)):,.{dp}f}"


def render(st: Statement) -> bytes:
    from fpdf import FPDF

    pdf = FPDF(format="A4", unit="mm")
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(True, margin=18)
    fonts = _font_dir()
    if fonts:
        pdf.add_font("Body", "", os.path.join(fonts, "DejaVuSans.ttf"))
        pdf.add_font("Body", "B", os.path.join(fonts, "DejaVuSans-Bold.ttf"))
        face, dash = "Body", "–"
    else:  # core font: Latin-1 only, so names are made safe rather than crashing
        face, dash = "Helvetica", "-"

    def safe(t: str) -> str:
        return t if fonts else t.encode("latin-1", "replace").decode("latin-1")

    ink, muted, gold = (16, 23, 34), (107, 114, 128), (184, 145, 42)
    pdf.set_title(f"Alphavantiq Capital statement {st.label}")
    pdf.set_author("Alphavantiq Capital")
    pdf.add_page()

    # header band
    pdf.set_fill_color(*ink)
    pdf.rect(0, 0, 210, 26, "F")
    pdf.set_xy(15, 9)
    pdf.set_font(face, "B", 11)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 8, "ALPHAVANTIQ", new_x="END")
    pdf.set_text_color(*gold)
    pdf.set_font(face, "", 11)
    pdf.cell(0, 8, "  CAPITAL")

    pdf.set_xy(15, 34)
    pdf.set_text_color(*ink)
    pdf.set_font(face, "B", 16)
    pdf.cell(0, 8, "Statement of account", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(face, "", 10)
    pdf.set_text_color(*muted)
    pdf.cell(0, 6, safe(f"{st.name}   ·   {st.label}".replace("·", "-" if not fonts else "·")),
             new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Generated {datetime.now(timezone.utc):%d %b %Y %H:%M} UTC   Account {st.investor_id[:8].upper()}",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    def line(label, value, bold=False, rule=False):
        if rule:
            y = pdf.get_y()
            pdf.set_draw_color(220, 223, 228)
            pdf.line(15, y, 195, y)
        pdf.set_font(face, "B" if bold else "", 11)
        pdf.set_text_color(*(ink if bold else (43, 52, 66)))
        pdf.cell(120, 8, safe(label))
        pdf.cell(60, 8, value, align="R", new_x="LMARGIN", new_y="NEXT")

    line(f"Value on {st.period_start - timedelta(days=1):%d %b %Y}", _m(st.opening_value), bold=True)
    line("Money you paid in", _m(st.money_in))
    line("Money paid out to you", "-" + _m(st.money_out) if st.money_out else _m(0))
    line("Investment result, before fees", _m(st.result))
    line("Fees", "-" + _m(st.fees) if st.fees else _m(0))
    line(f"Value on {st.period_end:%d %b %Y}", _m(st.closing_value), bold=True, rule=True)
    pdf.ln(3)

    pdf.set_font(face, "", 9)
    pdf.set_text_color(*muted)
    pdf.multi_cell(0, 5, safe(
        f"Units held: {_u(st.opening_units)} at the start, {_u(st.closing_units)} at the end. "
        f"Price per unit: {_u(st.opening_nav)} at the start, {_u(st.closing_nav)} at the end. "
        f"Value = units {'×' if fonts else 'x'} price per unit. All amounts in US dollars."))
    pdf.ln(4)

    # transactions
    pdf.set_font(face, "B", 11)
    pdf.set_text_color(*ink)
    pdf.cell(0, 8, "Transactions in this period", new_x="LMARGIN", new_y="NEXT")
    cols = [("Date", 30, "L"), ("Description", 50, "L"), ("Units", 35, "R"),
            ("Price per unit", 35, "R"), ("Amount", 30, "R")]
    pdf.set_font(face, "B", 9)
    pdf.set_text_color(*muted)
    for title, w, a in cols:
        pdf.cell(w, 7, title, align=a)
    pdf.ln()
    pdf.set_font(face, "", 9.5)
    pdf.set_text_color(43, 52, 66)
    if not st.rows:
        pdf.cell(0, 7, "No transactions in this period.", new_x="LMARGIN", new_y="NEXT")
    for r in st.rows:
        for (_, w, a), v in zip(cols, (r["date"].strftime("%d %b %Y"), r["what"], _u(r["units"]),
                                       _u(r["nav"]), _m(r["amount"]))):
            pdf.cell(w, 7, v, align=a)
        pdf.ln()
    pdf.ln(6)

    pdf.set_font(face, "", 8.5)
    pdf.set_text_color(*muted)
    pdf.multi_cell(0, 4.5, safe(
        "How to read this statement: the fund is divided into units. Money paid in buys units at that "
        "day's price and money paid out sells them; the price moves with the fund's trading results. "
        "Fees are taken by cancelling units at the month-end price. "
        + (f"Your money is held under the fund's terms version {st.terms_version}. " if st.terms_version else "")
        + f"This statement is produced from the fund's unit ledger {dash} the same record your online "
          "account shows. Please tell us within 30 days if anything looks wrong."))
    return bytes(pdf.output())


async def pdf_for(session, investor_id: str, period_start: date, period_end: date,
                  *, name: str | None = None) -> tuple[Statement, bytes]:
    st = await build(session, investor_id, period_start, period_end, name=name)
    return st, render(st)


async def final_statement_pdf(session, investor_id: str, *, name: str) -> bytes:
    """From the first day of the current month to today — the closing statement."""
    today = navmod.accounting_date()
    _, pdf = await pdf_for(session, investor_id, today.replace(day=1), today, name=name)
    return pdf
