# -*- coding: utf-8 -*-
from datetime import datetime, time
from functools import wraps
from urllib.parse import urlencode
from flask import Blueprint, render_template, redirect, url_for, request, flash, Response
from flask_login import login_required, current_user
from sqlalchemy import or_, func, and_
from models import Device, Customer, Fault, Sale
from extensions import db
from logging_utils import log_event
from mail_utils import send_email, send_email_with_attachment
try:
    from on_inceleme_pdf import build_on_inceleme_pdf
except ModuleNotFoundError:
    build_on_inceleme_pdf = None  # reportlab yoksa PDF eklenmez

secretary_bp = Blueprint('secretary', __name__)


def _get_or_create_customer(ad, telefon=None, adres=None, email=None, lokasyon_proje=None):
    """Ad + telefon kombinasyonuna göre müşteri bulur/yoksa oluşturur, e-posta ve lokasyon/proje bilgisini de günceller."""
    ad = (ad or '').strip()
    telefon = (telefon or '').strip() or None
    adres = (adres or '').strip() or None
    email = (email or '').strip() or None
    lokasyon_proje = (lokasyon_proje or '').strip() or None
    if not ad:
        return None
    q = Customer.query.filter(Customer.ad == ad)
    if telefon:
        q = q.filter(Customer.telefon == telefon)
    musteri = q.first()
    if not musteri:
        musteri = Customer(ad=ad, telefon=telefon, adres=adres, email=email, lokasyon_proje=lokasyon_proje)
        db.session.add(musteri)
        db.session.flush()
    else:
        # Var olan müşteride e-posta boşsa ve yeni bir e-posta geldiyse güncelle.
        if email and not musteri.email:
            musteri.email = email
        if lokasyon_proje and not getattr(musteri, 'lokasyon_proje', None):
            musteri.lokasyon_proje = lokasyon_proje
    return musteri


PARA_BIRIMI_SEMBOLLER = {'TL': '₺', 'USD': '$', 'EUR': '€', 'GBP': '£'}


def _fiyat_fmt(v, para_birimi='TL'):
    sembol = PARA_BIRIMI_SEMBOLLER.get((para_birimi or 'TL').upper(), '₺')
    try:
        return f'{float(v):,.2f} {sembol}'
    except (TypeError, ValueError):
        return (str(v) + ' ' + sembol) if v is not None else '–'


def _send_on_inceleme_raporu_email(email, musteri_adi, cihaz):
    """Ön inceleme raporunun müşteri panelinde olduğuna dair bilgilendirme (tek cihaz)."""
    return _send_on_inceleme_raporu_email_bulk(email, musteri_adi, [cihaz])


def _send_on_inceleme_raporu_email_bulk(email, musteri_adi, cihazlar):
    """Ön inceleme raporu – müşteri başına tek mail, her cihaz için marka/model/seri no, arızalar+fiyatlar, toplam."""
    email = (email or '').strip()
    if not email:
        return False
    from flask import url_for
    panel_url = url_for('musteri_panel.giris', _external=True)
    cihaz_bloklari = []
    for cihaz in cihazlar:
        marka = (cihaz.cihaz_marka or cihaz.cihaz_bilgisi or '').strip()
        model = (cihaz.cihaz_model or '').strip()
        seri = (cihaz.seri_no or '').strip()
        parcalar = []
        if marka:
            parcalar.append(marka)
        if model:
            parcalar.append(model)
        if seri:
            parcalar.append(f"Seri: {seri}")
        cihaz_baslik = ' • '.join(parcalar) if parcalar else '–'
        arizalar = list(cihaz.faults.order_by('id'))
        pb = getattr(cihaz, 'on_inceleme_para_birimi', None) or 'TL'
        if arizalar:
            ariza_satirlari = []
            toplamlar = {}
            for a in arizalar:
                fault_pb = (getattr(a, 'para_birimi', None) or pb or 'TL').upper()
                satir = f'• {a.aciklama}'
                if a.fiyat is not None:
                    satir += f' – {_fiyat_fmt(a.fiyat, fault_pb)}'
                    toplamlar[fault_pb] = toplamlar.get(fault_pb, 0) + float(a.fiyat)
                ariza_satirlari.append(satir)
            ariza_html = '<br>'.join(ariza_satirlari)
            if toplamlar:
                sirali = ['TL', 'USD', 'EUR', 'GBP']
                parcalar = []
                for code in sirali:
                    if code in toplamlar:
                        parcalar.append(_fiyat_fmt(toplamlar[code], code))
                for code, val in toplamlar.items():
                    if code not in sirali:
                        parcalar.append(_fiyat_fmt(val, code))
                toplam_str = ' + '.join(parcalar)
            else:
                toplam_str = '–'
            blok = f'<p><strong>{cihaz_baslik}</strong></p><p>{ariza_html}</p><p><strong>Tahmini toplam:</strong> {toplam_str}</p>'
        else:
            toplam_str = _fiyat_fmt(cihaz.on_inceleme_fiyat, pb) if cihaz.on_inceleme_fiyat is not None else '–'
            blok = f'<p><strong>{cihaz_baslik}</strong></p><p><strong>Tahmini fiyat:</strong> {toplam_str}</p>'
        cihaz_bloklari.append(blok)
    tum_cihazlar_html = '<br>'.join(cihaz_bloklari)
    subject = "SER-CRM - Ön inceleme raporunuz hazır"
    body_html = f"""
    <p>Sayın {musteri_adi},</p>
    <p>Aşağıdaki cihaz(lar)ınızın ön inceleme raporu hazırdır.</p>
    <p><strong>Ön inceleme bulguları (arıza / tahmini fiyat):</strong></p>
    {tum_cihazlar_html}
    <p>Raporu görüntüleyip onaylamanız veya reddetmeniz için müşteri panelimize giriş yapın:</p>
    <p><a href="{panel_url}">{panel_url}</a></p>
    <p>Saygılarımızla,<br>SER-CRM</p>
    """
    pdf_bytes = None
    filename = None
    if build_on_inceleme_pdf is not None:
        try:
            pdf_bytes = build_on_inceleme_pdf(musteri_adi, cihazlar)
            safe_name = (musteri_adi or 'On-Inceleme').strip().replace(' ', '-')
            filename = f"On-Inceleme-Raporu-{safe_name}.pdf"
        except Exception as e:
            log_event('secretary', 'on_inceleme_pdf_failed', extra=str(e))
            pdf_bytes = None
    if pdf_bytes:
        ok, err = send_email_with_attachment(email, subject, body_html, pdf_bytes, filename)
    else:
        ok, err = send_email(email, subject, body_html)
    if not ok:
        log_event('secretary', 'on_inceleme_email_failed', extra=f'email={email} error={err}')
        return False
    return True


