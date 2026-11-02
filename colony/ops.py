"""Every change to colony data, with its validation. No Streamlit in this module."""

from __future__ import annotations

import io
import re
from datetime import date

import pandas as pd
from PIL import Image, ImageOps

from .schema import (
    BREEDING_CHOICES,
    CRE_VALUES,
    DEFAULT_SETTINGS,
    FLOX_VALUES,
    GEL_LOCI,
    GEL_RESULTS,
    GENOTYPING_STATUSES,
    LITTER_STATUSES,
    LOCI,
    LOCUS_LABELS,
    LOCUS_VALUES,
    MOUSE_STATUSES,
    PENDING,
    SEXES,
    TUMOR_OUTCOMES,
    now_iso,
    today,
)
from .store import Tables, Transaction

EAR_TAG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_SIDE = 2000


class ValidationError(Exception):
    """A problem with what the user entered. The message is written for them."""


def clean_text(value: object) -> str:
    """Trim text and stop Excel treating it as a formula when the CSV is opened."""
    text = "" if value is None else str(value).strip()
    if text and text[0] in "=+-@\t\r":
        text = "'" + text
    return text


def clean_date(value: object, label: str, required: bool = True, allow_future: bool = False) -> str:
    if value in (None, ""):
        if required:
            raise ValidationError(f"{label} is required.")
        return ""
    if isinstance(value, date):
        parsed = value
    else:
        try:
            parsed = date.fromisoformat(str(value))
        except ValueError as exc:
            raise ValidationError(f"{label} is not a valid date.") from exc
    if not allow_future and parsed > today():
        raise ValidationError(f"{label} can't be in the future.")
    return parsed.isoformat()


def clean_ear_tag(value: object) -> str:
    tag = "" if value is None else str(value).strip()
    if not tag:
        raise ValidationError("Ear tag is required.")
    if not EAR_TAG_PATTERN.match(tag):
        raise ValidationError(
            f"Ear tag '{tag}' can only use letters, numbers, dots, dashes and underscores."
        )
    return tag


def _choice(value: object, allowed: list[str], label: str) -> str:
    if value not in allowed:
        raise ValidationError(f"{label} must be one of: {', '.join(allowed)}.")
    return str(value)


def _next_id(series: pd.Series, prefix: str, width: int) -> str:
    numbers = [int(v[len(prefix):]) for v in series if str(v).startswith(prefix) and str(v)[len(prefix):].isdigit()]
    return f"{prefix}{(max(numbers, default=0) + 1):0{width}d}"


def _row_index(frame: pd.DataFrame, column: str, value: str, label: str) -> int:
    matches = frame.index[frame[column] == value]
    if len(matches) == 0:
        raise ValidationError(f"{label} '{value}' was not found.")
    return int(matches[0])


def _append(frame: pd.DataFrame, row: dict[str, str]) -> pd.DataFrame:
    full = {column: row.get(column, "") for column in frame.columns}
    return pd.concat([frame, pd.DataFrame([full], dtype=str)], ignore_index=True)


def _tag_exists(mice: pd.DataFrame, tag: str, ignore: str | None = None) -> bool:
    lowered = mice["ear_tag"].str.lower()
    if ignore is not None:
        lowered = lowered[mice["ear_tag"] != ignore]
    return tag.lower() in set(lowered)


def _check_parent(mice: pd.DataFrame, parent: str, sex: str, label: str, child: str, child_dob: str) -> None:
    if not parent:
        return
    if parent == child:
        raise ValidationError(f"{child} can't be its own {label.lower()}.")
    index = _row_index(mice, "ear_tag", parent, label)
    row = mice.loc[index]
    if row["sex"] != sex:
        raise ValidationError(f"{label} {parent} is recorded as sex {row['sex']}.")
    if row["dob"] and child_dob and row["dob"] >= child_dob:
        raise ValidationError(f"{label} {parent} must be born before {child}.")


