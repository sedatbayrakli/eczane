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
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import List, Dict, Any, Tuple, Optional
import requests

# Türkiye Saat Dilimi (UTC+3)
TZ_TR = timezone(timedelta(hours=3))

def turkiye_saati() -> datetime:
    """Türkiye yerel saatini (UTC+3, Europe/Istanbul) naive datetime olarak döner."""
    return (datetime.now(timezone.utc) + timedelta(hours=3)).replace(tzinfo=None)

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


def mesafe_ve_yurume_hesapla(mesafe_metre: float) -> Tuple[str, str, str]:
    """
    Mesafeyi '850 m' veya '2.4 km' formatında,
    ortalama yürüme süresini (4.5 km/saat -> dakikada ~75 metre)
    ve şehir içi araç sürüş süresini (30 km/saat -> dakikada ~500 metre) döndürür.
    """
    if mesafe_metre < 1000:
        mesafe_metin = f"{int(round(mesafe_metre))} mt"
    else:
        mesafe_metin = f"{mesafe_metre / 1000.0:.1f} km"

    yurume_dakika = max(1, int(round(mesafe_metre / 75.0)))
    yurume_metin = f"~{yurume_dakika} dk"

    araba_dakika = max(1, int(round(mesafe_metre / 500.0)))
    araba_metin = f"~{araba_dakika} dk"

    return mesafe_metin, yurume_metin, araba_metin


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
    """
    Yerel olarak bağımsız, çevrimdışı uyumlu QR Kod (Base64 PNG Data URI) üretir.
    İnternet kesildiğinde bile kiosk ekranında doğrudan yerel hafızadan (localStorage) gösterilir.
    """
    try:
        import qrcode
        import io
        import base64

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=5,
            border=2
        )
        qr.add_data(hedef_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception:
        # Fallback: Yerel kütüphane bulunamazsa dış URL
        encoded_url = urllib.parse.quote(hedef_url)
        return f"https://api.qrserver.com/v1/create-qr-code/?size=180x180&data={encoded_url}&margin=6"


# ==========================================
# 1. BİRİNCİL KAYNAK: İSTANBUL ECZACI ODASI (İEO) WEB GATE API
# ==========================================
_ieo_web_cache = {
    "token": "",
    "token_time": 0.0,
    "districts": {}
}
_ieo_web_lock = Lock()


def _ieo_web_boot_yukle():
    """İEO'nun güncel Web Gate API anahtarını (X-Web-Token) ve ilçe haritasını çeker / önbellekten döner."""
    with _ieo_web_lock:
        simdi_ts = time.time()
        if _ieo_web_cache["token"] and _ieo_web_cache["districts"] and (simdi_ts - _ieo_web_cache["token_time"] < 1200):
            return _ieo_web_cache["token"], _ieo_web_cache["token_time"], _ieo_web_cache["districts"]

        url_ana = "https://www.istanbuleczaciodasi.org.tr/nobetci-eczane/"
        session = requests.Session()
        session.headers.update(TARAYICI_BASLIKLARI)

        try:
            yanit_ana = session.get(url_ana, timeout=10)
            if yanit_ana.status_code == 200:
                boot_m = re.search(r'<script type=["\']application/json["\'] id=["\']boot["\']>(.*?)</script>', yanit_ana.text, re.DOTALL)
                if boot_m:
                    boot_data = json.loads(boot_m.group(1))
                    yeni_token = boot_data.get("token", "")
                    ham_ilceler = boot_data.get("districts", [])
                    ilce_sozlugu = {}
                    for d in ham_ilceler:
                        if isinstance(d, dict) and d.get("ilceId") and d.get("ilceAd"):
                            slug = turkce_karakter_temizle(d["ilceAd"])
                            ilce_sozlugu[slug] = {"id": d["ilceId"], "name": d["ilceAd"]}

                    if yeni_token and ilce_sozlugu:
                        _ieo_web_cache["token"] = yeni_token
                        _ieo_web_cache["token_time"] = simdi_ts
                        _ieo_web_cache["districts"] = ilce_sozlugu
                        return yeni_token, simdi_ts, ilce_sozlugu
        except Exception as e:
            print(f"[İEO Boot Hatası] {e}", flush=True)

        return _ieo_web_cache.get("token", ""), _ieo_web_cache.get("token_time", 0.0), _ieo_web_cache.get("districts", {})


def kaynak_ieo_cek(il: str, ilce: str, tarih: Optional[str] = None) -> Tuple[List[Dict[str, Any]], str]:
    """İstanbul Eczacı Odası resmi Web Gate API servisinden güncel nöbetçi eczaneleri çeker."""
    il_temiz = turkce_karakter_temizle(il)
    if il_temiz not in ("istanbul", "yalova"):
        return [], "İEO servisi yalnızca İstanbul ve Yalova için geçerlidir"

    ilce_slug = turkce_karakter_temizle(ilce)
    token, token_ts, ilce_haritasi = _ieo_web_boot_yukle()

    if not token or not ilce_haritasi:
        return [], "İEO oturum güvenlik anahtarı (Web Token) temin edilemedi"

    ilce_bilgisi = ilce_haritasi.get(ilce_slug)
    if not ilce_bilgisi:
        for k_slug, k_val in ilce_haritasi.items():
            if ilce_slug in k_slug or k_slug in ilce_slug:
                ilce_bilgisi = k_val
                break

    if not ilce_bilgisi:
        return [], f"İEO ilçe listesinde '{ilce}' bulunamadı"

    ilce_id = ilce_bilgisi["id"]
    api_url = f"https://www.istanbuleczaciodasi.org.tr/api/nobetci-eczane-web/?action=district&id={ilce_id}"
    api_headers = {
        "X-Web-Token": token,
        "Accept": "application/json",
        "Referer": "https://www.istanbuleczaciodasi.org.tr/nobetci-eczane/",
        "Origin": "https://www.istanbuleczaciodasi.org.tr",
        "User-Agent": TARAYICI_BASLIKLARI.get("User-Agent", "Mozilla/5.0")
    }

    try:
        session = requests.Session()
        resp = session.get(api_url, headers=api_headers, timeout=10)

        if resp.status_code in (401, 403) or (resp.status_code == 200 and resp.json().get("error", {}).get("code") == "token"):
            with _ieo_web_lock:
                _ieo_web_cache["token"] = ""
            token, token_ts, _ = _ieo_web_boot_yukle()
            api_headers["X-Web-Token"] = token
            resp = session.get(api_url, headers=api_headers, timeout=10)

        if resp.status_code != 200:
            return [], f"İEO Web Gate API HTTP {resp.status_code}"

        payload = resp.json()
        if not payload.get("success"):
            err_msg = payload.get("error", {}).get("message") or "İEO API hatası"
            return [], err_msg

        ham_eczaneler = payload.get("data", [])
        if not ham_eczaneler:
            return [], f"İEO '{ilce}' için nöbetçi eczane listesi boş döndü"

        # Token çok yeniyse (< 2.2 sn) 'too_fast' hatası almamak için bekle
        gecen = time.time() - token_ts
        if gecen < 2.2:
            time.sleep(max(0.1, 2.2 - gecen))

        sonuclar = []
        for sira, e in enumerate(ham_eczaneler, start=1):
            eczane_adi = (e.get("ad") or f"Eczane #{sira}").strip()
            semt = (e.get("mahalle") or "").strip()
            adres = (e.get("adres") or "").strip()
            yol_tarifi = (e.get("tarif") or "").strip()
            sicil = str(e.get("sicil", "")).strip()

            try:
                enlem = float(e.get("lat")) if e.get("lat") else None
                boylam = float(e.get("lng")) if e.get("lng") else None
            except (ValueError, TypeError):
                enlem, boylam = None, None

            telefon_ham = ""
            region_id = e.get("regionId")
            phone_ticket = e.get("phoneTicket")
            if region_id and phone_ticket:
                try:
                    p_body = {"regionId": region_id, "sicil": sicil, "ticket": phone_ticket}
                    p_resp = session.post(
                        "https://www.istanbuleczaciodasi.org.tr/api/nobetci-eczane-web/?action=phone",
                        json=p_body,
                        headers=api_headers,
                        timeout=5
                    )
                    if p_resp.status_code == 200 and p_resp.json().get("success"):
                        p_data = p_resp.json().get("data", {})
                        telefon_ham = p_data.get("tel") or p_data.get("tel2") or ""
                except Exception:
                    pass

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
                "sicil": sicil
            })

        return sonuclar, ""
    except Exception as err:
        return [], f"İEO çekim hatası: {str(err)}"


