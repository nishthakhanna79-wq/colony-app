from __future__ import annotations

import streamlit as st

from .. import logic, ops, ui
from ..schema import today


def _add(ctx: ui.Context, tables) -> None:
    mice = ui.alive(tables["mice"])
    label = ui.mouse_label(mice)
    round_id = ui.form_round("add_pair")
    with st.expander("Add a pair", expanded=tables["pairs"].empty):
        with st.form(f"add_pair_{round_id}"):
            first, second = st.columns(2)
            with first:
                male = st.selectbox(
                    "Male",
                    ui.sorted_tags(mice[mice["sex"] == "M"]),
                    index=None,
                    format_func=label,
                    placeholder="Type an ear tag",
                    key=f"pair_male_{round_id}",
                )
            with second:
                female = st.selectbox(
                    "Female",
                    ui.sorted_tags(mice[mice["sex"] == "F"]),
                    index=None,
                    format_func=label,
                    placeholder="Type an ear tag",
                    key=f"pair_female_{round_id}",
                )
            first, second = st.columns(2)
            with first:
                cage = st.text_input("Cage", key=f"pair_cage_{round_id}")
            with second:
                start = st.date_input(
                    "Pairing start date",
                    value=today(),
                    min_value=ui.EARLIEST_DATE,
                    max_value=today(),
                    format="YYYY-MM-DD",
                    key=f"pair_start_{round_id}",
                )
            notes = st.text_input("Notes", key=f"pair_notes_{round_id}")
            st.caption("Saving marks both mice as Breeder and moves them to this cage.")
            submitted = ui.primary_submit("Save pair")
    if submitted and ui.save(
        ctx,
        f"Add breeding pair {male} x {female}",
        lambda tx: ops.add_pair(tx, male, female, cage, start, notes, ctx.user),
        f"Saved pair {male} x {female}.",
    ):
        ui.next_round("add_pair")
        st.rerun()


def render(ctx: ui.Context) -> None:
    tables = ctx.store.snapshot()
    settings = ops.get_settings(tables)
    st.title("Breeding pairs")
    ui.show_flash()
    _add(ctx, tables)

    if tables["pairs"].empty:
        st.info("No breeding pairs yet.")
        return

    summary = logic.pair_summary(tables, today(), settings["litter_gap_weeks"])
    show_retired = st.toggle("Show retired pairs")
    shown = summary if show_retired else summary[summary["Status"] == "Active"]
    ui.table(shown)

    pair_id = st.selectbox("Open a pair", list(shown["Pair"]), index=None, placeholder="Choose a pair")
    if not pair_id:
        return
    pair = tables["pairs"][tables["pairs"]["pair_id"] == pair_id].iloc[0]
    st.subheader(f"Pair {pair_id}: {pair['male']} x {pair['female']}")

    litters = tables["litters"][tables["litters"]["pair_id"] == pair_id]
    st.markdown(f"**Litter history ({len(litters)})**")
    if len(litters):
        ui.table(
            litters.sort_values("dob", ascending=False)[["litter_id", "dob", "n_pups", "weaning_date", "status"]].rename(
                columns={
                    "litter_id": "Litter",
                    "dob": "Born",
                    "n_pups": "Pups",
                    "weaning_date": "Weaned",
                    "status": "Status",
                }
            )
        )
    else:
        st.caption("No litters recorded.")

    if pair["status"] == "Active" and st.button("Retire this pair", key=f"retire_{pair_id}"):
        if ui.save(ctx, f"Retire pair {pair_id}", lambda tx: ops.retire_pair(tx, pair_id), f"Retired pair {pair_id}."):
            st.rerun()
