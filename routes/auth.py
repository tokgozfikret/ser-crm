# -*- coding: utf-8 -*-
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy import func
from extensions import db
from models import User, PasswordResetToken
from logging_utils import log_event
from mail_utils import send_email

auth_bp = Blueprint('auth', __name__)


def _write_auth_log(action, user, extra=None):
    """Giriş/çıkış log kaydı (auth kategorisi)."""
    # Eski fonksiyon imzasını koruyarak yeni log sistemine yönlendiriyoruz.
    extra_str = extra if isinstance(extra, str) else None
    log_event('auth', action, user=user, extra=extra_str)


@auth_bp.route('/giris', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        if current_user.role == 'admin':
            return redirect(url_for('admin.index'))
        if current_user.role == 'sekreter':
            return redirect(url_for('secretary.index'))
        if current_user.role == 'satis':
            return redirect(url_for('sales.index'))
        return redirect(url_for('technical.index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            _write_auth_log('login', user)
            flash('Giriş başarılı.', 'success')
            if user.role == 'admin':
                return redirect(url_for('admin.index'))
            if user.role == 'sekreter':
                return redirect(url_for('secretary.index'))
            if user.role == 'satis':
                return redirect(url_for('sales.index'))
            return redirect(url_for('technical.index'))
        else:
            _write_auth_log('login_failed', None, extra=f'username={username}')
            flash('Kullanıcı adı veya şifre hatalı.', 'error')
    return render_template('auth/login.html')


@auth_bp.route('/kayit', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        if current_user.role == 'admin':
            return redirect(url_for('admin.index'))
        if current_user.role == 'sekreter':
            return redirect(url_for('secretary.index'))
        if current_user.role == 'satis':
            return redirect(url_for('sales.index'))
        return redirect(url_for('technical.index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        password2 = request.form.get('password2', '')
        role = (request.form.get('role') or 'sekreter').strip()
        phone = (request.form.get('phone') or '').strip()
        email = (request.form.get('email') or '').strip()
        first_name = (request.form.get('first_name') or '').strip()
        last_name = (request.form.get('last_name') or '').strip()

        if not username or not password or not phone or not email or not first_name or not last_name:
            flash('Kullanıcı adı, şifre, ad, soyad, telefon ve e-posta zorunludur.', 'error')
        elif len(password) < 6:
            flash('Şifre en az 6 karakter olmalıdır.', 'error')
        elif password != password2:
            flash('Şifreler eşleşmiyor.', 'error')
        elif role not in ('sekreter', 'teknik', 'satis'):
            flash('Geçersiz rol seçimi.', 'error')
        elif User.query.filter_by(username=username).first():
            flash('Bu kullanıcı adı zaten kullanılıyor.', 'error')
        else:
            user = User(
                username=username,
                password_hash=generate_password_hash(password),
                role=role,
                phone=phone,
                email=email,
                first_name=first_name,
                last_name=last_name,
            )
            db.session.add(user)
            db.session.commit()
            flash('Kayıt başarılı. Şimdi giriş yapabilirsiniz.', 'success')
            return redirect(url_for('auth.login'))

    return render_template('auth/register.html')


@auth_bp.route('/sifremi-unuttum', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        if not email:
            flash('E-posta adresi girin.', 'error')
            return render_template('auth/forgot_password.html')
        user = User.query.filter(func.lower(User.email) == email).first()
        if not user:
            flash('Bu e-posta adresi sistemde kayıtlı değil.', 'error')
            return render_template('auth/forgot_password.html')
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(hours=1)
        PasswordResetToken.query.filter_by(user_id=user.id).delete()
        prt = PasswordResetToken(user_id=user.id, token=token, expires_at=expires_at)
        db.session.add(prt)
        db.session.commit()
        reset_url = url_for('auth.reset_password', token=token, _external=True)
        subject = 'SER-CRM - Şifre sıfırlama'
        body_html = f'''
        <p>Merhaba {user.first_name or user.username},</p>
        <p>Şifre sıfırlama talebinde bulundunuz. Aşağıdaki bağlantıya tıklayarak yeni şifrenizi belirleyebilirsiniz:</p>
        <p><a href="{reset_url}">{reset_url}</a></p>
        <p>Bu bağlantı 1 saat içinde geçerliliğini yitirecektir.</p>
        <p>Bu talebi siz yapmadıysanız lütfen bu e-postayı görmezden geliniz.</p>
        <p>SER-CRM</p>
        '''
        ok, err = send_email(user.email, subject, body_html, body_text=reset_url)
        if not ok:
            flash(f'E-posta gönderilemedi: {err}', 'error')
            return render_template('auth/forgot_password.html')
        flash('Şifre sıfırlama bağlantısı e-posta adresinize gönderildi.', 'success')
        return redirect(url_for('auth.login'))
    return render_template('auth/forgot_password.html')


@auth_bp.route('/sifre-sifirla/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    prt = PasswordResetToken.query.filter_by(token=token).first()
    if not prt or prt.expires_at < datetime.now():
        flash('Bağlantı geçersiz veya süresi dolmuş. Lütfen tekrar şifre sıfırlama talebinde bulunun.', 'error')
        return redirect(url_for('auth.forgot_password'))
    if request.method == 'POST':
        password = request.form.get('password', '')
        password2 = request.form.get('password2', '')
        if len(password) < 6:
            flash('Şifre en az 6 karakter olmalıdır.', 'error')
            return render_template('auth/reset_password.html', token=token)
        if password != password2:
            flash('Şifreler eşleşmiyor.', 'error')
            return render_template('auth/reset_password.html', token=token)
        user = User.query.get(prt.user_id)
        user.password_hash = generate_password_hash(password)
        db.session.delete(prt)
        db.session.commit()
        _write_auth_log('password_reset', user)
        flash('Şifreniz güncellendi. Giriş yapabilirsiniz.', 'success')
        return redirect(url_for('auth.login'))
    return render_template('auth/reset_password.html', token=token)


@auth_bp.route('/cikis')
@login_required
def logout():
    _write_auth_log('logout', current_user)
    logout_user()
    flash('Çıkış yaptınız.', 'info')
    return redirect(url_for('auth.login'))
