# 🏥 Nöbetçi Eczane TV Kiosk Sistemi & Multi-Tenant SaaS Yönetim Paneli

TV ve Mi Box ekranlarında 7/24 kesintisiz çalışan, merkezi bir Yönetim Paneli (Admin UI), lisanslama motoru, ekran canlılık takibi (Heartbeat) ve ilçe bazlı nöbetçi eczane listeleme özelliklerine sahip ticari Kiosk çözümüdür.

- **Canlı Domain:** [https://eczane.cakirlar.net](https://eczane.cakirlar.net)
- **Yönetim Paneli:** [https://eczane.cakirlar.net/admin](https://eczane.cakirlar.net/admin)
- **Lisanslı Kiosk Ekranı:** `https://eczane.cakirlar.net/kiosk?key=LISANS_ANAHTARI`

---

## ✨ Yeni SaaS ve Lisanslama Özellikleri

1. **Merkezi Yönetim Paneli (`/admin`):**
   - **Giriş:** Session tabanlı güvenli kimlik doğrulama.
   - **Eczane CRUD:** Eczane ekleme, düzenleme, silme, il ve ilçe belirleme (farklı il/ilçelere satılabilir mimari).
   - **Lisans Yönetimi:** Tek tıkla lisansı duraklatma/aktif etme (`is_active`), +30 gün ve +365 gün süre uzatma.
   - **Kiosk URL:** Her eczane için tek tıkla kopyalanabilir TV bağlantı linki.
   - **Canlılık Takibi (Heartbeat):** TV ekranının son 5 dakika içinde sinyal gönderip göndermediğini gösteren canlı LED rozeti (Yeşil: Açık / Kırmızı: Kapalı).

2. **Lisanslı Kiosk Ekranı (`/kiosk?key=...`):**
   - **Lisans Doğrulama:** TV açıldığında lisansın geçerliliği, aktifliği ve bitiş tarihi anlık sorgulanır.
   - **"Bu Gece Nöbetçiyiz" Vurgusu:** Eğer o gün nöbetçi olan eczane bu eczanenin kendisi ise, ekranın en üstünde dikkat çekici parıltılı bir nöbet rozeti gösterilir.
   - **Özel Kayan Yazı:** Eczaneye özel kampanya veya bilgilendirme duyurusu ekranın altında akar.
   - **Süresi Dolan Lisans Koruması:** Lisans süresi dolduğunda veya askıya alındığında ekran kapatılarak lisans yenileme bilgilendirme sayfası gösterilir (`kiosk_error.html`).

3. **Veritabanı Kalıcılığı (SQLite & Volume):**
   - SQLite veritabanı `/data/app.db` yolunda tutulur ve Coolify volume ile konteyner yeniden başlasa dahi veriler asla kaybolmaz.

---

## ⚙️ Ortam Değişkenleri (Environment Variables)

Coolify kontrol panelinde tanımlanabilecek ortam değişkenleri:

| Değişken Adı | Varsayılan | Açıklama |
| :--- | :--- | :--- |
| `ADMIN_USER` | `admin` | Yönetim paneli giriş kullanıcı adı |
| `ADMIN_PASSWORD` | `admin123` | Yönetim paneli giriş şifresi |
| `SECRET_KEY` | *(Rastgele metin)* | Flask session oturum imzalama anahtarı |
| `DATABASE_PATH` | `/data/app.db` | Kalıcı SQLite veritabanı dosya yolu |
| `DEFAULT_DISTRICT` | `bahcelievler` | Genel ekran için varsayılan ilçe |
| `CACHE_TTL` | `2700` | Nöbetçi eczane veri önbellek süresi (saniye - 45 dk) |
| `PORT` | `5000` | Konteyner iç çalışma portu |

---

## 💾 Kalıcı Depolama (Persistent Storage) Yapılandırması

Veritabanının konteyner güncellemelerinde silinmemesi için:

1. Coolify panelinizde uygulamanıza gidin: **Storages / Persistent Storage** sekmesini açın.
2. **Add Storage** butonuna tıklayın:
   - **Volume Name:** `pharmacy_data`
   - **Destination Path:** `/data`
3. Değişiklikleri kaydedip yeniden deploy edin.

---

## 🚀 Coolify Staging ve Canlıya Geçiş (Deployment) Rehberi

Bu geliştirme `feature/admin-and-licensing` branch'i üzerinde hazırlanmıştır. Canlı sistemi riske atmadan test etmek için 2 aşamalı strateji önerilir:

### 1. Aşama: Staging Ortamında Test Etme (Önerilen)
1. Coolify panelinde **Eczane** projesi içine **New Resource > Public / Private Repository** deyin.
2. **Repository:** `sedatbayrakli/eczane`
3. **Branch:** `feature/admin-and-licensing`
4. **Name:** `eczane-staging`
5. **Domains:** `https://dev-eczane.cakirlar.net`
6. **Port:** `5000`
7. **Deploy** edin ve `https://dev-eczane.cakirlar.net/admin` adresine giderek kullanıcı adı (`admin`) ve şifre (`admin123`) ile giriş yapıp test edin.

### 2. Aşama: Canlıya Alma (Production Merge)
Testleri tamamladıktan sonra geliştirmeyi canlı ortama aktarmak için:

```bash
# Yerel terminalde main branch'ine geç ve merge et
git checkout main
git merge feature/admin-and-licensing
git push origin main
```

Ardından Coolify panelinde mevcut `https://eczane.cakirlar.net` uygulamasında **Deploy** butonuna tıklamanız yeterlidir.
