from __future__ import annotations

import pandas as pd
import streamlit as st

from .. import logic, ops, ui
from ..schema import CRE_VALUES, FLOX_VALUES, LITTER_STATUSES, PENDING, SEXES, today


def _allele_columns() -> dict:
    return {
        "cre": st.column_config.SelectboxColumn("Cre", options=CRE_VALUES, required=True),
        "p53": st.column_config.SelectboxColumn("p53", options=FLOX_VALUES, required=True),
        "brca2": st.column_config.SelectboxColumn("Brca2", options=FLOX_VALUES, required=True),
    }


def _add_litter(ctx: ui.Context, tables) -> None:
    pairs = tables["pairs"]
    active = pairs[pairs["status"] == "Active"]
    labels = {row["pair_id"]: f"{row['pair_id']}: {row['male']} x {row['female']}, cage {row['cage']}" for _, row in active.iterrows()}
    round_id = ui.form_round("add_litter")
    with st.expander("Add a litter", expanded=tables["litters"].empty):
        with st.form(f"add_litter_{round_id}"):
            pair_id = st.selectbox(
                "Breeding pair",
                list(labels),
                index=None,
                format_func=lambda value: labels.get(value, value),
                placeholder="Choose a pair",
                key=f"litter_pair_{round_id}",
            )
            first, second = st.columns(2)
            with first:
                dob = st.date_input(
                    "Date of birth",
                    value=today(),
                    min_value=ui.EARLIEST_DATE,
                    max_value=today(),
                    format="YYYY-MM-DD",
                    key=f"litter_dob_{round_id}",
                )
            with second:
                n_pups = st.number_input("Number of pups", min_value=0, max_value=40, value=0, step=1, key=f"litter_n_{round_id}")
            notes = st.text_input("Notes", key=f"litter_notes_{round_id}")
            submitted = ui.primary_submit("Save litter")
    if submitted and ui.save(
        ctx,
        f"Add litter from pair {pair_id}",
        lambda tx: ops.add_litter(tx, pair_id, dob, n_pups, notes),
        f"Saved a litter of {n_pups} from pair {pair_id}.",
    ):
        ui.next_round("add_litter")
        st.rerun()


def _summary(tables) -> pd.DataFrame:
    rows = []
    for _, litter in tables["litters"].iterrows():
        breakdown = logic.litter_breakdown(tables["mice"], litter["litter_id"])
        rows.append(
            {
                "Litter": litter["litter_id"],
                "Pair": litter["pair_id"],
                "Born": litter["dob"],
                "Pups": litter["n_pups"],
                "Recorded": breakdown["recorded"],
                "F": breakdown["female"],
                "M": breakdown["male"],
                "Weaned": litter["weaning_date"],
                "Status": litter["status"],
            }
        )
    return pd.DataFrame(rows).sort_values("Born", ascending=False).reset_index(drop=True)


def _add_pups(ctx: ui.Context, litter, recorded: int) -> None:
    litter_id = litter["litter_id"]
    name = f"pups_{litter_id}"
    round_id = ui.form_round(name)
    remaining = max(int(litter["n_pups"] or 0) - recorded, 1)
    blank = pd.DataFrame(
        {
            "ear_tag": pd.Series([""] * remaining, dtype="string"),
            "sex": pd.Series([None] * remaining, dtype="string"),
            "cage": pd.Series([""] * remaining, dtype="string"),
            "cre": pd.Series([PENDING] * remaining, dtype="string"),
            "p53": pd.Series([PENDING] * remaining, dtype="string"),
            "brca2": pd.Series([PENDING] * remaining, dtype="string"),
        }
    )
    with st.form(f"{name}_{round_id}"):
        st.markdown("**Add pups**")
        st.caption("Rows with no ear tag are skipped. Parents and birth date come from the litter.")
        edited = st.data_editor(
            blank,
            hide_index=True,
            width="stretch",
            num_rows="dynamic",
            column_config={
                "ear_tag": st.column_config.TextColumn("Ear tag"),
                "sex": st.column_config.SelectboxColumn("Sex", options=SEXES),
                "cage": st.column_config.TextColumn("Cage"),
                **_allele_columns(),
            },
            key=f"{name}_editor_{round_id}",
        )
        submitted = ui.primary_submit("Save pups")
    if submitted:
        rows = []
        for row in edited.to_dict("records"):
            if pd.isna(row["ear_tag"]) or not str(row["ear_tag"]).strip():
                continue
            rows.append(
                {
                    "ear_tag": str(row["ear_tag"]).strip(),
                    "sex": None if pd.isna(row["sex"]) else row["sex"],
                    "cage": "" if pd.isna(row["cage"]) else row["cage"],
                    "cre": row["cre"],
                    "p53": row["p53"],
                    "brca2": row["brca2"],
                }
            )
        if ui.save(
            ctx,
            f"Add {len(rows)} pups to litter {litter_id}",
            lambda tx: ops.add_pups(tx, litter_id, rows, ctx.user),
            f"Saved {len(rows)} pup{'s' if len(rows) != 1 else ''} in litter {litter_id}.",
        ):
            ui.next_round(name)
            st.rerun()


