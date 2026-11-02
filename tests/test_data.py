import io
import threading
from datetime import date, timedelta

import pandas as pd
import pytest
from PIL import Image

from colony import logic, ops
from colony.backends import GitHubBackend, LocalBackend, StoreError
from colony.ops import ValidationError
from colony.store import Store, parse_table
from colony.schema import today

ACTOR = "tester@example.com"


def old(days):
    return (today() - timedelta(days=days)).isoformat()


@pytest.fixture
def store(tmp_path):
    return Store(LocalBackend(tmp_path), refresh_seconds=0)


@pytest.fixture
def remote(github):
    backend = GitHubBackend(github.repo, github.token, api_url=github.url)
    return Store(backend, refresh_seconds=0)


def add(store, tag, sex="F", days=100, **extra):
    data = {"ear_tag": tag, "sex": sex, "dob": old(days), **extra}
    return store.transaction(f"add {tag}", lambda tx: ops.add_mouse(tx, data, ACTOR))


# ---------- Saving to GitHub ----------


def test_first_save_sets_up_an_empty_repo_and_survives_restart(github, remote):
    add(remote, "A1")
    assert b"A1" in github.files_at_head()["mice.csv"]
    assert "README.md" in github.files_at_head()
    # A brand new app process, like after a Streamlit restart, sees the same data.
    fresh = Store(GitHubBackend(github.repo, github.token, api_url=github.url))
    assert list(fresh.snapshot()["mice"]["ear_tag"]) == ["A1"]


def test_one_save_is_one_commit_even_with_a_photo(github, remote):
    add(remote, "A1")
    before = len(github.commit_messages)
    photo = io.BytesIO()
    Image.new("RGB", (50, 50)).save(photo, format="PNG")
    gel = remote.transaction(
        "add gel",
        lambda tx: ops.add_gel(tx, "gel.png", photo.getvalue(), ["A1"], today(), "Cre", "x", "y", "Pass", "", ACTOR),
    )
    assert len(github.commit_messages) == before + 1
    files = github.files_at_head()
    assert f"gels/{gel}.jpg" in files and b"A1" in files["gels.csv"]
    assert remote.read_file(f"gels/{gel}.jpg") == files[f"gels/{gel}.jpg"]


def test_lost_race_keeps_both_changes(github, remote):
    add(remote, "A1")
    outside = github.files_at_head()["mice.csv"] + b"Z9,M,2020-01-01,TBD,TBD,TBD,Unassigned,,,,,,,Pending,,,\n"
    github.update_delay_hook = lambda: github.outside_commit({"mice.csv": outside})
    add(remote, "B2")
    tags = list(parse_table("mice", github.files_at_head()["mice.csv"])["ear_tag"])
    assert sorted(tags) == ["A1", "B2", "Z9"]


def test_simultaneous_saves_all_land(github, remote):
    errors = []

    def worker(tag):
        try:
            add(remote, tag)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"T{i}",)) for i in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert len(parse_table("mice", github.files_at_head()["mice.csv"])) == 12


def test_bad_token_gives_a_clear_error_without_leaking_it(github):
    bad = Store(GitHubBackend(github.repo, "secret-bad-token", api_url=github.url))
    with pytest.raises(StoreError) as caught:
        bad.snapshot()
    assert "token" in str(caught.value).lower()
    assert "secret-bad-token" not in str(caught.value)


def test_failed_validation_commits_nothing(github, remote):
    add(remote, "A1")
    before = len(github.commit_messages)
    with pytest.raises(ValidationError):
        add(remote, "A1")
    assert len(github.commit_messages) == before


def test_unreadable_csv_is_never_saved_over(github, remote):
    add(remote, "A1")
    broken = b'ear_tag,sex\n"unterminated,F\nA2,F,extra,extra\n'
    github.outside_commit({"mice.csv": broken})
    with pytest.raises(StoreError):
        add(remote, "B2")
    assert github.files_at_head()["mice.csv"] == broken


