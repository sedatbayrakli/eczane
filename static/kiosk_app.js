/**
 * Nöbetçi Eczane TV Bilgi Ekranı - Lisanslı Kiosk İstemci Motoru v3.2
 * 
 * Özellikler:
 * 1. Screen Wake Lock API (TV ve Android Box uyku modunu engelleme)
 * 2. Çevrimdışı Dayanıklılık (LocalStorage önbelleği & kesinti bildirimi)
 * 3. Bellek Sızıntısı Önleyici (Her gece 05:00 otomatik sayfa yenileme)
 * 4. Çoklu Ekran Temaları:
 *    - Tema 1: classic_grid (Klasik 4'lü Izgara & Harita)
 *    - Tema 2: animated_route (Canlı Sokak/Cadde Yol Navigasyon Animasyonu - OSRM)
 *    - Tema 3: focus_carousel (Vitrin Carousel & Dev Odak Kartı)
 *    - Tema 4: dual_card (İkili Dev Kart & Harita - 2 Eczane Odaklı, Sıfır Taşma)
 *    - Tema 5: auto_rotate (Karma Mod - Saatlik Otomatik Dönüşüm: 1 -> 2 -> 3 -> 4)
 * 5. Dinamik Mesafe Sıralaması, OSRM Gerçek Yürüyüş Rotası & Adım Adım Navigasyon QR Kodu
 */

const KIOSK_AYARLAR = {
    POLLING_ARALIGI_MS: 15 * 60 * 1000, // 15 dakikada bir veri tazeleme ve heartbeat
    HATA_TEKRAR_DENE_MS: 60 * 1000,     // Ağ kesintisinde 60 saniyede bir tekrar deneme
    SAAT_ARALIGI_MS: 1000,              // Saniyede bir saat güncelleme
    GECE_RELOAD_SAATI: 5,               // Her gece 05:00'te bellek temizliği için yenileme
    SLAYT_SURESI_MS: 10000              // Tema 2 ve Tema 3 için slayt süresi (10 saniye)
};

// Global Durum Değişkenleri
let kioskMap = null;
let mapMarkersGroup = null;
let routeLineGroup = null;
let screenWakeLock = null;
let sonGeceReloadGunu = -1;

let aktifTema = (typeof BASLANGIC_TEMASI !== 'undefined' && BASLANGIC_TEMASI) ? BASLANGIC_TEMASI : 'classic_grid';
let sonGecerliVeri = null;
let guncelEczaneler = [];
let guncelKendiEczane = null;
let slaytIndex = 0;
let slaytTimer = null;
let progressTimer = null;
let progressStartTime = 0;
let sonUygulananSaatlikGorunum = null;

// DOM Eleman Referansları
const elDigitalClock = document.getElementById('digital-clock');
const elCalendarDate = document.getElementById('calendar-date');
const elPharmacyGrid = document.getElementById('pharmacy-grid');
const elPharmacyCount = document.getElementById('pharmacy-count');
const elLastSyncTime = document.getElementById('last-sync-time');
const elSystemAlert = document.getElementById('system-alert');
const elAlertTitle = document.getElementById('alert-title');
const elAlertMessage = document.getElementById('alert-message');
const elAlertBadgeType = document.getElementById('alert-badge-type');
const elOnDutyBanner = document.getElementById('on-duty-banner');
const elTickerText = document.getElementById('ticker-text');
const elBrandName = document.getElementById('pharmacy-brand-name');
const elOfflineBadge = document.getElementById('offline-badge');
const elOfflineSyncTime = document.getElementById('offline-sync-time');
const elKioskLayout = document.getElementById('kiosk-layout');
const elMapPanelTitle = document.getElementById('map-panel-title');
const elProgressBarContainer = document.getElementById('route-progress-bar-container');
const elProgressBar = document.getElementById('route-progress-bar');


/**
 * Aktif Görünüm Modunu Belirler:
 * 'auto_rotate' (Karma Mod) ise o anki saate göre dönüşümlü tema seçer:
 *   Saat % 4 == 0 -> classic_grid
 *   Saat % 4 == 1 -> animated_route
 *   Saat % 4 == 2 -> focus_carousel
 *   Saat % 4 == 3 -> dual_card
 */
function aktifGorunumuBelirle() {
    if (aktifTema === 'auto_rotate') {
        const saat = new Date().getHours();
        const temalar = ['classic_grid', 'animated_route', 'focus_carousel', 'dual_card'];
        return temalar[saat % temalar.length];
    }
    return aktifTema;
}


/**
 * 1. Screen Wake Lock API - Ekranın Kapanmasını / Uyku Modunu Engeller
 */
async function ekranWakeLockBaslat() {
    if ('wakeLock' in navigator) {
        try {
            screenWakeLock = await navigator.wakeLock.request('screen');
            console.log('[Kiosk] Screen WakeLock aktif.');
            screenWakeLock.addEventListener('release', () => {
                console.log('[Kiosk] Screen WakeLock serbest bırakıldı.');
            });
        } catch (err) {
            console.warn('[Kiosk] WakeLock alınamadı:', err);
        }
    }
}

document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
        ekranWakeLockBaslat();
    }
});


/**
 * 2. Bellek Sızıntısı Önleyici (Her Gece 05:00'te Sayfa Yenileme)
 */
