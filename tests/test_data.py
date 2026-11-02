import io
import threading
from datetime import date, timedelta

import pandas as pd
import pytest
from PIL import Image

from colony import cards, imports, logic, ops
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


SETTINGS = {
    "wean_age_days": 21,
    "litter_gap_weeks": 8,
    "age_threshold_days": 240,
    "genotyping_pending_days": 21,
    "cage_capacity": 2,
    "gestation_days": 20,
    "breeder_replace_days": 240,
}


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


# ---------- Cage moves and cage list ----------


def test_moving_mice_records_each_move(store):
    add(store, "A1", cage="C1")
    add(store, "A2", cage="C1")
    moved = store.transaction("m", lambda tx: ops.move_mice(tx, ["A1", "A2"], "C2", today(), ACTOR))
    assert moved == 2
    tables = store.snapshot()
    assert set(tables["mice"]["cage"]) == {"C2"}
    assert list(tables["moves"]["from_cage"]) == ["C1", "C1"] and list(tables["moves"]["to_cage"]) == ["C2", "C2"]


def test_moving_to_the_same_cage_records_nothing(store):
    add(store, "A1", cage="C1")
    assert store.transaction("m", lambda tx: ops.move_mice(tx, ["A1"], "C1", today(), ACTOR)) == 0
    assert store.snapshot()["moves"].empty


@pytest.mark.parametrize("tags,cage,message", [([], "C2", "at least one"), (["A1"], "", "cage to move")])
def test_move_needs_mice_and_a_cage(store, tags, cage, message):
    add(store, "A1", cage="C1")
    with pytest.raises(ValidationError, match=message):
        store.transaction("m", lambda tx: ops.move_mice(tx, tags, cage, today(), ACTOR))


def test_editing_the_cage_and_pairing_also_record_moves(store):
    add(store, "F1", sex="F", days=200, cage="C1")
    add(store, "M1", sex="M", days=200, cage="C2")
    store.transaction("e", lambda tx: ops.update_mouse(tx, "F1", {"cage": "C3"}, ACTOR))
    store.transaction("p", lambda tx: ops.add_pair(tx, "M1", "F1", "C9", old(1), "", ACTOR))
    moves = store.snapshot()["moves"]
    assert list(zip(moves["ear_tag"], moves["to_cage"])) == [("F1", "C3"), ("M1", "C9"), ("F1", "C9")]
    assert list(moves["moved_on"])[1] == old(1)


def test_moves_follow_a_rename_and_go_with_a_deleted_mistake(store):
    add(store, "A1", cage="C1")
    store.transaction("m", lambda tx: ops.move_mice(tx, ["A1"], "C2", today(), ACTOR))
    store.transaction("r", lambda tx: ops.update_mouse(tx, "A1", {"ear_tag": "A2"}, ACTOR))
    assert list(store.snapshot()["moves"]["ear_tag"]) == ["A2"]
    store.transaction("d", lambda tx: ops.delete_mouse(tx, "A2"))
    assert store.snapshot()["moves"].empty


def test_cage_list_counts_living_mice_and_shows_the_pair(store):
    colony_with_litter(store)
    add(store, "X1", sex="F", cage="C7")
    add(store, "X2", sex="F", cage="C7", status="Deceased")
    listing = logic.cage_list(store.snapshot(), capacity=2).set_index("Cage")
    row = listing.loc["C7"]
    assert (row["Mice"], row["F"], row["M"], row["Pair"], row["Flag"]) == (3, 2, 1, "P001", "Over capacity")


# ---------- Spreadsheet import ----------


def sheet(*rows, columns=("Ear Tag", "Sex", "DOB", "Genotype", "Cage", "Dam", "Sire")):
    return pd.DataFrame([list(r) for r in rows], columns=list(columns), dtype=str)


ORDER = imports.GENOTYPE_ORDERS["Cre / p53 / Brca2"]


def run_import(store, frame, order=ORDER):
    mapping = imports.guess_columns(list(frame.columns))
    box = {}

    def change(tx):
        box["result"] = imports.import_mice(tx, frame, mapping, order, ACTOR)

    store.transaction("import", change)
    return box["result"]


