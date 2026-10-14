"""Deterministic label-verification domain logic.

This module intentionally contains no UI or route definitions. OCR perception happens
in the browser; these functions normalize extracted text, apply field-specific rules,
and conservatively escalate uncertainty to human review.
"""
from __future__ import annotations

import re
import time

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from rapidfuzz import fuzz

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


def _all_numeric_matches(pattern: str, text: str) -> list[float]:
    return [float(match.group(1)) for match in re.finditer(pattern, text, re.I)]


def _alcohol_percent_values(text: str, *, require_context: bool) -> list[float]:
    if not require_context:
        return _all_numeric_matches(
            r"(?<![\d.])(\d{1,3}(?:\.\d+)?)(?!\d)\s*%",
            text,
        )

    values = []

    # Common label forms such as "45% Alc./Vol.", "45% ABV",
    # and "45% alcohol by volume". The context must be adjacent to
    # this percentage so marketing claims such as "100% NATURAL"
    # are not mistaken for alcohol content just because an ABV
    # statement appears later on the same OCR line.
    post_context = re.compile(
        r"(?<![\d.])(\d{1,3}(?:\.\d+)?)(?!\d)\s*%\s*"
        r"(?=(?:"
        r"abv\b|"
        r"alcohol\s+by\s+volume\b|"
        r"alc(?:ohol)?\.?\s*(?:/|by)?\s*vol(?:ume)?\.?\b"
        r"))",
        re.I,
    )
    values.extend(float(match.group(1)) for match in post_context.finditer(text))

    # Also support context before the value, e.g. "ABV 45%" or
    # "Alcohol by volume: 45%".
    pre_context = re.compile(
        r"(?:"
        r"\babv\b|"
        r"\balcohol\s+by\s+volume\b|"
        r"\balc(?:ohol)?\.?\s*(?:/|by)?\s*vol(?:ume)?\.?"
        r")\s*[:=\-]?\s*"
        r"(?<![\d.])(\d{1,3}(?:\.\d+)?)(?!\d)\s*%",
        re.I,
    )
    values.extend(float(match.group(1)) for match in pre_context.finditer(text))

    # Preserve order while removing duplicates caused by equivalent
    # contextual patterns matching the same declaration.
    return list(dict.fromkeys(values))


def _alcohol_proof_values(text: str) -> list[float]:
    return _all_numeric_matches(
        r"(?<![\d.])(\d{1,3}(?:\.\d+)?)(?!\d)\s*proof\b",
        text,
    )


def _summarize_alcohol_values(
    percents_raw: list[float],
    proofs_raw: list[float],
) -> dict:
    valid_percents = [value for value in percents_raw if 0.0 <= value <= 100.0]
    valid_proofs = [value for value in proofs_raw if 0.0 <= value <= 200.0]

    invalid = (
        len(valid_percents) != len(percents_raw)
        or len(valid_proofs) != len(proofs_raw)
    )

    comparable_values = valid_percents + [proof / 2 for proof in valid_proofs]
    contradictory = (
        len(comparable_values) > 1
        and max(comparable_values) - min(comparable_values) > 0.05
    )

    first_percent = valid_percents[0] if valid_percents else None
    first_proof = valid_proofs[0] if valid_proofs else None
    resolved = (
        first_percent
        if first_percent is not None
        else (first_proof / 2 if first_proof is not None else None)
    )

    return {
        "abv": resolved,
        "percent": first_percent,
        "proof": first_proof,
        "percents": valid_percents,
        "proofs": valid_proofs,
        "percent_raw": percents_raw[0] if percents_raw else None,
        "proof_raw": proofs_raw[0] if proofs_raw else None,
        "percent_values_raw": percents_raw,
        "proof_values_raw": proofs_raw,
        "invalid": invalid,
        "contradictory": contradictory,
    }


def parse_alcohol(value: str | None) -> dict:
    """Parse an application alcohol-content field.

    The field itself is alcohol-specific, so every percentage in it is
    considered relevant. Multiple declarations are retained so contradictory
    values cannot be hidden behind the first match.
    """
    text = value or ""
    return _summarize_alcohol_values(
        _alcohol_percent_values(text, require_context=False),
        _alcohol_proof_values(text),
    )


def parse_abv(value: str | None) -> float | None:
    parsed = parse_alcohol(value)
    return None if parsed["invalid"] else parsed["abv"]


def extract_label_alcohol(lines: list[str]) -> dict | None:
    statements = []
    percent_values = []
    proof_values = []

    for line in lines:
        line_percents = _alcohol_percent_values(line, require_context=True)
        line_proofs = _alcohol_proof_values(line)

        if line_percents or line_proofs:
            statements.append(line)
            percent_values.extend(line_percents)
            proof_values.extend(line_proofs)

    if not statements:
        return None

    parsed = _summarize_alcohol_values(percent_values, proof_values)
    return {
        "text": " | ".join(dict.fromkeys(statements)),
        "abv": None
        if parsed["invalid"] or parsed["contradictory"]
        else parsed["abv"],
        "percents": parsed["percents"],
        "proofs": parsed["proofs"],
        "invalid": parsed["invalid"],
        "contradictory": parsed["contradictory"],
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


def is_country_origin_evidence(text: str | None, country: str | None) -> bool:
    """Return True only when a country token is plausibly an origin declaration.

    A bare token appearing inside unrelated prose (for example "contact us")
    must not create an automatic PASS. Exact country-only lines are accepted,
    while longer lines require an explicit origin cue.
    """
    if not text or not country or not contains_token_phrase(text, country):
        return False

    folded_line = fold_text(text)
    folded_country = fold_text(country)
    if folded_line == folded_country:
        return True

    origin_cues = (
        r"\bproduct\s+of\b",
        r"\bproduce\s+of\b",
        r"\bcountry\s+of\s+origin\b",
        r"\borigin\s*[:\-]?\b",
        r"\bmade\s+in\b",
        r"\bproduced\s+in\b",
        r"\bimported\s+from\b",
    )
    return any(re.search(pattern, folded_line, re.I) for pattern in origin_cues)

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
                and is_country_origin_evidence(line, application.country_of_origin)
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
