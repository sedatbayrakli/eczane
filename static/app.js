/**
 * Nöbetçi Eczane TV Bilgi Ekranı (Kiosk Dashboard)
 * İstemci Tarafı JavaScript Motoru
 */

// Yapılandırma ve Zamanlayıcı Değişkenleri
const AYARLAR = {
    // 15 dakikada bir arka planda sessiz veri güncelleme (15 * 60 * 1000 ms)
    POLLING_ARALIGI_MS: 15 * 60 * 1000,
    // Hata durumunda yeniden deneme aralığı (60 saniye)
    HATA_TEKRAR_DENE_MS: 60 * 1000,
    // Saniyede bir saat güncelleme
    SAAT_ARALIGI_MS: 1000
};

// URL parametrelerinden il ve ilçe bilgilerini çözümleme
const urlParams = new URLSearchParams(window.location.search);
const AKTIF_IL = urlParams.get('il') || 'istanbul';
const AKTIF_ILCE = urlParams.get('ilce') || 'bahcelievler';

// DOM Eleman Referansları
const elDigitalClock = document.getElementById('digital-clock');
const elCalendarDate = document.getElementById('calendar-date');
const elPharmacyGrid = document.getElementById('pharmacy-grid');
const elPharmacyCount = document.getElementById('pharmacy-count');
const elLastSyncTime = document.getElementById('last-sync-time');
const elHeaderDistrict = document.getElementById('header-district');
const elHeaderCity = document.getElementById('header-city');
const elSystemAlert = document.getElementById('system-alert');
const elAlertTitle = document.getElementById('alert-title');
const elAlertMessage = document.getElementById('alert-message');

/**
 * Türkçe Gün ve Ay İsimleriyle Dijital Saati Günceller
 */
function saatVeTarihiGuncelle() {
    const simdi = new Date();

    // Dijital Saat (SS:DD:SN)
    const saat = String(simdi.getHours()).padStart(2, '0');
    const dakika = String(simdi.getMinutes()).padStart(2, '0');
    const saniye = String(simdi.getSeconds()).padStart(2, '0');
    elDigitalClock.textContent = `${saat}:${dakika}:${saniye}`;

    // Türkçe Uzun Tarih Biçimi
    const gunler = ['Pazar', 'Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi'];
    const aylar = [
        'Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
        'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık'
    ];

    const gunAdi = gunler[simdi.getDay()];
    const ayAdi = aylar[simdi.getMonth()];
    const gunSayi = simdi.getDate();
    const yil = simdi.getFullYear();

    elCalendarDate.textContent = `${gunSayi} ${ayAdi} ${yil}, ${gunAdi}`;
}

/**
 * Eczane kartı için HTML şablonu üretir
 */
function eczaneKartiHtmlUret(eczane) {
    // Semt etiketi kontrolü
    const semtHtml = eczane.semt 
        ? `<span class="badge-semt">${escapeHtml(eczane.semt)}</span>` 
        : '';

    // Yol tarifi / Yakın yer bilgisi kontrolü
    const yolTarifiHtml = eczane.yol_tarifi 
        ? `
        <div class="card-landmark-box">
            <span class="card-landmark-icon">📍</span>
            <span>${escapeHtml(eczane.yol_tarifi)}</span>
        </div>
        ` 
        : '';

    // Güvenli QR Kod görseli
    const qrKodUrl = eczane.qr_kod_url || `https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=${encodeURIComponent(eczane.harita_linki)}`;

    return `
    <article class="pharmacy-card" data-id="${eczane.id}">
        <!-- Sol Bilgi Bölümü -->
        <div class="card-left-info">
            <div class="card-top-row">
                <h2 class="pharmacy-name-title">${escapeHtml(eczane.isim)}</h2>
                <div class="card-badges">
                    <span class="badge-duty">
                        <span class="pulse-indicator" style="width: 7px; height: 7px;"></span>
                        ${escapeHtml(eczane.nobet_durumu || 'Sabaha kadar açık')}
                    </span>
                    ${semtHtml}
                </div>
            </div>

            <!-- Adres ve Yol Tarifi -->
            <div class="card-address-block">
                <p class="card-address-text">${escapeHtml(eczane.adres)}</p>
                ${yolTarifiHtml}
            </div>

            <!-- Telefon Bilgisi -->
            <div class="card-phone-row">
                <div class="phone-icon-box">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
                        <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"></path>
                    </svg>
                </div>
                <span class="phone-number-display">${escapeHtml(eczane.telefon || 'Belirtilmedi')}</span>
            </div>
        </div>

        <!-- Sağ Bölüm: Dinamik QR Kod Kartı -->
        <div class="card-right-qr">
            <div class="qr-image-wrapper">
                <img class="qr-image" 
                     src="${qrKodUrl}" 
                     alt="${escapeHtml(eczane.isim)} Harita Konumu QR Kodu"
                     loading="lazy"
                     onerror="this.onerror=null; this.src='https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=https%3A%2F%2Fmaps.google.com';" />
            </div>
            <div class="qr-caption">
                Kamerayla <span>Konuma Git</span>
            </div>
        </div>
    </article>
    `;
}

