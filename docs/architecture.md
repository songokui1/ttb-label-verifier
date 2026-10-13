# Architecture and Decision Record

## Goal

LabelCheck AI is a standalone proof-of-concept for accelerating routine alcohol-label review while keeping a human reviewer in control.

## Data flow

1. A reviewer supplies expected application values and a label image.
2. The server validates the image and downsizes very large inputs.
3. A local Tesseract LSTM OCR engine extracts visible text and token-level confidence data.
4. Field-specific logic searches the OCR evidence for candidate values.
5. Deterministic rules compare application data with label evidence:
   - text normalization for typography/case/punctuation;
   - numerical ABV comparison;
   - normalized volume comparison;
   - canonical government-warning wording;
   - conservative heading-format assessment.
6. Each field returns PASS, MISMATCH, REVIEW NEEDED, or NOT REQUIRED.
7. The overall result is conservative: any mismatch yields MISMATCH; otherwise uncertainty yields REVIEW NEEDED.
8. The UI shows the evidence, rationale, OCR text, and measured processing time.

## Why local OCR

The stakeholder notes identify blocked outbound ML endpoints as a real deployment constraint. A local model keeps the core demo operational without an API key or external inference service and reduces the chance that a government network blocks a critical request.

Tesseract 5 uses an LSTM neural OCR engine. It is used only for perception. Regulatory comparison remains explicit and testable.

## Why deterministic verification

A general-purpose language model is not used as the final decision-maker. Exact and numeric checks are better expressed as code, which makes behavior reproducible and unit-testable. This also prevents a generative model from silently inventing missing label content.

## Human review boundary

The system escalates ambiguity rather than converting weak OCR evidence into a false pass. Examples include low-quality images, uncertain warning typography, and close-but-not-exact descriptive text.

The prototype is decision support. It does not issue a Certificate of Label Approval and does not replace TTB review.

## Performance

The application records server-side processing time for each label. The built-in demo is also exercised in CI with a 5-second assertion to reflect the stakeholder's usability target.

A hosting-platform cold start is not included in label-processing time. For a live evaluation, a non-sleeping service tier avoids conflating infrastructure wake-up latency with application inference latency.

## Security and privacy

- Uploaded images are validated and processed in memory.
- The application intentionally does not persist uploaded label images.
- No cloud-model API key is required.
- File size and image dimensions are bounded.
- Production deployment would require formal authorization, logging, retention, accessibility, privacy, monitoring, and security review.

## Batch-processing stretch feature

The UI exposes a bounded batch endpoint using a CSV manifest and up to 20 images. The prototype processes jobs with a small bounded worker pool.

For the 200–300-label importer batches described by the stakeholder, a production design should use asynchronous queued jobs, rate controls, durable result metadata, and observability instead of holding one HTTP request open.

## Scope trade-offs

The primary demonstration is distilled spirits. It does not attempt to encode every beer, wine, or distilled-spirits regulation. Visual checks such as exact type size, field-of-vision geometry, curved-bottle distortion, and all possible mandatory disclosures remain outside this prototype's deterministic scope unless sufficient evidence is available.
