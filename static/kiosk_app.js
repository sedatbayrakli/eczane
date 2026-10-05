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

// Güvenlik & Gizlilik: Adres çubuğundaki lisans anahtarını gizle (Tam ekrandan çıkıldığında görünmez)
(function adresCubugundakiLisansiGizle() {
    try {
        if (window.history && window.history.replaceState) {
            const url = new URL(window.location.href);
            const isPreview = url.searchParams.get('preview') === '1' || (typeof PREVIEW_MODE !== 'undefined' && PREVIEW_MODE);
            if (url.searchParams.has('key')) {
                const lisansKey = url.searchParams.get('key');
                if (lisansKey && !isPreview) {
                    try {
                        localStorage.setItem('kiosk_license_key', lisansKey);
                    } catch(e) {}
                }
                url.searchParams.delete('key');
                const temizUrl = url.pathname + (url.search ? url.search : '') + url.hash;
                window.history.replaceState({}, document.title, temizUrl);
            }
        }
    } catch (err) {
        console.warn('URL lisans gizleme hatası:', err);
    }
})();

const KIOSK_AYARLAR = {
    POLLING_ARALIGI_MS: 15 * 60 * 1000, // 15 dakikada bir veri tazeleme
    HEARTBEAT_ARALIGI_MS: 15 * 1000,    // 15 saniyede bir hafif canlılık ve komut dinleme sinyali
    HATA_TEKRAR_DENE_MS: 60 * 1000,     // Ağ kesintisinde 60 saniyede bir tekrar deneme
    SAAT_ARALIGI_MS: 1000,              // Saniyede bir saat güncelleme
    GECE_RELOAD_SAATI: 5,               // Her gece 05:00'te bellek temizliği için yenileme
    SLAYT_SURESI_MS: 15000,             // Nöbetçi kart ve harita döngü süresi (15 saniye)
    ANTI_BURN_IN: true,                 // TV / OLED Ekran Yanık Koruması (Piksel Kaydırma)
    TICKER_SPEED_PX: 55                 // Yaşlı vatandaşlar için ideal kayan yazı hızı (50-60 px/sn)
};

// Global Durum Değişkenleri
let kioskMap = null;
let mapMarkersGroup = null;
let routeLineGroup = null;
let screenWakeLock = null;
let sonGeceReloadGunu = -1;

// Önizleme veya cihaz parametrelerine göre başlangıç temasını belirle
const _urlParamsInit = (typeof window !== 'undefined' && window.location) ? new URLSearchParams(window.location.search) : null;
const _previewThemeInit = _urlParamsInit ? (_urlParamsInit.get('preview_theme') || '') : '';
let aktifTema = _previewThemeInit || ((typeof PREVIEW_THEME !== 'undefined' && PREVIEW_THEME) ? PREVIEW_THEME : ((typeof BASLANGIC_TEMASI !== 'undefined' && BASLANGIC_TEMASI) ? BASLANGIC_TEMASI : 'classic_grid'));
let sonGecerliVeri = null;

