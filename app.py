"""
Nöbetçi Eczane TV Bilgi Ekranı & Yönetim Paneli (Multi-Tenant SaaS)
Backend Servisi - Flask, SQLAlchemy & Gunicorn
"""

import os
import time
import json
import re
import html
import urllib.parse
from datetime import datetime, timedelta
from functools import wraps
from threading import Lock
from typing import Tuple, List, Dict, Any

from flask import (
    Flask, render_template, jsonify, request, 
    redirect, url_for, session, flash
)
import requests

from models import db, AdminUser, Pharmacy, lisans_anahtari_uret

# Flask uygulamasının başlatılması
app = Flask(__name__)

# Oturum ve güvenlik yapılandırması
app.secret_key = os.getenv("SECRET_KEY", "eczane-kiosk-secret-key-2026-secure-random")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

# SQLite Veritabanı Yolu Belirleme (Kalıcı /data dizini veya yerel fallback)
VARSAYILAN_DATA_DIR = "/data"
if not os.path.exists(VARSAYILAN_DATA_DIR):
    try:
        os.makedirs(VARSAYILAN_DATA_DIR, exist_ok=True)
    except Exception:
        # Konteyner dışı yerel geliştirme için fallback
        VARSAYILAN_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(VARSAYILAN_DATA_DIR, exist_ok=True)

DB_PATH = os.getenv("DATABASE_PATH", os.path.join(VARSAYILAN_DATA_DIR, "app.db"))
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Veritabanını Flask uygulamasına bağlama
db.init_app(app)

# Yapılandırma ve ortam değişkenleri
VARSAYILAN_IL = os.getenv("DEFAULT_PROVINCE", "istanbul").lower().strip()
VARSAYILAN_ILCE = os.getenv("DEFAULT_DISTRICT", "bahcelievler").lower().strip()
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
CACHE_SURESI = int(os.getenv("CACHE_TTL", "2700"))

# Bellek içi (in-memory) önbellek deposu ve thread güvenliği kilidi
onbellek_deposu = {}
onbellek_kilidi = Lock()


# ==========================================
# Kimlik Doğrulama Dekoratörü (Auth Helper)
# ==========================================
def login_required(f):
    """
    Yönetim paneli rotalarını koruyan oturum kontrol dekoratörü.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "admin_logged_in" not in session or not session["admin_logged_in"]:
            flash("Lütfen önce giriş yapınız.", "warning")
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated_function


# ==========================================
# Veritabanı ve İlk Başlatma Rutini
# ==========================================
def init_db():
    """
    Uygulama açılışında veritabanı tablolarını ve varsayılan yöneticiyi oluşturur.
    """
    with app.app_context():
        db.create_all()
        # Varsayılan yönetici hesabı kontrolü
        admin = AdminUser.query.filter_by(username=ADMIN_USER).first()
        if not admin:
            yeni_admin = AdminUser(username=ADMIN_USER)
            yeni_admin.sifre_belirle(ADMIN_PASSWORD)
            db.session.add(yeni_admin)
            db.session.commit()
            print(f"[BİLGİ] Varsayılan yönetici oluşturuldu: {ADMIN_USER}")

        # Eğer hiç eczane yoksa demo eczane kaydı aç
        if Pharmacy.query.count() == 0:
            demo_eczane = Pharmacy(
                name="Çakırlar Eczanesi",
                city="İstanbul",
                district="Bahçelievler",
                latitude=40.9985,
                longitude=28.8650,
                license_key="ECZ-CAKIRLAR-001",
                expires_at=datetime.now() + timedelta(days=365),
                is_active=True,
                ticker_text="Çakırlar Eczanesi sağlıklı günler diler. Reçeteli ilaçlarınız ve medikal ihtiyaçlarınız için danışabilirsiniz."
            )
            db.session.add(demo_eczane)
            db.session.commit()
            print("[BİLGİ] Demo eczane oluşturuldu: Çakırlar Eczanesi (ECZ-CAKIRLAR-001)")


# ==========================================
# Yardımcı Fonksiyonlar & Scraper
# ==========================================
def turkce_karakter_temizle(metin: str) -> str:
    """
    Türkçe karakterleri URL ve slug uyumlu standart ASCII karakterlerine dönüştürür.
    Büyük 'İ' ve 'I' harflerinin Unicode combining dot hatasını önler.
    """
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
    """Gelen ham telefon numarasını okunabilir biçime getirir."""
    if not telefon_ham:
        return ""
    rakamlar = re.sub(r'\D', '', telefon_ham)
    if len(rakamlar) == 10:
        rakamlar = "0" + rakamlar
    if len(rakamlar) == 11 and rakamlar.startswith("0"):
        return f"{rakamlar[:4]} {rakamlar[4:7]} {rakamlar[7:9]} {rakamlar[9:]}"
    return telefon_ham


def harita_linki_olustur(isim: str, adres: str, enlem: float = None, boylam: float = None) -> str:
    """Eczane için Google Haritalar linki oluşturur."""
    if enlem and boylam:
        return f"https://www.google.com/maps/search/?api=1&query={enlem},{boylam}"
    arama_sorgusu = urllib.parse.quote(f"{isim} {adres}")
    return f"https://www.google.com/maps/search/?api=1&query={arama_sorgusu}"


def qr_kod_url_olustur(hedef_url: str) -> str:
    """Dinamik QR kod görsel URL'si üretir."""
    encoded_url = urllib.parse.quote(hedef_url)
    return f"https://api.qrserver.com/v1/create-qr-code/?size=180x180&data={encoded_url}&margin=6"


