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
    redirect, url_for, session, flash
)
import requests

from models import db, AdminUser, Pharmacy, lisans_anahtari_uret
from services.pharmacy_service import (
    nobetci_eczaneleri_getir,
    turkce_karakter_temizle,
    haversine_mesafe,
    rota_linki_olustur,
    qr_kod_url_olustur,
    eczane_detay_bilgisi_ara
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

    if not isim:
        flash("Eczane adı zorunludur!", "danger")
        return redirect(url_for("admin_dashboard"))

    yeni_eczane = Pharmacy(
        name=isim,
        city=sehir,
        district=ilce,
        latitude=enlem,
        longitude=boylam,
        pharmacist_name=eczaci_adi,
        chamber_registration_no=oda_sicil,
        phone=telefon,
        mobile_phone=cep_telefonu,
        address=acik_adres,
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
    
    eczane.pharmacist_name = request.form.get("pharmacist_name", eczane.pharmacist_name).strip()
    eczane.chamber_registration_no = request.form.get("chamber_registration_no", eczane.chamber_registration_no).strip()
    eczane.phone = request.form.get("phone", eczane.phone).strip()
    eczane.mobile_phone = request.form.get("mobile_phone", eczane.mobile_phone).strip()
    eczane.address = request.form.get("address", eczane.address).strip()
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
    """Eczanenin TV ekranı cihaz kilidini sıfırlar."""
    eczane = Pharmacy.query.get_or_404(eczane_id)
    eczane.cihaz_kilidi_sifirla()
    db.session.commit()

    flash(f"'{eczane.name}' TV cihaz kilidi sıfırlandı. Yeni bağlanacak ilk TV cihazına kilitlenecektir.", "info")
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
    Kiosk TV ekranının 15 dakikada bir veri çektiği, heartbeat attığı ve
    dinamik mesafe/rota hesaplamaları yaptığı ana API uç noktası.
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

    # Cihaz Kilidi Kontrolü
    if device_token:
        if not eczane.cihaz_uyumlu_mu(device_token):
            return jsonify({
                "success": False,
                "license_valid": False,
                "reason": "device_mismatch",
                "message": "Bu lisans anahtarı başka bir TV cihazına kilitlenmiştir. Lisansınızı tek ekranda kullanabilirsiniz."
            }), 403

    # Canlılık ve IP güncellemesi
    eczane.last_ping = datetime.now()
    eczane.last_ip = istemci_ip_al()

    # İlgili ilçenin nöbetçi eczanelerini Fallback Pipeline ile çek
    kendi_lat = eczane.latitude
    kendi_lon = eczane.longitude
    veri = nobetci_eczaneleri_getir(eczane.city, eczane.district, kendi_lat, kendi_lon)

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

    eczane.is_on_duty_today = bu_gece_nobetci
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
            "ticker_text": eczane.ticker_text
        },
        "is_on_duty_today": nihai_nobet_durumu,
        "duty_test_active": eczane.duty_test_aktif_mi(),
        "eczaneler": veri.get("eczaneler", []),
        "kaynak": veri.get("kaynak"),
        "veri_saglayici": veri.get("veri_saglayici"),
        "onbellek_zamani": veri.get("onbellek_zamani")
    })


# ==========================================
# GÜVENLİ KÖK ROTA VE LİSANS PORTALI
# ==========================================

@app.route("/")
def index():
    """Kök dizin rotası. Key varsa kiosk'a yönlendirir, yoksa portalı açar."""
    key = request.args.get("key", "").strip()
    if key:
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


# Uygulama ayağa kalktığında veritabanını başlat
init_db()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
