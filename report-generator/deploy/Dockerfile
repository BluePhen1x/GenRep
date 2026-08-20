FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Playwright dependencies
RUN apt-get update && apt-get install -y \
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxkbcommon0 \
    libgbm1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install GenRep dependencies
COPY OpenManus/requirements.txt ./OpenManus-requirements.txt
RUN pip install --no-cache-dir -r OpenManus-requirements.txt

# Install Playwright browsers
RUN playwright install chromium

# Copy code
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY OpenManus/ ./OpenManus/

# Create temp directory
RUN mkdir -p /app/temp_reports

# Set environment
ENV PYTHONUNBUFFERED=1
ENV TEMP_DIR=/app/temp_reports
ENV OPENMANUS_PATH=/app/OpenManus

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
