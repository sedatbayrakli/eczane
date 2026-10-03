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
let isOfflineModAktif = false;


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
        const arMinutes = (window._kioskThemeSettings && window._kioskThemeSettings.auto_rotate_minutes) ? window._kioskThemeSettings.auto_rotate_minutes : 60;
        const totalPeriods = Math.floor(Date.now() / (arMinutes * 60 * 1000));
        const temalar = ['classic_grid', 'animated_route', 'focus_carousel', 'dual_card'];
        return temalar[totalPeriods % temalar.length];
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
    nobetGeriSayiminiGuncelle();
}

/**
 * 3.1 Nöbet Saatleri ve Canlı Geri Sayım Hesaplayıcı (Pazar & Resmi Tatil 24 Saat Desteği)
 */
function isBugunTatilVeyaPazar(tarihObj) {
    // 0: Pazar günü
    if (tarihObj.getDay() === 0) return true;

    // Sabit Resmi Tatiller (Ay-Gün)
    const m = tarihObj.getMonth() + 1;
    const d = tarihObj.getDate();
    const resmiTatiller = [
        '1-1',   // Yılbaşı
        '4-23',  // 23 Nisan Ulusal Egemenlik ve Çocuk Bayramı
        '5-1',   // 1 Mayıs Emek ve Dayanışma Günü
        '5-19',  // 19 Mayıs Atatürk'ü Anma, Gençlik ve Spor Bayramı
        '7-15',  // 15 Temmuz Demokrasi ve Milli Birlik Günü
        '8-30',  // 30 Ağustos Zafer Bayramı
        '10-29'  // 29 Ekim Cumhuriyet Bayramı
    ];
    return resmiTatiller.includes(`${m}-${d}`);
}

function nobetZamaniniHesapla() {
    const simdi = new Date();
    const saat = simdi.getHours();
    const pad = (n) => String(n).padStart(2, '0');

    const bugunTatil = isBugunTatilVeyaPazar(simdi);
    
    // Dün tatil miydi? (Örn: Pazar sabahı saat 00:00-09:00 arası Cumartesi nöbetidir, Pazartesi sabah 00:00-09:00 arası Pazar nöbetidir)
    const dun = new Date(simdi);
    dun.setDate(dun.getDate() - 1);
    const dunTatil = isBugunTatilVeyaPazar(dun);

    let isNobetSaatinde = false;
    let baslangicBitis = '19:00 — 09:00';
    let baslikMetni = 'NÖBET SAATLERİ';
    let etiket = 'Nöbet Bitimine';
    let hedefZaman = new Date(simdi);

    if (bugunTatil) {
        // PAZAR VEYA RESMİ TATİL GÜNÜ
        baslangicBitis = '09:00 — 09:00 (24 Sa)';
        baslikMetni = 'PAZAR NÖBETİ (24 SA)';

        if (saat < 9) {
            // Sabah 09:00'a kadar: Dünkü nöbetin son saatleri
            isNobetSaatinde = true;
            hedefZaman.setHours(9, 0, 0, 0);
            etiket = 'Nöbet Devrine';
            baslangicBitis = dunTatil ? '09:00 — 09:00' : '19:00 — 09:00';
            baslikMetni = 'NÖBET SAATLERİ';
        } else {
            // Sabah 09:00'dan sonra: Pazar nöbeti başlamıştır, ertesi sabah (Pazartesi) 09:00'a kadar 24 saat kesintisiz açıktır!
            isNobetSaatinde = true;
            hedefZaman.setDate(hedefZaman.getDate() + 1);
            hedefZaman.setHours(9, 0, 0, 0);
            etiket = 'Nöbet Bitimine';
        }
    } else {
        // HAFTA İÇİ VEYA CUMARTESİ
        if (saat < 9) {
            // Sabah 09:00'a kadar dün akşamdan devralınan nöbet
            isNobetSaatinde = true;
            hedefZaman.setHours(9, 0, 0, 0);
            etiket = 'Nöbet Bitimine';
            baslangicBitis = dunTatil ? '09:00 — 09:00 (Pazar)' : '19:00 — 09:00';
        } else if (saat >= 19) {
            // Akşam 19:00'dan sonra gece nöbeti
            isNobetSaatinde = true;
            hedefZaman.setDate(hedefZaman.getDate() + 1);
            hedefZaman.setHours(9, 0, 0, 0);
            etiket = 'Nöbet Bitimine';
            baslangicBitis = '19:00 — 09:00';
        } else {
            // Gündüz normal mesai: Akşam 19:00 nöbet başlangıç hedefi
            isNobetSaatinde = false;
            hedefZaman.setHours(19, 0, 0, 0);
            etiket = 'Akşam Nöbetine';
            baslangicBitis = '19:00 — 09:00';
        }
    }

    const farkMs = Math.max(0, hedefZaman - simdi);
    const toplamSaniye = Math.floor(farkMs / 1000);
    const ksaat = Math.floor(toplamSaniye / 3600);
    const kdakika = Math.floor((toplamSaniye % 3600) / 60);
    const ksaniye = toplamSaniye % 60;

    return {
        isNobetSaatinde: isNobetSaatinde,
        baslangicBitis: baslangicBitis,
        baslikMetni: baslikMetni,
        saatStr: pad(ksaat),
        dakikaStr: pad(kdakika),
        saniyeStr: pad(ksaniye),
        etiket: etiket
    };
}

function nobetGeriSayiminiGuncelle() {
    const zaman = nobetZamaniniHesapla();
    const sayaclar = document.querySelectorAll('.duty-live-countdown');
    sayaclar.forEach(sayac => {
        const saatEl = sayac.querySelector('.countdown-h');
        const dakEl = sayac.querySelector('.countdown-m');
        const etiketEl = sayac.querySelector('.countdown-label');
        if (saatEl) saatEl.textContent = zaman.saatStr;
        if (dakEl) dakEl.textContent = zaman.dakikaStr;
        if (etiketEl && etiketEl.textContent !== zaman.etiket) {
            etiketEl.textContent = zaman.etiket;
        }
    });

    // Nöbet saatleri çerçeve değerlerini de güncelle
    document.querySelectorAll('.duty-hours-val').forEach(el => {
        if (el.textContent !== zaman.baslangicBitis) el.textContent = zaman.baslangicBitis;
    });
    document.querySelectorAll('.compact-duty-hours').forEach(el => {
        if (el.textContent !== zaman.baslangicBitis) el.textContent = zaman.baslangicBitis;
    });
}

