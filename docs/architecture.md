# Architecture and Decision Record

## Goal

LabelCheck AI is a standalone proof-of-concept for accelerating routine alcohol-label review while keeping a human reviewer in control.

## Final architecture

1. The reviewer supplies expected application values and a label image.
2. The browser validates and downsizes the image when needed.
3. Tesseract.js runs OCR locally in a Web Worker using same-origin WebAssembly and English language-model assets bundled into the deployed Docker image.
4. Only extracted text, OCR confidence, and application values are sent to the FastAPI backend for the single-label workflow.
5. Deterministic rules compare application data with extracted label evidence:
   - normalization for harmless case and punctuation differences;
   - controlled similarity thresholds for descriptive text;
   - numerical ABV comparison;
   - normalized volume comparison;
   - canonical Government Health Warning wording;
   - explicit all-caps heading validation.
6. Each field returns PASS, MISMATCH, REVIEW NEEDED, or NOT REQUIRED.
7. The overall result is conservative: any mismatch yields MISMATCH; otherwise uncertainty yields REVIEW NEEDED.
8. The UI shows evidence, rationale, OCR confidence, and click-to-result latency.

## Why browser-side OCR

The discovery notes identify two constraints that matter at the same time:

- government networks may block outbound traffic to cloud ML endpoints;
- reviewers will reject a tool that is operationally too slow.

The first deployment used server-side OCR. The prototype host had intentionally minimal compute, which made OCR the wrong workload for that server tier even though the application and health checks remained healthy.

The final design moves OCR to the reviewer device instead of hiding that infrastructure mismatch behind paid compute. This produces a better proof-of-concept architecture:

- no external model endpoint;
- no model API key;
- source label image remains in the browser during single-label review;
- OCR compute scales with reviewer devices rather than the small web server;
- backend behavior remains deterministic, fast, and easy to test;
- the same server can support more concurrent reviewers without performing image inference itself.

The OCR JavaScript, Web Worker, WebAssembly core, and English trained data are built into the Docker image from pinned npm packages and served from the application's own origin.

## Why deterministic verification

A general-purpose language model is not used as the final decision-maker. Exact and numerical requirements are more reproducible when expressed as explicit code.

This makes key behaviors unit-testable and prevents a generative model from silently inventing missing label information.

## Human-review boundary

The system escalates ambiguity rather than converting weak evidence into a false pass.

Examples include:

- close-but-not-exact descriptive text;
- missing or low-confidence OCR evidence;
- warning typography such as boldness that cannot be positively established from the current evidence.

The prototype is decision support. It does not issue a Certificate of Label Approval and does not replace TTB review.

## Performance model

The interface reports click-to-result latency, including browser OCR initialization when needed, image preparation, recognition, and backend verification.

The OCR worker is initialized once and reused for later labels in the same browser session. The first run can therefore be slower than warm subsequent runs because WebAssembly and language data must be initialized.

In manual live testing on October 13, 2026, the built-in demo completed in approximately 2.48 seconds from click to rendered result in a fresh private-browser session. Subsequent warm runs were approximately 0.55–0.65 seconds.

The backend itself performs only lightweight deterministic comparison.

## Security and privacy

- Single-label image bytes remain in the reviewer browser.
- No external AI API is called.
- OCR model assets are served from the same deployed application.
- No API key or model credential is required.
- The application intentionally does not persist uploaded label images.
- Production deployment would still require formal authorization, accessibility, logging, retention, privacy, monitoring, and security review.

## Batch-processing stretch feature

The batch UI accepts a CSV manifest plus up to 20 matching images.

One browser OCR worker is reused and labels are processed sequentially. Only extracted text and application values are sent to the verification endpoint.

For the 200–300-label importer batches described by the stakeholder, a production architecture should use a purpose-built client or managed job workflow with durable result metadata, observability, and resumability.

## Deployment reliability

GitHub Actions performs three layers of validation:

1. Python unit tests for deterministic rules.
2. An exact Docker-image build, including pinned Tesseract.js assets.
3. A running-container smoke test for the health endpoint, locally hosted OCR assets, and demo-image endpoint.

This validates the same packaging path used by Render rather than testing only the Python source.

## Scope trade-offs

The primary demonstration is distilled spirits. It does not attempt to encode every beer, wine, or distilled-spirits regulation.

The following remain explicit prototype limitations:

- exhaustive regulatory coverage;
- exact typography measurements such as minimum point size;
- complete same-field-of-vision geometry;
- robust bottle-curvature and severe-glare correction;
- production COLA integration.

Those trade-offs preserve the take-home requirement to prefer a working, clean core over ambitious but incomplete features.
