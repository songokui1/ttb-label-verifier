# LabelCheck AI

AI-assisted alcohol-label verification prototype built for the U.S. Department of the Treasury take-home exercise.

LabelCheck AI helps a human compliance reviewer compare alcohol-label artwork with application data. It extracts visible label text in the reviewer browser, applies deterministic field-specific verification rules on the server, and returns clear outcomes: PASS, MISMATCH, or REVIEW NEEDED.

> This is a proof-of-concept decision-support tool. It does not issue a TTB approval or replace a regulatory determination.

## Live prototype

- **Application:** https://ttb-label-verifier-r392.onrender.com
- **Health check:** https://ttb-label-verifier-r392.onrender.com/health

Manual live testing on October 13, 2026 observed fresh private-browser click-to-result times of approximately **2.5–4.2 seconds**, with subsequent warm reviews around **0.5–0.7 seconds** after the local OCR worker was initialized. Additional manual checks following the warning-OCR correction recorded **1.55 seconds** for the built-in demo and **2.03 seconds** for a real back-label review (REVIEW NEEDED). These are observed results, not a performance guarantee; timing depends on the browser, device, and network. The in-app timer starts when the reviewer clicks a verification button and does **not** include initial navigation to the site or any Render service wake-up time.

## Design summary

The stakeholder notes emphasize three practical constraints: routine matching consumes reviewer time, useful results need to return quickly, and government networks may block outbound ML endpoints.

The prototype therefore uses a local-first architecture:

label image + application data  
→ browser-hosted Tesseract.js WebAssembly OCR  
→ extracted text + confidence only  
→ deterministic verification rules  
→ PASS / MISMATCH / REVIEW NEEDED  
→ human reviewer

The OCR engine and English language data are bundled into the deployed application and served from the same origin. In single-label review, **the source image stays in the browser**; extracted OCR text, confidence, expected application fields, and the optional demo-known warning-format flag are sent to the FastAPI verifier. No external AI inference API or model API key is required.

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
- Producer/bottler comparison with exact-normalized PASS behavior; near matches escalate to human review
- Country-of-origin comparison using token/phrase matching rather than substring matching
- Canonical Government Health Warning wording check
- Explicit all-caps GOVERNMENT WARNING heading check
- Conservative human-review escalation when bold typography cannot be established automatically
- Field-level evidence, explanations, and an on-page label preview for human review
- PASS / MISMATCH / REVIEW NEEDED outcomes
- OCR confidence and click-to-result latency displayed in the UI
- Built-in synthetic demonstration label
- Browser-side batch-review stretch feature using a validated CSV manifest and up to 20 images
- Automated unit tests plus an exact Docker deployment smoke test in GitHub Actions

## Requirements traceability

| Take-home expectation | Implementation |
| --- | --- |
| Standalone proof-of-concept | Independent FastAPI application; no COLA integration |
| Compare label artwork with application data | Label image + application form + field-by-field results |
| Common label fields | Brand, class/type, ABV, net contents, producer/bottler, country of origin, warning |
| Exact warning review | Canonical wording, uppercase heading, and boldness evidence are handled separately; OCR uncertainty escalates to human review |
| About five-second usability target | The UI measures click-to-result time (browser OCR + API + rendering); OCR runs on the reviewer device, with observed timings and their limits noted above |
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
3. The browser initializes the locally hosted OCR engine, analyzes a synthetic distilled-spirits label, and sends only the extracted text to the verification endpoint. The demo image is generated by this application, so its bold warning heading is known by construction; ordinary uploads do not receive that assumption.
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

## Code layout and verification

- `app/main.py` — FastAPI endpoints, request validation, and serving the application.
- `app/verification.py` — deterministic field extraction, comparison rules, and reviewer-escalation logic.
- `app/demo.py` — synthetic label image for the built-in demo.
- `app/index.html` — single-page reviewer UI, in-browser OCR, and batch workflow.
- `tests/test_app.py` — **36 passing tests** at the current assessed revision, including regressions for false matches, conflicting ABV declarations, and missing versus incorrect government warnings.
- `.github/workflows/tests.yml` — tests, a build/start of the deployment Docker image, bundled OCR asset checks, and Chromium browser smoke tests.

