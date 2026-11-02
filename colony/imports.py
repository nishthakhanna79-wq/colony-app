"""Bring an existing colony spreadsheet into the app, and take the data back out as Excel."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import pandas as pd

from . import ops
from .schema import LOCI, LOCUS_LABELS, MOUSE_STATUSES, PENDING, TABLE_COLUMNS, now_iso
from .store import Tables, Transaction

MAX_IMPORT_BYTES = 10 * 1024 * 1024
GENOTYPE_ORDERS = {
    "Cre / p53 / Brca2": ("cre", "p53", "brca2"),
    "Cre / Brca2 / p53": ("cre", "brca2", "p53"),
}

NOT_TYPED = {"", "?", "tbd", "pending", "na", "n/a", "nd", "unknown", "not done", "none", "-?"}
CRE_WORDS = {
    "p": "P", "+": "P", "pos": "P", "positive": "P", "yes": "P", "y": "P", "cre": "P", "cre+": "P", "tg": "P", "tg/+": "P",
    "n": "N", "-": "N", "neg": "N", "negative": "N", "no": "N", "wt": "N", "cre-": "N", "+/+": "N",
}
FLOX_WORDS = {
    "hom": "hom", "homo": "hom", "homozygous": "hom", "fl/fl": "hom", "f/f": "hom", "flox/flox": "hom", "-/-": "hom", "ko": "hom", "cko": "hom",
    "het": "het", "hetero": "het", "heterozygous": "het", "fl/+": "het", "+/fl": "het", "f/+": "het", "+/f": "het", "flox/+": "het", "+/-": "het", "-/+": "het",
    "wt": "WT", "+/+": "WT", "wild type": "WT", "wildtype": "WT", "wild-type": "WT", "neg": "WT",
}
SEX_WORDS = {"f": "F", "female": "F", "fem": "F", "m": "M", "male": "M"}

# Field -> spreadsheet column names that probably mean it.
FIELD_LABELS = {
    "ear_tag": "Ear tag",
    "sex": "Sex",
    "dob": "Date of birth",
    "genotype": "Genotype, all 3 in one column",
    "cre": "Cre",
    "p53": "p53",
    "brca2": "Brca2",
    "cage": "Cage",
    "status": "Status",
    "mother": "Mother",
    "father": "Father",
    "cohort": "Cohort tag",
    "notes": "Notes",
}
FIELD_GUESSES = {
    "ear_tag": ["ear tag", "eartag", "ear", "tag", "mouse id", "mouse", "id", "animal id", "animal"],
    "sex": ["sex", "gender"],
    "dob": ["dob", "date of birth", "birth date", "birthdate", "born", "birth"],
    "genotype": ["genotype", "geno", "gt"],
    "cre": ["cre", "wap-cre", "wapcre"],
    "p53": ["p53", "trp53"],
    "brca2": ["brca2"],
    "cage": ["cage", "cage number", "cage no", "cage #", "cage id"],
    "status": ["status"],
    "mother": ["mother", "dam", "mom"],
    "father": ["father", "sire", "dad"],
    "cohort": ["cohort", "cohort tag", "experiment"],
    "notes": ["notes", "note", "comments", "comment"],
}


@dataclass
class ImportResult:
    added: list[str] = field(default_factory=list)
    problems: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)


def read_spreadsheet(name: str, content: bytes) -> pd.DataFrame:
    if not content:
        raise ops.ValidationError("That file is empty.")
    if len(content) > MAX_IMPORT_BYTES:
        raise ops.ValidationError("Spreadsheets can be up to 10 MB.")
    try:
        if name.lower().endswith((".xlsx", ".xlsm")):
            frame = pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False)
        else:
            try:
                text = content.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = content.decode("latin-1")
            frame = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, sep=None, engine="python")
    except ops.ValidationError:
        raise
    except Exception as exc:
        raise ops.ValidationError("That file couldn't be read as a spreadsheet.") from exc
    frame.columns = [str(column).strip() for column in frame.columns]
    frame = frame.loc[:, [c for c in frame.columns if not c.startswith("Unnamed")]]
    empty_rows = frame.apply(lambda row: all(not str(value).strip() for value in row), axis=1) if len(frame) else []
    frame = frame[~empty_rows] if len(frame) else frame
    if frame.empty:
        raise ops.ValidationError("That spreadsheet has no rows.")
    return frame.reset_index(drop=True)


def guess_columns(columns: list[str]) -> dict[str, str | None]:
    lowered = {column: column.strip().lower() for column in columns}
    guesses: dict[str, str | None] = {}
    taken: set[str] = set()
    for target, names in FIELD_GUESSES.items():
        found = None
        for name in names:
            found = next((c for c, low in lowered.items() if low == name and c not in taken), None)
            if found:
                break
        guesses[target] = found
        if found:
            taken.add(found)
    return guesses


def clean_cre(value: object) -> str:
    text = str(value).strip().lower()
    if text in NOT_TYPED:
        return PENDING
    if text in CRE_WORDS:
        return CRE_WORDS[text]
    raise ops.ValidationError(f"Cre value '{value}' wasn't recognized.")


def clean_flox(value: object, locus: str) -> str:
    text = str(value).strip().lower()
    if text in NOT_TYPED:
        return PENDING
    if text in FLOX_WORDS:
        return FLOX_WORDS[text]
    raise ops.ValidationError(f"{LOCUS_LABELS[locus]} value '{value}' wasn't recognized.")


def clean_allele(locus: str, value: object) -> str:
    return clean_cre(value) if locus == "cre" else clean_flox(value, locus)


def split_genotype(text: object, order: tuple[str, str, str]) -> dict[str, str]:
    raw = str(text).strip()
    if raw.lower() in NOT_TYPED:
        return {locus: PENDING for locus in LOCI}
    for pattern in (r"\s*/\s*", r"\s*;\s*", r"\s*,\s*", r"\s*\|\s*", r"\s+"):
        parts = re.split(pattern, raw)
        if len(parts) == 3:
            return {locus: clean_allele(locus, part) for locus, part in zip(order, parts)}
    raise ops.ValidationError(f"Genotype '{raw}' doesn't have 3 alleles.")


def clean_import_date(value: object) -> str:
    text = str(value).strip()
    if not text:
        return ""
    parsed = pd.to_datetime(text, errors="coerce", format="mixed")
    return text if pd.isna(parsed) else parsed.date().isoformat()


def clean_sex(value: object) -> str:
    text = str(value).strip().lower()
    if text in SEX_WORDS:
        return SEX_WORDS[text]
    raise ops.ValidationError(f"Sex value '{value}' wasn't recognized." if text else "Sex is missing.")


def clean_status(value: object) -> str:
    text = str(value).strip().lower()
    if not text:
        return "Unassigned"
    for status in MOUSE_STATUSES:
        if status.lower() == text:
            return status
    return {"dead": "Deceased", "breeding": "Breeder"}.get(text, "Unassigned")


def _row_to_mouse(row: pd.Series, mapping: dict[str, str | None], order: tuple[str, str, str]) -> dict:
    def get(name: str) -> str:
        column = mapping.get(name)
        return str(row[column]).strip() if column else ""

    data = {
        "ear_tag": get("ear_tag"),
        "sex": clean_sex(get("sex")),
        "dob": clean_import_date(get("dob")),
        "cage": get("cage"),
        "status": clean_status(get("status")),
        "mother": get("mother"),
        "father": get("father"),
        "cohort": get("cohort"),
        "notes": get("notes"),
        "genotyping_status": "Pending",
    }
    if mapping.get("genotype"):
        data.update(split_genotype(get("genotype"), order))
    for locus in LOCI:
        if mapping.get(locus):
            data[locus] = clean_allele(locus, get(locus))
    return data


def _resolve_parent(tag: str, known: pd.Series) -> str | None:
    match = known[known.str.lower() == tag.lower()]
    return match.iloc[0] if len(match) else None


def import_mice(tx: Transaction, frame: pd.DataFrame, mapping: dict[str, str | None], order: tuple[str, str, str], actor: str) -> ImportResult:
    """Add every usable row. Rows with problems are reported and left out.

    Parents are linked after every mouse exists, because a sheet can list a pup above its
    mother. A parent link that can't be made is a warning, so one typo doesn't lose the mouse.
    """
    if not mapping.get("ear_tag"):
        raise ops.ValidationError("Choose which column holds the ear tag.")
    result = ImportResult()
    pending_parents = []
    for position, row in frame.iterrows():
        sheet_row = int(position) + 2  # Row 1 of the sheet is the header.
        label = str(row[mapping["ear_tag"]]).strip()
        try:
            data = _row_to_mouse(row, mapping, order)
            parents = {"mother": data.pop("mother"), "father": data.pop("father")}
            tag = ops.add_mouse(tx, data, actor)
        except ops.ValidationError as exc:
            result.problems.append({"Sheet row": sheet_row, "Ear tag": label, "Problem": str(exc)})
            continue
        result.added.append(tag)
        pending_parents.append((sheet_row, tag, parents))

    known = tx.tables["mice"]["ear_tag"]
    for sheet_row, tag, parents in pending_parents:
        for role, parent in parents.items():
            if not parent:
                continue
            resolved = _resolve_parent(parent, known)
            if resolved is None:
                result.warnings.append({"Sheet row": sheet_row, "Ear tag": tag, "Note": f"{role.title()} {parent} isn't in the colony, so it was left blank."})
                continue
            try:
                ops.update_mouse(tx, tag, {role: resolved}, actor)
            except ops.ValidationError as exc:
                result.warnings.append({"Sheet row": sheet_row, "Ear tag": tag, "Note": f"{exc} Left {role} blank."})
    return result


def preview_import(tables: Tables, frame: pd.DataFrame, mapping: dict[str, str | None], order: tuple[str, str, str], actor: str) -> ImportResult:
    """The same import on a scratch copy, so people can see the outcome before saving."""
    scratch = Transaction(tables={name: table.copy() for name, table in tables.items()})
    return import_mice(scratch, frame, mapping, order, actor)


def export_workbook(tables: Tables) -> bytes:
    """Every table as a sheet in one Excel file."""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for name in TABLE_COLUMNS:
            tables[name].to_excel(writer, sheet_name=name, index=False)
    return output.getvalue()


def export_file_name() -> str:
    return f"colony-export-{now_iso()[:10]}.xlsx"
