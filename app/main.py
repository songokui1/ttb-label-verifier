from __future__ import annotations

from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app.demo import sample_label_image
from app.verification import ApplicationData, verify_ocr_text

app = FastAPI(title="LabelCheck AI", version="2.0.0")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
INDEX_HTML = Path(__file__).with_name("index.html")

@app.get("/")
def home():
    return FileResponse(INDEX_HTML)


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
    try:
        application = ApplicationData(
            brand_name=brand_name,
            class_type=class_type,
            alcohol_content=alcohol_content,
            net_contents=net_contents,
            producer_name_address=producer_name_address,
            country_of_origin=(country_of_origin.strip() if country_of_origin else None),
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    return verify_ocr_text(
        raw=ocr_text,
        application=application,
        ocr_confidence=ocr_confidence,
        warning_bold=warning_bold,
    )
