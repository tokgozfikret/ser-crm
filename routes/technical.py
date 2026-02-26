# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, time
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from sqlalchemy import or_, func
from models import Device, Fault, Customer, User
from extensions import db
from logging_utils import log_event
from mail_utils import send_email

technical_bp = Blueprint('technical', __name__)


def _get_or_create_customer(ad, telefon=None, adres=None, email=None, lokasyon_proje=None):
    """Teknik panel için müşteri bul/oluştur (ileride cihaz güncellemede kullanılabilir)."""
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
        if email and not musteri.email:
            musteri.email = email
        if lokasyon_proje and not getattr(musteri, 'lokasyon_proje', None):
            musteri.lokasyon_proje = lokasyon_proje
    return musteri


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
SORT_COLUMNS = ('id', 'musteri_adi', 'cihaz_bilgisi', 'seri_no', 'aciklama', 'durum', 'updated_at', 'arizalar_tamamlandi_at', 'teknik_servise_gonderildi_at')


def technical_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        if not current_user.is_authenticated:
            # Oturum yoksa standart login akışına bırak
            return redirect(url_for('auth.login'))
        # Admin tüm panellere erişebilsin
        if current_user.role == 'admin':
            return f(*args, **kwargs)
        if current_user.role != 'teknik':
            flash('Bu sayfaya erişim yetkiniz yok.', 'error')
            # Giriş ekranına değil, rolüne uygun panele yönlendir
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return inner

TECHNICAL_FLOW = ['teknik_serviste', 'teknik_serviste_tamir', 'arizalar_tamamlandi']

