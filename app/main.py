from __future__ import annotations

import csv
import io
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO

import pytesseract
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
from pytesseract import Output
from rapidfuzz import fuzz

app = FastAPI(title="LabelCheck AI", version="1.0.0")

CANONICAL_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink "
    "alcoholic beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a car or operate "
    "machinery, and may cause health problems."
)


class ApplicationData(BaseModel):
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


def parse_abv(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"(\d{1,2}(?:\.\d+)?)\s*%", value)
    if match:
        return float(match.group(1))
    match = re.search(r"(\d{2,3}(?:\.\d+)?)\s*proof", value, re.I)
    return float(match.group(1)) / 2 if match else None


def parse_volume_ml(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(ml|mL|ML|l|L|liters?|litres?)\b", value, re.I
    )
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2).casefold()
    return amount * 1000 if unit in {"l", "liter", "liters", "litre", "litres"} else amount


def load_image(data: bytes) -> Image.Image:
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(400, "Image exceeds the 8 MB prototype limit.")
    try:
        image = Image.open(BytesIO(data))
        image.verify()
        image = Image.open(BytesIO(data)).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(400, "Uploaded file is not a readable image.") from exc
    if image.width * image.height > 25_000_000:
        raise HTTPException(400, "Image dimensions are too large.")
    if max(image.size) > 2200:
        image.thumbnail((2200, 2200), Image.Resampling.LANCZOS)
    return image


def extract_ocr(image: Image.Image) -> tuple[str, float | None, dict]:
    prepared = ImageOps.autocontrast(ImageOps.grayscale(image))
    prepared = ImageEnhance.Contrast(prepared).enhance(1.35)
    prepared = prepared.filter(ImageFilter.SHARPEN)
    data = pytesseract.image_to_data(
        prepared, output_type=Output.DICT, config="--oem 1 --psm 3"
    )
    raw = pytesseract.image_to_string(prepared, config="--oem 1 --psm 3")
    confidences: list[float] = []
    for value in data.get("conf", []):
        try:
            score = float(value)
        except (TypeError, ValueError):
            continue
        if score >= 0:
            confidences.append(score)
    mean = round(sum(confidences) / len(confidences), 1) if confidences else None
    return raw, mean, data


def clean_lines(raw: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", line).strip()
        for line in raw.splitlines()
        if line.strip()
    ]