def _format_cihaz_mail_satiri(c):
    """Cihazı mail için Marka - Model (Seri: xxx) formatında döndür."""
    marka = (getattr(c, 'cihaz_marka', '') or getattr(c, 'cihaz_bilgisi', '') or '').strip()
    model = (getattr(c, 'cihaz_model', '') or '').strip()
    seri = (getattr(c, 'seri_no', '') or '').strip()
    parcalar = []
    if marka:
        parcalar.append(marka)
    if model:
        parcalar.append(model)
    if seri:
        parcalar.append(f"Seri: {seri}")
    return ' • '.join(parcalar) if parcalar else '–'


def _send_device_shipped_email(email, musteri_adi, cihazlar, on_inceleme_reddedildi=False):
    """Cihaz(lar)ın müşteriye kargolandığına dair bilgilendirme (müşteri başına tek mail)."""
    email = (email or '').strip()
    if not email:
        return
    cihaz_listesi = [f"• {_format_cihaz_mail_satiri(c)}" for c in cihazlar]
    cihazlar_html = "<br>".join(cihaz_listesi) if cihaz_listesi else "–"
    subject = "SER-CRM - Cihazınız kargoya verildi"
    if on_inceleme_reddedildi:
        body_html = f"""
    <p>Sayın {musteri_adi},</p>
    <p>Ön inceleme raporunu reddettiğiniz cihazlarınız hiçbir işlem yapılmadan tarafınıza kargolanmıştır.</p>
    <p><strong>İade edilen cihaz(lar):</strong></p>
    <p>{cihazlar_html}</p>
    <p>Müşteri panelinden takip edebilirsiniz.</p>
    <p>Saygılarımızla,<br>SER-CRM</p>
    """
    else:
        body_html = f"""
    <p>Sayın {musteri_adi},</p>
    <p>Cihazınız/cihazlarınız müşteri panelinize işlendi ve kargoya verilmiştir.</p>
    <p><strong>Kargolanan cihaz(lar):</strong></p>
    <p>{cihazlar_html}</p>
    <p>Müşteri panelinden takip edebilirsiniz.</p>
    <p>Saygılarımızla,<br>SER-CRM</p>
    """
    ok, err = send_email(email, subject, body_html)
    if not ok:
        log_event('secretary', 'device_shipped_email_failed', extra=f'email={email} error={err}')
        flash(f'Cihaz kargolandı ancak bilgilendirme e-postası gönderilemedi: {err}', 'error')
    return ok


def _send_device_arrival_email(email, musteri_adi, cihazlar):
    """Müşteriye cihaz(lar)ın firmaya ulaştığına dair bilgilendirme e-postası gönder."""
    email = (email or '').strip()
    if not email:
        return
    cihaz_listesi = []
    for c in cihazlar:
        bilgi = getattr(c, 'cihaz_bilgisi', '') or f"{getattr(c, 'cihaz_marka', '') or ''} {getattr(c, 'cihaz_model', '') or ''}".strip()
        cihaz_listesi.append(f"• {bilgi}")
    cihazlar_html = "<br>".join(cihaz_listesi) if cihaz_listesi else "–"
    subject = "SER-CRM - Cihazınız firmamıza ulaşmıştır"
    body_html = f"""
    <p>Sayın {musteri_adi},</p>
    <p>Cihazınız/cihazlarınız firmamıza ulaşmıştır.</p>
    <p><strong>Kayıtlı cihaz(lar):</strong></p>
    <p>{cihazlar_html}</p>
    <p>Teknik servis sürecimiz devam etmektedir. Sorularınız için bizimle iletişime geçebilirsiniz.</p>
    <p>Saygılarımızla,<br>SER-CRM</p>
    """
    ok, err = send_email(email, subject, body_html)
    if not ok:
        log_event('secretary', 'device_arrival_email_failed', extra=f'email={email} error={err}')
        flash(f'Cihaz kaydedildi ancak bilgilendirme e-postası gönderilemedi: {err}', 'error')