function formatMesafeMetin(metin) {
    if (!metin) return '';
    return String(metin).replace(/(\d+)\s*m$/i, '$1 mt');
}

function nobetBilgisiHtmlUret(stil = 'focus') {
    const zaman = nobetZamaniniHesapla();

    if (stil === 'focus') {
        return `
        <div class="focus-duty-time-card">
            <!-- 1. Kutu: Nöbet Saatleri Çerçevesi (2 Satır) -->
            <div class="duty-framed-box duty-hours-frame">
                <span class="duty-frame-label">${zaman.baslikMetni}</span>
                <span class="duty-hours-val">${zaman.baslangicBitis}</span>
            </div>

            <!-- 2. Kutu: Nöbet Bitimine Geri Sayım Çerçevesi (2 Satır) -->
            <div class="duty-framed-box duty-countdown-frame duty-live-countdown">
                <div class="countdown-tag-row" style="justify-content: center; width: 100%;">
                    <span class="countdown-label" style="white-space: nowrap; font-size: 0.62rem; letter-spacing: 0.02em;">${zaman.etiket}</span>
                </div>
                <div class="countdown-clock">
                    <span class="countdown-num countdown-h">${zaman.saatStr}</span><span class="countdown-unit">sa</span>
                    <span class="countdown-sep">:</span>
                    <span class="countdown-num countdown-m">${zaman.dakikaStr}</span><span class="countdown-unit">dk</span>
                </div>
            </div>
        </div>
        `;
    } else {
        return `
        <div class="compact-duty-time-card">
            <div class="duty-framed-box compact-frame" style="padding: 0.2rem 0.5rem;">
                <span class="compact-duty-title" style="font-size: 0.72rem; color: #94a3b8;">${zaman.baslikMetni}</span>
                <strong class="compact-duty-hours" style="font-size: 0.88rem; color: #fff;">${zaman.baslangicBitis}</strong>
            </div>
            <div class="duty-framed-box compact-frame duty-live-countdown compact-countdown" style="padding: 0.2rem 0.5rem;">
                <span class="countdown-label" style="font-size: 0.62rem;">${zaman.etiket}</span>
                <div class="countdown-clock">
                    <span class="countdown-num countdown-h">${zaman.saatStr}</span><span class="countdown-unit">sa</span>
                    <span class="countdown-sep">:</span>
                    <span class="countdown-num countdown-m">${zaman.dakikaStr}</span><span class="countdown-unit">dk</span>
                </div>
            </div>
        </div>
        `;
    }
}


/**
 * 4. Sabit Donanım Parmak İzi (Hardware Fingerprint) ve Değişmez MAC Üretimi
 * Web tarayıcıları doğrudan fiziksel MAC'e erişemediği için ekran, CPU, WebGL GPU ve
 * platform donanım özelliklerinden deterministik (sabit) kimlik üretilir.
 * TV Bro veya tarayıcı kapanıp açılsa, önbellek silinse bile HER ZAMAN AYNI MAC ÜRETİLİR.
 */
function getHardwareFingerprint() {
    let components = [
        navigator.userAgent || '',
        screen.width + 'x' + screen.height,
        screen.colorDepth || 24,
        navigator.hardwareConcurrency || 4,
        navigator.platform || '',
        navigator.language || ''
    ];

    // WebGL GPU Renderer Bilgisi (Android TV ve Box cihazlarında donanıma özeldir)
    try {
        const canvas = document.createElement('canvas');
        const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
        if (gl) {
            const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
            if (debugInfo) {
                components.push(gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL) || '');
                components.push(gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL) || '');
            }
        }
    } catch (e) {}

    // FNV-1a benzeri 64-bit deterministik hash algoritması
    const str = components.join('###');
    let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
    for (let i = 0; i < str.length; i++) {
        const ch = str.charCodeAt(i);
        h1 = Math.imul(h1 ^ ch, 2654435761);
        h2 = Math.imul(h2 ^ ch, 1597334677);
    }
    h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
    h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);

    const part1 = (h1 >>> 0).toString(16).padStart(8, '0').toUpperCase();
    const part2 = (h2 >>> 0).toString(16).padStart(8, '0').toUpperCase();
    return (part1 + part2);
}

function getOrCreateDeviceToken() {
    // 1. LocalStorage
    let token = localStorage.getItem('kiosk_device_token');
    if (token) return token;

    // 2. Cookie kontrolü
    const cookieMatch = document.cookie.match(/(?:^|; )kiosk_device_token=([^;]*)/);
    if (cookieMatch && cookieMatch[1]) {
        token = decodeURIComponent(cookieMatch[1]);
        localStorage.setItem('kiosk_device_token', token);
        return token;
    }

    // 3. Deterministik Donanım Parmak İzinden Sabit Token
    const fp = getHardwareFingerprint();
    token = 'tv-hw-' + fp;
    try {
        localStorage.setItem('kiosk_device_token', token);
        document.cookie = `kiosk_device_token=${encodeURIComponent(token)}; max-age=315360000; path=/; SameSite=Lax`;
    } catch (e) {}
    return token;
}

function getOrCreateDeviceMac() {
    // 1. LocalStorage
    let mac = localStorage.getItem('kiosk_device_mac');
    if (mac && /^([0-9A-F]{2}:){5}[0-9A-F]{2}$/i.test(mac)) {
        return mac;
    }

    // 2. Cookie
    const cookieMatch = document.cookie.match(/(?:^|; )kiosk_device_mac=([^;]*)/);
    if (cookieMatch && cookieMatch[1]) {
        mac = decodeURIComponent(cookieMatch[1]);
        localStorage.setItem('kiosk_device_mac', mac);
        return mac;
    }

    // 3. Deterministik Donanım Parmak İzinden Sabit MAC Üret
    const fp = getHardwareFingerprint();
    mac = `4A:${fp.substring(0,2)}:${fp.substring(2,4)}:${fp.substring(4,6)}:${fp.substring(6,8)}:${fp.substring(8,10)}`.toUpperCase();

    try {
        localStorage.setItem('kiosk_device_mac', mac);
        document.cookie = `kiosk_device_mac=${encodeURIComponent(mac)}; max-age=315360000; path=/; SameSite=Lax`;
    } catch (e) {}

    return mac;
}

