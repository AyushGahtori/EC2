FROM python:3.12-slim

LABEL maintainer="Cyber AI SOC"
LABEL description="AI-powered SOC with Windows Event Log integration"

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app source
COPY app/ ./app/
COPY frontend/ ./frontend/

# Copy env example (actual .env mounted at runtime)
COPY .env.example .env.example

# Non-root user for security
RUN useradd -m -u 1000 socuser && chown -R socuser:socuser /app
USER socuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
