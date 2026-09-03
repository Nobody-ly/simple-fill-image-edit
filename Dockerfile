FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SIMPLE_FILL_HOST=0.0.0.0 \
    SIMPLE_FILL_DATA=/app/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

VOLUME ["/app/data"]
EXPOSE 7862
CMD ["python", "run.py"]
