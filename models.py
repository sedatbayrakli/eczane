import uuid
from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

def lisans_anahtari_uret() -> str:
    """Ticari SaaS formatında benzersiz lisans anahtarı üretir (Örn: ECZ-8F2A-4B1C)."""
    rastgele_uuid = uuid.uuid4().hex.upper()
    return f"ECZ-{rastgele_uuid[:4]}-{rastgele_uuid[4:8]}"


class AdminUser(db.Model):
    """
    Sisteme giriş yapacak yönetici kullanıcıları modeli.
    Şifreler Werkzeug pbkdf2 algoritması ile hashlenerek saklanır.
    """
    __tablename__ = "admin_users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def sifre_belirle(self, sifre: str):
        """Düz metin şifreyi güvenli hash'e dönüştürür."""
        self.password_hash = generate_password_hash(sifre)

    def sifre_kontrol(self, sifre: str) -> bool:
        """Kullanıcının girdiği şifreyi doğrular."""
        return check_password_hash(self.password_hash, sifre)

    def __repr__(self):
        return f"<AdminUser {self.username}>"


class KioskDevice(db.Model):
    """
    Lisansa bağlı TV, Android Box (Mi Box) veya kiosk ekranı cihaz modeli.
    Tek bir lisansa birden fazla ekranın (Örn: Vitrin TV, Kasa Arkası TV) bağlanabilmesini sağlar.
    """
    __tablename__ = "kiosk_devices"

    id = db.Column(db.Integer, primary_key=True)
    pharmacy_id = db.Column(db.Integer, db.ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=False)
    device_token = db.Column(db.String(128), unique=True, nullable=False)
    device_name = db.Column(db.String(100), default="TV Ekranı")
    ip_address = db.Column(db.String(64), nullable=True)
    screen_resolution = db.Column(db.String(50), nullable=True) # Örn: "1920x1080", "1280x720"
    user_agent = db.Column(db.String(256), nullable=True)
    last_ping = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def is_online(self, tolerans_dakika: int = 5) -> bool:
        """Cihazın son 5 dakika içinde ping atıp atmadığını kontrol eder."""
        if not self.last_ping:
            return False
        return (datetime.now() - self.last_ping) <= timedelta(minutes=tolerans_dakika)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "pharmacy_id": self.pharmacy_id,
            "device_token": self.device_token,
            "device_name": self.device_name,
            "ip_address": self.ip_address or "-",
            "screen_resolution": self.screen_resolution or "Bilinmiyor",
            "is_online": self.is_online(),
            "last_ping": self.last_ping.strftime("%H:%M:%S") if self.last_ping else None,
            "created_at": self.created_at.strftime("%d.%m.%Y") if self.created_at else None
        }


