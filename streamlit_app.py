"""Colony: BRCA2 mouse colony management."""

import streamlit as st

from colony import auth, ui
from colony.backends import StoreError
from colony.views import cages, dashboard, gels, litters, mice, pairs, pedigree, settings, tasks, tumors

st.set_page_config(page_title=auth.APP_NAME, layout="wide")
st.html(
    """<style>
    [data-testid="stMainBlockContainer"] { padding-top: 3.25rem; padding-bottom: 3rem; }
    h1 { font-size: 1.75rem !important; letter-spacing: -0.01em; }
    h2, h3 { font-size: 1.2rem !important; }
    [class*="st-key-alleles_"] > div { min-width: 0 !important; flex: 1 1 0 !important; }
    </style>"""
)

# The gate comes first. A logged-out visitor stops here, before any data is read.
user = auth.require_user()

try:
    store = ui.get_store()
    store.snapshot()
except StoreError as exc:
    st.title(auth.APP_NAME)
    st.error(str(exc))
    st.stop()

ctx = ui.Context(store=store, user=user)

page = st.navigation(
    [
        st.Page(lambda: dashboard.render(ctx), title="Dashboard", url_path="dashboard", default=True),
        st.Page(lambda: mice.render(ctx), title="Mice", url_path="mice"),
        st.Page(lambda: cages.render(ctx), title="Cages", url_path="cages"),
        st.Page(lambda: pairs.render(ctx), title="Pairs", url_path="pairs"),
        st.Page(lambda: litters.render(ctx), title="Litters", url_path="litters"),
        st.Page(lambda: tumors.render(ctx), title="Tumors", url_path="tumors"),
        st.Page(lambda: tasks.render(ctx), title="Tasks", url_path="tasks"),
        st.Page(lambda: pedigree.render(ctx), title="Pedigree", url_path="pedigree"),
        st.Page(lambda: gels.render(ctx), title="Gels", url_path="gels"),
        st.Page(lambda: settings.render(ctx), title="Settings", url_path="settings"),
    ],
    position="top",
)

if auth.login_configured():
    with st.container(horizontal=True, horizontal_alignment="right"):
        st.button("Log out", on_click=auth.log_out)

page.run()