[GitHub Actions test history](https://github.com/songokui1/ttb-label-verifier/actions/workflows/tests.yml) · [Verified 36-test run](https://github.com/songokui1/ttb-label-verifier/actions/runs/38506580231)

## Batch review

The stretch workflow accepts a CSV manifest and matching images. Required columns are:

    filename,brand_name,class_type,alcohol_content,net_contents,producer_name_address,country_of_origin

OCR is executed sequentially in the reviewer browser using one reused worker. Before processing, the prototype validates required CSV headers, required row values, duplicate filenames, missing image files, and the 20-row prototype limit.

Batch results are displayed only after the full batch completes successfully. If OCR or verification fails on any row, the batch stops, shows the failure, and does not present a partial summary; the reviewer corrects the issue and reruns the batch.

A production design for the 200–300-label importer batches described by the stakeholder should use a dedicated client or queued processing workflow with observability and durable result metadata.

A CSV manifest template is included in `sample_data/batch_manifest.csv`. Its `sample_label.png` filename is illustrative: **no matching image is bundled**, so supply an image with that filename or edit the manifest to match your selected image. The `country_of_origin` column must exist, but its value can be empty.

## Technical decisions

### Browser-side OCR

The stakeholder notes call out blocked outbound traffic to ML endpoints. Rather than requiring an external vision API, the deployment packages Tesseract.js, its WebAssembly core, and English language model inside the same Docker image.

This also removes OCR compute from the small prototype web server and keeps the source label image in the user's browser for the single-label workflow.

### Deterministic verification

OCR performs perception; it does not make the regulatory decision.

- Text fields only auto-PASS when normalized values are equal; close fuzzy matches escalate to REVIEW NEEDED.
- Alcohol extraction is context-aware, bounded to plausible numeric ranges, and checks percent/proof declarations for conflicts.
- ABV and net contents are parsed numerically. Proof-only equivalence is treated as review evidence rather than proof of regulatory completeness.
- Government-warning wording is compared with the canonical statement, ignoring differences in letter case and whitespace; heading capitalization and boldness are evaluated as distinct checks.
- A missing OCR detection is **not proof of a missing warning**. If OCR cannot establish the warning, wording and format return REVIEW NEEDED. A detected, materially incorrect warning or a detected non-uppercase heading still returns MISMATCH.

### Human-in-the-loop boundary

Every field displays a result and supporting evidence:

- **PASS:** the available evidence satisfies that field's implemented check, not a regulatory approval.
- **MISMATCH:** a detected conflict or violation under the implemented rules.
- **REVIEW NEEDED:** OCR or other available evidence is insufficient to decide reliably; the reviewer should inspect the displayed label image.
- **NOT REQUIRED:** used when no country-of-origin value was supplied; this does **not** assert domestic origin or waive import labeling requirements.

The overall result prioritizes MISMATCH, then REVIEW NEEDED, then PASS; NOT REQUIRED does not count as a pass or mismatch. Even high aggregate OCR confidence can miss a small but important warning area. The prototype therefore escalates uncertain warning presence or bold typography instead of claiming a definitive violation or an unsupported pass.

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
- Exact warning typography is not automatically established for ordinary uploads: OCR can miss a visibly present warning, and cannot prove the heading's boldness. The tool requests visual confirmation in those cases. The built-in synthetic demo is the exception for boldness because the application generates its heading in bold. The automated wording check ignores letter case and collapses whitespace, so it is not an exhaustive regulatory typography check.
- A blank country-of-origin field is treated as not provided; the prototype does not infer domestic origin from a blank field.
- Production use would require formal security, privacy, accessibility, records-retention, authorization, monitoring, and deployment review.
- The single-label workflow does not upload the source label image. It does send extracted text, confidence, and application entries to the server for comparison; the application does not intentionally persist those submissions.

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
