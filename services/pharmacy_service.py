"""
Nöbetçi Eczane Veri Servisi & Fallback Pipeline
İstanbul Eczacı Odası (İEO), eczaneler.gen.tr ve nobetcieczaneler.org kaynaklı
öncelikli sıralı veri çekme motoru.
"""

import re
import html
import json
import time
import math
import urllib.parse
from datetime import datetime
from threading import Lock
from typing import List, Dict, Any, Tuple, Optional
import requests

# 45 Dakikalık Önbellek Süresi (Saniye)
CACHE_SURESI_SANIYE = 45 * 60
onbellek_deposu: Dict[str, Dict[str, Any]] = {}
onbellek_kilidi = Lock()

# Standart Tarayıcı Başlıkları
TARAYICI_BASLIKLARI = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"
}


def turkce_karakter_temizle(metin: str) -> str:
    """Türkçe karakterleri URL ve slug uyumlu standart ASCII karakterlerine dönüştürür."""
    if not metin:
        return ""
    harf_haritasi = {
        'İ': 'i', 'I': 'i', 'ı': 'i',
        'Ğ': 'g', 'ğ': 'g',
        'Ü': 'u', 'ü': 'u',
        'Ş': 's', 'ş': 's',
        'Ö': 'o', 'ö': 'o',
        'Ç': 'c', 'ç': 'c'
    }
    for kaynak, hedef in harf_haritasi.items():
        metin = metin.replace(kaynak, hedef)
    metin = metin.lower()
    metin = re.sub(r'[^a-z0-9\-]+', '-', metin)
    return metin.strip('-')


def telefon_formatla(telefon_ham: str) -> str:
    """Ham telefon numarasını okunabilir '0XXX XXX XX XX' formatına çevirir."""
    if not telefon_ham:
        return ""
    rakamlar = re.sub(r'[^0-9]', '', telefon_ham)
    if len(rakamlar) == 10 and not rakamlar.startswith("0"):
        rakamlar = "0" + rakamlar
    if len(rakamlar) == 11 and rakamlar.startswith("0"):
        return f"{rakamlar[:4]} {rakamlar[4:7]} {rakamlar[7:9]} {rakamlar[9:]}"
    return telefon_ham


