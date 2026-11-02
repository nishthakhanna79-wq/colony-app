"""Shared Streamlit helpers: saving, form reset, and the mouse form."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Callable

import pandas as pd
import streamlit as st

from . import auth, logic, ops
from .backends import GitHubBackend, LocalBackend, StoreError
from .schema import (
    GENOTYPING_STATUSES,
    LOCI,
    LOCUS_LABELS,
    LOCUS_VALUES,
    MOUSE_STATUSES,
    PENDING,
    SEXES,
    TABLE_COLUMNS,
    today,
)
from .store import Store, Tables, Transaction

EARLIEST_DATE = date(2010, 1, 1)
DATA_BRANCH = "data"  # Never the branch Streamlit deploys, or every save would touch the app.


@dataclass
class Context:
    store: Store
    user: str


def get_store() -> Store:
    # The running app keeps this cached through code updates. Keying it on the table layout
    # means an update that changes the tables gets a new store, not one from before the change.
    return _cached_store(repr(TABLE_COLUMNS))


@st.cache_resource(show_spinner=False)
def _cached_store(table_layout: str) -> Store:
    github = auth.secret_section("github")
    if github is not None:
        backend = GitHubBackend(
            repo=github["repo"],
            token=github["token"],
            branch=github.get("branch", DATA_BRANCH),
            api_url=github.get("api_url", "https://api.github.com"),
        )
        return Store(backend)
    local = os.environ.get("COLONY_LOCAL_DATA")
    if local:
        return Store(LocalBackend(local), refresh_seconds=1)
    raise StoreError("The data repo isn't set up yet.")


def save(ctx: Context, message: str, change: Callable[[Transaction], object], success: str) -> bool:
    """Run one save. Show any problem and return False, leaving the form as typed."""
    try:
        ctx.store.transaction(f"{message} [{ctx.user}]", change)
    except (ops.ValidationError, StoreError) as exc:
        st.error(str(exc))
        return False
    st.session_state["flash"] = success
    return True


def show_flash() -> None:
    message = st.session_state.pop("flash", None)
    if message:
        st.success(message)


def form_round(name: str) -> int:
    """Forms stay filled in until a save works. Bumping the round then clears them."""
    return st.session_state.setdefault(f"round_{name}", 0)


def next_round(name: str) -> None:
    st.session_state[f"round_{name}"] = form_round(name) + 1


def table(frame: pd.DataFrame, **kwargs) -> None:
    st.dataframe(frame, hide_index=True, width="stretch", **kwargs)


def parse_date(value: str, fallback: date | None = None) -> date | None:
    return logic.parse_iso(value) or fallback


def mouse_label(mice: pd.DataFrame) -> Callable[[str], str]:
    labels = {row["ear_tag"]: f"{row['ear_tag']}  ({logic.genotype_label(row)})" for _, row in mice.iterrows()}
    return lambda tag: labels.get(tag, tag)


def sorted_tags(mice: pd.DataFrame) -> list[str]:
    return sorted(mice["ear_tag"], key=lambda tag: (len(tag), tag))


def _option_index(options: list[str], value: object, fallback: int | None = 0) -> int | None:
    return options.index(value) if value in options else fallback


def allele_inputs(prefix: str, values: dict) -> dict[str, str]:
    # A horizontal container keeps the 3 alleles on one line on a phone. Columns would stack.
    chosen = {}
    with st.container(horizontal=True, gap="small", key=f"alleles_{prefix}"):
        for locus in LOCI:
            options = LOCUS_VALUES[locus]
            chosen[locus] = st.selectbox(
                LOCUS_LABELS[locus],
                options,
                index=_option_index(options, values.get(locus, PENDING), options.index(PENDING)),
                key=f"{prefix}_{locus}",
            )
    return chosen


def mouse_fields(prefix: str, tables: Tables, values: dict) -> dict:
    """The fields for adding or editing a mouse. Call inside a form."""
    mice = tables["mice"]
    this_tag = values.get("ear_tag", "")

    first, second = st.columns(2)
    with first:
        ear_tag = st.text_input("Ear tag", value=this_tag, key=f"{prefix}_tag")
    with second:
        cage = st.text_input("Cage", value=values.get("cage", ""), key=f"{prefix}_cage")

    first, second = st.columns(2)
    with first:
        sex = st.radio(
            "Sex",
            SEXES,
            index=_option_index(SEXES, values.get("sex"), 0),
            horizontal=True,
            key=f"{prefix}_sex",
        )
    with second:
        dob = st.date_input(
            "Date of birth",
            value=parse_date(values.get("dob", ""), today()),
            min_value=EARLIEST_DATE,
            max_value=today(),
            format="YYYY-MM-DD",
            key=f"{prefix}_dob",
        )

    st.markdown("**Genotype**")
    alleles = allele_inputs(prefix, values)

    first, second = st.columns(2)
    with first:
        genotyping = st.selectbox(
            "Genotyping",
            GENOTYPING_STATUSES,
            index=_option_index(GENOTYPING_STATUSES, values.get("genotyping_status", "Pending")),
            key=f"{prefix}_genotyping",
        )
    with second:
        confirmed_on = st.date_input(
            "Date confirmed (if confirmed)",
            value=parse_date(values.get("genotype_confirmed_on", ""), today()),
            min_value=EARLIEST_DATE,
            max_value=today(),
            format="YYYY-MM-DD",
            key=f"{prefix}_confirmed_on",
        )

    first, second = st.columns(2)
    with first:
        status = st.selectbox(
            "Status",
            MOUSE_STATUSES,
            index=_option_index(MOUSE_STATUSES, values.get("status", "Unassigned")),
            key=f"{prefix}_status",
        )
    with second:
        cohorts = sorted({c for c in mice["cohort"] if c})
        cohort = st.selectbox(
            "Cohort tag (optional)",
            cohorts,
            index=_option_index(cohorts, values.get("cohort"), None),
            accept_new_options=True,
            placeholder="Choose or type a new tag",
            key=f"{prefix}_cohort",
        )

    label = mouse_label(mice)
    first, second = st.columns(2)
    with first:
        mothers = sorted_tags(mice[(mice["sex"] == "F") & (mice["ear_tag"] != this_tag)])
        mother = st.selectbox(
            "Mother (optional)",
            mothers,
            index=_option_index(mothers, values.get("mother"), None),
            format_func=label,
            placeholder="Type an ear tag",
            key=f"{prefix}_mother",
        )
    with second:
        fathers = sorted_tags(mice[(mice["sex"] == "M") & (mice["ear_tag"] != this_tag)])
        father = st.selectbox(
            "Father (optional)",
            fathers,
            index=_option_index(fathers, values.get("father"), None),
            format_func=label,
            placeholder="Type an ear tag",
            key=f"{prefix}_father",
        )

    notes = st.text_area(
        "Notes",
        value=values.get("notes", ""),
        placeholder="Health flags, tumor onset date",
        height=80,
        key=f"{prefix}_notes",
    )
    return {
        "ear_tag": ear_tag,
        "sex": sex,
        "dob": dob,
        "cage": cage,
        "status": status,
        "cohort": cohort or "",
        "mother": mother or "",
        "father": father or "",
        "notes": notes,
        "genotyping_status": genotyping,
        "genotype_confirmed_on": confirmed_on,
        **alleles,
    }


def primary_submit(label: str) -> bool:
    return st.form_submit_button(label, type="primary", width="stretch")


def alive(mice: pd.DataFrame) -> pd.DataFrame:
    return mice[mice["status"] != "Deceased"]


def mice_table(mice: pd.DataFrame) -> pd.DataFrame:
    current = today()
    return pd.DataFrame(
        {
            "Ear tag": mice["ear_tag"],
            "Cre / p53 / Brca2": mice.apply(logic.genotype_label, axis=1) if len(mice) else [],
            "Sex": mice["sex"],
            "Weeks": [(logic.age_days(dob, current) or 0) // 7 for dob in mice["dob"]],
            "Status": mice["status"],
            "Cage": mice["cage"],
            "Cohort": mice["cohort"],
            "Genotyping": mice["genotyping_status"],
            "Born": mice["dob"],
        }
    )


@st.cache_data(max_entries=200, show_spinner=False)
def _load_gel_file(path: str, _store: Store) -> bytes | None:
    return _store.read_file(path)


def gel_card(ctx: Context, gel) -> None:
    """One gel: its image or PDF plus its details."""
    st.markdown(f"**{gel['gel_id']}** · {gel['date_run']} · {gel['locus']} · {gel['result']}")
    try:
        content = _load_gel_file(gel["file"], ctx.store)
    except StoreError as exc:
        st.warning(str(exc))
        content = None
    if content is None:
        st.warning("The gel file couldn't be loaded.")
    elif gel["file_type"] == "pdf":
        st.download_button(
            "Download PDF",
            content,
            file_name=f"{gel['gel_id']}.pdf",
            mime="application/pdf",
            key=f"pdf_{gel['gel_id']}",
        )
    else:
        st.image(content, width="stretch")
    details = [
        ("Mice", gel["mice"].replace(";", ", ")),
        ("Primers", gel["primers"]),
        ("Bands", gel["bands"]),
        ("Notes", gel["notes"]),
    ]
    if gel["uploaded_by"] != auth.SHARED_USER:
        details.append(("Uploaded by", gel["uploaded_by"]))
    st.text("\n".join(f"{label}: {value}" for label, value in details if value))