// WebRTC üzerinden Yerel Ağ IP'sini (192.168.x.x) tespit etme
let kioskLocalIP = localStorage.getItem('kiosk_local_ip') || '';
function yerelIpTespitEt() {
    try {
        const pc = new RTCPeerConnection({ iceServers: [] });
        pc.createDataChannel('');
        pc.createOffer().then(offer => pc.setLocalDescription(offer)).catch(() => {});
        pc.onicecandidate = (ice) => {
            if (!ice || !ice.candidate || !ice.candidate.candidate) return;
            const match = /([0-9]{1,3}(\.[0-9]{1,3}){3})/.exec(ice.candidate.candidate);
            if (match && match[1] && !match[1].startsWith('127.')) {
                kioskLocalIP = match[1];
                localStorage.setItem('kiosk_local_ip', kioskLocalIP);
            }
        };
    } catch (e) {}
}
yerelIpTespitEt();


/**
 * 4.5 ÇEVRİMDIŞI (OFFLINE) DAYANIKLILIK MOTORLARI: KAREKOD & HARİTA
 */
const KIOSK_TILE_CACHE_NAME = 'kiosk-map-tiles-v1';

// Slippy Map Matematiksel Karo (Tile) Hesaplayıcıları
function lon2tile(lon, zoom) {
    return Math.floor((lon + 180) / 360 * Math.pow(2, zoom));
}
function lat2tile(lat, zoom) {
    return Math.floor((1 - Math.log(Math.tan(lat * Math.PI / 180) + 1 / Math.cos(lat * Math.PI / 180)) / Math.PI) / 2 * Math.pow(2, zoom));
}

/**
 * Yerel QR Kod Üretici (Sıfır Dış Ağ Bağımlılığı - Offline Uyumlu)
 */
function yerelQrKodUretDataUri(metin) {
    if (!metin) return '';
    try {
        if (typeof QRCode !== 'undefined') {
            const tempDiv = document.createElement('div');
            tempDiv.style.display = 'none';
            document.body.appendChild(tempDiv);

            new QRCode(tempDiv, {
                text: metin,
                width: 200,
                height: 200,
                colorDark: '#000000',
                colorLight: '#ffffff',
                correctLevel: QRCode.CorrectLevel.M
            });

            let dataUrl = '';
            const canvas = tempDiv.querySelector('canvas');
            if (canvas) {
                dataUrl = canvas.toDataURL('image/png');
            } else {
                const img = tempDiv.querySelector('img');
                if (img && img.src) dataUrl = img.src;
            }

            tempDiv.remove();
            return dataUrl;
        }
    } catch (e) {
        console.warn('[QR] Yerel QR üretilemedi:', e);
    }
    return '';
}

// Resim yüklenemezse veya çevrimdışıyken kırılırsa otomatik kurtarma
window.yerelQrKodFallback = function(imgEl, hedefUrl) {
    if (!imgEl || imgEl._fallbackUygulandi) return;
    imgEl._fallbackUygulandi = true;
    try {
        const yerelData = yerelQrKodUretDataUri(hedefUrl);
        if (yerelData) {
            imgEl.src = yerelData;
            imgEl.style.display = 'block';
        }
    } catch (e) {
        console.warn('[QR Fallback Hatası]', e);
    }
};

/**
 * Harita Karolarını (Tile) Arka Planda Sessizce İndirip Cache Storage'a Saklar
 * Böylece internet kesilse bile harita sokak sokak ekranda kalır!
 */
async function haritaTilelariniOnbellegeAl(kendiEczane, nobetciler) {
    if (!('caches' in window) || !navigator.onLine) return;

    try {
        const cache = await caches.open(KIOSK_TILE_CACHE_NAME);
        const tumNoktalar = [];

        if (kendiEczane && kendiEczane.latitude && kendiEczane.longitude) {
            tumNoktalar.push([parseFloat(kendiEczane.latitude), parseFloat(kendiEczane.longitude)]);
        }

        if (Array.isArray(nobetciler)) {
            nobetciler.forEach(e => {
                if (e.enlem && e.boylam) {
                    tumNoktalar.push([parseFloat(e.enlem), parseFloat(e.boylam)]);
                }
            });
        }

        if (tumNoktalar.length === 0) return;

        let minLat = 90, maxLat = -90, minLon = 180, maxLon = -180;
        tumNoktalar.forEach(([lat, lon]) => {
            if (lat < minLat) minLat = lat;
            if (lat > maxLat) maxLat = lat;
            if (lon < minLon) minLon = lon;
            if (lon > maxLon) maxLon = lon;
        });

        // Kapsama alanını biraz genişlet (bölge sokaklarını da al)
        const latPad = Math.max(0.015, (maxLat - minLat) * 0.35);
        const lonPad = Math.max(0.015, (maxLon - minLon) * 0.35);
        minLat -= latPad; maxLat += latPad;
        minLon -= lonPad; maxLon += lonPad;

        // Zoom 13, 14 ve 15 seviyelerini önbelleğe al
        const zoomSeviyeleri = [13, 14, 15];
        const tileUrls = [];

        zoomSeviyeleri.forEach(z => {
            const minX = lon2tile(minLon, z);
            const maxX = lon2tile(maxLon, z);
            const minY = lat2tile(maxLat, z);
            const maxY = lat2tile(minLat, z);

            const xBas = Math.min(minX, maxX);
            const xBit = Math.max(minX, maxX);
            const yBas = Math.min(minY, maxY);
            const yBit = Math.max(minY, maxY);

            // Sınır kontrolü (maksimum 40 karo/seviye)
            if ((xBit - xBas + 1) * (yBit - yBas + 1) <= 30) {
                for (let x = xBas; x <= xBit; x++) {
                    for (let y = yBas; y <= yBit; y++) {
                        tileUrls.push(`https://tile.openstreetmap.org/${z}/${x}/${y}.png`);
                    }
                }
            }
        });

        // Arka planda sessizce indir ve cache'e yaz
        let sayac = 0;
        for (const url of tileUrls) {
            if (sayac++ > 75) break; // Cihazı kasmamak için üst limit
            const mevcut = await cache.match(url);
            if (!mevcut) {
                fetch(url, { mode: 'cors' }).then(res => {
                    if (res && res.ok) {
                        cache.put(url, res);
                    }
                }).catch(() => {});
            }
        }
        console.log(`[Kiosk Harita] ${tileUrls.length} adet karo çevrimdışı önbelleğe alındı.`);
    } catch (err) {
        console.warn('[Kiosk Harita] Karo önbellekleme uyarısı:', err);
    }
}