def _clean_mouse(mice: pd.DataFrame, data: dict, actor: str, existing_tag: str | None) -> dict[str, str]:
    tag = clean_ear_tag(data.get("ear_tag"))
    if _tag_exists(mice, tag, ignore=existing_tag):
        raise ValidationError(f"Ear tag {tag} is already in use.")
    dob = clean_date(data.get("dob"), "Date of birth")
    row = {
        "ear_tag": tag,
        "sex": _choice(data.get("sex"), SEXES, "Sex"),
        "dob": dob,
        "status": _choice(data.get("status", "Unassigned"), MOUSE_STATUSES, "Status"),
        "cage": clean_text(data.get("cage")),
        "mother": clean_text(data.get("mother")),
        "father": clean_text(data.get("father")),
        "litter_id": clean_text(data.get("litter_id")),
        "cohort": clean_text(data.get("cohort")),
        "notes": clean_text(data.get("notes")),
        "updated_by": actor,
        "updated_at": now_iso(),
    }
    for locus in LOCI:
        row[locus] = _choice(data.get(locus, PENDING), LOCUS_VALUES[locus], LOCUS_LABELS[locus])
    _check_parent(mice, row["mother"], "F", "Mother", tag, dob)
    _check_parent(mice, row["father"], "M", "Father", tag, dob)
    row.update(_genotyping_fields(row, data))
    return row


def _genotyping_fields(row: dict[str, str], data: dict) -> dict[str, str]:
    status = _choice(data.get("genotyping_status", "Pending"), GENOTYPING_STATUSES, "Genotyping status")
    if status == "Pending":
        return {"genotyping_status": status, "genotype_confirmed_on": ""}
    missing = [LOCUS_LABELS[locus] for locus in LOCI if row[locus] == PENDING]
    if missing:
        raise ValidationError(
            f"{row['ear_tag']} can't be confirmed while {', '.join(missing)} is still TBD."
        )
    confirmed = clean_date(data.get("genotype_confirmed_on") or today(), "Date confirmed")
    return {"genotyping_status": status, "genotype_confirmed_on": confirmed}


def _move(tables: Tables, ear_tag: str, to_cage: str, moved_on: str, actor: str) -> bool:
    mice = tables["mice"]
    index = _row_index(mice, "ear_tag", ear_tag, "Mouse")
    from_cage = mice.loc[index, "cage"]
    if from_cage == to_cage:
        return False
    mice.loc[index, "cage"] = to_cage
    mice.loc[index, "updated_by"] = actor
    mice.loc[index, "updated_at"] = now_iso()
    tables["moves"] = _append(
        tables["moves"],
        {"moved_on": moved_on, "ear_tag": ear_tag, "from_cage": from_cage, "to_cage": to_cage, "updated_by": actor},
    )
    return True


def move_mice(tx: Transaction, ear_tags: list[str], to_cage: object, moved_on: object, actor: str) -> int:
    if not ear_tags:
        raise ValidationError("Choose at least one mouse to move.")
    cage = clean_text(to_cage)
    if not cage:
        raise ValidationError("Enter the cage to move to.")
    on = clean_date(moved_on, "Date moved")
    return sum(_move(tx.tables, tag, cage, on, actor) for tag in ear_tags)


def add_mouse(tx: Transaction, data: dict, actor: str) -> str:
    mice = tx.tables["mice"]
    row = _clean_mouse(mice, data, actor, None)
    tx.tables["mice"] = _append(mice, row)
    return row["ear_tag"]


def update_mouse(tx: Transaction, ear_tag: str, data: dict, actor: str) -> str:
    tables = tx.tables
    mice = tables["mice"]
    index = _row_index(mice, "ear_tag", ear_tag, "Mouse")
    merged = {**mice.loc[index].to_dict(), **data}
    row = _clean_mouse(mice, merged, actor, ear_tag)
    new_cage = row.pop("cage")
    for column, value in row.items():
        mice.loc[index, column] = value
    new_tag = row["ear_tag"]
    if new_tag != ear_tag:
        _rename_references(tables, ear_tag, new_tag)
    _move(tables, new_tag, new_cage, today().isoformat(), actor)
    return new_tag