def istemci_ip_al() -> str:
    """
    İstemcinin gerçek IP adresini tespit eder.
    Reverse proxy (Coolify, Cloudflare, Nginx) arkasındayken X-Forwarded-For başlığını okur.
    """
    if request.headers.get("X-Forwarded-For"):
        return request.headers.get("X-Forwarded-For").split(",")[0].strip()
    if request.headers.get("CF-Connecting-IP"):
        return request.headers.get("CF-Connecting-IP").strip()
    return request.remote_addr or "Bilinmiyor"


def yedek_veri_uret(il: str, ilce: str) -> list:
    """Dış servis kesintilerinde ekranın boş kalmaması için örnek kurtarma verisi seti."""
    return [
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
        }
    ]


def nobetci_eczaneleri_cek(il: str = "istanbul", ilce: str = "bahcelievler") -> Tuple[list, str]:
    """Hedef kaynaktan güncel nöbetçi eczaneleri web kazıma yöntemiyle çeker."""
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
            return [], f"Kaynak site HTTP {yanit.status_code} döndü"

        icerik = yanit.text

        # JSON-LD Ayrıştırma
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
                            anahtar = re.sub(r'\s+', ' ', eczane_adi.lower())
                            json_ld_eczaneler[anahtar] = eczane_objesi
            except Exception:
                continue

        # HTML Kartları Ayrıştırma
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
                    re.search(r'data-duty-active-label=[\"\']([^\"\']+)[\"\']', kart) or
                    re.search(r'class=[\"\']duty-badge[^>]*>\s*([^<]+)\s*<', kart)
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

                telefon = telefon_formatla(telefon_ham)
                telefon_link = f"tel:{re.sub(r'[^0-9+]', '', telefon_ham)}" if telefon_ham else ""

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

        if sonuc_listesi:
            return sonuc_listesi, ""
        return [], "Nöbetçi eczane bulunamadı"

    except Exception as hata:
        return [], str(hata)


def veri_getir_onbellekli(il: str, ilce: str) -> dict:
    """Önbellekli nöbetçi eczane veri motoru."""
    onbellek_anahtari = f"{turkce_karakter_temizle(il)}_{turkce_karakter_temizle(ilce)}"
    suan = time.time()

    with onbellek_kilidi:
        kayit = onbellek_deposu.get(onbellek_anahtari)
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
# YÖNETİM PANELİ ROTALARI (/admin)
# ==========================================

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """Yönetici giriş sayfası."""
    if request.method == "POST":
        kullanici_adi = request.form.get("username", "").strip()
        sifre = request.form.get("password", "").strip()

        admin = AdminUser.query.filter_by(username=kullanici_adi).first()
        if admin and admin.sifre_kontrol(sifre):
            session["admin_logged_in"] = True
            session["admin_username"] = admin.username
            flash("Başarıyla giriş yapıldı.", "success")
            return redirect(url_for("admin_dashboard"))
        else:
            flash("Kullanıcı adı veya şifre hatalı!", "danger")

    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    """Yönetici çıkış işlemi."""
    session.pop("admin_logged_in", None)
    session.pop("admin_username", None)
    flash("Oturum kapatıldı.", "info")
    return redirect(url_for("admin_login"))


