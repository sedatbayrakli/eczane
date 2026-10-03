from __future__ import annotations
import uuid
from datetime import datetime, timedelta
from typing import Tuple, Dict, Any, List, Optional
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
    mac_address = db.Column(db.String(64), nullable=True, index=True) # Cihaz MAC / Donanım Parmak İzi
    local_ip = db.Column(db.String(64), nullable=True)               # Cihaz Yerel Ağ IP Adresi (192.168.x.x)
    device_name = db.Column(db.String(100), default="TV Ekranı")
    ip_address = db.Column(db.String(64), nullable=True)              # Dış / Ağ IP Adresi
    screen_resolution = db.Column(db.String(50), nullable=True)       # Örn: "1920x1080", "1280x720"
    screen_scale = db.Column(db.String(20), default="auto", nullable=False) # 'auto', 'compact', '720p', '1080p', '4k'
    theme = db.Column(db.String(50), nullable=True)                          # 'classic_grid', 'animated_route', 'focus_carousel', 'dual_card', 'auto_rotate'
    theme_settings = db.Column(db.Text, nullable=True)                       # Cihaza özel tema parametreleri (JSON)
    identify_until = db.Column(db.DateTime, nullable=True)            # Ekranda tanımlama / parlatma sinyali süresi
    is_approved = db.Column(db.Boolean, default=True, nullable=False) # Yönetici tarafından lisans aktif edildi mi?
    approved_at = db.Column(db.DateTime, nullable=True)               # Lisansın aktif edildiği tarih
    user_agent = db.Column(db.String(256), nullable=True)
    last_ping = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def is_online(self, tolerans_dakika: int = 5) -> bool:
        """Cihazın son 5 dakika içinde ping atıp atmadığını kontrol eder."""
        if not self.last_ping:
            return False
        return (datetime.now() - self.last_ping) <= timedelta(minutes=tolerans_dakika)

    def ekran_cevrimici_mi(self, tolerans_dakika: int = 5) -> bool:
        """Cihazın canlılık durumunu kontrol eder (is_online alias)."""
        return self.is_online(tolerans_dakika)

    def son_sinyal_metni(self) -> str:
        """Cihazın son ping sinyalinin ne kadar önce geldiğini döner."""
        if not self.last_ping:
            return "Sinyal yok"
        toplam_sn = max(0, int((datetime.now() - self.last_ping).total_seconds()))
        if toplam_sn < 60:
            return f"{toplam_sn} sn önce"
        elif toplam_sn < 3600:
            return f"{toplam_sn // 60} dk önce"
        else:
            return f"{toplam_sn // 3600} sa önce"

    def cihazi_tanimla(self, saniye: int = 25):
        """Bu cihaza ekranda tanımlama sinyali gönderir."""
        self.identify_until = datetime.now() + timedelta(seconds=saniye)

    def is_identify_active(self) -> bool:
        """Tanımlama sinyalinin halen aktif olup olmadığını kontrol eder."""
        return bool(self.identify_until and self.identify_until > datetime.now())

    def get_theme(self) -> str:
        """Cihaza özel tanımlı tema yoksa eczanenin varsayılan temasını döndürür."""
        if self.theme and self.theme.strip():
            return self.theme.strip()
        if self.pharmacy and self.pharmacy.theme:
            return self.pharmacy.theme
        return "classic_grid"

    def get_theme_settings(self) -> dict:
        """Cihaza özel tema parametrelerini döndürür (varsayılanlarla harmanlanmış)."""
        import json
        varsayilan = {
            "carousel_interval_sec": 10,
            "auto_rotate_minutes": 60,
            "map_zoom": 14,
            "show_countdown": True,
            "show_qr": True,
            "show_travel_times": False,
            "show_district_counter": True,
            "show_landmark": True,
            "anti_burn_in": True,
            "ticker_speed_px": 55
        }
        # Önce eczanenin global parametrelerini temel al
        if self.pharmacy:
            varsayilan.update(self.pharmacy.get_theme_settings())
        # Cihaza özel ayarlar varsa üzerine yaz
        if self.theme_settings:
            try:
                kayitli = json.loads(self.theme_settings)
                if isinstance(kayitli, dict):
                    varsayilan.update(kayitli)
            except Exception:
                pass
        return varsayilan

    def set_theme_settings(self, ayarlar: dict):
        """Cihaza özel tema parametrik ayarlarını kaydeder."""
        import json
        guncel = self.get_theme_settings()
        if isinstance(ayarlar, dict):
            guncel.update(ayarlar)
        self.theme_settings = json.dumps(guncel, ensure_ascii=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "pharmacy_id": self.pharmacy_id,
            "device_token": self.device_token,
            "mac_address": self.mac_address or "-",
            "local_ip": self.local_ip or "-",
            "device_name": self.device_name,
            "ip_address": self.ip_address or "-",
            "screen_resolution": self.screen_resolution or "Bilinmiyor",
            "screen_scale": self.screen_scale or "auto",
            "theme": self.get_theme(),
            "theme_settings": self.get_theme_settings(),
            "is_approved": bool(self.is_approved),
            "approved_at": self.approved_at.strftime("%d.%m.%Y %H:%M") if self.approved_at else None,
            "identify_active": self.is_identify_active(),
            "is_online": self.is_online(),
            "last_ping": self.last_ping.strftime("%H:%M:%S") if self.last_ping else None,
            "last_ping_ago": self.son_sinyal_metni(),
            "created_at": self.created_at.strftime("%d.%m.%Y") if self.created_at else None
        }


