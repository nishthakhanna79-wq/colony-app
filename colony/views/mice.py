from __future__ import annotations

import pandas as pd
import streamlit as st

from .. import logic, ops, ui
from ..schema import CRE_VALUES, FLOX_VALUES, MOUSE_STATUSES, SEXES, today
from ..store import Tables


def _filter(mice: pd.DataFrame, query: str, filters: dict[str, list[str]]) -> pd.DataFrame:
    result = mice
    for column, chosen in filters.items():
        if chosen:
            result = result[result[column].isin(chosen)]
    if query:
        needle = query.strip().lower()
        haystack = result["ear_tag"] + " " + result["cage"] + " " + result["cohort"] + " " + result["notes"]
        result = result[haystack.str.lower().str.contains(needle, regex=False)]
    return result


def _find(ctx: ui.Context, tables: Tables) -> None:
    mice = tables["mice"]
    if mice.empty:
        st.info("No mice yet. Choose Add to log the first one.")
        return

    query = st.text_input("Search", placeholder="Ear tag, cage, cohort or notes")
    with st.expander("Filters"):
        first, second, third = st.columns(3)
        with first:
            cre = st.multiselect("Cre", CRE_VALUES)
        with second:
            p53 = st.multiselect("p53", FLOX_VALUES)
        with third:
            brca2 = st.multiselect("Brca2", FLOX_VALUES)
        first, second, third = st.columns(3)
        with first:
            sexes = st.multiselect("Sex", SEXES)
        with second:
            min_weeks = st.number_input("Age at least (weeks)", min_value=0, max_value=500, value=0, step=1)
        with third:
            max_weeks = st.number_input("Age at most (weeks)", min_value=0, max_value=500, value=500, step=1)
        first, second, third = st.columns(3)
        with first:
            statuses = st.multiselect(
                "Status", MOUSE_STATUSES, default=[s for s in MOUSE_STATUSES if s != "Deceased"]
            )
        with second:
            cohorts = st.multiselect("Cohort", sorted({c for c in mice["cohort"] if c}))
        with third:
            cages = st.multiselect("Cage", sorted({c for c in mice["cage"] if c}))

    found = _filter(
        mice,
        query,
        {"cre": cre, "p53": p53, "brca2": brca2, "sex": sexes, "status": statuses, "cohort": cohorts, "cage": cages},
    )
    current = today()
    weeks = found["dob"].map(lambda dob: (logic.age_days(dob, current) or 0) // 7)
    found = found[(weeks >= min_weeks) & (weeks <= max_weeks)]
    st.caption(f"{len(found)} of {len(mice)} mice")
    ui.table(ui.mice_table(found))

    if "pending_open_mouse" in st.session_state:
        st.session_state["open_mouse"] = st.session_state.pop("pending_open_mouse")
    tag = st.selectbox(
        "Open a mouse",
        ui.sorted_tags(found),
        index=None,
        placeholder="Type an ear tag",
        format_func=ui.mouse_label(mice),
        key="open_mouse",
    )
    if tag:
        _detail(ctx, tables, tag)


def _detail(ctx: ui.Context, tables: Tables, tag: str) -> None:
    mice = tables["mice"]
    mouse = mice[mice["ear_tag"] == tag].iloc[0]

    st.divider()
    st.subheader(f"Mouse {tag}")
    facts = {
        "Genotype (Cre / p53 / Brca2)": logic.genotype_label(mouse),
        "Sex": mouse["sex"],
        "Born": mouse["dob"],
        "Status": mouse["status"],
        "Cage": mouse["cage"] or "None",
        "Cohort": mouse["cohort"] or "None",
        "Genotyping": mouse["genotyping_status"]
        + (f", {mouse['genotype_confirmed_on']}" if mouse["genotype_confirmed_on"] else ""),
        "Mother": mouse["mother"] or "Not recorded",
        "Father": mouse["father"] or "Not recorded",
    }
    tumor = tables["tumors"][tables["tumors"]["ear_tag"] == tag]
    if len(tumor):
        record = tumor.iloc[0]
        ending = f", {record['outcome_date']}" if record["outcome_date"] else ""
        facts["Tumor"] = f"noticed {record['noticed_on']}, {record['outcome']}{ending}"
    st.text("\n".join(f"{label}: {value}" for label, value in facts.items()))
    if mouse["notes"]:
        st.text(f"Notes: {mouse['notes']}")

    st.markdown("**Litter of origin**")
    if mouse["litter_id"]:
        litter = tables["litters"][tables["litters"]["litter_id"] == mouse["litter_id"]]
        if len(litter):
            row = litter.iloc[0]
            mates = int((mice["litter_id"] == mouse["litter_id"]).sum()) - 1
            st.text(f"{row['litter_id']}, pair {row['pair_id']}, born {row['dob']}, {mates} littermates recorded")
    else:
        st.caption("Not linked to a litter.")

    moves = tables["moves"]
    history = moves[moves["ear_tag"] == tag]
    if len(history):
        st.markdown("**Cage moves**")
        ui.table(
            history.sort_values("moved_on", ascending=False)[["moved_on", "from_cage", "to_cage"]].rename(
                columns={"moved_on": "Date", "from_cage": "From", "to_cage": "To"}
            )
        )

    st.markdown("**Pedigree**")
    st.graphviz_chart(logic.pedigree_dot(mice, tag, 2), width="stretch")

    gels = tables["gels"]
    linked = gels[gels["mice"].map(lambda value: tag in value.split(";"))]
    st.markdown(f"**PCR gels ({len(linked)})**")
    for _, gel in linked.sort_values("date_run", ascending=False).iterrows():
        ui.gel_card(ctx, gel)

    with st.expander("Edit this mouse"):
        name = f"edit_{tag}"
        round_id = ui.form_round(name)
        with st.form(f"{name}_{round_id}"):
            data = ui.mouse_fields(f"{name}_{round_id}", tables, mouse.to_dict())
            submitted = ui.primary_submit("Save changes")
        if submitted and ui.save(
            ctx,
            f"Edit mouse {tag}",
            lambda tx: ops.update_mouse(tx, tag, data, ctx.user),
            f"Saved mouse {str(data['ear_tag']).strip()}.",
        ):
            ui.next_round(name)
            st.session_state["pending_open_mouse"] = str(data["ear_tag"]).strip()
            st.rerun()

    with st.expander("Delete this mouse"):
        references = ops.mouse_references(tables, tag)
        if references:
            st.caption(f"Linked to {' and '.join(references)}, so it can't be deleted. Change its status instead.")
        else:
            st.caption("Use this for entries made by mistake. To record a death, set the status to Deceased.")
            confirm = st.checkbox(f"Yes, delete {tag}", key=f"confirm_delete_{tag}")
            if st.button("Delete", disabled=not confirm, key=f"delete_{tag}") and ui.save(
                ctx, f"Delete mouse {tag}", lambda tx: ops.delete_mouse(tx, tag), f"Deleted mouse {tag}."
            ):
                st.session_state.pop("open_mouse", None)
                st.rerun()


def _add(ctx: ui.Context, tables: Tables) -> None:
    round_id = ui.form_round("add_mouse")
    with st.form(f"add_mouse_{round_id}"):
        data = ui.mouse_fields(f"add_mouse_{round_id}", tables, {})
        submitted = ui.primary_submit("Save mouse")
    if submitted and ui.save(
        ctx,
        f"Add mouse {str(data['ear_tag']).strip()}",
        lambda tx: ops.add_mouse(tx, data, ctx.user),
        f"Saved mouse {str(data['ear_tag']).strip()}. Ready for the next one.",
    ):
        ui.next_round("add_mouse")
        st.rerun()


def render(ctx: ui.Context) -> None:
    tables = ctx.store.snapshot()
    st.title("Mice")
    ui.show_flash()
    mode = st.segmented_control("View", ["Find", "Add"], default="Find", label_visibility="collapsed", key="mice_mode")
    if mode == "Add":
        _add(ctx, tables)
    else:
        _find(ctx, tables)
