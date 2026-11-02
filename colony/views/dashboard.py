from __future__ import annotations

import streamlit as st

from .. import logic, ops, ui
from ..schema import MOUSE_STATUSES, today


def _todo(title: str, frame, empty: str) -> None:
    count = len(frame)
    with st.expander(f"{title} ({count})", expanded=count > 0):
        if count:
            ui.table(frame)
        else:
            st.caption(empty)


def render(ctx: ui.Context) -> None:
    tables = ctx.store.snapshot()
    settings = ops.get_settings(tables)
    mice = tables["mice"]
    st.title("Dashboard")
    ui.show_flash()

    with st.container(horizontal=True):
        st.metric("All mice", len(mice), border=True)
        counts = mice["status"].value_counts()
        for status in MOUSE_STATUSES:
            st.metric(status, int(counts.get(status, 0)), border=True)

    st.subheader("To do")
    alerts = logic.build_alerts(tables, settings, today())
    problems = logic.inheritance_problems(mice)
    _todo("Pups to wean", alerts.wean, "No litters are due in the next 7 days.")
    _todo("Genotyping pending", alerts.genotyping, f"No mouse older than {settings['genotyping_pending_days']} days is waiting.")
    _todo(
        f"Pairs with no litter in {settings['litter_gap_weeks']} weeks",
        alerts.quiet_pairs,
        "Every active pair has a recent litter or was paired recently.",
    )
    _todo(
        f"Mice reaching {settings['age_threshold_days']} days",
        alerts.age,
        "No unassigned or experimental mouse is within 14 days of the threshold.",
    )
    _todo("Genotypes that don't fit the parents", problems, "No conflicts found.")

    st.subheader("Genotype ratios")
    ratios = logic.ratio_tracker(mice)
    skewed = int((ratios["Result"] == "Skewed").sum())
    if skewed:
        st.warning(f"{skewed} {'crosses differ' if skewed != 1 else 'cross differs'} from the expected ratio (chi-square, p < 0.05).")
    if len(ratios):
        ui.table(ratios)
        st.caption(
            "Offspring pooled by cross, for each allele. TBD alleles are left out. "
            "Cre P x N assumes the P parent carries one copy."
        )
    else:
        st.caption("Ratios appear once pups with recorded parents are genotyped.")

    st.subheader("Cages")
    census = logic.cage_census(mice, settings["cage_capacity"])
    over = int((census["Flag"] != "").sum())
    with st.container(horizontal=True):
        st.metric("Cages in use", len(census), border=True)
        st.metric("Over capacity", over, border=True)
    if len(census):
        with st.expander("Cage list", expanded=over > 0):
            ui.table(census)
