# 🏥 Nöbetçi Eczane TV Bilgi Ekranı (Kiosk Dashboard)

TV ve Mi Box ekranlarında 7/24 kesintisiz çalışmak üzere tasarlanmış, ilçe bazlı nöbetçi eczaneleri listeleyen, modern, karanlık temalı ve otomatik güncellenen bir web dashboard uygulamasıdır.

- **Canlı Domain:** [https://eczane.cakirlar.net](https://eczane.cakirlar.net)
- **Coolify Proje Adı:** `eczane`
- **Varsayılan İlçe:** Bahçelievler / İstanbul

---

## ✨ Özellikler

- **TV / Kiosk Optimizasyonu:** 1080p ve 4K TV ekranlarında uzaktan rahatça okunabilecek büyük puntolar, yüksek kontrast (derin siyah, eczane kırmızısı ve aktif yeşili). Dikey kaydırma çubuğu gizlenmiştir.
- **Dinamik QR Kodlar:** Her eczanenin kartında Google Haritalar navigasyonuna yönlendiren dinamik QR kod bulunur. Müşteriler veya hastalar telefon kamerasıyla tarayarak anında yol tarifi alabilir.
- **Canlı Dijital Saat & Tarih:** TV üst çubuğunda saniyesi saniyesine güncellenen şık dijital saat ve Türkçe tarih.
- **Akıllı Önbellek (Caching):** Hedef kaynakları yormamak ve kotayı korumak için 45 dakikalık in-memory önbellekleme mekanizması.
- **Sessiz Arka Plan Güncellemesi (Polling):** Sayfayı yeniden yüklemeden (F5 yapmadan) her 15 dakikada bir arkaplanda `fetch` ile verileri tazeler.
- **Ağ Kesintisi & Hata Koruması:** İnternet kopsa veya kaynak site yanıt vermese bile ekran kararmaz; son geçerli önbellek veya güvenli yedek veri yayında kalır ve üstte durum uyarısı gösterilir.
- **Parametrik İlçe Desteği:** URL üzerinden istenen il/ilçe görüntülenebilir (Örn: `https://eczane.cakirlar.net/?ilce=kadikoy` veya `/?il=ankara&ilce=cankaya`).

---

## 🚀 Coolify Dağıtımı (Deployment)

Coolify kontrol panelinde (`https://coolify.cakirlar.net`):

1. **New Resource > Public / Private Repository:**
   - Repository: `sedatbayrakli/eczane`
   - Branch: `main`
2. **Genel Ayarlar:**
   - **Proje Adı:** `eczane`
   - **Domains (FQDN):** `https://eczane.cakirlar.net`
   - **Port:** `5000`
3. **Deploy:**
   - **Deploy** butonuna tıklayın. Let's Encrypt SSL sertifikası ve Traefik yönlendirmesi otomatik olarak tanımlanır.

---

## 📺 TV / Mi Box Üzerinde 7/24 Tam Ekran Çalıştırma

1. **Android TV / Mi Box Kurulumu:**
   - Google Play Store'dan **Fully Kiosk Browser** veya **TV Bro** tarayıcısını yükleyin.
   - Tarayıcı başlangıç URL'si olarak dashboard adresinizi girin:
     ```
     https://eczane.cakirlar.net/?ilce=bahcelievler
     ```
2. **Kiosk Modu:**
   - Tarayıcı ayarlarından *"Tam Ekran (Hide Status/Nav Bar)"* ve *"Cihaz Açıldığında Otomatik Başlat"* seçeneklerini aktif edin.
   - Ekran kararma veya uyku modunu televizyon ayarlarından *"Hiçbir Zaman"* olarak ayarlayın.

---

## 🔌 API Uç Noktaları (Endpoints)

- `GET /`: TV Kiosk Dashboard arayüzü (`https://eczane.cakirlar.net/?ilce=bahcelievler`)
- `GET /api/nobetci-eczaneler`: Güncel nöbetçi eczanelerin JSON listesi (`https://eczane.cakirlar.net/api/nobetci-eczaneler?il=istanbul&ilce=bahcelievler`)
- `GET /api/health`: Konteyner sağlık kontrolü (Healthcheck)