/**
 * Özel Leaflet Çevrimdışı Karo Katmanı (Offline TileLayer)
 * Öncelikli olarak tarayıcı yerel diskindeki Cache Storage'dan okur (Cache-First).
 * İnternet kesilse bile harita sokak sokak görünmeye devam eder!
 */
let KioskOfflineTileLayer = null;
if (typeof L !== 'undefined') {
    KioskOfflineTileLayer = L.TileLayer.extend({
        createTile: function(coords, done) {
            const tile = document.createElement('img');
            const url = this.getTileUrl(coords);
            tile.setAttribute('role', 'presentation');

            if ('caches' in window) {
                caches.open(KIOSK_TILE_CACHE_NAME).then(cache => {
                    cache.match(url).then(cachedResponse => {
                        if (cachedResponse) {
                            // 1. ÖNCELİK: YEREL CACHE'TEN GETİR (İnternet olmasa da anında yüklenir!)
                            cachedResponse.blob().then(blob => {
                                tile.src = URL.createObjectURL(blob);
                                done(null, tile);
                            }).catch(() => {
                                this._indirVeSakla(url, tile, cache, done);
                            });
                        } else {
                            // 2. Cache'te yoksa internetten indir ve cache'e sakla
                            this._indirVeSakla(url, tile, cache, done);
                        }
                    }).catch(() => {
                        this._indirVeSakla(url, tile, cache, done);
                    });
                }).catch(() => {
                    this._dogrudanGoster(url, tile, done);
                });
            } else {
                this._dogrudanGoster(url, tile, done);
            }

            return tile;
        },

        _indirVeSakla: function(url, tile, cache, done) {
            tile.onload = () => done(null, tile);
            tile.onerror = () => {
                // Çevrimdışı ve karo bulunamazsa şık koyu grid deseni göster (asla gri/boş kalmaz)
                tile.src = 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256"><rect width="256" height="256" fill="%230b1120"/><path d="M0 64 H256 M0 128 H256 M0 192 H256 M64 0 V256 M128 0 V256 M192 0 V256" stroke="%231e293b" stroke-width="1" stroke-dasharray="2 4"/><circle cx="128" cy="128" r="48" fill="none" stroke="%23334155" stroke-width="1" stroke-dasharray="2 4"/></svg>';
                done(null, tile);
            };

            if (cache && navigator.onLine) {
                fetch(url, { mode: 'cors' }).then(res => {
                    if (res && res.ok) {
                        cache.put(url, res.clone()).catch(() => {});
                        return res.blob();
                    }
                    throw new Error('Tile fetch failed');
                }).then(blob => {
                    tile.src = URL.createObjectURL(blob);
                    done(null, tile);
                }).catch(() => {
                    tile.src = url;
                });
            } else {
                tile.src = url;
            }
        },

        _dogrudanGoster: function(url, tile, done) {
            tile.onload = () => done(null, tile);
            tile.onerror = () => {
                tile.src = 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256"><rect width="256" height="256" fill="%230b1120"/><path d="M0 64 H256 M0 128 H256 M0 192 H256 M64 0 V256 M128 0 V256 M192 0 V256" stroke="%231e293b" stroke-width="1" stroke-dasharray="2 4"/></svg>';
                done(null, tile);
            };
            tile.src = url;
        }
    });
}

/**
 * 5. Koyu Tema Leaflet.js Canlı Harita Motoru (Çevrimdışı Önbellek Korumalı)
 */