def test_columns_are_guessed_from_common_names():
    guess = imports.guess_columns(["Ear Tag", "Gender", "Date of Birth", "Genotype", "Cage #", "Dam", "Sire", "Comments"])
    assert guess["ear_tag"] == "Ear Tag" and guess["sex"] == "Gender" and guess["dob"] == "Date of Birth"
    assert guess["mother"] == "Dam" and guess["father"] == "Sire" and guess["notes"] == "Comments" and guess["cage"] == "Cage #"
    assert guess["cre"] is None and guess["status"] is None


def test_import_reads_a_messy_colony_sheet(store):
    frame = sheet(
        ("101", "Female", "3/14/2025", "P/het/het", "A1", "", ""),
        ("102", "m", "2025-03-14", "N / hom / WT", "A1", "", ""),
        ("103", "F", "2025-09-01 00:00:00", "pos/fl/fl/+", "A2", "101", "102"),
        ("104", "F", "2025-09-01", "", "A2", "101", "102"),
    )
    # 103's genotype has extra slashes, so it can't be split into 3 and is reported, not guessed.
    result = run_import(store, frame)
    assert result.added == ["101", "102", "104"]
    assert result.problems[0]["Sheet row"] == 4 and "3 alleles" in result.problems[0]["Problem"]
    mice = store.snapshot()["mice"].set_index("ear_tag")
    assert logic.genotype_label(mice.loc["101"]) == "P / het / het" and mice.loc["101", "dob"] == "2025-03-14"
    assert logic.genotype_label(mice.loc["102"]) == "N / hom / WT"
    assert logic.genotype_label(mice.loc["104"]) == "TBD / TBD / TBD" and mice.loc["104", "mother"] == "101"
    assert set(mice["genotyping_status"]) == {"Pending"}


def test_import_honors_the_allele_order_of_the_sheet(store):
    frame = sheet(("101", "F", "2025-03-14", "P/hom/het", "", "", ""))
    run_import(store, frame, imports.GENOTYPE_ORDERS["Cre / Brca2 / p53"])
    mouse = store.snapshot()["mice"].iloc[0]
    assert (mouse["brca2"], mouse["p53"]) == ("hom", "het")


def test_import_reads_separate_allele_columns_and_lab_notation(store):
    frame = sheet(("101", "F", "2025-03-14", "+", "fl/fl", "+/+"), columns=("Tag", "Sex", "Born", "Cre", "p53", "Brca2"))
    run_import(store, frame)
    assert logic.genotype_label(store.snapshot()["mice"].iloc[0]) == "P / hom / WT"


def test_import_keeps_good_rows_and_reports_bad_ones(store):
    add(store, "EXISTS")
    frame = sheet(
        ("GOOD", "F", "2025-01-01", "P/het/het", "", "", ""),
        ("EXISTS", "F", "2025-01-01", "P/het/het", "", "", ""),
        ("NOSEX", "", "2025-01-01", "P/het/het", "", "", ""),
        ("NOBIRTH", "F", "", "P/het/het", "", "", ""),
        ("BADDATE", "F", "next spring", "P/het/het", "", "", ""),
        ("BADCRE", "F", "2025-01-01", "maybe/het/het", "", "", ""),
        ("", "F", "2025-01-01", "P/het/het", "", "", ""),
    )
    result = run_import(store, frame)
    assert result.added == ["GOOD"]
    messages = {p["Ear tag"]: p["Problem"] for p in result.problems}
    assert "already in use" in messages["EXISTS"] and "Sex" in messages["NOSEX"]
    assert "required" in messages["NOBIRTH"] and "valid date" in messages["BADDATE"]
    assert "maybe" in messages["BADCRE"] and "required" in messages[""]


def test_parents_link_even_when_a_pup_is_listed_first_and_bad_links_become_notes(store):
    frame = sheet(
        ("PUP", "F", "2025-06-01", "", "", "DAM", "MISSING"),
        ("DAM", "F", "2025-01-01", "", "", "", ""),
    )
    result = run_import(store, frame)
    mice = store.snapshot()["mice"].set_index("ear_tag")
    assert mice.loc["PUP", "mother"] == "DAM" and mice.loc["PUP", "father"] == ""
    assert "MISSING" in result.warnings[0]["Note"]


