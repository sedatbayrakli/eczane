"""
Nöbetçi Eczane TV Bilgi Ekranı (Kiosk Dashboard)
Backend Servisi - Flask & Gunicorn
"""

import os
import time
import json
import re
import html
import urllib.parse
from datetime import datetime
from threading import Lock
from typing import Tuple, List, Dict, Any
from flask import Flask, render_template, jsonify, request
import requests

# Flask uygulamasının başlatılması
app = Flask(__name__)

# Yapılandırma ve ortam değişkenleri
VARSAYILAN_IL = os.getenv("DEFAULT_PROVINCE", "istanbul").lower().strip()
VARSAYILAN_ILCE = os.getenv("DEFAULT_DISTRICT", "bahcelievler").lower().strip()
# Önbellek süresi (saniye cinsinden - varsayılan 45 dakika = 2700 saniye)
CACHE_SURESI = int(os.getenv("CACHE_TTL", "2700"))

# Bellek içi (in-memory) önbellek deposu ve thread güvenliği kilidi
onbellek_deposu = {}
onbellek_kilidi = Lock()


def turkce_karakter_temizle(metin: str) -> str:
    """
    Türkçe karakterleri URL ve slug uyumlu standart ASCII karakterlerine dönüştürür.
    Örnek: 'Bahçelievler' -> 'bahcelievler', 'Kadıköy' -> 'kadikoy'
    """
    if not metin:
        return ""
    metin = metin.strip().lower()
    donusumler = {
        'ı': 'i', 'ğ': 'g', 'ü': 'u', 'ş': 's', 'ö': 'o', 'ç': 'c',
        'İ': 'i', 'Ğ': 'g', 'Ü': 'u', 'Ş': 's', 'Ö': 'o', 'Ç': 'c'
    }
    for kaynak, hedef in donusumler.items():
        metin = metin.replace(kaynak, hedef)
    # Alfanümerik ve tire dışındaki karakterleri temizleme
    metin = re.sub(r'[^a-z0-9\-]+', '-', metin)
    return metin.strip('-')


def telefon_formatla(telefon_ham: str) -> str:
    """
    Gelen ham telefon numarasını TV ekranında rahat okunacak '0212 555 55 55' biçimine getirir.
    """
    if not telefon_ham:
        return ""
    rakamlar = re.sub(r'\D', '', telefon_ham)
    if len(rakamlar) == 10:  # Örn: 2125555555
        rakamlar = "0" + rakamlar
    if len(rakamlar) == 11 and rakamlar.startswith("0"):
        return f"{rakamlar[:4]} {rakamlar[4:7]} {rakamlar[7:9]} {rakamlar[9:]}"
    return telefon_ham


def harita_linki_olustur(isim: str, adres: str, enlem: float = None, boylam: float = None) -> str:
    """
    Eczane için doğrudan Google Haritalar navigasyon veya arama linki oluşturur.
    """
    if enlem and boylam:
        return f"https://www.google.com/maps/search/?api=1&query={enlem},{boylam}"
    arama_sorgusu = urllib.parse.quote(f"{isim} {adres}")
    return f"https://www.google.com/maps/search/?api=1&query={arama_sorgusu}"


def qr_kod_url_olustur(hedef_url: str) -> str:
    """
    Verilen URL için telefon kamerasıyla taranabilecek dinamik QR kod görsel linki üretir.
    """
    encoded_url = urllib.parse.quote(hedef_url)
    return f"https://api.qrserver.com/v1/create-qr-code/?size=180x180&data={encoded_url}&margin=6"