@technical_bp.route('/')
@login_required
@technical_required
def index():
    q = request.args.get('q', '')
    page = request.args.get('page', 1, type=int)
    tab = request.args.get('tab', 'all')
    if tab == 'musteri_cihazlari':
        selected_name = request.args.get('musteri_adi')
        selected_phone = request.args.get('musteri_telefon')
        if selected_name:
            sorgu = Device.query.filter(
                Device.musteri_adi == selected_name,
                Device.durum.in_(TECHNICAL_FLOW)
            )
            if selected_phone:
                sorgu = sorgu.filter(Device.musteri_telefon == selected_phone)
            if q:
                sorgu = _arama_filtresi(sorgu, q)
            sorgu = sorgu.order_by(Device.created_at.desc(), Device.id.desc())
            pagination = sorgu.paginate(page=page, per_page=PER_PAGE, error_out=False)
            return render_template('technical/index.html',
                pagination=pagination, arama=q, aktif_tab=tab, sort='created_at', order='desc',
                secili_musteri=True, secili_musteri_adi=selected_name, secili_musteri_telefon=selected_phone,
                tarih_baslangic='', tarih_bitis='')
        else:
            sorgu = Device.query.filter(Device.durum.in_(TECHNICAL_FLOW))
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
            return render_template('technical/index.html',
                pagination=pagination, arama=q, aktif_tab=tab, sort='created_at', order='desc',
                secili_musteri=False, secili_musteri_adi=None, secili_musteri_telefon=None,
                tarih_baslangic='', tarih_bitis='')
    if tab == 'tamamlanmis':
        default_sort, default_order = 'updated_at', 'desc'
    elif tab in ('tamamlanmamis', 'on_inceleme'):
        default_sort, default_order = 'updated_at', 'desc'
    else:
        if tab not in ('all', 'musteri_cihazlari'):
            tab = 'all'
        default_sort, default_order = 'id', 'desc'
    sort = request.args.get('sort', default_sort)
    order = request.args.get('order', default_order)
    if sort not in SORT_COLUMNS:
        sort = default_sort
    if order not in ('asc', 'desc'):
        order = default_order
    tarih_baslangic = tarih_bitis = ''
    start_dt = end_dt = None
    if tab == 'all':
        start_str = request.args.get('tarih_baslangic', '').strip() or None
        end_str = request.args.get('tarih_bitis', '').strip() or None
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
        tarih_baslangic = start_str or ''
        tarih_bitis = end_str or ''
        if start_dt and end_dt and start_dt > end_dt:
            flash('Başlangıç tarihi, bitiş tarihinden sonra olamaz.', 'error')
            start_dt = end_dt = None
            tarih_baslangic = tarih_bitis = ''
    if tab == 'tamamlanmis':
        sorgu = Device.query.filter(Device.durum == 'arizalar_tamamlandi')
    elif tab == 'on_inceleme':
        sorgu = Device.query.filter(Device.durum == 'teknik_serviste')
    elif tab == 'tamamlanmamis':
        sorgu = Device.query.filter(Device.durum == 'teknik_serviste_tamir')
    else:
        sorgu = Device.query.filter(
            Device.durum.in_(['teknik_serviste', 'teknik_serviste_tamir', 'arizalar_tamamlandi'])
        )
    if tab == 'all' and (start_dt or end_dt):
        if start_dt:
            sorgu = sorgu.filter(Device.created_at >= start_dt)
        if end_dt:
            sorgu = sorgu.filter(Device.created_at <= end_dt)
    sorgu = _arama_filtresi(sorgu, q)
    col = getattr(Device, sort)

    # "Tüm cihazlar" tabında: 72+ saattir teknik serviste bekleyen cihazları en üste al.
    if tab == 'all':
        sorgu = sorgu.order_by(col.desc() if order == 'desc' else col.asc(), Device.id.desc())
        all_devices = sorgu.all()
        now = datetime.now()
        threshold = now - timedelta(hours=72)

        overdue = []
        normal = []
        for dev in all_devices:
            if dev.durum == 'teknik_serviste':
                ref_date = dev.teknik_servise_gonderildi_at
            elif dev.durum == 'teknik_serviste_tamir':
                # Arızası girilmiş ama yapıldı işaretlenmemiş → teknik servise ilk gönderilme tarihi
                if dev.faults.filter_by(yapildi=False).count() > 0:
                    ref_date = dev.teknik_servise_gonderildi_at
                else:
                    ref_date = dev.teknik_servise_tamir_icin_at or dev.updated_at
            else:
                ref_date = None
            dev.overdue_72h = (
                ref_date is not None
                and ref_date <= threshold
            )
            if dev.overdue_72h:
                overdue.append(dev)
            else:
                normal.append(dev)

        ordered = overdue + normal
        total = len(ordered)
        start = (page - 1) * PER_PAGE
        end = start + PER_PAGE
        page_items = ordered[start:end]

        class SimplePagination:
            def __init__(self, items, page, per_page, total):
                self.items = items
                self.page = page
                self.per_page = per_page
                self.total = total
                self.pages = (total + per_page - 1) // per_page or 1
                self.has_prev = page > 1
                self.has_next = page < self.pages
                self.prev_num = page - 1
                self.next_num = page + 1

            def iter_pages(self, left_edge=2, left_current=2, right_current=2, right_edge=2):
                pages_end = self.pages + 1
                if pages_end <= 1:
                    return
                left_end = min(1 + left_edge, pages_end)
                for p in range(1, left_end):
                    yield p
                mid_start = max(left_end, self.page - left_current)
                mid_end = min(self.page + right_current + 1, pages_end)
                if mid_start > left_end:
                    yield None
                for p in range(mid_start, mid_end):
                    yield p
                right_start = max(mid_end, pages_end - right_edge)
                if right_start > mid_end:
                    yield None
                for p in range(right_start, pages_end):
                    yield p

        pagination = SimplePagination(page_items, page, PER_PAGE, total)
    else:
        sorgu = sorgu.order_by(col.desc() if order == 'desc' else col.asc(), Device.id.desc())
        pagination = sorgu.paginate(page=page, per_page=PER_PAGE, error_out=False)
        # Diğer tablarda da bayrağı hesaplayalım (ileride kullanılabilir).
        now = datetime.now()
        threshold = now - timedelta(hours=72)
        for dev in pagination.items:
            if dev.durum == 'teknik_serviste':
                ref_date = dev.teknik_servise_gonderildi_at
            elif dev.durum == 'teknik_serviste_tamir':
                if dev.faults.filter_by(yapildi=False).count() > 0:
                    ref_date = dev.teknik_servise_gonderildi_at
                else:
                    ref_date = dev.teknik_servise_tamir_icin_at or dev.updated_at
            else:
                ref_date = None
            dev.overdue_72h = ref_date is not None and ref_date <= threshold

    return render_template(
        'technical/index.html',
        pagination=pagination,
        aktif_tab=tab,
        arama=q,
        sort=sort,
        order=order,
        secili_musteri=False,
        secili_musteri_adi=None,
        secili_musteri_telefon=None,
        tarih_baslangic=tarih_baslangic,
        tarih_bitis=tarih_bitis,
    )