/**
 * XSS saldırılarına karşı güvenli metin temizleme
 */
function escapeHtml(metin) {
    if (!metin) return '';
    const div = document.createElement('div');
    div.textContent = metin;
    return div.innerHTML;
}

/**
 * Sistem uyarı kutusunu gösterir veya gizler
 */
function uyariDurumunuAyarla(gosterilsinMi, baslik = '', mesaj = '') {
    if (gosterilsinMi) {
        elAlertTitle.textContent = baslik;
        elAlertMessage.textContent = mesaj;
        elSystemAlert.classList.remove('hidden');
    } else {
        elSystemAlert.classList.add('hidden');
    }
}

/**
 * Backend API'sinden nöbetçi eczane verilerini çeker ve arayüzü günceller
 */
async function eczaneVerileriniGetir() {
    const apiAdresi = `/api/nobetci-eczaneler?il=${encodeURIComponent(AKTIF_IL)}&ilce=${encodeURIComponent(AKTIF_ILCE)}&_t=${Date.now()}`;

    try {
        const yanit = await fetch(apiAdresi, {
            headers: {
                'Accept': 'application/json'
            }
        });

        if (!yanit.ok) {
            throw new Error(`Sunucu hatası: HTTP ${yanit.status}`);
        }

        const veri = await yanit.json();

        if (veri && veri.success && Array.isArray(veri.eczaneler) && veri.eczaneler.length > 0) {
            // Başlık alanlarını güncelleme
            if (veri.ilce) elHeaderDistrict.textContent = veri.ilce;
            if (veri.il) elHeaderCity.textContent = veri.il;
            elPharmacyCount.textContent = veri.eczaneler.length;

            // Kart sayısı sınıfı ayarlama (grid uyumu için)
            elPharmacyGrid.className = 'pharmacy-grid-container';
            if (veri.eczaneler.length === 1) {
                elPharmacyGrid.classList.add('grid-count-1');
            } else if (veri.eczaneler.length === 3) {
                elPharmacyGrid.classList.add('grid-count-3');
            } else if (veri.eczaneler.length >= 5) {
                elPharmacyGrid.classList.add('grid-count-6');
            }

            // Kartları DOM'a aktarma
            const kartlarHtml = veri.eczaneler.map(eczaneKartiHtmlUret).join('');
            elPharmacyGrid.innerHTML = kartlarHtml;

            // Son senkronizasyon zamanı
            const simdiSaat = new Date().toLocaleTimeString('tr-TR');
            elLastSyncTime.textContent = veri.onbellek_zamani ? veri.onbellek_zamani.split(' ')[1] : simdiSaat;

            // Kaynak durumuna göre uyarı bandı yönetimi
            if (veri.kaynak === 'stale_cache') {
                uyariDurumunuAyarla(
                    true,
                    'Geçici Ağ Uyarısı',
                    'Dış kaynağa anlık erişilemedi, son başarılı önbellek listeleniyor.'
                );
            } else if (veri.kaynak === 'fallback_offline') {
                uyariDurumunuAyarla(
                    true,
                    'Çevrimdışı Modu',
                    'Canlı veri bağlantısı kurulamadı. Ekran yedek verilerle yayında kalıyor.'
                );
            } else {
                // Başarılı ve taze canlı veri
                uyariDurumunuAyarla(false);
            }

        } else {
            throw new Error('Geçerli nöbetçi eczane verisi bulunamadı.');
        }

    } catch (hata) {
        console.error('[HATA] Eczane verisi çekilemedi:', hata);

        // Ekrandaki mevcut kartları silmiyoruz (kesintisiz TV yayını için)
        // Sadece üstte şık bir uyarı bildirimi açıyoruz
        uyariDurumunuAyarla(
            true,
            'Bağlantı Kesintisi',
            'Veriler tazelenirken bir sorun oluştu. 60 saniye içinde yeniden denenecek...'
        );

        // Hata durumunda 15 dakika beklemek yerine 60 saniye sonra tekrar dene
        setTimeout(eczaneVerileriniGetir, AYARLAR.HATA_TEKRAR_DENE_MS);
    }
}

/**
 * TV Kiosk Başlatıcı
 */
function kioskBaslat() {
    // 1. Canlı Saati Başlat
    saatVeTarihiGuncelle();
    setInterval(saatVeTarihiGuncelle, AYARLAR.SAAT_ARALIGI_MS);

    // 2. İlk Veri Çekimi
    eczaneVerileriniGetir();

    // 3. 15 Dakikalık Otomatik Polling Döngüsü
    setInterval(eczaneVerileriniGetir, AYARLAR.POLLING_ARALIGI_MS);
}

// Sayfa yüklendiğinde başlat
document.addEventListener('DOMContentLoaded', kioskBaslat);
