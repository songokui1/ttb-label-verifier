from fastapi.testclient import TestClient

from app.main import app, fold_text, parse_abv, parse_volume_ml

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