def _parse_kargolandi_date_range():
    """Müşteriye gönderilen sayfası için tarih aralığı parse et."""
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


@technical_bp.route('/kargolandi')
@login_required
@technical_required
def kargolandi():
    """Müşteriye kargolanmış (tamamen tamamlanmış) cihazlar."""
    q = request.args.get('q', '')
    page = request.args.get('page', 1, type=int)
    tarih_baslangic, tarih_bitis, start_dt, end_dt = _parse_kargolandi_date_range()
    if start_dt and end_dt and start_dt > end_dt:
        flash('Başlangıç tarihi, bitiş tarihinden sonra olamaz.', 'error')
        start_dt = end_dt = None
        tarih_baslangic = tarih_bitis = ''
    sorgu = Device.query.filter(
        Device.durum == 'kargolandı'
    ).order_by(Device.updated_at.desc(), Device.id.desc())
    kargo_tarihi = func.coalesce(Device.kargolandi_at, Device.updated_at)
    if start_dt:
        sorgu = sorgu.filter(kargo_tarihi >= start_dt)
    if end_dt:
        sorgu = sorgu.filter(kargo_tarihi <= end_dt)
    sorgu = _arama_filtresi(sorgu, q)
    pagination = sorgu.paginate(page=page, per_page=PER_PAGE, error_out=False)
    return render_template(
        'technical/tamamlanmis.html',
        pagination=pagination,
        arama=q,
        tarih_baslangic=tarih_baslangic,
        tarih_bitis=tarih_bitis,
    )


@technical_bp.route('/tamamlanmis')
@login_required
@technical_required
def tamamlanmis_redirect():
    return redirect(url_for('technical.index', tab='tamamlanmis'))

@technical_bp.route('/kargolandi/<int:id>')
@login_required
@technical_required
def kargolandi_detay(id):
    cihaz = Device.query.get_or_404(id)
    if cihaz.durum != 'kargolandı':
        flash('Bu cihaz henüz kargolanmamış.', 'error')
        return redirect(url_for('technical.kargolandi'))
    # Aynı seri numarasına sahip daha eski bir kayıt varsa, ilk geliş tarihini bul.
    first_seen_date = None
    if cihaz.seri_no:
        first_same = Device.query.filter(
            Device.seri_no == cihaz.seri_no,
            Device.id != cihaz.id
        ).order_by(Device.created_at.asc()).first()
        if first_same and first_same.created_at and first_same.created_at < cihaz.created_at:
            first_seen_date = first_same.created_at
    return render_template('technical/tamamlanmis_detay.html', cihaz=cihaz, first_seen_date=first_seen_date)


