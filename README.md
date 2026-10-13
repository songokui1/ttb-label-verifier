# LabelCheck AI

AI-assisted alcohol-label verification prototype built for the U.S. Department of the Treasury take-home exercise.

LabelCheck AI helps a human compliance reviewer compare alcohol-label artwork with application data. It extracts visible label text in the reviewer browser, applies deterministic field-specific verification rules on the server, and returns clear outcomes: PASS, MISMATCH, or REVIEW NEEDED.

> This is a proof-of-concept decision-support tool. It does not issue a TTB approval or replace a regulatory determination.

## Live prototype

- **Application:** https://ttb-label-verifier-r392.onrender.com
- **Health check:** https://ttb-label-verifier-r392.onrender.com/health

Manual live testing on October 13, 2026 measured approximately **2.48 seconds click-to-result on a fresh private-browser session**, with subsequent warm reviews around **0.55–0.65 seconds** after the local OCR worker was initialized.

## Design summary

The stakeholder notes emphasize three practical constraints: routine matching consumes reviewer time, useful results need to return quickly, and government networks may block outbound ML endpoints.

The prototype therefore uses a local-first architecture:

label image + application data  
→ browser-hosted Tesseract.js WebAssembly OCR  
→ extracted text + confidence only  
→ deterministic verification rules  
→ PASS / MISMATCH / REVIEW NEEDED  
→ human reviewer

The OCR engine and English language data are bundled into the deployed application and served from the same origin. The single-label workflow does not upload the source label image to the backend and does not depend on an external AI API.

## Tools used

- Python 3.12 and FastAPI for the web application and verification API
- Tesseract.js 7 WebAssembly OCR for local browser-side neural text extraction
- Pillow for generation of the synthetic demonstration label
- RapidFuzz for controlled text-similarity checks
- Pydantic for typed application data
- Pytest and GitHub Actions for automated verification
- Docker for reproducible packaging
- Render for the public prototype deployment

## Core features

- JPG, PNG, and WebP label input
- Browser-side OCR with locally bundled WebAssembly and English language data
- No cloud AI API or API key
- Brand-name normalization that tolerates harmless case and punctuation differences
- ABV parsing, including proof-to-ABV conversion
- Net-contents normalization such as 0.75 L = 750 mL
- Producer/bottler and country-of-origin comparison
- Canonical Government Health Warning wording check
- Explicit all-caps GOVERNMENT WARNING heading check
- Conservative human-review escalation when bold typography cannot be established automatically
- Field-level evidence and explanations
- PASS / MISMATCH / REVIEW NEEDED outcomes
- OCR confidence and click-to-result latency displayed in the UI
- Built-in synthetic demonstration label
- Browser-side batch-review stretch feature using a CSV manifest and up to 20 images
- Automated unit tests plus an exact Docker deployment smoke test in GitHub Actions

## Requirements traceability

| Take-home expectation | Implementation |
| --- | --- |
| Standalone proof-of-concept | Independent FastAPI application; no COLA integration |
| Compare label artwork with application data | Label image + application form + field-by-field results |
| Common label fields | Brand, class/type, ABV, net contents, producer/bottler, country of origin, warning |
| Exact warning review | Canonical wording comparison and all-caps heading check |
| About five-second usability target | Click-to-result latency is displayed; processing is moved off the constrained server and into the reviewer's browser |
| Simple UX | One main review form, one primary action, and clear result states |
| Human judgment for nuanced cases | Ambiguous text or typography is escalated to REVIEW NEEDED |
| Batch-upload opportunity | Optional CSV + multi-image batch workflow |
| Network restrictions | OCR assets are bundled and served from the same application; no external ML endpoint is required |
| README setup/run documentation | Docker and local-development instructions below |
| Approach, tools, assumptions | Documented here and in docs/architecture.md |
| Deployed prototype | Docker/Render configuration included; public URL supplied in the application submission |

## Reviewer quick test

1. Open the deployed application.
2. Click **Run built-in demo**.
3. The browser initializes the locally hosted OCR engine, analyzes a synthetic distilled-spirits label, and sends only the extracted text to the verification endpoint.
4. Inspect the field-by-field evidence, OCR confidence, and measured click-to-result time.
5. Upload another label and change one or more expected application values to observe mismatch and review behavior.

The first OCR run can take longer than subsequent runs because the browser must initialize the WebAssembly OCR engine. The worker is reused for later labels in the same page session.