@app.route("/admin")
@login_required
def admin_dashboard():
    """Yönetim paneli ana kontrol ekranı (Eczane listesi, istatistikler ve heartbeat)."""
    eczaneler = Pharmacy.query.order_by(Pharmacy.id.desc()).all()
    
    toplam_sayi = len(eczaneler)
    aktif_sayi = sum(1 for e in eczaneler if e.lisans_gecerli_mi())
    cevrimici_sayi = sum(1 for e in eczaneler if e.ekran_cevrimici_mi())

    # Host ve protokol bilgisi (kiosk URL kopyalama için)
    base_url = request.host_url.rstrip("/")

    return render_template(
        "admin_dashboard.html",
        eczaneler=eczaneler,
        toplam_sayi=toplam_sayi,
        aktif_sayi=aktif_sayi,
        cevrimici_sayi=cevrimici_sayi,
        base_url=base_url
    )


@app.route("/admin/pharmacy/add", methods=["POST"])
@login_required
def admin_add_pharmacy():
    """Yeni eczane ve lisans kaydı oluşturma."""
    isim = request.form.get("name", "").strip()
    sehir = request.form.get("city", "İstanbul").strip()
    ilce = request.form.get("district", "Bahçelievler").strip()
    enlem = request.form.get("latitude", type=float)
    boylam = request.form.get("longitude", type=float)
    kayan_yazi = request.form.get("ticker_text", "").strip()
    lisans_gun = request.form.get("license_days", 365, type=int)

    if not isim:
        flash("Eczane adı zorunludur!", "danger")
        return redirect(url_for("admin_dashboard"))

    yeni_eczane = Pharmacy(
        name=isim,
        city=sehir,
        district=ilce,
        latitude=enlem,
        longitude=boylam,
        license_key=lisans_anahtari_uret(),
        expires_at=datetime.now() + timedelta(days=lisans_gun),
        is_active=True,
        ticker_text=kayan_yazi or f"{isim} sağlıklı günler diler."
    )
    db.session.add(yeni_eczane)
    db.session.commit()

    flash(f"'{isim}' başarıyla eklendi. Lisans Anahtarı: {yeni_eczane.license_key}", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/pharmacy/<int:eczane_id>/edit", methods=["POST"])
@login_required
def admin_edit_pharmacy(eczane_id):
    """Mevcut eczane bilgilerini düzenleme."""
    eczane = Pharmacy.query.get_or_404(eczane_id)

    eczane.name = request.form.get("name", eczane.name).strip()
    eczane.city = request.form.get("city", eczane.city).strip()
    eczane.district = request.form.get("district", eczane.district).strip()
    eczane.latitude = request.form.get("latitude", eczane.latitude, type=float)
    eczane.longitude = request.form.get("longitude", eczane.longitude, type=float)
    eczane.ticker_text = request.form.get("ticker_text", eczane.ticker_text).strip()

    bitis_str = request.form.get("expires_at", "")
    if bitis_str:
        try:
            eczane.expires_at = datetime.strptime(bitis_str, "%Y-%m-%d")
        except ValueError:
            pass

    db.session.commit()
    flash(f"'{eczane.name}' bilgileri güncellendi.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/pharmacy/<int:eczane_id>/toggle", methods=["POST"])
@login_required
def admin_toggle_pharmacy(eczane_id):
    """Eczane lisansını tek tıkla aktif/pasif yapma."""
    eczane = Pharmacy.query.get_or_404(eczane_id)
    eczane.is_active = not eczane.is_active
    db.session.commit()

    durum = "Aktif" if eczane.is_active else "Pasif"
    flash(f"'{eczane.name}' lisans durumu '{durum}' olarak değiştirildi.", "info")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/pharmacy/<int:eczane_id>/extend", methods=["POST"])
@login_required
def admin_extend_pharmacy(eczane_id):
    """Lisans süresini uzatma (+30 gün veya +365 gün)."""
    eczane = Pharmacy.query.get_or_404(eczane_id)
    eklenecek_gun = request.form.get("days", 30, type=int)

    # Eğer süresi zaten dolmuşsa bugünden itibaren ekle, dolmamışsa mevcut tarihin üstüne ekle
    baslangic = eczane.expires_at if (eczane.expires_at and eczane.expires_at > datetime.now()) else datetime.now()
    eczane.expires_at = baslangic + timedelta(days=eklenecek_gun)
    eczane.is_active = True
    db.session.commit()

    flash(f"'{eczane.name}' lisansı {eklenecek_gun} gün uzatıldı. Yeni Bitiş: {eczane.expires_at.strftime('%d.%m.%Y')}", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/pharmacy/<int:eczane_id>/delete", methods=["POST"])
@login_required
def admin_delete_pharmacy(eczane_id):
    """Eczaneyi ve lisansını silme."""
    eczane = Pharmacy.query.get_or_404(eczane_id)
    isim = eczane.name
    db.session.delete(eczane)
    db.session.commit()

    flash(f"'{isim}' başarıyla silindi.", "warning")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/pharmacy/<int:eczane_id>/reset-device", methods=["POST"])
@login_required
def admin_reset_device(eczane_id):
    """
    Eczanenin TV ekranı cihaz kilidini sıfırlar.
    Böylece yeni bir TV veya tarayıcı bağlandığında o cihaza kilitlenir.
    """
    eczane = Pharmacy.query.get_or_404(eczane_id)
    eczane.cihaz_kilidi_sifirla()
    db.session.commit()

    flash(f"'{eczane.name}' TV cihaz kilidi sıfırlandı. Yeni bağlanacak ilk TV cihazına kilitlenecektir.", "info")
    return redirect(url_for("admin_dashboard"))


# ==========================================
# KIOSK EKRANI VE LİSANS DOĞRULAMA ROTALARI
# ==========================================

@app.route("/kiosk")
def kiosk():
    """
    Lisans anahtarlı TV Kiosk ekranı.
    /kiosk?key=ECZ-XXXX-XXXX
    """
    key = request.args.get("key", "").strip()
    device_token = request.args.get("device_token", "").strip()

    if not key:
        return render_template(
            "kiosk_error.html",
            hata_baslik="Lisans Anahtarı Eksik",
            hata_mesaj="TV ekranının çalışabilmesi için geçerli bir lisans anahtarı belirtilmelidir.",
            lisans_kodu="Belirtilmedi"
        )

    eczane = Pharmacy.query.filter_by(license_key=key).first()
    if not eczane:
        return render_template(
            "kiosk_error.html",
            hata_baslik="Geçersiz Lisans",
            hata_mesaj="Belirtilen lisans anahtarı sistemde kayıtlı bulunamadı.",
            lisans_kodu=key
        )

    if not eczane.lisans_gecerli_mi():
        return render_template(
            "kiosk_error.html",
            hata_baslik="Lisans Süresi Doldu",
            hata_mesaj="Bu ekranın lisans süresi dolmuştur veya askıya alınmıştır. Yenilemek için lütfen sağlayıcınızla iletişime geçin.",
            lisans_kodu=key
        )

    # Eğer URL'den device_token gönderildiyse ve cihaz kilitliyse kontrol et
    if device_token and not eczane.cihaz_uyumlu_mu(device_token):
        return render_template(
            "kiosk_error.html",
            hata_baslik="Cihaz Kilidi Engeli",
            hata_mesaj="Bu lisans anahtarı başka bir TV cihazına kilitlenmiştir. Sistem güvenliği gereği aynı lisans birden fazla cihazda açılamaz.",
            lisans_kodu=key
        )

    return render_template(
        "kiosk.html",
        eczane=eczane,
        secili_il=eczane.city,
        secili_ilce=eczane.district,
        lisans_anahtari=eczane.license_key
    )


@app.route("/api/kiosk-data")
def api_kiosk_data():
    """
    Kiosk TV ekranının 15 dakikada bir veri çektiği, heartbeat attığı ve cihaz kilidi denetlediği API.
    """
    key = request.args.get("key", "").strip()
    device_token = request.args.get("device_token", "").strip()

    if not key:
        return jsonify({
            "success": False,
            "license_valid": False,
            "reason": "missing_key",
            "message": "Lisans anahtarı eksik."
        }), 400

    eczane = Pharmacy.query.filter_by(license_key=key).first()
    if not eczane:
        return jsonify({
            "success": False,
            "license_valid": False,
            "reason": "invalid_key",
            "message": "Lisans anahtarı geçersiz."
        }), 403

    if not eczane.lisans_gecerli_mi():
        return jsonify({
            "success": False,
            "license_valid": False,
            "reason": "expired",
            "message": "Lisans süresi doldu veya pasif duruma getirildi.",
            "license_key": eczane.license_key
        }), 403

    # Cihaz Kilidi Kontrolü (Kaçak çoğaltmayı ve birden çok ekranda açmayı önleme)
    if device_token:
        if not eczane.cihaz_uyumlu_mu(device_token):
            return jsonify({
                "success": False,
                "license_valid": False,
                "reason": "device_mismatch",
                "message": "Bu lisans anahtarı başka bir TV cihazına kilitlenmiştir. Lisansınızı tek ekranda kullanabilirsiniz."
            }), 403

    # Canlılık zaman damgasını ve TV ekranının IP adresini güncelle
    eczane.last_ping = datetime.now()
    eczane.last_ip = istemci_ip_al()
    db.session.commit()

    # İlgili ilçenin nöbetçi eczanelerini çek
    veri = veri_getir_onbellekli(eczane.city, eczane.district)

    # Bu gece nöbetçi miyiz kontrolü
    bu_gece_nobetci = False
    eczane_adi_norm = turkce_karakter_temizle(eczane.name)
    for e in veri.get("eczaneler", []):
        gelen_norm = turkce_karakter_temizle(e.get("isim", ""))
        # İsim eşleşmesi (örn: "cakirlar" kelimesi)
        if (eczane_adi_norm in gelen_norm) or (gelen_norm in eczane_adi_norm):
            bu_gece_nobetci = True
            break
        # Koordinat yakınlığı eşleşmesi (50 metreden yakınsa)
        if eczane.latitude and eczane.longitude and e.get("enlem") and e.get("boylam"):
            fark_lat = abs(eczane.latitude - e["enlem"])
            fark_lng = abs(eczane.longitude - e["boylam"])
            if fark_lat < 0.001 and fark_lng < 0.001:
                bu_gece_nobetci = True
                break

    return jsonify({
        "success": True,
        "license_valid": True,
        "pharmacy": {
            "id": eczane.id,
            "name": eczane.name,
            "city": eczane.city,
            "district": eczane.district,
            "ticker_text": eczane.ticker_text
        },
        "is_on_duty_today": bu_gece_nobetci,
        "eczaneler": veri.get("eczaneler", []),
        "kaynak": veri.get("kaynak"),
        "onbellek_zamani": veri.get("onbellek_zamani")
    })


# ==========================================
# GÜVENLİ KÖK ROTA VE LİSANS PORTALI
# ==========================================

@app.route("/")
def index():
    """
    Kök dizin rotası.
    Eğer URL'de lisans anahtarı varsa doğrudan Kiosk ekranına yönlendirir,
    lisanssız girişlerde lisans sorgulama ve kurulum portalını açar.
    """
    key = request.args.get("key", "").strip()
    if key:
        return redirect(url_for("kiosk", key=key))
    return render_template("portal.html")


@app.route("/api/nobetci-eczaneler")
def api_nobetci_eczaneler():
    """
    Genel API uç noktası koruması.
    Yetkisiz veri çekimini engeller, lisans anahtarı zorunludur.
    """
    key = request.args.get("key", "").strip()
    if not key:
        return jsonify({
            "success": False,
            "error": "Bu API ticari Kiosk sistemine aittir. Erişim için geçerli bir lisans anahtarı (key) zorunludur.",
            "usage": "/api/kiosk-data?key=ECZ-XXXX-XXXX"
        }), 403
    return api_kiosk_data()


@app.route("/api/health")
def api_health():
    """Konteyner sağlık kontrolü."""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "database": "connected"
    }), 200


# Uygulama ayağa kalktığında veritabanını başlat
init_db()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
