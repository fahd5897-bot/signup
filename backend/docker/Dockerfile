# syntax=docker/dockerfile:1
#
# Two stages so the runtime image carries no compilers. The build-time and
# run-time system package lists are deliberately different, and both are longer
# than they look because two of this application's dependencies are not pure
# Python: WeasyPrint draws through Pango/Cairo, and Unstructured shells out to
# Tesseract and Poppler for scanned Arabic PDFs.

FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt


FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
        # WeasyPrint renders through these; without them `import weasyprint`
        # fails at run time rather than at build time.
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        # Arabic PDF export. A missing font produces a file full of boxes that
        # looks like a successful export, so the font is not optional — and
        # app/exports/pdf.py refuses to render Arabic if it is absent.
        fonts-noto-core \
        fonts-dejavu-core \
        # Scanned Arabic tenders: OCR plus the Arabic language data, and
        # Poppler for page rasterisation.
        tesseract-ocr \
        tesseract-ocr-ara \
        tesseract-ocr-eng \
        poppler-utils \
        libmagic1 \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

# Unprivileged. The application never needs to write outside /tmp: uploads go
# to object storage, not to local disk.
RUN useradd --create-home --uid 10001 app
WORKDIR /srv/app
COPY --chown=app:app . .
USER app

EXPOSE 8000

# Compose and Kubernetes both read this; it hits the same endpoint the load
# balancer does, so "healthy" means the same thing in every environment.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