function haritayiIlkKezOlustur() {
    const mapContainer = document.getElementById('kiosk-leaflet-map');
    if (!mapContainer || typeof L === 'undefined') return;

    if (!kioskMap) {
        kioskMap = L.map('kiosk-leaflet-map', {
            zoomControl: false,
            attributionControl: false
        });

        // Çevrimdışı Destekli Koyu Tema Harita Katmanı (Cache-First)
        if (KioskOfflineTileLayer) {
            new KioskOfflineTileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
                maxZoom: 19,
                attribution: '© OpenStreetMap'
            }).addTo(kioskMap);
        } else {
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                maxZoom: 19,
                attribution: '© OpenStreetMap'
            }).addTo(kioskMap);
        }

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
                        <b style="color:#0284c7;">${escapeHtml(formatMesafeMetin(e.mesafe_metin || ''))}</b>
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

    const arabaMetin = eczane.araba_metin || (eczane.mesafe_metre ? `~${Math.max(1, Math.round(eczane.mesafe_metre / 500))} dk` : '');
    const yurumeMetin = eczane.yurume_metin || (eczane.mesafe_metre ? `~${Math.max(1, Math.round(eczane.mesafe_metre / 75))} dk` : '');

    const mesafeHtml = eczane.mesafe_metin 
        ? `<span class="badge-distance">${arabaMetin ? `🚗 <strong>${escapeHtml(arabaMetin)}</strong> • ` : ''}🚶 <strong>${escapeHtml(formatMesafeMetin(eczane.mesafe_metin))}</strong> (${escapeHtml(yurumeMetin)})</span>` 
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
                <div class="card-badges">
                    <span class="badge-duty">
                        <span class="pulse-indicator" style="width: 7px; height: 7px;"></span>
                        ${escapeHtml(eczane.nobet_durumu || 'Sabaha kadar açık')}
                    </span>
                    ${mesafeHtml}
                </div>
            </div>

            <!-- Eczane İsmi Açık Adres Kutusunun İçine Alındı -->
            <div class="card-address-block">
                <div style="display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; margin-bottom: 0.4rem; flex-wrap: wrap;">
                    <h2 class="pharmacy-name-title" style="margin: 0;">${escapeHtml(eczane.isim)}</h2>
                    ${semtHtml}
                </div>
                <p class="card-address-text">${escapeHtml(eczane.adres)}</p>
                ${yolTarifiHtml}
            </div>

            ${nobetBilgisiHtmlUret('compact')}

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
                     onerror="window.yerelQrKodFallback(this, '${escapeHtml(eczane.rota_linki || eczane.harita_linki || '')}');"
                     loading="eager" />
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

    const arabaMetin = eczane.araba_metin || (eczane.mesafe_metre ? `~${Math.max(1, Math.round(eczane.mesafe_metre / 500))} dk` : '');
    const yurumeMetin = eczane.yurume_metin || (eczane.mesafe_metre ? `~${Math.max(1, Math.round(eczane.mesafe_metre / 75))} dk` : '');

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

        <div class="focus-body-grid">
            <!-- Sol Sütun: Mesafe Bilgisi (3 Satır: Mesafe, Araba, Yürüme) & Karekod -->
            <div class="focus-qr-col">
                ${eczane.mesafe_metin ? `
                    <div class="focus-qr-distance-badge-stacked">
                        <div class="dist-row-meter">
                            <span class="dist-pin-symbol">📍</span>
                            <strong class="dist-meter-text">${escapeHtml(formatMesafeMetin(eczane.mesafe_metin))}</strong>
                        </div>
                        <div class="dist-row-car">
                            <span class="dist-mode-icon">🚗</span>
                            <span class="dist-mode-text">Araba ${escapeHtml((arabaMetin || '~1 dk').replace('~', ''))}</span>
                        </div>
                        <div class="dist-row-walk">
                            <span class="dist-mode-icon">🚶</span>
                            <span class="dist-mode-text">Yürüme ${escapeHtml((yurumeMetin || '~2 dk').replace('~', ''))}</span>
                        </div>
                    </div>` : ''}

                <div class="focus-qr-frame">
                    <img class="focus-qr-image" 
                         src="${qrKodUrl}" 
                         alt="${escapeHtml(eczane.isim)} Harita QR"
                         onerror="window.yerelQrKodFallback(this, '${escapeHtml(eczane.rota_linki || eczane.harita_linki || '')}');"
                         loading="eager" />
                </div>
                <div class="focus-qr-text">
                    📲 Okutup <strong>anında rota başlatın</strong>
                </div>
            </div>

            <!-- Sağ Sütun: Eczane İsmi & Adres Tek Kutuda + Telefon -->
            <div class="focus-details-col">
                <div class="focus-address-card">
                    <div class="focus-pharmacy-name-row" style="display: flex; align-items: center; justify-content: space-between; gap: 0.6rem; border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 0.45rem; margin-bottom: 0.45rem; flex-wrap: wrap;">
                        <h2 class="focus-title" style="margin: 0; font-size: 1.55rem; color: #fff; font-weight: 800;">${escapeHtml(eczane.isim)}</h2>
                        ${eczane.semt ? `<span class="badge-semt" style="font-size: 0.88rem;">${escapeHtml(eczane.semt)}</span>` : ''}
                    </div>
                    <p class="focus-address-text">${escapeHtml(eczane.adres)}</p>
                </div>

                ${nobetBilgisiHtmlUret('focus')}

                <div class="focus-phone-card">
                    <div class="phone-icon-box" style="width: 30px; height: 30px; min-width: 30px;">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
                            <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"></path>
                        </svg>
                    </div>
                    <div class="focus-phone-number">${escapeHtml(eczane.telefon || 'Belirtilmedi')}</div>
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

    const arabaMetin = eczane.araba_metin || (eczane.mesafe_metre ? `~${Math.max(1, Math.round(eczane.mesafe_metre / 500))} dk` : '');
    const yurumeMetin = eczane.yurume_metin || (eczane.mesafe_metre ? `~${Math.max(1, Math.round(eczane.mesafe_metre / 75))} dk` : '');

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
                            ${arabaMetin ? `🚗 <strong>${escapeHtml(arabaMetin)}</strong> • ` : ''}🚶 <strong>${escapeHtml(formatMesafeMetin(eczane.mesafe_metin))}</strong> (${escapeHtml(yurumeMetin)})
                        </span>` : ''}
                </div>
            </div>

            <!-- Eczane İsmi Açık Adres Kutusuna Alındı -->
            <div class="dual-card-address-block">
                <div style="display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; margin-bottom: 0.4rem; flex-wrap: wrap;">
                    <h2 class="dual-pharmacy-title" style="margin: 0;">${escapeHtml(eczane.isim)}</h2>
                    ${eczane.semt ? `<span class="badge-semt">${escapeHtml(eczane.semt)}</span>` : ''}
                </div>
                <p class="dual-card-address-text">${escapeHtml(eczane.adres)}</p>
                ${yolTarifiHtml}
            </div>

            ${nobetBilgisiHtmlUret('compact')}

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
                     alt="${escapeHtml(eczane.isim)} Rota QR"
                     onerror="window.yerelQrKodFallback(this, '${escapeHtml(eczane.rota_linki || eczane.harita_linki || '')}');"
                     loading="eager" />
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
    if (!veri) return;

    try {
        // 1. QR Kodları LocalStorage İçin Base64 Data URI'ye Çevirip Gömme
        if (Array.isArray(veri.eczaneler)) {
            veri.eczaneler.forEach(e => {
                if (!e.qr_kod_url || !e.qr_kod_url.startsWith('data:image/')) {
                    const hedef = e.rota_linki || e.harita_linki || `https://maps.google.com/?q=${e.enlem},${e.boylam}`;
                    const yerelData = yerelQrKodUretDataUri(hedef);
                    if (yerelData) {
                        e.qr_kod_url = yerelData;
                    }
                }
            });
        }

        // 2. Veriyi ve Gömülü QR'ları LocalStorage'a Kaydet
        localStorage.setItem('kiosk_last_cached_data', JSON.stringify({
            savedAt: new Date().toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' }),
            data: veri
        }));

        // 3. Bölge Harita Karolarını (Tile) Çevrimdışı Diske Sakla
        if (veri.pharmacy && Array.isArray(veri.eczaneler)) {
            haritaTilelariniOnbellegeAl(veri.pharmacy, veri.eczaneler);
        }
    } catch (e) {
        console.warn('[Kiosk] Yerel önbelleğe yazılamadı:', e);
    }
}