def _rename_references(tables: Tables, old: str, new: str) -> None:
    for column in ("mother", "father"):
        tables["mice"].loc[tables["mice"][column] == old, column] = new
    for column in ("male", "female"):
        tables["pairs"].loc[tables["pairs"][column] == old, column] = new
    tables["gels"]["mice"] = tables["gels"]["mice"].map(
        lambda linked: ";".join(new if tag == old else tag for tag in linked.split(";") if tag)
    ).astype(str)
    tables["tumors"].loc[tables["tumors"]["ear_tag"] == old, "ear_tag"] = new
    tables["moves"].loc[tables["moves"]["ear_tag"] == old, "ear_tag"] = new


def mouse_references(tables: Tables, ear_tag: str) -> list[str]:
    found = []
    mice = tables["mice"]
    if ((mice["mother"] == ear_tag) | (mice["father"] == ear_tag)).any():
        found.append("offspring")
    pairs = tables["pairs"]
    if ((pairs["male"] == ear_tag) | (pairs["female"] == ear_tag)).any():
        found.append("a breeding pair")
    if tables["gels"]["mice"].map(lambda linked: ear_tag in linked.split(";")).any():
        found.append("a gel record")
    if (tables["tumors"]["ear_tag"] == ear_tag).any():
        found.append("a tumor record")
    return found


def delete_mouse(tx: Transaction, ear_tag: str) -> None:
    index = _row_index(tx.tables["mice"], "ear_tag", ear_tag, "Mouse")
    references = mouse_references(tx.tables, ear_tag)
    if references:
        raise ValidationError(
            f"{ear_tag} is linked to {' and '.join(references)}, so it can't be deleted. "
            "Mark it Deceased or Retired instead."
        )
    tx.tables["mice"] = tx.tables["mice"].drop(index=index).reset_index(drop=True)
    moves = tx.tables["moves"]
    tx.tables["moves"] = moves[moves["ear_tag"] != ear_tag].reset_index(drop=True)


def add_pair(tx: Transaction, male: str, female: str, cage: object, start_date: object, notes: object, actor: str) -> str:
    tables = tx.tables
    mice, pairs = tables["mice"], tables["pairs"]
    if not male or not female:
        raise ValidationError("Choose a male and a female.")
    male_row = mice.loc[_row_index(mice, "ear_tag", male, "Male")]
    female_row = mice.loc[_row_index(mice, "ear_tag", female, "Female")]
    if male_row["sex"] != "M":
        raise ValidationError(f"{male} is not recorded as male.")
    if female_row["sex"] != "F":
        raise ValidationError(f"{female} is not recorded as female.")
    for row in (male_row, female_row):
        if row["status"] == "Deceased":
            raise ValidationError(f"{row['ear_tag']} is marked Deceased.")
    duplicate = pairs[(pairs["male"] == male) & (pairs["female"] == female) & (pairs["status"] == "Active")]
    if len(duplicate):
        raise ValidationError(f"{male} and {female} are already active pair {duplicate.iloc[0]['pair_id']}.")

    cage_text = clean_text(cage)
    if not cage_text:
        raise ValidationError("Cage is required.")
    pair_id = _next_id(pairs["pair_id"], "P", 3)
    tables["pairs"] = _append(
        pairs,
        {
            "pair_id": pair_id,
            "male": male,
            "female": female,
            "cage": cage_text,
            "start_date": clean_date(start_date, "Start date"),
            "status": "Active",
            "notes": clean_text(notes),
        },
    )
    is_parent = mice["ear_tag"].isin([male, female])
    mice.loc[is_parent, "status"] = "Breeder"
    mice.loc[is_parent, "updated_by"] = actor
    mice.loc[is_parent, "updated_at"] = now_iso()
    started = clean_date(start_date, "Start date")
    _move(tables, male, cage_text, started, actor)
    _move(tables, female, cage_text, started, actor)
    return pair_id


