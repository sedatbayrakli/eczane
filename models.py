"""
Nöbetçi Eczane Kiosk Sistemi - Veritabanı Modelleri
SQLite & Flask-SQLAlchemy
"""

import uuid
from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

# SQLAlchemy veritabanı örneği
db = SQLAlchemy()


def lisans_anahtari_uret() -> str:
    """
    Eczaneler için benzersiz ve okunabilir lisans anahtarı üretir.
    Örnek: ECZ-8F2A-4B9C
    """
    rastgele_kod = uuid.uuid4().hex[:8].upper()
    return f"ECZ-{rastgele_kod[:4]}-{rastgele_kod[4:]}"


class AdminUser(db.Model):
    """
    Yönetim Paneli Yönetici Kullanıcı Modeli
    """
    __tablename__ = "admin_users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def sifre_belirle(self, sifre: str):
        """Kullanıcı şifresini güvenli bir şekilde hashler."""
        self.password_hash = generate_password_hash(sifre)

    def sifre_kontrol(self, sifre: str) -> bool:
        """Girilen şifrenin doğruluğunu kontrol eder."""
        return check_password_hash(self.password_hash, sifre)

    def __repr__(self):
        return f"<AdminUser {self.username}>"


class Pharmacy(db.Model):
    """
    Eczane ve Kiosk Lisanslama Modeli (Multi-Tenant)
    """
    __tablename__ = "pharmacies"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)                # Eczane Adı (Örn: Çakırlar Eczanesi)
    city = db.Column(db.String(100), default="İstanbul")            # İl
    district = db.Column(db.String(100), default="Bahçelievler")    # İlçe
    latitude = db.Column(db.Float, nullable=True)                  # Enlem (Koordinat)
    longitude = db.Column(db.Float, nullable=True)                 # Boylam (Koordinat)
    
    # Eczacı ve İletişim Bilgileri
    pharmacist_name = db.Column(db.String(120), nullable=True)         # Eczacı Ad Soyad
    chamber_registration_no = db.Column(db.String(60), nullable=True) # Oda Sicil / GLN No
    phone = db.Column(db.String(30), nullable=True)                   # Sabit Telefon
    mobile_phone = db.Column(db.String(30), nullable=True)            # Eczacı Cep Telefonu
    address = db.Column(db.Text, nullable=True)                       # Eczanenin Kendi Açık Adresi

    # Nöbetçi Durumu ve Hızlı Test Override
    is_on_duty_today = db.Column(db.Boolean, default=False)           # Otomatik Nöbetçi Tespiti
    manual_duty_override_until = db.Column(db.DateTime, nullable=True) # 1 Saatlik Test Nöbeti Bitiş Zamanı
    
    # Lisans Bilgileri
    license_key = db.Column(db.String(64), unique=True, nullable=False, default=lisans_anahtari_uret)
    expires_at = db.Column(db.DateTime, nullable=False)            # Lisans Bitiş Tarihi
    is_active = db.Column(db.Boolean, default=True)                # Lisans Aktif/Pasif Durumu
    
    # Kiosk Özelleştirmeleri
    ticker_text = db.Column(
        db.String(500), 
        default="Eczanemiz halk sağlığı için hizmetinizdedir. Reçeteli ve reçetesiz ilaçlarınız için danışabilirsiniz."
    )
    
    # TV Ekranı Cihaz Kilitleme & IP Takibi (Kaçak Kullanımı Engelleme)
    registered_device_token = db.Column(db.String(128), nullable=True) # İlk bağlanan TV'nin parmak izi
    last_ip = db.Column(db.String(64), nullable=True)                  # TV ekranının son IP adresi
    device_lock_enabled = db.Column(db.Boolean, default=True)          # Tek cihaz kilidi aktif mi

    # TV Ekranı Canlılık Takibi (Heartbeat)
    last_ping = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def nobetci_mi(self) -> bool:
        """
        Eczanenin anlık nöbetçi modunda olup olmadığını kontrol eder.
        1 saatlik manuel test modu veya otomatik sistem tespiti geçerliyse True döner.
        """
        if self.manual_duty_override_until and self.manual_duty_override_until > datetime.now():
            return True
        return bool(self.is_on_duty_today)

    def duty_test_aktif_mi(self) -> bool:
        """1 saatlik hızlı nöbet testinin şu an aktif olup olmadığını döndürür."""
        return bool(self.manual_duty_override_until and self.manual_duty_override_until > datetime.now())

    def cihaz_uyumlu_mu(self, gelen_token: str) -> bool:
        """
        Gelen TV cihaz token'ının bu lisansa kayıtlı cihazla eşleşip eşleşmediğini kontrol eder.
        İlk çalıştırmada cihaz yoksa gelen cihazı bu lisansa kilitler.
        """
        if not self.device_lock_enabled:
            return True
        if not self.registered_device_token:
            # İlk bağlanan TV cihazını kaydet
            self.registered_device_token = gelen_token
            return True
        return self.registered_device_token == gelen_token

    def cihaz_kilidi_sifirla(self):
        """
        Kayıtlı TV cihaz kilidini sıfırlar.
        Böylece eczane yeni bir TV aldığında sisteme bağlanabilir.
        """
        self.registered_device_token = None
        self.last_ip = None

    def lisans_gecerli_mi(self) -> bool:
        """
        Lisansın anlık olarak aktif ve geçerlilik süresi içinde olup olmadığını doğrular.
        """
        if not self.is_active:
            return False
        if not self.expires_at:
            return False
        return self.expires_at >= datetime.now()

    def kalan_gun_sayisi(self) -> int:
        """Lisansın bitmesine kalan gün sayısını hesaplar."""
        if not self.expires_at:
            return 0
        fark = self.expires_at - datetime.now()
        return max(0, fark.days)

    def ekran_cevrimici_mi(self, tolerans_dakika: int = 5) -> bool:
        """
        TV ekranının son 'tolerans_dakika' içinde ping atıp atmadığını kontrol eder (Heartbeat).
        """
        if not self.last_ping:
            return False
        gecen_sure = datetime.now() - self.last_ping
        return gecen_sure <= timedelta(minutes=tolerans_dakika)

    def to_dict(self) -> dict:
        """Model verilerini JSON sözlüğüne dönüştürür."""
        return {
            "id": self.id,
            "name": self.name,
            "city": self.city,
            "district": self.district,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "pharmacist_name": self.pharmacist_name or "",
            "chamber_registration_no": self.chamber_registration_no or "",
            "phone": self.phone or "",
            "mobile_phone": self.mobile_phone or "",
            "address": self.address or "",
            "is_on_duty_today": self.nobetci_mi(),
            "duty_test_active": self.duty_test_aktif_mi(),
            "license_key": self.license_key,
            "expires_at": self.expires_at.strftime("%Y-%m-%d %H:%M") if self.expires_at else None,
            "is_active": self.is_active,
            "is_valid": self.lisans_gecerli_mi(),
            "kalan_gun": self.kalan_gun_sayisi(),
            "is_online": self.ekran_cevrimici_mi(),
            "last_ping": self.last_ping.strftime("%Y-%m-%d %H:%M:%S") if self.last_ping else None,
            "last_ip": self.last_ip or "Bilinmiyor",
            "is_device_locked": bool(self.registered_device_token),
            "ticker_text": self.ticker_text
        }

    def __repr__(self):
        return f"<Pharmacy {self.name} - {self.license_key}>"