function geceHafizaTemizligiKontrolEt(simdi) {
    const saat = simdi.getHours();
    const dakika = simdi.getMinutes();
    const gun = simdi.getDate();

    if (saat === KIOSK_AYARLAR.GECE_RELOAD_SAATI && dakika === 0 && sonGeceReloadGunu !== gun) {
        sonGeceReloadGunu = gun;
        console.log('[Kiosk] Gece 05:00 bellek temizleme yenilemesi başlatılıyor...');
        window.location.reload(true);
    }
}


/**
 * 3. Canlı Dijital Saat, Türkçe Tarih ve Saatlik Karma Mod Kontrolü
 */
function saatVeTarihiGuncelle() {
    const simdi = new Date();
    const saat = String(simdi.getHours()).padStart(2, '0');
    const dakika = String(simdi.getMinutes()).padStart(2, '0');
    const saniye = String(simdi.getSeconds()).padStart(2, '0');
    if (elDigitalClock) elDigitalClock.textContent = `${saat}:${dakika}:${saniye}`;

    const gunler = ['Pazar', 'Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi'];
    const aylar = [
        'Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
        'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık'
    ];

    if (elCalendarDate) {
        elCalendarDate.textContent = `${simdi.getDate()} ${aylar[simdi.getMonth()]} ${simdi.getFullYear()}, ${gunler[simdi.getDay()]}`;
    }

    // Saatlik Karma Mod (auto_rotate) Kontrolü
    if (aktifTema === 'auto_rotate') {
        const buSaatGorunum = aktifGorunumuBelirle();
        if (sonUygulananSaatlikGorunum !== buSaatGorunum && sonGecerliVeri) {
            console.log(`[Kiosk] Karma Mod: Saat değişimiyle yeni temaya geçiliyor (${buSaatGorunum})`);
            arayuzuGuncelle(sonGecerliVeri, false);
        }
    }

    geceHafizaTemizligiKontrolEt(simdi);
}


/**
 * 4. Bu TV Ekranı İçin Cihaz Kimliği (Hardware/Device Lock)
 */
function getOrCreateDeviceToken() {
    let token = localStorage.getItem('kiosk_device_token');
    if (!token) {
        if (window.crypto && crypto.randomUUID) {
            token = 'tv-' + crypto.randomUUID();
        } else {
            token = 'tv-' + Math.random().toString(36).substring(2, 15) + '-' + Date.now();
        }
        localStorage.setItem('kiosk_device_token', token);
    }
    return token;
}


/**
 * 5. Koyu Tema Leaflet.js Canlı Harita Motoru
 */
function haritayiIlkKezOlustur() {
    const mapContainer = document.getElementById('kiosk-leaflet-map');
    if (!mapContainer || typeof L === 'undefined') return;

    if (!kioskMap) {
        kioskMap = L.map('kiosk-leaflet-map', {
            zoomControl: false,
            attributionControl: false
        });

        // Koyu Tema Harita Katmanı (OpenStreetMap + CSS Koyu Gece Filtresi)
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '© OpenStreetMap'
        }).addTo(kioskMap);

        mapMarkersGroup = L.featureGroup().addTo(kioskMap);
        routeLineGroup = L.featureGroup().addTo(kioskMap);
    }

    // Leaflet'in container boyutunu algılamasını garanti et
    setTimeout(() => {
        if (kioskMap) kioskMap.invalidateSize();
    }, 100);
}

/**
 * Haritadaki Pinleri Çizer
 */
function haritaPinleriniCiz(kendiEczane, nobetciler, seciliIndex = null) {
    haritayiIlkKezOlustur();
    if (!kioskMap || !mapMarkersGroup) return;

    mapMarkersGroup.clearLayers();
    const koordinatNoktalari = [];

    // Kendi Eczanemiz Pini (Mavi Parlayan Pin)
    if (kendiEczane && kendiEczane.latitude && kendiEczane.longitude) {
        const originIcon = L.divIcon({
            className: 'custom-leaflet-marker',
            html: `<div class="origin-marker-pin" title="${escapeHtml(kendiEczane.name)}">⭐</div>`,
            iconSize: [32, 32],
            iconAnchor: [16, 16]
        });

        const originMarker = L.marker([kendiEczane.latitude, kendiEczane.longitude], { icon: originIcon })
            .bindPopup(`<b>⭐ ${escapeHtml(kendiEczane.name)}</b><br><small>Buradasınız</small>`);
        mapMarkersGroup.addLayer(originMarker);
        koordinatNoktalari.push([kendiEczane.latitude, kendiEczane.longitude]);
    }

    // Nöbetçi Eczaneler Pinleri
    if (Array.isArray(nobetciler)) {
        nobetciler.forEach((e, idx) => {
            if (e.enlem && e.boylam) {
                const isSelected = (seciliIndex !== null && idx === seciliIndex);
                const dutyIcon = L.divIcon({
                    className: 'custom-leaflet-marker',
                    html: `<div class="pulse-ring-pin ${isSelected ? 'active-focused-pin' : ''}" title="${escapeHtml(e.isim)}">
                             ${isSelected ? '<span class="pin-selected-star">📍</span>' : ''}
                           </div>`,
                    iconSize: isSelected ? [36, 36] : [28, 28],
                    iconAnchor: isSelected ? [18, 18] : [14, 14]
                });

                const popupHtml = `
                    <div style="font-family:Inter,sans-serif; color:#0f172a; font-size:12px;">
                        <strong style="color:#b91c1c; font-size:13px;">${escapeHtml(e.isim)}</strong><br>
                        <span>${escapeHtml(e.adres || '')}</span><br>
                        <b style="color:#0284c7;">${escapeHtml(e.mesafe_metin || '')}</b>
                    </div>
                `;

                const marker = L.marker([e.enlem, e.boylam], { icon: dutyIcon }).bindPopup(popupHtml);
                mapMarkersGroup.addLayer(marker);
                koordinatNoktalari.push([e.enlem, e.boylam]);
            }
        });
    }

    setTimeout(() => {
        if (kioskMap) kioskMap.invalidateSize();
    }, 100);

    return koordinatNoktalari;
}