def secretary_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        if current_user.role == 'admin':
            return f(*args, **kwargs)
        if current_user.role != 'sekreter':
            flash('Bu sayfaya erişim yetkiniz yok.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return inner

def _arama_filtresi(sorgu, q):
    if not q or not q.strip():
        return sorgu
    arama = f'%{q.strip()}%'
    return sorgu.filter(or_(
        Device.musteri_adi.ilike(arama),
        Device.musteri_telefon.ilike(arama),
        Device.musteri_adres.ilike(arama),
        Device.cihaz_bilgisi.ilike(arama),
        Device.seri_no.ilike(arama),
        Device.aciklama.ilike(arama),
    ))


PER_PAGE = 25
TAB_VALUES = ('all', 'henuz_gonderilmedi', 'on_inceleme_sekreterlikte', 'musteri_onay_bekliyor', 'musteri_onayladi', 'musteri_onaylamadi', 'teknik_serviste', 'kargolanacak', 'musteriye_gonderilen', 'musteri_cihazlari')

def _tab_filtresi(sorgu, tab):
    if tab == 'henuz_gonderilmedi':
        return sorgu.filter(Device.durum == 'geldi')
    if tab == 'on_inceleme_sekreterlikte':
        return sorgu.filter(Device.durum == 'on_inceleme_sekreterlikte')
    if tab == 'musteri_onay_bekliyor':
        return sorgu.filter(Device.durum == 'musteri_onay_bekliyor')
    if tab == 'musteri_onayladi':
        return sorgu.filter(Device.durum == 'musteri_onayladi')
    if tab == 'musteri_onaylamadi':
        return sorgu.filter(Device.durum == 'musteri_onaylamadi')
    if tab == 'teknik_serviste':
        return sorgu.filter(Device.durum.in_(['teknik_serviste', 'teknik_serviste_tamir']))
    if tab == 'kargolanacak':
        return sorgu.filter(Device.durum == 'arizalar_tamamlandi')
    if tab == 'musteriye_gonderilen':
        return sorgu.filter(Device.durum == 'kargolandı')
    return sorgu  # all

SORT_COLUMNS = ('id', 'musteri_adi', 'cihaz_bilgisi', 'seri_no', 'created_at', 'teknik_servise_gonderildi_at', 'arizalar_tamamlandi_at', 'kargolandi_at')


def _parse_secretary_date_range():
    """Kargolandı sekmesi için tarih aralığı parse et."""
    start_str = request.args.get('tarih_baslangic', '').strip() or None
    end_str = request.args.get('tarih_bitis', '').strip() or None
    start_dt = end_dt = None
    if start_str:
        try:
            start_dt = datetime.combine(datetime.strptime(start_str, '%Y-%m-%d').date(), time.min)
        except ValueError:
            start_dt = None
    if end_str:
        try:
            end_dt = datetime.combine(datetime.strptime(end_str, '%Y-%m-%d').date(), time.max)
        except ValueError:
            end_dt = None
    return start_str or '', end_str or '', start_dt, end_dt


def _onay_bildirim_sayilari():
    """Müşteri onay/red bildirimi için sayılar."""
    return {
        'onayladi_count': Device.query.filter_by(durum='musteri_onayladi').count(),
        'onaylamadi_count': Device.query.filter_by(durum='musteri_onaylamadi').count(),
    }


@secretary_bp.route('/musteriler')
@login_required
@secretary_required
def musteriler():
    """Müşteri bilgileri listesi – e-posta, telefon, adres düzenleme için."""
    q = (request.args.get('q') or '').strip()
    page = request.args.get('page', 1, type=int)
    sorgu = Customer.query.order_by(Customer.ad.asc())
    if q:
        arama = f'%{q}%'
        sorgu = sorgu.filter(
            or_(
                Customer.ad.ilike(arama),
                Customer.yetkili_kisi.ilike(arama),
                Customer.telefon.ilike(arama),
                Customer.email.ilike(arama),
                Customer.adres.ilike(arama),
                Customer.lokasyon_proje.ilike(arama),
            )
        )
    pagination = sorgu.paginate(page=page, per_page=25, error_out=False)
    return render_template(
        'secretary/musteriler.html',
        pagination=pagination,
        arama=q,
    )


@secretary_bp.route('/musteriler/<int:id>/duzenle', methods=['GET', 'POST'])
@login_required
@secretary_required
def musteri_duzenle(id):
    """Müşteri bilgilerini düzenle – e-posta, telefon, adres."""
    musteri = Customer.query.get_or_404(id)
    if request.method == 'POST':
        musteri.yetkili_kisi = (request.form.get('yetkili_kisi') or '').strip() or None
        musteri.telefon = (request.form.get('telefon') or '').strip() or None
        musteri.email = (request.form.get('email') or '').strip() or None
        musteri.adres = (request.form.get('adres') or '').strip() or None
        musteri.lokasyon_proje = (request.form.get('lokasyon_proje') or '').strip() or None
        ad_yeni = (request.form.get('ad') or '').strip()
        if not ad_yeni:
            flash('Firma adı zorunludur.', 'error')
            return render_template('secretary/musteri_duzenle.html', musteri=musteri)
        musteri.ad = ad_yeni
        for c in Device.query.filter(Device.customer_id == musteri.id).all():
            c.musteri_adi = musteri.ad
            c.musteri_telefon = musteri.telefon
            c.musteri_adres = musteri.adres
            c.musteri_email = musteri.email
        for s in Sale.query.filter(Sale.customer_id == musteri.id).all():
            s.musteri_adi = musteri.ad
            s.musteri_telefon = musteri.telefon
            s.musteri_adres = musteri.adres
            s.musteri_email = musteri.email
        db.session.commit()
        log_event('secretary', 'customer_update', extra=f'customer_id={musteri.id}')
        flash('Firma bilgileri güncellendi.', 'success')
        return redirect(url_for('secretary.musteriler', q=request.form.get('return_q', ''), page=request.form.get('return_page', 1)))
    return render_template('secretary/musteri_duzenle.html', musteri=musteri)


@secretary_bp.route('/')
@login_required
@secretary_required
def index():
    q = request.args.get('q', '')
    tab = request.args.get('tab', 'all')

    # Eski cihaz kayıtları için Customer tablosunu geriye dönük doldur (customer_id boş olanlar).
    missing_devices = Device.query.filter(Device.customer_id.is_(None)).all()
    if missing_devices:
        for d in missing_devices:
            cust = _get_or_create_customer(d.musteri_adi, d.musteri_telefon, d.musteri_adres, getattr(d, 'musteri_email', None), None)
            if cust:
                d.customer_id = cust.id
        db.session.commit()
    if tab not in TAB_VALUES:
        tab = 'all'
    if tab == 'musteri_cihazlari':
        # Müşteriye göre cihazlar: aynı sayfada tab olarak göster
        page = request.args.get('page', 1, type=int)
        selected_name = request.args.get('musteri_adi')
        selected_phone = request.args.get('musteri_telefon')
        if selected_name:
            sorgu = Device.query.filter(Device.musteri_adi == selected_name)
            if selected_phone:
                sorgu = sorgu.filter(Device.musteri_telefon == selected_phone)
            if q:
                sorgu = _arama_filtresi(sorgu, q)
            sorgu = sorgu.order_by(Device.created_at.desc(), Device.id.desc())
            pagination = sorgu.paginate(page=page, per_page=PER_PAGE, error_out=False)
            return render_template('secretary/index.html',
                pagination=pagination, arama=q, aktif_tab=tab, sort='created_at', order='desc',
                secili_musteri=True, secili_musteri_adi=selected_name, secili_musteri_telefon=selected_phone,
                tarih_baslangic='', tarih_bitis='',
                **_onay_bildirim_sayilari())
        else:
            sorgu = Device.query
            if q:
                sorgu = _arama_filtresi(sorgu, q)
            customers_query = sorgu.with_entities(
                Device.musteri_adi,
                Device.musteri_telefon,
                Device.musteri_adres
            ).group_by(
                Device.musteri_adi,
                Device.musteri_telefon,
                Device.musteri_adres
            ).order_by(Device.musteri_adi.asc())
            pagination = customers_query.paginate(page=page, per_page=PER_PAGE, error_out=False)
            return render_template('secretary/index.html',
                pagination=pagination, arama=q, aktif_tab=tab, sort='created_at', order='desc',
                secili_musteri=False, secili_musteri_adi=None, secili_musteri_telefon=None,
                tarih_baslangic='', tarih_bitis='',
                **_onay_bildirim_sayilari())
    if tab == 'musteriye_gonderilen':
        default_sort, default_order = 'kargolandi_at', 'desc'
    elif tab == 'all':
        default_sort, default_order = 'id', 'desc'
    else:
        default_sort, default_order = 'created_at', 'desc'
    sort = request.args.get('sort', default_sort)
    order = request.args.get('order', default_order)
    if sort not in SORT_COLUMNS:
        sort = default_sort
    if order not in ('asc', 'desc'):
        order = default_order
    page = request.args.get('page', 1, type=int)
    tarih_baslangic = tarih_bitis = ''
    start_dt = end_dt = None
    if tab in ('musteriye_gonderilen', 'all'):
        tarih_baslangic, tarih_bitis, start_dt, end_dt = _parse_secretary_date_range()
        if start_dt and end_dt and start_dt > end_dt:
            flash('Başlangıç tarihi, bitiş tarihinden sonra olamaz.', 'error')
            start_dt = end_dt = None
            tarih_baslangic = tarih_bitis = ''
    sorgu = Device.query
    sorgu = _tab_filtresi(sorgu, tab)
    if tab == 'musteriye_gonderilen' and (start_dt or end_dt):
        kargo_tarihi = func.coalesce(Device.kargolandi_at, Device.updated_at)
        if start_dt:
            sorgu = sorgu.filter(kargo_tarihi >= start_dt)
        if end_dt:
            sorgu = sorgu.filter(kargo_tarihi <= end_dt)
    elif tab == 'all' and (start_dt or end_dt):
        if start_dt:
            sorgu = sorgu.filter(Device.created_at >= start_dt)
        if end_dt:
            sorgu = sorgu.filter(Device.created_at <= end_dt)
    sorgu = _arama_filtresi(sorgu, q)
    col = getattr(Device, sort)
    sorgu = sorgu.order_by(col.desc() if order == 'desc' else col.asc(), Device.id.desc())
    pagination = sorgu.paginate(page=page, per_page=PER_PAGE, error_out=False)
    return render_template('secretary/index.html', pagination=pagination, arama=q, aktif_tab=tab, sort=sort, order=order,
        secili_musteri=False, secili_musteri_adi=None, secili_musteri_telefon=None,
        tarih_baslangic=tarih_baslangic, tarih_bitis=tarih_bitis,
        **_onay_bildirim_sayilari())


@secretary_bp.route('/musteri-cihazlari', methods=['GET'])
@login_required
@secretary_required
def musteri_cihazlari():
    """Eski linkler için: index'e tab=musteri_cihazlari ile yönlendir."""
    return redirect(url_for('secretary.index', tab='musteri_cihazlari', q=request.args.get('q', ''), page=request.args.get('page', 1),
        musteri_adi=request.args.get('musteri_adi', ''), musteri_telefon=request.args.get('musteri_telefon', '')))


@secretary_bp.route('/coklu-cihaz-ekle', methods=['GET', 'POST'])
@login_required
@secretary_required
def coklu_cihaz_ekle():
    if request.method == 'POST':
        customers = Customer.query.order_by(Customer.ad.asc()).all()
        musteri_adi = request.form.get('musteri_adi', '').strip()
        musteri_telefon = request.form.get('musteri_telefon', '').strip()
        musteri_adres = request.form.get('musteri_adres', '').strip()
        musteri_email = request.form.get('musteri_email', '').strip()
        musteri_lokasyon = request.form.get('musteri_lokasyon', '').strip()
        if not musteri_adi:
            flash('Firma adı zorunludur.', 'error')
            return render_template('secretary/coklu_cihaz_ekle.html', son_cihaz=None, customers=customers)
        # Öncelikle select-box'tan gelen müşteri_id'yi kullan (varsa).
        customer_id_raw = request.form.get('customer_id', '').strip() or None
        musteri = None
        if customer_id_raw:
            try:
                musteri = Customer.query.get(int(customer_id_raw))
            except (TypeError, ValueError):
                musteri = None
        if not musteri:
            musteri = _get_or_create_customer(musteri_adi, musteri_telefon, musteri_adres, musteri_email, musteri_lokasyon)
        email_kullan = (musteri_email or '').strip() or (musteri.email if musteri else '')
        if not email_kullan:
            flash('Firma e-posta adresi zorunludur.', 'error')
            return render_template('secretary/coklu_cihaz_ekle.html', son_cihaz=None, customers=customers)
        cihaz_sayisi = request.form.get('cihaz_sayisi', 1, type=int)
        olusturulan = 0
        eklenen_cihazlar = []
        for i in range(cihaz_sayisi):
            cihaz_marka = request.form.get(f'cihaz_marka_{i}', '').strip()
            cihaz_model = request.form.get(f'cihaz_model_{i}', '').strip()
            if not cihaz_marka:
                continue
            cihaz_bilgisi = (cihaz_marka + ' ' + cihaz_model).strip() or cihaz_marka
            seri_no = request.form.get(f'seri_no_{i}', '').strip() or None
            aciklama = request.form.get(f'aciklama_{i}', '').strip() or None
            cihaz = Device(
                customer_id=musteri.id if musteri else None,
                musteri_adi=musteri_adi,
                musteri_telefon=musteri_telefon or None,
                musteri_adres=musteri_adres or None,
                musteri_email=musteri_email or None,
                cihaz_marka=cihaz_marka or None,
                cihaz_model=cihaz_model or None,
                cihaz_bilgisi=cihaz_bilgisi,
                seri_no=seri_no,
                aciklama=aciklama,
                durum='geldi'
            )
            db.session.add(cihaz)
            db.session.flush()
            eklenen_cihazlar.append(cihaz)
            olusturulan += 1
        if olusturulan:
            db.session.commit()
            log_event('secretary', 'device_add_bulk', extra=f'count={olusturulan}')
            _send_device_arrival_email(musteri_email or (musteri.email if musteri else None), musteri_adi, eklenen_cihazlar)
            flash(f'{olusturulan} cihaz kaydı oluşturuldu.', 'success')
            return redirect(url_for('secretary.index'))
        flash('En az bir cihaz için cihaz bilgisi girin.', 'error')
        return render_template('secretary/coklu_cihaz_ekle.html', son_cihaz=None, customers=customers)
    son_cihaz = None
    customers = Customer.query.order_by(Customer.ad.asc()).all()
    if request.args.get('son_musteri'):
        son_cihaz = Device.query.order_by(Device.created_at.desc()).first()
    return render_template('secretary/coklu_cihaz_ekle.html', son_cihaz=son_cihaz, customers=customers)

@secretary_bp.route('/on-inceleme-toplu-fiyat', methods=['GET', 'POST'])
@login_required
@secretary_required
def on_inceleme_toplu_fiyat_gir():
    """Toplu ön inceleme – fiyat girişi sayfası (rapor_cihaz_id ile gelir)."""
    ids = request.form.getlist('rapor_cihaz_id') or request.args.getlist('rapor_cihaz_id')
    if not ids:
        flash('Lütfen en az bir cihaz seçin.', 'error')
        return redirect(url_for('secretary.index', tab='on_inceleme_sekreterlikte'))
    cihazlar = Device.query.filter(
        Device.id.in_(ids),
        Device.durum == 'on_inceleme_sekreterlikte'
    ).order_by(Device.musteri_adi, Device.id).all()
    if not cihazlar:
        flash('Seçilen cihazlar bulunamadı veya artık ön inceleme sekreterlikte değil.', 'error')
        return redirect(url_for('secretary.index', tab='on_inceleme_sekreterlikte'))
    return render_template('secretary/on_inceleme_toplu_fiyat.html', cihazlar=cihazlar,
        return_tab=request.form.get('return_tab') or request.args.get('return_tab', 'on_inceleme_sekreterlikte'),
        return_page=request.form.get('return_page') or request.args.get('return_page', 1),
        return_q=request.form.get('return_q') or request.args.get('return_q', ''),
        return_sort=request.form.get('return_sort') or request.args.get('return_sort', 'created_at'),
        return_order=request.form.get('return_order') or request.args.get('return_order', 'desc'))


@secretary_bp.route('/on-inceleme-toplu-gonder', methods=['POST'])
@login_required
@secretary_required
def on_inceleme_toplu_gonder():
    """Toplu ön inceleme – fiyatları işle, müşteri başına tek mail gönder."""
    ids = request.form.getlist('rapor_cihaz_id')
    if not ids:
        flash('Lütfen en az bir cihaz seçin.', 'error')
        return redirect(url_for('secretary.index', tab='on_inceleme_sekreterlikte'))
    cihazlar = Device.query.filter(
        Device.id.in_(ids),
        Device.durum == 'on_inceleme_sekreterlikte'
    ).all()
    if not cihazlar:
        flash('Seçilen cihazlar bulunamadı.', 'error')
        return redirect(url_for('secretary.index', tab='on_inceleme_sekreterlikte'))
    for cihaz in cihazlar:
        arizalar = list(cihaz.faults.order_by('id'))
        toplam_fiyat = 0
        if arizalar:
            for f in arizalar:
                fiyat_str = (request.form.get(f'fault_fiyat_{f.id}') or '').strip().replace(',', '.')
                if not fiyat_str:
                    flash(f'Lütfen tüm arıza fiyatlarını girin: {cihaz.cihaz_marka or cihaz.cihaz_bilgisi} – "{f.aciklama[:40]}..."', 'error')
                    qs = [('rapor_cihaz_id', i) for i in ids]
                    for k in ('return_tab', 'return_page', 'return_q', 'return_sort', 'return_order'):
                        v = request.form.get(k)
                        if v is not None:
                            qs.append((k, v))
                    return redirect(url_for('secretary.on_inceleme_toplu_fiyat_gir') + '?' + urlencode(qs))
                try:
                    fiyat = float(fiyat_str)
                    if fiyat < 0:
                        raise ValueError('Negatif olamaz')
                except (ValueError, TypeError):
                    flash(f'Geçersiz fiyat: {f.aciklama[:40]}...', 'error')
                    return redirect(request.referrer or url_for('secretary.index'))
                f.fiyat = fiyat
                fault_pb = (request.form.get(f'fault_para_birimi_{f.id}') or 'TL').strip().upper()[:5] or 'TL'
                if fault_pb not in ('TL', 'USD', 'EUR', 'GBP'):
                    fault_pb = 'TL'
                f.para_birimi = fault_pb
                toplam_fiyat += fiyat
        else:
            fiyat_str = (request.form.get(f'on_inceleme_fiyat_{cihaz.id}') or '').strip().replace(',', '.')
            if not fiyat_str:
                flash(f'Lütfen tahmini fiyatı girin: {cihaz.cihaz_marka or cihaz.cihaz_bilgisi}', 'error')
                return redirect(request.referrer or url_for('secretary.index'))
            try:
                toplam_fiyat = float(fiyat_str)
                if toplam_fiyat < 0:
                    raise ValueError('Negatif olamaz')
            except (ValueError, TypeError):
                flash('Geçerli bir fiyat girin.', 'error')
                return redirect(request.referrer or url_for('secretary.index'))
        cihaz.on_inceleme_fiyat = toplam_fiyat
        pb = (request.form.get(f'on_inceleme_para_birimi_{cihaz.id}') or 'TL').strip().upper()[:5] or 'TL'
        if pb not in ('TL', 'USD', 'EUR', 'GBP'):
            pb = 'TL'
        cihaz.on_inceleme_para_birimi = pb
        cihaz.durum = 'musteri_onay_bekliyor'
        cihaz.on_inceleme_raporu_musteriye_at = datetime.now()
        cihaz.musteri_onay_durumu = 'bekliyor'
    db.session.commit()
    musteri_cihazlari = {}
    for cihaz in cihazlar:
        email = (cihaz.musteri_email or (cihaz.customer.email if cihaz.customer else '') or '').strip().lower()
        if email:
            if email not in musteri_cihazlari:
                musteri_cihazlari[email] = []
            musteri_cihazlari[email].append(cihaz)
    for email, m_cihazlar in musteri_cihazlari.items():
        musteri_adi = m_cihazlar[0].musteri_adi if m_cihazlar else ''
        _send_on_inceleme_raporu_email_bulk(email, musteri_adi, m_cihazlar)
    log_event('secretary', 'rapor_musteriye_toplu_gonderildi', extra=f'count={len(cihazlar)}')
    flash(f'{len(cihazlar)} cihazın ön inceleme raporu firmaya gönderildi.', 'success')
    return redirect(url_for('secretary.index', tab=request.form.get('return_tab', 'on_inceleme_sekreterlikte'),
        page=request.form.get('return_page', 1), q=request.form.get('return_q', ''),
        sort=request.form.get('return_sort', 'created_at'), order=request.form.get('return_order', 'desc')))


@secretary_bp.route('/raporu-musteriye-gonder/<int:id>', methods=['POST'])
@login_required
@secretary_required
def raporu_musteriye_gonder(id):
    """Ön inceleme raporunu müşteriye gönder (panel + e-posta). Her arıza için fiyat alınır."""
    cihaz = Device.query.get_or_404(id)
    if cihaz.durum != 'on_inceleme_sekreterlikte':
        flash('Sadece ön inceleme sekreterlikte olan cihazlar için rapor gönderilebilir.', 'error')
        return redirect(url_for('secretary.index'))
    email = (cihaz.musteri_email or (cihaz.customer.email if cihaz.customer else '') or '').strip()
    if not email:
        flash('Firma e-posta adresi bulunamadı.', 'error')
        return redirect(url_for('secretary.cihaz_detay', id=cihaz.id))
    toplam_fiyat = 0
    arizalar = list(cihaz.faults.order_by('id'))
    if arizalar:
        for f in arizalar:
            fiyat_str = (request.form.get(f'fault_fiyat_{f.id}') or '').strip().replace(',', '.')
            if not fiyat_str:
                flash(f'Lütfen tüm arıza fiyatlarını girin: "{f.aciklama[:50]}..."', 'error')
                return redirect(request.referrer or url_for('secretary.index'))
            try:
                fiyat = float(fiyat_str)
                if fiyat < 0:
                    raise ValueError('Negatif olamaz')
            except (ValueError, TypeError):
                flash(f'Geçersiz fiyat girişi: "{f.aciklama[:50]}..."', 'error')
                return redirect(request.referrer or url_for('secretary.index'))
            f.fiyat = fiyat
            fault_pb = (request.form.get(f'fault_para_birimi_{f.id}') or 'TL').strip().upper()[:5] or 'TL'
            if fault_pb not in ('TL', 'USD', 'EUR', 'GBP'):
                fault_pb = 'TL'
            f.para_birimi = fault_pb
            toplam_fiyat += fiyat
    else:
        fiyat_str = (request.form.get('on_inceleme_fiyat') or '').strip().replace(',', '.')
        if not fiyat_str:
            flash('Lütfen tahmini fiyatı girin.', 'error')
            return redirect(request.referrer or url_for('secretary.index'))
        try:
            toplam_fiyat = float(fiyat_str)
            if toplam_fiyat < 0:
                raise ValueError('Negatif olamaz')
        except (ValueError, TypeError):
            flash('Geçerli bir fiyat girin.', 'error')
            return redirect(request.referrer or url_for('secretary.index'))
    cihaz.on_inceleme_fiyat = toplam_fiyat
    if not arizalar:
        cihaz.on_inceleme_para_birimi = (request.form.get('on_inceleme_para_birimi') or 'TL').strip().upper()[:5] or 'TL'
        if cihaz.on_inceleme_para_birimi not in ('TL', 'USD', 'EUR', 'GBP'):
            cihaz.on_inceleme_para_birimi = 'TL'
    cihaz.durum = 'musteri_onay_bekliyor'
    cihaz.on_inceleme_raporu_musteriye_at = datetime.now()
    cihaz.musteri_onay_durumu = 'bekliyor'
    db.session.commit()
    _send_on_inceleme_raporu_email(email, cihaz.musteri_adi, cihaz)
    log_event('secretary', 'rapor_musteriye_gonderildi', extra=f'device_id={cihaz.id}')
    flash('Ön inceleme raporu firma panelinde yayınlandı ve firmaya e-posta gönderildi.', 'success')
    return_tab = request.form.get('return_tab') or request.args.get('return_tab')
    if return_tab:
        page = request.form.get('return_page') or request.args.get('return_page') or 1
        q = request.form.get('return_q') or request.args.get('return_q') or ''
        return redirect(url_for('secretary.index', tab=return_tab, page=page, q=q))
    return redirect(url_for('secretary.cihaz_detay', id=cihaz.id))


@secretary_bp.route('/teknik-servise-tamir-gonder/<int:id>', methods=['POST'])
@login_required
@secretary_required
def teknik_servise_tamir_gonder(id):
    """Müşteri onayladı – cihazı tamir için teknik servise gönder."""
    cihaz = Device.query.get_or_404(id)
    if cihaz.durum != 'musteri_onayladi':
        flash('Sadece firma onayladı durumundaki cihazlar teknik servise gönderilebilir.', 'error')
        return redirect(url_for('secretary.index'))
    cihaz.durum = 'teknik_serviste_tamir'
    cihaz.teknik_servise_tamir_icin_at = datetime.now()
    db.session.commit()
    log_event('secretary', 'device_tamir_icin_teknik', extra=f'device_id={cihaz.id}')
    flash('Cihaz tamir için teknik servise gönderildi.', 'success')
    page = request.form.get('return_page') or request.args.get('return_page') or 1
    q = request.form.get('return_q') or request.args.get('return_q') or ''
    return redirect(url_for('secretary.index', tab='musteri_onayladi', page=page, q=q))


@secretary_bp.route('/musteriye-iade-et/<int:id>', methods=['POST'])
@login_required
@secretary_required
def musteriye_iade_et(id):
    """Müşteri onaylamadı – cihazı işlem yapmadan müşteriye gönder."""
    cihaz = Device.query.get_or_404(id)
    if cihaz.durum != 'musteri_onaylamadi':
        flash('Sadece firma onaylamadı durumundaki cihazlar iade edilebilir.', 'error')
        return redirect(url_for('secretary.index'))
    email = (cihaz.musteri_email or (cihaz.customer.email if cihaz.customer else '') or '').strip()
    cihaz.durum = 'kargolandı'
    cihaz.kargolandi_at = datetime.now()
    db.session.commit()
    if email:
        _send_device_shipped_email(email, cihaz.musteri_adi, [cihaz], on_inceleme_reddedildi=True)
    log_event('secretary', 'device_musteriye_iade', extra=f'device_id={cihaz.id}')
    flash('Cihaz işlem yapılmadan firmaya gönderildi (kargolandı).', 'success')
    return redirect(url_for('secretary.index', tab='musteriye_gonderilen'))


@secretary_bp.route('/teknik-servise-gonder/<int:id>', methods=['POST'])
@login_required
@secretary_required
def teknik_servise_gonder(id):
    cihaz = Device.query.get_or_404(id)
    if cihaz.durum != 'geldi':
        flash('Sadece "Cihaz geldi" durumundaki cihazlar gönderilebilir.', 'error')
        return redirect(url_for('secretary.index'))
    cihaz.durum = 'teknik_serviste'
    cihaz.teknik_servise_gonderildi_at = datetime.now()
    db.session.commit()
    log_event('secretary', 'device_send_technical', extra=f'device_id={cihaz.id}')
    flash('Cihaz teknik servise gönderildi.', 'success')
    if request.form.get('return_tab') == 'musteri_cihazlari' and request.form.get('return_musteri_adi'):
        return redirect(url_for('secretary.index', tab='musteri_cihazlari', musteri_adi=request.form.get('return_musteri_adi'), musteri_telefon=request.form.get('return_musteri_telefon') or '', q=request.form.get('return_q', ''), page=request.form.get('return_page', 1, type=int)))
    return redirect(url_for('secretary.index', page=request.form.get('return_page', 1, type=int), q=request.form.get('return_q', ''), tab=request.form.get('return_tab', 'all'), sort=request.form.get('return_sort', 'created_at'), order=request.form.get('return_order', 'desc')))


def _redirect_index():
    return redirect(url_for('secretary.index', page=request.form.get('return_page', 1, type=int), q=request.form.get('return_q', ''), tab=request.form.get('return_tab', 'all')))

@secretary_bp.route('/toplu-islem', methods=['POST'])
@login_required
@secretary_required
def toplu_islem():
    action = request.form.get('toplu_islem')
    if action == 'teknik_servise_gonder':
        ids = request.form.getlist('cihaz_id')
        if not ids:
            flash('Lütfen en az bir cihaz seçin.', 'error')
            return _redirect_index()
        cihazlar = Device.query.filter(Device.id.in_(ids), Device.durum == 'geldi').all()
        adet = len(cihazlar)
        for c in cihazlar:
            c.durum = 'teknik_serviste'
            c.teknik_servise_gonderildi_at = datetime.now()
        db.session.commit()
        log_event('secretary', 'device_send_technical_bulk', extra=f'count={adet}')
        flash(f'{adet} cihaz teknik servise gönderildi.', 'success')
        return _redirect_index()
    elif action == 'kargolandı_isaretle':
        ids = request.form.getlist('kargo_cihaz_id')
        if not ids:
            flash('Lütfen en az bir cihaz seçin.', 'error')
            return _redirect_index()
        cihazlar = Device.query.filter(Device.id.in_(ids), Device.durum == 'arizalar_tamamlandi').all()
        adet = len(cihazlar)
        for c in cihazlar:
            c.durum = 'kargolandı'
            c.kargolandi_at = datetime.now()
        db.session.commit()
        # Müşteri başına tek mail: e-posta adresine göre grupla
        musteri_cihazlari = {}
        for c in cihazlar:
            email = (c.musteri_email or (c.customer.email if c.customer else '') or '').strip().lower()
            if email:
                if email not in musteri_cihazlari:
                    musteri_cihazlari[email] = []
                musteri_cihazlari[email].append(c)
        for email, m_cihazlar in musteri_cihazlari.items():
            musteri_adi = m_cihazlar[0].musteri_adi if m_cihazlar else ''
            _send_device_shipped_email(email, musteri_adi, m_cihazlar)
        log_event('secretary', 'device_mark_shipped_bulk', extra=f'count={adet}')
        flash(f'{adet} cihaz kargolandı olarak işaretlendi.', 'success')
        return _redirect_index()
    flash('Geçersiz işlem.', 'error')
    return _redirect_index()

@secretary_bp.route('/teknik-servise-toplu-gonder', methods=['POST'])
@login_required
@secretary_required
def teknik_servise_toplu_gonder():
    ids = request.form.getlist('cihaz_id')
    if not ids:
        flash('Lütfen en az bir cihaz seçin.', 'error')
        return _redirect_index()
    cihazlar = Device.query.filter(Device.id.in_(ids), Device.durum == 'geldi').all()
    adet = len(cihazlar)
    for c in cihazlar:
        c.durum = 'teknik_serviste'
        c.teknik_servise_gonderildi_at = datetime.now()
    db.session.commit()
    log_event('secretary', 'device_send_technical_bulk', extra=f'count={adet}')
    flash(f'{adet} cihaz teknik servise gönderildi.', 'success')
    return _redirect_index()

@secretary_bp.route('/kargolandı-toplu-isaretle', methods=['POST'])
@login_required
@secretary_required
def kargolandı_toplu_isaretle():
    ids = request.form.getlist('kargo_cihaz_id')
    if not ids:
        flash('Lütfen en az bir cihaz seçin.', 'error')
        return _redirect_index()
    cihazlar = Device.query.filter(Device.id.in_(ids), Device.durum == 'arizalar_tamamlandi').all()
    adet = len(cihazlar)
    for c in cihazlar:
        c.durum = 'kargolandı'
        c.kargolandi_at = datetime.now()
    db.session.commit()
    musteri_cihazlari = {}
    for c in cihazlar:
        email = (c.musteri_email or (c.customer.email if c.customer else '') or '').strip().lower()
        if email:
            if email not in musteri_cihazlari:
                musteri_cihazlari[email] = []
            musteri_cihazlari[email].append(c)
    for email, m_cihazlar in musteri_cihazlari.items():
        musteri_adi = m_cihazlar[0].musteri_adi if m_cihazlar else ''
        _send_device_shipped_email(email, musteri_adi, m_cihazlar)
    log_event('secretary', 'device_mark_shipped_bulk', extra=f'count={adet}')
    flash(f'{adet} cihaz kargolandı olarak işaretlendi.', 'success')
    return _redirect_index()

@secretary_bp.route('/kargolandı-isaretle/<int:id>', methods=['POST'])
@login_required
@secretary_required
def kargolandı_isaretle(id):
    cihaz = Device.query.get_or_404(id)
    if cihaz.durum != 'arizalar_tamamlandi':
        flash('Sadece arızaları tamamlanmış cihazlar kargolandı işaretlenebilir.', 'error')
        return redirect(url_for('secretary.index'))
    email = (cihaz.musteri_email or (cihaz.customer.email if cihaz.customer else '') or '').strip()
    cihaz.durum = 'kargolandı'
    cihaz.kargolandi_at = datetime.now()
    db.session.commit()
    if email:
        _send_device_shipped_email(email, cihaz.musteri_adi, [cihaz])
    log_event('secretary', 'device_mark_shipped', extra=f'device_id={cihaz.id}')
    flash('Cihaz kargolandı olarak işaretlendi.', 'success')
    if request.form.get('return_tab') == 'musteri_cihazlari' and request.form.get('return_musteri_adi'):
        return redirect(url_for('secretary.index', tab='musteri_cihazlari', musteri_adi=request.form.get('return_musteri_adi'), musteri_telefon=request.form.get('return_musteri_telefon') or '', q=request.form.get('return_q', ''), page=request.form.get('return_page', 1, type=int)))
    return redirect(url_for('secretary.index', page=request.form.get('return_page', 1, type=int), q=request.form.get('return_q', ''), tab=request.form.get('return_tab', 'all'), sort=request.form.get('return_sort', 'created_at'), order=request.form.get('return_order', 'desc')))

@secretary_bp.route('/cihaz/<int:id>/sil', methods=['POST'])
@login_required
@secretary_required
def cihaz_sil(id):
    """Sadece teknik servise gönderilmemiş (durum='geldi') cihazlar silinebilir."""
    cihaz = Device.query.get_or_404(id)
    if cihaz.durum != 'geldi':
        flash('Sadece teknik servise gönderilmemiş cihazlar silinebilir.', 'error')
        return redirect(url_for('secretary.index'))
    db.session.delete(cihaz)
    db.session.commit()
    log_event('secretary', 'device_delete', extra=f'device_id={id}')
    flash('Cihaz silindi.', 'success')
    return redirect(url_for('secretary.index',
        tab=request.form.get('return_tab', 'henuz_gonderilmedi'),
        page=request.form.get('return_page', 1, type=int),
        q=request.form.get('return_q', ''),
        sort=request.form.get('return_sort', 'created_at'),
        order=request.form.get('return_order', 'desc')))


@secretary_bp.route('/cihaz/<int:id>')
@login_required
@secretary_required
def cihaz_detay(id):
    cihaz = Device.query.get_or_404(id)
    # Aynı seri numarasına sahip daha eski bir kayıt varsa, ilk geliş tarihini bul.
    first_seen_date = None
    if cihaz.seri_no:
        first_same = Device.query.filter(
            Device.seri_no == cihaz.seri_no,
            Device.id != cihaz.id
        ).order_by(Device.created_at.asc()).first()
        if first_same and first_same.created_at and first_same.created_at < cihaz.created_at:
            first_seen_date = first_same.created_at
    return_tab = request.args.get('return_tab', 'all')
    return_page = request.args.get('return_page', 1, type=int)
    return_q = request.args.get('return_q') or request.args.get('q', '')
    return_sort = request.args.get('return_sort', 'created_at')
    return_order = request.args.get('return_order', 'desc')
    return_musteri_adi = request.args.get('return_musteri_adi', '')
    return_musteri_telefon = request.args.get('return_musteri_telefon', '') or None
    return render_template('secretary/cihaz_detay.html', cihaz=cihaz, first_seen_date=first_seen_date,
        return_tab=return_tab, return_page=return_page, return_q=return_q,
        return_sort=return_sort, return_order=return_order,
        return_musteri_adi=return_musteri_adi, return_musteri_telefon=return_musteri_telefon)


@secretary_bp.route('/cihaz/<int:id>/on-inceleme-pdf')
@login_required
@secretary_required
def cihaz_on_inceleme_pdf(id):
    """İlgili cihaz için ön inceleme PDF'ini indir (sekreterlik)."""
    cihaz = Device.query.get_or_404(id)
    if build_on_inceleme_pdf is None:
        flash('Ön inceleme PDF oluşturmak için reportlab kurulmalı: pip install reportlab', 'error')
        return redirect(url_for('secretary.cihaz_detay', id=cihaz.id))
    musteri_adi = cihaz.musteri_adi or (cihaz.customer.ad if cihaz.customer else '')
    pdf_bytes = build_on_inceleme_pdf(musteri_adi, [cihaz])
    safe_name = (musteri_adi or 'On-Inceleme').strip().replace(' ', '-')
    filename = f"On-Inceleme-Raporu-{safe_name or cihaz.id}.pdf"
    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename=\"{filename}\"'}
    )