def yedek_veri_uret(il: str, ilce: str) -> list:
    """
    Harici servis tamamen ulaşılamaz olduğunda veya internet kesintisinde
    TV ekranının boş kalmaması için üretilen kurtarma verisi seti.
    """
    ornek_eczaneler = [
        {
            "id": 1,
            "isim": "Örnek Emre Eczanesi",
            "il": il.capitalize(),
            "ilce": ilce.capitalize(),
            "semt": "Siyavuşpaşa",
            "adres": "Siyavuşpaşa Mahallesi, Çamlık Caddesi, İğde Sokak No: 3/2 Bahçelievler / İstanbul",
            "telefon": "0212 556 67 80",
            "telefon_link": "tel:02125566780",
            "yol_tarifi": "QNB Finansbank Yakını (~160m)",
            "nobet_durumu": "Sabaha kadar açık",
            "enlem": 41.004626,
            "boylam": 28.857539,
            "harita_linki": "https://www.google.com/maps/search/?api=1&query=41.004626,28.857539",
            "qr_kod_url": qr_kod_url_olustur("https://www.google.com/maps/search/?api=1&query=41.004626,28.857539")
        },
        {
            "id": 2,
            "isim": "Örnek Has Eczanesi",
            "il": il.capitalize(),
            "ilce": ilce.capitalize(),
            "semt": "Merkez",
            "adres": "Bahçelievler Mahallesi, Kültür Sokak No: 2/B Bahçelievler / İstanbul",
            "telefon": "0212 442 24 77",
            "telefon_link": "tel:02124422477",
            "yol_tarifi": "Gloria Jean's Yanı (~15m)",
            "nobet_durumu": "Sabaha kadar açık",
            "enlem": 40.995927,
            "boylam": 28.863768,
            "harita_linki": "https://www.google.com/maps/search/?api=1&query=40.995927,28.863768",
            "qr_kod_url": qr_kod_url_olustur("https://www.google.com/maps/search/?api=1&query=40.995927,28.863768")
        },
        {
            "id": 3,
            "isim": "Örnek Özperk Eczanesi",
            "il": il.capitalize(),
            "ilce": ilce.capitalize(),
            "semt": "Şirinevler",
            "adres": "Şirinevler Mahallesi, Fetih Caddesi No: 45 Bahçelievler / İstanbul",
            "telefon": "0212 503 17 16",
            "telefon_link": "tel:02125031716",
            "yol_tarifi": "Bülent Ecevit İlköğretim Okulu Karşısı (~90m)",
            "nobet_durumu": "Sabaha kadar açık",
            "enlem": 41.005404,
            "boylam": 28.845097,
            "harita_linki": "https://www.google.com/maps/search/?api=1&query=41.005404,28.845097",
            "qr_kod_url": qr_kod_url_olustur("https://www.google.com/maps/search/?api=1&query=41.005404,28.845097")
        },
        {
            "id": 4,
            "isim": "Örnek Hayat Eczanesi",
            "il": il.capitalize(),
            "ilce": ilce.capitalize(),
            "semt": "Yenibosna",
            "adres": "Zafer Mahallesi, Yıldırım Beyazıt Caddesi No: 88 Bahçelievler / İstanbul",
            "telefon": "0212 503 72 57",
            "telefon_link": "tel:02125037257",
            "yol_tarifi": "Özel İlke Hastanesi Yanı (~15m)",
            "nobet_durumu": "Sabaha kadar açık",
            "enlem": 40.997754,
            "boylam": 28.834849,
            "harita_linki": "https://www.google.com/maps/search/?api=1&query=40.997754,28.834849",
            "qr_kod_url": qr_kod_url_olustur("https://www.google.com/maps/search/?api=1&query=40.997754,28.834849")
        }
    ]
    return ornek_eczaneler


