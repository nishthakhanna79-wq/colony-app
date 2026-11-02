"""Table definitions and the fixed vocabularies used across the app."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

LAB_TIMEZONE = ZoneInfo("America/New_York")

# "TBD" must not contain any letter of P, N, hom, het or WT. The grid's dropdown filters on
# whatever you type, and "pending" kept matching "P" and "N", so typing P then Enter
# silently saved "pending". A test guards this.
PENDING = "TBD"
CRE_VALUES = ["P", "N", PENDING]
FLOX_VALUES = ["hom", "het", "WT", PENDING]

# Display order is fixed everywhere: Cre / p53 / Brca2.
LOCI = ("cre", "p53", "brca2")
LOCUS_LABELS = {"cre": "Cre", "p53": "p53", "brca2": "Brca2"}
LOCUS_VALUES = {"cre": CRE_VALUES, "p53": FLOX_VALUES, "brca2": FLOX_VALUES}

SEXES = ["F", "M"]
MOUSE_STATUSES = ["Unassigned", "Breeder", "Experimental", "Retired", "Deceased"]
GENOTYPING_STATUSES = ["Pending", "Confirmed"]
PAIR_STATUSES = ["Active", "Retired"]
LITTER_STATUSES = ["Pending genotyping", "Weaned", "Pups assigned"]
GEL_RESULTS = ["Pass", "Fail", "Ambiguous"]
GEL_LOCI = ["Cre", "p53", "Brca2", "Other"]
BREEDING_CHOICES = ["Yes", "No"]
TUMOR_OUTCOMES = ["Alive", "Deceased", "Collected"]

TABLE_COLUMNS: dict[str, list[str]] = {
    "mice": [
        "ear_tag",
        "sex",
        "dob",
        "cre",
        "p53",
        "brca2",
        "status",
        "cage",
        "mother",
        "father",
        "litter_id",
        "cohort",
        "notes",
        "genotyping_status",
        "genotype_confirmed_on",
        "updated_by",
        "updated_at",
    ],
    "pairs": ["pair_id", "male", "female", "cage", "start_date", "status", "notes"],
    "litters": [
        "litter_id",
        "pair_id",
        "dob",
        "n_pups",
        "weaning_date",
        "status",
        "notes",
    ],
    "gels": [
        "gel_id",
        "date_run",
        "locus",
        "primers",
        "bands",
        "result",
        "mice",
        "file",
        "file_type",
        "uploaded_by",
        "uploaded_at",
        "notes",
    ],
    "tumors": [
        "ear_tag",
        "noticed_on",
        "in_breeding",
        "outcome",
        "outcome_date",
        "notes",
        "updated_by",
        "updated_at",
    ],
    "settings": ["key", "value"],
}

DEFAULT_SETTINGS = {
    "wean_age_days": 21,
    "litter_gap_weeks": 8,
    "age_threshold_days": 240,
    "genotyping_pending_days": 21,
    "cage_capacity": 5,
}

SETTING_LABELS = {
    "wean_age_days": "Weaning age (days)",
    "litter_gap_weeks": "Flag pairs with no litter for this many weeks",
    "age_threshold_days": "Age threshold for tumor imaging or cohorts (days)",
    "genotyping_pending_days": "Flag genotyping pending past this age (days)",
    "cage_capacity": "Mice per cage",
}


def today() -> date:
    return datetime.now(LAB_TIMEZONE).date()


def now_iso() -> str:
    return datetime.now(LAB_TIMEZONE).isoformat(timespec="seconds")


def csv_path(table: str) -> str:
    return f"{table}.csv"
