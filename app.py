"""
Nöbetçi Eczane TV Bilgi Ekranı & Yönetim Paneli (Multi-Tenant SaaS)
Backend Servisi - Flask, SQLAlchemy & Gunicorn
"""

import os
import time
import json
import re
import urllib.parse
from datetime import datetime, timedelta
from functools import wraps
from typing import Tuple, List, Dict, Any

from flask import (
    Flask, render_template, jsonify, request, 
    redirect, url_for, session, flash, make_response
)
import requests

from models import db, AdminUser, Pharmacy, KioskDevice, SystemSetting, TickerTemplate, lisans_anahtari_uret
from services.pharmacy_service import (
    nobetci_eczaneleri_getir,
    turkce_karakter_temizle,
    haversine_mesafe,
    rota_linki_olustur,
    qr_kod_url_olustur,
    eczane_detay_bilgisi_ara,
    onbellek_temizle
)

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


# ==========================================
# Kimlik Doğrulama Dekoratörü (Auth Helper)
# ==========================================
def login_required(f):
    """Yönetim paneli rotalarını koruyan oturum kontrol dekoratörü."""
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
    Kalıcı veritabanında yeni eklenen kolonları (ALTER TABLE) otomatik uygular.
    """
    with app.app_context():
        db.create_all()

        # Otomatik SQLite migration: Eksik kolonları ekle
        try:
            from sqlalchemy import text, inspect
            inspector = inspect(db.engine)
            tablolar = inspector.get_table_names()
            if "pharmacies" in tablolar:
                mevcut_kolonlar = [c["name"] for c in inspector.get_columns("pharmacies")]
                with db.engine.connect() as conn:
                    if "registered_device_token" not in mevcut_kolonlar:
                        conn.execute(text("ALTER TABLE pharmacies ADD COLUMN registered_device_token VARCHAR(128)"))
                    if "last_ip" not in mevcut_kolonlar:
                        conn.execute(text("ALTER TABLE pharmacies ADD COLUMN last_ip VARCHAR(64)"))
                    if "device_lock_enabled" not in mevcut_kolonlar:
                        conn.execute(text("ALTER TABLE pharmacies ADD COLUMN device_lock_enabled BOOLEAN DEFAULT 1"))
                    if "pharmacist_name" not in mevcut_kolonlar:
                        conn.execute(text("ALTER TABLE pharmacies ADD COLUMN pharmacist_name VARCHAR(120)"))
                    if "chamber_registration_no" not in mevcut_kolonlar:
                        conn.execute(text("ALTER TABLE pharmacies ADD COLUMN chamber_registration_no VARCHAR(60)"))
                    if "phone" not in mevcut_kolonlar:
                        conn.execute(text("ALTER TABLE pharmacies ADD COLUMN phone VARCHAR(30)"))
                    if "mobile_phone" not in mevcut_kolonlar:
                        conn.execute(text("ALTER TABLE pharmacies ADD COLUMN mobile_phone VARCHAR(30)"))
                    if "address" not in mevcut_kolonlar:
                        conn.execute(text("ALTER TABLE pharmacies ADD COLUMN address TEXT"))
                    if "is_on_duty_today" not in mevcut_kolonlar:
                        conn.execute(text("ALTER TABLE pharmacies ADD COLUMN is_on_duty_today BOOLEAN DEFAULT 0"))
                    if "manual_duty_override_until" not in mevcut_kolonlar:
                        conn.execute(text("ALTER TABLE pharmacies ADD COLUMN manual_duty_override_until DATETIME"))
                    if "theme" not in mevcut_kolonlar:
                        conn.execute(text("ALTER TABLE pharmacies ADD COLUMN theme VARCHAR(50) DEFAULT 'classic_grid'"))
                    if "max_devices" not in mevcut_kolonlar:
                        conn.execute(text("ALTER TABLE pharmacies ADD COLUMN max_devices INTEGER DEFAULT 1"))
                    if "screen_scale" not in mevcut_kolonlar:
                        conn.execute(text("ALTER TABLE pharmacies ADD COLUMN screen_scale VARCHAR(20) DEFAULT 'auto'"))
                    if "theme_settings" not in mevcut_kolonlar:
                        conn.execute(text("ALTER TABLE pharmacies ADD COLUMN theme_settings TEXT DEFAULT '{}'"))
                    if "institution_type" not in mevcut_kolonlar:
                        conn.execute(text("ALTER TABLE pharmacies ADD COLUMN institution_type VARCHAR(50) DEFAULT 'pharmacy'"))
                    if "institution_settings" not in mevcut_kolonlar:
                        conn.execute(text("ALTER TABLE pharmacies ADD COLUMN institution_settings TEXT DEFAULT '{}'"))
                    conn.commit()

            if "kiosk_devices" in tablolar:
                mevcut_dev_kolonlar = [c["name"] for c in inspector.get_columns("kiosk_devices")]
                with db.engine.connect() as conn:
                    if "screen_scale" not in mevcut_dev_kolonlar:
                        conn.execute(text("ALTER TABLE kiosk_devices ADD COLUMN screen_scale VARCHAR(20) DEFAULT 'auto'"))
                    if "identify_until" not in mevcut_dev_kolonlar:
                        conn.execute(text("ALTER TABLE kiosk_devices ADD COLUMN identify_until DATETIME"))
                    if "mac_address" not in mevcut_dev_kolonlar:
                        conn.execute(text("ALTER TABLE kiosk_devices ADD COLUMN mac_address VARCHAR(64)"))
                    if "local_ip" not in mevcut_dev_kolonlar:
                        conn.execute(text("ALTER TABLE kiosk_devices ADD COLUMN local_ip VARCHAR(64)"))
                    if "is_approved" not in mevcut_dev_kolonlar:
                        conn.execute(text("ALTER TABLE kiosk_devices ADD COLUMN is_approved BOOLEAN DEFAULT 1"))
                    if "approved_at" not in mevcut_dev_kolonlar:
                        conn.execute(text("ALTER TABLE kiosk_devices ADD COLUMN approved_at DATETIME"))
                    if "theme" not in mevcut_dev_kolonlar:
                        conn.execute(text("ALTER TABLE kiosk_devices ADD COLUMN theme VARCHAR(50)"))
                    if "theme_settings" not in mevcut_dev_kolonlar:
                        conn.execute(text("ALTER TABLE kiosk_devices ADD COLUMN theme_settings TEXT"))
                    conn.commit()
        except Exception as hata:
            print(f"[UYARI] Veritabanı kolon denetim hatası: {hata}")

        # Varsayılan yönetici hesabı kontrolü
        admin = AdminUser.query.filter_by(username=ADMIN_USER).first()
        if not admin:
            yeni_admin = AdminUser(username=ADMIN_USER)
            yeni_admin.sifre_belirle(ADMIN_PASSWORD)
            db.session.add(yeni_admin)
            db.session.commit()
            print(f"[BİLGİ] Varsayılan yönetici oluşturuldu: {ADMIN_USER}")

        # Duyuru şablonlarını seed et
        try:
            TickerTemplate.seed_defaults()
        except Exception as e:
            print(f"[UYARI] TickerTemplate seed hatası: {e}")

        # Eğer hiç eczane yoksa demo eczane kaydı aç
        if Pharmacy.query.count() == 0:
            demo_eczane = Pharmacy(
                name="Çakırlar Eczanesi",
                city="İstanbul",
                district="Bahçelievler",
                latitude=40.9985,
                longitude=28.8650,
                pharmacist_name="Ecz. Sedat Bayraklı",
                chamber_registration_no="34-12345",
                phone="0212 555 44 33",
                mobile_phone="0532 111 22 33",
                address="Bahçelievler Mahallesi, Çakırlar Caddesi No: 12 Bahçelievler / İstanbul",
                license_key="ECZ-CAKIRLAR-001",
                expires_at=datetime.now() + timedelta(days=365),
                is_active=True,
                ticker_text="Çakırlar Eczanesi sağlıklı günler diler. Reçeteli ilaçlarınız ve medikal ihtiyaçlarınız için danışabilirsiniz."
            )
            db.session.add(demo_eczane)
            db.session.commit()
            print("[BİLGİ] Demo eczane oluşturuldu: Çakırlar Eczanesi (ECZ-CAKIRLAR-001)")

        # Global sistem ayarlarını başlat (yoksa varsayılan kayıt oluşturur)
        try:
            SystemSetting.get_settings()
        except Exception as e:
            print(f"[UYARI] Sistem ayarları ilk başlatma hatası: {e}")


# ==========================================
# Yardımcı Güvenlik Fonksiyonları
# ==========================================
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
    """Yönetim paneli ana kontrol ekranı (Eczane listesi, istatistikler, cihaz onayları ve heartbeat)."""
    eczaneler = Pharmacy.query.order_by(Pharmacy.id.desc()).all()
    
    toplam_sayi = len(eczaneler)
    aktif_sayi = sum(1 for e in eczaneler if e.lisans_gecerli_mi())
    cevrimici_sayi = sum(1 for e in eczaneler if e.ekran_cevrimici_mi())
    bekleyen_onay_sayisi = KioskDevice.query.filter_by(is_approved=False).count()

    # Host ve protokol bilgisi (kiosk URL kopyalama ve önizleme için HTTPS duyarlı)
    proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.headers.get("X-Forwarded-Host", request.host)
    base_url = f"{proto}://{host}"

    return render_template(
        "admin_dashboard.html",
        eczaneler=eczaneler,
        toplam_sayi=toplam_sayi,
        aktif_sayi=aktif_sayi,
        cevrimici_sayi=cevrimici_sayi,
        bekleyen_onay_sayisi=bekleyen_onay_sayisi,
        base_url=base_url
    )


@app.route("/admin/api/geocode")
@login_required
def admin_api_geocode():
    """
    Eczacı Odası ve açık dizinler üzerinden eczane adı, açık adres, telefon ve koordinatları arar.
    Admin formundan 'Bilgileri & Konumu Getir' butonuna basıldığında çağrılır.
    Kesinlikle başka ilçelerin koordinatlarını kabul etmez (ilçe sınır kontrolü).
    """
    q = request.args.get("q", "").strip()
    il = request.args.get("il", "İstanbul").strip()
    ilce = request.args.get("ilce", "").strip()
    address = request.args.get("address", "").strip()

    # 0. Aşama: Veritabanında kayıtlı eczane varsa tamamlayıcı bilgi olarak al
    mevcut_eczane = Pharmacy.query.filter(
        Pharmacy.name.ilike(f"%{q}%"),
        Pharmacy.district.ilike(f"%{ilce}%")
    ).first() if (q and ilce) else None

    db_adres = mevcut_eczane.address if (mevcut_eczane and mevcut_eczane.address) else ""
    db_tel = mevcut_eczane.phone if (mevcut_eczane and mevcut_eczane.phone) else ""
    db_sicil = mevcut_eczane.chamber_registration_no if (mevcut_eczane and mevcut_eczane.chamber_registration_no) else ""

    # 1. Aşama: Eczacı Odası (İEO) ve Akıllı Bilgi Ağından Adres ve Telefon Sorgulama
    detay = {}
    if q and ilce:
        detay = eczane_detay_bilgisi_ara(eczane_adi=q, ilce=ilce, il=il, mevcut_adres=address or db_adres)
        if not address and detay.get("adres"):
            address = detay.get("adres")
        elif not address and db_adres:
            address = db_adres

    nihai_tel = detay.get("telefon") or db_tel
    nihai_sicil = detay.get("sicil") or db_sicil
    nihai_kaynak = detay.get("kaynak") or ("Veritabanı Kaydı" if db_adres else "Açık Harita Servisi")

    # Eğer resmi Eczacı Odası'ndan veya veritabanından doğrudan koordinat geldiyse doğrudan döndür
    if detay.get("enlem") and detay.get("boylam"):
        return jsonify({
            "success": True,
            "name": q,
            "address": detay.get("adres") or address,
            "phone": nihai_tel,
            "chamber_registration_no": nihai_sicil,
            "latitude": float(detay["enlem"]),
            "longitude": float(detay["boylam"]),
            "display_name": f"{detay.get('adres', '')} ({ilce}, {il})",
            "source": detay.get("kaynak", "İstanbul Eczacı Odası (İEO Resmi)"),
            "is_fallback": False,
            "message": f"Eczane bilgileri ve konumu {detay.get('kaynak')} üzerinden başarıyla alındı."
        })

    tarayici_basligi = {
        "User-Agent": "EczaneKioskSystem/2.0 (destek@cakirlar.net)"
    }

    def metin_norm(s: str) -> str:
        """Karşılaştırma için Türkçe karakterleri normalize eder."""
        if not s:
            return ""
        tr_map = str.maketrans("İIĞÜŞÖÇığıüşöç", "iiguusociguuso")
        return s.translate(tr_map).lower().strip()

    def ilce_dogrula(sonuc_item: dict, hedef_ilce: str) -> bool:
        """Gelen koordinat sonucunun gerçekten hedef ilçede olup olmadığını denetler."""
        if not hedef_ilce:
            return True
        hedef_norm = metin_norm(hedef_ilce)
        if not hedef_norm:
            return True
        
        addr_dict = sonuc_item.get("address", {})
        display_name = metin_norm(sonuc_item.get("display_name", ""))
        
        # İlçe ve mahalle alanlarını kontrol et
        kontrol_alanlari = [
            metin_norm(addr_dict.get("county", "")),
            metin_norm(addr_dict.get("district", "")),
            metin_norm(addr_dict.get("city_district", "")),
            metin_norm(addr_dict.get("suburb", "")),
            metin_norm(addr_dict.get("town", "")),
            metin_norm(addr_dict.get("municipality", "")),
        ]
        
        # Hedef ilçe bu alanlardan birinde veya display_name içinde geçiyor mu?
        for alan in kontrol_alanlari:
            if alan and (hedef_norm in alan or alan in hedef_norm):
                return True
        if hedef_norm in display_name:
            return True
            
        return False

    # Arama terimlerini öncelik sırasına göre hazırla
    arama_terimleri = []
    
    # 1. Öncelik: Açık adres verildiyse (veya Eczacı Odasından/Ağdan çekildiyse) adres odaklı arama
    if address:
        arama_terimleri.append((f"{address}, {ilce}, {il}, Türkiye", False))
        
        # Adresten Mahalle ve Sokak ayıklama (Örn: Zafer Mah., Gümüş Sok.)
        m_mah = re.search(r'([A-Za-zÇŞĞÜÖİçşğüöı0-9]+)\s*(?:Mah\.|Mahallesi|Mah)', address, re.IGNORECASE)
        m_sok = re.search(r'([A-Za-zÇŞĞÜÖİçşğüöı0-9\s]+?)\s*(?:Sok\.|Sokak|Sokağı|Cad\.|Caddesi)', address, re.IGNORECASE)
        
        if m_mah and m_sok:
            sok_adi = m_sok.group(1).split()[-1]
            arama_terimleri.append((f"{m_mah.group(1)} Mahallesi, {sok_adi} Sokak, {ilce}, {il}", False))
        if m_mah:
            arama_terimleri.append((f"{m_mah.group(1)} Mahallesi, {ilce}, {il}", False))
        if m_sok:
            sok_adi = m_sok.group(1).split()[-1]
            arama_terimleri.append((f"{sok_adi} Sokak, {ilce}, {il}", False))

    # 2. Öncelik: Eczane adı + ilçe
    if q:
        q_temiz = re.sub(r'(?i)\beczane(si)?\b', '', q).strip()
        arama_terimleri.append((f"{q}, {ilce}, {il}, Türkiye", False))
        if q_temiz != q:
            arama_terimleri.append((f"{q_temiz} Eczanesi, {ilce}, {il}", False))
            arama_terimleri.append((f"{q_temiz} Eczanesi, {ilce}", False))
        arama_terimleri.append((f"{q}, {ilce}", False))

    # 3. Öncelik (Güvenli Fallback): Yalnızca hedef ilçenin kendi merkezi (Asla başka ilçeye atlamaz!)
    if ilce:
        arama_terimleri.append((f"{ilce}, {il}, Türkiye", True))
    elif il:
        arama_terimleri.append((f"{il}, Türkiye", True))

    for sorgu, is_fallback in arama_terimleri:
        try:
            url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(sorgu)}&format=json&limit=5&addressdetails=1&countrycodes=tr"
            r = requests.get(url, headers=tarayici_basligi, timeout=6)
            if r.status_code == 200:
                sonuclar = r.json()
                for sonuc in sonuclar:
                    # İlçe uyumu kontrolü - Farklı ilçeler kesinlikle elenir!
                    if ilce and not ilce_dogrula(sonuc, ilce):
                        continue

                    display_name = sonuc.get("display_name", "")
                    kaynak_bilgisi = detay.get("kaynak", "Açık Harita Servisi")
                    
                    if is_fallback:
                        mesaj = f"Eczane için nokta atışı adres bulunamadı, {ilce} ilçe merkezine odaklanıldı. Lütfen harita üzerinden pini tam eczane konumunuza taşıyınız."
                    else:
                        mesaj = f"Eczane bilgileri ve konumu ({kaynak_bilgisi}) başarıyla tespit edildi."

                    return jsonify({
                        "success": True,
                        "name": q,
                        "address": detay.get("adres") or address,
                        "phone": nihai_tel,
                        "chamber_registration_no": nihai_sicil,
                        "latitude": float(sonuc["lat"]),
                        "longitude": float(sonuc["lon"]),
                        "display_name": display_name,
                        "source": nihai_kaynak,
                        "is_fallback": is_fallback,
                        "message": mesaj
                    })
        except Exception:
            continue

    return jsonify({
        "success": False,
        "message": f"{ilce} ilçesinde koordinat tespit edilemedi. Lütfen harita üzerinden pini sürükleyerek konumu belirleyiniz."
    }), 404


@app.route("/admin/pharmacy/add", methods=["POST"])
@login_required
def admin_add_pharmacy():
    """Yeni eczane ve lisans kaydı oluşturma."""
    isim = request.form.get("name", "").strip()
    sehir = request.form.get("city", "İstanbul").strip()
    ilce = request.form.get("district", "Bahçelievler").strip()
    enlem = request.form.get("latitude", type=float)
    boylam = request.form.get("longitude", type=float)
    
    eczaci_adi = request.form.get("pharmacist_name", "").strip()
    oda_sicil = request.form.get("chamber_registration_no", "").strip()
    telefon = request.form.get("phone", "").strip()
    cep_telefonu = request.form.get("mobile_phone", "").strip()
    acik_adres = request.form.get("address", "").strip()
    kayan_yazi = request.form.get("ticker_text", "").strip()
    lisans_gun = request.form.get("license_days", 365, type=int)
    tema = request.form.get("theme", "classic_grid").strip()
    cihaz_limiti = request.form.get("max_devices", 1, type=int)
    ekran_olcegi = request.form.get("screen_scale", "auto").strip()

    kurum_turu = request.form.get("institution_type", "pharmacy").strip()
    if not isim:
        flash("Kurum adı zorunludur!", "danger")
        return redirect(url_for("admin_dashboard"))

    yeni_eczane = Pharmacy(
        name=isim,
        institution_type=kurum_turu or "pharmacy",
        city=sehir,
        district=ilce,
        latitude=enlem,
        longitude=boylam,
        pharmacist_name=eczaci_adi,
        chamber_registration_no=oda_sicil,
        phone=telefon,
        mobile_phone=cep_telefonu,
        address=acik_adres,
        theme=tema,
        max_devices=max(1, cihaz_limiti),
        screen_scale=ekran_olcegi,
        license_key=lisans_anahtari_uret(),
        expires_at=datetime.now() + timedelta(days=lisans_gun),
        is_active=True,
        ticker_text=kayan_yazi or f"{isim} sağlıklı günler diler."
    )
    db.session.add(yeni_eczane)
    db.session.commit()

    flash(f"'{isim}' ({yeni_eczane.get_institution_type_label()}) başarıyla eklendi. Lisans: {yeni_eczane.license_key} (Cihaz Limiti: {yeni_eczane.max_devices})", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/pharmacy/<int:eczane_id>/edit", methods=["POST"])
@login_required
def admin_edit_pharmacy(eczane_id):
    """Mevcut kurum bilgilerini düzenleme."""
    eczane = Pharmacy.query.get_or_404(eczane_id)

    eczane.name = request.form.get("name", eczane.name).strip()
    kurum_turu = request.form.get("institution_type")
    if kurum_turu:
        eczane.institution_type = kurum_turu.strip()
    eczane.city = request.form.get("city", eczane.city).strip()
    eczane.district = request.form.get("district", eczane.district).strip()
    eczane.latitude = request.form.get("latitude", eczane.latitude, type=float)
    eczane.longitude = request.form.get("longitude", eczane.longitude, type=float)
    
    eczane.pharmacist_name = request.form.get("pharmacist_name", eczane.pharmacist_name).strip()
    eczane.chamber_registration_no = request.form.get("chamber_registration_no", eczane.chamber_registration_no).strip()
    eczane.phone = request.form.get("phone", eczane.phone).strip()
    eczane.mobile_phone = request.form.get("mobile_phone", eczane.mobile_phone).strip()
    eczane.address = request.form.get("address", eczane.address).strip()
    eczane.ticker_text = request.form.get("ticker_text", eczane.ticker_text).strip()
    eczane.theme = request.form.get("theme", eczane.theme or "classic_grid").strip()
    eczane.max_devices = request.form.get("max_devices", eczane.max_devices or 1, type=int)
    eczane.screen_scale = request.form.get("screen_scale", eczane.screen_scale or "auto").strip()

    bitis_str = request.form.get("expires_at", "")
    if bitis_str:
        try:
            eczane.expires_at = datetime.strptime(bitis_str, "%Y-%m-%d")
        except ValueError:
            pass

    db.session.commit()
    flash(f"'{eczane.name}' bilgileri güncellendi.", "success")
    return redirect(url_for("admin_dashboard"))



@app.route("/admin/pharmacy/<int:eczane_id>/ticker", methods=["POST"])
@login_required
def admin_update_ticker(eczane_id):
    """Eczane kayan yazı duyurusunu hızlıca güncelleme (AJAX)."""
    eczane = Pharmacy.query.get_or_404(eczane_id)
    veri = request.get_json(silent=True) or {}
    yeni_metin = veri.get("ticker_text", "").strip()
    if not yeni_metin:
        return jsonify({"success": False, "error": "Duyuru metni boş olamaz."}), 400
    eczane.ticker_text = yeni_metin
    db.session.commit()
    return jsonify({"success": True, "ticker_text": eczane.ticker_text})


# ==========================================
# Duyuru Şablonları Yönetimi (CRUD API)
# ==========================================
@app.route("/admin/api/ticker-templates", methods=["GET"])
@login_required
def admin_get_ticker_templates():
    """Tüm duyuru şablonlarını listeler."""
    TickerTemplate.seed_defaults()
    sablonlar = TickerTemplate.query.order_by(TickerTemplate.is_default.desc(), TickerTemplate.id.asc()).all()
    return jsonify({"success": True, "templates": [s.to_dict() for s in sablonlar]})


@app.route("/admin/api/ticker-templates", methods=["POST"])
@login_required
def admin_create_ticker_template():
    """Yeni duyuru şablonu ekler."""
    veri = request.get_json(silent=True) or {}
    baslik = veri.get("title", "").strip()
    metin = veri.get("text", "").strip()
    if not baslik or not metin:
        return jsonify({"success": False, "error": "Şablon başlığı ve metni zorunludur."}), 400
    yeni = TickerTemplate(title=baslik, text=metin, is_default=False)
    db.session.add(yeni)
    db.session.commit()
    return jsonify({"success": True, "template": yeni.to_dict()})


@app.route("/admin/api/ticker-templates/<int:t_id>/edit", methods=["POST"])
@login_required
def admin_edit_ticker_template(t_id):
    """Mevcut duyuru şablonunu günceller."""
    sablon = TickerTemplate.query.get_or_404(t_id)
    veri = request.get_json(silent=True) or {}
    baslik = veri.get("title", "").strip()
    metin = veri.get("text", "").strip()
    if not baslik or not metin:
        return jsonify({"success": False, "error": "Şablon başlığı ve metni zorunludur."}), 400
    sablon.title = baslik
    sablon.text = metin
    db.session.commit()
    return jsonify({"success": True, "template": sablon.to_dict()})


@app.route("/admin/api/ticker-templates/<int:t_id>/delete", methods=["POST"])
@login_required
def admin_delete_ticker_template(t_id):
    """Duyuru şablonunu siler."""
    sablon = TickerTemplate.query.get_or_404(t_id)
    db.session.delete(sablon)
    db.session.commit()
    return jsonify({"success": True, "id": t_id})


# ==========================================
# Kiosk Ekran Teması Parametrik Ayarları
# ==========================================
@app.route("/admin/pharmacy/<int:eczane_id>/theme-settings", methods=["GET"])
@login_required
def admin_get_theme_settings(eczane_id):
    """Eczanenin tema parametrelerini döndürür."""
    eczane = Pharmacy.query.get_or_404(eczane_id)
    return jsonify({
        "success": True,
        "pharmacy_name": eczane.name,
        "theme": eczane.theme,
        "theme_settings": eczane.get_theme_settings()
    })


@app.route("/admin/pharmacy/<int:eczane_id>/theme-settings", methods=["POST"])
@login_required
def admin_save_theme_settings(eczane_id):
    """Eczanenin tema parametrik ayarlarını kaydeder (AJAX)."""
    eczane = Pharmacy.query.get_or_404(eczane_id)
    veri = request.get_json(silent=True) or {}
    
    yeni_tema = veri.get("theme")
    if yeni_tema and yeni_tema in ["classic_grid", "list_view", "animated_route", "focus_carousel", "dual_card", "auto_rotate"]:
        eczane.theme = yeni_tema
        
    ayarlar = veri.get("theme_settings", {})
    if isinstance(ayarlar, dict):
        eczane.set_theme_settings(ayarlar)
        
    db.session.commit()
    return jsonify({
        "success": True, 
        "theme": eczane.theme,
        "theme_settings": eczane.get_theme_settings()
    })



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
@app.route("/admin/pharmacy/<int:eczane_id>/reset-devices", methods=["POST"])
@login_required
def admin_reset_device(eczane_id):
    """Eczanenin TV ekranı cihaz kilitlerini sıfırlar."""
    eczane = Pharmacy.query.get_or_404(eczane_id)
    eczane.tum_cihazlari_sifirla()

    flash(f"'{eczane.name}' TV cihaz kilitleri sıfırlandı. Yeni TV ekranları lisansa bağlanabilir.", "info")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/pharmacy/<int:eczane_id>/device/<int:device_id>/delete", methods=["POST"])
@login_required
def admin_delete_single_device(eczane_id, device_id):
    """Tek bir TV cihazının lisans bağlantısını keser ve siler (AJAX destekli)."""
    eczane = Pharmacy.query.get_or_404(eczane_id)
    cihaz = KioskDevice.query.filter_by(id=device_id, pharmacy_id=eczane.id).first_or_404()
    cihaz_adi = cihaz.device_name
    db.session.delete(cihaz)
    db.session.commit()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
        return jsonify({
            "success": True,
            "message": f"'{cihaz_adi}' bağlantısı kesildi ve silindi.",
            "device_id": device_id,
            "remaining_count": eczane.devices.count()
        })

    flash(f"'{eczane.name}' - '{cihaz_adi}' bağlantısı kesildi ve cihaz silindi.", "warning")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/api/health-check-devices")
@login_required
def admin_api_health_check_devices():
    """Tüm kayıtlı eczanelerin ve TV ekranlarının anlık canlılık (heartbeat) durumunu döner."""
    sistem_ayari = SystemSetting.get_settings()
    tolerans = sistem_ayari.heartbeat_tolerance_min or 5

    eczaneler = Pharmacy.query.all()
    sonuc = []
    cevrimici_sayisi = 0

    for e in eczaneler:
        e_online = e.ekran_cevrimici_mi(tolerans_dakika=tolerans)
        if e_online:
            cevrimici_sayisi += 1

        dev_list = []
        for d in e.devices:
            d_online = d.ekran_cevrimici_mi(tolerans_dakika=tolerans)
            dev_list.append({
                "id": d.id,
                "name": d.device_name,
                "is_online": d_online,
                "last_ping": d.last_ping.strftime("%H:%M:%S") if d.last_ping else None,
                "local_ip": d.local_ip,
                "ip": d.ip_address
            })

        sonuc.append({
            "id": e.id,
            "name": e.name,
            "is_online": e_online,
            "last_ip": e.last_ip,
            "last_ping": e.last_ping.strftime("%H:%M:%S") if e.last_ping else None,
            "devices": dev_list
        })

    return jsonify({
        "success": True,
        "pharmacies": sonuc,
        "online_count": cevrimici_sayisi,
        "checked_at": datetime.now().strftime("%H:%M:%S")
    })


@app.route("/admin/api/pharmacy/<int:eczane_id>/health-check")
@login_required
def admin_api_pharmacy_health_check(eczane_id):
    """Tek bir eczanenin anlık canlılık (heartbeat) ve bağlı cihaz durumlarını döner."""
    eczane = Pharmacy.query.get_or_404(eczane_id)
    sistem_ayari = SystemSetting.get_settings()
    tolerans = sistem_ayari.heartbeat_tolerance_min or 5

    is_online = eczane.ekran_cevrimici_mi(tolerans_dakika=tolerans)

    dev_list = []
    for d in eczane.devices:
        dev_list.append({
            "id": d.id,
            "name": d.device_name,
            "is_online": d.is_online(tolerans_dakika=tolerans),
            "is_approved": d.is_approved,
            "last_ping": d.last_ping.strftime("%H:%M:%S") if d.last_ping else None,
            "last_ping_ago": d.son_sinyal_metni(),
            "local_ip": d.local_ip,
            "ip": d.ip_address,
            "mac": d.mac_address,
            "resolution": d.screen_resolution,
            "scale": d.screen_scale
        })

    return jsonify({
        "success": True,
        "pharmacy": {
            "id": eczane.id,
            "name": eczane.name,
            "is_online": is_online,
            "last_ip": eczane.last_ip,
            "last_ping": eczane.last_ping.strftime("%H:%M:%S") if eczane.last_ping else None,
            "last_ping_ago": eczane.son_sinyal_metni(),
            "devices": dev_list,
            "device_count": len(dev_list),
            "online_device_count": sum(1 for d in dev_list if d["is_online"])
        },
        "checked_at": datetime.now().strftime("%H:%M:%S")
    })


@app.route("/admin/api/pharmacy/<int:eczane_id>/device/<int:device_id>/health-check")
@login_required
def admin_api_device_health_check(eczane_id, device_id):
    """Eczaneye bağlı tek bir TV cihazının anlık canlılık durumunu döner."""
    eczane = Pharmacy.query.get_or_404(eczane_id)
    cihaz = KioskDevice.query.filter_by(id=device_id, pharmacy_id=eczane.id).first_or_404()
    sistem_ayari = SystemSetting.get_settings()
    tolerans = sistem_ayari.heartbeat_tolerance_min or 5

    return jsonify({
        "success": True,
        "pharmacy_name": eczane.name,
        "device": {
            "id": cihaz.id,
            "name": cihaz.device_name,
            "is_online": cihaz.is_online(tolerans_dakika=tolerans),
            "is_approved": cihaz.is_approved,
            "last_ping": cihaz.last_ping.strftime("%H:%M:%S") if cihaz.last_ping else None,
            "last_ping_ago": cihaz.son_sinyal_metni(),
            "local_ip": cihaz.local_ip,
            "ip": cihaz.ip_address,
            "mac": cihaz.mac_address,
            "resolution": cihaz.screen_resolution,
            "scale": cihaz.screen_scale
        },
        "checked_at": datetime.now().strftime("%H:%M:%S")
    })


@app.route("/admin/help")
@login_required
def admin_help():
    """Sistemin kullanım kılavuzu, TV kurulumu ve özellikler rehberi sayfası."""
    return render_template("admin_help.html")


@app.route("/admin/duty-pharmacies")
@login_required
def admin_duty_pharmacies():
    """İl, ilçe ve tarih bazında nöbetçi eczaneleri sorgulama ve listeleme sayfası."""
    il = request.args.get("il", "İstanbul")
    ilce = request.args.get("ilce", "Bahçelievler")
    tarih = request.args.get("tarih", datetime.now().strftime("%Y-%m-%d"))
    kaynak = request.args.get("kaynak", "hepsi")
    return render_template(
        "admin_duty_pharmacies.html",
        secili_il=il,
        secili_ilce=ilce,
        secili_tarih=tarih,
        secili_kaynak=kaynak
    )


@app.route("/admin/api/duty-pharmacies")
@login_required
def admin_api_duty_pharmacies():
    """AJAX ile nöbetçi eczaneleri JSON olarak döndüren uç nokta."""
    il = request.args.get("il", "İstanbul").strip()
    ilce = request.args.get("ilce", "Bahçelievler").strip()
    tarih = request.args.get("tarih", "").strip() or None
    kaynak = request.args.get("kaynak", "hepsi").strip()
    force_refresh = request.args.get("refresh", "0") in ("1", "true", "True")

    # Özel kaynak seçimi yapılmışsa öncelik listesini ona göre ayarla
    kaynak_siralamasi = [kaynak] if (kaynak and kaynak not in ("hepsi", "auto", "tum")) else None
    cache_suresi = 0 if force_refresh else None

    try:
        sonuc = nobetci_eczaneleri_getir(
            il=il,
            ilce=ilce,
            cache_suresi_dakika=cache_suresi,
            kaynak_siralamasi=kaynak_siralamasi,
            tarih=tarih
        )
        return jsonify({
            "success": True,
            "eczaneler": sonuc.get("eczaneler", []),
            "veri_saglayici": sonuc.get("veri_saglayici", ""),
            "guncellenme_zamani": sonuc.get("guncellenme_zamani", ""),
            "il": il,
            "ilce": ilce,
            "tarih": tarih or datetime.now().strftime("%Y-%m-%d"),
            "kaynak": kaynak
        })
    except Exception as e:
        app.logger.error(f"Nöbetçi eczane sorgu hatası: {e}")
        return jsonify({
            "success": False,
            "error": f"Nöbetçi eczaneler alınırken hata oluştu: {str(e)}",
            "eczaneler": []
        }), 500


@app.route("/admin/pharmacy/<int:eczane_id>/devices-json")
@login_required
def admin_pharmacy_devices_json(eczane_id):
    """Eczanenin bağlı cihazlarını JSON olarak döner (Admin modalı için)."""
    eczane = Pharmacy.query.get_or_404(eczane_id)
    return jsonify({
        "success": True,
        "pharmacy": eczane.to_dict()
    })


@app.route("/admin/pharmacy/<int:eczane_id>/device/<int:device_id>/approve", methods=["POST"])
@login_required
def admin_approve_single_device(eczane_id, device_id):
    """Cihazın lisansını aktif eder ve onaylar. MAC değişmediği sürece bir daha onay istemez."""
    eczane = Pharmacy.query.get_or_404(eczane_id)
    cihaz = KioskDevice.query.filter_by(id=device_id, pharmacy_id=eczane.id).first_or_404()
    cihaz.is_approved = True
    cihaz.approved_at = datetime.now()
    db.session.commit()

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
        return jsonify({
            "success": True,
            "message": f"'{cihaz.device_name}' cihazının lisansı başarıyla aktif edildi! Ekran otomatik yayına başlayacaktır.",
            "device": cihaz.to_dict()
        })

    flash(f"'{cihaz.device_name}' cihazının lisansı başarıyla aktif edildi! Ekran otomatik yayına başlayacaktır.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/pharmacy/<int:eczane_id>/device/<int:device_id>/edit", methods=["POST"])
@login_required
def admin_edit_single_device(eczane_id, device_id):
    """Tek bir TV/Kiosk cihazının adını, temasını, ekran ölçeğini ve MAC adresini günceller (Form veya AJAX)."""
    eczane = Pharmacy.query.get_or_404(eczane_id)
    cihaz = KioskDevice.query.filter_by(id=device_id, pharmacy_id=eczane.id).first_or_404()

    veri = request.get_json(silent=True) or request.form

    yeni_ad = (veri.get("device_name") or cihaz.device_name).strip()
    yeni_tema = (veri.get("theme") or cihaz.get_theme()).strip()
    yeni_olcek = (veri.get("screen_scale") or cihaz.screen_scale or "auto").strip()
    yeni_mac = (veri.get("mac_address") or cihaz.mac_address or "").strip()

    if yeni_ad:
        cihaz.device_name = yeni_ad
    if yeni_tema in ["classic_grid", "list_view", "animated_route", "focus_carousel", "dual_card", "auto_rotate"]:
        cihaz.theme = yeni_tema
    if yeni_olcek:
        cihaz.screen_scale = yeni_olcek
    if yeni_mac:
        cihaz.mac_address = yeni_mac

    db.session.commit()

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "success": True,
            "message": f"'{cihaz.device_name}' cihaz ayarları güncellendi.",
            "device": cihaz.to_dict()
        })

    flash(f"'{cihaz.device_name}' cihaz ayarları güncellendi.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/pharmacy/<int:eczane_id>/device/<int:device_id>/theme-settings", methods=["GET"])
@login_required
def admin_get_device_theme_settings(eczane_id, device_id):
    """Cihaza özel tema ve parametrik ayarları döndürür."""
    eczane = Pharmacy.query.get_or_404(eczane_id)
    cihaz = KioskDevice.query.filter_by(id=device_id, pharmacy_id=eczane.id).first_or_404()
    return jsonify({
        "success": True,
        "pharmacy_name": eczane.name,
        "device_id": cihaz.id,
        "device_name": cihaz.device_name,
        "theme": cihaz.get_theme(),
        "theme_settings": cihaz.get_theme_settings()
    })


@app.route("/admin/pharmacy/<int:eczane_id>/device/<int:device_id>/theme-settings", methods=["POST"])
@login_required
def admin_save_device_theme_settings(eczane_id, device_id):
    """Cihaza özel tema ve parametrik ayarlarını kaydeder (AJAX)."""
    eczane = Pharmacy.query.get_or_404(eczane_id)
    cihaz = KioskDevice.query.filter_by(id=device_id, pharmacy_id=eczane.id).first_or_404()
    veri = request.get_json(silent=True) or {}

    yeni_tema = veri.get("theme")
    if yeni_tema and yeni_tema in ["classic_grid", "list_view", "animated_route", "focus_carousel", "dual_card", "auto_rotate"]:
        cihaz.theme = yeni_tema

    ayarlar = veri.get("theme_settings", {})
    if isinstance(ayarlar, dict):
        cihaz.set_theme_settings(ayarlar)

    db.session.commit()
    return jsonify({
        "success": True,
        "device_id": cihaz.id,
        "device_name": cihaz.device_name,
        "theme": cihaz.get_theme(),
        "theme_settings": cihaz.get_theme_settings()
    })


@app.route("/admin/pharmacy/<int:eczane_id>/device/<int:device_id>/identify", methods=["POST"])
@login_required
def admin_identify_single_device(eczane_id, device_id):
    """Cihazı TV ekranında parlatarak/belirterek tanımlama sinyali gönderir."""
    eczane = Pharmacy.query.get_or_404(eczane_id)
    cihaz = KioskDevice.query.filter_by(id=device_id, pharmacy_id=eczane.id).first_or_404()

    cihaz.cihazi_tanimla(25)
    db.session.commit()

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
        return jsonify({
            "success": True,
            "message": f"'{cihaz.device_name}' cihazına ekranda göster sinyali gönderildi! TV ekranında 25 saniye boyunca parlayacak."
        })

    flash(f"'{cihaz.device_name}' cihazına ekranda göster sinyali gönderildi! TV ekranında 25 saniye boyunca parlayacak.", "info")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/pharmacy/<int:eczane_id>/toggle-duty-test", methods=["POST"])
@login_required
def admin_toggle_duty_test(eczane_id):
    """
    1 Saatlik Hızlı Nöbet Testi Modunu açar veya kapatır.
    Eczacı veya yönetici ekranın 'Bu Gece Nöbetçiyiz' tasarımını TV'de hemen test edebilir.
    """
    eczane = Pharmacy.query.get_or_404(eczane_id)
    if eczane.duty_test_aktif_mi():
        eczane.manual_duty_override_until = None
        db.session.commit()
        flash(f"'{eczane.name}' için 1 saatlik hızlı nöbet testi kapatıldı.", "info")
    else:
        eczane.manual_duty_override_until = datetime.now() + timedelta(hours=1)
        db.session.commit()
        bitis_saati = eczane.manual_duty_override_until.strftime("%H:%M")
        flash(f"'{eczane.name}' 1 saatliğine test amaçlı NÖBETÇİ moduna alındı! (Bitiş: {bitis_saati})", "success")

    return redirect(url_for("admin_dashboard"))


# ==========================================
# SİSTEM GENEL AYARLARI & ÖNBELLEK YÖNETİMİ
# ==========================================

@app.route("/admin/settings", methods=["GET", "POST"])
@login_required
def admin_settings():
    """Tüm projenin genel sistem ayarlarını yönetir."""
    ayar = SystemSetting.get_settings()

    if request.method == "POST":
        try:
            cache_dur = request.form.get("cache_duration_minutes", 30, type=int)
            primary_src = request.form.get("primary_source", "ieo_resmi").strip()
            secondary_src = request.form.get("secondary_source", "eczaneler_gen_tr").strip()
            tertiary_src = request.form.get("tertiary_source", "nobetcieczaneler_org").strip()
            poll_interval = request.form.get("kiosk_poll_interval_sec", 60, type=int)
            duty_start = request.form.get("duty_start_time", "19:00").strip()
            duty_end = request.form.get("duty_end_time", "09:00").strip()
            tolerance = request.form.get("heartbeat_tolerance_min", 5, type=int)
            max_dist = request.form.get("max_search_distance_km", 15, type=int)
            map_theme = request.form.get("map_theme", "cartodb_dark").strip()

            ayar.cache_duration_minutes = max(5, cache_dur)
            ayar.primary_source = primary_src
            ayar.secondary_source = secondary_src
            ayar.tertiary_source = tertiary_src
            ayar.kiosk_poll_interval_sec = max(15, poll_interval)
            ayar.duty_start_time = duty_start or "19:00"
            ayar.duty_end_time = duty_end or "09:00"
            ayar.heartbeat_tolerance_min = max(1, tolerance)
            ayar.max_search_distance_km = max(1, max_dist)
            ayar.map_theme = map_theme or "cartodb_dark"
            ayar.updated_at = datetime.utcnow()

            db.session.commit()
            flash("Sistem genel ayarları başarıyla güncellendi!", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Ayarlar kaydedilirken hata oluştu: {str(e)}", "danger")

        return redirect(url_for("admin_settings"))

    return render_template("admin_settings.html", ayar=ayar)


@app.route("/admin/cache/clear", methods=["POST"])
@login_required
def admin_cache_clear():
    """Tüm önbelleğe alınmış nöbetçi eczane verilerini anında sıfırlar."""
    onbellek_temizle()
    flash("Tüm il ve ilçelerin nöbetçi eczane önbelleği başarıyla temizlendi! Sonraki sorgulamada taze veri çekilecektir.", "success")
    ref = request.referrer
    if ref and "/admin" in ref:
        return redirect(ref)
    return redirect(url_for("admin_settings"))


# ==========================================
# KIOSK EKRANI VE LİSANS DOĞRULAMA ROTALARI
# ==========================================

@app.route("/kiosk")
def kiosk():
    """
    Lisans anahtarlı TV Kiosk ekranı.
    /kiosk?key=ECZ-XXXX-XXXX veya çerezde kayıtlı lisans anahtarı
    """
    key = request.args.get("key", "").strip().upper()
    if not key:
        key = request.cookies.get("kiosk_license_key", "").strip().upper()

    device_token = request.args.get("device_token", "").strip()
    if not device_token:
        device_token = request.cookies.get("kiosk_device_token", "").strip()

    if not key:
        return render_template(
            "kiosk_error.html",
            hata_baslik="Lisans Anahtarı Eksik",
            hata_mesaj="TV ekranının çalışabilmesi için geçerli bir lisans anahtarı belirtilmelidir.",
            lisans_kodu="Belirtilmedi"
        )

    # Lisans anahtarını harf büyüklüğünden bağımsız (case-insensitive) ara
    eczane = Pharmacy.query.filter(db.func.upper(Pharmacy.license_key) == key).first()
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

    cihaz_mac = request.cookies.get("kiosk_device_mac", "").strip()
    if (device_token or cihaz_mac) and not eczane.cihaz_uyumlu_mu(device_token, cihaz_mac):
        return render_template(
            "kiosk_error.html",
            hata_baslik="Cihaz Limiti Dolu",
            hata_mesaj="Bu lisans için tanımlı TV ekranı sınırına ulaşıldı. Yönetim panelinden cihaz limitini artırın veya kullanılmayan bir cihazı silin.",
            lisans_kodu=key
        )

    resp = make_response(render_template(
        "kiosk.html",
        eczane=eczane,
        secili_il=eczane.city,
        secili_ilce=eczane.district,
        lisans_anahtari=eczane.license_key
    ))
    resp.set_cookie("kiosk_license_key", eczane.license_key, max_age=365*24*3600, samesite="Lax")
    if device_token:
        resp.set_cookie("kiosk_device_token", device_token, max_age=365*24*3600, samesite="Lax")
    return resp


@app.route("/api/kiosk-data")
def api_kiosk_data():
    """
    Kiosk TV ekranının 15 dakikada bir veri çektiği, heartbeat attığı ve
    dinamik mesafe/rota hesaplamaları yaptığı ana API uç noktası.
    """
    key = request.args.get("key", "").strip().upper()
    if not key:
        key = request.cookies.get("kiosk_license_key", "").strip().upper()

    device_token = request.args.get("device_token", "").strip()
    if not device_token:
        device_token = request.cookies.get("kiosk_device_token", "").strip()

    if not key:
        return jsonify({
            "success": False,
            "license_valid": False,
            "reason": "missing_key",
            "message": "Lisans anahtarı eksik."
        }), 400

    eczane = Pharmacy.query.filter(db.func.upper(Pharmacy.license_key) == key).first()
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

    resolution = request.args.get("res", "").strip()
    mac_addr = request.args.get("mac", "").strip()
    local_ip = request.args.get("local_ip", "").strip()
    user_agent = request.headers.get("User-Agent", "")
    client_ip = istemci_ip_al()

    # Çoklu TV / Kiosk Cihazı Doğrulama ve Kayıt (MAC, Token & Yerel IP)
    aktif_cihaz = None
    if device_token or mac_addr:
        erisim_var, mesaj, cihaz_obj = eczane.cihaz_dogrula_veya_kaydet(
            token=device_token,
            mac=mac_addr,
            local_ip=local_ip,
            ip=client_ip,
            resolution=resolution,
            user_agent=user_agent
        )
        aktif_cihaz = cihaz_obj

        if not erisim_var:
            if cihaz_obj and not cihaz_obj.is_approved:
                # Yönetim panelinden lisans aktivasyonu/onayı bekliyor
                return jsonify({
                    "success": False,
                    "license_valid": True,
                    "reason": "device_pending_approval",
                    "message": mesaj,
                    "device_id": cihaz_obj.id,
                    "device_name": cihaz_obj.device_name,
                    "mac": cihaz_obj.mac_address or mac_addr or "-",
                    "local_ip": cihaz_obj.local_ip or local_ip or "-",
                    "ip": client_ip,
                    "pharmacy_name": eczane.name,
                    "license_key": eczane.license_key
                }), 403
            else:
                # Limit dolu veya başka hata
                return jsonify({
                    "success": False,
                    "license_valid": False,
                    "reason": "device_limit_exceeded",
                    "message": mesaj,
                    "max_devices": eczane.max_devices or 1,
                    "device_count": eczane.devices.count(),
                    "license_key": eczane.license_key
                }), 403
    else:
        eczane.last_ping = datetime.now()
        eczane.last_ip = client_ip
        db.session.commit()

    # Sistem global ayarlarını al (Önbellek süresi ve kaynak öncelikleri)
    sistem_ayari = SystemSetting.get_settings()
    kaynak_sirasi = [
        sistem_ayari.primary_source,
        sistem_ayari.secondary_source,
        sistem_ayari.tertiary_source
    ]

    # İlgili ilçenin nöbetçi eczanelerini Fallback Pipeline ile çek
    kendi_lat = eczane.latitude
    kendi_lon = eczane.longitude
    veri = nobetci_eczaneleri_getir(
        eczane.city, 
        eczane.district, 
        kendi_lat, 
        kendi_lon,
        cache_suresi_dakika=sistem_ayari.cache_duration_minutes,
        kaynak_siralamasi=kaynak_sirasi
    )

    # Otomatik nöbetçi tespiti
    bu_gece_nobetci = False
    eczane_adi_norm = turkce_karakter_temizle(eczane.name)
    for e in veri.get("eczaneler", []):
        gelen_norm = turkce_karakter_temizle(e.get("isim", ""))
        if (eczane_adi_norm in gelen_norm) or (gelen_norm in eczane_adi_norm):
            bu_gece_nobetci = True
            break
        if eczane.latitude and eczane.longitude and e.get("enlem") and e.get("boylam"):
            fark_lat = abs(eczane.latitude - e["enlem"])
            fark_lng = abs(eczane.longitude - e["boylam"])
            if fark_lat < 0.001 and fark_lng < 0.001:
                bu_gece_nobetci = True
                break

    # Cihaza özel ekran ölçeği & Ekranda Tanımlama Sinyali Kontrolü
    # Not: aktif_cihaz yukarıda token/MAC/yerel IP ile doğrulandı; sıfırlanmamalı.
    # (Önceden yalnızca token ile yeniden arandığı için MAC ile eşleşen cihazın teması uygulanmıyordu.)
    if not aktif_cihaz and device_token:
        aktif_cihaz = eczane.devices.filter_by(device_token=device_token).first()

    # Cihaza özel ekran ölçeği, tema ve parametre kontrolü
    cihaz_ekran_olcegi = eczane.screen_scale or "auto"
    cihaz_temasi = eczane.theme or "classic_grid"
    cihaz_tema_ayarlari = eczane.get_theme_settings()
    identify_bilgisi = None

    if aktif_cihaz:
        if aktif_cihaz.screen_scale and aktif_cihaz.screen_scale != "auto":
            cihaz_ekran_olcegi = aktif_cihaz.screen_scale
        cihaz_temasi = aktif_cihaz.get_theme()
        cihaz_tema_ayarlari = aktif_cihaz.get_theme_settings()
        if aktif_cihaz.is_identify_active():
            identify_bilgisi = {
                "active": True,
                "device_id": aktif_cihaz.id,
                "device_name": aktif_cihaz.device_name or f"TV Ekranı {aktif_cihaz.id}",
                "screen_scale": cihaz_ekran_olcegi,
                "code": f"CİHAZ #{aktif_cihaz.id}"
            }
            # Sinyal TV ekranına teslim edildi, tek seferlik olarak tüketilir
            aktif_cihaz.identify_until = None
            db.session.commit()

    # Manuel test veya otomatik tespit kontrolü
    nihai_nobet_durumu = eczane.nobetci_mi()

    return jsonify({
        "success": True,
        "license_valid": True,
        "pharmacy": {
            "id": eczane.id,
            "name": eczane.name,
            "city": eczane.city,
            "district": eczane.district,
            "latitude": eczane.latitude,
            "longitude": eczane.longitude,
            "pharmacist_name": eczane.pharmacist_name or "",
            "chamber_registration_no": eczane.chamber_registration_no or "",
            "phone": eczane.phone or "",
            "mobile_phone": eczane.mobile_phone or "",
            "address": eczane.address or "",
            "ticker_text": eczane.ticker_text,
            "theme": cihaz_temasi,
            "screen_scale": cihaz_ekran_olcegi,
            "max_devices": eczane.max_devices or 1,
            "device_count": eczane.devices.count(),
            "theme_settings": cihaz_tema_ayarlari
        },
        "identify": identify_bilgisi,
        "is_on_duty_today": nihai_nobet_durumu,
        "duty_test_active": eczane.duty_test_aktif_mi(),
        "eczaneler": veri.get("eczaneler", []),
        "kaynak": veri.get("kaynak"),
        "veri_saglayici": veri.get("veri_saglayici"),
        "onbellek_zamani": veri.get("onbellek_zamani"),
        "system_settings": {
            "poll_interval_sec": sistem_ayari.kiosk_poll_interval_sec,
            "duty_start_time": sistem_ayari.duty_start_time,
            "duty_end_time": sistem_ayari.duty_end_time,
            "map_theme": sistem_ayari.map_theme
        }
    })


@app.route("/api/kiosk-ping")
def api_kiosk_ping():
    """
    TV Kiosk ekranının arka planda her 45 saniyede bir attığı hafif heartbeat uç noktası.
    Veritabanına anlık 'last_ping' yazar, ekranın sürekli canlı kalmasını sağlar.
    Ağır nöbetçi sorgulaması yapmaz, sadece canlılık ve identify kontrolü döner.
    """
    key = request.args.get("key", "").strip().upper()
    if not key:
        key = request.cookies.get("kiosk_license_key", "").strip().upper()
    if not key:
        return jsonify({"success": False, "error": "missing_key"}), 400

    eczane = Pharmacy.query.filter(db.func.upper(Pharmacy.license_key) == key).first()
    if not eczane or not eczane.lisans_gecerli_mi():
        return jsonify({"success": False, "error": "invalid_or_expired"}), 403

    device_token = request.args.get("device_token", "").strip() or request.cookies.get("kiosk_device_token", "").strip()
    mac_addr = request.args.get("mac", "").strip()
    local_ip = request.args.get("local_ip", "").strip()
    client_ip = istemci_ip_al()
    resolution = request.args.get("res", "").strip()

    aktif_cihaz = None
    if device_token or mac_addr:
        if mac_addr:
            aktif_cihaz = eczane.devices.filter_by(mac_address=mac_addr).first()
        if not aktif_cihaz and device_token:
            aktif_cihaz = eczane.devices.filter_by(device_token=device_token).first()
        if not aktif_cihaz and local_ip and not local_ip.startswith("127.") and local_ip != "-":
            pasiflik_siniri = datetime.now() - timedelta(minutes=10)
            aktif_cihaz = eczane.devices.filter(
                KioskDevice.local_ip == local_ip,
                KioskDevice.is_approved == True,
                db.or_(KioskDevice.last_ping == None, KioskDevice.last_ping < pasiflik_siniri)
            ).first()

        if aktif_cihaz:
            aktif_cihaz.last_ping = datetime.now()
            if client_ip: aktif_cihaz.ip_address = client_ip
            if local_ip: aktif_cihaz.local_ip = local_ip
            if resolution: aktif_cihaz.screen_resolution = resolution

    eczane.last_ping = datetime.now()
    if client_ip: eczane.last_ip = client_ip
    db.session.commit()

    identify_bilgisi = None
    if aktif_cihaz and aktif_cihaz.is_identify_active():
        identify_bilgisi = {
            "active": True,
            "device_id": aktif_cihaz.id,
            "device_name": aktif_cihaz.device_name,
            "screen_scale": aktif_cihaz.screen_scale or eczane.screen_scale or "auto",
            "code": f"CİHAZ #{aktif_cihaz.id}"
        }
        # Sinyal TV ekranına teslim edildi, tek seferlik olarak tüketilir
        aktif_cihaz.identify_until = None
        db.session.commit()

    return jsonify({
        "success": True,
        "online": True,
        "pharmacy_id": eczane.id,
        "device_id": aktif_cihaz.id if aktif_cihaz else None,
        "identify": identify_bilgisi,
        "server_time": datetime.now().strftime("%H:%M:%S")
    })


# ==========================================
# GÜVENLİ KÖK ROTA VE LİSANS PORTALI
# ==========================================

@app.route("/")
def index():
    """Kök dizin rotası. Key parametresi veya çerezde geçerli lisans varsa kiosk'a yönlendirir, yoksa portalı açar."""
    key = request.args.get("key", "").strip().upper()
    if not key:
        key = request.cookies.get("kiosk_license_key", "").strip().upper()

    if key:
        eczane = Pharmacy.query.filter(db.func.upper(Pharmacy.license_key) == key).first()
        if eczane and eczane.lisans_gecerli_mi():
            return redirect(url_for("kiosk", key=key))

    return render_template("portal.html")


@app.route("/api/nobetci-eczaneler")
def api_nobetci_eczaneler():
    """Genel API uç noktası koruması (Lisans anahtarı zorunludur)."""
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


@app.after_request
def ekle_guvenlik_basliklari(response):
    """TV Kiosk önizleme penceresinin iframe içinde açılabilmesini sağlar."""
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response


# Uygulama ayağa kalktığında veritabanını başlat
init_db()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