def best_segment(lines: list[str], expected: str, max_lines: int = 2) -> str | None:
    target = fold_text(expected)
    best_score = 0.0
    best_value = None
    for start in range(len(lines)):
        for width in range(1, min(max_lines, len(lines) - start) + 1):
            segment = " ".join(lines[start : start + width])
            candidate = fold_text(segment)
            score = max(
                fuzz.ratio(target, candidate), fuzz.partial_ratio(target, candidate)
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
    tail = raw[match.start() : match.start() + 650]
    end = re.search(r"health\s+problems\s*[.]", tail, re.I)
    if end:
        tail = tail[: end.end()]
    return re.sub(r"\s+", " ", tail).strip()


def estimate_warning_boldness(image: Image.Image, data: dict) -> bool | None:
    texts = [str(value).strip() for value in data.get("text", [])]
    heading_indices: list[int] = []
    start = None
    for index, text in enumerate(texts):
        folded = fold_text(text)
        if folded == "government":
            start = index
            heading_indices = [index]
        elif (
            start is not None
            and index - start <= 3
            and folded.startswith("warning")
        ):
            heading_indices.append(index)
            break
    if len(heading_indices) < 2:
        return None

    body_indices = [
        i
        for i in range(
            heading_indices[-1] + 1, min(len(texts), heading_indices[-1] + 18)
        )
        if len(fold_text(texts[i])) >= 3
    ]
    if len(body_indices) < 4:
        return None

    gray = image.convert("L")

    def density(index: int) -> float:
        x, y, width, height = [
            int(data[key][index]) for key in ("left", "top", "width", "height")
        ]
        histogram = gray.crop((x, y, x + width, y + height)).histogram()
        return sum(histogram[:160]) / max(1, width * height)

    heading = [density(i) for i in heading_indices]
    body = sorted(value for i in body_indices if (value := density(i)) > 0)
    if not body:
        return None
    ratio = (sum(heading) / len(heading)) / body[len(body) // 2]
    if ratio >= 1.45:
        return True
    if ratio <= 1.10:
        return False
    return None


def field_result(field, expected, observed, status, reason):
    return {
        "field": field,
        "expected": expected,
        "observed": observed,
        "status": status,
        "reason": reason,
    }


def verify_label(image_bytes: bytes, application: ApplicationData) -> dict:
    started = time.perf_counter()
    image = load_image(image_bytes)
    raw, mean_confidence, ocr_data = extract_ocr(image)
    lines = clean_lines(raw)

    extracted = {
        "brand_name": best_segment(lines, application.brand_name, 1),
        "class_type": best_segment(lines, application.class_type, 2),
        "producer": best_segment(lines, application.producer_name_address, 3),
        "abv": next((line for line in lines if parse_abv(line) is not None), None),
        "volume": next(
            (line for line in lines if parse_volume_ml(line) is not None), None
        ),
        "country": next(
            (
                line
                for line in lines
                if application.country_of_origin
                and fold_text(application.country_of_origin) in fold_text(line)
            ),
            None,
        ),
        "warning": extract_warning(raw),
        "warning_caps": bool(re.search(r"GOVERNMENT\s+WARNING", raw)),
        "warning_bold": estimate_warning_boldness(image, ocr_data),
    }

    results = []
    for field, expected, observed, pass_threshold, review_threshold in [
        ("Brand name", application.brand_name, extracted["brand_name"], 99, 84),
        ("Class / type", application.class_type, extracted["class_type"], 96, 82),
        (
            "Producer / bottler",
            application.producer_name_address,
            extracted["producer"],
            94,
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
                    "No reliable text was extracted; human review is needed.",
                )
            )
            continue
        expected_folded, observed_folded = fold_text(expected), fold_text(observed)
        if (
            expected_folded == observed_folded
            or expected_folded in observed_folded
            or observed_folded in expected_folded
        ):
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
        score = max(
            fuzz.ratio(expected_folded, observed_folded),
            fuzz.partial_ratio(expected_folded, observed_folded),
        )
        status = (
            "pass"
            if score >= pass_threshold
            else "review_needed"
            if score >= review_threshold
            else "mismatch"
        )
        reason = (
            "Only insignificant typography differences were detected."
            if status == "pass"
            else "Values are similar but need human review."
            if status == "review_needed"
            else "Label text does not match the application value."
        )
        results.append(field_result(field, expected, observed, status, reason))

    expected_abv = parse_abv(application.alcohol_content)
    observed_abv = parse_abv(extracted["abv"])
    abv_status = (
        "pass"
        if expected_abv is not None
        and observed_abv is not None
        and abs(expected_abv - observed_abv) <= 0.05
        else "mismatch"
        if observed_abv is not None
        else "review_needed"
    )
    results.append(
        field_result(
            "Alcohol content",
            application.alcohol_content,
            extracted["abv"],
            abv_status,
            f"Parsed ABV: application={expected_abv}; label={observed_abv}.",
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
                "Not applicable / domestic",
                None,
                "not_required",
                "No country of origin supplied in the application data.",
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
        similarity = fuzz.partial_ratio(fold_text(CANONICAL_WARNING), fold_text(warning))
        exact = canonical in candidate
        warning_status = (
            "pass" if exact else "review_needed" if similarity >= 92 else "mismatch"
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

    bold = extracted["warning_bold"]
    format_status = (
        "pass"
        if extracted["warning_caps"] and bold is True
        else "mismatch"
        if (not extracted["warning_caps"] or bold is False)
        else "review_needed"
    )
    results.append(
        field_result(
            "Government warning format",
            "GOVERNMENT WARNING in all caps and bold; body not bold",
            f"all_caps={extracted['warning_caps']}; bold={bold}",
            format_status,
            "Visual heading check; ambiguous typography is escalated to a human.",
        )
    )

    statuses = {
        item["status"] for item in results if item["status"] != "not_required"
    }
    overall = (
        "mismatch"
        if "mismatch" in statuses
        else "review_needed"
        if "review_needed" in statuses
        else "pass"
    )

    return {
        "overall_status": overall,
        "processing_ms": int((time.perf_counter() - started) * 1000),
        "extractor": "local-tesseract-lstm",
        "results": results,
        "raw_text": raw,
        "mean_ocr_confidence": mean_confidence,
        "notes": [
            "Decision-support prototype only; it does not issue or replace a TTB regulatory determination.",
            "Images are processed in memory and are not intentionally persisted.",
        ],
    }


def sample_label_image() -> bytes:
    width, height = 1500, 1900
    image = Image.new("RGB", (width, height), "#efe8d8")
    draw = ImageDraw.Draw(image)

    def font(size: int, bold: bool = False):
        path = (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        )
        return ImageFont.truetype(path, size)

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
        (55, 55, width - 55, height - 55),
        32,
        outline="#3b3024",
        width=5,
    )
    centered("OLD TOM DISTILLERY", 145, 88, True)
    centered("KENTUCKY STRAIGHT", 295, 56, True)
    centered("BOURBON WHISKEY", 365, 72, True)
    centered("45% Alc./Vol. (90 Proof)", 520, 48)
    centered("750 mL", 595, 44)
    centered("Bottled by Old Tom Distillery", 735, 34)
    centered("Frankfort, KY", 785, 34)

    x, y = 120, 1050
    heading_font = font(30, True)
    body_font = font(30)
    draw.text((x, y), "GOVERNMENT WARNING:", fill="#111111", font=heading_font)
    cursor_x = x + draw.textlength("GOVERNMENT WARNING: ", font=heading_font)
    body = CANONICAL_WARNING.split(":", 1)[1].strip()
    for word in body.split():
        token = word + " "
        token_width = draw.textlength(token, font=body_font)
        if cursor_x + token_width > width - 120:
            y += 47
            cursor_x = 120
        draw.text((cursor_x, y), token, fill="#111111", font=body_font)
        cursor_x += token_width

    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LabelCheck AI</title>
<style>
:root{--navy:#0d2343;--blue:#13315c;--ink:#18202b;--muted:#667085;--line:#dfe3e8}
*{box-sizing:border-box}body{margin:0;background:#f5f7fa;color:var(--ink);font-family:Inter,system-ui,Segoe UI,sans-serif;line-height:1.5}
header{background:var(--navy);color:#fff;padding:20px}.shell{max-width:1100px;margin:auto;padding:0 18px}
.hero{padding:42px 0 20px}.hero h1{font-size:clamp(2rem,5vw,3.2rem);max-width:820px;line-height:1.05;margin:8px 0 14px;letter-spacing:-.035em}
.muted{color:var(--muted)}.card{background:#fff;border:1px solid var(--line);border-radius:16px;padding:24px;margin:18px 0;box-shadow:0 10px 30px #10182812}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.wide{grid-column:1/-1}label{font-size:13px;font-weight:700}
input{width:100%;margin-top:6px;padding:11px;border:1px solid #cfd5dd;border-radius:9px;font:inherit}
button{padding:11px 16px;border-radius:9px;border:1px solid #bfc7d2;font-weight:750;background:#fff;cursor:pointer}
.primary{background:var(--blue);color:#fff;border-color:var(--blue)}.drop{display:block;border:1.5px dashed #aeb7c4;padding:28px;border-radius:12px;margin:18px 0;text-align:center}
.notice{background:#edf5ff;border-left:4px solid var(--blue);padding:12px 14px;border-radius:10px}.hidden{display:none}
.pill{display:inline-block;padding:6px 10px;border-radius:999px;font-weight:800;font-size:12px}.pass{background:#ecfdf3;color:#166534}.mismatch{background:#fff1f0;color:#b42318}.review_needed{background:#fff8db;color:#9a6700}.not_required{background:#f2f4f7;color:#475467}
table{width:100%;border-collapse:collapse;min-width:850px}th,td{padding:11px;text-align:left;border-bottom:1px solid #e4e7ec;vertical-align:top;font-size:13px}th{background:#f8fafc}.wrap{overflow:auto}pre{white-space:pre-wrap;background:#f8fafc;padding:12px;border-radius:10px}
@media(max-width:700px){.grid{grid-template-columns:1fr}.wide{grid-column:auto}}
</style>
</head>
<body>
<header><div class="shell"><strong>LabelCheck AI</strong> · human-in-the-loop decision support</div></header>
<main class="shell">
<section class="hero"><p class="muted">AI-POWERED ALCOHOL LABEL VERIFICATION</p><h1>Check routine label fields in seconds. Escalate uncertainty to a person.</h1><p class="muted">Local machine-learning OCR + deterministic verification rules + evidence-first review.</p></section>
<div class="notice"><b>Prototype:</b> assists compliance review; does not issue a TTB approval.</div>
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
<label class="drop">Label image<input style="display:block;margin:10px auto 0;max-width:420px" type="file" name="image" accept="image/jpeg,image/png,image/webp" required></label>
<button class="primary" id="verify-button">Verify label</button>
<button type="button" id="demo-button">Run built-in demo</button>
<span id="client-status" class="muted" role="status" aria-live="polite" style="margin-left:10px"></span>
</form>
</section>
<section id="result-section" class="card hidden">
<h2>Verification result</h2>
<p><span id="overall" class="pill"></span> <span id="timing" class="muted"></span></p>
<div class="wrap"><table><thead><tr><th>Field</th><th>Application</th><th>Observed label</th><th>Result</th><th>Why</th></tr></thead><tbody id="rows"></tbody></table></div>
<details><summary>Technical evidence</summary><pre id="raw"></pre></details>
</section>
<section class="card">
<details><summary><b>Batch review (stretch feature)</b></summary>
<p class="muted">Upload a CSV manifest and matching images. Prototype limit: 20.</p>
<form id="batch-form">
<label>CSV manifest<input type="file" name="manifest" accept=".csv" required></label>
<label>Images<input type="file" name="images" multiple accept="image/*" required></label>
<button>Run batch</button>
</form>
<pre id="batch-output"></pre>
</details>
</section>
</main>
<script>
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function render(j){
  const section=document.getElementById("result-section");
  const overall=document.getElementById("overall");
  overall.className="pill "+j.overall_status;
  overall.textContent=j.overall_status.replace("_"," ").toUpperCase();
  document.getElementById("timing").textContent="Processed in "+(j.processing_ms/1000).toFixed(2)+" s · "+j.extractor;
  document.getElementById("rows").innerHTML=j.results.map(x=>"<tr><td><b>"+esc(x.field)+"</b></td><td>"+esc(x.expected)+"</td><td>"+esc(x.observed??"Not detected")+"</td><td><span class='pill "+x.status+"'>"+esc(x.status.replace("_"," "))+"</span></td><td>"+esc(x.reason)+"</td></tr>").join("");
  document.getElementById("raw").textContent=j.raw_text||"(none)";
  section.classList.remove("hidden");
  section.scrollIntoView({behavior:"smooth"});
}
async function readJson(response){
  const text=await response.text();
  try{return JSON.parse(text)}catch{return {detail:text||"Unexpected server response"}}
}
document.getElementById("verify-form").onsubmit=async e=>{
  e.preventDefault();
  const button=document.getElementById("verify-button");
  const status=document.getElementById("client-status");
  button.disabled=true; status.textContent="Verifying label…";
  try{
    const response=await fetch("/api/verify",{method:"POST",body:new FormData(e.target)});
    const data=await readJson(response);
    if(!response.ok) throw new Error(data.detail||"Verification failed");
    render(data); status.textContent="Verification complete.";
  }catch(err){
    status.textContent="Verification failed.";
    alert(err.message||"Verification failed");
  }finally{button.disabled=false}
};
document.getElementById("demo-button").onclick=async()=>{
  const button=document.getElementById("demo-button");
  const status=document.getElementById("client-status");
  button.disabled=true; status.textContent="Running built-in demo…";
  try{
    const response=await fetch("/api/demo",{method:"POST"});
    const data=await readJson(response);
    if(!response.ok) throw new Error(data.detail||"Built-in demo failed");
    render(data); status.textContent="Demo complete.";
  }catch(err){
    status.textContent="Demo failed.";
    alert(err.message||"Built-in demo failed");
  }finally{button.disabled=false}
};
document.getElementById("batch-form").onsubmit=async e=>{
  e.preventDefault();
  const response=await fetch("/api/batch",{method:"POST",body:new FormData(e.target)});
  const data=await response.json();
  document.getElementById("batch-output").textContent=JSON.stringify(data,null,2);
};
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML


@app.get("/health")
def health():
    return {"status": "ok", "extractor": "local-tesseract-lstm"}


@app.post("/api/verify")
async def api_verify(
    image: UploadFile = File(...),
    brand_name: str = Form(...),
    class_type: str = Form(...),
    alcohol_content: str = Form(...),
    net_contents: str = Form(...),
    producer_name_address: str = Form(...),
    country_of_origin: str | None = Form(None),
):
    if image.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(415, "Use JPG, PNG, or WebP.")
    application = ApplicationData(
        brand_name=brand_name,
        class_type=class_type,
        alcohol_content=alcohol_content,
        net_contents=net_contents,
        producer_name_address=producer_name_address,
        country_of_origin=country_of_origin or None,
    )
    return verify_label(await image.read(), application)


@app.api_route("/api/demo", methods=["GET", "POST"])
def api_demo():
    try:
        application = ApplicationData(
            brand_name="OLD TOM DISTILLERY",
            class_type="Kentucky Straight Bourbon Whiskey",
            alcohol_content="45% Alc./Vol. (90 Proof)",
            net_contents="750 mL",
            producer_name_address="Bottled by Old Tom Distillery, Frankfort, KY",
        )
        return verify_label(sample_label_image(), application)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Built-in demo failed: {type(exc).__name__}: {exc}",
        ) from exc


@app.post("/api/batch")
async def api_batch(
    manifest: UploadFile = File(...), images: list[UploadFile] = File(...)
):
    if len(images) > 20:
        raise HTTPException(400, "Prototype batch limit is 20 images.")
    try:
        manifest_rows = list(
            csv.DictReader(io.StringIO((await manifest.read()).decode("utf-8-sig")))
        )
    except Exception as exc:
        raise HTTPException(400, "Manifest must be a readable UTF-8 CSV.") from exc

    image_map = {image.filename: await image.read() for image in images}
    jobs = []
    for row in manifest_rows:
        filename = row.get("filename")
        if filename not in image_map:
            continue
        try:
            application = ApplicationData(
                brand_name=row["brand_name"],
                class_type=row["class_type"],
                alcohol_content=row["alcohol_content"],
                net_contents=row["net_contents"],
                producer_name_address=row["producer_name_address"],
                country_of_origin=row.get("country_of_origin") or None,
            )
        except Exception:
            continue
        jobs.append((filename, image_map[filename], application))

    if not jobs:
        raise HTTPException(
            400, "No manifest rows matched the uploaded image filenames."
        )

    started = time.perf_counter()
    items = []
    with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as pool:
        futures = {
            pool.submit(verify_label, payload, application): filename
            for filename, payload, application in jobs
        }
        for future in as_completed(futures):
            filename = futures[future]
            try:
                outcome = future.result()
                items.append(
                    {
                        "filename": filename,
                        "overall_status": outcome["overall_status"],
                        "processing_ms": outcome["processing_ms"],
                    }
                )
            except Exception:
                items.append(
                    {
                        "filename": filename,
                        "overall_status": "review_needed",
                        "processing_ms": 0,
                    }
                )

    return {
        "total": len(items),
        "total_processing_ms": int((time.perf_counter() - started) * 1000),
        "items": sorted(items, key=lambda item: item["filename"].casefold()),
    }