def _record_genotypes(ctx: ui.Context, litter_id: str, pups: pd.DataFrame) -> None:
    name = f"genotypes_{litter_id}"
    round_id = ui.form_round(name)
    grid = pd.DataFrame(
        {
            "ear_tag": pups["ear_tag"],
            "cre": pups["cre"],
            "p53": pups["p53"],
            "brca2": pups["brca2"],
            "confirmed": pups["genotyping_status"] == "Confirmed",
        }
    ).reset_index(drop=True)
    with st.form(f"{name}_{round_id}"):
        st.markdown("**Record genotypes**")
        edited = st.data_editor(
            grid,
            hide_index=True,
            width="stretch",
            num_rows="fixed",
            disabled=["ear_tag"],
            column_config={
                "ear_tag": st.column_config.TextColumn("Ear tag"),
                **_allele_columns(),
                "confirmed": st.column_config.CheckboxColumn("Confirmed"),
            },
            key=f"{name}_editor_{round_id}",
        )
        submitted = ui.primary_submit("Save genotypes")
    if submitted:
        rows = edited.to_dict("records")
        changed = {"count": 0}

        def change(tx):
            changed["count"] = ops.record_genotypes(tx, rows, ctx.user)

        if ui.save(ctx, f"Record genotypes for litter {litter_id}", change, ""):
            count = changed["count"]
            st.session_state["flash"] = (
                f"Saved genotypes for {count} pup{'s' if count != 1 else ''} in litter {litter_id}."
                if count
                else "Nothing changed, so there was nothing to save."
            )
            ui.next_round(name)
            st.rerun()


def _update_litter(ctx: ui.Context, litter) -> None:
    litter_id = litter["litter_id"]
    name = f"update_{litter_id}"
    round_id = ui.form_round(name)
    with st.expander("Update this litter"):
        with st.form(f"{name}_{round_id}"):
            first, second = st.columns(2)
            with first:
                n_pups = st.number_input(
                    "Number of pups", min_value=0, max_value=40, value=int(litter["n_pups"] or 0), key=f"{name}_n_{round_id}"
                )
            with second:
                status = st.selectbox(
                    "Status",
                    LITTER_STATUSES,
                    index=LITTER_STATUSES.index(litter["status"]) if litter["status"] in LITTER_STATUSES else 0,
                    key=f"{name}_status_{round_id}",
                )
            weaning = st.date_input(
                "Weaning date",
                value=ui.parse_date(litter["weaning_date"]),
                min_value=ui.EARLIEST_DATE,
                max_value=today(),
                format="YYYY-MM-DD",
                key=f"{name}_wean_{round_id}",
            )
            notes = st.text_input("Notes", value=litter["notes"], key=f"{name}_notes_{round_id}")
            submitted = ui.primary_submit("Save litter")
    if submitted and ui.save(
        ctx,
        f"Update litter {litter_id}",
        lambda tx: ops.update_litter(tx, litter_id, n_pups, weaning, status, notes),
        f"Saved litter {litter_id}.",
    ):
        ui.next_round(name)
        st.rerun()


def _detail(ctx: ui.Context, tables, litter_id: str) -> None:
    litter = tables["litters"][tables["litters"]["litter_id"] == litter_id].iloc[0]
    mice = tables["mice"]
    pups = mice[mice["litter_id"] == litter_id]
    breakdown = logic.litter_breakdown(mice, litter_id)

    st.subheader(f"Litter {litter_id}")
    st.text(
        f"Pair {litter['pair_id']}, born {litter['dob']}, {litter['n_pups']} pups, "
        f"{breakdown['recorded']} recorded ({breakdown['female']} F, {breakdown['male']} M)"
    )
    if litter["notes"]:
        st.text(f"Notes: {litter['notes']}")

    if breakdown["genotypes"]:
        st.markdown("**Genotype counts (Cre / p53 / Brca2)**")
        ui.table(pd.DataFrame(list(breakdown["genotypes"].items()), columns=["Genotype", "Pups"]))
    if len(pups):
        _record_genotypes(ctx, litter_id, pups)
    _add_pups(ctx, litter, breakdown["recorded"])
    _update_litter(ctx, litter)


def render(ctx: ui.Context) -> None:
    tables = ctx.store.snapshot()
    st.title("Litters")
    ui.show_flash()
    if tables["pairs"].empty:
        st.info("Add a breeding pair first, then log its litters here.")
        return
    _add_litter(ctx, tables)
    if tables["litters"].empty:
        return
    summary = _summary(tables)
    ui.table(summary)
    litter_id = st.selectbox("Open a litter", list(summary["Litter"]), index=None, placeholder="Choose a litter", key="open_litter")
    if litter_id:
        _detail(ctx, tables, litter_id)