class Pharmacy(db.Model):
    """
    Lisanslı eczane modeli.
    Multi-tenant yapıda her eczane kendi ilçesini, konumunu ve cihazlarını yönetir.
    """
    __tablename__ = "pharmacies"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)                    # Eczane / Kurum Adı
    institution_type = db.Column(db.String(50), default="pharmacy", nullable=False) # 'pharmacy', 'hospital', 'medical_center', 'policlinic', 'other'
    institution_settings = db.Column(db.Text, default='{}', nullable=False)        # Kurum tipine özel modüler ayarlar (JSON)
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
    # Kiosk Ekran Teması Parametrik Ayarları (JSON)
    theme_settings = db.Column(db.Text, default='{}', nullable=False)
    
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

    def cihaz_dogrula_veya_kaydet(self, token: str, mac: str = None, local_ip: str = None, ip: str = None, resolution: str = None, user_agent: str = None) -> tuple[bool, str, object]:
        """
        Gelen TV/Kiosk cihazını doğrular veya yeni cihaz olarak kaydeder.
        MAC adresi ve yerel IP eşleşmesini kontrol eder.
        Döndürür: (erisim_izni_var_mi: bool, aciklama: str, cihaz_nesnesi: KioskDevice)
        """
        if not self.device_lock_enabled:
            self.last_ping = datetime.now()
            if ip: self.last_ip = ip
            return True, "Cihaz kilidi devre dışı", None

        if not token and not mac:
            return False, "Cihaz belirteci (token/MAC) eksik", None

        # 1. Cihaz zaten bu lisansa kayıtlı mı?
        kayitli_cihaz = None
        if mac:
            kayitli_cihaz = self.devices.filter_by(mac_address=mac).first()
        if not kayitli_cihaz and token:
            kayitli_cihaz = self.devices.filter_by(device_token=token).first()

        # C) Akıllı Cihaz & Yerel IP Eşleştirmesi:
        # TV Bro veya tarayıcı önbelleği silinse bile aynı TV kutusunun yerel IP'si (örn: 192.168.1.10)
        # zaten onaylı bir cihaza aitse, mükerrer cihaz kaydı açmak yerine o onaylı cihazı koru ve güncelle.
        if not kayitli_cihaz and local_ip and not local_ip.startswith("127.") and local_ip != "-":
            ayni_ip_onayli_cihaz = self.devices.filter(
                KioskDevice.local_ip == local_ip,
                KioskDevice.is_approved == True
            ).first()
            if ayni_ip_onayli_cihaz:
                kayitli_cihaz = ayni_ip_onayli_cihaz
                if mac:
                    kayitli_cihaz.mac_address = mac
                if token:
                    kayitli_cihaz.device_token = token

        if kayitli_cihaz:
            kayitli_cihaz.last_ping = datetime.now()
            if mac: kayitli_cihaz.mac_address = mac
            if local_ip: kayitli_cihaz.local_ip = local_ip
            if ip: kayitli_cihaz.ip_address = ip
            if resolution: kayitli_cihaz.screen_resolution = resolution
            if user_agent: kayitli_cihaz.user_agent = user_agent[:250]
            self.last_ping = datetime.now()
            if ip: self.last_ip = ip
            db.session.commit()

            # Cihaz yönetici tarafından onaylanmış mı?
            if kayitli_cihaz.is_approved:
                return True, "Kayıtlı ve onaylı cihaz doğrulandı", kayitli_cihaz
            else:
                return False, "Bu cihaz yönetim panelinden onay bekliyor. Lütfen yönetici panelinden lisansı aktif ediniz.", kayitli_cihaz

        # 2. Cihaz henüz kayıtlı değil, lisans limitinde yer var mı?
        mevcut_cihaz_sayisi = self.devices.count()
        if mevcut_cihaz_sayisi >= (self.max_devices or 1):
            return False, f"Lisans cihaz limiti dolu ({mevcut_cihaz_sayisi}/{self.max_devices}). Başka bir cihazdan bağlantıyı kesin veya limiti artırın.", None

        # 3. Limitte yer var, yeni cihazı lisansa kaydet (Varsayılan olarak onay bekliyor!)
        yeni_ad = f"TV Ekranı {mevcut_cihaz_sayisi + 1}"
        yeni_cihaz = KioskDevice(
            pharmacy_id=self.id,
            device_token=token or f"tv-{mac}",
            mac_address=mac,
            local_ip=local_ip,
            device_name=yeni_ad,
            ip_address=ip,
            screen_resolution=resolution,
            screen_scale=self.screen_scale or "auto",
            theme=self.theme or "classic_grid",
            theme_settings=self.theme_settings or "{}",
            is_approved=False, # Yeni bağlanan cihaz admin onayına düşer
            user_agent=user_agent[:250] if user_agent else None,
            last_ping=datetime.now()
        )
        db.session.add(yeni_cihaz)
        self.last_ping = datetime.now()
        if ip: self.last_ip = ip
        if not self.registered_device_token:
            self.registered_device_token = token
        db.session.commit()

        return False, "Yeni cihaz sisteme eklendi ve onay bekliyor. Yönetim panelinden lisansı aktif ediniz.", yeni_cihaz

    def cihaz_uyumlu_mu(self, token: str) -> bool:
        """
        Gelen cihaz belirtecinin (token) bu eczane lisansına erişip erişemeyeceğini kontrol eder.
        Cihaz kilidi kapalıysa, token kayıtlıysa veya yeni cihaz limiti henüz dolmamışsa True döner.
        """
        if not self.device_lock_enabled:
            return True
        if not token:
            return True
        if self.registered_device_token and self.registered_device_token == token:
            return True
        cihaz = self.devices.filter_by(device_token=token).first()
        if cihaz:
            return True
        # Henüz kayıtlı değilse, cihaz limitinde yer varsa izin ver (ekranda onay bekliyor uyarısı çıkabilsin)
        if self.devices.count() < (self.max_devices or 1):
            return True
        return False

    def son_sinyal_metni(self) -> str:
        """Eczanenin son TV canlılık sinyalinin ne kadar önce geldiğini döner."""
        # Cihazlardan en güncel olanı al
        en_yeni_ping = self.last_ping
        for dev in self.devices:
            if dev.last_ping:
                if not en_yeni_ping or dev.last_ping > en_yeni_ping:
                    en_yeni_ping = dev.last_ping

        if not en_yeni_ping:
            return "Sinyal yok"
        toplam_sn = max(0, int((datetime.now() - en_yeni_ping).total_seconds()))
        if toplam_sn < 60:
            return f"{toplam_sn} sn önce"
        elif toplam_sn < 3600:
            return f"{toplam_sn // 60} dk önce"
        else:
            return f"{toplam_sn // 3600} sa önce"

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

    def get_institution_type_label(self) -> str:
        """Kurum türünün Türkçe etiketini ve ikonunu döndürür."""
        labels = {
            "pharmacy": "💊 Eczane",
            "hospital": "🏥 Hastane",
            "medical_center": "🩺 Tıp Merkezi",
            "policlinic": "🏢 Poliklinik",
            "other": "🏛️ Diğer Kurum"
        }
        return labels.get(self.institution_type or "pharmacy", "💊 Eczane")

    def get_institution_settings(self) -> dict:
        """İleride farklı kurum tiplerine özel modül ayarlarını döndürür."""
        import json
        if self.institution_settings:
            try:
                veri = json.loads(self.institution_settings)
                if isinstance(veri, dict):
                    return veri
            except Exception:
                pass
        return {}

    def set_institution_settings(self, ayarlar: dict):
        """Farklı kurum tiplerine özel ayarları kaydeder."""
        import json
        guncel = self.get_institution_settings()
        if isinstance(ayarlar, dict):
            guncel.update(ayarlar)
        self.institution_settings = json.dumps(guncel, ensure_ascii=False)

    def to_dict(self) -> dict:
        """Model verilerini JSON sözlüğüne dönüştürür."""
        cihazlar = [d.to_dict() for d in self.devices]
        return {
            "id": self.id,
            "name": self.name,
            "institution_type": self.institution_type or "pharmacy",
            "institution_type_label": self.get_institution_type_label(),
            "institution_settings": self.get_institution_settings(),
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
            "last_ping_ago": self.son_sinyal_metni(),
            "last_ip": self.last_ip or "Bilinmiyor",
            "is_device_locked": bool(self.devices.count() > 0 or self.registered_device_token),
            "max_devices": self.max_devices or 1,
            "device_count": len(cihazlar),
            "screen_scale": self.screen_scale or "auto",
            "devices": cihazlar,
            "ticker_text": self.ticker_text,
            "theme": self.theme or "classic_grid",
            "theme_settings": self.get_theme_settings()
        }

    def get_theme_settings(self) -> dict:
        """Kiosk ekran teması parametrik ayarlarını döndürür (varsayılanlarla harmanlanmış)."""
        import json
        varsayilan = {
            "carousel_interval_sec": 10,
            "auto_rotate_minutes": 60,
            "map_zoom": 14,
            "show_countdown": True,
            "show_qr": True,
            "show_travel_times": False,
            "show_district_counter": True,
            "show_landmark": True,
            "anti_burn_in": True,
            "ticker_speed_px": 55
        }
        if not self.theme_settings:
            return varsayilan
        try:
            kayitli = json.loads(self.theme_settings)
            if isinstance(kayitli, dict):
                varsayilan.update(kayitli)
        except Exception:
            pass
        return varsayilan

    def set_theme_settings(self, ayarlar: dict):
        """Kiosk ekran teması parametrik ayarlarını günceller."""
        import json
        guncel = self.get_theme_settings()
        if isinstance(ayarlar, dict):
            guncel.update(ayarlar)
        self.theme_settings = json.dumps(guncel, ensure_ascii=False)

    def __repr__(self):
        return f"<Pharmacy {self.name} - {self.license_key}>"


