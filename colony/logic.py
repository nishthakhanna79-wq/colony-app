"""Read-only calculations: genotype labels, inheritance checks, ratios, alerts, pedigree."""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from .schema import LOCI, LOCUS_LABELS, PENDING
from .store import Tables

FLOX_DOSE = {"hom": 1.0, "het": 0.5, "WT": 0.0}
CHI2_CRITICAL_05 = {1: 3.841, 2: 5.991}
MIN_EXPECTED = 5
ALIVE_EXCLUDED = {"Deceased"}

# Allele colors used in the pedigree. Text is always shown too, so color is never the only cue.
ALLELE_COLORS = {
    "hom": ("#0F5C56", "#FFFFFF"),
    "het": ("#8FCFC3", "#0B3B37"),
    "WT": ("#F4F1E6", "#333329"),
    "P": ("#6B3A8E", "#FFFFFF"),
    "N": ("#F4F1E6", "#333329"),
    PENDING: ("#D5D3C8", "#333329"),
}


def _day_count(days: int) -> str:
    return f"{days} day" if days == 1 else f"{days} days"


def genotype_label(row) -> str:
    return " / ".join(str(row[locus]) for locus in LOCI)


def parse_iso(value: str) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def age_days(dob: str, today: date) -> int | None:
    parsed = parse_iso(dob)
    return None if parsed is None else (today - parsed).days


def expected_offspring(locus: str, mother: str, father: str) -> dict[str, float] | None:
    """Expected offspring fractions, or None when the cross doesn't set a prediction.

    Cre: a P parent could carry one copy or two, so P x P predicts nothing. P x N
    assumes one copy, which is what the 1:1 expectation means.
    """
    if mother == PENDING or father == PENDING:
        return None
    if locus == "cre":
        if mother == "N" and father == "N":
            return {"P": 0.0, "N": 1.0}
        if mother == "P" and father == "P":
            return None
        return {"P": 0.5, "N": 0.5}
    a, b = FLOX_DOSE[mother], FLOX_DOSE[father]
    return {
        "hom": a * b,
        "het": a * (1 - b) + b * (1 - a),
        "WT": (1 - a) * (1 - b),
    }


def inheritance_problems(mice: pd.DataFrame) -> pd.DataFrame:
    """Mice whose recorded allele can't come from their recorded parents."""
    columns = ["Ear tag", "Allele", "Mouse", "Mother", "Father"]
    by_tag = {row["ear_tag"]: row for _, row in mice.iterrows()}
    rows = []
    for _, mouse in mice.iterrows():
        mother = by_tag.get(mouse["mother"])
        father = by_tag.get(mouse["father"])
        if mother is None or father is None:
            continue
        for locus in LOCI:
            if mouse[locus] == PENDING:
                continue
            expected = expected_offspring(locus, mother[locus], father[locus])
            if expected is not None and expected.get(mouse[locus], 0.0) == 0.0:
                rows.append(
                    {
                        "Ear tag": mouse["ear_tag"],
                        "Allele": LOCUS_LABELS[locus],
                        "Mouse": mouse[locus],
                        "Mother": mother[locus],
                        "Father": father[locus],
                    }
                )
    return pd.DataFrame(rows, columns=columns)