@technical_bp.route('/cihaz/<int:id>', methods=['GET', 'POST'])
@login_required
@technical_required
def cihaz_detay(id):
    cihaz = Device.query.get_or_404(id)
    if cihaz.durum not in ('teknik_serviste', 'teknik_serviste_tamir', 'arizalar_tamamlandi'):
        flash('Bu cihaz teknik serviste değil.', 'error')
        return redirect(url_for('technical.index'))

    # Aynı seri numarasına sahip daha eski bir kayıt varsa, ilk geliş tarihini bul.
    first_seen_date = None
    if cihaz.seri_no:
        first_same = Device.query.filter(
            Device.seri_no == cihaz.seri_no,
            Device.id != cihaz.id
        ).order_by(Device.created_at.asc()).first()
        if first_same and first_same.created_at and first_same.created_at < cihaz.created_at:
            first_seen_date = first_same.created_at

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'ariza_ekle':
            if cihaz.durum == 'teknik_serviste_tamir':
                flash('Müşteri onayı ile tamir için gönderilen cihazlara yeni arıza eklenemez.', 'error')
            else:
                aciklama = request.form.get('ariza_aciklama', '').strip()
                if aciklama:
                    fault = Fault(device_id=cihaz.id, aciklama=aciklama, yapildi=False)
                    db.session.add(fault)
                    db.session.commit()
                    log_event('technical', 'fault_add', extra=f'device_id={cihaz.id} fault_id={fault.id}')
                    flash('Arıza kaydı eklendi.', 'success')
                else:
                    flash('Arıza açıklaması girin.', 'error')
        elif action == 'ariza_yapildi':
            fault_id = request.form.get('fault_id', type=int)
            fault = Fault.query.filter_by(id=fault_id, device_id=cihaz.id).first()
            if fault:
                fault.yapildi = True
                if cihaz.durum == 'teknik_serviste_tamir' and cihaz.tamir_yapan_id is None:
                    cihaz.tamir_yapan_id = current_user.id
                db.session.commit()
                log_event('technical', 'fault_mark_done', extra=f'device_id={cihaz.id} fault_id={fault.id}')
                flash('Arıza yapıldı olarak işaretlendi.', 'success')
        elif action == 'ariza_sil':
            fault_id = request.form.get('fault_id', type=int)
            fault = Fault.query.filter_by(id=fault_id, device_id=cihaz.id).first()
            if fault and not fault.yapildi and cihaz.durum == 'teknik_serviste':
                db.session.delete(fault)
                db.session.commit()
                log_event('technical', 'fault_delete', extra=f'device_id={cihaz.id} fault_id={fault_id}')
                flash('Arıza kaydı silindi.', 'success')
        elif action == 'on_inceleme_tamamlandi':
            if cihaz.durum != 'teknik_serviste' or cihaz.faults.count() == 0:
                flash('Ön inceleme için en az bir arıza kaydı ekleyin.', 'error')
            else:
                cihaz.durum = 'on_inceleme_sekreterlikte'
                cihaz.on_inceleme_sekreterlikte_at = datetime.now()
                db.session.commit()
                log_event('technical', 'on_inceleme_tamamlandi', extra=f'device_id={cihaz.id}')
                flash('Ön inceleme tamamlandı. Sekreterlik raporu müşteriye gönderebilir.', 'success')
                # Ön inceleme sekmesinde kal (cihaz artık teknik serviste olmadığı için listeye dön)
                return_tab = request.form.get('return_tab') or request.args.get('return_tab') or 'on_inceleme'
                try:
                    page = int(request.form.get('return_page') or request.args.get('return_page') or 1)
                except (TypeError, ValueError):
                    page = 1
                q = request.form.get('q') or request.args.get('q') or ''
                redir_kw = {'tab': return_tab, 'page': page, 'q': q}
                if return_tab == 'musteri_cihazlari':
                    mu_adi = request.form.get('return_musteri_adi') or request.args.get('return_musteri_adi')
                    mu_tel = request.form.get('return_musteri_telefon') or request.args.get('return_musteri_telefon') or ''
                    if mu_adi:
                        redir_kw['musteri_adi'] = mu_adi
                        redir_kw['musteri_telefon'] = mu_tel
                return redirect(url_for('technical.index', **redir_kw))
        elif action == 'kontrol_kisi_ata':
            if cihaz.durum != 'teknik_serviste_tamir':
                flash('Kontrol kişisi sadece tamir aşamasındaki cihazlar için atanabilir.', 'error')
            elif cihaz.faults.filter_by(yapildi=False).count() > 0:
                flash('Tüm arızaları yapıldı işaretleyin.', 'error')
            else:
                kid = request.form.get('kontrol_kisi_id', type=int)
                if kid and kid != current_user.id:
                    kontrol_user = User.query.filter(User.id == kid, User.role == 'teknik').first()
                    if kontrol_user:
                        cihaz.kontrol_kisi_id = kid
                        cihaz.kontrol_onaylandi_at = None
                        db.session.commit()
                        log_event('technical', 'kontrol_kisi_ata', extra=f'device_id={cihaz.id} kontrol_kisi_id={kid}')
                        if kontrol_user.email:
                            cihaz_url = url_for('technical.cihaz_detay', id=cihaz.id, _external=True)
                            subject = f'SER-CRM: Cihaz #{cihaz.id} için kontrol kişisi olarak atandınız'
                            body_html = (
                                f'<p>Merhaba <strong>{kontrol_user.username}</strong>,</p>'
                                f'<p><strong>{current_user.username}</strong> sizi aşağıdaki cihaz için <strong>kontrol kişisi</strong> olarak atadı.</p>'
                                f'<p><strong>Cihaz #</strong>{cihaz.id}<br>'
                                f'<strong>Firma:</strong> {cihaz.musteri_adi or "-"}<br>'
                                f'<strong>Cihaz:</strong> {cihaz.cihaz_bilgisi or (cihaz.cihaz_marka or "") + " " + (cihaz.cihaz_model or "")}</p>'
                                f'<p>Tamamlanan arızaları kontrol edip onayladıktan sonra cihaz sekreterliğe bildirilebilir.</p>'
                                f'<p><a href="{cihaz_url}">Cihaz detayına git</a></p>'
                                f'<p>— SER-CRM</p>'
                            )
                            ok, err = send_email(kontrol_user.email, subject, body_html)
                            if not ok and err:
                                log_event('technical', 'kontrol_mail_fail', extra=f'device_id={cihaz.id} error={err}')
                        flash('Kontrol kişisi atandı. Sekreterliğe bildirmek için atanan personelin onayı gerekir.', 'success')
                    else:
                        flash('Geçersiz kontrol kişisi.', 'error')
                else:
                    flash('Kendiniz dışında bir teknik personel seçin.', 'error')
        elif action == 'kontrol_onayla':
            if cihaz.durum != 'teknik_serviste_tamir' or not cihaz.kontrol_kisi_id:
                flash('Bu cihaz için kontrol atanmamış.', 'error')
            elif cihaz.kontrol_kisi_id != current_user.id:
                flash('Sadece atanan kontrol kişisi onaylayabilir.', 'error')
            else:
                cihaz.kontrol_onaylandi_at = datetime.now()
                db.session.commit()
                log_event('technical', 'kontrol_onayla', extra=f'device_id={cihaz.id}')
                flash('Kontrol onaylandı. Cihaz sekreterliğe bildirilebilir.', 'success')
        elif action == 'arizalar_tamamlandi':
            if cihaz.durum != 'teknik_serviste_tamir':
                flash('Bu işlem sadece tamir aşamasındaki cihazlar için geçerlidir.', 'error')
            elif cihaz.tamir_yapan_id and cihaz.tamir_yapan_id != current_user.id:
                flash('Sekreterliğe bildir butonuna sadece tamiri yapan personel basabilir.', 'error')
            elif not cihaz.kontrol_onaylandi_at:
                flash('Sekreterliğe bildirmek için önce kontrol kişisi atanmalı ve kontrol onayı alınmalıdır.', 'error')
            else:
                cihaz.durum = 'arizalar_tamamlandi'
                cihaz.arizalar_tamamlandi_at = datetime.now()
                db.session.commit()
                log_event('technical', 'device_mark_faults_done', extra=f'device_id={cihaz.id}')
                flash('Arızalar tamamlandı. Sekreterlik kargolandı işaretleyebilir.', 'success')
        # Sadece "Tüm arızalar tamamlandı" sonrası listeye dön; arıza ekle / yapıldı işaretle sonrası aynı sayfada kal
        return_tab = request.args.get('return_tab') or request.form.get('return_tab')
        if action == 'arizalar_tamamlandi' and return_tab is not None:
            try:
                page = int(request.args.get('return_page') or request.form.get('return_page') or 1)
            except (TypeError, ValueError):
                page = 1
            q = request.args.get('q') or request.form.get('q') or ''
            redir_kw = {'tab': return_tab, 'page': page, 'q': q}
            if return_tab == 'musteri_cihazlari':
                mu_adi = request.args.get('return_musteri_adi') or request.form.get('return_musteri_adi')
                mu_tel = request.args.get('return_musteri_telefon') or request.form.get('return_musteri_telefon') or ''
                if mu_adi:
                    redir_kw['musteri_adi'] = mu_adi
                    redir_kw['musteri_telefon'] = mu_tel
            return redirect(url_for('technical.index', **redir_kw))
        # Arıza eklendi / yapıldı işaretlendi: aynı detay sayfasında kal (return parametreleri URL'de kalsın)
        kwargs = {'id': cihaz.id}
        if return_tab:
            kwargs['return_tab'] = return_tab
        if request.args.get('return_page') or request.form.get('return_page'):
            kwargs['return_page'] = request.args.get('return_page') or request.form.get('return_page')
        if request.args.get('q') is not None or request.form.get('q') is not None:
            kwargs['q'] = request.args.get('q') or request.form.get('q') or ''
        if request.args.get('return_musteri_adi') or request.form.get('return_musteri_adi'):
            kwargs['return_musteri_adi'] = request.args.get('return_musteri_adi') or request.form.get('return_musteri_adi')
            kwargs['return_musteri_telefon'] = request.args.get('return_musteri_telefon') or request.form.get('return_musteri_telefon') or ''
        return redirect(url_for('technical.cihaz_detay', **kwargs))

    teknik_users = User.query.filter(User.role == 'teknik', User.id != current_user.id).order_by(User.username).all()
    return render_template('technical/cihaz_detay.html', cihaz=cihaz, first_seen_date=first_seen_date,
        teknik_users=teknik_users,
        return_tab=request.args.get('return_tab'), return_page=request.args.get('return_page'), return_q=request.args.get('q'),
        return_musteri_adi=request.args.get('return_musteri_adi', ''), return_musteri_telefon=request.args.get('return_musteri_telefon') or '')


