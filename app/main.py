from __future__ import annotations

import re
import time
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict, Field
from rapidfuzz import fuzz

app = FastAPI(title="LabelCheck AI", version="2.0.0")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

CANONICAL_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink "
    "alcoholic beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a car or operate "
    "machinery, and may cause health problems."
)


class ApplicationData(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    brand_name: str = Field(min_length=1)
    class_type: str = Field(min_length=1)
    alcohol_content: str = Field(min_length=1)
    net_contents: str = Field(min_length=1)
    producer_name_address: str = Field(min_length=1)
    country_of_origin: str | None = None


def fold_text(value: str | None) -> str:
    value = (value or "").casefold().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_alcohol(value: str | None) -> dict:
    text = value or ""
    percent_match = re.search(
        r"(?<![\d.])(\d{1,3}(?:\.\d+)?)(?!\d)\s*%",
        text,
    )
    proof_match = re.search(
        r"(?<![\d.])(\d{1,3}(?:\.\d+)?)(?!\d)\s*proof\b",
        text,
        re.I,
    )

    percent_raw = float(percent_match.group(1)) if percent_match else None
    proof_raw = float(proof_match.group(1)) if proof_match else None
    percent = (
        percent_raw
        if percent_raw is not None and 0.0 <= percent_raw <= 100.0
        else None
    )
    proof = (
        proof_raw
        if proof_raw is not None and 0.0 <= proof_raw <= 200.0
        else None
    )
    invalid = (
        (percent_raw is not None and percent is None)
        or (proof_raw is not None and proof is None)
    )
    resolved = percent if percent is not None else (proof / 2 if proof is not None else None)
    contradictory = (
        percent is not None
        and proof is not None
        and abs(percent - (proof / 2)) > 0.05
    )
    return {
        "abv": resolved,
        "percent": percent,
        "proof": proof,
        "percent_raw": percent_raw,
        "proof_raw": proof_raw,
        "invalid": invalid,
        "contradictory": contradictory,
    }


def parse_abv(value: str | None) -> float | None:
    parsed = parse_alcohol(value)
    return None if parsed["invalid"] else parsed["abv"]


def _has_alcohol_context(value: str) -> bool:
    return bool(
        re.search(
            r"\bproof\b|\babv\b|\balcohol\b|\balc\b|alc\W*vol|%\s*(?:vol(?:ume)?|alc)",
            value,
            re.I,
        )
    )


def extract_label_alcohol(lines: list[str]) -> dict | None:
    statements = []
    parsed_statements = []

    for line in lines:
        parsed = parse_alcohol(line)
        has_numeric = (
            parsed["percent_raw"] is not None
            or parsed["proof_raw"] is not None
        )
        if has_numeric and _has_alcohol_context(line):
            statements.append(line)
            parsed_statements.append(parsed)

    if not parsed_statements:
        return None

    values = []
    percents = []
    proofs = []
    invalid = False

    for parsed in parsed_statements:
        invalid = invalid or parsed["invalid"]
        if parsed["percent"] is not None:
            percents.append(parsed["percent"])
            values.append(parsed["percent"])
        if parsed["proof"] is not None:
            proofs.append(parsed["proof"])
            values.append(parsed["proof"] / 2)

    contradictory = False
    if len(values) > 1:
        contradictory = max(values) - min(values) > 0.05

    resolved = values[0] if values and not contradictory and not invalid else None
    return {
        "text": " | ".join(dict.fromkeys(statements)),
        "abv": resolved,
        "percents": percents,
        "proofs": proofs,
        "invalid": invalid,
        "contradictory": contradictory,
    }


def parse_volume_ml(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(
        r"(?<![\d.])(\d+(?:,\d{3})*(?:\.\d+)?)(?!\d)\s*(ml|l|liters?|litres?)\b",
        value,
        re.I,
    )
    if not match:
        return None
    amount = float(match.group(1).replace(",", ""))
    unit = match.group(2).casefold()
    return amount * 1000 if unit in {"l", "liter", "liters", "litre", "litres"} else amount


def _phrase_tokens(value: str | None) -> list[str]:
    tokens = fold_text(value).split()
    if len(tokens) > 1 and all(len(token) == 1 for token in tokens):
        return ["".join(tokens)]
    return tokens


def contains_token_phrase(text: str | None, phrase: str | None) -> bool:
    haystack = _phrase_tokens(text)
    needle = _phrase_tokens(phrase)
    if not needle or len(needle) > len(haystack):
        return False
    width = len(needle)
    return any(haystack[i : i + width] == needle for i in range(len(haystack) - width + 1))

def clean_lines(raw: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", line).strip()
        for line in raw.splitlines()
        if line.strip()
    ]


def best_segment(lines: list[str], expected: str, max_lines: int = 2) -> str | None:
    target = fold_text(expected)
    if not target or not lines:
        return None

    best_score = 0.0
    best_value = None
    for start in range(len(lines)):
        for width in range(1, min(max_lines, len(lines) - start) + 1):
            segment = " ".join(lines[start : start + width])
            candidate = fold_text(segment)
            if not candidate:
                continue
            score = max(
                fuzz.ratio(target, candidate),
                fuzz.partial_ratio(target, candidate),
            )
            score += fuzz.ratio(target, candidate) / 1000
            if score > best_score:
                best_score = score
                best_value = segment
    return best_value


def extract_warning(raw: str) -> str | None:
    match = re.search(r"GOVERNMENT\s+WARNING", raw, re.I)
    if not match:
        return None
    tail = raw[match.start() : match.start() + 700]
    end = re.search(r"health\s+problems\s*[.]", tail, re.I)
    if end:
        tail = tail[: end.end()]
    return re.sub(r"\s+", " ", tail).strip()


def field_result(field, expected, observed, status, reason):
    return {
        "field": field,
        "expected": expected,
        "observed": observed,
        "status": status,
        "reason": reason,
    }


def verify_ocr_text(
    raw: str,
    application: ApplicationData,
    ocr_confidence: float | None = None,
    warning_bold: bool | None = None,
) -> dict:
    started = time.perf_counter()
    raw = (raw or "").strip()
    if not raw:
        raise HTTPException(
            status_code=422,
            detail="No readable text was detected in the label image.",
        )

    lines = clean_lines(raw)
    extracted = {
        "brand_name": best_segment(lines, application.brand_name, 1),
        "class_type": best_segment(lines, application.class_type, 2),
        "producer": best_segment(lines, application.producer_name_address, 3),
        "alcohol": extract_label_alcohol(lines),
        "volume": next(
            (line for line in lines if parse_volume_ml(line) is not None),
            None,
        ),
        "country": next(
            (
                line
                for line in lines
                if application.country_of_origin
                and contains_token_phrase(line, application.country_of_origin)
            ),
            None,
        ),
        "warning": extract_warning(raw),
        "warning_caps": bool(re.search(r"\bGOVERNMENT\s+WARNING\b", raw)),
    }

    results = []
    for field, expected, observed, review_threshold in [
        ("Brand name", application.brand_name, extracted["brand_name"], 84),
        ("Class / type", application.class_type, extracted["class_type"], 82),
        (
            "Producer / bottler",
            application.producer_name_address,
            extracted["producer"],
            78,
        ),
    ]:
        if not observed:
            results.append(
                field_result(
                    field,
                    expected,
                    None,
                    "review_needed",
                    "No reliable matching text was extracted; human review is needed.",
                )
            )
            continue

        expected_folded = fold_text(expected)
        observed_folded = fold_text(observed)
        if expected_folded == observed_folded:
            results.append(
                field_result(
                    field,
                    expected,
                    observed,
                    "pass",
                    "Normalized label text matches the application value.",
                )
            )
            continue

        score = fuzz.ratio(expected_folded, observed_folded)
        status = "review_needed" if score >= review_threshold else "mismatch"
        reason = (
            "Values are similar but not identical after normalization; human review is needed."
            if status == "review_needed"
            else "Label text does not match the application value."
        )
        results.append(field_result(field, expected, observed, status, reason))

    expected_alcohol = parse_alcohol(application.alcohol_content)
    observed_alcohol = extracted["alcohol"]
    expected_abv = expected_alcohol["abv"]
    observed_abv = observed_alcohol["abv"] if observed_alcohol else None
    observed_text = observed_alcohol["text"] if observed_alcohol else None

    if expected_alcohol["invalid"]:
        abv_status = "mismatch"
        abv_reason = "Application alcohol content contains an out-of-range numeric declaration."
    elif expected_alcohol["contradictory"]:
        abv_status = "mismatch"
        abv_reason = (
            "Application alcohol statement is internally contradictory: "
            f"{expected_alcohol['percent']}% ABV does not equal "
            f"{expected_alcohol['proof']} proof / 2."
        )
    elif observed_alcohol is None:
        abv_status = "review_needed"
        abv_reason = "No alcohol-specific label declaration was reliably detected."
    elif observed_alcohol["invalid"]:
        abv_status = "mismatch"
        abv_reason = "Label alcohol content contains an out-of-range numeric declaration."
    elif observed_alcohol["contradictory"]:
        abv_status = "mismatch"
        abv_reason = (
            "Conflicting alcohol declarations were detected on the label: "
            f"{observed_text}."
        )
    elif expected_abv is not None and observed_abv is not None:
        if abs(expected_abv - observed_abv) > 0.05:
            abv_status = "mismatch"
            abv_reason = f"Parsed ABV: application={expected_abv}; label={observed_abv}."
        elif (
            expected_alcohol["percent"] is not None
            and not observed_alcohol["percents"]
            and observed_alcohol["proofs"]
        ):
            abv_status = "review_needed"
            abv_reason = (
                f"Proof is numerically equivalent to {observed_abv}% ABV, "
                "but an ABV percentage declaration was not detected; human review is needed."
            )
        else:
            abv_status = "pass"
            abv_reason = f"Parsed ABV: application={expected_abv}; label={observed_abv}."
    else:
        abv_status = "review_needed"
        abv_reason = (
            f"Parsed ABV: application={expected_abv}; label={observed_abv}. "
            "A reliable alcohol-content value could not be established."
        )

    results.append(
        field_result(
            "Alcohol content",
            application.alcohol_content,
            observed_text,
            abv_status,
            abv_reason,
        )
    )

    expected_ml = parse_volume_ml(application.net_contents)
    observed_ml = parse_volume_ml(extracted["volume"])
    volume_status = (
        "pass"
        if expected_ml is not None
        and observed_ml is not None
        and abs(expected_ml - observed_ml) <= 0.5
        else "mismatch"
        if observed_ml is not None
        else "review_needed"
    )
    results.append(
        field_result(
            "Net contents",
            application.net_contents,
            extracted["volume"],
            volume_status,
            f"Normalized volume: application={expected_ml} mL; label={observed_ml} mL.",
        )
    )

    if application.country_of_origin:
        country_status = "pass" if extracted["country"] else "review_needed"
        results.append(
            field_result(
                "Country of origin",
                application.country_of_origin,
                extracted["country"],
                country_status,
                "Country text found."
                if extracted["country"]
                else "Country of origin was not reliably detected.",
            )
        )
    else:
        results.append(
            field_result(
                "Country of origin",
                "Not provided",
                None,
                "not_required",
                "No country of origin was supplied; this prototype does not infer domestic origin from a blank field.",
            )
        )

    warning = extracted["warning"]
    if not warning:
        results.append(
            field_result(
                "Government warning wording",
                CANONICAL_WARNING,
                None,
                "mismatch",
                "Required government warning text was not detected.",
            )
        )
    else:
        canonical = re.sub(r"\s+", " ", CANONICAL_WARNING).casefold()
        candidate = re.sub(r"\s+", " ", warning).casefold()
        similarity = fuzz.partial_ratio(
            fold_text(CANONICAL_WARNING),
            fold_text(warning),
        )
        exact = canonical in candidate
        warning_status = (
            "pass"
            if exact
            else "review_needed"
            if similarity >= 92
            else "mismatch"
        )
        results.append(
            field_result(
                "Government warning wording",
                CANONICAL_WARNING,
                warning,
                warning_status,
                "Canonical wording detected."
                if exact
                else "OCR is highly similar but not exact; human review is needed."
                if similarity >= 92
                else "Detected warning differs materially from the required wording.",
            )
        )

    if not extracted["warning_caps"]:
        format_status = "mismatch"
        format_reason = "The required all-caps GOVERNMENT WARNING heading was not detected."
    elif warning_bold is True:
        format_status = "pass"
        format_reason = "All-caps heading detected and bold formatting was positively verified."
    elif warning_bold is False:
        format_status = "mismatch"
        format_reason = "All-caps heading detected, but bold formatting was not present."
    else:
        format_status = "review_needed"
        format_reason = (
            "All-caps heading detected. Bold typography requires visual confirmation."
        )

    results.append(
        field_result(
            "Government warning format",
            "GOVERNMENT WARNING in all caps and bold; body not bold",
            (
                f"all_caps={extracted['warning_caps']}; "
                f"bold={warning_bold if warning_bold is not None else 'unverified'}"
            ),
            format_status,
            format_reason,
        )
    )

    statuses = {
        item["status"]
        for item in results
        if item["status"] != "not_required"
    }
    overall = (
        "mismatch"
        if "mismatch" in statuses
        else "review_needed"
        if "review_needed" in statuses
        else "pass"
    )

    if ocr_confidence is not None and ocr_confidence < 55 and overall == "pass":
        overall = "review_needed"

    return {
        "overall_status": overall,
        "processing_ms": int((time.perf_counter() - started) * 1000),
        "ocr_confidence": ocr_confidence,
        "extractor": "browser-tesseract-js-wasm",
        "results": results,
        "raw_text": raw,
        "notes": [
            "OCR runs in the reviewer browser using locally served WebAssembly assets.",
            "The backend applies deterministic verification rules and does not issue a TTB regulatory determination.",
            "Uploaded label images are not sent to the server by the single-label workflow.",
        ],
    }


def sample_label_image() -> bytes:
    width, height = 900, 1180
    image = Image.new("RGB", (width, height), "#f1eadc")
    draw = ImageDraw.Draw(image)

    def font(size: int, bold: bool = False):
        candidates = (
            [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "C:/Windows/Fonts/arialbd.ttf",
                "C:/Windows/Fonts/segoeuib.ttf",
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                "/Library/Fonts/Arial Bold.ttf",
            ]
            if bold
            else [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "C:/Windows/Fonts/arial.ttf",
                "C:/Windows/Fonts/segoeui.ttf",
                "/System/Library/Fonts/Supplemental/Arial.ttf",
                "/Library/Fonts/Arial.ttf",
            ]
        )
        for candidate in candidates:
            if Path(candidate).exists():
                try:
                    return ImageFont.truetype(candidate, size)
                except OSError:
                    pass

        # Pillow/FreeType may also resolve DejaVu by family name.
        family = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        try:
            return ImageFont.truetype(family, size)
        except OSError:
            # Final portable fallback keeps the demo endpoint functional even
            # on hosts without the expected system font packages.
            try:
                return ImageFont.load_default(size=size)
            except TypeError:
                return ImageFont.load_default()

    def centered(text: str, y: int, size: int, bold: bool = False):
        selected = font(size, bold)
        box = draw.textbbox((0, 0), text, font=selected)
        draw.text(
            ((width - (box[2] - box[0])) / 2, y),
            text,
            fill="#111111",
            font=selected,
        )

    draw.rounded_rectangle(
        (30, 30, width - 30, height - 30),
        20,
        outline="#3b3024",
        width=4,
    )
    centered("OLD TOM DISTILLERY", 85, 50, True)
    centered("KENTUCKY STRAIGHT", 175, 32, True)
    centered("BOURBON WHISKEY", 220, 40, True)
    centered("45% Alc./Vol. (90 Proof)", 320, 28)
    centered("750 mL", 365, 27)
    centered("Bottled by Old Tom Distillery", 455, 21)
    centered("Frankfort, KY", 488, 21)

    x, y = 65, 650
    heading_font = font(18, True)
    body_font = font(18)
    draw.text((x, y), "GOVERNMENT WARNING:", fill="#111111", font=heading_font)
    cursor_x = x + draw.textlength("GOVERNMENT WARNING: ", font=heading_font)
    body = CANONICAL_WARNING.split(":", 1)[1].strip()
    for word in body.split():
        token = word + " "
        token_width = draw.textlength(token, font=body_font)
        if cursor_x + token_width > width - 65:
            y += 28
            cursor_x = 65
        draw.text((cursor_x, y), token, fill="#111111", font=body_font)
        cursor_x += token_width

    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LabelCheck AI</title>
<script src="/static/ocr/tesseract.min.js"></script>
<style>
:root{--navy:#0d2343;--blue:#13315c;--ink:#18202b;--muted:#667085;--line:#dfe3e8}
*{box-sizing:border-box}body{margin:0;background:#f5f7fa;color:var(--ink);font-family:Inter,system-ui,Segoe UI,sans-serif;line-height:1.5}
header{background:var(--navy);color:#fff;padding:20px}.shell{max-width:1100px;margin:auto;padding:0 18px}
.hero{padding:42px 0 20px}.hero h1{font-size:clamp(2rem,5vw,3.2rem);max-width:820px;line-height:1.05;margin:8px 0 14px;letter-spacing:-.035em}
.muted{color:var(--muted)}.card{background:#fff;border:1px solid var(--line);border-radius:16px;padding:24px;margin:18px 0;box-shadow:0 10px 30px #10182812}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.wide{grid-column:1/-1}label{font-size:13px;font-weight:700}
input{width:100%;margin-top:6px;padding:11px;border:1px solid #cfd5dd;border-radius:9px;font:inherit}
button{padding:11px 16px;border-radius:9px;border:1px solid #bfc7d2;font-weight:750;background:#fff;cursor:pointer}
button:disabled{opacity:.6;cursor:wait}.primary{background:var(--blue);color:#fff;border-color:var(--blue)}
.drop{display:block;border:1.5px dashed #aeb7c4;padding:28px;border-radius:12px;margin:18px 0;text-align:center}
.notice{background:#edf5ff;border-left:4px solid var(--blue);padding:12px 14px;border-radius:10px}.hidden{display:none}
.pill{display:inline-block;padding:6px 10px;border-radius:999px;font-weight:800;font-size:12px}.pass{background:#ecfdf3;color:#166534}.mismatch{background:#fff1f0;color:#b42318}.review_needed{background:#fff8db;color:#9a6700}.not_required{background:#f2f4f7;color:#475467}
.progress{height:8px;background:#eaecf0;border-radius:999px;overflow:hidden;margin:12px 0}.progress>div{height:100%;background:var(--blue);width:0;transition:width .2s}
.quality{display:flex;gap:14px;flex-wrap:wrap;margin:10px 0 18px}.quality span{background:#f8fafc;border:1px solid var(--line);padding:7px 10px;border-radius:8px;font-size:13px}
table{width:100%;border-collapse:collapse;min-width:850px}th,td{padding:11px;text-align:left;border-bottom:1px solid #e4e7ec;vertical-align:top;font-size:13px}th{background:#f8fafc}
.wrap{overflow:auto}pre{white-space:pre-wrap;background:#f8fafc;padding:12px;border-radius:10px}
@media(max-width:700px){.grid{grid-template-columns:1fr}.wide{grid-column:auto}}
</style>
</head>
<body>
<header><div class="shell"><strong>LabelCheck AI</strong> · human-in-the-loop decision support</div></header>
<main class="shell">
<section class="hero">
<p class="muted">AI-POWERED ALCOHOL LABEL VERIFICATION</p>
<h1>Check routine label fields in seconds.<br>Escalate uncertainty to a person.</h1>
<p class="muted">Private in-browser OCR + deterministic verification rules + evidence-first review.</p>
</section>
<div class="notice"><b>Prototype:</b> assists compliance review; does not issue a TTB approval. Label images are OCR-processed in the browser and are not uploaded by the single-label workflow.</div>

<section class="card">
<form id="verify-form">
<div class="grid">
<label>Brand name<input name="brand_name" value="OLD TOM DISTILLERY" required></label>
<label>Class / type<input name="class_type" value="Kentucky Straight Bourbon Whiskey" required></label>
<label>Alcohol content<input name="alcohol_content" value="45% Alc./Vol. (90 Proof)" required></label>
<label>Net contents<input name="net_contents" value="750 mL" required></label>
<label class="wide">Producer / bottler<input name="producer_name_address" value="Bottled by Old Tom Distillery, Frankfort, KY" required></label>
<label class="wide">Country of origin (optional)<input name="country_of_origin"></label>
</div>
<label class="drop">Label image<input id="label-image" style="display:block;margin:10px auto 0;max-width:420px" type="file" accept="image/jpeg,image/png,image/webp" required></label>
<div id="progress-wrap" class="progress hidden"><div id="progress-bar"></div></div>
<button class="primary" id="verify-button">Verify label</button>
<button type="button" id="demo-button">Run built-in demo</button>
<span id="client-status" class="muted" role="status" aria-live="polite" style="margin-left:10px"></span>
</form>
</section>

<section id="result-section" class="card hidden">
<h2>Verification result</h2>
<p><span id="overall" class="pill"></span> <span id="timing" class="muted"></span></p>
<div class="quality">
<span id="ocr-confidence">OCR confidence: —</span>
<span>OCR location: reviewer browser</span>
<span>Decision logic: deterministic server rules</span>
</div>
<div class="wrap">
<table>
<thead><tr><th>Field</th><th>Application</th><th>Observed label</th><th>Result</th><th>Why</th></tr></thead>
<tbody id="rows"></tbody>
</table>
</div>
<details><summary>Technical evidence</summary><pre id="raw"></pre></details>
</section>

<section class="card">
<details>
<summary><b>Batch review (stretch feature)</b></summary>
<p class="muted">Upload a CSV manifest and matching images. OCR runs sequentially in this browser so the server is not burdened by image processing. Prototype limit: 20 labels.</p>
<form id="batch-form">
<label>CSV manifest<input id="batch-manifest" type="file" accept=".csv" required></label>
<label>Images<input id="batch-images" type="file" multiple accept="image/jpeg,image/png,image/webp" required></label>
<button id="batch-button">Run batch</button>
<span id="batch-status" class="muted" role="status" aria-live="polite"></span>
</form>
<pre id="batch-output"></pre>
</details>
</section>
</main>

<script>
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function errorMessage(value){
  if(!value) return "Verification failed.";
  if(typeof value==="string") return value;
  if(Array.isArray(value)){
    return value.map(item=>{
      if(typeof item==="string") return item;
      if(item && typeof item.msg==="string") return item.msg;
      return JSON.stringify(item);
    }).join("; ");
  }
  if(typeof value==="object"){
    if(typeof value.detail==="string") return value.detail;
    if(value.detail) return errorMessage(value.detail);
    if(typeof value.message==="string") return value.message;
    try{return JSON.stringify(value)}catch{return "Verification failed."}
  }
  return String(value);
}
let ocrWorkerPromise=null;

function setProgress(value){
  const wrap=document.getElementById("progress-wrap");
  const bar=document.getElementById("progress-bar");
  wrap.classList.remove("hidden");
  bar.style.width=Math.max(0,Math.min(100,value))+"%";
}
function hideProgress(){
  document.getElementById("progress-wrap").classList.add("hidden");
  document.getElementById("progress-bar").style.width="0";
}
async function getOcrWorker(){
  if(!window.Tesseract) throw new Error("Local OCR assets did not load.");
  if(!ocrWorkerPromise){
    ocrWorkerPromise=Tesseract.createWorker("eng",1,{
      workerPath:"/static/ocr/worker.min.js",
      corePath:"/static/ocr/core",
      langPath:"/static/ocr/lang",
      logger:m=>{
        if(typeof m.progress==="number") setProgress(Math.round(m.progress*100));
        const status=document.getElementById("client-status");
        if(status && m.status) status.textContent="OCR: "+m.status.replaceAll("_"," ")+"…";
      }
    }).then(async worker=>{
      await worker.setParameters({tessedit_pageseg_mode:Tesseract.PSM.SPARSE_TEXT});
      return worker;
    });
  }
  return ocrWorkerPromise;
}

async function downscaleImage(blob,maxSide=1400){
  const bitmap=await createImageBitmap(blob);
  const scale=Math.min(1,maxSide/Math.max(bitmap.width,bitmap.height));
  if(scale===1){bitmap.close();return blob;}
  const canvas=document.createElement("canvas");
  canvas.width=Math.max(1,Math.round(bitmap.width*scale));
  canvas.height=Math.max(1,Math.round(bitmap.height*scale));
  const ctx=canvas.getContext("2d",{alpha:false});
  ctx.fillStyle="#fff";ctx.fillRect(0,0,canvas.width,canvas.height);
  ctx.drawImage(bitmap,0,0,canvas.width,canvas.height);
  bitmap.close();
  return new Promise((resolve,reject)=>canvas.toBlob(
    out=>out?resolve(out):reject(new Error("Could not prepare image for OCR.")),
    "image/jpeg",
    .92
  ));
}

async function runOcr(blob){
  const prepared=await downscaleImage(blob);
  const worker=await getOcrWorker();
  const started=performance.now();
  const ret=await worker.recognize(prepared);
  return {
    text:(ret.data.text||"").trim(),
    confidence:Number.isFinite(ret.data.confidence)?ret.data.confidence:null,
    ocrMs:Math.round(performance.now()-started)
  };
}

function applicationFields(form){
  const source=form instanceof FormData?form:new FormData(form);
  return {
    brand_name:source.get("brand_name")||"",
    class_type:source.get("class_type")||"",
    alcohol_content:source.get("alcohol_content")||"",
    net_contents:source.get("net_contents")||"",
    producer_name_address:source.get("producer_name_address")||"",
    country_of_origin:source.get("country_of_origin")||""
  };
}

async function verifyText(fields,ocr,warningBold=null){
  const body=new FormData();
  Object.entries(fields).forEach(([key,value])=>body.append(key,value));
  body.append("ocr_text",ocr.text);
  if(ocr.confidence!==null) body.append("ocr_confidence",String(ocr.confidence));
  if(warningBold!==null) body.append("warning_bold",String(warningBold));
  const response=await fetch("/api/verify-text",{method:"POST",body});
  const text=await response.text();
  let data;
  try{data=JSON.parse(text)}catch{data={detail:text||"Unexpected server response"}}
  if(!response.ok) throw new Error(errorMessage(data.detail||data));
  data.client_ocr_ms=ocr.ocrMs;
  return data;
}

function clearResult(){
  const section=document.getElementById("result-section");
  section.classList.add("hidden");
  document.getElementById("rows").innerHTML="";
  document.getElementById("raw").textContent="";
  document.getElementById("overall").textContent="";
  document.getElementById("timing").textContent="";
}

function render(j,totalClientMs=null){
  const section=document.getElementById("result-section");
  const overall=document.getElementById("overall");
  overall.className="pill "+j.overall_status;
  overall.textContent=j.overall_status.replaceAll("_"," ").toUpperCase();
  const total=totalClientMs!==null?totalClientMs:((j.client_ocr_ms||0)+(j.processing_ms||0));
  document.getElementById("timing").textContent="Click-to-result time: "+(total/1000).toFixed(2)+" s";
  document.getElementById("ocr-confidence").textContent="OCR confidence: "+(j.ocr_confidence===null||j.ocr_confidence===undefined?"—":Math.round(j.ocr_confidence)+"%");
  document.getElementById("rows").innerHTML=j.results.map(x=>"<tr><td><b>"+esc(x.field)+"</b></td><td>"+esc(x.expected)+"</td><td>"+esc(x.observed??"Not detected")+"</td><td><span class='pill "+x.status+"'>"+esc(x.status.replaceAll("_"," "))+"</span></td><td>"+esc(x.reason)+"</td></tr>").join("");
  document.getElementById("raw").textContent=j.raw_text||"(none)";
  section.classList.remove("hidden");
  section.scrollIntoView({behavior:"smooth",block:"start"});
}

document.getElementById("verify-form").onsubmit=async e=>{
  e.preventDefault();
  const button=document.getElementById("verify-button");
  const status=document.getElementById("client-status");
  const file=document.getElementById("label-image").files[0];
  if(!file){alert("Choose a label image first.");return;}
  clearResult();
  const clickStarted=performance.now();
  button.disabled=true;status.textContent="Preparing local OCR…";setProgress(2);
  try{
    const ocr=await runOcr(file);
    if(!ocr.text.trim()) throw new Error("No readable text was detected in the label image. Try a clearer image.");
    status.textContent="Applying verification rules…";
    const data=await verifyText(applicationFields(e.target),ocr,null);
    render(data,Math.round(performance.now()-clickStarted));status.textContent="Verification complete.";
  }catch(err){
    console.error(err);
    const message=errorMessage(err);
    status.textContent="Verification failed: "+message;
    alert(message);
  }finally{button.disabled=false;hideProgress()}
};

document.getElementById("demo-button").onclick=async()=>{
  const button=document.getElementById("demo-button");
  const status=document.getElementById("client-status");
  clearResult();
  const clickStarted=performance.now();
  button.disabled=true;status.textContent="Loading built-in demo…";setProgress(2);
  try{
    const imageResponse=await fetch("/api/demo-image");
    if(!imageResponse.ok) throw new Error("Could not load built-in demo image.");
    const blob=await imageResponse.blob();
    const ocr=await runOcr(blob);
    if(!ocr.text.trim()) throw new Error("The built-in demo image produced no readable OCR text.");
    status.textContent="Applying verification rules…";
    const fields=applicationFields(document.getElementById("verify-form"));
    const data=await verifyText(fields,ocr,true);
    render(data,Math.round(performance.now()-clickStarted));status.textContent="Demo complete.";
  }catch(err){
    console.error(err);
    const message=errorMessage(err);
    status.textContent="Demo failed: "+message;
    alert(message);
  }finally{button.disabled=false;hideProgress()}
};

function parseCsv(text){
  const rows=[];let row=[],field="",quoted=false;
  for(let i=0;i<text.length;i++){
    const ch=text[i],next=text[i+1];
    if(ch==='"' && quoted && next==='"'){field+='"';i++;continue}
    if(ch==='"'){quoted=!quoted;continue}
    if(ch==="," && !quoted){row.push(field);field="";continue}
    if((ch==="\n"||ch==="\r")&&!quoted){
      if(ch==="\r"&&next==="\n")i++;
      row.push(field);field="";
      if(row.some(v=>v.trim()!==""))rows.push(row);
      row=[];continue
    }
    field+=ch;
  }
  row.push(field);if(row.some(v=>v.trim()!==""))rows.push(row);
  if(rows.length<2)return[];
  const headers=rows[0].map(x=>x.trim());
  return rows.slice(1).map(values=>Object.fromEntries(headers.map((h,i)=>[h,(values[i]||"").trim()])));
}

document.getElementById("batch-form").onsubmit=async e=>{
  e.preventDefault();
  const button=document.getElementById("batch-button");
  const status=document.getElementById("batch-status");
  const manifestFile=document.getElementById("batch-manifest").files[0];
  const images=[...document.getElementById("batch-images").files];
  if(!manifestFile||!images.length){alert("Choose a manifest and label images.");return}
  if(images.length>20){alert("Prototype batch limit is 20 images.");return}
  button.disabled=true;setProgress(2);
  document.getElementById("batch-output").textContent="";
  try{
    const rows=parseCsv(await manifestFile.text());
    if(!rows.length) throw new Error("The CSV manifest has no data rows.");
    const imageMap=new Map(images.map(file=>[file.name,file]));
    const missing=rows
      .map(row=>row.filename)
      .filter(filename=>filename && !imageMap.has(filename));
    if(missing.length){
      throw new Error(
        "Missing image files referenced by the manifest: "+missing.join(", ")
      );
    }
    const results=[];
    let processed=0;
    for(const row of rows){
      const file=imageMap.get(row.filename);
      if(!file)continue;
      status.textContent="Batch OCR "+(processed+1)+"…";
      const ocr=await runOcr(file);
      if(!ocr.text.trim()) throw new Error("No readable text was detected in "+row.filename+". Try a clearer image.");
      const fields={
        brand_name:row.brand_name||"",
        class_type:row.class_type||"",
        alcohol_content:row.alcohol_content||"",
        net_contents:row.net_contents||"",
        producer_name_address:row.producer_name_address||"",
        country_of_origin:row.country_of_origin||""
      };
      const data=await verifyText(fields,ocr,null);
      results.push({filename:row.filename,status:data.overall_status,ocr_confidence:data.ocr_confidence,processing_ms:(data.client_ocr_ms||0)+(data.processing_ms||0)});
      processed++;
    }
    if(!results.length)throw new Error("No manifest filenames matched the selected images.");
    const counts=results.reduce((acc,item)=>(acc[item.status]=(acc[item.status]||0)+1,acc),{});
    document.getElementById("batch-output").textContent=JSON.stringify({total:results.length,summary:counts,items:results},null,2);
    status.textContent="Batch complete.";
  }catch(err){
    console.error(err);
    const message=errorMessage(err);
    status.textContent="Batch failed: "+message;
    alert(message);
  }finally{button.disabled=false;hideProgress()}
};
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML


@app.get("/health")
def health():
    return {
        "status": "ok",
        "ocr_location": "browser",
        "verification": "server-deterministic-rules",
    }


@app.get("/api/demo-image")
def demo_image():
    return StreamingResponse(
        BytesIO(sample_label_image()),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.post("/api/verify-text")
def api_verify_text(
    brand_name: str = Form(...),
    class_type: str = Form(...),
    alcohol_content: str = Form(...),
    net_contents: str = Form(...),
    producer_name_address: str = Form(...),
    country_of_origin: str | None = Form(None),
    ocr_text: str = Form(...),
    ocr_confidence: float | None = Form(None, ge=0, le=100),
    warning_bold: bool | None = Form(None),
):
    application = ApplicationData(
        brand_name=brand_name,
        class_type=class_type,
        alcohol_content=alcohol_content,
        net_contents=net_contents,
        producer_name_address=producer_name_address,
        country_of_origin=(country_of_origin.strip() if country_of_origin else None),
    )
    return verify_ocr_text(
        raw=ocr_text,
        application=application,
        ocr_confidence=ocr_confidence,
        warning_bold=warning_bold,
    )
