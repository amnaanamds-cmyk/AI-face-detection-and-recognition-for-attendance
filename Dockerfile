FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends git libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt requirements-postgres.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-postgres.txt
COPY . .
RUN python scripts/download_models.py
EXPOSE 8000 8443
# default: HTTPS on 8443 with a self-signed certificate (camera access needs HTTPS)
CMD ["python", "run.py", "--lan", "--host", "0.0.0.0", "--port", "8443"]
