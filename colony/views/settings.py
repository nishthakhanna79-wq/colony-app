from __future__ import annotations

import pandas as pd
import streamlit as st

from .. import auth, imports, ops, ui
from ..schema import DEFAULT_SETTINGS, SETTING_LABELS


def _thresholds(ctx: ui.Context, tables) -> None:
    current = ops.get_settings(tables)
    with st.form("settings"):
        values = {
            key: st.number_input(SETTING_LABELS[key], min_value=1, max_value=2000, value=current[key], step=1)
            for key in DEFAULT_SETTINGS
        }
        submitted = ui.primary_submit("Save settings")
    if submitted and ui.save(ctx, "Update settings", lambda tx: ops.save_settings(tx, values), "Saved settings."):
        st.rerun()


def _import(ctx: ui.Context, tables) -> None:
    round_id = ui.form_round("import")
    st.caption("Upload your colony sheet as Excel or CSV. You'll see what will happen before anything is saved.")
    upload = st.file_uploader("Spreadsheet", type=["xlsx", "csv"], key=f"import_file_{round_id}")
    if not upload:
        return
    try:
        frame = imports.read_spreadsheet(upload.name, upload.getvalue())
    except ops.ValidationError as exc:
        st.error(str(exc))
        return

    st.markdown(f"**{len(frame)} rows found. Match your columns:**")
    guesses = imports.guess_columns(list(frame.columns))
    options = list(frame.columns)
    mapping: dict[str, str | None] = {}
    fields = list(imports.FIELD_LABELS.items())
    for start in range(0, len(fields), 2):  # Row by row, so the order holds when a phone stacks them.
        for column, (name, label) in zip(st.columns(2), fields[start : start + 2]):
            with column:
                mapping[name] = st.selectbox(
                    label,
                    options,
                    index=options.index(guesses[name]) if guesses.get(name) in options else None,
                    placeholder="Not in my sheet",
                    key=f"import_map_{name}_{round_id}",
                )

    order = imports.GENOTYPE_ORDERS["Cre / p53 / Brca2"]
    if mapping.get("genotype"):
        order_name = st.radio(
            "Order of the alleles in your genotype column",
            list(imports.GENOTYPE_ORDERS),
            horizontal=True,
            key=f"import_order_{round_id}",
        )
        order = imports.GENOTYPE_ORDERS[order_name]

    if not mapping.get("ear_tag"):
        st.info("Choose which column holds the ear tag.")
        return

    preview = imports.preview_import(tables, frame, mapping, order, ctx.user)
    with st.container(horizontal=True):
        st.metric("Ready to add", len(preview.added), border=True)
        st.metric("Rows with problems", len(preview.problems), border=True)
        st.metric("Notes", len(preview.warnings), border=True)
    if preview.problems:
        st.warning("These rows will be left out. Fix them in your sheet and upload it again to add them.")
        ui.table(pd.DataFrame(preview.problems))
    if preview.warnings:
        st.markdown("**These mice will be added with a parent left blank:**")
        ui.table(pd.DataFrame(preview.warnings))
    if preview.added and st.button(f"Add {len(preview.added)} mice", type="primary", key=f"import_go_{round_id}"):
        count = {"added": 0}

        def change(tx):
            count["added"] = len(imports.import_mice(tx, frame, mapping, order, ctx.user).added)

        if ui.save(ctx, f"Import mice from {upload.name}", change, ""):
            st.session_state["flash"] = f"Added {count['added']} mice."
            ui.next_round("import")
            st.rerun()


def _export(tables) -> None:
    st.caption("Everything except gel files, as one Excel file with a sheet for each kind of record.")
    st.download_button(
        "Download Excel file",
        imports.export_workbook(tables),
        file_name=imports.export_file_name(),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )


def render(ctx: ui.Context) -> None:
    tables = ctx.store.snapshot()
    st.title("Settings")
    ui.show_flash()
    mode = st.segmented_control(
        "View",
        ["Alerts", "Import", "Export"],
        default="Alerts",
        label_visibility="collapsed",
        key="settings_mode",
    )
    if mode == "Import":
        _import(ctx, tables)
    elif mode == "Export":
        _export(tables)
    else:
        _thresholds(ctx, tables)

    github = auth.secret_section("github")
    if github:
        branch = github.get("branch", ui.DATA_BRANCH)
        st.markdown(f"[Full change history on GitHub](https://github.com/{github['repo']}/commits/{branch})")