class SystemSetting(db.Model):
    """
    Tüm SaaS sisteminin global ayarlarını saklar (Singleton Model).
    Veri çekme sıklığı, kaynak öncelikleri, TV yenileme süresi ve nöbet saatlerini yönetir.
    """
    __tablename__ = "system_settings"

    id = db.Column(db.Integer, primary_key=True)
    cache_duration_minutes = db.Column(db.Integer, default=30, nullable=False) # Veri çekme sıklığı (dk)
    primary_source = db.Column(db.String(50), default="ieo_resmi", nullable=False) # 1. Öncelikli Kaynak
    secondary_source = db.Column(db.String(50), default="eczaneler_gen_tr", nullable=False) # 2. Öncelikli Kaynak
    tertiary_source = db.Column(db.String(50), default="nobetcieczaneler_org", nullable=False) # 3. Öncelikli Kaynak
    kiosk_poll_interval_sec = db.Column(db.Integer, default=60, nullable=False) # TV Kiosk Sorgulama Sıklığı (sn)
    duty_start_time = db.Column(db.String(10), default="19:00", nullable=False) # Nöbet Başlangıç Saati
    duty_end_time = db.Column(db.String(10), default="09:00", nullable=False) # Nöbet Bitiş Saati
    heartbeat_tolerance_min = db.Column(db.Integer, default=5, nullable=False) # Çevrimdışı Sinyal Toleransı (dk)
    max_search_distance_km = db.Column(db.Integer, default=15, nullable=False) # Maksimum Nöbetçi Çemberi (km)
    map_theme = db.Column(db.String(50), default="cartodb_dark", nullable=False) # Harita Sağlayıcı Stili
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @classmethod
    def get_settings(cls):
        """Tekil sistem ayarları kaydını getirir veya yoksa varsayılanlarla oluşturur."""
        ayar = cls.query.first()
        if not ayar:
            ayar = cls()
            db.session.add(ayar)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
        return ayar

    def to_dict(self) -> dict:
        return {
            "cache_duration_minutes": self.cache_duration_minutes,
            "primary_source": self.primary_source,
            "secondary_source": self.secondary_source,
            "tertiary_source": self.tertiary_source,
            "kiosk_poll_interval_sec": self.kiosk_poll_interval_sec,
            "duty_start_time": self.duty_start_time,
            "duty_end_time": self.duty_end_time,
            "heartbeat_tolerance_min": self.heartbeat_tolerance_min,
            "max_search_distance_km": self.max_search_distance_km,
            "map_theme": self.map_theme,
            "updated_at": self.updated_at.strftime("%d.%m.%Y %H:%M") if self.updated_at else None
        }


