from fastapi.testclient import TestClient

from app.main import (
    ApplicationData,
    CANONICAL_WARNING,
    app,
    fold_text,
    parse_abv,
    parse_alcohol,
    parse_volume_ml,
    verify_ocr_text,
)

client = TestClient(app)

SAMPLE_OCR = """OLD TOM DISTILLERY
KENTUCKY STRAIGHT
BOURBON WHISKEY
45% Alc./Vol. (90 Proof)
750 mL
Bottled by Old Tom Distillery
Frankfort, KY
""" + CANONICAL_WARNING


def application():
    return ApplicationData(
        brand_name="OLD TOM DISTILLERY",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol. (90 Proof)",
        net_contents="750 mL",
        producer_name_address="Bottled by Old Tom Distillery, Frankfort, KY",
    )


def test_health_reports_browser_ocr():
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["ocr_location"] == "browser"


def test_brand_normalization_tolerates_case_and_punctuation():
    assert fold_text("STONE'S THROW") == fold_text("Stone’s Throw")


def test_abv_parses_proof():
    assert parse_abv("90 Proof") == 45.0


def test_volume_normalizes_liters():
    assert parse_volume_ml("0.75 L") == 750.0


def test_deterministic_verifier_passes_complete_matching_text():
    payload = verify_ocr_text(
        SAMPLE_OCR,
        application(),
        ocr_confidence=95.0,
        warning_bold=True,
    )
    assert payload["overall_status"] == "pass"
    fields = {row["field"]: row["status"] for row in payload["results"]}
    assert fields["Brand name"] == "pass"
    assert fields["Class / type"] == "pass"
    assert fields["Alcohol content"] == "pass"
    assert fields["Net contents"] == "pass"
    assert fields["Government warning wording"] == "pass"
    assert fields["Government warning format"] == "pass"


def test_wrong_brand_is_not_silently_accepted():
    changed = application().model_copy(
        update={"brand_name": "COMPLETELY DIFFERENT BRAND"}
    )
    payload = verify_ocr_text(
        SAMPLE_OCR,
        changed,
        ocr_confidence=95.0,
        warning_bold=True,
    )
    brand = next(row for row in payload["results"] if row["field"] == "Brand name")
    assert brand["status"] in {"mismatch", "review_needed"}
    assert payload["overall_status"] != "pass"


def test_unverified_boldness_escalates_to_human_review():
    payload = verify_ocr_text(
        SAMPLE_OCR,
        application(),
        ocr_confidence=95.0,
        warning_bold=None,
    )
    warning = next(
        row
        for row in payload["results"]
        if row["field"] == "Government warning format"
    )
    assert warning["status"] == "review_needed"
    assert payload["overall_status"] == "review_needed"


def test_verify_text_endpoint():
    response = client.post(
        "/api/verify-text",
        data={
            "brand_name": "OLD TOM DISTILLERY",
            "class_type": "Kentucky Straight Bourbon Whiskey",
            "alcohol_content": "45% Alc./Vol. (90 Proof)",
            "net_contents": "750 mL",
            "producer_name_address": "Bottled by Old Tom Distillery, Frankfort, KY",
            "country_of_origin": "",
            "ocr_text": SAMPLE_OCR,
            "ocr_confidence": "95",
            "warning_bold": "true",
        },
    )
    assert response.status_code == 200
    assert response.json()["overall_status"] == "pass"


def test_demo_image_is_available():
    response = client.get("/api/demo-image")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert len(response.content) > 1000


def test_stones_throw_typography_is_not_a_mismatch():
    text = SAMPLE_OCR.replace("OLD TOM DISTILLERY", "STONE'S THROW", 1)
    changed = application().model_copy(update={"brand_name": "Stone's Throw"})
    payload = verify_ocr_text(
        text,
        changed,
        ocr_confidence=95.0,
        warning_bold=None,
    )
    brand = next(row for row in payload["results"] if row["field"] == "Brand name")
    assert brand["status"] == "pass"
    assert payload["overall_status"] == "review_needed"


def test_volume_parses_thousands_separator():
    assert parse_volume_ml("1,750 mL") == 1750.0


def test_contradictory_percent_and_proof_are_detected():
    parsed = parse_alcohol("45% Alc./Vol. (80 Proof)")
    assert parsed["abv"] == 45.0
    assert parsed["contradictory"] is True


def test_incomplete_class_description_does_not_pass():
    text = SAMPLE_OCR.replace(
        "KENTUCKY STRAIGHT\nBOURBON WHISKEY",
        "BOURBON WHISKEY",
        1,
    )
    payload = verify_ocr_text(
        text,
        application(),
        ocr_confidence=95.0,
        warning_bold=None,
    )
    result = next(row for row in payload["results"] if row["field"] == "Class / type")
    assert result["status"] != "pass"


def test_missing_producer_address_does_not_pass():
    text = SAMPLE_OCR.replace(
        "Bottled by Old Tom Distillery\nFrankfort, KY",
        "Bottled by Old Tom Distillery",
        1,
    )
    payload = verify_ocr_text(
        text,
        application(),
        ocr_confidence=95.0,
        warning_bold=None,
    )
    result = next(
        row for row in payload["results"] if row["field"] == "Producer / bottler"
    )
    assert result["status"] != "pass"


def test_contradictory_label_abv_and_proof_is_mismatch():
    text = SAMPLE_OCR.replace(
        "45% Alc./Vol. (90 Proof)",
        "45% Alc./Vol. (80 Proof)",
        1,
    )
    payload = verify_ocr_text(
        text,
        application(),
        ocr_confidence=95.0,
        warning_bold=None,
    )
    result = next(row for row in payload["results"] if row["field"] == "Alcohol content")
    assert result["status"] == "mismatch"
    assert "internally contradictory" in result["reason"]
