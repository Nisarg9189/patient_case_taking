"""Build frontend/public/data/medicines.json, the medicine list the prescription editor searches
(served as a static file, fetched once by the browser and searched there).

Source: "The list of 2110 generic medicines under PMBJP till 31.12.2025" (Pradhan Mantri
Bhartiya Janaushadhi Pariyojana, Annexure published by PIB, Feb 2026):
  https://static.pib.gov.in/WriteReadData/specificdocs/documents/2026/feb/doc202626781701.pdf

    pdftotext -layout doc202626781701.pdf pmbjp.txt
    python tools/medicines/build.py pmbjp.txt

Each row's official name ("Paracetamol Paediatric Oral Suspension IP 125 mg per 5 ml") is
kept as it is, and split into what the prescription form needs: generic name(s), strength,
dosage form, route. The split is heuristic: the editor always shows the official name, and
the doctor can change every field.
"""
import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "frontend" / "public" / "data" / "medicines.json"
SOURCE = {
    "title": "List of 2110 generic medicines under PMBJP (Jan Aushadhi) till 31.12.2025",
    "url": "https://static.pib.gov.in/WriteReadData/specificdocs/documents/2026/feb/doc202626781701.pdf",
    "as_of": "2025-12-31",
}

# what doctors type for some medicines, beside the listed name
ALIASES = [("oral rehydration", "ors"), ("vitamin", "vit"), ("multivitamin", "mvi"), ("iron and folic", "ifa")]

ROW = re.compile(r"^\s{0,3}(\d{1,4})\s+(\d{1,5})\s+(\S.*?)(?:\s{2,}(\S.*?))?\s*$")
CONTINUATION = re.compile(r"^\s{8,}(\S.*?)(?:\s{2,}(\S.*?))?\s*$")

# dosage form words, most specific first -> (form in the prescription form, route or None)
FORMS = [
    (r"eye ointment|eye gel|ophthalmic ointment", "ointment", "eye"),
    (r"eye drops?|ophthalmic", "drops", "eye"),
    (r"ear drops?|otic", "drops", "ear"),
    (r"nasal drops?", "drops", "nasal"),
    (r"nasal", "spray", "nasal"),
    (r"strips?|orally disintegrating|mouth dissolving", "other", "oral"),
    (r"transdermal|patch", "other", "topical"),
    (r"vaginal|pessar", "other", "vaginal"),
    (r"suppositor|rectal", "other", "rectal"),
    (r"inhaler|rotacap|respule|inhalation|nebuli", "inhaler", "inhaled"),
    (r"injection|infusion|for injection", "injection", None),
    (r"capsule", "capsule", "oral"),
    (r"tablet", "tablet", "oral"),
    (r"suspension", "suspension", "oral"),
    (r"syrup|oral solution|oral liquid|elixir|linctus|oral emulsion", "syrup", "oral"),
    (r"oral drops|drops", "drops", "oral"),
    (r"sachet|granules|oral powder|powder for oral|ors|oral rehydration", "sachet", "oral"),
    (r"ointment", "ointment", "topical"),
    (r"cream", "cream", "topical"),
    (r"\bgel\b", "gel", "topical"),
    (r"lotion", "lotion", "topical"),
    (r"spray", "spray", "topical"),
    (r"mouth ?wash|gargle|paint", "other", "topical"),
    (r"dusting powder|powder", "other", "topical"),
    (r"shampoo|soap", "other", "topical"),
]

# words that describe the product, not the ingredient
DESCRIPTORS = re.compile(
    r"\b(?:I\.?P\.?|B\.?P\.?|U\.?S\.?P\.?|tablets?|capsules?|injections?|infusion|oral|suspension|syrup|solution|"
    r"liquid|drops?|eye|ear|nasal|ointment|cream|gel|lotion|spray|sachets?|granules|powder|for|dispersible|"
    r"film[- ]coated|coated|chewable|effervescent|sugar[- ]free|paediatric|pediatric|mouth|dissolving|"
    r"sterile|single|dose|vial|ampoule|pre[- ]filled|syringe|inhaler|rotacaps?|respules?|topical|vaginal|"
    r"rectal|suppositor(?:y|ies)|linctus|elixir|emulsion|shampoo|soap|lozenges?|premix|in|of|with|skin|strips?|"
    r"carton|long|acting|orally|disintegrating|lyophili[sz]ed|medicated|plain|gargle|wash|each|contains|"
    r"vials?|mono ?cartons?|mono|pack|face|transdermal|patch|kit|bottle|inhalation|flavou?r(?:ed)?|orange|"
    r"mint|lemon|strawberry|mango|pineapple|base|"
    r"prolonged|extended|modified|sustained|controlled|delayed|release|gastro[- ]resistant|enteric|SR|ER|XR|CR|MR|DR)\b",
    re.I,
)
RELEASE = re.compile(r"\b(prolonged release|extended release|modified release|sustained release|controlled release|"
                     r"delayed release|gastro[- ]resistant|enteric coated|dispersible|chewable|effervescent|"
                     r"mouth dissolving|orally disintegrating|long acting release|long acting|\bSR\b|\bER\b|\bXR\b|\bCR\b|\bMR\b|\bDR\b)", re.I)