def test_store_loaded_before_a_table_existed_picks_it_up_without_waiting(tmp_path):
    # What a running app holds after a code update adds a table.
    store = Store(LocalBackend(tmp_path), refresh_seconds=3600)
    add(store, "A1")
    store.snapshot()
    del store._tables["tumors"]
    tables = store.snapshot()
    assert tables["tumors"].empty
    assert list(tables["mice"]["ear_tag"]) == ["A1"]


# ---------- Mice ----------


def test_duplicate_tag_is_rejected_regardless_of_case(store):
    add(store, "a1")
    with pytest.raises(ValidationError, match="already in use"):
        add(store, "A1")


@pytest.mark.parametrize("tag", ["", "has space", "a;b", "=SUM(A1)"])
def test_bad_ear_tags_are_rejected(store, tag):
    with pytest.raises(ValidationError):
        add(store, tag)


def test_future_birth_date_is_rejected(store):
    with pytest.raises(ValidationError, match="future"):
        add(store, "A1", days=-3)


def test_confirmed_needs_all_three_alleles(store):
    with pytest.raises(ValidationError, match="p53"):
        add(store, "A1", genotyping_status="Confirmed", cre="P", brca2="het")
    add(store, "A2", genotyping_status="Confirmed", cre="P", p53="het", brca2="het")
    row = store.snapshot()["mice"].iloc[0]
    assert row["genotype_confirmed_on"] == today().isoformat()


def test_parent_sex_and_age_are_checked(store):
    add(store, "M1", sex="M", days=200)
    add(store, "F1", sex="F", days=10)
    with pytest.raises(ValidationError, match="recorded as sex"):
        add(store, "P1", mother="M1", days=50)
    with pytest.raises(ValidationError, match="born before"):
        add(store, "P2", mother="F1", days=50)


def test_renaming_a_mouse_updates_every_link(store):
    add(store, "M1", sex="M", days=200)
    add(store, "F1", sex="F", days=200)
    add(store, "C1", days=50, mother="F1", father="M1")
    store.transaction("pair", lambda tx: ops.add_pair(tx, "M1", "F1", "C7", today(), "", ACTOR))
    photo = io.BytesIO()
    Image.new("RGB", (5, 5)).save(photo, format="PNG")
    store.transaction("gel", lambda tx: ops.add_gel(tx, "g.png", photo.getvalue(), ["F1", "C1"], today(), "Cre", "", "", "Pass", "", ACTOR))
    store.transaction("rename", lambda tx: ops.update_mouse(tx, "F1", {"ear_tag": "F99"}, ACTOR))
    tables = store.snapshot()
    assert tables["mice"].set_index("ear_tag").loc["C1", "mother"] == "F99"
    assert tables["pairs"].iloc[0]["female"] == "F99"
    assert tables["gels"].iloc[0]["mice"] == "F99;C1"


def test_linked_mouse_cannot_be_deleted(store):
    add(store, "M1", sex="M", days=200)
    add(store, "C1", days=50, father="M1")
    with pytest.raises(ValidationError, match="linked"):
        store.transaction("del", lambda tx: ops.delete_mouse(tx, "M1"))
    store.transaction("del", lambda tx: ops.delete_mouse(tx, "C1"))
    assert list(store.snapshot()["mice"]["ear_tag"]) == ["M1"]