def retire_pair(tx: Transaction, pair_id: str) -> None:
    pairs = tx.tables["pairs"]
    pairs.loc[_row_index(pairs, "pair_id", pair_id, "Pair"), "status"] = "Retired"


def _clean_count(value: object, label: str) -> str:
    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label} must be a whole number.") from exc
    if count < 0:
        raise ValidationError(f"{label} can't be negative.")
    return str(count)


def add_litter(tx: Transaction, pair_id: str, dob: object, n_pups: object, notes: object) -> str:
    tables = tx.tables
    if not pair_id:
        raise ValidationError("Choose a breeding pair.")
    _row_index(tables["pairs"], "pair_id", pair_id, "Pair")
    litter_id = _next_id(tables["litters"]["litter_id"], "L", 3)
    tables["litters"] = _append(
        tables["litters"],
        {
            "litter_id": litter_id,
            "pair_id": pair_id,
            "dob": clean_date(dob, "Date of birth"),
            "n_pups": _clean_count(n_pups, "Number of pups"),
            "status": "Pending genotyping",
            "notes": clean_text(notes),
        },
    )
    return litter_id


def update_litter(tx: Transaction, litter_id: str, n_pups: object, weaning_date: object, status: object, notes: object) -> None:
    litters = tx.tables["litters"]
    index = _row_index(litters, "litter_id", litter_id, "Litter")
    weaning = clean_date(weaning_date, "Weaning date", required=False)
    if weaning and weaning < litters.loc[index, "dob"]:
        raise ValidationError("Weaning date can't be before the litter's date of birth.")
    litters.loc[index, "n_pups"] = _clean_count(n_pups, "Number of pups")
    litters.loc[index, "weaning_date"] = weaning
    litters.loc[index, "status"] = _choice(status, LITTER_STATUSES, "Status")
    litters.loc[index, "notes"] = clean_text(notes)


def add_pups(tx: Transaction, litter_id: str, rows: list[dict], actor: str) -> int:
    """Create every pup in `rows`, or none of them if any row is wrong."""
    tables = tx.tables
    litters, pairs = tables["litters"], tables["pairs"]
    litter = litters.loc[_row_index(litters, "litter_id", litter_id, "Litter")]
    pair = pairs.loc[_row_index(pairs, "pair_id", litter["pair_id"], "Pair")]
    created = 0
    for raw in rows:
        data = {
            **raw,
            "dob": litter["dob"],
            "mother": pair["female"],
            "father": pair["male"],
            "litter_id": litter_id,
            "status": raw.get("status") or "Unassigned",
            "genotyping_status": "Pending",
        }
        try:
            add_mouse(tx, data, actor)
        except ValidationError as exc:
            raise ValidationError(f"Pup {raw.get('ear_tag')}: {exc} Nothing was saved.") from exc
        created += 1
    if not created:
        raise ValidationError("Add at least one pup with an ear tag.")
    return created


def record_genotypes(tx: Transaction, rows: list[dict], actor: str) -> int:
    """Update alleles for many mice in one save. `rows` holds ear_tag, the 3 loci and confirmed."""
    mice = tx.tables["mice"]
    changed = 0
    for raw in rows:
        tag = raw["ear_tag"]
        index = _row_index(mice, "ear_tag", tag, "Mouse")
        current = mice.loc[index]
        new = {locus: _choice(raw[locus], LOCUS_VALUES[locus], LOCUS_LABELS[locus]) for locus in LOCI}
        status = "Confirmed" if raw.get("confirmed") else "Pending"
        same = all(current[locus] == new[locus] for locus in LOCI) and current["genotyping_status"] == status
        if same:
            continue
        fields = _genotyping_fields(
            {"ear_tag": tag, **new},
            {
                "genotyping_status": status,
                "genotype_confirmed_on": current["genotype_confirmed_on"] if current["genotyping_status"] == "Confirmed" else None,
            },
        )
        for column, value in {**new, **fields, "updated_by": actor, "updated_at": now_iso()}.items():
            mice.loc[index, column] = value
        changed += 1
    return changed


