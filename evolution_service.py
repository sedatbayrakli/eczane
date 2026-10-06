import requests
import json
import logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

def telefon_formatla(telefon: str) -> str:
    """
    Telefon numarasını Evolution API formatına (905xxxxxxxxx) dönüştürür.
    """
    if not telefon:
        return ""
    temiz = "".join(filter(str.isdigit, str(telefon)))
    if temiz.startswith("0") and len(temiz) == 11:
        return "9" + temiz
    elif len(temiz) == 10 and temiz.startswith("5"):
        return "90" + temiz
    return temiz

def evolution_whatsapp_gonder(alici_telefon: str, mesaj: str, ayarlar=None) -> Tuple[bool, str]:
    """
    Evolution API v2 üzerinden WhatsApp metin mesajı gönderir.
    Parametreler:
        alici_telefon: Alıcı cep telefonu (Örn: 905321112233)
        mesaj: Gönderilecek metin
        ayarlar: SystemSetting objesi (verilmezse veritabanından çekilir)
    Döndürür:
        (basarili: bool, aciklama: str)
    """
    try:
        from models import SystemSetting
        if not ayarlar:
            ayarlar = SystemSetting.get_settings()

        if not ayarlar.whatsapp_enabled:
            return False, "WhatsApp bildirimleri sistem ayarlarından kapatılmış."

        api_url = (ayarlar.evolution_api_url or "").rstrip("/")
        instance = ayarlar.evolution_instance or "sedat2"
        api_key = ayarlar.evolution_instance_key or ayarlar.evolution_global_key

        if not api_url or not instance or not api_key:
            return False, "Evolution API URL, Instance veya API Key eksik."

        formatli_tel = telefon_formatla(alici_telefon)
        if not formatli_tel or len(formatli_tel) < 10:
            return False, f"Geçersiz telefon numarası: {alici_telefon}"

        # Evolution API v2 sendText endpoint
        endpoint = f"{api_url}/message/sendText/{instance}"
        headers = {
            "Content-Type": "application/json",
            "apikey": api_key
        }
        payload = {
            "number": formatli_tel,
            "text": mesaj,
            "delay": 1200
        }

        resp = requests.post(endpoint, json=payload, headers=headers, timeout=10)
        
        # Log kaydı oluştur
        try:
            from models import db, WhatsAppLog
            log_durum = "sent" if resp.status_code in (200, 201) else "failed"
            err_metin = None if resp.status_code in (200, 201) else f"HTTP {resp.status_code}: {resp.text[:300]}"
            w_log = WhatsAppLog(
                direction="outgoing",
                phone=formatli_tel,
                message=mesaj,
                status=log_durum,
                error_message=err_metin,
                instance=instance,
                raw_response=resp.text[:1000] if resp.text else None
            )
            db.session.add(w_log)
            db.session.commit()
        except Exception as db_err:
            logger.warning(f"WhatsAppLog kaydetme hatası: {db_err}")

        if resp.status_code in (200, 201):
            return True, "WhatsApp mesajı başarıyla iletildi."
        else:
            hata_mesaji = f"Evolution API hatası ({resp.status_code}): {resp.text}"
            logger.warning(hata_mesaji)
            return False, hata_mesaji

    except Exception as e:
        logger.error(f"WhatsApp gönderim istisnası: {e}")
        try:
            from models import db, WhatsAppLog
            w_log = WhatsAppLog(
                direction="outgoing",
                phone=telefon_formatla(alici_telefon) or alici_telefon or "-",
                message=mesaj,
                status="failed",
                error_message=str(e),
                instance=getattr(ayarlar, 'evolution_instance', 'sedat2')
            )
            db.session.add(w_log)
            db.session.commit()
        except Exception:
            pass
        return False, str(e)
