from __future__ import annotations

import html

import streamlit as st

from .. import logic, ui


def _legend() -> None:
    chips = "".join(
        f'<span style="background:{fill};color:{text};padding:2px 8px;border:1px solid #8A887C;'
        f'border-radius:3px;margin:0 6px 6px 0;display:inline-block;font-size:0.8rem">{html.escape(value)}</span>'
        for value, (fill, text) in logic.ALLELE_COLORS.items()
    )
    st.html(f"<div>{chips}</div>")
    st.caption("Red border and (check): at least one allele can't come from the recorded parents.")


def render(ctx: ui.Context) -> None:
    tables = ctx.store.snapshot()
    mice = tables["mice"]
    st.title("Pedigree")
    ui.show_flash()
    if mice.empty:
        st.info("Add mice with parents to see family trees here.")
        return

    tag = st.selectbox(
        "Mouse",
        ui.sorted_tags(mice),
        index=None,
        format_func=ui.mouse_label(mice),
        placeholder="Type an ear tag",
        key="pedigree_mouse",
    )
    generations = st.segmented_control("Generations back", [1, 2, 3], default=2, key="pedigree_generations") or 2
    _legend()
    if tag:
        st.graphviz_chart(logic.pedigree_dot(mice, tag, generations), width="stretch")
        problems = logic.inheritance_problems(mice)
        mine = problems[problems["Ear tag"] == tag]
        if len(mine):
            st.warning(f"{tag} has alleles that don't fit its recorded parents.")
            ui.table(mine)
