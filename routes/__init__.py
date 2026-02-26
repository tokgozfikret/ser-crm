# -*- coding: utf-8 -*-
from .auth import auth_bp
from .secretary import secretary_bp
from .technical import technical_bp
from .sales import sales_bp
from .admin import admin_bp
from .musteri_panel import musteri_panel_bp

__all__ = ['auth_bp', 'secretary_bp', 'technical_bp', 'sales_bp', 'admin_bp', 'musteri_panel_bp']