def prepare_gel_file(name: str, content: bytes) -> tuple[str, bytes, str]:
    """Return (extension, bytes to store, file type).

    Photos are shrunk to keep the repo small. Re-encoding also removes EXIF data,
    which on a phone photo can include the location it was taken.
    """
    if not content:
        raise ValidationError("The file is empty.")
    if content[:5] == b"%PDF-" or name.lower().endswith(".pdf"):
        if content[:5] != b"%PDF-":
            raise ValidationError("That file is named .pdf but isn't a PDF.")
        if len(content) > MAX_PDF_BYTES:
            raise ValidationError("PDFs can be up to 10 MB.")
        return "pdf", content, "pdf"
    if len(content) > MAX_IMAGE_BYTES:
        raise ValidationError("Photos can be up to 25 MB.")
    try:
        with Image.open(io.BytesIO(content)) as image:
            upright = ImageOps.exif_transpose(image).convert("RGB")
    except Exception as exc:
        raise ValidationError("That file couldn't be read as a photo or PDF.") from exc
    upright.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    output = io.BytesIO()
    upright.save(output, format="JPEG", quality=82, optimize=True)
    return "jpg", output.getvalue(), "image"


def add_gel(
    tx: Transaction,
    file_name: str,
    file_content: bytes | None,
    mice_tags: list[str],
    date_run: object,
    locus: object,
    primers: object,
    bands: object,
    result: object,
    notes: object,
    actor: str,
) -> str:
    tables = tx.tables
    if not mice_tags:
        raise ValidationError("Link the gel to at least one mouse.")
    for tag in mice_tags:
        _row_index(tables["mice"], "ear_tag", tag, "Mouse")
    if not file_content:
        raise ValidationError("Add a gel photo or PDF.")
    extension, stored, file_type = prepare_gel_file(file_name, file_content)
    gel_id = _next_id(tables["gels"]["gel_id"], "G", 4)
    path = f"gels/{gel_id}.{extension}"
    tx.add_file(path, stored)
    tables["gels"] = _append(
        tables["gels"],
        {
            "gel_id": gel_id,
            "date_run": clean_date(date_run, "Date run"),
            "locus": _choice(locus, GEL_LOCI, "Locus"),
            "primers": clean_text(primers),
            "bands": clean_text(bands),
            "result": _choice(result, GEL_RESULTS, "Result"),
            "mice": ";".join(mice_tags),
            "file": path,
            "file_type": file_type,
            "uploaded_by": actor,
            "uploaded_at": now_iso(),
            "notes": clean_text(notes),
        },
    )
    return gel_id


def _clean_tumor(tables: Tables, ear_tag: str, data: dict, actor: str) -> dict[str, str]:
    in_breeding = data.get("in_breeding")
    if in_breeding not in BREEDING_CHOICES:
        raise ValidationError("Choose whether this mouse is in breeding.")
    outcome = _choice(data.get("outcome"), TUMOR_OUTCOMES, "Outcome")
    noticed = clean_date(data.get("noticed_on"), "Date tumor noticed")
    outcome_date = clean_date(data.get("outcome_date"), "Date deceased or collected", required=False)
    if outcome == "Alive" and outcome_date:
        raise ValidationError("Choose Deceased or Collected, or clear the date.")
    if outcome != "Alive" and not outcome_date:
        raise ValidationError(f"Add the date this mouse was {outcome.lower()}.")
    if outcome_date and outcome_date < noticed:
        raise ValidationError("The date deceased or collected can't be before the tumor was noticed.")
    mice = tables["mice"]
    match = mice[mice["ear_tag"] == ear_tag]
    if len(match) and match.iloc[0]["dob"] and noticed < match.iloc[0]["dob"]:
        raise ValidationError(f"The tumor can't be noticed before {ear_tag} was born.")
    return {
        "ear_tag": ear_tag,
        "noticed_on": noticed,
        "in_breeding": str(in_breeding),
        "outcome": outcome,
        "outcome_date": outcome_date,
        "notes": clean_text(data.get("notes")),
        "updated_by": actor,
        "updated_at": now_iso(),
    }


