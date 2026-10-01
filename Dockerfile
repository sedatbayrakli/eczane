# =======================================================
# Nöbetçi Eczane TV Bilgi Ekranı - Production Dockerfile
# Güvenli, Hafif ve Coolify Uyumlu
# =======================================================

FROM python:3.11-slim

# Python ortam değişkenleri: Bytecode üretilmesini engelle ve logları anlık aktar
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=5000

# Çalışma dizini oluşturma
WORKDIR /app

# Güvenlik: Uygulamanın root yetkisi olmadan çalışması için unprivileged kullanıcı oluşturma
RUN addgroup --system --gid 1001 appgroup && \
    adduser --system --uid 1001 --ingroup appgroup --no-create-home appuser

# Sistem bağımlılıkları (curl ile healthcheck için)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Bağımlılık dosyasını kopyalama ve yükleme (Docker layer cache optimizasyonu)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Proje kaynak kodlarını kopyalama
COPY . .

# Dosya izinlerini unprivileged kullanıcıya devretme
RUN chown -R appuser:appgroup /app

# Güvenli kullanıcıya geçiş
USER appuser

# TV Kiosk HTTP Portunu Dışa Açma
EXPOSE 5000

# Docker / Coolify Konteyner Sağlık Kontrolü
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5000/api/health || exit 1

# Prodüksiyon çalıştırma komutu (Gunicorn WSGI Sunucusu)
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "--timeout", "60", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
