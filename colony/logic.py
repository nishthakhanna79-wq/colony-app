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


@dataclass
class Alerts:
    wean: pd.DataFrame
    quiet_pairs: pd.DataFrame
    age: pd.DataFrame
    genotyping: pd.DataFrame


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
                    "When": f"{-days} days overdue" if days < 0 else ("Today" if days == 0 else f"In {days} days"),
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
                    "When": f"In {left} days" if left > 0 else "Reached",
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
    return Alerts(wean=wean, quiet_pairs=quiet, age=age_frame, genotyping=genotyping)


def cage_census(mice: pd.DataFrame, capacity: int) -> pd.DataFrame:
    housed = mice[(~mice["status"].isin(ALIVE_EXCLUDED)) & (mice["cage"] != "")]
    counts = housed.groupby("cage").size().reset_index(name="Mice").rename(columns={"cage": "Cage"})
    counts["Capacity"] = capacity
    counts["Flag"] = counts["Mice"].map(lambda n: "Over capacity" if n > capacity else "")
    return counts.sort_values("Cage").reset_index(drop=True)


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