def _tarih_normalize(t_str: str) -> str:
    """Tarih dizgesini YYYY-MM-DD formatına normalize eder."""
    if not t_str:
        return ""
    t_str = t_str.strip()
    m1 = re.match(r'^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', t_str)
    if m1:
        return f"{m1.group(1)}-{int(m1.group(2)):02d}-{int(m1.group(3)):02d}"
    m2 = re.match(r'^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})', t_str)
    if m2:
        return f"{m2.group(3)}-{int(m2.group(2)):02d}-{int(m2.group(1)):02d}"
    return t_str


def kaynak_ieo_native_cek(sno: str, il: str = "İstanbul", ilce: Optional[str] = None,
                          tarih: Optional[str] = None, is_weekly: bool = False) -> Tuple[List[Dict[str, Any]], str]:
    """
    İstanbul Eczacı Odası resmi Kiosk API'sinden (bn.php) veri çeker.
    Parametreler:
        sno: Cihaz Seri Numarası / Kiosk Kodu (Zorunlu)
        is_weekly: True ise 'req=get_hn' (Haftalık Nöbetler), False ise 'req=get_bn' (Bugünkü Nöbetler)
        tarih: Belirli bir gün filtresi (YYYY-MM-DD)
    """
    if not sno or not sno.strip():
        return [], "İEO Kiosk API için Cihaz Seri Numarası (sno) gereklidir"

    sno_temiz = sno.strip()
    bugun_str = turkiye_saati().strftime("%Y-%m-%d")
    req_tur = "get_hn" if (is_weekly or (tarih and _tarih_normalize(tarih) != bugun_str)) else "get_bn"
    url = f"http://www.istanbuleczaciodasi.org.tr/android/com.ieo.eczanebilgiekrani/bn.php?req={req_tur}&sno={urllib.parse.quote(sno_temiz)}"

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 7.1.2; TV-BOX Build/NHG47K)",
        "Accept": "*/*"
    })

    try:
        yanit = session.get(url, timeout=10)
        if yanit.status_code != 200:
            return [], f"İEO Kiosk API HTTP {yanit.status_code}"

        try:
            veri = yanit.json()
        except Exception:
            temiz_metin = yanit.text.strip().lstrip('\ufeff')
            veri = json.loads(temiz_metin)

        if isinstance(veri, dict) and (veri.get("error") == 1 or veri.get("error") == "1"):
            hata_mesaji = veri.get("message") or "Cihaz seri no eşleşmiyor!"
            return [], f"İEO Cihaz Hatası: {hata_mesaji}"

        ham_eczaneler = veri.get("nobetciler", []) if isinstance(veri, dict) else []
        if not ham_eczaneler:
            return [], "İEO Kiosk servisinde nöbetçi eczane listesi bulunamadı"

        hedef_tarih_norm = _tarih_normalize(tarih) if tarih else ""
        ilce_norm = turkce_karakter_temizle(ilce) if ilce else ""

        sonuclar = []
        for sira, e in enumerate(ham_eczaneler, start=1):
            if not isinstance(e, dict):
                continue

            raw_tarih = str(e.get("tarih", "")).strip()
            e_tarih = _tarih_normalize(raw_tarih)

            # Tarih filtresi varsa eşleştir
            if hedef_tarih_norm and e_tarih and e_tarih != hedef_tarih_norm:
                continue

            # İlçe filtresi varsa eşleştir
            e_ilcesemt = str(e.get("ilce_semt", "") or e.get("ilcesemt", "")).strip()
            e_adres_raw = str(e.get("adres", "")).strip()

            if ilce_norm:
                e_ilcesemt_norm = turkce_karakter_temizle(e_ilcesemt)
                e_adres_norm = turkce_karakter_temizle(e_adres_raw)
                if (ilce_norm not in e_ilcesemt_norm) and (ilce_norm not in e_adres_norm):
                    continue

            eczane_adi = (e.get("eczane_ad") or e.get("eczane") or f"Eczane #{sira}").strip()
            semt = e_ilcesemt

            mahalle = (e.get("mahalle") or "").strip()
            cadde_sokak = (e.get("cadde_sokak") or "").strip()
            bina_kapi = (e.get("bina_kapi") or "").strip()
            adres = e_adres_raw
            if not adres and (mahalle or cadde_sokak or bina_kapi):
                adres = f"{mahalle} {cadde_sokak} {bina_kapi}".strip()
            adres = re.sub(r'<[^>]+>', '', adres).replace("Adres:", "").strip()

            telefon_ham = str(e.get("eczane_tel") or e.get("telefon") or "").strip()
            tarif = str(e.get("tarif") or "").strip()
            yol_tarifi = re.sub(r'<[^>]+>', '', tarif).replace("Tarif:", "").strip()

            try:
                enlem = float(e.get("lat")) if e.get("lat") else None
                boylam = float(e.get("lng") or e.get("lon")) if (e.get("lng") or e.get("lon")) else None
            except (ValueError, TypeError):
                enlem, boylam = None, None

            sonuclar.append({
                "id": sira,
                "isim": eczane_adi,
                "il": "İstanbul",
                "ilce": ilce.capitalize() if ilce else semt,
                "semt": semt,
                "adres": adres,
                "telefon": telefon_formatla(telefon_ham),
                "telefon_link": f"tel:{re.sub(r'[^0-9+]', '', telefon_ham)}" if telefon_ham else "",
                "yol_tarifi": yol_tarifi,
                "nobet_durumu": "Sabaha kadar açık",
                "enlem": enlem,
                "boylam": boylam,
                "kaynak": "ieo_kiosk_native",
                "sicil": str(e.get("sicil", "")).strip(),
                "tarih": e_tarih or tarih or "",
                "qrlink": str(e.get("qrlink", "")).strip()
            })

        if not sonuclar and (hedef_tarih_norm or ilce_norm):
            return [], f"{ilce or ''} için {tarih or ''} tarihinde İEO Kiosk kaydı bulunamadı"

        return sonuclar, ""
    except Exception as err:
        return [], f"İEO Kiosk API çekim hatası: {str(err)}"


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
def kaynak_eczaneler_gen_tr_cek(il: str, ilce: str, tarih: Optional[str] = None) -> Tuple[List[Dict[str, Any]], str]:
    """eczaneler.gen.tr sitesinden ilçe bazlı nöbetçi eczaneleri çeker."""
    from bs4 import BeautifulSoup

    il_slug = turkce_karakter_temizle(il)
    ilce_slug = turkce_karakter_temizle(ilce)
    hedef_url = f"https://www.eczaneler.gen.tr/nobetci-{il_slug}-{ilce_slug}"
    if tarih:
        hedef_url += f"?tarih={tarih}"

    try:
        yanit = requests.get(hedef_url, headers=TARAYICI_BASLIKLARI, timeout=10, allow_redirects=True)
        if yanit.status_code != 200:
            return [], f"eczaneler.gen.tr HTTP {yanit.status_code}"

        icerik = yanit.text
        if "cf-mitigated" in icerik or "challenges.cloudflare.com" in icerik:
            return [], "eczaneler.gen.tr Cloudflare challenge korumasına takıldı"

        soup = BeautifulSoup(icerik, "html.parser")
        isim_spanlari = soup.find_all("span", class_="isim")
        
        sonuclar = []
        for sira, span in enumerate(isim_spanlari, start=1):
            eczane_adi = span.get_text().strip()
            row = span.find_parent("div", class_="row")
            if not row:
                continue

            adres = ""
            tarif = ""
            adr_div = row.find("div", class_="col-lg-6")
            if adr_div:
                satirlar = [s.strip() for s in adr_div.get_text(separator='\n', strip=True).split('\n') if s.strip()]
                adres = satirlar[0] if len(satirlar) > 0 else ""
                if len(satirlar) > 1:
                    tarif = ' '.join(satirlar[1:])

            telefon_ham = ""
            for a_el in row.find_all("a"):
                href = a_el.get("href", "")
                if href.startswith("tel:"):
                    telefon_ham = href.replace("tel:", "").strip()
                    break
            if not telefon_ham:
                tel_m = re.search(r'0?\s*\(?2[0-9]{2}\)?[\s\-]?[0-9]{3}[\s\-]?[0-9]{2}[\s\-]?[0-9]{2}', row.get_text())
                if tel_m:
                    telefon_ham = tel_m.group(0).strip()

            enlem, boylam = None, None
            for a_el in row.find_all("a"):
                href = a_el.get("href", "")
                coord_m = re.search(r'query=([0-9\.]+),([0-9\.]+)', href)
                if coord_m:
                    enlem = float(coord_m.group(1))
                    boylam = float(coord_m.group(2))
                    break

            sonuclar.append({
                "id": sira,
                "isim": eczane_adi,
                "il": il.capitalize(),
                "ilce": ilce.capitalize(),
                "semt": "",
                "adres": adres,
                "telefon": telefon_formatla(telefon_ham),
                "telefon_link": f"tel:{re.sub(r'[^0-9+]', '', telefon_ham)}" if telefon_ham else "",
                "yol_tarifi": tarif,
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
def kaynak_nobetcieczaneler_org_cek(il: str, ilce: str, tarih: Optional[str] = None) -> Tuple[List[Dict[str, Any]], str]:
    """
    nobetcieczaneler.org sitesinden güncel nöbetçi eczaneleri ve koordinatları çeker.
    """
    il_slug = turkce_karakter_temizle(il)
    ilce_slug = turkce_karakter_temizle(ilce)
    hedef_url = f"https://www.nobetcieczaneler.org/nobetci-eczane/{il_slug}/{ilce_slug}"
    if tarih:
        hedef_url += f"?tarih={tarih}"

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
        },
        {
            "id": 3,
            "isim": "Örnek Yaşam Eczanesi",
            "il": il.capitalize(),
            "ilce": ilce.capitalize(),
            "semt": "Şirinevler",
            "adres": f"Şirinevler Mahallesi, Mareşal Fevzi Çakmak Caddesi No: 18/A {ilce.capitalize()} / {il.capitalize()}",
            "telefon": "0212 654 32 10",
            "telefon_link": "tel:02126543210",
            "yol_tarifi": "Şirinevler Meydanı Yakını",
            "nobet_durumu": "Sabaha kadar açık",
            "enlem": 40.998120,
            "boylam": 28.845620,
            "kaynak": "fallback_offline"
        },
        {
            "id": 4,
            "isim": "Örnek Hilal Eczanesi",
            "il": il.capitalize(),
            "ilce": ilce.capitalize(),
            "semt": "Yenibosna",
            "adres": f"Yenibosna Merkez Mahallesi, Yıldırım Beyazıt Caddesi No: 45 {ilce.capitalize()} / {il.capitalize()}",
            "telefon": "0212 551 90 20",
            "telefon_link": "tel:02125519020",
            "yol_tarifi": "Yenibosna Polis Merkezi Karşısı",
            "nobet_durumu": "Sabaha kadar açık",
            "enlem": 40.999540,
            "boylam": 28.829870,
            "kaynak": "fallback_offline"
        }
    ]