function yerelOnbellegiYukle() {
    try {
        const kayitStr = localStorage.getItem('kiosk_last_cached_data');
        if (kayitStr) {
            const parsed = JSON.parse(kayitStr);
            // Güvenlik: QR kodları çevrimdışı doğrula (kırık resim olmasını önle)
            if (parsed && parsed.data && Array.isArray(parsed.data.eczaneler)) {
                parsed.data.eczaneler.forEach(e => {
                    if (!e.qr_kod_url || !e.qr_kod_url.startsWith('data:image/')) {
                        const hedef = e.rota_linki || e.harita_linki || `https://maps.google.com/?q=${e.enlem},${e.boylam}`;
                        const yerelData = yerelQrKodUretDataUri(hedef);
                        if (yerelData) e.qr_kod_url = yerelData;
                    }
                });
            }
            return parsed;
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
        const rotaPrefix = isOfflineModAktif ? '💾 ÇEVRİMDIŞI ROTA & HARİTA' : 'CANLI ROTA & NAVİGASYON';
        if (elMapPanelTitle) elMapPanelTitle.textContent = `${rotaPrefix} (${seciliEczane.isim})`;
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

    // Harita Alt Bant Yol Tarifi Güncellemesi (İki Bant Arasında Harita)
    const elLandmarkBar = document.getElementById('map-panel-landmark-bar');
    const elLandmarkText = document.getElementById('map-panel-landmark-text');
    if (elLandmarkBar && elLandmarkText) {
        if (seciliEczane && seciliEczane.yol_tarifi) {
            elLandmarkText.textContent = seciliEczane.yol_tarifi;
            elLandmarkBar.style.display = 'flex';
        } else {
            elLandmarkBar.style.display = 'none';
        }
    }

    slaytIlerlemeAnimasyonunuBaslat();
    temaIcerikGorunurlukleriniUygula();
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
 * Kiosk Ekran Teması Parametrik Görünürlük Ayarlarını DOM'a Uygular
 */
function temaIcerikGorunurlukleriniUygula(ts) {
    if (!ts) ts = window._kioskThemeSettings;
    if (!ts) return;

    // Nöbet Bitimi Sayacı (show_countdown)
    const countdownEls = document.querySelectorAll('.duty-countdown-frame, .duty-live-countdown');
    countdownEls.forEach(el => {
        el.style.display = (ts.show_countdown === false) ? 'none' : '';
    });

    // QR Kod Bölümü (show_qr)
    const qrCols = document.querySelectorAll('.focus-qr-col, .pharmacy-qr, .qr-section');
    qrCols.forEach(el => {
        el.style.display = (ts.show_qr === false) ? 'none' : '';
    });

    // Ulaşım / Seyahat Süreleri (show_travel_times)
    const travelBoxes = document.querySelectorAll('.travel-modes-box');
    travelBoxes.forEach(el => {
        el.style.display = (ts.show_travel_times === false) ? 'none' : '';
    });

    // İlçe Nöbetçi Sayacı (show_district_counter)
    const counterPill = document.querySelector('.pharmacy-counter');
    if (counterPill) {
        counterPill.style.display = (ts.show_district_counter === false) ? 'none' : '';
    }

    // Yol Tarifi / Landmark Bandı (show_landmark)
    const landmarkBar = document.getElementById('map-panel-landmark-bar');
    if (landmarkBar && ts.show_landmark === false) {
        landmarkBar.style.display = 'none';
    }
}


/**
 * 9. ARAYÜZÜ VERİ İLE DOLDURMA MOTORU
 */
function arayuzuGuncelle(veri, isOffline = false, savedTime = '') {
    sonGecerliVeri = veri;
    isOfflineModAktif = Boolean(isOffline);

    // 1. Eczane Özel Bilgileri ve Tema Belirleme
    if (veri.pharmacy) {
        if (veri.pharmacy.name && elBrandName) elBrandName.textContent = veri.pharmacy.name;
        const elDistrictName = document.getElementById('header-district-name');
        if (elDistrictName && veri.pharmacy.district) {
            elDistrictName.textContent = veri.pharmacy.district;
        }
        
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

        // Kiosk Ekran Teması Parametrik Ayarlarını Uygula
        if (veri.pharmacy.theme_settings) {
            const ts = veri.pharmacy.theme_settings;
            window._kioskThemeSettings = ts;
            if (ts.carousel_interval_sec && ts.carousel_interval_sec > 0) {
                KIOSK_AYARLAR.SLAYT_SURESI_MS = ts.carousel_interval_sec * 1000;
            }
            if (ts.auto_rotate_minutes && ts.auto_rotate_minutes > 0) {
                KIOSK_AYARLAR.AUTO_ROTATE_MINUTES = ts.auto_rotate_minutes;
            }
            if (ts.map_zoom) {
                KIOSK_AYARLAR.MAP_ZOOM = ts.map_zoom;
            }
            temaIcerikGorunurlukleriniUygula(ts);
        }
    }

    // Cihaz Tanımlama (Identify / Ekranda Göster) Sinyali Kontrolü
    if (veri.identify && veri.identify.active) {
        cihazTanimlamaGoster(veri.identify);
    } else {
        cihazTanimlamaGizle();
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
    const deviceMac = getOrCreateDeviceMac();
    const localIp = localStorage.getItem('kiosk_local_ip') || kioskLocalIP || '';
    const ekranCozunurluk = `${window.innerWidth}x${window.innerHeight}`;
    const apiAdresi = `/api/kiosk-data?key=${encodeURIComponent(LISANS_KEY)}&device_token=${encodeURIComponent(deviceToken)}&mac=${encodeURIComponent(deviceMac)}&local_ip=${encodeURIComponent(localIp)}&res=${encodeURIComponent(ekranCozunurluk)}&_t=${Date.now()}`;

    try {
        const yanit = await fetch(apiAdresi);

        if (yanit.status === 403 || yanit.status === 401) {
            const errData = await yanit.json().catch(() => ({}));
            
            // Cihaz Lisans Onayı Bekleme Durumu (Admin Aktivasyonu)
            if (errData.reason === 'device_pending_approval') {
                window._cihazOnayBekliyor = true;

                // Orijinal sayfa yapısını yok etmeden tam ekran overlay göster
                let overlay = document.getElementById('device-pending-approval-overlay');
                if (!overlay) {
                    overlay = document.createElement('div');
                    overlay.id = 'device-pending-approval-overlay';
                    overlay.style.cssText = 'position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:999999;background:#080a10;display:flex;align-items:center;justify-content:center;padding:2rem;text-align:center;font-family:Inter,sans-serif;';
                    document.body.appendChild(overlay);
                }

                overlay.innerHTML = `
                    <div style="background:rgba(18,24,38,0.96);border:1.5px solid rgba(56,189,248,0.35);border-radius:24px;padding:2.2rem 2.8rem;max-width:640px;width:100%;box-shadow:0 25px 60px rgba(0,0,0,0.65), 0 0 35px rgba(56,189,248,0.15);box-sizing:border-box;">
                        <div style="width:58px;height:58px;background:rgba(56,189,248,0.12);border:1px solid rgba(56,189,248,0.3);border-radius:16px;display:inline-flex;align-items:center;justify-content:center;font-size:2rem;margin-bottom:1rem;">📺</div>
                        <h1 style="font-size:1.45rem;font-weight:800;margin-bottom:0.4rem;color:#ffffff;letter-spacing:-0.01em;">Cihaz Lisans Onayı Bekleniyor</h1>
                        <p style="font-size:0.92rem;color:#94a3b8;line-height:1.5;margin-bottom:1.4rem;">
                            Bu ekran sisteme kaydedildi ve <strong style="color:#e2e8f0;">${escapeHtml(errData.pharmacy_name || 'Eczane')}</strong> lisansına bağlandı.<br>
                            Yönetim panelinden onay verildiğinde ekran <strong>otomatik olarak yayına başlayacaktır</strong>.
                        </p>
                        
                        <!-- Dengeli 3 Kutucuklu Bilgi Alanı -->
                        <div style="display:grid;grid-template-columns:repeat(3, 1fr);gap:0.75rem;margin-bottom:1.4rem;">
                            <div style="background:rgba(0,0,0,0.4);border:1px solid rgba(255,255,255,0.08);border-radius:12px;padding:0.7rem 0.5rem;text-align:center;">
                                <div style="font-size:0.7rem;color:#94a3b8;text-transform:uppercase;font-weight:700;letter-spacing:0.04em;margin-bottom:0.25rem;">Cihaz MAC</div>
                                <div style="font-size:0.92rem;font-weight:800;color:#38bdf8;font-family:'JetBrains Mono',monospace;word-break:break-all;">${escapeHtml(errData.mac || deviceMac)}</div>
                            </div>
                            <div style="background:rgba(0,0,0,0.4);border:1px solid rgba(255,255,255,0.08);border-radius:12px;padding:0.7rem 0.5rem;text-align:center;">
                                <div style="font-size:0.7rem;color:#94a3b8;text-transform:uppercase;font-weight:700;letter-spacing:0.04em;margin-bottom:0.25rem;">Yerel IP</div>
                                <div style="font-size:0.92rem;font-weight:800;color:#f8fafc;font-family:'JetBrains Mono',monospace;word-break:break-all;">${escapeHtml(errData.local_ip || localIp || '-')}</div>
                            </div>
                            <div style="background:rgba(0,0,0,0.4);border:1px solid rgba(255,255,255,0.08);border-radius:12px;padding:0.7rem 0.5rem;text-align:center;">
                                <div style="font-size:0.7rem;color:#94a3b8;text-transform:uppercase;font-weight:700;letter-spacing:0.04em;margin-bottom:0.25rem;">Dış Ağ IP</div>
                                <div style="font-size:0.92rem;font-weight:800;color:#cbd5e1;font-family:'JetBrains Mono',monospace;word-break:break-all;">${escapeHtml(errData.ip || '-')}</div>
                            </div>
                        </div>

                        <div style="display:flex;align-items:center;justify-content:center;gap:0.75rem;flex-wrap:wrap;">
                            <div style="display:inline-flex;align-items:center;gap:0.5rem;background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.35);color:#fbbf24;padding:0.45rem 1rem;border-radius:9999px;font-size:0.82rem;font-weight:700;">
                                <span class="pulse-indicator" style="background:#f59e0b;width:7px;height:7px;"></span>
                                Panelden onay bekleniyor... (Oto: 3sn)
                            </div>
                            <button onclick="window.location.reload(true);" style="background:rgba(56,189,248,0.15);border:1px solid rgba(56,189,248,0.4);color:#38bdf8;padding:0.45rem 1rem;border-radius:9999px;font-size:0.82rem;font-weight:700;cursor:pointer;display:inline-flex;align-items:center;gap:0.35rem;">
                                🔄 Şimdi Kontrol Et
                            </button>
                        </div>
                    </div>
                `;
                // Yönetici panelden onayladığı an ekran otomatik açılsın diye 3 saniyede bir yeniden sorgula
                setTimeout(kioskVerileriniGetir, 3000);
                return;
            }

            // Cihaz Limiti Aşımı Durumu
            if (errData.reason === 'device_limit_exceeded') {
                document.body.innerHTML = `
                    <div style="background:radial-gradient(circle at center, #0f172a 0%, #080a10 100%);color:#fff;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:1.5rem;text-align:center;font-family:Inter,sans-serif;box-sizing:border-box;">
                        <div style="background:rgba(18,24,38,0.96);border:1.5px solid rgba(245,158,11,0.4);border-radius:24px;padding:2.2rem 2.8rem;max-width:640px;width:100%;box-shadow:0 25px 60px rgba(0,0,0,0.65), 0 0 35px rgba(245,158,11,0.15);box-sizing:border-box;">
                            <div style="width:58px;height:58px;background:rgba(245,158,11,0.12);border:1px solid rgba(245,158,11,0.3);border-radius:16px;display:inline-flex;align-items:center;justify-content:center;font-size:2rem;margin-bottom:1rem;">📺</div>
                            <h1 style="font-size:1.45rem;font-weight:800;margin-bottom:0.4rem;color:#fbbf24;">Cihaz Limiti Dolu</h1>
                            <p style="font-size:0.92rem;color:#94a3b8;line-height:1.5;margin-bottom:1.4rem;">
                                Bu lisans anahtarı için tanımlı maksimum TV ekranı sınırına (${errData.max_devices || 1} Cihaz) ulaşılmıştır.
                            </p>
                            <div style="background:rgba(0,0,0,0.4);border:1px solid rgba(255,255,255,0.08);padding:0.75rem 1.4rem;border-radius:12px;display:inline-block;font-family:'JetBrains Mono',monospace;color:#38bdf8;font-size:1rem;font-weight:700;margin-bottom:1.4rem;">
                                Lisans: ${escapeHtml(LISANS_KEY)} (${errData.max_devices}/${errData.max_devices} TV Dolu)
                            </div>
                            <p style="font-size:0.82rem;color:#64748b;line-height:1.5;margin:0;">
                                Yeni bir TV veya kiosk ekranı bağlamak için Yönetim Panelinden cihaz limitini artırabilir veya eski bir TV ekranının kilidini kaldırabilirsiniz.
                            </p>
                        </div>
                    </div>
                `;
                return;
            }

            if (errData.reason === 'device_mismatch') {
                document.body.innerHTML = `
                    <div style="background:radial-gradient(circle at center, #0f172a 0%, #080a10 100%);color:#fff;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:1.5rem;text-align:center;font-family:Inter,sans-serif;box-sizing:border-box;">
                        <div style="background:rgba(18,24,38,0.96);border:1.5px solid rgba(239,68,68,0.4);border-radius:24px;padding:2.2rem 2.8rem;max-width:640px;width:100%;box-shadow:0 25px 60px rgba(0,0,0,0.65), 0 0 35px rgba(239,68,68,0.15);box-sizing:border-box;">
                            <div style="width:58px;height:58px;background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.3);border-radius:16px;display:inline-flex;align-items:center;justify-content:center;font-size:2rem;margin-bottom:1rem;">🔒</div>
                            <h1 style="font-size:1.45rem;font-weight:800;margin-bottom:0.4rem;color:#f87171;">Cihaz Kilidi Engeli</h1>
                            <p style="font-size:0.92rem;color:#94a3b8;line-height:1.5;margin-bottom:1.4rem;">
                                Bu lisans anahtarı başka bir TV ekranına kilitlenmiştir.
                            </p>
                            <div style="background:rgba(0,0,0,0.4);border:1px solid rgba(255,255,255,0.08);padding:0.75rem 1.4rem;border-radius:12px;display:inline-block;font-family:'JetBrains Mono',monospace;color:#fbbf24;font-size:1rem;font-weight:700;margin-bottom:1.4rem;">
                                Lisans: ${escapeHtml(LISANS_KEY)}
                            </div>
                            <p style="font-size:0.82rem;color:#64748b;line-height:1.5;margin:0;">
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
            // Eğer cihaz daha önce onay bekleme durumundaysa veya onay overlay'i açıksa:
            // Sayfayı temiz bir şekilde yeniden yükle (böylece harita ve tüm vitrin sıfırdan sorunsuz başlar!)
            const beklemeOverlay = document.getElementById('device-pending-approval-overlay');
            if (window._cihazOnayBekliyor || beklemeOverlay || !document.getElementById('pharmacy-brand-name')) {
                console.log('[KİOSK] Cihaz lisansı onaylandı! Sayfa otomatik yenilenerek vitrin açılıyor...');
                window.location.reload(true);
                return;
            }

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
        const ua = (navigator.userAgent || '').toLowerCase();
        const isTvDevice = /android|smart-tv|smarttv|googletv|appletv|tizen|webos|crkey|aft/i.test(ua);

        // TV cihazlarında veya dikey alanı kısıtlı ekranlarda kompakt TV modu
        if (isTvDevice && vh < 750) {
            document.body.classList.add('scale-compact');
        } else if (vh < 620) {
            document.body.classList.add('scale-compact');
        } else if (vh < 850) {
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

/**
 * 12. Cihaz Tanımlama & Ekranda Göster Sinyali (Identify Overlay)
 */
function cihazTanimlamaGoster(identifyData) {
    let overlay = document.getElementById('device-identify-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'device-identify-overlay';
        overlay.className = 'device-identify-overlay';
        document.body.appendChild(overlay);
    }

    const scaleLabels = {
        'auto': 'Otomatik TV Algılama',
        'compact': 'Mi Box / Kompakt (%80)',
        '720p': 'HD TV 720p (%85)',
        '1080p': 'Full HD 1080p (%100)',
        '4k': '4K Vitrin Ekranı (%130)'
    };
    const scaleMetin = scaleLabels[identifyData.screen_scale] || identifyData.screen_scale || 'Otomatik';

    overlay.innerHTML = `
        <div class="device-identify-box animate-pulse-glow">
            <div class="identify-radar-icon">📡</div>
            <div class="identify-header-tag">CİHAZ TANIMLAMA SİNYALİ</div>
            <h1 class="identify-device-title">${escapeHtml(identifyData.device_name || 'TV EKRANI')}</h1>
            <div class="identify-details-row">
                <span class="badge" style="background: rgba(56,189,248,0.25); color: #38bdf8; font-size: 1.1rem; padding: 0.5rem 1.1rem; border: 1px solid rgba(56,189,248,0.5);">
                    📺 Ölçek: ${scaleMetin}
                </span>
                <span class="badge" style="background: rgba(255,255,255,0.12); color: #f1f5f9; font-size: 1rem; padding: 0.5rem 1.1rem; border: 1px solid rgba(255,255,255,0.2);">
                    🔑 Cihaz No: #${identifyData.device_id || '1'}
                </span>
            </div>
            <div class="identify-device-token">
                Cihaz Kodu: <code>${escapeHtml(identifyData.code || '---')}</code>
            </div>
            <div class="identify-footer-note">
                ✨ Bu ekran yönetim panelinden başarıyla tanımlandı (25 saniye sonra kapanacak).
            </div>
        </div>
    `;
    overlay.style.display = 'flex';
}

function cihazTanimlamaGizle() {
    const overlay = document.getElementById('device-identify-overlay');
    if (overlay) {
        overlay.style.display = 'none';
    }
}

document.addEventListener('DOMContentLoaded', kioskBaslat);