def test_preview_changes_nothing_and_matches_the_real_import(store):
    frame = sheet(("A", "F", "2025-01-01", "", "", "", ""), ("B", "", "2025-01-01", "", "", "", ""))
    tables = store.snapshot()
    mapping = imports.guess_columns(list(frame.columns))
    preview = imports.preview_import(tables, frame, mapping, ORDER, ACTOR)
    assert preview.added == ["A"] and len(preview.problems) == 1
    assert store.snapshot()["mice"].empty
    assert run_import(store, frame).added == preview.added


def test_import_needs_an_ear_tag_column(store):
    frame = sheet(("F",), columns=("Sex",))
    with pytest.raises(ValidationError, match="ear tag"):
        run_import(store, frame)


def test_reading_csv_excel_and_bad_files(tmp_path):
    table = sheet(("101", "F", "2025-01-01", "P/het/het", "A1", "", ""))
    path = tmp_path / "s.xlsx"
    table.to_excel(path, index=False)
    assert list(imports.read_spreadsheet("s.xlsx", path.read_bytes())["Ear Tag"]) == ["101"]
    csv_bytes = table.to_csv(index=False).encode()
    assert list(imports.read_spreadsheet("s.csv", csv_bytes)["Genotype"]) == ["P/het/het"]
    assert list(imports.read_spreadsheet("s.csv", b"Tag;Sex\n7;F\n")["Tag"]) == ["7"]
    assert list(imports.read_spreadsheet("s.csv", "Tag,Note\n7,caf\xe9\n".encode("latin-1"))["Note"]) == ["caf\xe9"]
    for name, content, message in (("s.csv", b"", "empty"), ("s.csv", b"Tag,Sex\n", "no rows"), ("s.xlsx", b"not excel", "couldn't be read")):
        with pytest.raises(ValidationError, match=message):
            imports.read_spreadsheet(name, content)


def test_export_has_a_sheet_for_every_table(store):
    colony_with_litter(store)
    workbook = pd.read_excel(io.BytesIO(imports.export_workbook(store.snapshot())), sheet_name=None, dtype=str)
    assert set(workbook) == set(store.snapshot())
    assert set(workbook["mice"]["ear_tag"]) == {"M1", "F1"}


# ---------- Cage cards ----------


def test_cage_cards_make_a_pdf_with_a_page_per_six_cages(store):
    for number in range(7):
        add(store, f"T{number}", cage=f"C{number}")
    pdf = cards.cage_cards_pdf(store.snapshot(), [f"C{n}" for n in range(7)], "PI name, protocol 123", today())
    assert pdf[:5] == b"%PDF-"
    assert pdf.count(b"/Type /Page\n") == 2 or pdf.count(b"/Type /Page ") == 2 or pdf.count(b"/Type /Page\r") == 2


def test_cage_cards_cope_with_many_mice_and_unusual_text(store):
    for number in range(12):
        add(store, f"T{number}", cage="C\u00b5\u2603")
    pdf = cards.cage_cards_pdf(store.snapshot(), ["C\u00b5\u2603"], "Prot\u2603col", today())
    assert pdf[:5] == b"%PDF-"


def test_cards_list_only_cages_with_living_mice(store):
    add(store, "A", cage="C2")
    add(store, "B", cage="C10")
    add(store, "C", cage="C3", status="Deceased")
    add(store, "D")
    assert cards.cages_with_mice(store.snapshot()["mice"]) == ["C2", "C10"]


# ---------- Weaning ----------


