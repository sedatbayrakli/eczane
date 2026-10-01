/**
 * Nöbetçi Eczane TV Bilgi Ekranı - Lisanslı Kiosk İstemci Motoru
 * Heartbeat, Dinamik Veri Yenileme & Nöbetçi Uyarısı
 */

const KIOSK_AYARLAR = {
    POLLING_ARALIGI_MS: 15 * 60 * 1000, // 15 dakikada bir veri tazeleme ve heartbeat
    HATA_TEKRAR_DENE_MS: 60 * 1000,     // Ağ kesintisinde 60 saniyede bir tekrar deneme
    SAAT_ARALIGI_MS: 1000               // Saniyede bir saat güncelleme
};

// DOM Eleman Referansları
const elDigitalClock = document.getElementById('digital-clock');
const elCalendarDate = document.getElementById('calendar-date');
const elPharmacyGrid = document.getElementById('pharmacy-grid');
const elPharmacyCount = document.getElementById('pharmacy-count');
const elLastSyncTime = document.getElementById('last-sync-time');
const elSystemAlert = document.getElementById('system-alert');
const elAlertTitle = document.getElementById('alert-title');
const elAlertMessage = document.getElementById('alert-message');
const elOnDutyBanner = document.getElementById('on-duty-banner');
const elTickerText = document.getElementById('ticker-text');
const elBrandName = document.getElementById('pharmacy-brand-name');

/**
 * Canlı Dijital Saat ve Türkçe Tarih Güncelleyici
 */
function saatVeTarihiGuncelle() {
    const simdi = new Date();
    const saat = String(simdi.getHours()).padStart(2, '0');
    const dakika = String(simdi.getMinutes()).padStart(2, '0');
    const saniye = String(simdi.getSeconds()).padStart(2, '0');
    elDigitalClock.textContent = `${saat}:${dakika}:${saniye}`;

    const gunler = ['Pazar', 'Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi'];
    const aylar = [
        'Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
        'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık'
    ];

    elCalendarDate.textContent = `${simdi.getDate()} ${aylar[simdi.getMonth()]} ${simdi.getFullYear()}, ${gunler[simdi.getDay()]}`;
}

/**
 * Eczane kartı için TV optimize HTML şablonu üretir
 */
function eczaneKartiHtmlUret(eczane) {
    const semtHtml = eczane.semt 
        ? `<span class="badge-semt">${escapeHtml(eczane.semt)}</span>` 
        : '';

    const yolTarifiHtml = eczane.yol_tarifi 
        ? `
        <div class="card-landmark-box">
            <span class="card-landmark-icon">📍</span>
            <span>${escapeHtml(eczane.yol_tarifi)}</span>
        </div>` 
        : '';

    const qrKodUrl = eczane.qr_kod_url || `https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=${encodeURIComponent(eczane.harita_linki)}`;

    return `
    <article class="pharmacy-card" data-id="${eczane.id}">
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

            <div class="card-address-block">
                <p class="card-address-text">${escapeHtml(eczane.adres)}</p>
                ${yolTarifiHtml}
            </div>

            <div class="card-phone-row">
                <div class="phone-icon-box">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
                        <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"></path>
                    </svg>
                </div>
                <span class="phone-number-display">${escapeHtml(eczane.telefon || 'Belirtilmedi')}</span>
            </div>
        </div>

        <div class="card-right-qr">
            <div class="qr-image-wrapper">
                <img class="qr-image" 
                     src="${qrKodUrl}" 
                     alt="${escapeHtml(eczane.isim)} Harita Konumu QR Kodu"
                     loading="lazy" />
            </div>
            <div class="qr-caption">
                Kamerayla <span>Konuma Git</span>
            </div>
        </div>
    </article>
    `;
}

function escapeHtml(metin) {
    if (!metin) return '';
    const div = document.createElement('div');
    div.textContent = metin;
    return div.innerHTML;
}

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
 * Lisanslı Kiosk Verilerini ve Heartbeat Sinyalini Gönderir
 */
async function kioskVerileriniGetir() {
    const apiAdresi = `/api/kiosk-data?key=${encodeURIComponent(LISANS_KEY)}&_t=${Date.now()}`;

    try {
        const yanit = await fetch(apiAdresi);

        // Lisans süresi dolduysa veya yetkisizse sayfayı yenileyerek hata ekranına geçir
        if (yanit.status === 403 || yanit.status === 401) {
            window.location.reload();
            return;
        }

        if (!yanit.ok) throw new Error(`HTTP ${yanit.status}`);

        const veri = await yanit.json();

        if (veri && veri.success) {
            // Eczane özel bilgileri
            if (veri.pharmacy) {
                if (veri.pharmacy.name && elBrandName) elBrandName.textContent = veri.pharmacy.name;
                if (veri.pharmacy.ticker_text && elTickerText) elTickerText.textContent = veri.pharmacy.ticker_text;
            }

            // "Bu Gece Nöbetçiyiz" Vurgusu
            if (elOnDutyBanner) {
                if (veri.is_on_duty_today) {
                    elOnDutyBanner.classList.add('active');
                } else {
                    elOnDutyBanner.classList.remove('active');
                }
            }

            // Eczaneler listesi
            if (Array.isArray(veri.eczaneler) && veri.eczaneler.length > 0) {
                elPharmacyCount.textContent = veri.eczaneler.length;

                elPharmacyGrid.className = 'pharmacy-grid-container';
                if (veri.eczaneler.length === 1) elPharmacyGrid.classList.add('grid-count-1');
                else if (veri.eczaneler.length === 3) elPharmacyGrid.classList.add('grid-count-3');
                else if (veri.eczaneler.length >= 5) elPharmacyGrid.classList.add('grid-count-6');

                elPharmacyGrid.innerHTML = veri.eczaneler.map(eczaneKartiHtmlUret).join('');

                const simdiSaat = new Date().toLocaleTimeString('tr-TR');
                elLastSyncTime.textContent = veri.onbellek_zamani ? veri.onbellek_zamani.split(' ')[1] : simdiSaat;

                if (veri.kaynak === 'stale_cache') {
                    uyariDurumunuAyarla(true, 'Geçici Ağ Uyarısı', 'Dış kaynağa anlık erişilemedi, son başarılı önbellek listeleniyor.');
                } else if (veri.kaynak === 'fallback_offline') {
                    uyariDurumunuAyarla(true, 'Çevrimdışı Modu', 'Canlı veri bağlantısı kurulamadı. Ekran yedek verilerle yayında kalıyor.');
                } else {
                    uyariDurumunuAyarla(false);
                }
            }
        }
    } catch (hata) {
        console.error('[HATA] Kiosk verisi çekilemedi:', hata);
        uyariDurumunuAyarla(true, 'Bağlantı Kesintisi', 'Veriler tazelenirken sorun oluştu. 60 saniye içinde yeniden denenecek...');
        setTimeout(kioskVerileriniGetir, KIOSK_AYARLAR.HATA_TEKRAR_DENE_MS);
    }
}

/**
 * TV Kiosk Başlatıcı
 */
function kioskBaslat() {
    saatVeTarihiGuncelle();
    setInterval(saatVeTarihiGuncelle, KIOSK_AYARLAR.SAAT_ARALIGI_MS);

    kioskVerileriniGetir();
    setInterval(kioskVerileriniGetir, KIOSK_AYARLAR.POLLING_ARALIGI_MS);
}

document.addEventListener('DOMContentLoaded', kioskBaslat);