def _mark_deceased_if_gone(tables: Tables, row: dict[str, str], actor: str) -> None:
    if row["outcome"] == "Alive":
        return
    mice = tables["mice"]
    is_mouse = mice["ear_tag"] == row["ear_tag"]
    mice.loc[is_mouse, "status"] = "Deceased"
    mice.loc[is_mouse, "updated_by"] = actor
    mice.loc[is_mouse, "updated_at"] = now_iso()


def add_tumor(tx: Transaction, data: dict, actor: str) -> str:
    """Record a tumor. The ear tag doesn't have to be in the Mice list yet."""
    tables = tx.tables
    tag = clean_ear_tag(data.get("ear_tag"))
    known = tables["mice"]["ear_tag"]
    same_mouse = known[known.str.lower() == tag.lower()]
    if len(same_mouse):
        tag = same_mouse.iloc[0]
    if tag.lower() in set(tables["tumors"]["ear_tag"].str.lower()):
        raise ValidationError(f"{tag} already has a tumor record. Update it from the list.")
    row = _clean_tumor(tables, tag, data, actor)
    tables["tumors"] = _append(tables["tumors"], row)
    _mark_deceased_if_gone(tables, row, actor)
    return tag


def update_tumor(tx: Transaction, ear_tag: str, data: dict, actor: str) -> None:
    tables = tx.tables
    tumors = tables["tumors"]
    index = _row_index(tumors, "ear_tag", ear_tag, "Tumor record")
    row = _clean_tumor(tables, ear_tag, data, actor)
    for column, value in row.items():
        tumors.loc[index, column] = value
    _mark_deceased_if_gone(tables, row, actor)


def delete_tumor(tx: Transaction, ear_tag: str) -> None:
    tumors = tx.tables["tumors"]
    index = _row_index(tumors, "ear_tag", ear_tag, "Tumor record")
    tx.tables["tumors"] = tumors.drop(index=index).reset_index(drop=True)


def get_settings(tables: Tables) -> dict[str, int]:
    stored = dict(zip(tables["settings"]["key"], tables["settings"]["value"]))
    settings = {}
    for key, default in DEFAULT_SETTINGS.items():
        try:
            settings[key] = int(stored.get(key, default))
        except ValueError:
            settings[key] = default
    return settings


def save_settings(tx: Transaction, values: dict[str, object]) -> None:
    rows = []
    for key in DEFAULT_SETTINGS:
        value = int(_clean_count(values[key], key))
        if value < 1:
            raise ValidationError("Every setting must be at least 1.")
        rows.append({"key": key, "value": str(value)})
    tx.tables["settings"] = pd.DataFrame(rows, dtype=str)


__all__ = [
    "CRE_VALUES",
    "FLOX_VALUES",
    "ValidationError",
    "add_gel",
    "add_litter",
    "add_mouse",
    "add_pair",
    "add_pups",
    "add_tumor",
    "delete_mouse",
    "delete_tumor",
    "get_settings",
    "mouse_references",
    "move_mice",
    "prepare_gel_file",
    "record_genotypes",
    "retire_pair",
    "save_settings",
    "update_litter",
    "update_mouse",
    "update_tumor",
]
