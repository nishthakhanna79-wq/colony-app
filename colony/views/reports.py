from __future__ import annotations

import streamlit as st

from .. import logic, ui
from ..schema import today


def _section(title: str, frame, empty: str) -> None:
    st.subheader(title)
    if frame.empty:
        st.caption(empty)
    else:
        ui.table(frame)


def render(ctx: ui.Context) -> None:
    tables = ctx.store.snapshot()
    mice = tables["mice"]
    st.title("Reports")
    ui.show_flash()
    st.caption("Each table has a download button at its top right when you tap or hover over it.")
    _section("Living mice by genotype", logic.genotype_report(mice), "No living mice yet.")
    _section("Living mice by age", logic.age_report(mice, today()), "No living mice yet.")
    _section("Breeding pairs", logic.pair_report(tables), "No breeding pairs yet.")
    _section("Births by month", logic.births_by_month(tables["litters"]), "No litters yet.")
    tumors = logic.tumor_report(tables)
    _section("Tumors by genotype", tumors, "No tumors recorded yet.")
    if len(tumors):
        st.caption(
            "Mice recorded counts every mouse with that genotype, including young mice that haven't "
            "had time to develop a tumor, so this isn't an incidence rate."
        )