def ratio_tracker(mice: pd.DataFrame) -> pd.DataFrame:
    """Observed vs. expected offspring per allele and cross, pooled across litters."""
    by_tag = {row["ear_tag"]: row for _, row in mice.iterrows()}
    counts: dict[tuple[str, tuple[str, str]], dict[str, int]] = {}
    for _, pup in mice.iterrows():
        mother = by_tag.get(pup["mother"])
        father = by_tag.get(pup["father"])
        if mother is None or father is None:
            continue
        for locus in LOCI:
            if pup[locus] == PENDING:
                continue
            if expected_offspring(locus, mother[locus], father[locus]) is None:
                continue
            cross = tuple(sorted((mother[locus], father[locus])))
            tally = counts.setdefault((locus, cross), {})
            tally[pup[locus]] = tally.get(pup[locus], 0) + 1

    rows = []
    for (locus, cross), tally in counts.items():
        expected = expected_offspring(locus, cross[0], cross[1])
        possible = [genotype for genotype, fraction in expected.items() if fraction > 0]
        total = sum(tally.values())
        observed_text = ", ".join(f"{g} {tally.get(g, 0)}" for g in expected if g in possible or tally.get(g))
        expected_text = ", ".join(f"{g} {expected[g] * total:.1f}" for g in possible)
        degrees = len(possible) - 1
        if degrees == 0:
            result = "One outcome expected"
        elif min(expected[g] * total for g in possible) < MIN_EXPECTED:
            result = "Too few pups"
        else:
            chi2 = sum((tally.get(g, 0) - expected[g] * total) ** 2 / (expected[g] * total) for g in possible)
            result = "Skewed" if chi2 > CHI2_CRITICAL_05[degrees] else "OK"
        rows.append(
            {
                "Allele": LOCUS_LABELS[locus],
                "Cross": f"{cross[0]} x {cross[1]}",
                "Pups": total,
                "Observed": observed_text,
                "Expected": expected_text,
                "Result": result,
            }
        )
    # Result comes early so it's visible on a phone without scrolling the table sideways.
    frame = pd.DataFrame(rows, columns=["Allele", "Cross", "Result", "Pups", "Observed", "Expected"])
    return frame.sort_values(["Allele", "Cross"]).reset_index(drop=True)


def pair_summary(tables: Tables, today: date, gap_weeks: int) -> pd.DataFrame:
    pairs, litters = tables["pairs"], tables["litters"]
    gap_start = today - timedelta(weeks=gap_weeks)
    rows = []
    for _, pair in pairs.iterrows():
        mine = litters[litters["pair_id"] == pair["pair_id"]]
        dates = [d for d in (parse_iso(v) for v in mine["dob"]) if d]
        last = max(dates, default=None)
        started = parse_iso(pair["start_date"])
        reference = last or started
        quiet = pair["status"] == "Active" and reference is not None and reference <= gap_start
        rows.append(
            {
                "Pair": pair["pair_id"],
                "Male": pair["male"],
                "Female": pair["female"],
                "Cage": pair["cage"],
                "Started": pair["start_date"],
                "Status": pair["status"],
                "Litters": len(dates),
                f"Last {gap_weeks} weeks": sum(1 for d in dates if d > gap_start),
                "Last litter": last.isoformat() if last else "",
                "Flag": "No recent litter" if quiet else "",
            }
        )
    columns = ["Pair", "Male", "Female", "Cage", "Started", "Status", "Litters", f"Last {gap_weeks} weeks", "Last litter", "Flag"]
    return pd.DataFrame(rows, columns=columns)


PLUG_WINDOW_DAYS = 5  # A litter this close to the expected date is taken to come from the plug.
PLUG_OVERDUE_DAYS = 7  # Past this, a plug is treated as not leading to a litter.


def plug_table(tables: Tables, settings: dict[str, int], today: date) -> pd.DataFrame:
    """Each plug with its expected birth date and what became of it, newest first."""
    columns = ["Plug", "Pair", "Male", "Female", "Cage", "Plug date", "Expected birth", "Status", "_open", "_days"]
    pairs = {row["pair_id"]: row for _, row in tables["pairs"].iterrows()}
    rows = []
    for _, plug in tables["plugs"].iterrows():
        plugged = parse_iso(plug["plug_date"])
        pair = pairs.get(plug["pair_id"])
        if plugged is None or pair is None:
            continue
        expected = plugged + timedelta(days=settings["gestation_days"])
        window_start = expected - timedelta(days=PLUG_WINDOW_DAYS)
        window_end = expected + timedelta(days=PLUG_WINDOW_DAYS)
        litter_id = ""
        for _, litter in tables["litters"][tables["litters"]["pair_id"] == plug["pair_id"]].iterrows():
            born = parse_iso(litter["dob"])
            if born and window_start <= born <= window_end:
                litter_id = litter["litter_id"]
        days = (expected - today).days
        is_open = False
        if litter_id:
            status = f"Litter {litter_id}"
        elif days < -PLUG_OVERDUE_DAYS:
            status = "No litter recorded"
        else:
            is_open = True
            status = f"Due in {_day_count(days)}" if days > 0 else ("Due today" if days == 0 else f"{_day_count(-days)} overdue")
        rows.append(
            {
                "Plug": plug["plug_id"],
                "Pair": plug["pair_id"],
                "Male": pair["male"],
                "Female": pair["female"],
                "Cage": pair["cage"],
                "Plug date": plug["plug_date"],
                "Expected birth": expected.isoformat(),
                "Status": status,
                "_open": is_open,
                "_days": days,
            }
        )
    frame = pd.DataFrame(rows, columns=columns)
    return frame.sort_values("Plug date", ascending=False).reset_index(drop=True)


