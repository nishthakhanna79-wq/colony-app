from __future__ import annotations

from datetime import timedelta

import streamlit as st

from .. import logic, ops, ui
from ..schema import today
from ..store import Tables


def _add(ctx: ui.Context) -> None:
    round_id = ui.form_round("add_task")
    with st.form(f"add_task_{round_id}"):
        title = st.text_input("What needs doing", key=f"task_title_{round_id}")
        first, second = st.columns(2)
        with first:
            due = st.date_input(
                "Due",
                value=today() + timedelta(days=1),
                min_value=ui.EARLIEST_DATE,
                format="YYYY-MM-DD",
                key=f"task_due_{round_id}",
            )
        with second:
            who = st.text_input("Who (optional)", key=f"task_who_{round_id}")
        about = st.text_input("About (optional)", placeholder="Ear tag, cage or pair", key=f"task_about_{round_id}")
        notes = st.text_input("Notes", key=f"task_notes_{round_id}")
        submitted = ui.primary_submit("Save task")
    if submitted and ui.save(
        ctx,
        "Add task",
        lambda tx: ops.add_task(tx, title, due, who, about, notes),
        "Saved the task.",
    ):
        ui.next_round("add_task")
        st.rerun()


def _open(ctx: ui.Context, tables: Tables) -> None:
    tasks = logic.open_tasks(tables, today())
    if tasks.empty:
        st.info("No open tasks. Choose Add to create one.")
        return
    st.caption("Tick a task to mark it done.")
    for _, task in tasks.iterrows():
        extra = " · ".join(part for part in (task["When"], task["Who"], task["About"]) if part)
        done = st.checkbox(f"{task['What']}", key=f"task_done_{task['Task']}", help=None)
        st.caption(f"Due {task['Due']} · {extra}")
        if done:
            if ui.save(ctx, f"Finish task {task['Task']}", lambda tx: ops.set_task_done(tx, task["Task"], True), "Marked the task done."):
                st.session_state.pop(f"task_done_{task['Task']}", None)
                st.rerun()


def _done(ctx: ui.Context, tables: Tables) -> None:
    done = tables["tasks"][tables["tasks"]["status"] == "Done"].sort_values("done_on", ascending=False)
    if done.empty:
        st.info("No finished tasks yet.")
        return
    ui.table(
        done[["title", "due_date", "done_on", "who", "about"]].rename(
            columns={"title": "What", "due_date": "Due", "done_on": "Done", "who": "Who", "about": "About"}
        )
    )
    labels = dict(zip(done["task_id"], done["title"]))
    task_id = st.selectbox(
        "Change a finished task",
        list(labels),
        index=None,
        format_func=lambda value: labels.get(value, value),
        placeholder="Choose a task",
        key="task_change",
    )
    if task_id:
        first, second = st.columns(2)
        with first:
            if st.button("Reopen", key=f"task_reopen_{task_id}", width="stretch") and ui.save(
                ctx, f"Reopen task {task_id}", lambda tx: ops.set_task_done(tx, task_id, False), "Reopened the task."
            ):
                st.session_state.pop("task_change", None)
                st.rerun()
        with second:
            if st.button("Delete", key=f"task_delete_{task_id}", width="stretch") and ui.save(
                ctx, f"Delete task {task_id}", lambda tx: ops.delete_task(tx, task_id), "Deleted the task."
            ):
                st.session_state.pop("task_change", None)
                st.rerun()


def _calendar(tables: Tables) -> None:
    days = st.segmented_control("Show", [14, 30, 90], default=30, format_func=lambda n: f"{n} days", key="calendar_days") or 30
    frame = logic.agenda(tables, ops.get_settings(tables), today(), days=days)
    if frame.empty:
        st.info("Nothing coming up in that time.")
    else:
        ui.table(frame)
    st.caption("Open tasks, weaning, expected litters and age thresholds, by date. Overdue items stay listed.")


def render(ctx: ui.Context) -> None:
    tables = ctx.store.snapshot()
    st.title("Tasks")
    ui.show_flash()
    mode = st.segmented_control(
        "View", ["To do", "Calendar", "Add", "Finished"], default="To do", label_visibility="collapsed", key="tasks_mode"
    )
    if mode == "Add":
        _add(ctx)
    elif mode == "Calendar":
        _calendar(tables)
    elif mode == "Finished":
        _done(ctx, tables)
    else:
        _open(ctx, tables)