/**
 * Tema 2: OSRM API ile Gerçek Sokak/Cadde Yürüyüş Rotası Çizme
 */
async function haritadaRotaGoster(kendiEczane, hedefEczane) {
    if (!kioskMap || !routeLineGroup) return;

    routeLineGroup.clearLayers();

    if (!kendiEczane || !kendiEczane.latitude || !kendiEczane.longitude ||
        !hedefEczane || !hedefEczane.enlem || !hedefEczane.boylam) {
        return;
    }

    const startLng = kendiEczane.longitude;
    const startLat = kendiEczane.latitude;
    const endLng = hedefEczane.boylam;
    const endLat = hedefEczane.enlem;

    const osrmUrl = `https://router.project-osrm.org/route/v1/foot/${startLng},${startLat};${endLng},${endLat}?overview=full&geometries=geojson`;

    try {
        const yanit = await fetch(osrmUrl);
        const data = await yanit.json();

        if (data.code === 'Ok' && data.routes && data.routes.length > 0) {
            const rotaKoordinatlari = data.routes[0].geometry.coordinates;
            const leafletNoktalar = rotaKoordinatlari.map(k => [k[1], k[0]]);

            const baseRota = L.polyline(leafletNoktalar, {
                color: '#0369a1',
                weight: 7,
                opacity: 0.5,
                lineCap: 'round',
                lineJoin: 'round'
            });
            routeLineGroup.addLayer(baseRota);

            const pulseRota = L.polyline(leafletNoktalar, {
                color: '#38bdf8',
                weight: 4,
                dashArray: '12, 16',
                className: 'animated-nav-polyline',
                opacity: 0.95,
                lineCap: 'round',
                lineJoin: 'round'
            });
            routeLineGroup.addLayer(pulseRota);

            try {
                kioskMap.flyToBounds(baseRota.getBounds(), {
                    padding: [40, 40],
                    maxZoom: 16,
                    duration: 1.2
                });
            } catch (e) {
                kioskMap.fitBounds(baseRota.getBounds(), { padding: [40, 40] });
            }
        } else {
            yedekDuzCizgiCiz(startLat, startLng, endLat, endLng);
        }
    } catch (hata) {
        yedekDuzCizgiCiz(startLat, startLng, endLat, endLng);
    }

    setTimeout(() => {
        if (kioskMap) kioskMap.invalidateSize();
    }, 150);
}

function yedekDuzCizgiCiz(startLat, startLng, endLat, endLng) {
    const start = [startLat, startLng];
    const end = [endLat, endLng];

    const baseLine = L.polyline([start, end], {
        color: '#0369a1', weight: 5, opacity: 0.4,
        lineCap: 'round', dashArray: '8, 12'
    });
    routeLineGroup.addLayer(baseLine);

    try {
        kioskMap.flyToBounds([start, end], { padding: [40, 40], maxZoom: 16, duration: 1.2 });
    } catch (e) {
        kioskMap.fitBounds([start, end], { padding: [40, 40] });
    }
}


/**
 * 6. ŞABLONLAR: Klasik Kart, Dev Odak Kartı ve İkili Kart
 */

// Şablon A: Klasik Izgara Kartı (Tema 1)
function eczaneKartiHtmlUret(eczane, index) {
    const semtHtml = eczane.semt 
        ? `<span class="badge-semt">${escapeHtml(eczane.semt)}</span>` 
        : '';

    const mesafeHtml = eczane.mesafe_metin 
        ? `<span class="badge-distance">🚶 <strong>${escapeHtml(eczane.mesafe_metin)}</strong> (${escapeHtml(eczane.yurume_metin)})</span>` 
        : '';

    const yolTarifiHtml = eczane.yol_tarifi 
        ? `
        <div class="card-landmark-box">
            <span class="card-landmark-icon">📍</span>
            <span>${escapeHtml(eczane.yol_tarifi)}</span>
        </div>` 
        : '';

    const qrKodUrl = eczane.qr_kod_url || `https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=${encodeURIComponent(eczane.rota_linki || eczane.harita_linki)}`;

    return `
    <article class="pharmacy-card" data-id="${eczane.id}" data-index="${index}">
        <div class="card-left-info">
            <div class="card-top-row">
                <h2 class="pharmacy-name-title">${escapeHtml(eczane.isim)}</h2>
                <div class="card-badges">
                    <span class="badge-duty">
                        <span class="pulse-indicator" style="width: 7px; height: 7px;"></span>
                        ${escapeHtml(eczane.nobet_durumu || 'Sabaha kadar açık')}
                    </span>
                    ${mesafeHtml}
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
                     alt="${escapeHtml(eczane.isim)} Rota QR Kodu"
                     loading="lazy" />
            </div>
            <div class="qr-caption">
                Kamerayla <span>Rota Başlat</span>
            </div>
        </div>
    </article>
    `;
}