# ==========================================
# ANA FALLBACK PIPELINE SERVİSİ
# ==========================================
def onbellek_temizle():
    """Tüm il ve ilçelerin önbelleğini anında sıfırlar (Admin panelinden tetiklenir)."""
    with onbellek_kilidi:
        onbellek_deposu.clear()


def nobetci_eczaneleri_getir(il: str, ilce: str,
                             kendi_enlem: Optional[float] = None,
                             kendi_boylam: Optional[float] = None,
                             cache_suresi_dakika: Optional[int] = None,
                             kaynak_siralamasi: Optional[List[str]] = None,
                             tarih: Optional[str] = None,
                             sno: Optional[str] = None) -> Dict[str, Any]:
    """
    Sıralı Fallback Pipeline & İEO Kiosk Entegrasyonu:
    - Seri No (sno) doluysa: Resmi İEO Kiosk API'sinden (get_hn / get_bn) haftalık ve ileri/geçmiş tarihli sorgu yapar.
    - Seri No (sno) boşsa: Yalnızca bugünün güncel nöbetçileri sorgulanır; tarihli sorgulara izin verilmez.
    """
    today_str = turkiye_saati().strftime("%Y-%m-%d")
    aktif_tarih = _tarih_normalize(tarih) if tarih else today_str
    is_today = (aktif_tarih == today_str)

    # KURAL: Seri No boşsa, bugün dışındaki tarih sorgulamalarına izin verilmez
    if not is_today and not (sno and sno.strip()):
        return {
            "success": False,
            "error": "İleri veya geçmiş tarihli haftalık nöbet listelerini sorgulamak için İEO Cihaz Seri No / Kodu tanımlanmalıdır. Lütfen Genel Ayarlar veya Eczane Düzenleme bölümünden seri numaranızı tanımlayınız.",
            "sno_required": True,
            "eczaneler": [],
            "tarih": aktif_tarih,
            "il": il.capitalize(),
            "ilce": ilce.capitalize()
        }

    tarih_eki = f"_{turkce_karakter_temizle(aktif_tarih)}"
    sno_eki = f"_{turkce_karakter_temizle(sno[:8])}" if sno else ""
    onbellek_anahtari = f"{turkce_karakter_temizle(il)}_{turkce_karakter_temizle(ilce)}{tarih_eki}{sno_eki}"
    suan = time.time()
    cache_ttl_saniye = (cache_suresi_dakika * 60) if (cache_suresi_dakika and cache_suresi_dakika > 0) else CACHE_SURESI_SANIYE

    # 1. Aşama: Geçerli önbellek kontrolü
    with onbellek_kilidi:
        kayit = onbellek_deposu.get(onbellek_anahtari)
        if kayit and (suan - kayit["timestamp"] < cache_ttl_saniye):
            eczaneler = _mesafe_ve_rotalari_zenginlestir(kayit["data"], kendi_enlem, kendi_boylam)
            return {
                "success": True,
                "eczaneler": eczaneler,
                "kaynak": "cache",
                "veri_saglayici": kayit.get("veri_saglayici", "onbellek"),
                "tarih": aktif_tarih,
                "onbellek_zamani": kayit["formatted_time"],
                "gecikme_saniye": int(suan - kayit["timestamp"]),
                "il": il.capitalize(),
                "ilce": ilce.capitalize()
            }

    basarili_eczaneler: List[Dict[str, Any]] = []
    kullanilan_kaynak = ""
    hata_raporu = []

    # 2. Aşama: İleri/Geçmiş Tarih Sorgusu (SNO ile Haftalık Çekim)
    if not is_today and sno and sno.strip():
        eczaneler, hata = kaynak_ieo_native_cek(sno=sno.strip(), il=il, ilce=ilce, tarih=aktif_tarih, is_weekly=True)
        if eczaneler:
            basarili_eczaneler = eczaneler
            kullanilan_kaynak = "ieo_kiosk_native"
        else:
            hata_raporu.append(f"İEO Kiosk Haftalık: {hata}")
            return {
                "success": False,
                "error": f"İEO Haftalık Nöbet Çekim Hatası: {hata}",
                "sno_required": False,
                "eczaneler": [],
                "tarih": aktif_tarih,
                "il": il.capitalize(),
                "ilce": ilce.capitalize()
            }

    # 3. Aşama: Bugünün Sorgusu
    if is_today:
        # Eğer SNO varsa önce resmi native endpoint'i dene (get_bn)
        if sno and sno.strip():
            eczaneler, hata = kaynak_ieo_native_cek(sno=sno.strip(), il=il, ilce=ilce, tarih=aktif_tarih, is_weekly=False)
            if eczaneler:
                basarili_eczaneler = eczaneler
                kullanilan_kaynak = "ieo_kiosk_native"
            else:
                hata_raporu.append(f"İEO Kiosk Günlük: {hata}")

        # Eğer native ile çekilemediyse veya SNO yoksa sıralı web kaynaklarını dene
        if not basarili_eczaneler:
            kaynak_haritasi = {
                "ieo_resmi": ("İEO (İstanbul Eczacı Odası)", lambda _il, _ilce: kaynak_ieo_cek(_il, _ilce, tarih=aktif_tarih)),
                "eczaneler_gen_tr": ("Eczaneler.gen.tr", lambda _il, _ilce: kaynak_eczaneler_gen_tr_cek(_il, _ilce, tarih=aktif_tarih)),
                "nobetcieczaneler_org": ("Nobetcieczaneler.org", lambda _il, _ilce: kaynak_nobetcieczaneler_org_cek(_il, _ilce, tarih=aktif_tarih))
            }

            if not kaynak_siralamasi:
                kaynak_siralamasi = ["ieo_resmi", "eczaneler_gen_tr", "nobetcieczaneler_org"]

            for kaynak_anahtari in kaynak_siralamasi:
                if kaynak_anahtari in kaynak_haritasi:
                    kaynak_adi, kaynak_func = kaynak_haritasi[kaynak_anahtari]
                    eczaneler, hata = kaynak_func(il, ilce)
                    if eczaneler:
                        basarili_eczaneler = eczaneler
                        kullanilan_kaynak = kaynak_anahtari
                        break
                    else:
                        hata_raporu.append(f"{kaynak_adi}: {hata}")

    zaman_metni = turkiye_saati().strftime("%d.%m.%Y %H:%M:%S")

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
            "tarih": aktif_tarih,
            "onbellek_zamani": zaman_metni,
            "gecikme_saniye": 0,
            "il": il.capitalize(),
            "ilce": ilce.capitalize()
        }

    # Canlı kaynaklar başarısız olduysa ve bayat önbellek varsa onu kullan
    with onbellek_kilidi:
        if kayit and kayit.get("data"):
            zenginlestirilmis = _mesafe_ve_rotalari_zenginlestir(kayit["data"], kendi_enlem, kendi_boylam)
            return {
                "success": True,
                "eczaneler": zenginlestirilmis,
                "kaynak": "stale_cache",
                "veri_saglayici": kayit.get("veri_saglayici", "eski_onbellek"),
                "tarih": aktif_tarih,
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
        "tarih": aktif_tarih,
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

        # Mesafe, Yürüme ve Araç Süresi Hesabı
        mesafe_metin = ""
        yurume_metin = ""
        araba_metin = ""
        mesafe_metre_deger = None

        if kendi_enlem and kendi_boylam and hedef_lat and hedef_lon:
            mesafe_metre_deger = haversine_mesafe(kendi_enlem, kendi_boylam, hedef_lat, hedef_lon)
            mesafe_metin, yurume_metin, araba_metin = mesafe_ve_yurume_hesapla(mesafe_metre_deger)

        item["harita_linki"] = harita_linki
        item["rota_linki"] = rota_linki
        item["qr_kod_url"] = qr_kod_url
        item["mesafe_metre"] = int(mesafe_metre_deger) if mesafe_metre_deger else None
        item["mesafe_metin"] = mesafe_metin
        item["yurume_metin"] = yurume_metin
        item["araba_metin"] = araba_metin

        zengin_liste.append(item)

    # Lisans sahibi eczaneye en yakın nöbetçi eczaneyi 1. sıraya al (Küçükten büyüğe mesafe sıralaması)
    if kendi_enlem and kendi_boylam:
        zengin_liste.sort(key=lambda x: x["mesafe_metre"] if x.get("mesafe_metre") is not None else float("inf"))

    return zengin_liste


# ==========================================
# İSTANBUL ECZACI ODASI (İEO) RESMİ TATİL VE NÖBET TAKVİMİ
# ==========================================
_tatil_onbellegi: Dict[str, Any] = {"veri": None, "son_guncelleme": 0}
_tatil_kilidi = Lock()

BILINEN_TATILLER = {
    "01-01": "Yılbaşı Tatili",
    "04-23": "23 Nisan Ulusal Egemenlik ve Çocuk Bayramı",
    "05-01": "1 Mayıs Emek ve Dayanışma Günü",
    "05-19": "19 Mayıs Atatürk'ü Anma, Gençlik ve Spor Bayramı",
    "07-15": "15 Temmuz Demokrasi ve Milli Birlik Günü",
    "08-30": "30 Ağustos Zafer Bayramı",
    "10-28": "29 Ekim Cumhuriyet Bayramı Arifesi",
    "10-29": "29 Ekim Cumhuriyet Bayramı"
}

def ieo_resmi_tatil_bilgisi(tarih_str: Optional[str] = None) -> Dict[str, Any]:
    """
    İEO resmi mobil/TV sunucusundaki settings.json takvimini ve haftalık pazar günlerini inceler.
    Bugünün (veya verilen tarihin) resmi tatil, pazar veya arife günü olup olmadığını,
    nöbet başlangıç/bitiş saatlerini ve durum bilgisini döner.
    """
    simdi = turkiye_saati()
    if tarih_str:
        try:
            hedef_tarih = datetime.strptime(tarih_str, "%Y-%m-%d")
        except Exception:
            hedef_tarih = simdi
    else:
        hedef_tarih = simdi

    gun_key = hedef_tarih.strftime("%Y-%m-%d")
    ay_gun_key = hedef_tarih.strftime("%m-%d")
    is_sunday = (hedef_tarih.weekday() == 6) # Pazar

    # 1. İEO settings.json önbellek kontrolü (24 saatlik önbellek)
    fulldates_set = set()
    global _tatil_onbellegi
    suan_sn = time.time()
    
    with _tatil_kilidi:
        if not _tatil_onbellegi["veri"] or (suan_sn - _tatil_onbellegi["son_guncelleme"] > 86400):
            try:
                resp = requests.get(
                    "http://www.istanbuleczaciodasi.org.tr/android/com.ieo.eczanebilgiekrani/settings.json",
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=5
                )
                if resp.status_code == 200:
                    data = resp.json()
                    fulldates_raw = data.get("fulldates", "")
                    _tatil_onbellegi["veri"] = set(d.strip() for d in fulldates_raw.split("|") if d.strip())
                    _tatil_onbellegi["son_guncelleme"] = suan_sn
            except Exception as e:
                # Fallback: statik bilinen tatiller
                pass

        if _tatil_onbellegi.get("veri"):
            fulldates_set = _tatil_onbellegi["veri"]

    is_ieo_fulldate = (gun_key in fulldates_set)
    is_known_holiday = (ay_gun_key in BILINEN_TATILLER)

    is_holiday = is_sunday or is_ieo_fulldate or is_known_holiday
    is_arife = ("arife" in BILINEN_TATILLER.get(ay_gun_key, "").lower())

    holiday_name = ""
    if is_known_holiday:
        holiday_name = BILINEN_TATILLER[ay_gun_key]
    elif is_sunday:
        holiday_name = "Pazar Günü (Tam Gün Nöbet)"
    elif is_ieo_fulldate:
        holiday_name = "Resmi Tatil / Bayram Nöbeti"

    # Nöbet Saatleri Belirleme
    if is_arife:
        nobet_baslangic = "13:00"
        nobet_bitis = "09:00"
        aciklama = f"{holiday_name} nedeniyle nöbetçi eczaneler saat 13:00 itibarıyla hizmet vermektedir."
    elif is_holiday:
        nobet_baslangic = "09:00"
        nobet_bitis = "09:00"
        aciklama = f"{holiday_name} nedeniyle nöbetçi eczaneler tam gün kesintisiz hizmet vermektedir."
    else:
        nobet_baslangic = "19:00"
        nobet_bitis = "09:00"
        aciklama = "Eczaneler 08:30 - 19:00 arası açıktır. Nöbetçi eczaneler saat 19:00'dan itibaren hizmet vermektedir."

    return {
        "tarih": gun_key,
        "is_holiday": is_holiday,
        "is_sunday": is_sunday,
        "is_arife": is_arife,
        "holiday_name": holiday_name,
        "nobet_baslangic": nobet_baslangic,
        "nobet_bitis": nobet_bitis,
        "aciklama": aciklama
    }