class TickerTemplate(db.Model):
    """
    Sık kullanılan kayan yazı / duyuru şablonları.
    Yöneticinin ekleyip düzenleyebileceği, silebileceği ve tek tıkla uygulayabileceği duyurular.
    """
    __tablename__ = "ticker_templates"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False) # Örn: "Sağlıklı Günler"
    text = db.Column(db.Text, nullable=False)          # Örn: "{eczane} sağlıklı günler diler."
    is_default = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "text": self.text,
            "is_default": self.is_default,
            "created_at": self.created_at.strftime("%d.%m.%Y") if self.created_at else None
        }

    @classmethod
    def seed_defaults(cls):
        """Varsayılan şablonlar yoksa oluşturur."""
        if cls.query.count() == 0:
            sablonlar = [
                cls(title="Sağlıklı Günler", text="{eczane} sağlıklı günler diler.", is_default=True),
                cls(title="Kesintisiz Nöbet", text="{eczane} sabaha kadar kesintisiz nöbet hizmeti vermektedir. Sağlıklı günler dileriz.", is_default=True),
                cls(title="Reçeteli & Medikal", text="{eczane} sağlıklı günler diler. Reçeteli ilaçlarınız ve medikal ihtiyaçlarınız için danışabilirsiniz.", is_default=True)
            ]
            for s in sablonlar:
                db.session.add(s)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

