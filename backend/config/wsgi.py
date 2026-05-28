"""
config/wsgi.py
==============
WSGI config for the Blackjack project.

Exposes the WSGI callable as module-level ``application``.
Used by Gunicorn on Render.com (see Procfile).
"""

import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
application = get_wsgi_application()