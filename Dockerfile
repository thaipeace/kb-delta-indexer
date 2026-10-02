FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Ensure Python doesn't buffer stdout/stderr and doesn't write .pyc files
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install project dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code and scripts
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY main.py .

# Execute the daily sync batch job and exit
ENTRYPOINT ["python", "main.py"]