## Run with Docker

    git clone https://github.com/songokui1/ttb-label-verifier.git
    cd ttb-label-verifier
    docker build -t labelcheck-ai .
    docker run --rm -p 10000:10000 labelcheck-ai

Then open http://localhost:10000.

## Local development

For backend-only development:

macOS / Linux:

    python -m venv .venv
    source .venv/bin/activate

Windows PowerShell:

    python -m venv .venv
    .\.venv\Scripts\Activate.ps1

Windows Command Prompt:

    python -m venv .venv
    .venv\Scripts\activate.bat

Then:

    pip install -r requirements-dev.txt
    uvicorn app.main:app --reload
    python -m pytest

These direct Python commands are intentionally for backend/API development and tests. The browser OCR assets are assembled during the Docker build, so use the **Run with Docker** path above to exercise the complete application locally, including OCR.

## Batch review

The stretch workflow accepts a CSV manifest and matching images. Required columns are:

    filename,brand_name,class_type,alcohol_content,net_contents,producer_name_address,country_of_origin

OCR is executed sequentially in the reviewer browser using one reused worker. The prototype limits a batch to 20 labels to keep the interactive demo predictable.

A production design for the 200–300-label importer batches described by the stakeholder should use a dedicated client or queued processing workflow with observability and durable result metadata.

A sample CSV is included in sample_data/batch_manifest.csv.

## Technical decisions

### Browser-side OCR

The stakeholder notes call out blocked outbound traffic to ML endpoints. Rather than requiring an external vision API, the deployment packages Tesseract.js, its WebAssembly core, and English language model inside the same Docker image.

This also removes OCR compute from the small prototype web server and keeps the source label image in the user's browser for the single-label workflow.

### Deterministic verification

OCR performs perception; it does not make the regulatory decision.

- Text fields use normalization and controlled similarity thresholds.
- ABV and net contents are parsed numerically.
- Government-warning wording is checked against the canonical statement.
- Ambiguous results are escalated rather than silently accepted.

### Human-in-the-loop boundary

The prototype deliberately exposes uncertainty. If OCR evidence is weak or visual typography such as warning boldness is not positively established, the result is REVIEW NEEDED rather than a fabricated pass.

## TTB guidance used

The prototype focuses on common label information described by TTB for distilled spirits, including brand name, class/type, alcohol content, net contents, name/address, country of origin for imports, and the Government Health Warning Statement.

Official references:

- https://www.ttb.gov/regulated-commodities/beverage-alcohol/distilled-spirits/labeling
- https://www.ttb.gov/regulated-commodities/beverage-alcohol/distilled-spirits/ds-labeling-home/ds-health-warning
- https://www.ttb.gov/regulated-commodities/beverage-alcohol/distilled-spirits/ds-labeling-home/ds-net-contents
- https://www.ttb.gov/regulated-commodities/beverage-alcohol/distilled-spirits/ds-labeling-home/ds-brand-name

## Assumptions and limitations

- This is not an exhaustive implementation of every beer, wine, and distilled-spirits regulation.
- The primary demonstration is a distilled-spirits workflow.
- Direct COLA integration is intentionally out of scope.
- OCR quality varies with glare, curvature, image quality, resolution, and typography.
- Exact warning boldness is conservatively escalated to a human unless positively known.
- Production use would require formal security, privacy, accessibility, records-retention, authorization, monitoring, and deployment review.
- The single-label workflow does not intentionally persist or upload the source label image.

## Deployment

The repository includes a Render Blueprint and Dockerfile.

- Runtime: Docker
- Health check: `/health`
- Region: Virginia
- Prototype plan: Free
- No AI API key required

The multi-stage Docker build vendors the browser OCR dependencies into the final image, so runtime OCR does not depend on npm, jsDelivr, or another third-party ML endpoint.

## Evaluation alignment

- **Correctness and completeness:** core field comparisons and warning checks
- **Code quality and organization:** typed data model, isolated rule functions, tests
- **Technical choices:** browser-side WebAssembly OCR plus deterministic verification
- **UX and error handling:** simple workflow, visible progress, confidence, explicit uncertainty
- **Attention to requirements:** latency measurement, standalone deployment, blocked-network consideration
- **Creative problem-solving:** same-origin browser inference, human-review boundary, and batch-processing stretch workflow