@secretary_bp.route('/cihaz/<int:id>/duzenle', methods=['GET', 'POST'])
@login_required
@secretary_required
def cihaz_duzenle(id):
    cihaz = Device.query.get_or_404(id)
    if cihaz.durum in ('teknik_serviste', 'teknik_serviste_tamir', 'on_inceleme_sekreterlikte', 'musteri_onay_bekliyor', 'musteri_onayladi', 'musteri_onaylamadi', 'arizalar_tamamlandi', 'kargolandı'):
        flash('Bu cihaz sekreter tarafından düzenlenemez.', 'error')
        return redirect(url_for('secretary.cihaz_detay', id=cihaz.id))
    if request.method == 'POST':
        cihaz.musteri_adi = request.form.get('musteri_adi', '').strip()
        cihaz.musteri_telefon = request.form.get('musteri_telefon', '').strip() or None
        cihaz.musteri_adres = request.form.get('musteri_adres', '').strip() or None
        cihaz.musteri_email = request.form.get('musteri_email', '').strip() or None
        # Cihaz kaydı üzerinden müşterinin lokasyon/proje bilgisini de güncelle
        lok = request.form.get('musteri_lokasyon', '').strip() or None
        if cihaz.customer and lok:
            cihaz.customer.lokasyon_proje = lok
        cihaz_marka = request.form.get('cihaz_marka', '').strip()
        cihaz_model = request.form.get('cihaz_model', '').strip()
        cihaz.cihaz_marka = cihaz_marka or None
        cihaz.cihaz_model = cihaz_model or None
        cihaz.cihaz_bilgisi = ((cihaz_marka + ' ' + cihaz_model).strip() or cihaz_marka or cihaz.cihaz_bilgisi)
        cihaz.seri_no = request.form.get('seri_no', '').strip() or None
        cihaz.aciklama = request.form.get('aciklama', '').strip() or None
        if not cihaz.musteri_adi or not cihaz_marka:
            flash('Firma adı ve cihaz markası zorunludur.', 'error')
            return render_template('secretary/cihaz_duzenle.html', cihaz=cihaz)
        db.session.commit()
        log_event('secretary', 'device_update', extra=f'device_id={cihaz.id}')
        flash('Kayıt güncellendi.', 'success')
        return redirect(url_for('secretary.cihaz_detay', id=cihaz.id))
    return render_template('secretary/cihaz_duzenle.html', cihaz=cihaz)


