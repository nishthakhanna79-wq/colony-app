from __future__ import annotations

import streamlit as st

from .. import ops, ui
from ..schema import GEL_LOCI, GEL_RESULTS, today

PER_PAGE_OPTIONS = [12, 24, 48]


def _upload(ctx: ui.Context, tables) -> None:
    mice = tables["mice"]
    round_id = ui.form_round("add_gel")
    source = st.segmented_control(
        "Source",
        ["Choose a file", "Take a photo"],
        default="Choose a file",
        label_visibility="collapsed",
        key=f"gel_source_{round_id}",
    )
    with st.form(f"add_gel_{round_id}"):
        if source == "Take a photo":
            upload = st.camera_input("Gel photo", key=f"gel_camera_{round_id}")
        else:
            upload = st.file_uploader(
                "Gel photo or PDF",
                type=["jpg", "jpeg", "png", "pdf"],
                key=f"gel_file_{round_id}",
            )
        tags = st.multiselect(
            "Mice on this gel",
            ui.sorted_tags(mice),
            format_func=ui.mouse_label(mice),
            placeholder="Type ear tags",
            key=f"gel_mice_{round_id}",
        )
        first, second = st.columns(2)
        with first:
            date_run = st.date_input(
                "Date run",
                value=today(),
                min_value=ui.EARLIEST_DATE,
                max_value=today(),
                format="YYYY-MM-DD",
                key=f"gel_date_{round_id}",
            )
        with second:
            locus = st.selectbox("Allele", GEL_LOCI, key=f"gel_locus_{round_id}")
        primer_options = sorted({p for p in tables["gels"]["primers"] if p})
        primers = st.selectbox(
            "Primers used",
            primer_options,
            index=None,
            accept_new_options=True,
            placeholder="Choose or type primers",
            key=f"gel_primers_{round_id}",
        )
        bands = st.text_input("Expected vs. observed bands", key=f"gel_bands_{round_id}")
        result = st.radio("Result", GEL_RESULTS, horizontal=True, key=f"gel_result_{round_id}")
        notes = st.text_input("Notes", key=f"gel_notes_{round_id}")
        submitted = ui.primary_submit("Save gel")
    if submitted:
        name = upload.name if upload else ""
        content = upload.getvalue() if upload else None
        if ui.save(
            ctx,
            f"Add {locus} gel for {len(tags)} mice",
            lambda tx: ops.add_gel(tx, name, content, tags, date_run, locus, primers, bands, result, notes, ctx.user),
            "Saved the gel.",
        ):
            ui.next_round("add_gel")
            st.rerun()


def _gallery(ctx: ui.Context, tables) -> None:
    gels = tables["gels"]
    if gels.empty:
        st.info("No gels yet. Choose Upload to add the first one.")
        return
    first, second, third = st.columns(3)
    with first:
        tag = st.selectbox(
            "Mouse",
            ui.sorted_tags(tables["mice"]),
            index=None,
            placeholder="All mice",
            key="gel_filter_mouse",
        )
    with second:
        dates = st.date_input(
            "Date run",
            value=(),
            min_value=ui.EARLIEST_DATE,
            max_value=today(),
            format="YYYY-MM-DD",
            key="gel_filter_dates",
        )
    with third:
        limit = st.selectbox("Show", PER_PAGE_OPTIONS, key="gel_limit")

    found = gels
    if tag:
        found = found[found["mice"].map(lambda value: tag in value.split(";"))]
    if len(dates) == 2:
        found = found[(found["date_run"] >= dates[0].isoformat()) & (found["date_run"] <= dates[1].isoformat())]
    found = found.sort_values(["date_run", "gel_id"], ascending=False)
    st.caption(f"Showing {min(limit, len(found))} of {len(found)} matching gels")

    columns = st.columns(3)
    for position, (_, gel) in enumerate(found.head(limit).iterrows()):
        with columns[position % 3]:
            with st.container(border=True):
                ui.gel_card(ctx, gel)


def render(ctx: ui.Context) -> None:
    tables = ctx.store.snapshot()
    st.title("PCR gels")
    ui.show_flash()
    mode = st.segmented_control("View", ["Browse", "Upload"], default="Browse", label_visibility="collapsed", key="gels_mode")
    if mode == "Upload":
        if tables["mice"].empty:
            st.info("Add mice first, so the gel has something to link to.")
        else:
            _upload(ctx, tables)
    else:
        _gallery(ctx, tables)
