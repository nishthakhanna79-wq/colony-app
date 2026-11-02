from __future__ import annotations

import streamlit as st

from .. import logic, ops, ui
from ..schema import BREEDING_CHOICES, TUMOR_OUTCOMES, today
from ..store import Tables


def _fields(prefix: str, values: dict) -> dict:
    """The tumor details, shared by adding and updating. Call inside a form."""
    noticed = st.date_input(
        "Date tumor noticed",
        value=ui.parse_date(values.get("noticed_on", ""), today()),
        min_value=ui.EARLIEST_DATE,
        max_value=today(),
        format="YYYY-MM-DD",
        key=f"{prefix}_noticed",
    )
    in_breeding = st.radio(
        "In breeding?",
        BREEDING_CHOICES,
        index=BREEDING_CHOICES.index(values["in_breeding"]) if values.get("in_breeding") in BREEDING_CHOICES else None,
        horizontal=True,
        key=f"{prefix}_breeding",
    )
    outcome = st.radio(
        "Outcome",
        TUMOR_OUTCOMES,
        index=TUMOR_OUTCOMES.index(values["outcome"]) if values.get("outcome") in TUMOR_OUTCOMES else 0,
        horizontal=True,
        key=f"{prefix}_outcome",
    )
    outcome_date = st.date_input(
        "Date deceased or collected",
        value=ui.parse_date(values.get("outcome_date", "")),
        min_value=ui.EARLIEST_DATE,
        max_value=today(),
        format="YYYY-MM-DD",
        key=f"{prefix}_outcome_date",
    )
    notes = st.text_input("Notes", value=values.get("notes", ""), placeholder="Site, size", key=f"{prefix}_notes")
    st.caption("Deceased or Collected also marks the mouse Deceased if it's in the Mice list.")
    return {
        "noticed_on": noticed,
        "in_breeding": in_breeding,
        "outcome": outcome,
        "outcome_date": outcome_date,
        "notes": notes,
    }


def _add(ctx: ui.Context, tables: Tables) -> None:
    mice = tables["mice"]
    recorded = set(tables["tumors"]["ear_tag"])
    round_id = ui.form_round("add_tumor")
    with st.form(f"add_tumor_{round_id}"):
        ear_tag = st.selectbox(
            "Ear tag",
            [tag for tag in ui.sorted_tags(mice) if tag not in recorded],
            index=None,
            accept_new_options=True,
            format_func=ui.mouse_label(mice),
            placeholder="Choose or type an ear tag",
            key=f"tumor_tag_{round_id}",
        )
        data = _fields(f"add_tumor_{round_id}", {})
        submitted = ui.primary_submit("Save tumor record")
    if submitted and ui.save(
        ctx,
        f"Add tumor record for {str(ear_tag or '').strip()}",
        lambda tx: ops.add_tumor(tx, {**data, "ear_tag": ear_tag}, ctx.user),
        f"Saved tumor record for {str(ear_tag or '').strip()}.",
    ):
        ui.next_round("add_tumor")
        st.rerun()


def _update(ctx: ui.Context, tables: Tables) -> None:
    tumors = tables["tumors"]
    tag = st.selectbox(
        "Update a mouse",
        ui.sorted_tags(tumors),
        index=None,
        placeholder="Type an ear tag",
        key="open_tumor",
    )
    if not tag:
        return
    record = tumors[tumors["ear_tag"] == tag].iloc[0]
    name = f"edit_tumor_{tag}"
    round_id = ui.form_round(name)
    with st.form(f"{name}_{round_id}"):
        st.markdown(f"**Mouse {tag}**")
        data = _fields(f"{name}_{round_id}", record.to_dict())
        submitted = ui.primary_submit("Save changes")
    if submitted and ui.save(
        ctx,
        f"Update tumor record for {tag}",
        lambda tx: ops.update_tumor(tx, tag, data, ctx.user),
        f"Saved tumor record for {tag}.",
    ):
        ui.next_round(name)
        st.rerun()

    with st.expander("Delete this tumor record"):
        st.caption("Use this for entries made by mistake. It doesn't change the mouse's status.")
        confirm = st.checkbox(f"Yes, delete the record for {tag}", key=f"confirm_delete_tumor_{tag}")
        if st.button("Delete", disabled=not confirm, key=f"delete_tumor_{tag}") and ui.save(
            ctx,
            f"Delete tumor record for {tag}",
            lambda tx: ops.delete_tumor(tx, tag),
            f"Deleted the tumor record for {tag}.",
        ):
            st.session_state.pop("open_tumor", None)
            st.rerun()


def _list(ctx: ui.Context, tables: Tables) -> None:
    tumors = tables["tumors"]
    if tumors.empty:
        st.info("No tumors recorded yet. Choose Add to log the first one.")
        return
    with st.container(horizontal=True):
        st.metric("Tumor mice", len(tumors), border=True)
        st.metric("In breeding", int((tumors["in_breeding"] == "Yes").sum()), border=True)
        for outcome in ("Alive", "Deceased", "Collected"):
            st.metric(outcome, int((tumors["outcome"] == outcome).sum()), border=True)
    ui.table(logic.tumor_table(tables))
    _update(ctx, tables)


def render(ctx: ui.Context) -> None:
    tables = ctx.store.snapshot()
    st.title("Tumors")
    ui.show_flash()
    mode = st.segmented_control("View", ["List", "Add"], default="List", label_visibility="collapsed", key="tumors_mode")
    if mode == "Add":
        _add(ctx, tables)
    else:
        _list(ctx, tables)
