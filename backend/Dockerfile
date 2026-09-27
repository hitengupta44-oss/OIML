# NAWI reporter — Hugging Face Docker Space
#
# Docker rather than the Gradio SDK because the frontend wants a plain
# REST API, not Gradio's two-step queue protocol, and because LibreOffice
# needs a root apt install.

FROM python:3.11-slim

# LibreOffice Writer is the PDF converter. It is a large install, so it
# goes in its own layer and gets cached across rebuilds.
#   fonts-dejavu / liberation  keep the report's typography stable; without
#                              them LibreOffice substitutes and the tables
#                              shift.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer \
        libreoffice-core \
        fonts-dejavu \
        fonts-liberation \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces run as uid 1000, not root. Anything written at
# runtime has to be somewhere that user owns.
RUN useradd -m -u 1000 user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    REPORT_OUT_DIR=/tmp/reports

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user . /app
RUN mkdir -p /tmp/reports && chown -R user /tmp/reports /app

USER user

# Spaces expect 7860.
EXPOSE 7860

HEALTHCHECK --interval=60s --timeout=10s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:7860/health',timeout=5).status==200 else 1)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]