def nobetci_eczaneleri_cek(il: str = "istanbul", ilce: str = "bahcelievler") -> Tuple[list, str]:
    """
    Hedef kaynaktan güncel nöbetçi eczaneleri web kazıma (scraping) ve JSON-LD yapılandırılmış
    veri çözümleme yöntemiyle çeker. Hata durumunda kurtarma mekanizmasını devreye sokar.
    """
    il_slug = turkce_karakter_temizle(il)
    ilce_slug = turkce_karakter_temizle(ilce)
    hedef_url = f"https://www.nobetcieczaneler.org/{il_slug}/{ilce_slug}"

    tarayici_basliklari = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"
    }

    try:
        yanit = requests.get(hedef_url, headers=tarayici_basliklari, timeout=12)
        if yanit.status_code != 200:
            print(f"[UYARI] Dış kaynak HTTP kodu döndü: {yanit.status_code}")
            return [], f"Kaynak site yanıt vermedi (HTTP {yanit.status_code})"

        icerik = yanit.text

        # 1. Aşama: Sayfadaki Schema.org JSON-LD yapısal verisini ayrıştırma
        json_ld_eczaneler = {}
        json_bloklari = re.findall(r'<script type=[\"\']application/ld\+json[\"\']>(.*?)</script>', icerik, re.DOTALL)
        for blok in json_bloklari:
            try:
                veri = json.loads(blok)
                if isinstance(veri, dict) and veri.get("@type") == "ItemList":
                    for eleman in veri.get("itemListElement", []):
                        eczane_objesi = eleman.get("item", {})
                        eczane_adi = eczane_objesi.get("name", "").strip()
                        if eczane_adi:
                            # İsim normalizasyonu (büyük/küçük harf bağımsız eşleştirme için)
                            anahtar = re.sub(r'\s+', ' ', eczane_adi.lower())
                            json_ld_eczaneler[anahtar] = eczane_objesi
            except Exception:
                continue

        # 2. Aşama: HTML kartlarını ayrıştırarak semt, yol tarifi ve nöbet bilgisini alma
        kartlar = re.findall(
            r'<article[^>]*class=[\"\'][^\"\']*nearest-pharmacy-card[^\"\']*[\"\'][^>]*>(.*?)</article>',
            icerik,
            re.DOTALL
        )
        if not kartlar:
            kartlar = re.findall(
                r'<div[^>]*class=[\"\'][^\"\']*nearest-pharmacy-card[^\"\']*[\"\'][^>]*>(.*?)(?=<div[^>]*class=[\"\'][^\"\']*nearest-pharmacy-card|<!-- end|$)',
                icerik,
                re.DOTALL
            )

        sonuc_listesi = []

        # Eğer HTML kartları bulunduysa kart + JSON-LD eşleştirmesi yap
        if kartlar:
            for sira, kart in enumerate(kartlar, start=1):
                isim_eslesme = re.search(r'<h3>.*?<a[^>]*>(.*?)</a>', kart, re.DOTALL) or re.search(r'<h3>(.*?)</h3>', kart, re.DOTALL)
                eczane_adi = html.unescape(isim_eslesme.group(1).strip()) if isim_eslesme else f"Eczane #{sira}"
                
                # HTML temizleme (içeride tag kaldıysa)
                eczane_adi = re.sub(r'<[^>]+>', '', eczane_adi).strip()

                semt_eslesme = re.search(r'nearest-pharmacy-card__semt-tag[^>]*>\s*([^<]+)\s*<', kart)
                semt = html.unescape(semt_eslesme.group(1).strip()) if semt_eslesme else ""

                landmark_eslesme = re.search(r'nearest-pharmacy-card__landmark-name[^>]*>\s*([^<]+)\s*<', kart)
                mesafe_eslesme = re.search(r'nearest-pharmacy-card__landmark-distance[^>]*>\s*([^<]+)\s*<', kart)
                
                landmark = html.unescape(landmark_eslesme.group(1).strip()) if landmark_eslesme else ""
                mesafe = html.unescape(mesafe_eslesme.group(1).strip()) if mesafe_eslesme else ""
                mesafe = mesafe.replace('·', '').strip()
                
                yol_tarifi = ""
                if landmark and mesafe:
                    yol_tarifi = f"{landmark} ({mesafe})"
                elif landmark:
                    yol_tarifi = landmark

                nobet_eslesme = (
                    re.search(r'data-duty-active-label=[\"\']([^\"\']+)[\"\']', kart) or
                    re.search(r'class=[\"\']duty-badge[^>]*>\s*([^<]+)\s*<', kart)
                )
                nobet_durumu = html.unescape(nobet_eslesme.group(1).strip()) if nobet_eslesme else "Sabaha kadar açık"

                # JSON-LD verisiyle eşleştirme
                anahtar = re.sub(r'\s+', ' ', eczane_adi.lower())
                json_detay = json_ld_eczaneler.get(anahtar, {})

                # Adres
                adres = ""
                if json_detay.get("address"):
                    adres = json_detay["address"].get("streetAddress", "")
                if not adres:
                    adres_eslesme = re.search(r'nearest-pharmacy-card__address[^>]*>\s*([^<]+)\s*<', kart)
                    adres = html.unescape(adres_eslesme.group(1).strip()) if adres_eslesme else f"{ilce.capitalize()}, {il.capitalize()}"

                # Telefon
                telefon_ham = json_detay.get("telephone", "")
                if not telefon_ham:
                    tel_eslesme = re.search(r'nearest-pharmacy-card__phone[^>]*>\s*([^<]+)\s*<', kart)
                    telefon_ham = tel_eslesme.group(1).strip() if tel_eslesme else ""

                telefon = telefon_formatla(telefon_ham)
                telefon_link = f"tel:{re.sub(r'[^0-9+]', '', telefon_ham)}" if telefon_ham else ""

                # Koordinatlar
                geo = json_detay.get("geo", {})
                enlem = geo.get("latitude")
                boylam = geo.get("longitude")

                harita_link = harita_linki_olustur(eczane_adi, adres, enlem, boylam)
                qr_kod_url = qr_kod_url_olustur(harita_link)

                sonuc_listesi.append({
                    "id": sira,
                    "isim": eczane_adi,
                    "il": il.capitalize(),
                    "ilce": ilce.capitalize(),
                    "semt": semt,
                    "adres": adres,
                    "telefon": telefon,
                    "telefon_link": telefon_link,
                    "yol_tarifi": yol_tarifi,
                    "nobet_durumu": nobet_durumu,
                    "enlem": enlem,
                    "boylam": boylam,
                    "harita_linki": harita_link,
                    "qr_kod_url": qr_kod_url
                })

        # Eğer HTML kartları ayrıştırılamadıysa doğrudan JSON-LD üzerinden üret
        elif json_ld_eczaneler:
            for sira, (anahtar, eczane_objesi) in enumerate(json_ld_eczaneler.items(), start=1):
                eczane_adi = eczane_objesi.get("name", f"Eczane #{sira}")
                adres_obj = eczane_objesi.get("address", {})
                adres = adres_obj.get("streetAddress", f"{ilce.capitalize()}, {il.capitalize()}")
                telefon_ham = eczane_objesi.get("telephone", "")
                geo = eczane_objesi.get("geo", {})
                enlem = geo.get("latitude")
                boylam = geo.get("longitude")

                harita_link = harita_linki_olustur(eczane_adi, adres, enlem, boylam)
                qr_kod_url = qr_kod_url_olustur(harita_link)

                sonuc_listesi.append({
                    "id": sira,
                    "isim": eczane_adi,
                    "il": il.capitalize(),
                    "ilce": ilce.capitalize(),
                    "semt": "",
                    "adres": adres,
                    "telefon": telefon_formatla(telefon_ham),
                    "telefon_link": f"tel:{re.sub(r'[^0-9+]', '', telefon_ham)}" if telefon_ham else "",
                    "yol_tarifi": "",
                    "nobet_durumu": "Sabaha kadar açık",
                    "enlem": enlem,
                    "boylam": boylam,
                    "harita_linki": harita_link,
                    "qr_kod_url": qr_kod_url
                })

        if sonuc_listesi:
            return sonuc_listesi, ""
        return [], "Sayfada nöbetçi eczane bulunamadı"

    except Exception as hata:
        print(f"[HATA] Nöbetçi eczane çekilirken hata oluştu: {str(hata)}")
        return [], str(hata)


