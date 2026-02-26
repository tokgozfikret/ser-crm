# -*- coding: utf-8 -*-
"""E-posta gönderme yardımcıları."""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from flask import current_app


def get_mail_config():
    """Veritabanından mail yapılandırmasını al."""
    try:
        from models import MailConfig
        cfg = MailConfig.query.first()
        return cfg
    except Exception:
        return None


def send_email(to_email: str, subject: str, body_html: str, body_text: str = None) -> tuple[bool, str]:
    """
    E-posta gönder.

    Returns:
        (başarılı_mı, hata_mesajı_veya_boş)
    """
    cfg = get_mail_config()
    if not cfg or not cfg.smtp_host or not cfg.from_email:
        return False, "Mail sunucusu yapılandırılmamış. Admin panelinden Mail Ayarları bölümünü doldurun."

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = cfg.from_email
        msg['To'] = to_email

        if body_text:
            msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
        msg.attach(MIMEText(body_html, 'html', 'utf-8'))

        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port or 587) as server:
            if cfg.use_tls:
                server.starttls()
            if cfg.smtp_username and cfg.smtp_password:
                server.login(cfg.smtp_username, cfg.smtp_password)
            server.sendmail(cfg.from_email, [to_email], msg.as_string())

        return True, ""
    except smtplib.SMTPAuthenticationError as e:
        return False, f"Mail sunucu kimlik doğrulama hatası: {e}"
    except smtplib.SMTPException as e:
        return False, f"Mail gönderim hatası: {e}"
    except Exception as e:
        return False, str(e)


def send_email_with_attachment(
    to_email: str,
    subject: str,
    body_html: str,
    attachment_bytes: bytes,
    attachment_filename: str,
    body_text: str = None,
) -> tuple[bool, str]:
    """
    E-posta gönder; ekte PDF/dosya ekle.
    Returns:
        (başarılı_mı, hata_mesajı_veya_boş)
    """
    cfg = get_mail_config()
    if not cfg or not cfg.smtp_host or not cfg.from_email:
        return False, "Mail sunucusu yapılandırılmamış. Admin panelinden Mail Ayarları bölümünü doldurun."

    try:
        msg = MIMEMultipart()
        msg['Subject'] = subject
        msg['From'] = cfg.from_email
        msg['To'] = to_email

        if body_text:
            msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
        msg.attach(MIMEText(body_html, 'html', 'utf-8'))

        part = MIMEBase('application', 'pdf')
        part.set_payload(attachment_bytes)
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', 'attachment', filename=('utf-8', '', attachment_filename))
        msg.attach(part)

        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port or 587) as server:
            if cfg.use_tls:
                server.starttls()
            if cfg.smtp_username and cfg.smtp_password:
                server.login(cfg.smtp_username, cfg.smtp_password)
            server.sendmail(cfg.from_email, [to_email], msg.as_string())

        return True, ""
    except smtplib.SMTPAuthenticationError as e:
        return False, f"Mail sunucu kimlik doğrulama hatası: {e}"
    except smtplib.SMTPException as e:
        return False, f"Mail gönderim hatası: {e}"
    except Exception as e:
        return False, str(e)