AMOUNT = r"\d+(?:\.\d+)?\s*(?:mg|mcg|µg|g|gm|ml|IU|I\.U\.|units?|lakh IU|million(?: spores| CFU)?|%\s*w/[wv]|%)"
STRENGTH = re.compile(rf"({AMOUNT})(?:\s*(?:per|/|in each)\s*(\d+(?:\.\d+)?\s*(?:ml|g|gm|tablet|capsule|dose|puff|actuation)|ml|g|gm|dose|puff))?", re.I)


def parse_rows(text):
    rows, current = [], None
    for line in text.splitlines():
        if not line.strip() or re.match(r"^\s*(S\. No\.|Code|Annexure|The list of)", line):
            continue
        row = ROW.match(line)
        if row and int(row.group(1)) == (rows[-1]["sno"] + 1 if rows else 1):
            current = {"sno": int(row.group(1)), "code": row.group(2), "name": row.group(3).strip(),
                       "pack": (row.group(4) or "").strip()}
            rows.append(current)
            continue
        more = CONTINUATION.match(line)
        if more and current:
            current["name"] += " " + more.group(1).strip()
            if more.group(2) and not current["pack"]:
                current["pack"] = more.group(2).strip()
    return rows


def tidy_amount(text):
    text = re.sub(r"\s+", " ", text.strip())
    text = re.sub(r"(\d)\s*(mg|mcg|gm|g|ml)\b", lambda m: f"{m.group(1)} {m.group(2).lower()}", text, flags=re.I)
    text = re.sub(r"(\d)\s*(IU|units?)\b", r"\1 \2", text, flags=re.I)
    return text.replace(" gm", " g")


def split(name):
    lowered = name.lower()
    form, route = "other", None
    for pattern, f, r in FORMS:
        if re.search(pattern, lowered):
            form, route = f, r
            break
    release = RELEASE.search(name)

    # "X 100mg, Y 50mg and Z 325mg Tablets" -> ingredients with their amounts
    parts = re.split(r",\s*|\s+and\s+|\s*\+\s*|\s+&\s+", name, flags=re.I)
    ingredients, strengths = [], []
    for part in parts:
        found = STRENGTH.search(part)  # also inside brackets: "Suspension (30mg/5ml)"
        part = re.sub(r"oral rehydration", "OralRehydration", part, flags=re.I)  # not the "oral" of a dosage form
        part = re.sub(r"\bin a flavou?red base\b", " ", part, flags=re.I)
        words = DESCRIPTORS.sub(" ", STRENGTH.sub(" ", re.sub(r"\([^)]*\)", " ", part)))
        words = words.replace("OralRehydration", "Oral Rehydration")
        words = re.sub(r"[^A-Za-z0-9\- ]", " ", words)
        words = re.sub(r"\s+", " ", words).strip(" -")
        words = re.sub(r"(\s+\d+)+$", "", words)  # "Tazobactam 1" (from "1 vial")
        if words and not re.fullmatch(r"\d+", words):
            ingredients.append(words)
        if found:
            amount = tidy_amount(found.group(1))
            per = found.group(2)
            strengths.append(f"{amount}/{tidy_amount(per)}" if per else amount)
    generic = " + ".join(dict.fromkeys(i.upper() for i in ingredients)) or name.upper()
    # "Amlodipine + Enalapril Tablet (5/5 Mg)": one amount per ingredient, in brackets
    listed = re.search(r"\((\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?)+)\s*(mg|mcg|g)\s*\)", name, re.I)
    if listed and len(ingredients) > 1:
        amounts = [a.strip() for a in listed.group(1).split("/")]
        if len(amounts) == len(ingredients):
            strengths = [f"{a} {listed.group(2).lower()}" for a in amounts]
    aliases = [short for words, short in ALIASES if words in lowered]
    return {
        "aliases": aliases,
        "generic": generic,
        "strength": " + ".join(strengths),
        "form": form,
        "route": route,
        "release": release.group(1).lower() if release else "",
    }


def main(path):
    rows = parse_rows(Path(path).read_text())
    items = []
    for row in rows:
        name = re.sub(r"\s+", " ", row["name"]).strip()
        items.append({"code": row["code"], "name": name, "pack": row["pack"], **split(name)})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"source": SOURCE, "items": items}, ensure_ascii=False, separators=(",", ":")))
    print(f"{len(items)} medicines -> {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1])