class Pharmacy(db.Model):
    """
    Lisanslı eczane modeli.
    Multi-tenant yapıda her eczane kendi ilçesini, konumunu ve cihazlarını yönetir.
    """
    __tablename__ = "pharmacies"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)                    # Eczane Adı
    city = db.Column(db.String(50), default="İstanbul", nullable=False) # İl
    district = db.Column(db.String(80), nullable=False)                 # İlçe
    latitude = db.Column(db.Float, nullable=True)                       # Enlem
    longitude = db.Column(db.Float, nullable=True)                      # Boylam

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
    
    # Çoklu Cihaz ve Ekran Çözünürlük/Ölçek Yönetimi
    max_devices = db.Column(db.Integer, default=1, nullable=False) # İzin verilen maksimum cihaz sayısı (1, 2, 3, 5...)
    screen_scale = db.Column(db.String(20), default="auto", nullable=False) # 'auto', '1080p', '720p', 'compact', '4k'

    # Kiosk Özelleştirmeleri
    ticker_text = db.Column(
        db.String(500), 
        default="Eczanemiz halk sağlığı için hizmetinizdedir. Reçeteli ve reçetesiz ilaçlarınız için danışabilirsiniz."
    )
    # Kiosk Ekran Teması ('classic_grid', 'animated_route', 'focus_carousel', 'dual_card', 'auto_rotate')
    theme = db.Column(db.String(50), default="classic_grid", nullable=False)
    
    # TV Ekranı Cihaz Kilitleme & IP Takibi
    registered_device_token = db.Column(db.String(128), nullable=True) # Geriye dönük uyumluluk için
    last_ip = db.Column(db.String(64), nullable=True)                  # TV ekranının son IP adresi
    device_lock_enabled = db.Column(db.Boolean, default=True)          # Cihaz kilidi aktif mi

    # TV Ekranı Canlılık Takibi (Heartbeat)
    last_ping = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Bağlı Cihazlar İlişkisi (One-to-Many)
    devices = db.relationship('KioskDevice', backref='pharmacy', cascade='all, delete-orphan', lazy='dynamic')

    def nobetci_mi(self) -> bool:
        """Eczanenin anlık nöbetçi modunda olup olmadığını kontrol eder."""
        if self.manual_duty_override_until and self.manual_duty_override_until > datetime.now():
            return True
        return bool(self.is_on_duty_today)

    def duty_test_aktif_mi(self) -> bool:
        """1 saatlik hızlı nöbet testinin şu an aktif olup olmadığını döndürür."""
        return bool(self.manual_duty_override_until and self.manual_duty_override_until > datetime.now())

    def cihaz_dogrula_veya_kaydet(self, token: str, ip: str = None, resolution: str = None, user_agent: str = None) -> tuple[bool, str]:
        """
        Gelen TV/Kiosk cihazını doğrular veya yeni cihaz olarak kaydeder.
        Lisansın max_devices sınırına göre kontrol yapar.
        Döndürür: (basarili_mi: bool, aciklama: str)
        """
        if not self.device_lock_enabled:
            self.last_ping = datetime.now()
            if ip: self.last_ip = ip
            return True, "Cihaz kilidi devre dışı"

        if not token:
            return False, "Cihaz belirteci (token) eksik"

        # 1. Cihaz zaten bu lisansa kayıtlı mı?
        kayitli_cihaz = self.devices.filter_by(device_token=token).first()
        if kayitli_cihaz:
            kayitli_cihaz.last_ping = datetime.now()
            if ip: kayitli_cihaz.ip_address = ip
            if resolution: kayitli_cihaz.screen_resolution = resolution
            if user_agent: kayitli_cihaz.user_agent = user_agent[:250]
            self.last_ping = datetime.now()
            if ip: self.last_ip = ip
            return True, "Kayıtlı cihaz doğrulandı"

        # 2. Cihaz henüz kayıtlı değil, limit doldu mu?
        mevcut_cihaz_sayisi = self.devices.count()
        if mevcut_cihaz_sayisi >= (self.max_devices or 1):
            return False, f"Lisans cihaz limiti dolu ({mevcut_cihaz_sayisi}/{self.max_devices}). Başka bir cihazdan bağlantıyı kesin veya limiti artırın."

        # 3. Limitte yer var, yeni cihazı lisansa kaydet
        yeni_ad = f"TV Ekranı {mevcut_cihaz_sayisi + 1}"
        yeni_cihaz = KioskDevice(
            pharmacy_id=self.id,
            device_token=token,
            device_name=yeni_ad,
            ip_address=ip,
            screen_resolution=resolution,
            user_agent=user_agent[:250] if user_agent else None,
            last_ping=datetime.now()
        )
        db.session.add(yeni_cihaz)
        self.last_ping = datetime.now()
        if ip: self.last_ip = ip
        # Geriye dönük uyumluluk için ilk cihazı ana alana da yaz
        if not self.registered_device_token:
            self.registered_device_token = token
        db.session.commit()

        return True, "Yeni cihaz başarıyla lisansa eklendi"

    def tum_cihazlari_sifirla(self):
        """Eczaneye bağlı tüm cihaz kayıtlarını sıfırlar."""
        KioskDevice.query.filter_by(pharmacy_id=self.id).delete()
        self.registered_device_token = None
        self.last_ip = None
        db.session.commit()

    def lisans_gecerli_mi(self) -> bool:
        """Lisansın aktif ve süresi içinde olup olmadığını doğrular."""
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
        """Herhangi bir TV ekranının son 5 dakikada ping atıp atmadığını kontrol eder."""
        # 1. İlişkili cihazlardan biri aktif mi?
        for dev in self.devices:
            if dev.is_online(tolerans_dakika):
                return True
        # 2. Ana last_ping aktif mi?
        if not self.last_ping:
            return False
        gecen_sure = datetime.now() - self.last_ping
        return gecen_sure <= timedelta(minutes=tolerans_dakika)

    def to_dict(self) -> dict:
        """Model verilerini JSON sözlüğüne dönüştürür."""
        cihazlar = [d.to_dict() for d in self.devices]
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
            "is_device_locked": bool(self.devices.count() > 0 or self.registered_device_token),
            "max_devices": self.max_devices or 1,
            "device_count": len(cihazlar),
            "screen_scale": self.screen_scale or "auto",
            "devices": cihazlar,
            "ticker_text": self.ticker_text,
            "theme": self.theme or "classic_grid"
        }

    def __repr__(self):
        return f"<Pharmacy {self.name} - {self.license_key}>"