def breeders_to_replace(tables: Tables, settings: dict[str, int], today: date) -> pd.DataFrame:
    """Mice in active pairs that are past the replacement age, or that have died."""
    by_tag = {row["ear_tag"]: row for _, row in tables["mice"].iterrows()}
    rows = []
    for _, pair in tables["pairs"][tables["pairs"]["status"] == "Active"].iterrows():
        for tag in (pair["male"], pair["female"]):
            mouse = by_tag.get(tag)
            if mouse is None:
                continue
            age = age_days(mouse["dob"], today)
            if mouse["status"] == "Deceased":
                reason = "Deceased"
            elif age is not None and age >= settings["breeder_replace_days"]:
                reason = "Age"
            else:
                continue
            rows.append({"Ear tag": tag, "Pair": pair["pair_id"], "Cage": pair["cage"], "Age (days)": age, "Reason": reason})
    return pd.DataFrame(rows, columns=["Ear tag", "Pair", "Cage", "Age (days)", "Reason"])


def _when(days: int) -> str:
    if days == 0:
        return "Today"
    return f"In {_day_count(days)}" if days > 0 else f"{_day_count(-days)} overdue"


def open_tasks(tables: Tables, today: date) -> pd.DataFrame:
    """Open tasks, soonest first, with how far away each is."""
    columns = ["Task", "What", "Due", "When", "Who", "About", "_days"]
    rows = []
    for _, task in tables["tasks"][tables["tasks"]["status"] == "Open"].iterrows():
        due = parse_iso(task["due_date"])
        days = (due - today).days if due else 0
        rows.append(
            {
                "Task": task["task_id"],
                "What": task["title"],
                "Due": task["due_date"],
                "When": _when(days),
                "Who": task["who"],
                "About": task["about"],
                "_days": days,
            }
        )
    frame = pd.DataFrame(rows, columns=columns)
    return frame.sort_values("_days").reset_index(drop=True)


def agenda(tables: Tables, settings: dict[str, int], today: date, days: int = 30) -> pd.DataFrame:
    """Everything coming up, by date: tasks, weaning, expected litters and age thresholds."""
    columns = ["Date", "When", "Type", "Detail"]
    rows = []

    def add(moment: date, kind: str, detail: str) -> None:
        rows.append({"Date": moment.isoformat(), "When": _when((moment - today).days), "Type": kind, "Detail": detail})

    horizon = today + timedelta(days=days)
    for _, task in open_tasks(tables, today).iterrows():
        due = parse_iso(task["Due"])
        if due and due <= horizon:
            who = f" ({task['Who']})" if task["Who"] else ""
            add(due, "Task", f"{task['What']}{who}")
    for _, litter in tables["litters"].iterrows():
        born = parse_iso(litter["dob"])
        if born is None or litter["weaning_date"] or litter["status"] != "Pending genotyping":
            continue
        due = born + timedelta(days=settings["wean_age_days"])
        if due <= horizon:
            add(due, "Wean", f"Litter {litter['litter_id']}, pair {litter['pair_id']}")
    plugs = plug_table(tables, settings, today)
    for _, plug in plugs[plugs["_open"].astype(bool)].iterrows():
        expected = parse_iso(plug["Expected birth"])
        if expected and expected <= horizon:
            add(expected, "Expected litter", f"Pair {plug['Pair']}, cage {plug['Cage']}")
    for _, mouse in tables["mice"].iterrows():
        born = parse_iso(mouse["dob"])
        if born is None or mouse["status"] not in ("Unassigned", "Experimental"):
            continue
        reaches = born + timedelta(days=settings["age_threshold_days"])
        if today <= reaches <= horizon:
            add(reaches, "Age threshold", f"Mouse {mouse['ear_tag']}, cage {mouse['cage'] or 'none'}")
    frame = pd.DataFrame(rows, columns=columns)
    return frame.sort_values("Date").reset_index(drop=True)