@technical_bp.route('/cihaz/<int:id>/duzenle', methods=['GET', 'POST'])
@login_required
@technical_required
def cihaz_duzenle(id):
    cihaz = Device.query.get_or_404(id)
    if cihaz.durum not in ('teknik_serviste', 'arizalar_tamamlandi', 'kargolandı'):
        flash('Bu cihazı düzenleyemezsiniz.', 'error')
        return redirect(url_for('technical.index'))
    if request.method == 'POST':
        cihaz.musteri_adi = request.form.get('musteri_adi', '').strip()
        cihaz.musteri_telefon = request.form.get('musteri_telefon', '').strip() or None
        cihaz.musteri_adres = request.form.get('musteri_adres', '').strip() or None
        cihaz.cihaz_bilgisi = request.form.get('cihaz_bilgisi', '').strip()
        cihaz.seri_no = request.form.get('seri_no', '').strip() or None
        cihaz.aciklama = request.form.get('aciklama', '').strip() or None
        if not cihaz.musteri_adi or not cihaz.cihaz_bilgisi:
            flash('Müşteri adı ve cihaz bilgisi zorunludur.', 'error')
            return render_template('technical/cihaz_duzenle.html', cihaz=cihaz, return_tab=request.args.get('return_tab'), return_page=request.args.get('return_page'), return_q=request.args.get('q'))
        db.session.commit()
        log_event('technical', 'device_update', extra=f'device_id={cihaz.id}')
        flash('Kayıt güncellendi.', 'success')
        return_tab = request.args.get('return_tab') or request.form.get('return_tab')
        if return_tab is not None:
            try:
                page = int(request.args.get('return_page') or request.form.get('return_page') or 1)
            except (TypeError, ValueError):
                page = 1
            q = request.args.get('q') or request.form.get('q') or ''
            return redirect(url_for('technical.index', tab=return_tab, page=page, q=q))
        if cihaz.durum == 'kargolandı':
            return redirect(url_for('technical.kargolandi_detay', id=cihaz.id))
        return redirect(url_for('technical.cihaz_detay', id=cihaz.id))
    return render_template('technical/cihaz_duzenle.html', cihaz=cihaz, return_tab=request.args.get('return_tab'), return_page=request.args.get('return_page'), return_q=request.args.get('q'))
