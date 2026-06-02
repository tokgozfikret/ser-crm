# -*- coding: utf-8 -*-
"""
SER-CRM üretim (production) başlatıcı.

Windows üzerinde ofis ağında 7/24 çalıştırmak için kullanılır.
- gunicorn yerine waitress kullanır (gunicorn Windows'ta çalışmaz).
- SECRET_KEY'i kodun içine yazmak yerine kalıcı bir dosyada saklar/üretir.
- debug KAPALIDIR (üretim için güvenli).

Çalıştırma:  python serve.py
"""
import os
import secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(BASE_DIR, 'secret_key.txt')

# Güçlü ve kalıcı bir SECRET_KEY sağla.
# Ortam değişkeni varsa onu kullan; yoksa dosyadan oku; o da yoksa üret ve kaydet.
if not os.environ.get('SECRET_KEY'):
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, 'r', encoding='utf-8') as f:
            os.environ['SECRET_KEY'] = f.read().strip()
    else:
        new_key = secrets.token_hex(32)
        with open(KEY_FILE, 'w', encoding='utf-8') as f:
            f.write(new_key)
        os.environ['SECRET_KEY'] = new_key
        print('Yeni SECRET_KEY uretildi ve secret_key.txt dosyasina kaydedildi.')

# SECRET_KEY ayarlandiktan SONRA app'i import et (app.py import aninda okuyor).
from app import app, init_db  # noqa: E402
from waitress import serve  # noqa: E402

HOST = os.environ.get('CRM_HOST', '0.0.0.0')
PORT = int(os.environ.get('CRM_PORT', '8080'))

if __name__ == '__main__':
    init_db()
    print('=' * 60)
    print(' SER-CRM uretim sunucusu calisiyor (waitress)')
    print(f' Bu bilgisayarda:   http://localhost:{PORT}')
    print(f' Agdaki diger PC\'ler: http://<bu-bilgisayarin-IP-adresi>:{PORT}')
    print(' Durdurmak icin: Ctrl + C')
    print('=' * 60)
    serve(app, host=HOST, port=PORT, threads=8)
