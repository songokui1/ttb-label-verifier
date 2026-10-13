FROM node:20-bookworm-slim AS ocr-assets

WORKDIR /ocr-build
RUN npm init -y >/dev/null 2>&1 \
    && npm install --omit=dev tesseract.js@7.0.0 tesseract.js-core@6.1.2 @tesseract.js-data/eng@1.0.0 \
    && mkdir -p /bundle/core /bundle/lang \
    && cp node_modules/tesseract.js/dist/tesseract.min.js /bundle/tesseract.min.js \
    && cp node_modules/tesseract.js/dist/worker.min.js /bundle/worker.min.js \
    && find node_modules/tesseract.js-core -maxdepth 1 -type f \( -name 'tesseract-core*.js' -o -name 'tesseract-core*.wasm' \) -exec cp {} /bundle/core/ \; \
    && LANGFILE="$(find node_modules/@tesseract.js-data/eng -type f -name 'eng.traineddata.gz' | head -n 1)" \
    && test -n "$LANGFILE" \
    && cp "$LANGFILE" /bundle/lang/eng.traineddata.gz \
    && test -f /bundle/tesseract.min.js \
    && test -f /bundle/worker.min.js \
    && test -f /bundle/lang/eng.traineddata.gz

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=ocr-assets /bundle /app/static/ocr

EXPOSE 10000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