// Sayfa açılır açılmaz anında URL veya şablon parametrelerine göre zoom & safe area uygula (FOUC önleme)
try {
    ekranOlceginiUygula(
        (typeof CIHAZ_SCALE !== 'undefined' && CIHAZ_SCALE) ? CIHAZ_SCALE : 'auto',
        (typeof CIHAZ_SAFE_MARGIN !== 'undefined' && CIHAZ_SAFE_MARGIN) ? CIHAZ_SAFE_MARGIN : 0
    );
} catch (e) {
    console.warn('[Ölçekleme] İlk başlatma hatası:', e);
}
let guncelEczaneler = [];
let guncelKendiEczane = null;
let aktifEczaneIndex = 0;
let slaytIndex = 0;
let dualSayfaIndex = 0;
let classicSayfaIndex = 0;
let listSayfaIndex = 0;
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
 *   1 -> classic_grid (Klasik 4'lü Izgara & Harita)
 *   2 -> list_view (Liste Görünümü - Haritasız)
 *   3 -> focus_carousel (Vitrin Carousel & Rota)
 *   4 -> dual_card (İkili Dev Kart & Harita)
 */
function aktifGorunumuBelirle() {
    if (aktifTema === 'auto_rotate') {
        const arMinutes = (window._kioskThemeSettings && window._kioskThemeSettings.auto_rotate_minutes) ? window._kioskThemeSettings.auto_rotate_minutes : 60;
        const totalPeriods = Math.floor(Date.now() / (arMinutes * 60 * 1000));
        const temalar = ['classic_grid', 'list_view', 'focus_carousel', 'dual_card'];
        return temalar[totalPeriods % temalar.length];
    }
    // Geriye dönük uyumluluk: animated_route doğrudan seçilmişse focus_carousel motorunu kullanır
    if (aktifTema === 'animated_route') {
        return 'focus_carousel';
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
        // PAZAR VEYA RESMİ TATİL GÜNÜ: 24 Saat kesintisiz nöbet
        baslangicBitis = '09:00 — 09:00';
        baslikMetni = 'PAZAR NÖBETİ (24 SAAT)';

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
            baslangicBitis = dunTatil ? '09:00 — 09:00' : '19:00 — 09:00';
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

/**
 * Pikselleri Koruma Modu (Anti-Burn-In Kontrolü):
 * 7/24 kesintisiz çalışan Kiosk ve TV monitörlerinde statik nesnelerin (saat, çerçeveler)
 * panelde yanık (burn-in / ghosting) oluşturmasını engellemek için her 60 saniyede bir
 * insan gözünün hissetmeyeceği mikro (1-2px) yörüngesel piksel kaydırması uygular.
 */
let antiBurnInTimer = null;
let antiBurnInAdim = 0;
const PIXEL_SHIFT_YORUNGE = [
    { x: 0, y: 0 },
    { x: 1, y: 0 },
    { x: 2, y: 1 },
    { x: 1, y: 2 },
    { x: 0, y: 1 },
    { x: -1, y: 0 },
    { x: -2, y: -1 },
    { x: -1, y: -2 }
];

function antiBurnInModunuUygula(aktif = true) {
    if (antiBurnInTimer) {
        clearInterval(antiBurnInTimer);
        antiBurnInTimer = null;
    }

    const grid = document.querySelector('.pharmacy-grid-container');
    const header = document.querySelector('.top-header');
    if (!grid) return;

    if (!aktif) {
        grid.style.transform = 'none';
        if (header) header.style.transform = 'none';
        return;
    }

    grid.style.transition = 'transform 1.2s cubic-bezier(0.4, 0, 0.2, 1)';
    if (header) header.style.transition = 'transform 1.2s cubic-bezier(0.4, 0, 0.2, 1)';

    antiBurnInTimer = setInterval(() => {
        antiBurnInAdim = (antiBurnInAdim + 1) % PIXEL_SHIFT_YORUNGE.length;
        const offset = PIXEL_SHIFT_YORUNGE[antiBurnInAdim];
        grid.style.transform = `translate3d(${offset.x}px, ${offset.y}px, 0)`;
        if (header) header.style.transform = `translate3d(${offset.x}px, 0, 0)`;
    }, 60000);
}

/**
 * Yaşlı Vatandaşlar İçin İdeal Kayan Yazı Hızı (50-60 px/sn):
 * Duyuru metninin uzunluğuna ve ekran genişliğine göre saniyede 55 piksel hızda akıcı animasyon süresi belirler.
 */
function tickerHiziniVePozisyonunuAyarla(hizPxSaniye = 55) {
    if (!elTickerText) return;
    const wrapper = elTickerText.parentElement;
    if (!wrapper) return;

    const wrapperW = wrapper.offsetWidth || window.innerWidth || 1200;
    const textW = elTickerText.offsetWidth || elTickerText.scrollWidth || 800;

    // Hız: 50-60 px/sn ideal, varsayılan 55 px/sn
    const hiz = Math.max(35, Math.min(100, Number(hizPxSaniye) || 55));
    const toplamMesafe = wrapperW + textW;
    const gerekenSure = Math.max(15, Math.round(toplamMesafe / hiz));

    elTickerText.style.animationDuration = `${gerekenSure}s`;
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
            <!-- 1. Kutu: Nöbet Saatleri (Üstte Başlık, Altta Saat) -->
            <div class="duty-framed-box compact-frame">
                <span class="compact-duty-title">${zaman.baslikMetni}</span>
                <strong class="compact-duty-hours">${zaman.baslangicBitis}</strong>
            </div>
            <!-- 2. Kutu: Nöbet Bitimine Geri Sayım (Üstte Nöbet Bitimine İfadesi, Altta Kalan Süre) -->
            <div class="duty-framed-box compact-frame duty-live-countdown compact-countdown">
                <span class="countdown-label">${zaman.etiket}</span>
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
                                const objUrl = URL.createObjectURL(blob);
                                tile.onload = () => {
                                    URL.revokeObjectURL(objUrl);
                                    done(null, tile);
                                };
                                tile.onerror = () => {
                                    URL.revokeObjectURL(objUrl);
                                    done(null, tile);
                                };
                                tile.src = objUrl;
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
                    const objUrl = URL.createObjectURL(blob);
                    tile.onload = () => {
                        URL.revokeObjectURL(objUrl);
                        done(null, tile);
                    };
                    tile.onerror = () => {
                        URL.revokeObjectURL(objUrl);
                        done(null, tile);
                    };
                    tile.src = objUrl;
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
 * Eczane İsmini Türkçeye Uygun Baş Harfleri Büyük, Diğerleri Küçük (Title Case) Formatına Dönüştürür.
 * Örnek: "İLAYDA ECZANESİ" -> "İlayda Eczanesi"
 * Örnek: "ÇINAR ECZANESİ" -> "Çınar Eczanesi"
 * Örnek: "ŞİFA ECZANESİ" -> "Şifa Eczanesi"
 */
function turkceIsimStandartlastir(metin) {
    if (!metin || typeof metin !== 'string') return '';
    const temiz = metin.trim().replace(/\s+/g, ' ');
    return temiz.toLocaleLowerCase('tr-TR').split(' ').map(kelime => {
        if (!kelime) return '';
        const match = kelime.match(/[a-zA-ZçÇğĞıİöÖşŞüÜ]/);
        if (!match) return kelime;
        const idx = match.index;
        const onEk = kelime.slice(0, idx);
        const ilkHarf = match[0].toLocaleUpperCase('tr-TR');
        const kalan = kelime.slice(idx + 1);
        return onEk + ilkHarf + kalan;
    }).join(' ');
}

/**
 * Haritadaki Pinleri Çizer
 * - Kendi eczanemiz (Buradasınız) sabit kalır.
 * - Numaralar ve sabit çoklu isim kutuları kaldırılmıştır.
 * - Sadece o an aktif olan eczanenin pini büyür ve üzerinde temiz mesafe bilgisi (örn: "597 mt") görünür.
 * - Diğer nöbetçiler sade kırmızı pin olarak kalır.
 */
function haritaPinleriniCiz(kendiEczane, nobetciler, seciliIndex = null) {
    haritayiIlkKezOlustur();
    if (!kioskMap || !mapMarkersGroup) return [];

    mapMarkersGroup.clearLayers();
    const koordinatNoktalari = [];

    // Kendi Eczanemiz Pini (Sabit Konum: Buradasınız)
    if (kendiEczane && kendiEczane.latitude && kendiEczane.longitude) {
        const kendiIsimFormatli = turkceIsimStandartlastir(kendiEczane.name);
        const originIcon = L.divIcon({
            className: 'custom-leaflet-marker',
            html: `<div class="origin-marker-pin" title="Buradasınız: ${escapeHtml(kendiIsimFormatli)}">⭐</div>`,
            iconSize: [32, 32],
            iconAnchor: [16, 16]
        });

        const originMarker = L.marker([kendiEczane.latitude, kendiEczane.longitude], { icon: originIcon })
            .bindPopup(`<b>⭐ ${escapeHtml(kendiIsimFormatli)}</b><br><small>Buradasınız</small>`)
            .bindTooltip(`⭐ Buradasınız`, {
                permanent: true,
                direction: 'top',
                className: 'kiosk-map-tooltip origin-tooltip',
                offset: [0, -22]
            });
        mapMarkersGroup.addLayer(originMarker);
        koordinatNoktalari.push([kendiEczane.latitude, kendiEczane.longitude]);
    }

    // Nöbetçi Eczaneler Pinleri
    if (Array.isArray(nobetciler)) {
        nobetciler.forEach((e, idx) => {
            if (e.enlem && e.boylam) {
                const isSelected = (seciliIndex !== null && idx === seciliIndex);
                const isTarget = isSelected || (seciliIndex === null && idx === 0);
                const eczaneIsimFormatli = turkceIsimStandartlastir(e.isim);

                // Sade kırmızı pin (Aktif olan büyür ve parlar; numaralar kaldırıldı)
                const dutyIcon = L.divIcon({
                    className: 'custom-leaflet-marker',
                    html: `<div class="pulse-ring-pin ${isTarget ? 'active-focused-pin' : ''}" title="${escapeHtml(eczaneIsimFormatli)}"></div>`,
                    iconSize: isTarget ? [38, 38] : [24, 24],
                    iconAnchor: isTarget ? [19, 19] : [12, 12]
                });

                const popupHtml = `
                    <div style="font-family:Inter,sans-serif; color:#0f172a; font-size:12px;">
                        <strong style="color:#b91c1c; font-size:13px;">${escapeHtml(eczaneIsimFormatli)}</strong><br>
                        <span>${escapeHtml(e.adres || '')}</span><br>
                        <b style="color:#0284c7;">${escapeHtml(formatMesafeMetin(e.mesafe_metin || ''))}</b>
                    </div>
                `;

                const marker = L.marker([e.enlem, e.boylam], { icon: dutyIcon }).bindPopup(popupHtml);

                // Sadece aktif olan eczanenin üzerinde temiz mesafe bilgisi etiketi gösterilir (diğerleri sade pindir)
                if (isTarget) {
                    const mesafeYazisi = formatMesafeMetin(e.mesafe_metin || '');
                    if (mesafeYazisi) {
                        marker.bindTooltip(escapeHtml(mesafeYazisi), {
                            permanent: true,
                            direction: 'top',
                            className: 'kiosk-map-tooltip target-distance-tooltip',
                            offset: [0, -22]
                        });
                    }
                }

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
 * Yol Tarifi / Önemli Nokta (Landmark) Metnini Standartlaştırır:
 * - Tamamı büyük harf gelen verileri Türkçeye uygun Baş Harfleri Büyük (Title Case) formatına küçültür
 * - Gereksiz boşlukları temizler
 */
function turkceTarifStandartlastir(metin) {
    if (!metin || typeof metin !== 'string') return '';
    let temiz = metin.trim().replace(/\s+/g, ' ');
    const buyukSayisi = (temiz.match(/[A-ZÇĞİÖŞÜ]/g) || []).length;
    const harfSayisi = (temiz.match(/[a-zA-ZçÇğĞıİöÖşŞüÜ]/g) || []).length;
    if (harfSayisi > 0 && (buyukSayisi / harfSayisi) > 0.5) {
        temiz = temiz.toLocaleLowerCase('tr-TR').split(' ').map(kelime => {
            if (!kelime) return '';
            const m = kelime.match(/[a-zA-ZçÇğĞıİöÖşŞüÜ]/);
            if (!m) return kelime;
            const idx = m.index;
            return kelime.slice(0, idx) + kelime.charAt(idx).toLocaleUpperCase('tr-TR') + kelime.slice(idx + 1);
        }).join(' ');
    }
    return temiz;
}

/**
 * Yol Tarifi için Kayan Yazı (Marquee Ticker - Duyuru Hızında ~50-55 px/sn) HTML'i Üretir
 */
function yolTarifiBadgeHtmlUret(yolTarifi, ekStil = '') {
    if (!yolTarifi || !yolTarifi.trim()) return '';
    const normalizeMetin = turkceTarifStandartlastir(yolTarifi);
    const escaped = escapeHtml(normalizeMetin);
    
    // 20 karakterden uzunsa kayan yazı moduna geç
    const isLong = normalizeMetin.length > 20;
    // Süre hesabı: Duyuru hızıyla uyumlu (her karakter ~0.24 saniye, minimum 8 saniye)
    const animDuration = Math.max(8, Math.round(normalizeMetin.length * 0.24));

    if (isLong) {
        return `
        <div class="card-landmark-box has-marquee" style="${ekStil}" title="${escaped}">
            <span class="card-landmark-icon">📍</span>
            <div class="landmark-ticker-container">
                <span class="landmark-ticker-text is-marquee" style="animation-duration: ${animDuration}s;">
                    ${escaped}
                </span>
            </div>
        </div>`;
    } else {
        return `
        <div class="card-landmark-box" style="${ekStil}" title="${escaped}">
            <span class="card-landmark-icon">📍</span>
            <span class="landmark-ticker-text">${escaped}</span>
        </div>`;
    }
}

/**
 * 6. ŞABLONLAR: Klasik Kart, Dev Odak Kartı ve İkili Kart
 */

// Şablon A: Klasik Izgara Kartı (Tema 1)
function eczaneKartiHtmlUret(eczane, index) {
    const semtHtml = eczane.semt 
        ? `<span class="badge-semt" style="font-size: 0.75rem; padding: 0.15rem 0.45rem; border-radius: 4px;">${escapeHtml(eczane.semt)}</span>` 
        : '';

    const mesafeHtml = eczane.mesafe_metin 
        ? `<span class="badge-distance" style="font-size: 0.82rem; font-weight: 800; background: rgba(56, 189, 248, 0.14); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.35); padding: 0.15rem 0.5rem; border-radius: 9999px; display: inline-flex; align-items: center; gap: 0.25rem;">📍 ${escapeHtml(formatMesafeMetin(eczane.mesafe_metin))}</span>` 
        : '';

    const yolTarifiHtml = yolTarifiBadgeHtmlUret(eczane.yol_tarifi);

    const qrKodUrl = eczane.qr_kod_url || `https://api.qrserver.com/v1/create-qr-code/?size=160x160&data=${encodeURIComponent(eczane.rota_linki || eczane.harita_linki)}`;

    return `
    <article class="pharmacy-card classic-grid-card" data-id="${eczane.id}" data-index="${index}">
        <div class="card-left-info">
            <div class="card-top-row" style="margin-bottom: 0.15rem;">
                <div style="display: flex; align-items: center; justify-content: space-between; width: 100%; gap: 0.3rem;">
                    ${mesafeHtml}
                    ${semtHtml}
                </div>
            </div>

            <!-- Eczane İsmi ve Adres -->
            <div class="card-address-block">
                <h2 class="pharmacy-name-title" style="margin: 0.1rem 0; font-size: clamp(1.05rem, 1.25vw, 1.22rem); font-weight: 800; color: #ffffff; line-height: 1.18; white-space: normal; display: flex; align-items: center; gap: 0.45rem;" title="${escapeHtml(eczane.isim)}">
                    <span style="display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">${escapeHtml(eczane.isim)}</span>
                </h2>
                <p class="card-address-text" style="font-size: 0.78rem; line-height: 1.24; margin: 0; color: #cbd5e1; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">
                    ${escapeHtml(eczane.adres)}
                </p>
                ${yolTarifiHtml}
            </div>

            <div class="card-phone-row" style="display: flex; align-items: center; gap: 0.45rem; margin-top: 0.2rem; white-space: nowrap;">
                <div class="phone-icon-box" style="width: 22px; height: 22px; min-width: 22px; padding: 2px;">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                        <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"></path>
                    </svg>
                </div>
                <span class="phone-number-display" style="font-size: 0.95rem; font-weight: 800; color: #ffffff; white-space: nowrap; font-family: monospace;">
                    ${escapeHtml(eczane.telefon || 'Belirtilmedi')}
                </span>
            </div>
        </div>

        <div class="card-right-qr classic-qr-box">
            <div class="qr-image-wrapper classic-qr-img-wrap">
                <img class="qr-image" 
                     src="${qrKodUrl}" 
                     alt="${escapeHtml(eczane.isim)} Rota QR Kodu"
                     onerror="window.yerelQrKodFallback(this, '${escapeHtml(eczane.rota_linki || eczane.harita_linki || '')}');"
                     loading="eager" />
            </div>
            <div class="qr-caption" style="margin-top: 0.2rem;">
                <span style="font-size: 0.62rem; color: #94a3b8; display: block; line-height: 1.1;">Adres İçin</span>
                <strong style="font-size: 0.72rem; color: #38bdf8; font-weight: 800; letter-spacing: 0.04em;">OKUTUN</strong>
            </div>
        </div>
    </article>
    `;
}

// Şablon B: Dev Odak Kartı (Tema 2 & Tema 3 İçin - Asla Taşmaz)
function devOdakKartiHtmlUret(eczane, siraNo, toplamAdet, modAdi = 'NAVİGASYON') {
    const qrKodUrl = eczane.qr_kod_url || `https://api.qrserver.com/v1/create-qr-code/?size=250x250&data=${encodeURIComponent(eczane.rota_linki || eczane.harita_linki)}`;

    const arabaMetin = eczane.araba_metin || (eczane.mesafe_metre ? `~${Math.max(1, Math.round(eczane.mesafe_metre / 500))} dk` : '');
    const yurumeMetin = eczane.yurume_metin || (eczane.mesafe_metre ? `~${Math.max(1, Math.round(eczane.mesafe_metre / 75))} dk` : '');

    let paginationHtml = '';
    if (toplamAdet > 1) {
        const pills = Array.from({ length: toplamAdet }).map((_, pIdx) => `
            <span class="dual-page-pill ${pIdx === siraNo ? 'active' : ''}" onclick="manuelSlaytaGit(${pIdx})" title="${pIdx + 1}. Nöbetçi"></span>
        `).join('');

        paginationHtml = `
            <div class="dual-pagination-bar" style="margin-top: 0.5rem;">
                <span class="dual-page-badge">📄 Nöbetçi Eczane: ${siraNo + 1} / ${toplamAdet}</span>
                <div class="dual-pills-row">${pills}</div>
            </div>
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
                    </div>` : ''}

                <div class="focus-qr-frame">
                    <img class="focus-qr-image" 
                         src="${qrKodUrl}" 
                         alt="${escapeHtml(eczane.isim)} Harita QR"
                         onerror="window.yerelQrKodFallback(this, '${escapeHtml(eczane.rota_linki || eczane.harita_linki || '')}');"
                         loading="eager" />
                </div>
                <div class="focus-qr-text">
                    <div class="qr-camera-icon">
                        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path>
                            <circle cx="12" cy="13" r="4"></circle>
                        </svg>
                    </div>
                    <span class="qr-text-top">Adres Tarifi İçin</span>
                    <strong class="qr-text-bottom">OKUTUNUZ</strong>
                </div>
            </div>

            <!-- Sağ Sütun: Eczane İsmi & Adres Tek Kutuda + Telefon -->
            <div class="focus-details-col">
                <div class="focus-address-card">
                    <div class="focus-pharmacy-name-row" style="display: flex; align-items: center; justify-content: space-between; gap: 0.6rem; border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 0.45rem; margin-bottom: 0.45rem; flex-wrap: wrap;">
                        <h2 class="focus-title" style="margin: 0; font-size: 1.55rem; color: #fff; font-weight: 800; display: flex; align-items: center; gap: 0.55rem;">
                            <span>${escapeHtml(eczane.isim)}</span>
                        </h2>
                        ${eczane.semt ? `<span class="badge-semt" style="font-size: 0.88rem;">${escapeHtml(eczane.semt)}</span>` : ''}
                    </div>
                    <p class="focus-address-text">${escapeHtml(eczane.adres)}</p>
                </div>

                <div class="focus-phone-card">
                    <div class="phone-icon-box" style="width: 30px; height: 30px; min-width: 30px;">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
                            <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"></path>
                        </svg>
                    </div>
                    <div class="focus-phone-number">${escapeHtml(eczane.telefon || 'Belirtilmedi')}</div>
                </div>

                ${nobetBilgisiHtmlUret('focus')}
            </div>
        </div>

        ${paginationHtml}
    </div>
    `;
}

// Şablon C: İkili Dev Kart (Tema 4 - Sadece 2 Eczane Odaklı, Sıfır Taşma)
function ikiliEczaneKartiHtmlUret(eczane, siraNo) {
    const qrKodUrl = eczane.qr_kod_url || `https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=${encodeURIComponent(eczane.rota_linki || eczane.harita_linki)}`;

    const arabaMetin = eczane.araba_metin || (eczane.mesafe_metre ? `~${Math.max(1, Math.round(eczane.mesafe_metre / 500))} dk` : '');
    const yurumeMetin = eczane.yurume_metin || (eczane.mesafe_metre ? `~${Math.max(1, Math.round(eczane.mesafe_metre / 75))} dk` : '');

    const yolTarifiHtml = yolTarifiBadgeHtmlUret(eczane.yol_tarifi, 'margin-top: 0.15rem;');

    return `
    <article class="dual-pharmacy-card animate-fade-in" data-id="${eczane.id}">
        <div class="dual-card-left">
            <!-- Eczane İsmi, Yanında Mesafe ve Açık Adres -->
            <div class="dual-card-address-block">
                <div style="display: flex; align-items: center; justify-content: space-between; gap: 0.6rem; margin-bottom: 0.35rem; flex-wrap: wrap;">
                    <div style="display: flex; align-items: center; gap: 0.65rem; min-width: 0; flex-wrap: wrap;">
                        <h2 class="dual-pharmacy-title" style="margin: 0; display: flex; align-items: center; gap: 0.5rem;">
                            <span>${escapeHtml(eczane.isim)}</span>
                        </h2>
                        ${eczane.mesafe_metin ? `
                            <span class="badge-distance" style="font-size: 0.95rem; font-weight: 800; padding: 0.25rem 0.65rem; white-space: nowrap;">
                                📍 ${escapeHtml(formatMesafeMetin(eczane.mesafe_metin))}
                            </span>` : ''}
                    </div>
                    ${eczane.semt ? `<span class="badge-semt" style="font-size: 0.82rem; padding: 0.25rem 0.55rem;">${escapeHtml(eczane.semt)}</span>` : ''}
                </div>
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

            ${nobetBilgisiHtmlUret('compact')}
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
                <div class="qr-camera-icon-sm">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path>
                        <circle cx="12" cy="13" r="4"></circle>
                    </svg>
                </div>
                <span class="qr-text-top">Adres Tarifi İçin</span>
                <strong class="qr-text-bottom">OKUTUNUZ</strong>
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
    const ts = window._kioskThemeSettings || {};
    // Rota gösterimi: parametreye göre (show_route !== false) veya eski animated_route temasına göre belirlenir
    const showRoute = (aktifTema === 'animated_route') ? true : (ts.show_route !== false);

    if (showRoute) {
        // Tema 3 (Rotası Açık): Canlı Yol & Navigasyon Rota
        const rotaPrefix = isOfflineModAktif ? '💾 ÇEVRİMDIŞI HARİTA & KONUM' : 'CANLI HARİTA & YOL TARİFİ';
        if (elMapPanelTitle) elMapPanelTitle.textContent = `${rotaPrefix} (${turkceIsimStandartlastir(seciliEczane.isim)})`;
        elPharmacyGrid.innerHTML = devOdakKartiHtmlUret(seciliEczane, slaytIndex, guncelEczaneler.length, 'YOL TARİFİ');
        
        haritaPinleriniCiz(guncelKendiEczane, guncelEczaneler, slaytIndex);
        haritadaRotaGoster(guncelKendiEczane, seciliEczane);

    } else {
        // Tema 3 (Rotasız Temiz Vitrin): Vitrin Carousel & Dev Odak Kartı
        if (elMapPanelTitle) elMapPanelTitle.textContent = `BÖLGE NÖBETÇİ HARİTASI (${turkceIsimStandartlastir(seciliEczane.isim)})`;
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
 * Tema 4: İkili Dev Kart Sayfa Gösterimi (2'şerli Nöbetçi Gösterimi ve Otomatik Sayfalama)
 */
function dualSayfaGoster() {
    if (!guncelEczaneler || guncelEczaneler.length === 0) return;

    if (routeLineGroup) routeLineGroup.clearLayers();

    const toplamDualSayfa = Math.ceil(guncelEczaneler.length / 2);
    if (dualSayfaIndex >= toplamDualSayfa) {
        dualSayfaIndex = 0;
    }

    const baslangic = dualSayfaIndex * 2;
    const buSayfaEczaneler = guncelEczaneler.slice(baslangic, baslangic + 2);
    const sayfaIciAktifIdx = aktifEczaneIndex - baslangic;

    if (elMapPanelTitle) {
        if (toplamDualSayfa > 1) {
            elMapPanelTitle.textContent = `CANLI HARİTA & YOL TARİFİ (SAYFA ${dualSayfaIndex + 1} / ${toplamDualSayfa})`;
        } else {
            elMapPanelTitle.textContent = `CANLI HARİTA & YOL TARİFİ`;
        }
    }

    let paginationHtml = '';
    if (toplamDualSayfa > 1) {
        const pills = Array.from({ length: toplamDualSayfa }).map((_, pIdx) => `
            <span class="dual-page-pill ${pIdx === dualSayfaIndex ? 'active' : ''}" onclick="manuelDualSayfayaGit(${pIdx})" title="Sayfa ${pIdx + 1}"></span>
        `).join('');

        paginationHtml = `
            <div class="dual-pagination-bar">
                <span class="dual-page-badge">📄 Nöbetçiler: Sayfa ${dualSayfaIndex + 1} / ${toplamDualSayfa}</span>
                <div class="dual-pills-row">${pills}</div>
            </div>
        `;
    }

    let cardsHtml = buSayfaEczaneler.map((e, idx) => {
        const isAktif = (idx === sayfaIciAktifIdx);
        let cardHtml = ikiliEczaneKartiHtmlUret(e, baslangic + idx);
        if (isAktif) {
            cardHtml = cardHtml.replace('class="dual-pharmacy-card animate-fade-in', 'class="dual-pharmacy-card animate-fade-in active-carousel-card');
        }
        cardHtml = cardHtml.replace('data-id="', `onclick="manuelAktifEczaneSec(${baslangic + idx})" style="cursor: pointer;" data-id="`);
        return cardHtml;
    }).join('');

    // Son sayfada tek kart kaldığında mimarinin bozulmaması için boş 2. kare
    if (buSayfaEczaneler.length < 2) {
        cardsHtml += `
            <div class="dual-pharmacy-card dual-card-placeholder" style="visibility: hidden; opacity: 0; pointer-events: none; border: none; background: transparent; box-shadow: none;"></div>
        `;
    }

    elPharmacyGrid.className = 'pharmacy-grid-container';
    elPharmacyGrid.innerHTML = `
        <div class="dual-pharmacy-cards-container">
            ${cardsHtml}
            ${paginationHtml}
        </div>
    `;

    // Harita: Lisanslı eczanemiz + ekrandaki nöbetçiler (SADECE o an aktif olan eczane büyür ve mesafesi gösterilir)
    const noktalar = haritaPinleriniCiz(guncelKendiEczane, buSayfaEczaneler, sayfaIciAktifIdx);
    if (noktalar && noktalar.length > 0 && kioskMap) {
        setTimeout(() => {
            kioskMap.invalidateSize();
            if (!window._kioskMapBoundsDualSet) {
                try {
                    kioskMap.flyToBounds(noktalar, { padding: [45, 45], maxZoom: 16, duration: 0.8 });
                } catch (e) {
                    kioskMap.fitBounds(noktalar, { padding: [45, 45], maxZoom: 16 });
                }
                window._kioskMapBoundsDualSet = true;
            }
        }, 100);
    }

    slaytIlerlemeAnimasyonunuBaslat();
    temaIcerikGorunurlukleriniUygula();
}

function manuelDualSayfayaGit(index) {
    const toplamDualSayfa = Math.ceil(guncelEczaneler.length / 2);
    dualSayfaIndex = index;
    aktifEczaneIndex = index * 2;
    dualSayfaGoster(aktifEczaneIndex);
    dondurVeZamanlayiciyiYenile();
}

/**
 * Tema 1: Klasik Izgara Sayfa Gösterimi (4'erli Nöbetçi Gösterimi ve 10sn Sıralı Döngü)
 * - Her 10 saniyede bir sıradaki eczanenin kartı aktif olur (mavi border + glow, sol kırmızı çizgi korunur)
 * - Haritada o an aktif olan eczanenin pini büyür ve üzerinde sadece karttaki mesafe bilgisi (örn: "597 mt") görünür
 * - 4'ten fazla eczane varsa sayfalar otomatik olarak döner
 */
function classicSayfaGoster(hedefEczaneIndex = null) {
    if (!guncelEczaneler || guncelEczaneler.length === 0) return;

    if (routeLineGroup) routeLineGroup.clearLayers();

    const toplamEczane = guncelEczaneler.length;
    if (hedefEczaneIndex !== null) {
        aktifEczaneIndex = (hedefEczaneIndex % toplamEczane + toplamEczane) % toplamEczane;
    } else if (aktifEczaneIndex >= toplamEczane) {
        aktifEczaneIndex = 0;
    }

    const toplamClassicSayfa = Math.ceil(toplamEczane / 4);
    const yeniSayfaIndex = Math.floor(aktifEczaneIndex / 4);
    const sayfaDegisti = (classicSayfaIndex !== yeniSayfaIndex);
    classicSayfaIndex = yeniSayfaIndex;

    const baslangic = classicSayfaIndex * 4;
    const buSayfaEczaneler = guncelEczaneler.slice(baslangic, baslangic + 4);
    const sayfaIciAktifIdx = aktifEczaneIndex - baslangic;

    // Her eczaneye genel sıralama indeksini ata
    buSayfaEczaneler.forEach((e, i) => {
        e._globalIndex = baslangic + i;
    });

    if (elMapPanelTitle) {
        if (toplamClassicSayfa > 1) {
            elMapPanelTitle.textContent = `CANLI HARİTA & YOL TARİFİ (SAYFA ${classicSayfaIndex + 1} / ${toplamClassicSayfa})`;
        } else {
            elMapPanelTitle.textContent = `CANLI HARİTA & YOL TARİFİ`;
        }
    }

    elPharmacyGrid.className = 'pharmacy-grid-container';

    let paginationHtml = '';
    if (toplamClassicSayfa > 1) {
        const pills = Array.from({ length: toplamClassicSayfa }).map((_, pIdx) => `
            <span class="dual-page-pill ${pIdx === classicSayfaIndex ? 'active' : ''}" onclick="manuelClassicSayfayaGit(${pIdx})" title="Sayfa ${pIdx + 1}"></span>
        `).join('');

        paginationHtml = `
            <div class="dual-pagination-bar" style="grid-column: 1 / -1; margin-top: 0.35rem;">
                <span class="dual-page-badge">📄 Nöbetçiler: Sayfa ${classicSayfaIndex + 1} / ${toplamClassicSayfa} (${baslangic + 1}-${Math.min(baslangic + 4, toplamEczane)} / Toplam ${toplamEczane})</span>
                <div class="dual-pills-row">${pills}</div>
            </div>
        `;
    }

    let cardsHtml = buSayfaEczaneler.map((e, idx) => {
        const isAktif = (idx === sayfaIciAktifIdx);
        let cardHtml = eczaneKartiHtmlUret(e, baslangic + idx);
        if (isAktif) {
            cardHtml = cardHtml.replace('class="pharmacy-card classic-grid-card', 'class="pharmacy-card classic-grid-card active-carousel-card');
        }
        cardHtml = cardHtml.replace('data-id="', `onclick="manuelAktifEczaneSec(${baslangic + idx})" style="cursor: pointer;" data-id="`);
        return cardHtml;
    }).join('');

    // 4'ten az kart olduğunda mimarinin bozulmaması için boş kareler
    const eksikKart = 4 - buSayfaEczaneler.length;
    for (let k = 0; k < eksikKart; k++) {
        cardsHtml += `
            <div class="pharmacy-card classic-card-placeholder" style="visibility: hidden; opacity: 0; pointer-events: none; border: none; background: transparent; box-shadow: none;"></div>
        `;
    }

    elPharmacyGrid.innerHTML = cardsHtml + paginationHtml;

    // Harita: Lisanslı eczanemiz (Buradasınız) + ekrandaki nöbetçiler (SADECE o an aktif olan eczane büyür ve mesafesi görünür)
    const noktalar = haritaPinleriniCiz(guncelKendiEczane, buSayfaEczaneler, sayfaIciAktifIdx);
    if (noktalar && noktalar.length > 0 && kioskMap) {
        setTimeout(() => {
            kioskMap.invalidateSize();
            if (sayfaDegisti || !window._kioskMapBoundsClassicSet) {
                try {
                    kioskMap.flyToBounds(noktalar, { padding: [45, 45], maxZoom: 16, duration: 0.8 });
                } catch (e) {
                    kioskMap.fitBounds(noktalar, { padding: [45, 45], maxZoom: 16 });
                }
                window._kioskMapBoundsClassicSet = true;
            }
        }, 100);
    }

    slaytIlerlemeAnimasyonunuBaslat();
    temaIcerikGorunurlukleriniUygula();
}

function manuelClassicSayfayaGit(index) {
    const toplamClassicSayfa = Math.ceil(guncelEczaneler.length / 4);
    classicSayfaIndex = index;
    aktifEczaneIndex = index * 4;
    classicSayfaGoster(aktifEczaneIndex);
    dondurVeZamanlayiciyiYenile();
}

/**
 * Kullanıcı bir karta tıkladığında veya timer tetiklendiğinde aktif eczaneyi seçme
 */
window.manuelAktifEczaneSec = function(globalIdx) {
    if (!guncelEczaneler || guncelEczaneler.length === 0) return;
    aktifEczaneIndex = globalIdx;
    slaytIndex = globalIdx;

    const gorunum = aktifGorunumuBelirle();
    if (gorunum === 'classic_grid') {
        classicSayfaGoster(aktifEczaneIndex);
    } else if (gorunum === 'dual_card') {
        dualSayfaGoster(aktifEczaneIndex);
    } else if (gorunum === 'animated_route' || gorunum === 'focus_carousel') {
        manuelSlaytaGit(aktifEczaneIndex);
    }

    dondurVeZamanlayiciyiYenile();
};

/**
 * 10 Saniyelik Sıralı Döngüyü Bir Sonraki Eczaneye İlerletir
 */
function siraliDonguyuIlerlet() {
    if (!guncelEczaneler || guncelEczaneler.length === 0) return;
    aktifEczaneIndex = (aktifEczaneIndex + 1) % guncelEczaneler.length;
    slaytIndex = aktifEczaneIndex;

    const gorunum = aktifGorunumuBelirle();
    if (gorunum === 'animated_route' || gorunum === 'focus_carousel') {
        slaytGoster();
    } else if (gorunum === 'dual_card') {
        dualSayfaGoster(aktifEczaneIndex);
    } else if (gorunum === 'list_view') {
        listSayfaIndex = Math.floor(aktifEczaneIndex / 4);
        listSayfaGoster();
    } else {
        // classic_grid
        classicSayfaGoster(aktifEczaneIndex);
    }
}

/**
 * Manuel tıklama veya sayfa değişiminde timer'ı sıfırlar ve 10sn döngüyü yeniden başlatır
 */
function dondurVeZamanlayiciyiYenile() {
    if (slaytTimer) clearInterval(slaytTimer);
    slaytTimer = setInterval(() => {
        siraliDonguyuIlerlet();
    }, KIOSK_AYARLAR.SLAYT_SURESI_MS);
}


/**
 * Tema 2: Liste Görünümü (Haritasız, Tek Satırlık Şerit Kartlar & Sayfalama)
 */
function listeEczaneKartiHtmlUret(e, globalIdx) {
    const mesafeMetin = formatMesafeMetin(e.mesafe_metin || '');
    const telefon = e.telefon ? escapeHtml(e.telefon) : '';
    const adres = escapeHtml(e.adres || '');
    const yolTarifi = e.yol_tarifi ? escapeHtml(e.yol_tarifi) : '';

    const nobetZaman = nobetZamaniniHesapla();
    const nobetSaatAraligi = e.nobet_saatleri || (nobetZaman ? nobetZaman.baslangicBitis : 'Sabaha Kadar');

    // Harita QR Kodu
    let qrHtml = '';
    if (e.harita_linki) {
        const qrUrl = `https://api.qrserver.com/v1/create-qr-code/?size=80x80&data=${encodeURIComponent(e.harita_linki)}&margin=1`;
        qrHtml = `
            <div class="list-card-qr-box" title="Harita ve Yol Tarifi İçin Telefonunuzla Okutun">
                <img src="${qrUrl}" alt="Navigasyon QR" loading="lazy" class="list-card-qr-img">
                <span class="list-card-qr-text">YOL TARİFİ</span>
            </div>
        `;
    }

    return `
        <div class="list-pharmacy-row-card">
            <!-- Sol: Eczane Bilgileri (İsim, Mesafe, Saat, Adres, Alt Satırda Tarif) -->
            <div class="list-card-main-info">
                <div class="list-card-name-group">
                    <!-- 1. Satır: İsim + Mesafe Rozeti + Nöbet Saati Rozeti -->
                    <div class="list-card-title-row">
                        <h2 class="list-card-pharmacy-name">${escapeHtml(e.isim)}</h2>
                        ${mesafeMetin ? `<span class="list-badge-distance">🚶 ${escapeHtml(mesafeMetin)}</span>` : ''}
                        <span class="list-badge-hours">⏰ ${escapeHtml(nobetSaatAraligi)}</span>
                    </div>

                    <!-- 2. Satır: Açık Adres -->
                    <div class="list-card-address-row">
                        <span class="list-card-address">📍 ${adres}</span>
                    </div>

                    <!-- 3. Satır: Eczane Yol Tarifi (Uzun tariflerin sıkışmaması için standart kayan yazı rozeti) -->
                    ${yolTarifi ? `
                        <div class="list-card-landmark-row">
                            ${yolTarifiBadgeHtmlUret(e.yol_tarifi)}
                        </div>
                    ` : ''}
                </div>
            </div>

            <!-- Sağ: İletişim Telefonu ve QR Kod -->
            <div class="list-card-meta-group">
                ${telefon ? `
                    <div class="list-card-phone-pill">
                        <span class="list-phone-icon">📞</span>
                        <span class="list-phone-number">${telefon}</span>
                    </div>
                ` : ''}
                ${qrHtml}
            </div>
        </div>
    `;
}

function listSayfaGoster() {
    if (!guncelEczaneler || guncelEczaneler.length === 0) return;

    if (routeLineGroup) routeLineGroup.clearLayers();

    const toplamEczane = guncelEczaneler.length;
    const LISTE_SAYFA_BOYUTU = 4;
    const toplamListSayfa = Math.ceil(toplamEczane / LISTE_SAYFA_BOYUTU);
    if (listSayfaIndex >= toplamListSayfa) {
        listSayfaIndex = 0;
    }

    const baslangic = listSayfaIndex * LISTE_SAYFA_BOYUTU;
    const buSayfaEczaneler = guncelEczaneler.slice(baslangic, baslangic + LISTE_SAYFA_BOYUTU);

    buSayfaEczaneler.forEach((e, i) => {
        e._globalIndex = baslangic + i;
    });

    let paginationHtml = '';
    if (toplamListSayfa > 1) {
        const pills = Array.from({ length: toplamListSayfa }).map((_, pIdx) => `
            <span class="dual-page-pill ${pIdx === listSayfaIndex ? 'active' : ''}" onclick="manuelListSayfayaGit(${pIdx})" title="Sayfa ${pIdx + 1}"></span>
        `).join('');

        paginationHtml = `
            <div class="dual-pagination-bar" style="margin-top: 0.4rem; justify-content: center;">
                <span class="dual-page-badge">📄 Nöbetçiler: Sayfa ${listSayfaIndex + 1} / ${toplamListSayfa} (${baslangic + 1}-${Math.min(baslangic + LISTE_SAYFA_BOYUTU, toplamEczane)} / Toplam ${toplamEczane})</span>
                <div class="dual-pills-row">${pills}</div>
            </div>
        `;
    }

    let cardsHtml = buSayfaEczaneler.map((e, idx) => listeEczaneKartiHtmlUret(e, baslangic + idx)).join('');
    // 4'ten az kart olduğunda (örneğin son sayfada 1, 2 veya 3 kart varken) kartların dikeyde büyüyüp
    // ölçeğin bozulmaması için eksik satırlara şeffaf boş yer tutucular eklenir:
    const eksikKart = LISTE_SAYFA_BOYUTU - buSayfaEczaneler.length;
    for (let k = 0; k < eksikKart; k++) {
        cardsHtml += `
            <div class="list-pharmacy-row-card list-card-placeholder" style="visibility: hidden; opacity: 0; pointer-events: none; border: none; background: transparent; box-shadow: none;"></div>
        `;
    }

    elPharmacyGrid.className = 'pharmacy-grid-container layout-list-container';
    elPharmacyGrid.innerHTML = `
        <div class="list-pharmacy-rows-wrapper">
            ${cardsHtml}
            ${paginationHtml}
        </div>
    `;

    if (toplamListSayfa > 1) {
        slaytIlerlemeAnimasyonunuBaslat();
    } else {
        if (elProgressBarContainer) elProgressBarContainer.style.display = 'none';
    }

    temaIcerikGorunurlukleriniUygula();
}

function manuelListSayfayaGit(index) {
    listSayfaIndex = index;
    listSayfaGoster();

    const toplamListSayfa = Math.ceil(guncelEczaneler.length / 4);
    if (toplamListSayfa > 1) {
        if (slaytTimer) clearInterval(slaytTimer);
        slaytTimer = setInterval(() => {
            listSayfaIndex = (listSayfaIndex + 1) % toplamListSayfa;
            listSayfaGoster();
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

        // TV / Mi Box Ekran Çözünürlüğü, Dinamik Zoom ve Güvenli Alan Ayarını Uygula
        const tsSettings = veri.pharmacy.theme_settings || {};
        const seciliScale = veri.pharmacy.scale || veri.pharmacy.screen_scale || tsSettings.scale || '1.0';
        const seciliSafeMargin = (tsSettings.safeAreaMargin !== undefined && tsSettings.safeAreaMargin !== null) 
            ? tsSettings.safeAreaMargin 
            : (veri.pharmacy.safeAreaMargin || 0);

        ekranOlceginiUygula(seciliScale, seciliSafeMargin);

        // Kiosk Ekran Teması Parametrik Ayarlarını Uygula
        if (veri.pharmacy.theme_settings) {
            const ts = veri.pharmacy.theme_settings;
            window._kioskThemeSettings = ts;
            if (ts.carousel_interval_sec && ts.carousel_interval_sec > 0) {
                const sn = (ts.carousel_interval_sec === 10) ? 15 : ts.carousel_interval_sec;
                KIOSK_AYARLAR.SLAYT_SURESI_MS = sn * 1000;
            }
            if (ts.auto_rotate_minutes && ts.auto_rotate_minutes > 0) {
                KIOSK_AYARLAR.AUTO_ROTATE_MINUTES = ts.auto_rotate_minutes;
            }
            if (ts.map_zoom) {
                KIOSK_AYARLAR.MAP_ZOOM = ts.map_zoom;
            }
            if (ts.ticker_speed_px) {
                KIOSK_AYARLAR.TICKER_SPEED_PX = ts.ticker_speed_px;
            }
            if (ts.anti_burn_in !== undefined) {
                KIOSK_AYARLAR.ANTI_BURN_IN = ts.anti_burn_in;
            }
            temaIcerikGorunurlukleriniUygula(ts);
        }

        // Yaşlı Vatandaşlar İçin Ticker Hızı (50-60 px/sn) ve Anti-Burn-In Motorunu Başlat
        tickerHiziniVePozisyonunuAyarla(KIOSK_AYARLAR.TICKER_SPEED_PX || 55);
        antiBurnInModunuUygula(KIOSK_AYARLAR.ANTI_BURN_IN !== false);
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
        slaytDongusunuDurdur();

        // SEÇİLEN GÖRÜNÜME GÖRE İLK RENDER:
        if (gorunum === 'animated_route' || gorunum === 'focus_carousel') {
            // Tema 3: Vitrin Carousel & Dev Odak Kartı (veya Canlı Rota)
            slaytGoster();
        } else if (gorunum === 'dual_card') {
            // Tema 4: İkili Dev Kart (2'şerli nöbetçi gösterimi & 10sn sıralı döngü)
            dualSayfaGoster(aktifEczaneIndex);
        } else if (gorunum === 'list_view') {
            // Tema 2: Liste Görünümü (Haritasız Tam Liste & Sayfalama)
            listSayfaGoster();
        } else {
            // Tema 1: classic_grid (Klasik 4'lü Izgara & Harita, 10sn Sıralı Döngü)
            classicSayfaGoster(aktifEczaneIndex);
        }

        // 10 Saniyelik Sıralı Döngü Zamanlayıcısını Başlat (Tüm temalarda 10sn aralıkla sıradaki eczaneyi odaklar)
        if (guncelEczaneler.length > 1) {
            slaytTimer = setInterval(() => {
                siraliDonguyuIlerlet();
            }, KIOSK_AYARLAR.SLAYT_SURESI_MS);
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
    const urlParams = (typeof window !== 'undefined' && window.location) ? new URLSearchParams(window.location.search) : null;
    const isPreview = (urlParams && urlParams.get('preview') === '1') || (typeof PREVIEW_MODE !== 'undefined' && PREVIEW_MODE);
    const prevDevId = (urlParams && urlParams.get('preview_device_id')) || (typeof PREVIEW_DEVICE_ID !== 'undefined' ? PREVIEW_DEVICE_ID : null);
    const prevTheme = (urlParams && urlParams.get('preview_theme')) || (typeof PREVIEW_THEME !== 'undefined' ? PREVIEW_THEME : '');
    const prevScale = (urlParams && urlParams.get('preview_scale')) || (typeof PREVIEW_SCALE !== 'undefined' ? PREVIEW_SCALE : '');

    const deviceToken = getOrCreateDeviceToken();
    const deviceMac = getOrCreateDeviceMac();
    const localIp = localStorage.getItem('kiosk_local_ip') || kioskLocalIP || '';
    const ekranCozunurluk = `${window.innerWidth}x${window.innerHeight}`;
    let apiAdresi = `/api/kiosk-data?key=${encodeURIComponent(LISANS_KEY)}&device_token=${encodeURIComponent(deviceToken)}&mac=${encodeURIComponent(deviceMac)}&local_ip=${encodeURIComponent(localIp)}&res=${encodeURIComponent(ekranCozunurluk)}&_t=${Date.now()}`;
    if (isPreview) {
        apiAdresi += `&preview=1`;
    }
    if (prevDevId) {
        apiAdresi += `&preview_device_id=${encodeURIComponent(prevDevId)}`;
    }
    if (prevTheme) {
        apiAdresi += `&preview_theme=${encodeURIComponent(prevTheme)}`;
    }
    if (prevScale) {
        apiAdresi += `&preview_scale=${encodeURIComponent(prevScale)}`;
    }

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
 * TV Kiosk Ekranının Canlılık Sinyalini (Heartbeat) Gönderir (5 saniyede bir)
 * Sunucudan gelen komutları (Cihaz Tanımlama / Identify) anında yakalar.
 */
async function kioskHeartbeatPing() {
    try {
        const deviceToken = getOrCreateDeviceToken();
        const deviceMac = getOrCreateDeviceMac();
        const localIp = localStorage.getItem('kiosk_local_ip') || kioskLocalIP || '';
        const ekranCozunurluk = `${window.innerWidth}x${window.innerHeight}`;
        const pingUrl = `/api/kiosk-ping?key=${encodeURIComponent(LISANS_KEY)}&device_token=${encodeURIComponent(deviceToken)}&mac=${encodeURIComponent(deviceMac)}&local_ip=${encodeURIComponent(localIp)}&res=${encodeURIComponent(ekranCozunurluk)}&_t=${Date.now()}`;

        const resp = await fetch(pingUrl);
        if (resp.ok) {
            const data = await resp.json();
            if (data && data.identify && data.identify.active) {
                cihazTanimlamaGoster(data.identify);
            }
        }
    } catch (e) {
        // Sessizce yutulur
    }
}


/**
 * 11. TV Kiosk Başlatıcı
 */
function kioskBaslat() {
    ekranWakeLockBaslat();

    saatVeTarihiGuncelle();
    setInterval(saatVeTarihiGuncelle, KIOSK_AYARLAR.SAAT_ARALIGI_MS);

    // İlk canlılık pingini at ve her 5 saniyede bir tekrarla (komut dinleme)
    kioskHeartbeatPing();
    setInterval(kioskHeartbeatPing, KIOSK_AYARLAR.HEARTBEAT_ARALIGI_MS);

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
            const ph = sonGecerliVeri.pharmacy;
            const ts = ph.theme_settings || {};
            const sc = (ts.scale !== undefined && ts.scale !== null) ? ts.scale : (ph.scale || ph.screen_scale || 'auto');
            const sm = (ts.safeAreaMargin !== undefined && ts.safeAreaMargin !== null) ? ts.safeAreaMargin : (ph.safeAreaMargin || 0);
            ekranOlceginiUygula(sc, sm);
        }
        if (kioskMap) {
            setTimeout(() => kioskMap.invalidateSize(), 200);
        }
        tickerHiziniVePozisyonunuAyarla(KIOSK_AYARLAR.TICKER_SPEED_PX || 55);
    });
}

/**
 * Ekran Ölçekleme (Zoom) ve Güvenli Alan (Safe Area Margin) Parametrelerini Ayrıştırır
 * URL Parametreleri (Öncelikli):
 *   - ?zoom=0.88 veya ?scale=88 veya ?scale=0.85
 *   - ?margin=20 veya ?padding=20 veya ?safe_area=20
 */
function getDisplayScalingParams() {
    let urlZoom = null;
    let urlMargin = null;

    try {
        const params = (typeof window !== 'undefined' && window.location) ? new URLSearchParams(window.location.search) : null;
        
        // 1. Zoom / Ölçek Parametresi
        const rawZoom = (params && (params.get('zoom') || params.get('scale'))) || (typeof URL_ZOOM !== 'undefined' ? URL_ZOOM : null);
        if (rawZoom !== null && rawZoom !== '') {
            const num = parseFloat(rawZoom);
            if (!isNaN(num) && num > 0) {
                // 85 -> 0.85 (yüzde girilmişse oranla)
                urlZoom = (num > 2) ? (num / 100) : num;
            } else if (rawZoom === '720p' || rawZoom === 'compact') {
                urlZoom = 0.85;
            } else if (rawZoom === '1080p') {
                urlZoom = 1.0;
            } else if (rawZoom === '4k') {
                urlZoom = 1.25;
            }
        }

        // 2. Güvenli Alan Margin / Padding Parametresi
        const rawMargin = (params && (params.get('margin') || params.get('padding') || params.get('safe_area') || params.get('safeAreaMargin'))) || (typeof URL_MARGIN !== 'undefined' ? URL_MARGIN : null);
        if (rawMargin !== null && rawMargin !== '') {
            const numMargin = parseFloat(rawMargin);
            if (!isNaN(numMargin) && numMargin >= 0) {
                urlMargin = numMargin;
            }
        }
    } catch (e) {
        console.warn('[Ölçekleme] URL parametreleri okunamadı:', e);
    }

    return { urlZoom, urlMargin };
}

/**
 * TV ve Kiosk Ekran Çözünürlüğü, Dinamik Zoom ve Güvenli Alanı (Safe Area) Uygular
 * Öncelik Sırası:
 * 1. URL Parametreleri (?zoom=0.88&margin=20)
 * 2. Cihaz / API Ayarları (scale, safeAreaMargin)
 * 3. Varsayılanlar (1.0 ve 0px)
 */
function ekranOlceginiUygula(scaleAyar = 'auto', safeAreaAyar = null) {
    const { urlZoom, urlMargin } = getDisplayScalingParams();

    // 1. Hedef Zoom Belirleme
    let hedefZoom = 1.0;
    
    if (urlZoom !== null) {
        // Öncelik 1: URL Parametresi (?zoom=0.88 veya ?scale=85)
        hedefZoom = urlZoom;
    } else if (typeof scaleAyar === 'number' && scaleAyar > 0) {
        hedefZoom = (scaleAyar > 2) ? (scaleAyar / 100) : scaleAyar;
    } else if (typeof scaleAyar === 'string' && scaleAyar.trim()) {
        const parsed = parseFloat(scaleAyar);
        if (!isNaN(parsed) && parsed > 0 && scaleAyar !== 'auto') {
            hedefZoom = (parsed > 2) ? (parsed / 100) : parsed;
        } else if (scaleAyar === 'compact' || scaleAyar === '720p') {
            hedefZoom = 0.85;
        } else if (scaleAyar === '1080p') {
            hedefZoom = 1.0;
        } else if (scaleAyar === '4k') {
            hedefZoom = 1.25;
        } else if (scaleAyar === 'auto') {
            // Standart TV ve Monitörlerde doğal tam ekran (yapay zoom büzüşmesi olmadan)
            hedefZoom = 1.0;
        }
    } else if (typeof CIHAZ_SCALE !== 'undefined' && CIHAZ_SCALE) {
        const parsed = parseFloat(CIHAZ_SCALE);
        if (!isNaN(parsed) && parsed > 0 && CIHAZ_SCALE !== 'auto') {
            hedefZoom = (parsed > 2) ? (parsed / 100) : parsed;
        }
    }

    // 2. Hedef Güvenli Alan (Safe Area Margin) Belirleme
    let hedefMargin = 0;
    if (urlMargin !== null) {
        // Öncelik 1: URL Parametresi (?margin=20 veya ?padding=20)
        hedefMargin = urlMargin;
    } else if (safeAreaAyar !== null && safeAreaAyar !== undefined) {
        const parsedMargin = parseFloat(safeAreaAyar);
        if (!isNaN(parsedMargin) && parsedMargin >= 0) {
            hedefMargin = parsedMargin;
        }
    } else if (typeof CIHAZ_SAFE_MARGIN !== 'undefined' && CIHAZ_SAFE_MARGIN) {
        const parsedMargin = parseFloat(CIHAZ_SAFE_MARGIN);
        if (!isNaN(parsedMargin) && parsedMargin >= 0) {
            hedefMargin = parsedMargin;
        }
    }

    // Güvenlik sınırları (Zoom: %40 - %200, Margin: 0 - 100px)
    hedefZoom = Math.max(0.4, Math.min(2.0, hedefZoom));
    hedefMargin = Math.max(0, Math.min(100, hedefMargin));

    // 3. CSS Zoom / Scale Uygulama (Tam ekranı doldurarak orantılı ölçekler)
    if (Math.abs(hedefZoom - 1.0) > 0.01) {
        if ('zoom' in document.body.style) {
            document.body.style.zoom = String(hedefZoom);
            // Zoom uygulandığında ekranın sağında ve altında boşluk kalmaması için boyutu telafi et
            const compW = (100 / hedefZoom).toFixed(4);
            const compH = (100 / hedefZoom).toFixed(4);
            document.body.style.width = `${compW}vw`;
            document.body.style.height = `${compH}vh`;
            document.body.style.minWidth = `${compW}vw`;
            document.body.style.minHeight = `${compH}vh`;
            document.body.style.maxWidth = `${compW}vw`;
            document.body.style.maxHeight = `${compH}vh`;
        }
    } else {
        document.body.style.zoom = '';
        document.body.style.width = '100vw';
        document.body.style.height = '100vh';
        document.body.style.minWidth = '';
        document.body.style.minHeight = '';
        document.body.style.maxWidth = '';
        document.body.style.maxHeight = '';
    }

    // 4. Safe Area Margin / Padding Değişkenlerini Güncelle (Overscan kesilmesini engeller)
    document.documentElement.style.setProperty('--safe-area-top', `${hedefMargin}px`);
    document.documentElement.style.setProperty('--safe-area-right', `${hedefMargin}px`);
    document.documentElement.style.setProperty('--safe-area-bottom', `${hedefMargin}px`);
    document.documentElement.style.setProperty('--safe-area-left', `${hedefMargin}px`);

    const elWrapper = document.querySelector('.dashboard-wrapper');
    if (elWrapper) {
        elWrapper.style.boxSizing = 'border-box';
        if (hedefMargin > 0) {
            elWrapper.style.padding = `calc(0.65rem + ${hedefMargin}px) calc(0.85rem + ${hedefMargin}px)`;
        } else {
            elWrapper.style.padding = '';
        }
    }

    // 5. CSS Sınıflarını Uyumlu Tut
    document.body.classList.remove('scale-compact', 'scale-720p', 'scale-1080p', 'scale-4k');
    if (hedefZoom <= 0.88) {
        document.body.classList.add('scale-compact');
    } else if (hedefZoom <= 0.95) {
        document.body.classList.add('scale-720p');
    } else if (hedefZoom <= 1.15) {
        document.body.classList.add('scale-1080p');
    } else {
        document.body.classList.add('scale-4k');
    }

    // 6. Harita Boyut Güncellemesi (Leaflet tile koordinatlarının kaymaması için)
    if (kioskMap) {
        setTimeout(() => {
            try { kioskMap.invalidateSize(); } catch (e) {}
        }, 120);
        setTimeout(() => {
            try { kioskMap.invalidateSize(); } catch (e) {}
        }, 350);
    }
}

/**
 * 12. Cihaz Tanımlama & Ekranda Göster Sinyali (Identify Overlay)
 * Geri sayım sayacı içerir ve 25 saniye sonunda otomatik olarak kapanır.
 */
let identifyTimer = null;
let identifyCountdownInterval = null;
let identifyKalanSaniye = 25;

function cihazTanimlamaGoster(identifyData) {
    let overlay = document.getElementById('device-identify-overlay');
    if (overlay) {
        // Zaten ekranda gösterimde, mükerrer oluşturma
        return;
    }

    overlay = document.createElement('div');
    overlay.id = 'device-identify-overlay';
    overlay.className = 'device-identify-overlay';
    // Tıklamayla erkenden kapatılabilmesi için
    overlay.onclick = function() { cihazTanimlamaGizle(); };

    const scaleLabels = {
        'auto': 'Otomatik TV Algılama',
        'compact': 'Mi Box / Kompakt (%80)',
        '720p': 'HD TV 720p (%85)',
        '1080p': 'Full HD 1080p (%100)',
        '4k': '4K Vitrin Ekranı (%130)'
    };
    const scaleMetin = scaleLabels[identifyData.screen_scale] || identifyData.screen_scale || 'Otomatik';

    overlay.innerHTML = `
        <div class="device-identify-box animate-pulse-glow" onclick="event.stopPropagation()">
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
                ✨ Bu ekran yönetim panelinden başarıyla tanımlandı (<span id="identify-countdown-num" style="color: #38bdf8; font-weight: 800; font-size: 1.1rem;">25</span> sn sonra kapanacak).
            </div>
        </div>
    `;

    document.body.appendChild(overlay);

    // Timer'ları başlat
    if (identifyTimer) clearTimeout(identifyTimer);
    if (identifyCountdownInterval) clearInterval(identifyCountdownInterval);

    identifyKalanSaniye = 25;
    identifyCountdownInterval = setInterval(() => {
        identifyKalanSaniye--;
        const countEl = document.getElementById('identify-countdown-num');
        if (countEl) countEl.textContent = Math.max(0, identifyKalanSaniye);
        if (identifyKalanSaniye <= 0) {
            cihazTanimlamaGizle();
        }
    }, 1000);

    identifyTimer = setTimeout(() => {
        cihazTanimlamaGizle();
    }, 25000);
}

function cihazTanimlamaGizle() {
    if (identifyTimer) {
        clearTimeout(identifyTimer);
        identifyTimer = null;
    }
    if (identifyCountdownInterval) {
        clearInterval(identifyCountdownInterval);
        identifyCountdownInterval = null;
    }
    const overlay = document.getElementById('device-identify-overlay');
    if (overlay) {
        overlay.style.transition = 'opacity 0.35s ease';
        overlay.style.opacity = '0';
        setTimeout(() => {
            if (overlay.parentNode) {
                overlay.parentNode.removeChild(overlay);
            }
        }, 350);
    }
}

document.addEventListener('DOMContentLoaded', kioskBaslat);
