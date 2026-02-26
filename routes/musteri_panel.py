# -*- coding: utf-8 -*-
"""Müşteri paneli – cihaz takip portalı."""
from datetime import datetime
from sqlalchemy import or_, func
from flask import Blueprint, render_template, request, redirect, url_for, flash, session

from models import Device, Customer, Fault
from extensions import db

musteri_panel_bp = Blueprint('musteri_panel', __name__, url_prefix='/musteri-panel')


def _get_musteri_cihazlari(email, telefon):
    """E-posta ve telefon (ikisi zorunlu) ile müşteri cihazlarını getir."""
    email = (email or '').strip().lower()
    telefon = (telefon or '').strip() or None
    if not email or not telefon:
        return []
    # Cihazlar: musteri_email eşleşmesi VEYA Customer üzerinden bağlantı
    cust_ids = [c.id for c in Customer.query.filter(func.lower(Customer.email) == email).all()]
    if cust_ids:
        q = Device.query.filter(
            or_(
                func.lower(Device.musteri_email) == email,
                Device.customer_id.in_(cust_ids),
            )
        )
    else:
        q = Device.query.filter(func.lower(Device.musteri_email) == email)
    q = q.filter(Device.musteri_telefon == telefon)
    return q.order_by(Device.created_at.desc()).all()


def _cihaza_erişim_var(cihaz_id, panel_email, panel_telefon):
    """Cihazın bu e-posta ve telefon ile erişilebilir olup olmadığını kontrol et."""
    cihazlar = _get_musteri_cihazlari(panel_email, panel_telefon)
    return any(c.id == cihaz_id for c in cihazlar)


@musteri_panel_bp.route('/')
def giris():
    """E-posta ile giriş formu."""
    if session.get('musteri_panel_email'):
        return redirect(url_for('musteri_panel.cihazlar'))
    return render_template('musteri_panel/giris.html')


@musteri_panel_bp.route('/giris', methods=['POST'])
def giris_post():
    """E-posta ve telefon ile cihazları ara – ikisi de zorunlu ve uyumlu olmalı."""
    email = (request.form.get('email') or '').strip()
    telefon = (request.form.get('telefon') or '').strip()
    if not email:
        flash('E-posta adresi girin.', 'error')
        return render_template('musteri_panel/giris.html')
    if not telefon:
        flash('Telefon numarası girin.', 'error')
        return render_template('musteri_panel/giris.html')
    cihazlar = _get_musteri_cihazlari(email, telefon)
    if not cihazlar:
        flash('Bu e-posta ve telefon ile eşleşen kayıt bulunamadı. Telefonu cihaz kaydındaki formatta girin (örn: 05551234567).', 'error')
        return render_template('musteri_panel/giris.html')
    session['musteri_panel_email'] = email.lower()
    session['musteri_panel_telefon'] = telefon
    return redirect(url_for('musteri_panel.cihazlar'))


@musteri_panel_bp.route('/cihazlar')
def cihazlar():
    """Müşterinin cihaz listesi."""
    email = session.get('musteri_panel_email')
    telefon = session.get('musteri_panel_telefon')
    if not email or not telefon:
        flash('Lütfen giriş yapın.', 'error')
        return redirect(url_for('musteri_panel.giris'))
    cihazlar = _get_musteri_cihazlari(email, telefon)
    if not cihazlar:
        session.pop('musteri_panel_email', None)
        session.pop('musteri_panel_telefon', None)
        flash('Kayıtlı cihaz bulunamadı.', 'error')
        return redirect(url_for('musteri_panel.giris'))
    musteri_adi = cihazlar[0].musteri_adi if cihazlar else ''
    return render_template('musteri_panel/cihazlar.html', cihazlar=cihazlar, musteri_adi=musteri_adi)


@musteri_panel_bp.route('/cihaz/<int:cihaz_id>')
def cihaz_detay(cihaz_id):
    """Tek cihaz detayı (arıza listesi dahil)."""
    email = session.get('musteri_panel_email')
    telefon = session.get('musteri_panel_telefon')
    if not email or not telefon:
        flash('Lütfen giriş yapın.', 'error')
        return redirect(url_for('musteri_panel.giris'))
    if not _cihaza_erişim_var(cihaz_id, email, telefon):
        flash('Bu cihaza erişim yetkiniz yok.', 'error')
        return redirect(url_for('musteri_panel.cihazlar'))
    cihaz = Device.query.get_or_404(cihaz_id)
    arızalar = Fault.query.filter_by(device_id=cihaz_id).order_by(Fault.id.asc()).all()
    return render_template('musteri_panel/cihaz_detay.html', cihaz=cihaz, arizalar=arızalar)


@musteri_panel_bp.route('/cihaz/<int:cihaz_id>/onayla', methods=['POST'])
def cihaz_onayla(cihaz_id):
    """Ön inceleme raporunu onayla."""
    email = session.get('musteri_panel_email')
    telefon = session.get('musteri_panel_telefon')
    if not email or not telefon or not _cihaza_erişim_var(cihaz_id, email, telefon):
        flash('Bu cihaza erişim yetkiniz yok.', 'error')
        return redirect(url_for('musteri_panel.cihazlar'))
    cihaz = Device.query.get_or_404(cihaz_id)
    if cihaz.durum != 'musteri_onay_bekliyor':
        flash('Bu cihaz onay beklenen durumda değil.', 'error')
        return redirect(url_for('musteri_panel.cihaz_detay', cihaz_id=cihaz_id))
    cihaz.durum = 'musteri_onayladi'
    cihaz.musteri_onay_durumu = 'onaylandi'
    cihaz.musteri_onay_tarihi = datetime.now()
    db.session.commit()
    flash('Ön inceleme raporunu onayladınız.', 'success')
    return redirect(url_for('musteri_panel.cihazlar'))


@musteri_panel_bp.route('/cihaz/<int:cihaz_id>/reddet', methods=['POST'])
def cihaz_reddet(cihaz_id):
    """Ön inceleme raporunu reddet."""
    email = session.get('musteri_panel_email')
    telefon = session.get('musteri_panel_telefon')
    if not email or not telefon or not _cihaza_erişim_var(cihaz_id, email, telefon):
        flash('Bu cihaza erişim yetkiniz yok.', 'error')
        return redirect(url_for('musteri_panel.cihazlar'))
    cihaz = Device.query.get_or_404(cihaz_id)
    if cihaz.durum != 'musteri_onay_bekliyor':
        flash('Bu cihaz onay beklenen durumda değil.', 'error')
        return redirect(url_for('musteri_panel.cihaz_detay', cihaz_id=cihaz_id))
    cihaz.durum = 'musteri_onaylamadi'
    cihaz.musteri_onay_durumu = 'onaylanmadi'
    cihaz.musteri_onay_tarihi = datetime.now()
    db.session.commit()
    flash('Ön inceleme raporunu reddettiniz. Cihazınız işlem yapılmadan size iade edilecektir.', 'info')
    return redirect(url_for('musteri_panel.cihazlar'))


@musteri_panel_bp.route('/cikis')
def cikis():
    """Oturumu kapat."""
    session.pop('musteri_panel_email', None)
    session.pop('musteri_panel_telefon', None)
    flash('Oturumunuz kapatıldı.', 'info')
    return redirect(url_for('musteri_panel.giris'))
