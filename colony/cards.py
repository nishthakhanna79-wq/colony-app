"""Printable cage cards, as a PDF: 6 cards on a letter page, with cut lines."""

from __future__ import annotations

from datetime import date

import pandas as pd
from fpdf import FPDF

from . import logic
from .store import Tables

CARD_WIDTH = 4.0
CARD_HEIGHT = 3.3
LEFT_MARGIN = 0.25
TOP_MARGIN = 0.25
COLUMNS = 2
ROWS = 3
MAX_MICE_ON_CARD = 7


def _safe(text: object) -> str:
    """The built-in PDF fonts only cover Latin-1. Anything else prints as a question mark."""
    return str(text).encode("latin-1", "replace").decode("latin-1")


def _fit(pdf: FPDF, text: str, width: float) -> str:
    fitted = _safe(text)
    while fitted and pdf.get_string_width(fitted) > width:
        fitted = fitted[:-1]
    return fitted


def _card(pdf: FPDF, x: float, y: float, cage: str, tables: Tables, header: str, printed: date) -> None:
    inner = x + 0.12
    width = CARD_WIDTH - 0.24
    pdf.set_draw_color(120, 120, 120)
    pdf.set_line_width(0.008)
    pdf.rect(x, y, CARD_WIDTH, CARD_HEIGHT)

    if header:
        pdf.set_font("Helvetica", "", 8)
        pdf.set_xy(inner, y + 0.08)
        pdf.cell(width, 0.15, _fit(pdf, header, width))

    pdf.set_font("Helvetica", "B", 15)
    pdf.set_xy(inner, y + 0.24)
    pdf.cell(width, 0.27, _fit(pdf, f"Cage {cage}", width))

    pairs = tables["pairs"]
    active = pairs[(pairs["cage"] == cage) & (pairs["status"] == "Active")]
    next_y = y + 0.53
    if len(active):
        pair = active.iloc[0]
        litters = tables["litters"][tables["litters"]["pair_id"] == pair["pair_id"]]
        line = f"Pair {pair['pair_id']}: {pair['male']} x {pair['female']}, since {pair['start_date']}"
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_xy(inner, next_y)
        pdf.cell(width, 0.16, _fit(pdf, line, width))
        next_y += 0.16
        if len(litters):
            last = litters.sort_values("dob").iloc[-1]
            pdf.set_xy(inner, next_y)
            pdf.cell(width, 0.16, _fit(pdf, f"Last litter {last['litter_id']}, born {last['dob']}, {last['n_pups']} pups", width))
            next_y += 0.16

    mice = tables["mice"]
    housed = mice[(mice["cage"] == cage) & (mice["status"] != "Deceased")]
    next_y += 0.05
    pdf.set_font("Helvetica", "B", 8.5)
    columns = [("Ear tag", 0.95), ("Sex", 0.33), ("Born", 0.82), ("Cre / p53 / Brca2", width - 2.10)]
    position = inner
    for title, column_width in columns:
        pdf.set_xy(position, next_y)
        pdf.cell(column_width, 0.16, title)
        position += column_width
    next_y += 0.17
    pdf.line(inner, next_y, inner + width, next_y)
    next_y += 0.02

    pdf.set_font("Helvetica", "", 8.5)
    for _, mouse in housed.head(MAX_MICE_ON_CARD).iterrows():
        values = [mouse["ear_tag"], mouse["sex"], mouse["dob"], logic.genotype_label(mouse)]
        position = inner
        for value, (_, column_width) in zip(values, columns):
            pdf.set_xy(position, next_y)
            pdf.cell(column_width, 0.16, _fit(pdf, value, column_width - 0.03))
            position += column_width
        next_y += 0.16
    if len(housed) > MAX_MICE_ON_CARD:
        pdf.set_xy(inner, next_y)
        pdf.cell(width, 0.16, f"and {len(housed) - MAX_MICE_ON_CARD} more")

    pdf.set_font("Helvetica", "", 7)
    pdf.set_xy(inner, y + CARD_HEIGHT - 0.2)
    pdf.cell(width, 0.14, f"Printed {printed.isoformat()}")


def cage_cards_pdf(tables: Tables, cages: list[str], header: str, printed: date) -> bytes:
    pdf = FPDF(unit="in", format="letter")
    pdf.set_auto_page_break(False)
    pdf.set_title("Cage cards")
    per_page = COLUMNS * ROWS
    for index, cage in enumerate(cages):
        if index % per_page == 0:
            pdf.add_page()
        slot = index % per_page
        x = LEFT_MARGIN + (slot % COLUMNS) * CARD_WIDTH
        y = TOP_MARGIN + (slot // COLUMNS) * CARD_HEIGHT
        _card(pdf, x, y, cage, tables, header, printed)
    return bytes(pdf.output())


def cages_with_mice(mice: pd.DataFrame) -> list[str]:
    housed = mice[(mice["status"] != "Deceased") & (mice["cage"] != "")]
    return sorted(set(housed["cage"]), key=lambda cage: (len(cage), cage))