def test_excel_formula_text_is_neutralized():
    assert ops.clean_text("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert ops.clean_text(" fine ") == "fine"


# ---------- Pairs, litters, pups ----------


def colony_with_litter(store):
    add(store, "M1", sex="M", days=200, cre="P", p53="het", brca2="het")
    add(store, "F1", sex="F", days=200, cre="N", p53="het", brca2="het")
    pair = store.transaction("pair", lambda tx: ops.add_pair(tx, "M1", "F1", "C7", old(60), "", ACTOR))
    litter = store.transaction("litter", lambda tx: ops.add_litter(tx, pair, old(21), 4, ""))
    return pair, litter


def test_pair_marks_both_mice_breeder_in_the_pair_cage(store):
    colony_with_litter(store)
    mice = store.snapshot()["mice"].set_index("ear_tag")
    assert mice.loc["M1", "status"] == "Breeder" and mice.loc["F1", "cage"] == "C7"


def test_pair_rejects_wrong_sex(store):
    add(store, "M1", sex="M")
    add(store, "F1", sex="F")
    with pytest.raises(ValidationError, match="not recorded as male"):
        store.transaction("pair", lambda tx: ops.add_pair(tx, "F1", "F1", "C1", today(), "", ACTOR))


def test_pups_inherit_parents_and_birth_date(store):
    _, litter = colony_with_litter(store)
    rows = [{"ear_tag": "P1", "sex": "F"}, {"ear_tag": "P2", "sex": "M"}]
    assert store.transaction("pups", lambda tx: ops.add_pups(tx, litter, rows, ACTOR)) == 2
    pups = store.snapshot()["mice"].set_index("ear_tag")
    assert pups.loc["P1", "mother"] == "F1" and pups.loc["P2", "father"] == "M1"
    assert pups.loc["P1", "dob"] == old(21) and pups.loc["P1", "litter_id"] == litter


def test_one_bad_pup_saves_none_of_them(store):
    _, litter = colony_with_litter(store)
    rows = [{"ear_tag": "P1", "sex": "F"}, {"ear_tag": "M1", "sex": "M"}]
    with pytest.raises(ValidationError):
        store.transaction("pups", lambda tx: ops.add_pups(tx, litter, rows, ACTOR))
    assert "P1" not in set(store.snapshot()["mice"]["ear_tag"])


def test_record_genotypes_in_bulk(store):
    _, litter = colony_with_litter(store)
    store.transaction("pups", lambda tx: ops.add_pups(tx, litter, [{"ear_tag": "P1", "sex": "F"}], ACTOR))
    row = {"ear_tag": "P1", "cre": "P", "p53": "hom", "brca2": "het", "confirmed": True}
    assert store.transaction("g", lambda tx: ops.record_genotypes(tx, [row], ACTOR)) == 1
    pup = store.snapshot()["mice"].set_index("ear_tag").loc["P1"]
    assert logic.genotype_label(pup) == "P / hom / het"
    assert pup["genotyping_status"] == "Confirmed"
    with pytest.raises(ValidationError, match="TBD"):
        store.transaction("g", lambda tx: ops.record_genotypes(tx, [{**row, "p53": "TBD"}], ACTOR))


# ---------- Gel files ----------


def test_photo_is_shrunk_and_location_data_removed():
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"
    source = io.BytesIO()
    Image.new("RGB", (4000, 3000), "white").save(source, format="JPEG", exif=exif)
    extension, stored, kind = ops.prepare_gel_file("IMG.jpg", source.getvalue())
    result = Image.open(io.BytesIO(stored))
    assert (extension, kind) == ("jpg", "image")
    assert max(result.size) == 2000
    assert not result.getexif()


def test_pdf_passes_through_and_fakes_are_rejected():
    assert ops.prepare_gel_file("a.pdf", b"%PDF-1.7 data")[0] == "pdf"
    with pytest.raises(ValidationError):
        ops.prepare_gel_file("a.pdf", b"not a pdf")
    with pytest.raises(ValidationError):
        ops.prepare_gel_file("a.jpg", b"not an image")


# ---------- Genetics ----------


def test_expected_offspring():
    assert logic.expected_offspring("brca2", "het", "het") == {"hom": 0.25, "het": 0.5, "WT": 0.25}
    assert logic.expected_offspring("p53", "hom", "WT") == {"hom": 0.0, "het": 1.0, "WT": 0.0}
    assert logic.expected_offspring("cre", "P", "N") == {"P": 0.5, "N": 0.5}
    assert logic.expected_offspring("cre", "P", "P") is None
    assert logic.expected_offspring("p53", "TBD", "het") is None


def mouse_row(tag, sex="F", cre="N", p53="WT", brca2="WT", mother="", father="", status="Unassigned", dob="2026-01-01", cage="", litter_id="", genotyping_status="Pending"):
    return dict(ear_tag=tag, sex=sex, cre=cre, p53=p53, brca2=brca2, mother=mother, father=father, status=status, dob=dob, cage=cage, litter_id=litter_id, genotyping_status=genotyping_status)


def test_impossible_genotype_is_flagged():
    mice = pd.DataFrame(
        [
            mouse_row("F1", p53="hom"),
            mouse_row("M1", sex="M", p53="hom"),
            mouse_row("OK", p53="hom", mother="F1", father="M1"),
            mouse_row("BAD", p53="WT", mother="F1", father="M1"),
        ]
    )
    problems = logic.inheritance_problems(mice)
    assert list(problems["Ear tag"]) == ["BAD"] and list(problems["Allele"]) == ["p53"]


def het_cross(pups):
    return pd.DataFrame(
        [mouse_row("F1", brca2="het"), mouse_row("M1", sex="M", brca2="het")]
        + [mouse_row(f"P{i}", brca2=g, mother="F1", father="M1") for i, g in enumerate(pups)]
    )


def result_for(mice, allele="Brca2"):
    frame = logic.ratio_tracker(mice)
    return frame[frame["Allele"] == allele].iloc[0]


def test_ratio_within_expected_is_ok():
    assert result_for(het_cross(["hom"] * 6 + ["het"] * 12 + ["WT"] * 6))["Result"] == "OK"


def test_skewed_ratio_is_flagged():
    # 24 pups expected 6:12:6. Chi-square for 0 hom, 12 het, 12 WT is 12, well past 5.99.
    assert result_for(het_cross(["het"] * 12 + ["WT"] * 12))["Result"] == "Skewed"


def test_small_litters_are_not_called_skewed():
    assert result_for(het_cross(["het"] * 4))["Result"] == "Too few pups"


def test_pending_pups_are_excluded_from_ratios():
    row = result_for(het_cross(["hom"] * 6 + ["het"] * 12 + ["WT"] * 6 + ["TBD"] * 20))
    assert row["Pups"] == 24


# ---------- Alerts ----------


SETTINGS = {"wean_age_days": 21, "litter_gap_weeks": 8, "age_threshold_days": 240, "genotyping_pending_days": 21, "cage_capacity": 2}


def test_wean_alert_timing(store):
    pair, due_now = colony_with_litter(store)
    store.transaction("l", lambda tx: ops.add_litter(tx, pair, old(1), 3, ""))
    near = store.transaction("l", lambda tx: ops.add_litter(tx, pair, old(16), 3, ""))
    alerts = logic.build_alerts(store.snapshot(), SETTINGS, today())
    assert set(alerts.wean["Litter"]) == {due_now, near}
    assert alerts.wean.iloc[0]["When"] == "Today"


def test_weaned_litter_stops_alerting(store):
    _, litter = colony_with_litter(store)
    store.transaction("u", lambda tx: ops.update_litter(tx, litter, 4, today(), "Weaned", ""))
    assert logic.build_alerts(store.snapshot(), SETTINGS, today()).wean.empty


def test_quiet_pair_flag(store):
    add(store, "M1", sex="M", days=300)
    add(store, "F1", sex="F", days=300)
    add(store, "M2", sex="M", days=300)
    add(store, "F2", sex="F", days=300)
    quiet = store.transaction("p", lambda tx: ops.add_pair(tx, "M1", "F1", "C1", old(70), "", ACTOR))
    store.transaction("p", lambda tx: ops.add_pair(tx, "M2", "F2", "C2", old(10), "", ACTOR))
    assert list(logic.build_alerts(store.snapshot(), SETTINGS, today()).quiet_pairs["Pair"]) == [quiet]
    store.transaction("l", lambda tx: ops.add_litter(tx, quiet, old(5), 5, ""))
    assert logic.build_alerts(store.snapshot(), SETTINGS, today()).quiet_pairs.empty


def test_age_and_genotyping_alerts(store):
    add(store, "NEAR", days=230, status="Experimental")
    add(store, "PAST", days=300, status="Experimental")
    add(store, "YOUNG", days=20, status="Experimental")
    add(store, "BREEDER", days=300, status="Breeder")
    add(store, "GONE", days=300, status="Deceased")
    alerts = logic.build_alerts(store.snapshot(), SETTINGS, today())
    assert list(alerts.age["Ear tag"]) == ["PAST", "NEAR"]
    assert alerts.age.iloc[1]["When"] == "In 10 days"
    assert "YOUNG" not in set(alerts.genotyping["Ear tag"])
    assert "GONE" not in set(alerts.genotyping["Ear tag"])


def test_cage_census_flags_over_capacity():
    mice = pd.DataFrame([mouse_row("A", cage="C1"), mouse_row("B", cage="C1"), mouse_row("C", cage="C1"), mouse_row("D", cage="C2"), mouse_row("E", cage="C2", status="Deceased")])
    census = logic.cage_census(mice, 2).set_index("Cage")
    assert census.loc["C1", "Flag"] == "Over capacity" and census.loc["C2", "Mice"] == 1


def test_settings_defaults_and_save(store):
    assert ops.get_settings(store.snapshot())["wean_age_days"] == 21
    values = {**ops.get_settings(store.snapshot()), "wean_age_days": 28}
    store.transaction("s", lambda tx: ops.save_settings(tx, values))
    assert ops.get_settings(store.snapshot())["wean_age_days"] == 28


# ---------- Pedigree ----------


def test_pedigree_shows_three_generations_and_marks_problems():
    mice = pd.DataFrame(
        [
            mouse_row("GM", p53="hom"),
            mouse_row("GF", sex="M", p53="hom"),
            mouse_row("MUM", p53="WT", mother="GM", father="GF"),
            mouse_row("DAD", sex="M", p53="het"),
            mouse_row("KID", p53="het", mother="MUM", father="DAD"),
        ]
    )
    dot = logic.pedigree_dot(mice, "KID", generations=2)
    for tag in ("GM", "GF", "MUM", "DAD", "KID"):
        assert f"<B>{tag}</B>" in dot
    assert '"MUM" -> "KID"' in dot and '"GM" -> "MUM"' in dot
    assert "MUM</B> female (check)" in dot  # hom x hom can't give WT.
    assert "not recorded" not in dot  # Founders further back don't get placeholder boxes.
    assert "father not recorded" in logic.pedigree_dot(mice, "DAD", generations=1)
    assert "<B>GM</B>" not in logic.pedigree_dot(mice, "KID", generations=1)


# ---------- Grid dropdowns ----------


def test_typing_an_allele_in_the_grid_matches_only_that_allele():
    # The grid's dropdown filters by substring and keeps the current value if it still matches.
    # If the "not typed yet" value matched, typing P then Enter would silently keep it.
    from colony.schema import CRE_VALUES, FLOX_VALUES

    for options in (CRE_VALUES, FLOX_VALUES):
        for value in options:
            assert [o for o in options if value.lower() in o.lower()] == [value]


# ---------- Tumors ----------


def tumor(tag="1", **extra):
    return {"ear_tag": tag, "noticed_on": old(5), "in_breeding": "No", "outcome": "Alive", **extra}


def add_tumor(store, **kwargs):
    return store.transaction("t", lambda tx: ops.add_tumor(tx, tumor(**kwargs), ACTOR))


def test_tumor_can_be_logged_with_just_an_ear_tag_not_in_the_mice_list(store):
    assert add_tumor(store, tag="NEW1") == "NEW1"
    row = logic.tumor_table(store.snapshot()).iloc[0]
    assert row["Ear tag"] == "NEW1" and row["Cre / p53 / Brca2"] == "Not in Mice list" and row["Age (weeks)"] == ""


def test_tumor_links_to_a_known_mouse_regardless_of_case(store):
    add(store, "aB1", days=70, cre="P", p53="hom", brca2="het")
    assert add_tumor(store, tag="AB1") == "aB1"
    row = logic.tumor_table(store.snapshot()).iloc[0]
    assert row["Cre / p53 / Brca2"] == "P / hom / het" and row["Age (weeks)"] == "9"


def test_a_mouse_only_gets_one_tumor_record(store):
    add_tumor(store, tag="A1")
    with pytest.raises(ValidationError, match="already has"):
        add_tumor(store, tag="a1")


def test_breeding_answer_is_required(store):
    with pytest.raises(ValidationError, match="in breeding"):
        add_tumor(store, in_breeding=None)


@pytest.mark.parametrize("outcome", ["Deceased", "Collected"])
def test_ending_needs_a_date_and_marks_the_mouse_deceased(store, outcome):
    add(store, "A1", days=100, status="Experimental")
    with pytest.raises(ValidationError, match="Add the date"):
        add_tumor(store, tag="A1", outcome=outcome)
    assert store.snapshot()["mice"].iloc[0]["status"] == "Experimental"
    add_tumor(store, tag="A1", outcome=outcome, outcome_date=old(1))
    assert store.snapshot()["mice"].iloc[0]["status"] == "Deceased"


def test_alive_with_a_date_is_refused(store):
    with pytest.raises(ValidationError, match="clear the date"):
        add_tumor(store, outcome_date=old(1))


def test_dates_must_make_sense(store):
    add(store, "A1", days=30)
    with pytest.raises(ValidationError, match="before the tumor"):
        add_tumor(store, tag="B1", outcome="Collected", outcome_date=old(9))
    with pytest.raises(ValidationError, match="before A1 was born"):
        add_tumor(store, tag="A1", noticed_on=old(40))
    with pytest.raises(ValidationError, match="future"):
        add_tumor(store, noticed_on=(today() + timedelta(days=1)).isoformat())


def test_tumor_update_records_collection_later(store):
    add(store, "A1", days=100)
    add_tumor(store, tag="A1")
    change = tumor(tag="A1", outcome="Collected", outcome_date=old(0), in_breeding="Yes")
    store.transaction("u", lambda tx: ops.update_tumor(tx, "A1", change, ACTOR))
    tables = store.snapshot()
    record = tables["tumors"].iloc[0]
    assert (record["outcome"], record["outcome_date"], record["in_breeding"]) == ("Collected", old(0), "Yes")
    assert tables["mice"].iloc[0]["status"] == "Deceased"


def test_tumor_record_follows_a_renamed_mouse_and_blocks_deleting_it(store):
    add(store, "A1", days=100)
    add_tumor(store, tag="A1")
    with pytest.raises(ValidationError, match="tumor record"):
        store.transaction("d", lambda tx: ops.delete_mouse(tx, "A1"))
    store.transaction("r", lambda tx: ops.update_mouse(tx, "A1", {"ear_tag": "A2"}, ACTOR))
    assert list(store.snapshot()["tumors"]["ear_tag"]) == ["A2"]


def test_deleting_a_tumor_record_leaves_the_mouse_alone(store):
    add(store, "A1", days=100)
    add_tumor(store, tag="A1")
    store.transaction("d", lambda tx: ops.delete_tumor(tx, "A1"))
    tables = store.snapshot()
    assert tables["tumors"].empty and list(tables["mice"]["ear_tag"]) == ["A1"]