def _redirect_musteri_cihazlari():
    """Müşteri cihazları tabına yönlendir (index, tab=musteri_cihazlari)."""
    q = request.form.get('q', '').strip()
    page = request.form.get('page', 1, type=int)
    musteri_adi = request.form.get('return_musteri_adi', '').strip()
    musteri_telefon = request.form.get('return_musteri_telefon', '') or ''
    kwargs = {'tab': 'musteri_cihazlari', 'q': q, 'page': page}
    if musteri_adi:
        kwargs['musteri_adi'] = musteri_adi
        kwargs['musteri_telefon'] = musteri_telefon
    return redirect(url_for('secretary.index', **kwargs))


@secretary_bp.route('/musteri-cihazlari/toplu', methods=['POST'])
@login_required
@secretary_required
def musteri_cihazlari_toplu():
    """Müşteri bazlı listeden toplu işlem."""
    action = request.form.get('toplu_islem')
    q = request.form.get('q', '').strip()
    page = request.form.get('page', 1, type=int)
    if action == 'teknik_servise_gonder':
        ids = request.form.getlist('cihaz_id')
        if not ids:
            flash('Lütfen en az bir cihaz seçin.', 'error')
            return _redirect_musteri_cihazlari()
        cihazlar = Device.query.filter(Device.id.in_(ids), Device.durum == 'geldi').all()
        adet = len(cihazlar)
        for c in cihazlar:
            c.durum = 'teknik_serviste'
            c.teknik_servise_gonderildi_at = datetime.now()
        db.session.commit()
        flash(f'{adet} cihaz teknik servise gönderildi.', 'success')
        return _redirect_musteri_cihazlari()
    elif action == 'kargolandı_isaretle':
        ids = request.form.getlist('kargo_cihaz_id')
        if not ids:
            flash('Lütfen en az bir cihaz seçin.', 'error')
            return _redirect_musteri_cihazlari()
        cihazlar = Device.query.filter(Device.id.in_(ids), Device.durum == 'arizalar_tamamlandi').all()
        adet = len(cihazlar)
        for c in cihazlar:
            c.durum = 'kargolandı'
            c.kargolandi_at = datetime.now()
        db.session.commit()
        # Müşteri başına tek mail
        musteri_cihazlari = {}
        for c in cihazlar:
            email = (c.musteri_email or (c.customer.email if c.customer else '') or '').strip().lower()
            if email:
                if email not in musteri_cihazlari:
                    musteri_cihazlari[email] = []
                musteri_cihazlari[email].append(c)
        for email, m_cihazlar in musteri_cihazlari.items():
            musteri_adi = m_cihazlar[0].musteri_adi if m_cihazlar else ''
            _send_device_shipped_email(email, musteri_adi, m_cihazlar)
        flash(f'{adet} cihaz kargolandı olarak işaretlendi.', 'success')
        return _redirect_musteri_cihazlari()
    flash('Geçersiz işlem.', 'error')
    return _redirect_musteri_cihazlari()
