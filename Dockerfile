FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_TEMP_ROOT=/tmp/ifctoolkit

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN groupadd --system --gid 10001 ifctoolkit \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app --shell /usr/sbin/nologin ifctoolkit \
    && mkdir -p /tmp/ifctoolkit /app/static/_hashed \
    && chown -R ifctoolkit:ifctoolkit /tmp/ifctoolkit /app/static/_hashed

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