def haversine_mesafe(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    İki koordinat arasındaki kuş uçuşu mesafeyi metre cinsinden hesaplar (Haversine Formülü).
    """
    R = 6371000  # Dünya yarıçapı (metre)
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


def mesafe_ve_yurume_hesapla(mesafe_metre: float) -> Tuple[str, str]:
    """
    Mesafeyi '850 m' veya '2.4 km' formatında ve ortalama yürüme süresini döndürür.
    (Ortalama yürüme hızı: 4.5 km/saat -> dakikada ~75 metre)
    """
    if mesafe_metre < 1000:
        mesafe_metin = f"{int(round(mesafe_metre))} m"
    else:
        mesafe_metin = f"{mesafe_metre / 1000.0:.1f} km"

    yurume_dakika = max(1, int(round(mesafe_metre / 75.0)))
    yurume_metin = f"~{yurume_dakika} dk"
    return mesafe_metin, yurume_metin


def rota_linki_olustur(hedef_enlem: Optional[float], hedef_boylam: Optional[float],
                       eczane_adi: str, adres: str,
                       kendi_enlem: Optional[float] = None, kendi_boylam: Optional[float] = None) -> str:
    """
    Google Haritalar yön tarifi (Turn-by-turn navigation) veya konum arama linki oluşturur.
    Kendi eczanemizin konumu varsa 'origin' ve 'destination' rota linki verir.
    """
    if kendi_enlem and kendi_boylam and hedef_enlem and hedef_boylam:
        return (f"https://www.google.com/maps/dir/?api=1"
                f"&origin={kendi_enlem},{kendi_boylam}"
                f"&destination={hedef_enlem},{hedef_boylam}&travelmode=walking")
    elif hedef_enlem and hedef_boylam:
        return f"https://www.google.com/maps/search/?api=1&query={hedef_enlem},{hedef_boylam}"
    else:
        arama = urllib.parse.quote(f"{eczane_adi} {adres}")
        return f"https://www.google.com/maps/search/?api=1&query={arama}"


def qr_kod_url_olustur(hedef_url: str) -> str:
    """QR Server API ile dinamik QR kod resim bağlantısı oluşturur."""
    encoded_url = urllib.parse.quote(hedef_url)
    return f"https://api.qrserver.com/v1/create-qr-code/?size=180x180&data={encoded_url}&margin=6"


# ==========================================
# 1. BİRİNCİL KAYNAK: İSTANBUL ECZACI ODASI (İEO)
# ==========================================
def kaynak_ieo_cek(il: str, ilce: str) -> Tuple[List[Dict[str, Any]], str]:
    """
    İstanbul Eczacı Odası resmi nöbetçi eczane servisinden veri çeker.
    Yalnızca İstanbul ili için geçerlidir.
    """
    if turkce_karakter_temizle(il) != "istanbul":
        return [], "İEO servisi yalnızca İstanbul için geçerlidir"

    ilce_temiz = ilce.strip()

    url_ana = "https://www.istanbuleczaciodasi.org.tr/nobetci-eczane/"
    url_ajax = "https://www.istanbuleczaciodasi.org.tr/nobetci-eczane/index.php"

    session = requests.Session()
    session.headers.update(TARAYICI_BASLIKLARI)

    try:
        yanit_ana = session.get(url_ana, timeout=8)
        if yanit_ana.status_code != 200:
            return [], f"İEO ana sayfa HTTP {yanit_ana.status_code}"

        h_eslesme = re.search(r'id=["\']h["\']\s+value=["\']([^"\']+)["\']', yanit_ana.text)
        if not h_eslesme:
            return [], "İEO oturum güvenlik anahtarı (h token) bulunamadı"

        h_token = h_eslesme.group(1)

        ajax_headers = {
            "Referer": url_ana,
            "X-Requested-With": "XMLHttpRequest"
        }
        post_data = {
            "jx": "1",
            "islem": "get_ilce_eczane",
            "ilce": ilce_temiz,
            "h": h_token
        }

        yanit_ajax = session.post(url_ajax, data=post_data, headers=ajax_headers, timeout=10)
        if yanit_ajax.status_code != 200:
            return [], f"İEO ajax HTTP {yanit_ajax.status_code}"

        veri = yanit_ajax.json()
        if veri.get("error") != 0 and veri.get("error") != "0":
            return [], veri.get("msg", "İEO API hata döndürdü")

        ham_eczaneler = veri.get("eczaneler", [])
        if not ham_eczaneler:
            return [], "İEO ilçede nöbetçi eczane listesi boş döndü"

        sonuclar = []
        for sira, e in enumerate(ham_eczaneler, start=1):
            eczane_adi = e.get("eczane_ad", "").strip() or e.get("adi", "").strip() or f"Eczane #{sira}"
            semt = e.get("semt", "").strip()
            
            # Adres HTML etiketlerini ve başlığını temizle
            raw_adres = e.get("adres", "").strip()
            adres = re.sub(r'<[^>]+>', '', raw_adres).replace("Adres:", "").strip()
            
            # Telefon ve tarif temizleme
            telefon_ham = e.get("eczane_tel", "").strip() or e.get("tel", "").strip()
            raw_tarif = e.get("tarif", "").strip()
            yol_tarifi = re.sub(r'<[^>]+>', '', raw_tarif).replace("Tarif:", "").strip()

            try:
                enlem = float(e.get("lat")) if e.get("lat") else None
                boylam = float(e.get("lng") or e.get("lon")) if (e.get("lng") or e.get("lon")) else None
            except (ValueError, TypeError):
                enlem, boylam = None, None

            sonuclar.append({
                "id": sira,
                "isim": eczane_adi,
                "il": "İstanbul",
                "ilce": ilce.capitalize(),
                "semt": semt,
                "adres": adres,
                "telefon": telefon_formatla(telefon_ham),
                "telefon_link": f"tel:{re.sub(r'[^0-9+]', '', telefon_ham)}" if telefon_ham else "",
                "yol_tarifi": yol_tarifi,
                "nobet_durumu": "Sabaha kadar açık",
                "enlem": enlem,
                "boylam": boylam,
                "kaynak": "ieo_resmi",
                "sicil": str(e.get("sicil", "")).strip()
            })

        return sonuclar, ""
    except Exception as err:
        return [], f"İEO çekim hatası: {str(err)}"


def eczane_detay_bilgisi_ara(eczane_adi: str, ilce: str, il: str = "İstanbul", mevcut_adres: str = "") -> Dict[str, Any]:
    """
    Eczane kayıt formunda otomatik adres, telefon, sicil ve koordinat bulucu.
    Kullanım Sırası:
    1. Öncelikli Kaynak: İstanbul Eczacı Odası (İEO)
    2. İkincil Kaynak: Açık Ağ Eczane Dizinleri ve Arama Motoru (Lite & HTML)
    """
    from bs4 import BeautifulSoup

    eczane_temiz = re.sub(r'(?i)\beczane(si)?\b', '', eczane_adi).strip()
    bulunan_adres = mevcut_adres.strip() if mevcut_adres else ""
    bulunan_tel = ""
    bulunan_lat = None
    bulunan_lon = None
    kaynak_adi = ""
    oda_sicil = ""

    # ----------------------------------------------------
    # 1. BİRİNCİL VE ÖNCELİKLİ KAYNAK: İSTANBUL ECZACI ODASI
    # ----------------------------------------------------
    if turkce_karakter_temizle(il) == "istanbul":
        try:
            ieo_eczaneler, ieo_err = kaynak_ieo_cek(il, ilce)
            if ieo_eczaneler:
                aranan_norm = turkce_karakter_temizle(eczane_temiz)
                for e in ieo_eczaneler:
                    if aranan_norm in turkce_karakter_temizle(e.get("isim", "")):
                        kaynak_adi = "İstanbul Eczacı Odası (İEO Resmi)"
                        bulunan_adres = e.get("adres", "")
                        bulunan_tel = e.get("telefon", "")
                        oda_sicil = e.get("sicil", "")
                        bulunan_lat = e.get("enlem")
                        bulunan_lon = e.get("boylam")
                        break
        except Exception as e:
            print(f"[İEO Arama Hatası] {e}", flush=True)

    # ----------------------------------------------------
    # 2. İKİNCİL KAYNAK: AKILLI DİZİN VE AĞ TARAMASI (Lite & HTML)
    # ----------------------------------------------------
    if not bulunan_adres or not bulunan_tel:
        arama_servisleri = [
            ("https://lite.duckduckgo.com/lite/", "td", "result-snippet"),
            ("https://html.duckduckgo.com/html/", "a", "result__snippet")
        ]

        q = f"{eczane_adi} {ilce} adres telefon eczanesi"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded"
        }

        for endpoint_url, tag_name, class_name in arama_servisleri:
            try:
                r_ara = requests.post(endpoint_url, data={"q": q}, headers=headers, timeout=8)
                if r_ara.status_code == 200:
                    soup = BeautifulSoup(r_ara.text, "html.parser")
                    elements = soup.find_all(tag_name, class_=class_name)
                    snippets = [el.get_text().strip() for el in elements]
                    tam_metin = " ".join(snippets)

                    if not tam_metin:
                        # Fallback: Tüm sayfa metninden ara
                        tam_metin = soup.get_text()

                    # Telefon numarası ayıkla (0212... veya (0212)...)
                    if not bulunan_tel:
                        tel_m = re.search(r'(?:0\s*\(?2[0-9]{2}\)?|\(?0?2[0-9]{2}\)?)[ \-]?[0-9]{3}[ \-]?[0-9]{2}[ \-]?[0-9]{2}', tam_metin)
                        if tel_m:
                            bulunan_tel = telefon_formatla(tel_m.group(0).strip())

                    # Açık adres ayıkla
                    if not bulunan_adres:
                        adr_m = re.search(r'(?:Eczanenin Adresi|Adres|Adresi)\s*:\s*([^.]+?)(?:Telefon|Tel|şeklindedir|\.|$|\n)', tam_metin, re.IGNORECASE)
                        if adr_m:
                            bulunan_adres = adr_m.group(1).strip()
                        else:
                            mah_sok = re.search(r'([A-Za-zÇŞĞÜÖİçşğüöı0-9\s]+Mahallesi[^\.\,\;]+(?:Sokak|Sk\.|Cad\.|Caddesi)[^\.\,\;]*(?:No\s*:\s*[0-9\/A-Za-z]+)?)', tam_metin, re.IGNORECASE)
                            if mah_sok:
                                bulunan_adres = mah_sok.group(1).strip()

                    if bulunan_adres or bulunan_tel:
                        if not kaynak_adi:
                            kaynak_adi = "Eczane Bilgi Portalı (Açık Ağ)"
                        break
            except Exception as e:
                print(f"[Ağ Arama Hatası - {endpoint_url}] {e}", flush=True)
                continue

    return {
        "eczane_adi": eczane_adi,
        "ilce": ilce,
        "il": il,
        "adres": bulunan_adres,
        "telefon": bulunan_tel,
        "sicil": oda_sicil,
        "enlem": bulunan_lat,
        "boylam": bulunan_lon,
        "kaynak": kaynak_adi
    }


# ==========================================
# 2. İKİNCİL KAYNAK: ECZANELER.GEN.TR
# ==========================================
def kaynak_eczaneler_gen_tr_cek(il: str, ilce: str) -> Tuple[List[Dict[str, Any]], str]:
    """
    eczaneler.gen.tr sitesinden ilçe bazlı nöbetçi eczaneleri çeker.
    """
    il_slug = turkce_karakter_temizle(il)
    ilce_slug = turkce_karakter_temizle(ilce)
    hedef_url = f"https://www.eczaneler.gen.tr/nobetci-{il_slug}-{ilce_slug}"

    try:
        yanit = requests.get(hedef_url, headers=TARAYICI_BASLIKLARI, timeout=10, allow_redirects=True)
        if yanit.status_code != 200:
            return [], f"eczaneler.gen.tr HTTP {yanit.status_code}"

        icerik = yanit.text
        if "cf-mitigated" in icerik or "challenges.cloudflare.com" in icerik:
            return [], "eczaneler.gen.tr Cloudflare challenge korumasına takıldı"

        # Kartları ayrıştır
        kartlar = re.findall(r'<div[^>]*class=["\'][^"\']*my-2[^"\']*["\'][^>]*>(.*?)</div>\s*</div>', icerik, re.DOTALL)
        if not kartlar:
            kartlar = re.findall(r'<tr[^>]*>(.*?)</tr>', icerik, re.DOTALL)

        sonuclar = []
        for sira, kart in enumerate(kartlar, start=1):
            isim_m = re.search(r'<span[^>]*class=["\']text-capitalize["\'][^>]*>(.*?)</span>', kart) or re.search(r'<h3>(.*?)</h3>', kart)
            if not isim_m:
                continue
            isim = html.unescape(re.sub(r'<[^>]+>', '', isim_m.group(1)).strip())

            adres_m = re.search(r'<span[^>]*class=["\']col-lg-6[^"\']*["\'][^>]*>(.*?)</span>', kart)
            adres = html.unescape(re.sub(r'<[^>]+>', '', adres_m.group(1)).strip()) if adres_m else ""

            tel_m = re.search(r'tel:([0-9\+]+)', kart) or re.search(r'([0-9]{3,4}\s*[0-9]{3}\s*[0-9]{2}\s*[0-9]{2})', kart)
            telefon_ham = tel_m.group(1) if tel_m else ""

            # Koordinat çıkarma
            geo_m = re.search(r'data-latitude=["\']([0-9\.]+)["\']\s+data-longitude=["\']([0-9\.]+)["\']', kart)
            enlem = float(geo_m.group(1)) if geo_m else None
            boylam = float(geo_m.group(2)) if geo_m else None

            sonuclar.append({
                "id": sira,
                "isim": isim,
                "il": il.capitalize(),
                "ilce": ilce.capitalize(),
                "semt": "",
                "adres": adres,
                "telefon": telefon_formatla(telefon_ham),
                "telefon_link": f"tel:{telefon_ham}",
                "yol_tarifi": "",
                "nobet_durumu": "Sabaha kadar açık",
                "enlem": enlem,
                "boylam": boylam,
                "kaynak": "eczaneler_gen_tr"
            })

        if sonuclar:
            return sonuclar, ""
        return [], "eczaneler.gen.tr ayrıştırılabilir kart bulamadı"
    except Exception as err:
        return [], f"eczaneler.gen.tr çekim hatası: {str(err)}"


# ==========================================
# 3. ÜÇÜNCÜL KAYNAK: NOBETCIECZANELER.ORG (YÜKSEK BAŞARI ORANI)
# ==========================================
def kaynak_nobetcieczaneler_org_cek(il: str, ilce: str) -> Tuple[List[Dict[str, Any]], str]:
    """
    nobetcieczaneler.org sitesinden güncel nöbetçi eczaneleri ve koordinatları çeker.
    """
    il_slug = turkce_karakter_temizle(il)
    ilce_slug = turkce_karakter_temizle(ilce)
    hedef_url = f"https://www.nobetcieczaneler.org/nobetci-eczane/{il_slug}/{ilce_slug}"

    try:
        yanit = requests.get(hedef_url, headers=TARAYICI_BASLIKLARI, timeout=12, allow_redirects=True)
        if yanit.status_code != 200:
            # Alternatif eski URL yapısını dene
            eski_url = f"https://www.nobetcieczaneler.org/{il_slug}/{ilce_slug}"
            yanit = requests.get(eski_url, headers=TARAYICI_BASLIKLARI, timeout=12, allow_redirects=True)
            if yanit.status_code != 200:
                return [], f"nobetcieczaneler.org HTTP {yanit.status_code}"

        icerik = yanit.text

        # JSON-LD Ayrıştırma
        json_ld_eczaneler = {}
        json_bloklari = re.findall(r'<script type=["\']application/ld\+json["\']>(.*?)</script>', icerik, re.DOTALL)
        for blok in json_bloklari:
            try:
                veri = json.loads(blok)
                if isinstance(veri, dict) and veri.get("@type") == "ItemList":
                    for eleman in veri.get("itemListElement", []):
                        eczane_objesi = eleman.get("item", {})
                        eczane_adi = eczane_objesi.get("name", "").strip()
                        if eczane_adi:
                            anahtar = re.sub(r'\s+', ' ', eczane_adi.lower())
                            json_ld_eczaneler[anahtar] = eczane_objesi
            except Exception:
                continue

        # HTML Kartları Ayrıştırma
        kartlar = re.findall(
            r'<article[^>]*class=["\'][^"\']*nearest-pharmacy-card[^"\']*["\'][^>]*>(.*?)</article>',
            icerik,
            re.DOTALL
        )
        if not kartlar:
            kartlar = re.findall(
                r'<div[^>]*class=["\'][^"\']*nearest-pharmacy-card[^"\']*["\'][^>]*>(.*?)(?=<div[^>]*class=["\'][^"\']*nearest-pharmacy-card|<!-- end|$)',
                icerik,
                re.DOTALL
            )

        sonuc_listesi = []
        if kartlar:
            for sira, kart in enumerate(kartlar, start=1):
                isim_eslesme = re.search(r'<h3>.*?<a[^>]*>(.*?)</a>', kart, re.DOTALL) or re.search(r'<h3>(.*?)</h3>', kart, re.DOTALL)
                eczane_adi = html.unescape(isim_eslesme.group(1).strip()) if isim_eslesme else f"Eczane #{sira}"
                eczane_adi = re.sub(r'<[^>]+>', '', eczane_adi).strip()

                semt_eslesme = re.search(r'nearest-pharmacy-card__semt-tag[^>]*>\s*([^<]+)\s*<', kart)
                semt = html.unescape(semt_eslesme.group(1).strip()) if semt_eslesme else ""

                landmark_eslesme = re.search(r'nearest-pharmacy-card__landmark-name[^>]*>\s*([^<]+)\s*<', kart)
                mesafe_eslesme = re.search(r'nearest-pharmacy-card__landmark-distance[^>]*>\s*([^<]+)\s*<', kart)
                landmark = html.unescape(landmark_eslesme.group(1).strip()) if landmark_eslesme else ""
                mesafe = html.unescape(mesafe_eslesme.group(1).strip()) if mesafe_eslesme else ""
                mesafe = mesafe.replace('·', '').strip()
                yol_tarifi = f"{landmark} ({mesafe})" if (landmark and mesafe) else landmark

                nobet_eslesme = (
                    re.search(r'data-duty-active-label=["\']([^"\']+)["\']', kart) or
                    re.search(r'class=["\']duty-badge[^>]*>\s*([^<]+)\s*<', kart)
                )
                nobet_durumu = html.unescape(nobet_eslesme.group(1).strip()) if nobet_eslesme else "Sabaha kadar açık"

                anahtar = re.sub(r'\s+', ' ', eczane_adi.lower())
                json_detay = json_ld_eczaneler.get(anahtar, {})

                adres = ""
                if json_detay.get("address"):
                    adres = json_detay["address"].get("streetAddress", "")
                if not adres:
                    adres_eslesme = re.search(r'nearest-pharmacy-card__address[^>]*>\s*([^<]+)\s*<', kart)
                    adres = html.unescape(adres_eslesme.group(1).strip()) if adres_eslesme else f"{ilce.capitalize()}, {il.capitalize()}"

                telefon_ham = json_detay.get("telephone", "")
                if not telefon_ham:
                    tel_eslesme = re.search(r'nearest-pharmacy-card__phone[^>]*>\s*([^<]+)\s*<', kart)
                    telefon_ham = tel_eslesme.group(1).strip() if tel_eslesme else ""

                geo = json_detay.get("geo", {})
                enlem = geo.get("latitude")
                boylam = geo.get("longitude")

                sonuc_listesi.append({
                    "id": sira,
                    "isim": eczane_adi,
                    "il": il.capitalize(),
                    "ilce": ilce.capitalize(),
                    "semt": semt,
                    "adres": adres,
                    "telefon": telefon_formatla(telefon_ham),
                    "telefon_link": f"tel:{re.sub(r'[^0-9+]', '', telefon_ham)}" if telefon_ham else "",
                    "yol_tarifi": yol_tarifi,
                    "nobet_durumu": nobet_durumu,
                    "enlem": float(enlem) if enlem else None,
                    "boylam": float(boylam) if boylam else None,
                    "kaynak": "nobetcieczaneler_org"
                })

        if sonuc_listesi:
            return sonuc_listesi, ""
        return [], "nobetcieczaneler.org ayrıştırılabilir eczane kartı bulamadı"
    except Exception as err:
        return [], f"nobetcieczaneler.org hatası: {str(err)}"


# ==========================================
# 4. YEDEK VERİ MOTORU (OFFLINE FALLBACK)
# ==========================================
def yedek_veri_uret(il: str, ilce: str) -> List[Dict[str, Any]]:
    """Tüm dış kaynaklar kapalı olduğunda TV ekranının boş kalmasını önleyen acil durum veri seti."""
    return [
        {
            "id": 1,
            "isim": "Örnek Emre Eczanesi",
            "il": il.capitalize(),
            "ilce": ilce.capitalize(),
            "semt": "Siyavuşpaşa",
            "adres": f"Siyavuşpaşa Mahallesi, Çamlık Caddesi No: 3/2 {ilce.capitalize()} / {il.capitalize()}",
            "telefon": "0212 556 67 80",
            "telefon_link": "tel:02125566780",
            "yol_tarifi": "QNB Finansbank Yakını (~160m)",
            "nobet_durumu": "Sabaha kadar açık",
            "enlem": 41.004626,
            "boylam": 28.857539,
            "kaynak": "fallback_offline"
        },
        {
            "id": 2,
            "isim": "Örnek Has Eczanesi",
            "il": il.capitalize(),
            "ilce": ilce.capitalize(),
            "semt": "Merkez",
            "adres": f"Merkez Mahallesi, Kültür Sokak No: 2/B {ilce.capitalize()} / {il.capitalize()}",
            "telefon": "0212 442 24 77",
            "telefon_link": "tel:02124422477",
            "yol_tarifi": "Gloria Jean's Yanı (~15m)",
            "nobet_durumu": "Sabaha kadar açık",
            "enlem": 40.995927,
            "boylam": 28.863768,
            "kaynak": "fallback_offline"
        }
    ]


# ==========================================
# ANA FALLBACK PIPELINE SERVİSİ
# ==========================================
def nobetci_eczaneleri_getir(il: str, ilce: str,
                             kendi_enlem: Optional[float] = None,
                             kendi_boylam: Optional[float] = None) -> Dict[str, Any]:
    """
    Sıralı Fallback Pipeline:
    1. İEO (Resmi) -> 2. eczaneler.gen.tr -> 3. nobetcieczaneler.org -> 4. Cache / Fallback
    Mesafeleri, yürüme sürelerini ve rota linklerini otomatik hesaplayarak döndürür.
    En az 45 dakika boyunca yerel bellekte saklar.
    """
    onbellek_anahtari = f"{turkce_karakter_temizle(il)}_{turkce_karakter_temizle(ilce)}"
    suan = time.time()

    # 1. Aşama: Geçerli önbellek kontrolü (45 dakika)
    with onbellek_kilidi:
        kayit = onbellek_deposu.get(onbellek_anahtari)
        if kayit and (suan - kayit["timestamp"] < CACHE_SURESI_SANIYE):
            eczaneler = _mesafe_ve_rotalari_zenginlestir(kayit["data"], kendi_enlem, kendi_boylam)
            return {
                "success": True,
                "eczaneler": eczaneler,
                "kaynak": "cache",
                "veri_saglayici": kayit.get("veri_saglayici", "onbellek"),
                "onbellek_zamani": kayit["formatted_time"],
                "gecikme_saniye": int(suan - kayit["timestamp"]),
                "il": il.capitalize(),
                "ilce": ilce.capitalize()
            }

    # 2. Aşama: Sıralı Kaynak Denemeleri (Fallback Pipeline)
    basarili_eczaneler: List[Dict[str, Any]] = []
    kullanilan_kaynak = ""
    hata_raporu = []

    # Kaynak 1: İEO (Resmi Oda)
    eczaneler, hata1 = kaynak_ieo_cek(il, ilce)
    if eczaneler:
        basarili_eczaneler = eczaneler
        kullanilan_kaynak = "ieo_resmi"
    else:
        hata_raporu.append(f"İEO: {hata1}")

    # Kaynak 2: eczaneler.gen.tr (Eğer 1 başarısız olduysa)
    if not basarili_eczaneler:
        eczaneler, hata2 = kaynak_eczaneler_gen_tr_cek(il, ilce)
        if eczaneler:
            basarili_eczaneler = eczaneler
            kullanilan_kaynak = "eczaneler_gen_tr"
        else:
            hata_raporu.append(f"eczaneler.gen.tr: {hata2}")

    # Kaynak 3: nobetcieczaneler.org (Eğer 1 ve 2 başarısız olduysa)
    if not basarili_eczaneler:
        eczaneler, hata3 = kaynak_nobetcieczaneler_org_cek(il, ilce)
        if eczaneler:
            basarili_eczaneler = eczaneler
            kullanilan_kaynak = "nobetcieczaneler_org"
        else:
            hata_raporu.append(f"nobetcieczaneler.org: {hata3}")

    zaman_metni = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    # Başarılı veri çekildiyse önbelleğe kaydet
    if basarili_eczaneler:
        with onbellek_kilidi:
            onbellek_deposu[onbellek_anahtari] = {
                "timestamp": suan,
                "formatted_time": zaman_metni,
                "data": basarili_eczaneler,
                "veri_saglayici": kullanilan_kaynak
            }
        zenginlestirilmis = _mesafe_ve_rotalari_zenginlestir(basarili_eczaneler, kendi_enlem, kendi_boylam)
        return {
            "success": True,
            "eczaneler": zenginlestirilmis,
            "kaynak": "live",
            "veri_saglayici": kullanilan_kaynak,
            "onbellek_zamani": zaman_metni,
            "gecikme_saniye": 0,
            "il": il.capitalize(),
            "ilce": ilce.capitalize()
        }

    # Eğer canlı kaynaklar başarısız olduysa ve bayat önbellek varsa onu kullan
    with onbellek_kilidi:
        if kayit and kayit.get("data"):
            zenginlestirilmis = _mesafe_ve_rotalari_zenginlestir(kayit["data"], kendi_enlem, kendi_boylam)
            return {
                "success": True,
                "eczaneler": zenginlestirilmis,
                "kaynak": "stale_cache",
                "veri_saglayici": kayit.get("veri_saglayici", "eski_onbellek"),
                "hata_detayi": " | ".join(hata_raporu),
                "onbellek_zamani": kayit["formatted_time"],
                "gecikme_saniye": int(suan - kayit["timestamp"]),
                "il": il.capitalize(),
                "ilce": ilce.capitalize()
            }

    # Hiçbir şey yoksa acil durum offline yedeği
    yedek = yedek_veri_uret(il, ilce)
    zenginlestirilmis = _mesafe_ve_rotalari_zenginlestir(yedek, kendi_enlem, kendi_boylam)
    return {
        "success": True,
        "eczaneler": zenginlestirilmis,
        "kaynak": "fallback_offline",
        "veri_saglayici": "acil_durum_yedegi",
        "hata_detayi": " | ".join(hata_raporu),
        "onbellek_zamani": zaman_metni,
        "gecikme_saniye": 0,
        "il": il.capitalize(),
        "ilce": ilce.capitalize()
    }


def _mesafe_ve_rotalari_zenginlestir(eczaneler: List[Dict[str, Any]],
                                     kendi_enlem: Optional[float] = None,
                                     kendi_boylam: Optional[float] = None) -> List[Dict[str, Any]]:
    """
    Her eczane kartına dinamik Google Haritalar linki, doğrudan Rota linki,
    dinamik rota QR kod linki ve mesafe/yürüme süresi bilgilerini ekler.
    """
    zengin_liste = []
    for e in eczaneler:
        item = dict(e)
        hedef_lat = item.get("enlem")
        hedef_lon = item.get("boylam")

        # Rota ve Harita linkleri
        harita_linki = f"https://www.google.com/maps/search/?api=1&query={hedef_lat},{hedef_lon}" if (hedef_lat and hedef_lon) else (
            f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(item.get('isim', '') + ' ' + item.get('adres', ''))}"
        )

        rota_linki = rota_linki_olustur(
            hedef_enlem=hedef_lat,
            hedef_boylam=hedef_lon,
            eczane_adi=item.get("isim", ""),
            adres=item.get("adres", ""),
            kendi_enlem=kendi_enlem,
            kendi_boylam=kendi_boylam
        )

        # Rota veya Harita QR Kodu
        qr_kod_url = qr_kod_url_olustur(rota_linki)

        # Mesafe ve Yürüme Süresi Hesabı
        mesafe_metin = ""
        yurume_metin = ""
        mesafe_metre_deger = None

        if kendi_enlem and kendi_boylam and hedef_lat and hedef_lon:
            mesafe_metre_deger = haversine_mesafe(kendi_enlem, kendi_boylam, hedef_lat, hedef_lon)
            mesafe_metin, yurume_metin = mesafe_ve_yurume_hesapla(mesafe_metre_deger)

        item["harita_linki"] = harita_linki
        item["rota_linki"] = rota_linki
        item["qr_kod_url"] = qr_kod_url
        item["mesafe_metre"] = int(mesafe_metre_deger) if mesafe_metre_deger else None
        item["mesafe_metin"] = mesafe_metin
        item["yurume_metin"] = yurume_metin

        zengin_liste.append(item)

    return zengin_liste
