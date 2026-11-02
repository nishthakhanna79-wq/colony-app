from __future__ import annotations

import streamlit as st

from .. import auth, ops, ui
from ..schema import DEFAULT_SETTINGS, SETTING_LABELS


def render(ctx: ui.Context) -> None:
    tables = ctx.store.snapshot()
    current = ops.get_settings(tables)
    st.title("Settings")
    ui.show_flash()

    with st.form("settings"):
        values = {
            key: st.number_input(SETTING_LABELS[key], min_value=1, max_value=2000, value=current[key], step=1)
            for key in DEFAULT_SETTINGS
        }
        submitted = ui.primary_submit("Save settings")
    if submitted and ui.save(ctx, "Update settings", lambda tx: ops.save_settings(tx, values), "Saved settings."):
        st.rerun()

    github = auth.secret_section("github")
    if github:
        branch = github.get("branch", ui.DATA_BRANCH)
        st.markdown(f"[Full change history on GitHub](https://github.com/{github['repo']}/commits/{branch})")
