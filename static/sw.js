/**
 * Nöbetçi Eczane TV Kiosk - Service Worker v1.0
 * Çevrimdışı Dayanıklılık ve Statik Varlık Önbellekleme
 */

const CACHE_NAME = 'kiosk-cache-v1';
const STATIC_ASSETS = [
    '/static/style.css',
    '/static/kiosk_app.js',
    '/static/qrcode.min.js',
    '/static/html2canvas.min.js',
    '/static/manifest.json'
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            console.log('[ServiceWorker] Statik varlıklar önbelleğe alınıyor...');
            return cache.addAll(STATIC_ASSETS).catch((err) => {
                console.warn('[ServiceWorker] Bazı varlıklar önbelleğe alınamadı:', err);
            });
        })
    );
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) => {
            return Promise.all(
                keys.map((k) => {
                    if (k !== CACHE_NAME) {
                        console.log('[ServiceWorker] Eski önbellek siliniyor:', k);
                        return caches.delete(k);
                    }
                })
            );
        })
    );
    self.clients.claim();
});

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // API çağrıları için: Network-First (Önce Ağ, başarısızsa önbellek yok - çünkü dinamik veri)
    if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/admin')) {
        return; // Tarayıcının varsayılan ağ akışına bırak
    }

    // Statik dosyalar ve Kiosk HTML için: Stale-While-Revalidate
    event.respondWith(
        caches.match(event.request).then((cachedResponse) => {
            const fetchPromise = fetch(event.request)
                .then((networkResponse) => {
                    if (networkResponse && networkResponse.status === 200 && networkResponse.type === 'basic') {
                        const responseToCache = networkResponse.clone();
                        caches.open(CACHE_NAME).then((cache) => {
                            cache.put(event.request, responseToCache);
                        });
                    }
                    return networkResponse;
                })
                .catch(() => {
                    // Çevrimdışıyken ağ hatası olursa cachedResponse döner
                    return cachedResponse;
                });

            return cachedResponse || fetchPromise;
        })
    );
});
