# LabelCheck AI

AI-assisted alcohol label verification prototype built for the U.S. Department of the Treasury take-home exercise.

LabelCheck AI helps a human compliance reviewer compare alcohol-label artwork with application data. It extracts visible label text, applies field-specific verification rules, and returns three clear outcomes: Match, Mismatch, or Review needed.

> This is a proof-of-concept decision-support tool. It does not issue a TTB approval or replace a regulatory determination.

## Why this design

The stakeholder notes emphasize three practical constraints:

- much of the workload is repetitive data matching;
- results need to return in about five seconds to be useful;
- outbound ML endpoints may be blocked on government networks.

The prototype therefore uses a local-first architecture:

label image + application data
→ local ML OCR extraction
→ structured evidence
→ deterministic field-specific verification
→ PASS / MISMATCH / REVIEW NEEDED
→ human reviewer

This separates perception from decision logic. Machine learning is used where it is useful for reading label text, while numeric equivalence, text normalization, and government-warning checks are implemented as explicit, testable rules.

## Core features

- JPG, PNG, and WebP label upload
- Local Tesseract LSTM OCR with no cloud API dependency
- Brand-name normalization that tolerates harmless case and punctuation differences
- ABV parsing, including proof-to-ABV conversion
- Net-contents normalization such as 0.75 L = 750 mL
- Producer/bottler and country-of-origin comparison
- Government health-warning wording check
- Conservative visual check of the GOVERNMENT WARNING heading
- Field-level evidence and explanations
- PASS / MISMATCH / REVIEW NEEDED outcomes
- Measured processing latency shown in the interface
- In-memory image processing with no intentional document persistence
- Built-in synthetic demo label for immediate reviewer testing
- Batch-review stretch feature with CSV + multiple label images
- Automated tests and GitHub Actions CI
- Docker and Render deployment configuration

## Reviewer quick test

After deployment:

1. Open the application URL.
2. Click Run built-in demo.
3. The synthetic label should produce a PASS result in roughly one second on a warm local environment.
4. Upload another label and change one or more expected application fields to observe mismatch and review behavior.

The built-in demo is generated at runtime, so no external image asset or network call is required.

## Run with Docker

    git clone https://github.com/songokui1/ttb-label-verifier.git
    cd ttb-label-verifier
    docker build -t labelcheck-ai .
    docker run --rm -p 10000:10000 labelcheck-ai

Then open http://localhost:10000.

## Local development

Requirements:

- Python 3.12+
- Tesseract OCR installed and available as tesseract

Commands:

    python -m venv .venv
    source .venv/bin/activate

On Windows use:

    .venv\Scripts\activate

Then:

    pip install -r requirements-dev.txt
    uvicorn app.main:app --reload
    python -m pytest

## Batch review

The stretch feature accepts a CSV manifest plus matching image files. Required columns are:

    filename,brand_name,class_type,alcohol_content,net_contents,producer_name_address,country_of_origin

The public prototype caps one request at 20 images. A production implementation for 200–300-label importer batches should use queued jobs and bounded workers rather than keeping a web request open.

A sample CSV is included in sample_data/batch_manifest.csv.

## Technical choices

### Local-first OCR

The stakeholder notes specifically mention government-network restrictions on outbound ML endpoints. The prototype therefore works without any cloud model or API key. This also improves privacy and demo reliability.

### Deterministic verification

The OCR engine extracts visible evidence, but it does not decide whether a label is legally approved.

- Brand and descriptive text use normalization and controlled similarity thresholds.
- ABV and volume are parsed numerically.
- The government-warning wording is checked against the canonical statement.
- Uncertain OCR or visual formatting is sent to Review needed rather than being silently accepted.

### Human-in-the-loop behavior

Some label-review questions are inherently visual or contextual. The prototype deliberately exposes uncertainty instead of producing false confidence.

## TTB guidance used

The prototype focuses on common label information described by TTB for distilled spirits, including brand name, class/type, alcohol content, net contents, name/address, country of origin for imports, and the Government Health Warning Statement.

Official references:

- TTB Distilled Spirits Labeling: https://www.ttb.gov/regulated-commodities/beverage-alcohol/distilled-spirits/labeling
- TTB Health Warning Statement: https://www.ttb.gov/regulated-commodities/beverage-alcohol/distilled-spirits/ds-labeling-home/ds-health-warning
- TTB Net Contents: https://www.ttb.gov/regulated-commodities/beverage-alcohol/distilled-spirits/ds-labeling-home/ds-net-contents
- TTB Brand Name: https://www.ttb.gov/regulated-commodities/beverage-alcohol/distilled-spirits/ds-labeling-home/ds-brand-name

## Assumptions and limitations

- This is not an exhaustive implementation of all beer, wine, and distilled-spirits regulations.
- The primary demonstration is a distilled-spirits workflow.
- Direct COLA integration is intentionally out of scope, matching the stakeholder notes.
- OCR accuracy varies with glare, curvature, image quality, resolution, and typography.
- The local visual boldness check is conservative. Ambiguous typography is escalated for human review.
- Production use would require formal security, privacy, accessibility, monitoring, records-retention, authorization, and deployment review.
- No uploaded label image is intentionally written to persistent storage by the application code.

## Evaluation alignment

The implementation is intentionally optimized for the criteria in the take-home:

- Correctness and completeness: core comparison workflow plus warning checks
- Code quality and organization: typed application model, isolated verification functions, tests
- Technical choices: local-first OCR and deterministic rule layer
- UX and error handling: simple form, explicit results, validation, review state
- Attention to requirements: measured latency and standalone deployment
- Creative problem-solving: explainable human-review path and batch-processing stretch feature


## Deployment

The repository includes a Render Blueprint and Dockerfile. The service uses the local Tesseract OCR engine and does not require an API key.

Render configuration:

- Runtime: Docker
- Health check: `/health`
- Region: Virginia
- Automatic deploy: only after linked CI checks pass
- Default prototype plan: Free

For an evaluation window where instant first-load response matters, a non-sleeping Render compute plan is preferable because Render Free services can spin down after periods of inactivity. This hosting cold start is separate from the application's measured label-processing time.
