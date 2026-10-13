from fastapi.testclient import TestClient

from app.main import (
    ApplicationData,
    app,
    fold_text,
    parse_abv,
    parse_volume_ml,
    sample_label_image,
    verify_label,
)

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_brand_normalization_tolerates_case_and_punctuation():
    assert fold_text("STONE'S THROW") == fold_text("Stone’s Throw")


def test_abv_parses_proof():
    assert parse_abv("90 Proof") == 45.0


def test_volume_normalizes_liters():
    assert parse_volume_ml("0.75 L") == 750.0


def test_builtin_demo_meets_core_expectations():
    response = client.post("/api/demo")
    assert response.status_code == 200
    payload = response.json()
    assert payload["overall_status"] == "pass"
    assert payload["processing_ms"] < 5000
    fields = {row["field"]: row["status"] for row in payload["results"]}
    assert fields["Brand name"] == "pass"
    assert fields["Alcohol content"] == "pass"
    assert fields["Government warning wording"] == "pass"


def test_wrong_brand_is_not_silently_accepted():
    application = ApplicationData(
        brand_name="COMPLETELY DIFFERENT BRAND",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_content="45% Alc./Vol. (90 Proof)",
        net_contents="750 mL",
        producer_name_address="Bottled by Old Tom Distillery, Frankfort, KY",
    )
    payload = verify_label(sample_label_image(), application)
    brand = next(row for row in payload["results"] if row["field"] == "Brand name")
    assert brand["status"] in {"mismatch", "review_needed"}
    assert payload["overall_status"] != "pass"


def test_unreadable_upload_returns_clear_client_error():
    response = client.post(
        "/api/verify",
        data={
            "brand_name": "OLD TOM DISTILLERY",
            "class_type": "Kentucky Straight Bourbon Whiskey",
            "alcohol_content": "45%",
            "net_contents": "750 mL",
            "producer_name_address": "Old Tom Distillery",
            "country_of_origin": "",
        },
        files={"image": ("broken.png", b"not actually an image", "image/png")},
    )
    assert response.status_code == 400
    assert "readable image" in response.json()["detail"]