@dataclass
class Alerts:
    wean: pd.DataFrame
    quiet_pairs: pd.DataFrame
    age: pd.DataFrame
    genotyping: pd.DataFrame
    expected: pd.DataFrame
    replace: pd.DataFrame
    tasks: pd.DataFrame


def build_alerts(tables: Tables, settings: dict[str, int], today: date) -> Alerts:
    wean_rows = []
    for _, litter in tables["litters"].iterrows():
        dob = parse_iso(litter["dob"])
        if dob is None or litter["weaning_date"] or litter["status"] != "Pending genotyping":
            continue
        due = dob + timedelta(days=settings["wean_age_days"])
        days = (due - today).days
        if days <= 7:
            wean_rows.append(
                {
                    "Litter": litter["litter_id"],
                    "Pair": litter["pair_id"],
                    "Born": litter["dob"],
                    "Wean by": due.isoformat(),
                    "When": _when(days),
                    "_days": days,
                }
            )
    wean = pd.DataFrame(wean_rows, columns=["Litter", "Pair", "Born", "Wean by", "When", "_days"])
    wean = wean.sort_values("_days").drop(columns="_days").reset_index(drop=True)

    pairs = pair_summary(tables, today, settings["litter_gap_weeks"])
    quiet = pairs[pairs["Flag"] != ""][["Pair", "Male", "Female", "Cage", "Started", "Last litter"]].reset_index(drop=True)

    age_rows, genotyping_rows = [], []
    threshold = settings["age_threshold_days"]
    for _, mouse in tables["mice"].iterrows():
        age = age_days(mouse["dob"], today)
        if age is None or mouse["status"] in ALIVE_EXCLUDED:
            continue
        if mouse["status"] in ("Unassigned", "Experimental") and age >= threshold - 14:
            left = threshold - age
            age_rows.append(
                {
                    "Ear tag": mouse["ear_tag"],
                    "Genotype": genotype_label(mouse),
                    "Cage": mouse["cage"],
                    "Age (days)": age,
                    "When": f"In {_day_count(left)}" if left > 0 else "Reached",
                }
            )
        if mouse["genotyping_status"] == "Pending" and age > settings["genotyping_pending_days"]:
            genotyping_rows.append(
                {
                    "Ear tag": mouse["ear_tag"],
                    "Genotype": genotype_label(mouse),
                    "Cage": mouse["cage"],
                    "Age (days)": age,
                }
            )
    age_frame = pd.DataFrame(age_rows, columns=["Ear tag", "Genotype", "Cage", "Age (days)", "When"])
    age_frame = age_frame.sort_values("Age (days)", ascending=False).reset_index(drop=True)
    genotyping = pd.DataFrame(genotyping_rows, columns=["Ear tag", "Genotype", "Cage", "Age (days)"])
    genotyping = genotyping.sort_values("Age (days)", ascending=False).reset_index(drop=True)
    plugs = plug_table(tables, settings, today)
    upcoming = plugs[plugs["_open"].astype(bool) & (plugs["_days"].astype(int) <= 7)]
    expected = upcoming.sort_values("_days")[["Pair", "Cage", "Plug date", "Expected birth", "Status"]].reset_index(drop=True)
    tasks = open_tasks(tables, today)
    tasks = tasks[tasks["_days"].astype(int) <= 7][["What", "Due", "When", "Who", "About"]].reset_index(drop=True)
    return Alerts(
        wean=wean,
        quiet_pairs=quiet,
        age=age_frame,
        genotyping=genotyping,
        expected=expected,
        replace=breeders_to_replace(tables, settings, today),
        tasks=tasks,
    )


def cage_census(mice: pd.DataFrame, capacity: int) -> pd.DataFrame:
    housed = mice[(~mice["status"].isin(ALIVE_EXCLUDED)) & (mice["cage"] != "")]
    counts = housed.groupby("cage").size().reset_index(name="Mice").rename(columns={"cage": "Cage"})
    counts["Capacity"] = capacity
    counts["Flag"] = counts["Mice"].map(lambda n: "Over capacity" if n > capacity else "")
    return counts.sort_values("Cage").reset_index(drop=True)


