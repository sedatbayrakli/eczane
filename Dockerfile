# =======================================================
# Nöbetçi Eczane TV Bilgi Ekranı & Yönetim Paneli
# Production Dockerfile - Kalıcı /data Dizini & Unprivileged Kullanıcı
# =======================================================

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=5000 \
    DATABASE_PATH=/data/app.db

WORKDIR /app

# Güvenlik: Unprivileged kullanıcı oluşturma
RUN addgroup --system --gid 1001 appgroup && \
    adduser --system --uid 1001 --ingroup appgroup --no-create-home appuser

# Sistem bağımlılıkları (curl ile healthcheck için)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Kalıcı veritabanı dizini oluşturma ve izinleri devretme
RUN mkdir -p /data && chown -R appuser:appgroup /data

# Bağımlılıkları yükleme
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Proje kaynak kodlarını kopyalama
COPY . .
RUN chown -R appuser:appgroup /app

USER appuser
EXPOSE 5000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -f http://localhost:5000/api/health || exit 1

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "--timeout", "60", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