// Şablon B: Dev Odak Kartı (Tema 2 & Tema 3 İçin - Asla Taşmaz)
function devOdakKartiHtmlUret(eczane, siraNo, toplamAdet, modAdi = 'NAVİGASYON') {
    const qrKodUrl = eczane.qr_kod_url || `https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(eczane.rota_linki || eczane.harita_linki)}`;

    const yolTarifiHtml = eczane.yol_tarifi 
        ? `
        <div class="focus-landmark-card">
            <div class="focus-landmark-header">
                <span class="focus-landmark-icon">📍</span>
                <span class="focus-section-label" style="color: #fde68a; margin-bottom: 0;">YOL TARİFİ / BİLİNEN YER</span>
            </div>
            <p class="focus-landmark-text">${escapeHtml(eczane.yol_tarifi)}</p>
        </div>` 
        : '';

    let paginationPills = '';
    for (let i = 0; i < toplamAdet; i++) {
        const isCurrent = (i === siraNo);
        paginationPills += `
            <button class="focus-page-btn ${isCurrent ? 'active' : ''}" onclick="manuelSlaytaGit(${i})">
                ${i + 1}. Nöbetçi
            </button>
        `;
    }

    return `
    <div class="focus-container-card animate-fade-in">
        <div class="focus-header-row">
            <div class="focus-duty-tag">
                <span class="pulse-indicator"></span>
                <span>NÖBETÇİ ECZANE ${siraNo + 1} / ${toplamAdet}</span>
            </div>
            <div class="focus-mode-badge">${modAdi} MODU</div>
        </div>

        <div class="focus-main-info">
            <div class="focus-name-block">
                <div style="display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap;">
                    <h2 class="focus-title" style="margin-bottom: 0;">${escapeHtml(eczane.isim)}</h2>
                    ${eczane.semt ? `<span class="badge-semt" style="font-size: 0.88rem;">${escapeHtml(eczane.semt)}</span>` : ''}
                </div>
            </div>
        </div>

        <div class="focus-body-grid">
            <!-- Sol Sütun: Mesafe Bilgisi & Karekod Navigasyon Bloğu -->
            <div class="focus-qr-col">
                ${eczane.mesafe_metin ? `
                    <div class="focus-qr-distance-badge">
                        <span class="qr-dist-label">🚶 Buradan</span>
                        <div class="qr-dist-val"><strong>${escapeHtml(eczane.mesafe_metin)}</strong></div>
                        <div class="qr-walk-val">(${escapeHtml(eczane.yurume_metin)})</div>
                    </div>` : ''}

                <div class="focus-qr-frame">
                    <img class="focus-qr-image" 
                         src="${qrKodUrl}" 
                         alt="${escapeHtml(eczane.isim)} Harita QR" />
                </div>
                <div class="focus-qr-text">
                    📲 Okutup <strong>anında rota başlatın</strong>
                </div>
            </div>

            <!-- Sağ Sütun: Açık Adres, Yol Tarifi ve Sabit Telefon (Geniş & Ferah Alan) -->
            <div class="focus-details-col">
                <div class="focus-address-card">
                    <span class="focus-section-label">AÇIK ADRES</span>
                    <p class="focus-address-text">${escapeHtml(eczane.adres)}</p>
                </div>

                ${yolTarifiHtml}

                <div class="focus-phone-card">
                    <div class="phone-icon-box" style="width: 30px; height: 30px; min-width: 30px;">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
                            <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"></path>
                        </svg>
                    </div>
                    <div>
                        <span class="focus-section-label" style="margin-bottom: 0.1rem;">SABİT TELEFON</span>
                        <div class="focus-phone-number">${escapeHtml(eczane.telefon || 'Belirtilmedi')}</div>
                    </div>
                </div>
            </div>
        </div>

        <div class="focus-pagination-bar">
            ${paginationPills}
        </div>
    </div>
    `;
}

