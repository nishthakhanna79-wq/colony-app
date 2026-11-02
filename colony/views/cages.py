from __future__ import annotations

import streamlit as st

from .. import cards, logic, ops, ui
from ..schema import today
from ..store import Tables


def _move_form(ctx: ui.Context, tables: Tables, cage: str, housed) -> None:
    name = f"move_{cage}"
    round_id = ui.form_round(name)
    with st.form(f"{name}_{round_id}"):
        st.markdown("**Move mice**")
        tags = st.multiselect(
            "Mice to move",
            ui.sorted_tags(housed),
            format_func=ui.mouse_label(housed),
            placeholder="Choose mice",
            key=f"{name}_tags_{round_id}",
        )
        first, second = st.columns(2)
        with first:
            target = st.text_input("To cage", key=f"{name}_to_{round_id}")
        with second:
            moved_on = st.date_input(
                "Date moved",
                value=today(),
                min_value=ui.EARLIEST_DATE,
                max_value=today(),
                format="YYYY-MM-DD",
                key=f"{name}_on_{round_id}",
            )
        submitted = ui.primary_submit("Move")
    if submitted and ui.save(
        ctx,
        f"Move {len(tags)} mice from cage {cage} to {target}",
        lambda tx: ops.move_mice(tx, tags, target, moved_on, ctx.user),
        f"Moved {len(tags)} {'mouse' if len(tags) == 1 else 'mice'} to cage {str(target).strip()}.",
    ):
        ui.next_round(name)
        st.rerun()


def _detail(ctx: ui.Context, tables: Tables, cage: str) -> None:
    mice = tables["mice"]
    housed = mice[(mice["cage"] == cage) & (mice["status"] != "Deceased")]
    st.subheader(f"Cage {cage}")
    ui.table(ui.mice_table(housed))
    if len(housed):
        _move_form(ctx, tables, cage, housed)


def _print_cards(tables: Tables) -> None:
    cage_options = cards.cages_with_mice(tables["mice"])
    with st.expander("Print cage cards"):
        chosen = st.multiselect("Cages", cage_options, placeholder="Choose cages", key="card_cages")
        header = st.text_input(
            "Line at the top of each card (optional)",
            placeholder="PI, protocol number",
            key="card_header",
        )
        if chosen:
            st.download_button(
                f"Download {len(chosen)} card{'s' if len(chosen) != 1 else ''} as PDF",
                cards.cage_cards_pdf(tables, chosen, header, today()),
                file_name=f"cage-cards-{today().isoformat()}.pdf",
                mime="application/pdf",
                type="primary",
            )
            st.caption("6 cards fit on a letter page, with lines to cut along.")


def render(ctx: ui.Context) -> None:
    tables = ctx.store.snapshot()
    settings = ops.get_settings(tables)
    st.title("Cages")
    ui.show_flash()
    listing = logic.cage_list(tables, settings["cage_capacity"])
    if listing.empty:
        st.info("No mice have a cage yet. Cages appear here once mice are assigned to them.")
        return
    with st.container(horizontal=True):
        st.metric("Cages in use", len(listing), border=True)
        st.metric("Mice housed", int(listing["Mice"].sum()), border=True)
        st.metric("Over capacity", int((listing["Flag"] == "Over capacity").sum()), border=True)
    ui.table(listing)
    _print_cards(tables)
    cage = st.selectbox("Open a cage", list(listing["Cage"]), index=None, placeholder="Type a cage", key="open_cage")
    if cage:
        _detail(ctx, tables, cage)