def cage_list(tables: Tables, capacity: int) -> pd.DataFrame:
    """Each occupied cage: who's in it, whether it holds a breeding pair, and whether it's full."""
    mice = tables["mice"]
    housed = mice[(~mice["status"].isin(ALIVE_EXCLUDED)) & (mice["cage"] != "")]
    pairs = tables["pairs"]
    active = pairs[pairs["status"] == "Active"]
    pair_by_cage = dict(zip(active["cage"], active["pair_id"]))
    rows = []
    for cage, group in housed.groupby("cage"):
        count = len(group)
        rows.append(
            {
                "Cage": cage,
                "Mice": count,
                "F": int((group["sex"] == "F").sum()),
                "M": int((group["sex"] == "M").sum()),
                "Pair": pair_by_cage.get(cage, ""),
                "Flag": "Over capacity" if count > capacity else "",
            }
        )
    frame = pd.DataFrame(rows, columns=["Cage", "Mice", "F", "M", "Pair", "Flag"])
    return frame.sort_values("Cage", key=lambda s: s.str.len() * 1000).reset_index(drop=True) if len(frame) else frame


def tumor_table(tables: Tables) -> pd.DataFrame:
    """Tumor records, newest first, with genotype and age filled in when the mouse is known."""
    # The answers people look for come first, so they show on a phone without scrolling sideways.
    columns = ["Ear tag", "In breeding", "Outcome", "Outcome date", "Noticed", "Age (weeks)", "Cre / p53 / Brca2", "Notes"]
    by_tag = {row["ear_tag"]: row for _, row in tables["mice"].iterrows()}
    rows = []
    for _, tumor in tables["tumors"].iterrows():
        mouse = by_tag.get(tumor["ear_tag"])
        noticed = parse_iso(tumor["noticed_on"])
        age = age_days(mouse["dob"], noticed) if mouse is not None and noticed else None
        rows.append(
            {
                "Ear tag": tumor["ear_tag"],
                "Cre / p53 / Brca2": genotype_label(mouse) if mouse is not None else "Not in Mice list",
                "Noticed": tumor["noticed_on"],
                "Age (weeks)": "" if age is None else str(age // 7),
                "In breeding": tumor["in_breeding"],
                "Outcome": tumor["outcome"],
                "Outcome date": tumor["outcome_date"],
                "Notes": tumor["notes"],
            }
        )
    frame = pd.DataFrame(rows, columns=columns)
    return frame.sort_values("Noticed", ascending=False).reset_index(drop=True)


def litter_breakdown(mice: pd.DataFrame, litter_id: str) -> dict:
    pups = mice[mice["litter_id"] == litter_id]
    genotypes = pups.apply(genotype_label, axis=1).value_counts().to_dict() if len(pups) else {}
    return {
        "recorded": len(pups),
        "female": int((pups["sex"] == "F").sum()),
        "male": int((pups["sex"] == "M").sum()),
        "genotypes": genotypes,
    }


def _pedigree_node(node_id: str, mouse, flagged: set[str], is_focus: bool) -> str:
    tag = html.escape(str(mouse["ear_tag"]))
    sex = "female" if mouse["sex"] == "F" else "male"
    border_color = "#B3261E" if mouse["ear_tag"] in flagged else ("#1B1F1E" if is_focus else "#8A887C")
    border = 3 if mouse["ear_tag"] in flagged or is_focus else 1
    warning = " (check)" if mouse["ear_tag"] in flagged else ""
    cells = ""
    for locus in LOCI:
        value = str(mouse[locus])
        fill, text = ALLELE_COLORS.get(value, ALLELE_COLORS[PENDING])
        cells += (
            f'<TD BGCOLOR="{fill}" CELLPADDING="4"><FONT COLOR="{text}" POINT-SIZE="10">'
            f"{LOCUS_LABELS[locus]}<BR/>{html.escape(value)}</FONT></TD>"
        )
    return (
        f'"{node_id}" [shape=plaintext, label=<'
        f'<TABLE BORDER="{border}" COLOR="{border_color}" CELLBORDER="0" CELLSPACING="1" BGCOLOR="#FFFFFF">'
        f'<TR><TD COLSPAN="3"><B>{tag}</B> {sex}{warning}</TD></TR><TR>{cells}</TR></TABLE>>];'
    )


def pedigree_dot(mice: pd.DataFrame, ear_tag: str, generations: int) -> str:
    """Graphviz source for a mouse and its ancestors, oldest generation on the left."""
    by_tag = {row["ear_tag"]: row for _, row in mice.iterrows()}
    flagged = set(inheritance_problems(mice)["Ear tag"])
    lines = [
        "digraph pedigree {",
        'rankdir=LR; bgcolor="transparent"; nodesep=0.25; ranksep=0.5;',
        'edge [color="#6F6D62", arrowsize=0.6];',
    ]
    seen: set[str] = set()
    edges: list[str] = []
    unknown = 0

    def visit(tag: str, depth: int) -> None:
        nonlocal unknown
        if tag in seen:
            return
        seen.add(tag)
        mouse = by_tag[tag]
        lines.append(_pedigree_node(tag, mouse, flagged, tag == ear_tag))
        if depth >= generations:
            return
        for parent_column, role in (("mother", "mother"), ("father", "father")):
            parent = mouse[parent_column]
            if parent in by_tag:
                visit(parent, depth + 1)
                edges.append(f'"{parent}" -> "{tag}";')
            elif tag == ear_tag:
                # Only the chosen mouse gets placeholders. On every ancestor they would
                # take up the width a phone needs for the mice themselves.
                unknown += 1
                node = f"unknown{unknown}"
                lines.append(
                    f'"{node}" [shape=box, style=dashed, color="#8A887C", fontcolor="#5F5E5A", '
                    f'fontsize=10, label="{role} not recorded"];'
                )
                edges.append(f'"{node}" -> "{tag}" [style=dashed];')

    visit(ear_tag, 0)
    return "\n".join(lines + edges + ["}"])


def progeny_dot(mice: pd.DataFrame, ear_tag: str, generations: int) -> str:
    """Graphviz source for a mouse and its descendants, oldest generation on the left."""
    children: dict[str, list[str]] = {}
    for _, mouse in mice.iterrows():
        for parent in (mouse["mother"], mouse["father"]):
            if parent:
                children.setdefault(parent, []).append(mouse["ear_tag"])
    by_tag = {row["ear_tag"]: row for _, row in mice.iterrows()}
    flagged = set(inheritance_problems(mice)["Ear tag"])
    lines = [
        "digraph progeny {",
        'rankdir=LR; bgcolor="transparent"; nodesep=0.25; ranksep=0.5;',
        'edge [color="#6F6D62", arrowsize=0.6];',
    ]
    seen = {ear_tag}
    lines.append(_pedigree_node(ear_tag, by_tag[ear_tag], flagged, True))
    edges: list[str] = []
    level = [ear_tag]
    for _ in range(generations):
        following = []
        for parent in level:
            for child in children.get(parent, []):
                edges.append(f'"{parent}" -> "{child}";')
                if child not in seen:
                    seen.add(child)
                    lines.append(_pedigree_node(child, by_tag[child], flagged, False))
                    following.append(child)
        level = following
    return "\n".join(lines + edges + ["}"])


def count_descendants(mice: pd.DataFrame, ear_tag: str, generations: int) -> int:
    return progeny_dot(mice, ear_tag, generations).count("<TABLE") - 1


def genotype_report(mice: pd.DataFrame) -> pd.DataFrame:
    """Living mice by genotype and sex, most common first."""
    living = mice[~mice["status"].isin(ALIVE_EXCLUDED)]
    rows = []
    if len(living):
        labelled = living.assign(_genotype=living.apply(genotype_label, axis=1))
        for genotype, group in labelled.groupby("_genotype"):
            rows.append(
                {
                    "Cre / p53 / Brca2": genotype,
                    "Total": len(group),
                    "F": int((group["sex"] == "F").sum()),
                    "M": int((group["sex"] == "M").sum()),
                }
            )
    frame = pd.DataFrame(rows, columns=["Cre / p53 / Brca2", "Total", "F", "M"])
    return frame.sort_values("Total", ascending=False).reset_index(drop=True)


AGE_BANDS = [(0, 3, "Under 4 weeks"), (4, 8, "4 to 8 weeks"), (9, 16, "9 to 16 weeks"), (17, 26, "17 to 26 weeks"), (27, 39, "27 to 39 weeks"), (40, 10_000, "40 weeks or more")]


def age_report(mice: pd.DataFrame, today: date) -> pd.DataFrame:
    """Living mice by age band."""
    living = mice[~mice["status"].isin(ALIVE_EXCLUDED)]
    weeks = [age // 7 for age in (age_days(dob, today) for dob in living["dob"]) if age is not None]
    rows = [{"Age": label, "Mice": sum(1 for w in weeks if low <= w <= high)} for low, high, label in AGE_BANDS]
    return pd.DataFrame(rows)


def pair_report(tables: Tables) -> pd.DataFrame:
    """Productivity of each pair, most pups first."""
    rows = []
    for _, pair in tables["pairs"].iterrows():
        litters = tables["litters"][tables["litters"]["pair_id"] == pair["pair_id"]]
        pups = [int(n) for n in litters["n_pups"] if str(n).isdigit()]
        dates = sorted(d for d in (parse_iso(v) for v in litters["dob"]) if d)
        gaps = [(later - earlier).days for earlier, later in zip(dates, dates[1:])]
        rows.append(
            {
                "Pair": pair["pair_id"],
                "Status": pair["status"],
                "Litters": len(litters),
                "Pups": sum(pups),
                "Pups per litter": round(sum(pups) / len(pups), 1) if pups else None,
                "Days between litters": round(sum(gaps) / len(gaps)) if gaps else None,
            }
        )
    columns = ["Pair", "Status", "Litters", "Pups", "Pups per litter", "Days between litters"]
    return pd.DataFrame(rows, columns=columns).sort_values("Pups", ascending=False).reset_index(drop=True)


def births_by_month(litters: pd.DataFrame) -> pd.DataFrame:
    """Litters and pups per month, newest first."""
    months: dict[str, dict[str, int]] = {}
    for _, litter in litters.iterrows():
        born = parse_iso(litter["dob"])
        if born is None:
            continue
        month = months.setdefault(born.strftime("%Y-%m"), {"Litters": 0, "Pups": 0})
        month["Litters"] += 1
        month["Pups"] += int(litter["n_pups"]) if str(litter["n_pups"]).isdigit() else 0
    rows = [{"Month": month, **counts} for month, counts in sorted(months.items(), reverse=True)]
    return pd.DataFrame(rows, columns=["Month", "Litters", "Pups"])


def tumor_report(tables: Tables) -> pd.DataFrame:
    """Tumor counts by genotype, with the age tumors were noticed. Uses every recorded mouse."""
    mice = tables["mice"]
    by_tag = {row["ear_tag"]: row for _, row in mice.iterrows()}
    totals: dict[str, int] = {}
    for _, mouse in mice.iterrows():
        label = genotype_label(mouse)
        totals[label] = totals.get(label, 0) + 1
    groups: dict[str, list[int | None]] = {}
    for _, tumor in tables["tumors"].iterrows():
        mouse = by_tag.get(tumor["ear_tag"])
        label = genotype_label(mouse) if mouse is not None else "Not in Mice list"
        noticed = parse_iso(tumor["noticed_on"])
        age = age_days(mouse["dob"], noticed) if mouse is not None and noticed else None
        groups.setdefault(label, []).append(None if age is None else age // 7)
    rows = []
    for label, ages in groups.items():
        known = sorted(age for age in ages if age is not None)
        middle = known[len(known) // 2] if len(known) % 2 else (known[len(known) // 2 - 1] + known[len(known) // 2]) / 2 if known else None
        rows.append(
            {
                "Cre / p53 / Brca2": label,
                "Tumor mice": len(ages),
                "Mice recorded": totals.get(label),
                "Median age at tumor (weeks)": middle,
            }
        )
    columns = ["Cre / p53 / Brca2", "Tumor mice", "Mice recorded", "Median age at tumor (weeks)"]
    return pd.DataFrame(rows, columns=columns).sort_values("Tumor mice", ascending=False).reset_index(drop=True)