// Şablon C: İkili Dev Kart (Tema 4 - Sadece 2 Eczane Odaklı, Sıfır Taşma)
function ikiliEczaneKartiHtmlUret(eczane, siraNo) {
    const qrKodUrl = eczane.qr_kod_url || `https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=${encodeURIComponent(eczane.rota_linki || eczane.harita_linki)}`;

    const yolTarifiHtml = eczane.yol_tarifi 
        ? `
        <div class="card-landmark-box" style="margin-top: 0.2rem;">
            <span class="card-landmark-icon">📍</span>
            <span>${escapeHtml(eczane.yol_tarifi)}</span>
        </div>` 
        : '';

    return `
    <article class="dual-pharmacy-card animate-fade-in" data-id="${eczane.id}">
        <div class="dual-card-left">
            <div class="dual-card-top-row">
                <div style="display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;">
                    <span class="badge" style="background: rgba(239, 68, 68, 0.2); color: #fca5a5; border: 1px solid rgba(239, 68, 68, 0.4); font-weight: 800;">
                        ⭐ ${siraNo + 1}. EN YAKIN NÖBETÇİ
                    </span>
                    <span class="badge-duty">
                        <span class="pulse-indicator" style="width: 7px; height: 7px;"></span>
                        ${escapeHtml(eczane.nobet_durumu || 'Sabaha kadar açık')}
                    </span>
                    ${eczane.mesafe_metin ? `
                        <span class="badge-distance">
                            🚶 <strong>${escapeHtml(eczane.mesafe_metin)}</strong> (${escapeHtml(eczane.yurume_metin)})
                        </span>` : ''}
                </div>
            </div>

            <div>
                <h2 class="dual-pharmacy-title">${escapeHtml(eczane.isim)}</h2>
            </div>

            <div class="dual-card-address-block">
                <p class="dual-card-address-text">${escapeHtml(eczane.adres)}</p>
                ${yolTarifiHtml}
            </div>

            <div class="dual-card-phone-row">
                <div class="phone-icon-box">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
                        <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"></path>
                    </svg>
                </div>
                <span class="dual-phone-number">${escapeHtml(eczane.telefon || 'Belirtilmedi')}</span>
            </div>
        </div>

        <div class="dual-card-right-qr">
            <div class="dual-qr-wrapper">
                <img class="dual-qr-image" 
                     src="${qrKodUrl}" 
                     alt="${escapeHtml(eczane.isim)} Rota QR" />
            </div>
            <div class="qr-caption" style="font-size: 0.68rem; margin-top: 0.2rem;">
                Kamerayla <span>Rota Başlat</span>
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

function uyariDurumunuAyarla(gosterilsinMi, baslik = '', mesaj = '', rozet = 'ÖNBELLEK MODU') {
    if (gosterilsinMi) {
        if (elAlertTitle) elAlertTitle.textContent = baslik;
        if (elAlertMessage) elAlertMessage.textContent = mesaj;
        if (elAlertBadgeType) elAlertBadgeType.textContent = rozet;
        if (elSystemAlert) elSystemAlert.classList.remove('hidden');
    } else {
        if (elSystemAlert) elSystemAlert.classList.add('hidden');
    }
}


/**
 * 7. Çevrimdışı (Offline) Dayanıklılık Motoru
 */
function yerelOnbellegeKaydet(veri) {
    try {
        localStorage.setItem('kiosk_last_cached_data', JSON.stringify({
            savedAt: new Date().toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' }),
            data: veri
        }));
    } catch (e) {
        console.warn('[Kiosk] Yerel önbelleğe yazılamadı:', e);
    }
}

function yerelOnbellegiYukle() {
    try {
        const kayitStr = localStorage.getItem('kiosk_last_cached_data');
        if (kayitStr) {
            return JSON.parse(kayitStr);
        }
    } catch (e) {
        console.warn('[Kiosk] Yerel önbellek okunamadı:', e);
    }
    return null;
}


/**
 * 8. DÖNGÜ VE SLAYT YÖNETİMİ (Tema 2 & Tema 3 İçin)
 */
function slaytDongusunuDurdur() {
    if (slaytTimer) { clearInterval(slaytTimer); slaytTimer = null; }
    if (progressTimer) { clearInterval(progressTimer); progressTimer = null; }
    if (elProgressBarContainer) elProgressBarContainer.style.display = 'none';
}

function slaytIlerlemeAnimasyonunuBaslat() {
    if (!elProgressBarContainer || !elProgressBar) return;
    elProgressBarContainer.style.display = 'block';
    elProgressBar.style.width = '0%';
    progressStartTime = Date.now();

    if (progressTimer) clearInterval(progressTimer);
    progressTimer = setInterval(() => {
        const gecen = Date.now() - progressStartTime;
        const yuzde = Math.min(100, (gecen / KIOSK_AYARLAR.SLAYT_SURESI_MS) * 100);
        elProgressBar.style.width = yuzde + '%';
        if (gecen >= KIOSK_AYARLAR.SLAYT_SURESI_MS) {
            clearInterval(progressTimer);
        }
    }, 50);
}

function slaytGoster() {
    if (!guncelEczaneler || guncelEczaneler.length === 0) return;

    if (slaytIndex >= guncelEczaneler.length) {
        slaytIndex = 0;
    }

    const seciliEczane = guncelEczaneler[slaytIndex];
    const gorunum = aktifGorunumuBelirle();

    if (gorunum === 'animated_route') {
        // Tema 2: Canlı Yol & Navigasyon Rota
        if (elMapPanelTitle) elMapPanelTitle.textContent = `CANLI ROTA & NAVİGASYON (${seciliEczane.isim})`;
        elPharmacyGrid.innerHTML = devOdakKartiHtmlUret(seciliEczane, slaytIndex, guncelEczaneler.length, 'CANLI ROTA');
        
        haritaPinleriniCiz(guncelKendiEczane, guncelEczaneler, slaytIndex);
        haritadaRotaGoster(guncelKendiEczane, seciliEczane);

    } else if (gorunum === 'focus_carousel') {
        // Tema 3: Vitrin Carousel & Dev Odak Kartı (Harita Siyah Ekran Hatası Çözüldü)
        if (elMapPanelTitle) elMapPanelTitle.textContent = `BÖLGE NÖBETÇİ HARİTASI (${seciliEczane.isim})`;
        elPharmacyGrid.innerHTML = devOdakKartiHtmlUret(seciliEczane, slaytIndex, guncelEczaneler.length, 'VİTRİN');
        
        // Önceki rotayı temizle
        if (routeLineGroup) routeLineGroup.clearLayers();

        // Pinleri çiz ve seçiliyi vurgula
        haritaPinleriniCiz(guncelKendiEczane, guncelEczaneler, slaytIndex);

        // Haritayı hem lisanslı eczane hem seçili nöbetçiyi gösterecek şekilde odakla
        if (kioskMap) {
            setTimeout(() => {
                kioskMap.invalidateSize();
                if (guncelKendiEczane && guncelKendiEczane.latitude && guncelKendiEczane.longitude &&
                    seciliEczane.enlem && seciliEczane.boylam) {
                    try {
                        kioskMap.flyToBounds([
                            [guncelKendiEczane.latitude, guncelKendiEczane.longitude],
                            [seciliEczane.enlem, seciliEczane.boylam]
                        ], { padding: [40, 40], maxZoom: 16, duration: 1.2 });
                    } catch (e) {
                        kioskMap.fitBounds([
                            [guncelKendiEczane.latitude, guncelKendiEczane.longitude],
                            [seciliEczane.enlem, seciliEczane.boylam]
                        ], { padding: [40, 40] });
                    }
                } else if (seciliEczane.enlem && seciliEczane.boylam) {
                    kioskMap.flyTo([seciliEczane.enlem, seciliEczane.boylam], 15, { duration: 1.2 });
                }
            }, 100);
        }
    }

    slaytIlerlemeAnimasyonunuBaslat();
}

function manuelSlaytaGit(index) {
    slaytIndex = index;
    slaytGoster();

    if (slaytTimer) {
        clearInterval(slaytTimer);
        slaytTimer = setInterval(() => {
            slaytIndex = (slaytIndex + 1) % guncelEczaneler.length;
            slaytGoster();
        }, KIOSK_AYARLAR.SLAYT_SURESI_MS);
    }
}


/**
 * 9. ARAYÜZÜ VERİ İLE DOLDURMA MOTORU
 */
function arayuzuGuncelle(veri, isOffline = false, savedTime = '') {
    sonGecerliVeri = veri;

    // 1. Eczane Özel Bilgileri ve Tema Belirleme
    if (veri.pharmacy) {
        if (veri.pharmacy.name && elBrandName) elBrandName.textContent = veri.pharmacy.name;
        
        // Kayan Yazı Duyurusu ve Sabit Nöbet Saati
        const duyuruMetni = veri.pharmacy.ticker_text || 'Eczanemiz halk sağlığı için hizmetinizdedir.';
        const sabitNobetDuyurusu = '⏰ Nöbet Saatleri: 19:00 — 09:00 (Sabaha kadar kesintisiz açıktır)';
        if (elTickerText) {
            elTickerText.textContent = `${duyuruMetni}   •   ${sabitNobetDuyurusu}`;
        }
        
        if (veri.pharmacy.theme && veri.pharmacy.theme !== aktifTema) {
            aktifTema = veri.pharmacy.theme;
        }

        // TV / Mi Box Ekran Çözünürlüğü ve Ölçek Ayarını Uygula
        ekranOlceginiUygula(veri.pharmacy.screen_scale || 'auto');
    }

    // Aktif Görünümü Belirle (auto_rotate ise saat bazlı mod)
    const gorunum = aktifGorunumuBelirle();
    sonUygulananSaatlikGorunum = gorunum;

    document.body.dataset.theme = gorunum;
    if (elKioskLayout) {
        elKioskLayout.className = `kiosk-content-layout layout-${gorunum}`;
    }

    // "Bu Gece Nöbetçiyiz" Vurgusu
    if (elOnDutyBanner) {
        if (veri.is_on_duty_today) {
            elOnDutyBanner.classList.add('active');
        } else {
            elOnDutyBanner.classList.remove('active');
        }
    }

    // Çevrimdışı Rozeti
    if (isOffline) {
        elOfflineBadge.classList.remove('hidden');
        if (elOfflineSyncTime) elOfflineSyncTime.textContent = savedTime || '--:--';
    } else {
        elOfflineBadge.classList.add('hidden');
    }

    // Mesafe Sıralaması (En Yakın Eczane 1. Sırada)
    if (Array.isArray(veri.eczaneler) && veri.eczaneler.length > 0) {
        veri.eczaneler.sort((a, b) => {
            const mA = (typeof a.mesafe_metre === 'number') ? a.mesafe_metre : 9999999;
            const mB = (typeof b.mesafe_metre === 'number') ? b.mesafe_metre : 9999999;
            return mA - mB;
        });

        guncelEczaneler = veri.eczaneler;
        guncelKendiEczane = veri.pharmacy;
        elPharmacyCount.textContent = veri.eczaneler.length;

        const simdiSaat = new Date().toLocaleTimeString('tr-TR');
        elLastSyncTime.textContent = veri.onbellek_zamani ? veri.onbellek_zamani.split(' ')[1] : simdiSaat;

        // SEÇİLEN GÖRÜNÜME GÖRE RENDER:
        if (gorunum === 'animated_route' || gorunum === 'focus_carousel') {
            // Tema 2 veya Tema 3: Slayt Döngüsü
            slaytDongusunuDurdur();
            slaytGoster();

            slaytTimer = setInterval(() => {
                slaytIndex = (slaytIndex + 1) % guncelEczaneler.length;
                slaytGoster();
            }, KIOSK_AYARLAR.SLAYT_SURESI_MS);

        } else if (gorunum === 'dual_card') {
            // Tema 4: İkili Dev Kart (Sadece en yakın 2 nöbetçi devasa gösterilir)
            slaytDongusunuDurdur();
            if (routeLineGroup) routeLineGroup.clearLayers();
            if (elMapPanelTitle) elMapPanelTitle.textContent = `CANLI HARİTA (EN YAKIN 2 NÖBETÇİ)`;

            const ilk2 = guncelEczaneler.slice(0, 2);
            elPharmacyGrid.className = 'pharmacy-grid-container';
            elPharmacyGrid.innerHTML = `
                <div class="dual-pharmacy-cards-container">
                    ${ilk2.map((e, idx) => ikiliEczaneKartiHtmlUret(e, idx)).join('')}
                </div>
            `;

            // Harita: Lisanslı eczane ve ilk 2 eczaneyi göster
            const noktalar = haritaPinleriniCiz(veri.pharmacy, ilk2);
            if (noktalar && noktalar.length > 0 && kioskMap) {
                setTimeout(() => {
                    kioskMap.invalidateSize();
                    kioskMap.fitBounds(noktalar, { padding: [35, 35], maxZoom: 15 });
                }, 100);
            }

        } else {
            // Tema 1: classic_grid (Klasik 4'lü Izgara & Harita)
            slaytDongusunuDurdur();
            if (routeLineGroup) routeLineGroup.clearLayers();
            if (elMapPanelTitle) elMapPanelTitle.textContent = `CANLI HARİTA & YOL TARİFİ`;

            elPharmacyGrid.className = 'pharmacy-grid-container';
            if (veri.eczaneler.length === 1) elPharmacyGrid.classList.add('grid-count-1');
            else if (veri.eczaneler.length === 3) elPharmacyGrid.classList.add('grid-count-3');
            else if (veri.eczaneler.length >= 5) elPharmacyGrid.classList.add('grid-count-6');

            elPharmacyGrid.innerHTML = veri.eczaneler.map((e, idx) => eczaneKartiHtmlUret(e, idx)).join('');

            const noktalar = haritaPinleriniCiz(veri.pharmacy, veri.eczaneler);
            if (noktalar && noktalar.length > 0 && kioskMap) {
                setTimeout(() => {
                    kioskMap.invalidateSize();
                    kioskMap.fitBounds(noktalar, { padding: [35, 35], maxZoom: 15 });
                }, 100);
            }
        }

        // Haritanın siyah kalmaması için genel invalidate tetikleyicisi
        setTimeout(() => {
            if (kioskMap) kioskMap.invalidateSize();
        }, 200);

        // Bildirim Mesajları
        if (isOffline) {
            uyariDurumunuAyarla(true, 'Ağ Kesintisi', 'İnternet bağlantısı kesildi. Ekran son hafızadaki verilerle yayında kalıyor.', 'ÇEVRİMDIŞI');
        } else if (veri.kaynak === 'stale_cache') {
            uyariDurumunuAyarla(true, 'Geçici Ağ Uyarısı', 'Dış kaynağa anlık erişilemedi, son başarılı önbellek listeleniyor.');
        } else if (veri.kaynak === 'fallback_offline') {
            uyariDurumunuAyarla(true, 'Yedek Modu', 'Canlı veri bağlantısı kurulamadı. Ekran yedek verilerle yayında kalıyor.');
        } else {
            uyariDurumunuAyarla(false);
        }
    }
}


/**
 * 10. Lisanslı Kiosk Verilerini ve Heartbeat Sinyalini Gönderir
 */
async function kioskVerileriniGetir() {
    const deviceToken = getOrCreateDeviceToken();
    const ekranCozunurluk = `${window.innerWidth}x${window.innerHeight}`;
    const apiAdresi = `/api/kiosk-data?key=${encodeURIComponent(LISANS_KEY)}&device_token=${encodeURIComponent(deviceToken)}&res=${encodeURIComponent(ekranCozunurluk)}&_t=${Date.now()}`;

    try {
        const yanit = await fetch(apiAdresi);

        if (yanit.status === 403 || yanit.status === 401) {
            const errData = await yanit.json().catch(() => ({}));
            
            // Cihaz Limiti Aşımı Durumu
            if (errData.reason === 'device_limit_exceeded') {
                document.body.innerHTML = `
                    <div style="background:#080a10;color:#fff;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:2rem;text-align:center;font-family:Inter,sans-serif;">
                        <div style="background:rgba(18,24,38,0.95);border:2px solid #f59e0b;border-radius:24px;padding:3.5rem 3rem;max-width:700px;box-shadow:0 0 40px rgba(245,158,11,0.2);">
                            <div style="font-size:4rem;margin-bottom:1rem;">📺</div>
                            <h1 style="font-size:2.2rem;margin-bottom:1rem;color:#fbbf24;">Cihaz Limiti Dolu</h1>
                            <p style="font-size:1.25rem;color:#cbd5e1;line-height:1.6;margin-bottom:2rem;">
                                Bu lisans anahtarı için tanımlı maksimum TV ekranı sınırına (${errData.max_devices || 1} Cihaz) ulaşılmıştır.
                            </p>
                            <div style="background:rgba(0,0,0,0.5);border:1px dashed rgba(255,255,255,0.2);padding:1rem 1.8rem;border-radius:12px;display:inline-block;font-family:'JetBrains Mono',monospace;color:#38bdf8;font-size:1.3rem;font-weight:700;margin-bottom:1.5rem;">
                                Lisans: ${escapeHtml(LISANS_KEY)} (${errData.max_devices}/${errData.max_devices} TV Dolu)
                            </div>
                            <p style="font-size:0.95rem;color:#94a3b8;line-height:1.5;">
                                Yeni bir TV veya kiosk ekranı bağlamak için lütfen Yönetim Panelinden cihaz limitini artırın veya eski bir TV ekranının kilidini kaldırın.
                            </p>
                        </div>
                    </div>
                `;
                return;
            }

            if (errData.reason === 'device_mismatch') {
                document.body.innerHTML = `
                    <div style="background:#080a10;color:#fff;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:2rem;text-align:center;font-family:Inter,sans-serif;">
                        <div style="background:rgba(18,24,38,0.95);border:2px solid #ef4444;border-radius:24px;padding:3.5rem 3rem;max-width:700px;box-shadow:0 0 40px rgba(239,68,68,0.2);">
                            <div style="font-size:4rem;margin-bottom:1rem;">🔒</div>
                            <h1 style="font-size:2.2rem;margin-bottom:1rem;color:#f87171;">Cihaz Kilidi Engeli</h1>
                            <p style="font-size:1.25rem;color:#cbd5e1;line-height:1.6;margin-bottom:2rem;">
                                Bu lisans anahtarı başka bir TV ekranına kilitlenmiştir.
                            </p>
                            <div style="background:rgba(0,0,0,0.5);border:1px dashed rgba(255,255,255,0.2);padding:1rem 1.8rem;border-radius:12px;display:inline-block;font-family:'JetBrains Mono',monospace;color:#fbbf24;font-size:1.4rem;font-weight:700;margin-bottom:1.5rem;">
                                Lisans: ${escapeHtml(LISANS_KEY)}
                            </div>
                            <p style="font-size:0.95rem;color:#64748b;">
                                Yönetim Panelinden "Cihaz Kilitlerini Sıfırla" butonuna tıklayıp sayfayı yenileyiniz.
                            </p>
                        </div>
                    </div>
                `;
                return;
            }
            window.location.reload();
            return;
        }

        if (!yanit.ok) throw new Error(`HTTP ${yanit.status}`);

        const veri = await yanit.json();

        if (veri && veri.success) {
            yerelOnbellegeKaydet(veri);
            arayuzuGuncelle(veri, false);
        }
    } catch (hata) {
        console.error('[HATA] Kiosk verisi çekilemedi:', hata);
        
        const yerelKayit = yerelOnbellegiYukle();
        if (yerelKayit && yerelKayit.data) {
            arayuzuGuncelle(yerelKayit.data, true, yerelKayit.savedAt);
        } else {
            uyariDurumunuAyarla(true, 'Bağlantı Kesintisi', 'Veriler tazelenirken sorun oluştu. 60 saniye içinde yeniden denenecek...');
        }
        
        setTimeout(kioskVerileriniGetir, KIOSK_AYARLAR.HATA_TEKRAR_DENE_MS);
    }
}


/**
 * 11. TV Kiosk Başlatıcı
 */
function kioskBaslat() {
    ekranWakeLockBaslat();

    saatVeTarihiGuncelle();
    setInterval(saatVeTarihiGuncelle, KIOSK_AYARLAR.SAAT_ARALIGI_MS);

    kioskVerileriniGetir();
    setInterval(kioskVerileriniGetir, KIOSK_AYARLAR.POLLING_ARALIGI_MS);

    // Çift tıklamayla TV tam ekran modunu açıp kapatma
    document.addEventListener('dblclick', () => {
        if (!document.fullscreenElement) {
            document.documentElement.requestFullscreen().catch(() => {});
        } else {
            document.exitFullscreen().catch(() => {});
        }
    });

    // Pencere boyutu değiştiğinde ölçeği ve haritayı yeniden hesapla
    window.addEventListener('resize', () => {
        if (sonGecerliVeri && sonGecerliVeri.pharmacy) {
            ekranOlceginiUygula(sonGecerliVeri.pharmacy.screen_scale || 'auto');
        }
        if (kioskMap) {
            setTimeout(() => kioskMap.invalidateSize(), 200);
        }
    });
}

/**
 * TV ve Kiosk Ekran Çözünürlüğünü Ayarlar (Mi Box & TV Uyumluluğu)
 */
function ekranOlceginiUygula(scaleAyar = 'auto') {
    document.body.classList.remove('scale-compact', 'scale-720p', 'scale-1080p', 'scale-4k');

    if (scaleAyar === 'auto') {
        const vh = window.innerHeight;
        // Mi Box veya TV tarayıcılarında navigasyon barı açıkken yükseklik < 780px kalır
        if (vh < 780) {
            document.body.classList.add('scale-compact');
        } else if (vh < 920) {
            document.body.classList.add('scale-720p');
        } else if (vh < 1450) {
            document.body.classList.add('scale-1080p');
        } else {
            document.body.classList.add('scale-4k');
        }
    } else {
        document.body.classList.add(`scale-${scaleAyar}`);
    }
}

document.addEventListener('DOMContentLoaded', kioskBaslat);