def veri_getir_onbellekli(il: str, ilce: str) -> dict:
    """
    Önbellek mekanizması:
    1. Geçerli önbellek varsa doğrudan döndürür.
    2. Süresi dolmuşsa veya veri yoksa taze veriyi çeker ve önbelleğe yazar.
    3. Hata veya kesinti olursa en son geçerli önbelleği 'stale' bayrağıyla korur.
    4. Hiç veri yoksa yedek örnek veriyi döner (TV asla siyah/boş kalmaz).
    """
    onbellek_anahtari = f"{turkce_karakter_temizle(il)}_{turkce_karakter_temizle(ilce)}"
    suan = time.time()

    with onbellek_kilidi:
        kayit = onbellek_deposu.get(onbellek_anahtari)
        # Eğer önbellek varsa ve TTL süresi henüz dolmadıysa
        if kayit and (suan - kayit["timestamp"] < CACHE_SURESI):
            return {
                "success": True,
                "eczaneler": kayit["data"],
                "kaynak": "cache",
                "onbellek_zamani": kayit["formatted_time"],
                "gecikme_saniye": int(suan - kayit["timestamp"]),
                "il": il.capitalize(),
                "ilce": ilce.capitalize()
            }

    # Yeni veri çekme denemesi
    taze_eczaneler, hata_mesaji = nobetci_eczaneleri_cek(il, ilce)

    if taze_eczaneler:
        zaman_metni = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        with onbellek_kilidi:
            onbellek_deposu[onbellek_anahtari] = {
                "timestamp": suan,
                "formatted_time": zaman_metni,
                "data": taze_eczaneler
            }
        return {
            "success": True,
            "eczaneler": taze_eczaneler,
            "kaynak": "live",
            "onbellek_zamani": zaman_metni,
            "gecikme_saniye": 0,
            "il": il.capitalize(),
            "ilce": ilce.capitalize()
        }

    # Çekme başarısız olduysa önceden kalmış eski önbellek verisi var mı kontrol et
    with onbellek_kilidi:
        if kayit and kayit.get("data"):
            return {
                "success": True,
                "eczaneler": kayit["data"],
                "kaynak": "stale_cache",
                "hata_detayi": hata_mesaji,
                "onbellek_zamani": kayit["formatted_time"],
                "gecikme_saniye": int(suan - kayit["timestamp"]),
                "il": il.capitalize(),
                "ilce": ilce.capitalize()
            }

    # Hiçbir önbellek yoksa ve bağlantı koptuysa yedek veriyi sun
    zaman_metni = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    yedek = yedek_veri_uret(il, ilce)
    return {
        "success": True,
        "eczaneler": yedek,
        "kaynak": "fallback_offline",
        "hata_detayi": hata_mesaji,
        "onbellek_zamani": zaman_metni,
        "gecikme_saniye": 0,
        "il": il.capitalize(),
        "ilce": ilce.capitalize()
    }


