# Use a slim Python image for smaller containers
FROM python:3.12-slim

# Don't write .pyc files and flush logs immediately
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# Workdir
WORKDIR /app

# Install system deps if needed later; for now minimal
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency file and install
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY backend /app/backend
COPY frontend /app/frontend

# Expose port (for local clarity; Cloud Run auto-handles it)
EXPOSE 8080

# Start the app with Uvicorn
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8080"]
