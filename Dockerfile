# ── Base image ────────────────────────────────────────────────────────────────
# Python 3.11 slim — smaller than full, still compatible with all dependencies
FROM python:3.11-slim

# ── System dependencies for OpenCV & image processing ─────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgl1-mesa-glx \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ─────────────────────────────────────────────────────────
WORKDIR /app

# ── Install Python dependencies ───────────────────────────────────────────────
# Copy requirements first so Docker can cache this layer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Copy application code ─────────────────────────────────────────────────────
COPY . .

# ── Create upload directories ─────────────────────────────────────────────────
RUN mkdir -p static/uploads static/vis

# ── Hugging Face Spaces runs on port 7860 ─────────────────────────────────────
# $PORT defaults to 7860 on HF Spaces; other platforms (Render, Railway) set it
ENV PORT=7860

EXPOSE 7860

# ── Start server ──────────────────────────────────────────────────────────────
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT}