def test_weaning_moves_pups_by_sex_and_records_it(store):
    _, litter = colony_with_litter(store)
    rows = [dict(ear_tag="PF", sex="F", cage="C7"), dict(ear_tag="PM", sex="M", cage="C7"), dict(ear_tag="PX", sex="F", cage="C7", status="Deceased")]
    store.transaction("p", lambda tx: ops.add_pups(tx, litter, rows, ACTOR))
    assert store.transaction("w", lambda tx: ops.wean_litter(tx, litter, today(), "F-CAGE", "M-CAGE", ACTOR)) == 2
    tables = store.snapshot()
    mice = tables["mice"].set_index("ear_tag")
    assert (mice.loc["PF", "cage"], mice.loc["PM", "cage"], mice.loc["PX", "cage"]) == ("F-CAGE", "M-CAGE", "C7")
    row = tables["litters"].iloc[0]
    assert (row["weaning_date"], row["status"]) == (today().isoformat(), "Weaned")
    assert logic.build_alerts(tables, SETTINGS, today()).wean.empty


def test_blank_wean_cage_leaves_pups_in_place(store):
    _, litter = colony_with_litter(store)
    store.transaction("p", lambda tx: ops.add_pups(tx, litter, [dict(ear_tag="PF", sex="F", cage="C7")], ACTOR))
    assert store.transaction("w", lambda tx: ops.wean_litter(tx, litter, today(), "", "", ACTOR)) == 0


def test_cannot_wean_before_birth(store):
    _, litter = colony_with_litter(store)
    with pytest.raises(ValidationError, match="before the litter"):
        store.transaction("w", lambda tx: ops.wean_litter(tx, litter, old(30), "", "", ACTOR))


# ---------- Plugs ----------


def plugged_pair(store, days_ago=5):
    add(store, "M1", sex="M", days=100)
    add(store, "F1", sex="F", days=100)
    pair = store.transaction("p", lambda tx: ops.add_pair(tx, "M1", "F1", "C1", old(60), "", ACTOR))
    store.transaction("g", lambda tx: ops.add_plug(tx, pair, old(days_ago), ""))
    return pair


def test_plug_gives_an_expected_birth_and_shows_on_the_dashboard_when_near(store):
    plugged_pair(store, days_ago=15)
    row = logic.plug_table(store.snapshot(), SETTINGS, today()).iloc[0]
    assert row["Expected birth"] == (today() + timedelta(days=5)).isoformat() and row["Status"] == "Due in 5 days"
    assert list(logic.build_alerts(store.snapshot(), SETTINGS, today()).expected["Status"]) == ["Due in 5 days"]


def test_recent_plug_stays_off_the_dashboard_until_near(store):
    plugged_pair(store, days_ago=2)
    assert logic.build_alerts(store.snapshot(), SETTINGS, today()).expected.empty


def test_a_litter_near_the_expected_date_closes_the_plug(store):
    pair = plugged_pair(store, days_ago=20)
    litter = store.transaction("l", lambda tx: ops.add_litter(tx, pair, old(1), 6, ""))
    row = logic.plug_table(store.snapshot(), SETTINGS, today()).iloc[0]
    assert row["Status"] == f"Litter {litter}" and not row["_open"]
    assert logic.build_alerts(store.snapshot(), SETTINGS, today()).expected.empty


def test_overdue_plug_flags_then_stops_flagging(store):
    plugged_pair(store, days_ago=23)
    assert logic.plug_table(store.snapshot(), SETTINGS, today()).iloc[0]["Status"] == "3 days overdue"
    store.transaction("g", lambda tx: ops.add_plug(tx, "P001", old(40), ""))
    statuses = dict(zip(*[logic.plug_table(store.snapshot(), SETTINGS, today())[c] for c in ("Plug date", "Status")]))
    assert statuses[old(40)] == "No litter recorded"


def test_duplicate_plug_is_refused_and_a_plug_can_be_deleted(store):
    plugged_pair(store, days_ago=5)
    with pytest.raises(ValidationError, match="already has a plug"):
        store.transaction("g", lambda tx: ops.add_plug(tx, "P001", old(5), ""))
    store.transaction("d", lambda tx: ops.delete_plug(tx, "PG001"))
    assert store.snapshot()["plugs"].empty


def test_cage_card_includes_an_open_plug(store):
    plugged_pair(store, days_ago=5)
    assert cards.cage_cards_pdf(store.snapshot(), ["C1"], "", today())[:5] == b"%PDF-"


# ---------- Breeder replacement ----------