# ==========================================
# HTTP Rotaları (Endpoints)
# ==========================================

@app.route("/")
def index():
    """
    TV Kiosk arayüzünü (Dashboard HTML) sunar.
    URL parametresi ile ilçe özelleştirilebilir: /?ilce=kadikoy
    """
    ilce = request.args.get("ilce", VARSAYILAN_ILCE)
    il = request.args.get("il", VARSAYILAN_IL)
    return render_template(
        "index.html",
        secili_il=il.capitalize(),
        secili_ilce=ilce.capitalize(),
        varsayilan_ilce=ilce
    )


@app.route("/api/nobetci-eczaneler")
def api_nobetci_eczaneler():
    """
    TV arayüzünün 15 dakikada bir sorgulayacağı JSON veri endpoint'i.
    """
    il = request.args.get("il", VARSAYILAN_IL)
    ilce = request.args.get("ilce", VARSAYILAN_ILCE)
    veri = veri_getir_onbellekli(il, ilce)
    return jsonify(veri)


@app.route("/api/health")
def api_health():
    """
    Coolify ve Docker Healthcheck için sağlık durumu kontrol endpoint'i.
    """
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "cache_entries": len(onbellek_deposu)
    }), 200


if __name__ == "__main__":
    # Geliştirme ortamı çalıştırması
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
