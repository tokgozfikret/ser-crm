# -*- coding: utf-8 -*-
"""
Basit log yardımcıları.

Özellikler:
- Her panel için ayrı log dosyası (auth, admin, secretary, technical, sales)
- RotatingFileHandler ile otomatik log döndürme (rotation)
"""

import logging
import os
from logging.handlers import RotatingFileHandler

from flask import current_app, has_request_context, request


LOG_FILES = {
    'auth': 'auth_log.txt',
    'admin': 'admin_log.txt',
    'secretary': 'secretary_log.txt',
    'technical': 'technical_log.txt',
    'sales': 'sales_log.txt',
}


def _get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(f'ser_crm.{name}')
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    app = current_app._get_current_object()
    log_dir = app.config.get('LOG_DIR') or os.path.join(os.path.dirname(__file__), 'logs')
    os.makedirs(log_dir, exist_ok=True)

    filename = LOG_FILES.get(name, f'{name}.log')
    log_path = os.path.join(log_dir, filename)

    # ~1 MB dosya boyutuna ulaştığında döndür, 5 yedek tut
    handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=5, encoding='utf-8')
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    handler.setFormatter(formatter)

    logger.addHandler(handler)
    logger.propagate = False
    return logger


def log_event(category: str, action: str, message: str = '', user=None, extra: str | None = None) -> None:
    """
    Tek satırlık log kaydı yaz.

    Örnek satır:
    2026-02-11 16:30:00,123 INFO action=device_add ip=127.0.0.1 user=admin role=admin msg="Yeni cihaz eklendi" device_id=42
    """
    try:
        logger = _get_logger(category)
    except Exception:
        # current_app yoksa ya da başka bir sorun varsa sessizce geç
        return

    parts: list[str] = [f'action={action}']

    if has_request_context():
        ip = request.remote_addr or '-'
        parts.append(f'ip={ip}')

    if user is None and has_request_context():
        try:
            from flask_login import current_user  # type: ignore

            if current_user.is_authenticated:
                user = current_user
        except Exception:
            user = None

    if user is not None:
        username = getattr(user, 'username', 'unknown')
        role = getattr(user, 'role', '-')
        parts.append(f'user={username}')
        parts.append(f'role={role}')

    if message:
        parts.append(f'msg="{message}"')

    if extra:
        parts.append(extra)

    logger.info(' '.join(parts))