def test_old_or_dead_breeders_in_active_pairs_are_flagged(store):
    add(store, "OLD", sex="M", days=300)
    add(store, "YOUNG", sex="F", days=100)
    add(store, "M2", sex="M", days=100)
    add(store, "F2", sex="F", days=100)
    store.transaction("p", lambda tx: ops.add_pair(tx, "OLD", "YOUNG", "C1", old(60), "", ACTOR))
    second = store.transaction("p", lambda tx: ops.add_pair(tx, "M2", "F2", "C2", old(60), "", ACTOR))
    store.transaction("u", lambda tx: ops.update_mouse(tx, "F2", {"status": "Deceased"}, ACTOR))
    flagged = logic.breeders_to_replace(store.snapshot(), SETTINGS, today()).set_index("Ear tag")
    assert set(flagged.index) == {"OLD", "F2"}
    assert flagged.loc["OLD", "Reason"] == "Age" and flagged.loc["F2", "Reason"] == "Deceased"
    store.transaction("r", lambda tx: ops.retire_pair(tx, second))
    assert set(logic.breeders_to_replace(store.snapshot(), SETTINGS, today())["Ear tag"]) == {"OLD"}


# ---------- Tasks and calendar ----------


def future(days):
    return (today() + timedelta(days=days)).isoformat()


def test_tasks_sort_by_due_date_and_flag_when_due(store):
    store.transaction("t", lambda tx: ops.add_task(tx, "Later", future(20), "", "", ""))
    store.transaction("t", lambda tx: ops.add_task(tx, "Soon", future(1), "Abc", "C1", ""))
    store.transaction("t", lambda tx: ops.add_task(tx, "Late", old(2), "", "", ""))
    tasks = logic.open_tasks(store.snapshot(), today())
    assert list(tasks["What"]) == ["Late", "Soon", "Later"]
    assert list(tasks["When"]) == ["2 days overdue", "In 1 days", "In 20 days"]
    assert list(logic.build_alerts(store.snapshot(), SETTINGS, today()).tasks["What"]) == ["Late", "Soon"]


def test_finishing_a_task_removes_it_and_it_can_be_reopened(store):
    task = store.transaction("t", lambda tx: ops.add_task(tx, "Do it", future(1), "", "", ""))
    store.transaction("d", lambda tx: ops.set_task_done(tx, task, True))
    tables = store.snapshot()
    assert logic.open_tasks(tables, today()).empty and tables["tasks"].iloc[0]["done_on"] == today().isoformat()
    store.transaction("d", lambda tx: ops.set_task_done(tx, task, False))
    assert len(logic.open_tasks(store.snapshot(), today())) == 1
    store.transaction("x", lambda tx: ops.delete_task(tx, task))
    assert store.snapshot()["tasks"].empty


def test_task_needs_a_title_and_may_be_due_in_the_future(store):
    with pytest.raises(ValidationError, match="what needs doing"):
        store.transaction("t", lambda tx: ops.add_task(tx, " ", future(1), "", "", ""))
    assert store.transaction("t", lambda tx: ops.add_task(tx, "Fine", future(400), "", "", "")) == "T0001"


def test_agenda_collects_everything_coming_up_in_date_order(store):
    add(store, "NEAR", days=230, status="Experimental")
    add(store, "BRED", days=230, status="Breeder")
    pair = plugged_pair(store, days_ago=10)
    store.transaction("l", lambda tx: ops.add_litter(tx, pair, old(15), 4, ""))
    store.transaction("t", lambda tx: ops.add_task(tx, "Order chow", future(2), "Abc", "", ""))
    store.transaction("t", lambda tx: ops.add_task(tx, "Far away", future(90), "", "", ""))
    frame = logic.agenda(store.snapshot(), SETTINGS, today(), days=30)
    assert list(frame["Date"]) == sorted(frame["Date"])
    assert set(frame["Type"]) == {"Task", "Wean", "Expected litter", "Age threshold"}
    details = " | ".join(frame["Detail"])
    assert "Order chow (Abc)" in details and "Far away" not in details and "NEAR" in details and "BRED" not in details
