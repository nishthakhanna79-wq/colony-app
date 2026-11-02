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
    first, second = st.columns(2)
    with first:
        view = st.segmented_control("Show", ["Ancestors", "Progeny"], default="Ancestors", key="pedigree_view") or "Ancestors"
    with second:
        generations = st.selectbox("Generations", list(range(1, 11)), index=1, key="pedigree_generations")
    _legend()
    if tag:
        if view == "Progeny":
            count = logic.count_descendants(mice, tag, generations)
            if count:
                st.caption(f"{count} descendant{'s' if count != 1 else ''} in {generations} generation{'s' if generations != 1 else ''}.")
                st.graphviz_chart(logic.progeny_dot(mice, tag, generations), width="stretch")
            else:
                st.info(f"{tag} has no recorded offspring.")
            return
        st.graphviz_chart(logic.pedigree_dot(mice, tag, generations), width="stretch")
        problems = logic.inheritance_problems(mice)
        mine = problems[problems["Ear tag"] == tag]
        if len(mine):
            st.warning(f"{tag} has alleles that don't fit its recorded parents.")
            ui.table(mine)
