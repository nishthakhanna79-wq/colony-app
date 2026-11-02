"""Fill a local data folder with made-up test mice. Usage: python tests/seed.py FOLDER"""
import io
import sys
from datetime import timedelta
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from colony import ops  # noqa: E402
from colony.backends import LocalBackend  # noqa: E402
from colony.schema import today  # noqa: E402
from colony.store import Store  # noqa: E402

ACTOR = "seed@example.com"


def ago(days):
    return (today() - timedelta(days=days)).isoformat()


def gel_photo():
    image = Image.new("RGB", (1200, 800), "#111111")
    draw = ImageDraw.Draw(image)
    for lane in range(8):
        x = 100 + lane * 130
        draw.rectangle([x, 300, x + 80, 318], fill="#e8ffe8")
        if lane % 3 == 0:
            draw.rectangle([x, 450, x + 80, 466], fill="#e8ffe8")
    output = io.BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


def main(folder):
    store = Store(LocalBackend(folder), refresh_seconds=0)

    def mouse(tag, sex, days, cre, p53, brca2, **extra):
        data = dict(ear_tag=tag, sex=sex, dob=ago(days), cre=cre, p53=p53, brca2=brca2, **extra)
        store.transaction(f"add {tag}", lambda tx: ops.add_mouse(tx, data, ACTOR))

    done = dict(genotyping_status="Confirmed")
    mouse("1001", "F", 420, "N", "het", "het", cage="A1", **done)
    mouse("1002", "M", 430, "P", "het", "het", cage="A1", **done)
    mouse("1003", "F", 300, "P", "het", "het", cage="A2", mother="1001", father="1002", **done)
    mouse("1004", "M", 300, "N", "hom", "het", cage="A2", mother="1001", father="1002", **done)
    mouse("1005", "F", 230, "P", "hom", "het", cage="B1", status="Experimental", cohort="Aim1_pilot", mother="1001", father="1002", **done)
    mouse("1006", "M", 260, "P", "het", "WT", cage="B1", status="Experimental", cohort="Aim1_pilot", mother="1001", father="1002", **done)
    mouse("1007", "F", 400, "N", "WT", "het", cage="C1", status="Deceased", notes="Euthanized, tumor")
    mouse("1008", "M", 90, "N", "WT", "WT", cage="C2")

    first = store.transaction("pair", lambda tx: ops.add_pair(tx, "1002", "1001", "A1", ago(350), "", ACTOR))
    second = store.transaction("pair", lambda tx: ops.add_pair(tx, "1004", "1003", "A2", ago(120), "", ACTOR))

    old = store.transaction("litter", lambda tx: ops.add_litter(tx, first, ago(90), 8, ""))
    pending = store.transaction("litter", lambda tx: ops.add_litter(tx, second, ago(19), 6, ""))
    store.transaction("litter", lambda tx: ops.add_litter(tx, second, ago(2), 5, "First day looks good"))

    pups = [("2001", "F", "P", "hom", "hom"), ("2002", "M", "N", "het", "het"), ("2003", "F", "P", "het", "het"), ("2004", "M", "P", "WT", "het"), ("2005", "F", "N", "het", "WT"), ("2006", "M", "P", "het", "hom"), ("2007", "F", "N", "hom", "het"), ("2008", "F", "P", "het", "WT")]
    rows = [dict(ear_tag=t, sex=s, cage="D1", cre=c, p53=p, brca2=b) for t, s, c, p, b in pups]
    store.transaction("pups", lambda tx: ops.add_pups(tx, old, rows, ACTOR))
    store.transaction("wean", lambda tx: ops.update_litter(tx, old, 8, ago(69), "Weaned", ""))
    rows = [dict(ear_tag=f"30{i:02d}", sex="F" if i % 2 else "M") for i in range(1, 5)]
    store.transaction("pups", lambda tx: ops.add_pups(tx, pending, rows, ACTOR))

    store.transaction("gel", lambda tx: ops.add_gel(tx, "gel.jpg", gel_photo(), ["2001", "2002", "2003"], ago(60), "Cre", "Cre F/R", "450 bp expected, seen in lanes 1 and 3", "Pass", "", ACTOR))
    store.transaction("gel", lambda tx: ops.add_gel(tx, "gel.pdf", b"%PDF-1.4 test", ["1005"], ago(50), "Brca2", "B2 11F + B2 11R", "Faint lower band", "Ambiguous", "Rerun", ACTOR))

    store.transaction("tumor", lambda tx: ops.add_tumor(tx, dict(ear_tag="1005", noticed_on=ago(20), in_breeding="No", outcome="Alive", notes="Left flank"), ACTOR))
    store.transaction("tumor", lambda tx: ops.add_tumor(tx, dict(ear_tag="1007", noticed_on=ago(60), in_breeding="Yes", outcome="Collected", outcome_date=ago(40)), ACTOR))
    store.transaction("tumor", lambda tx: ops.add_tumor(tx, dict(ear_tag="OLD-77", noticed_on=ago(100), in_breeding="No", outcome="Deceased", outcome_date=ago(80)), ACTOR))


if __name__ == "__main__":
    main(sys.argv[1])
