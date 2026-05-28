"""
config/settings.py
==================
Django settings for the Blackjack project.

Environment variables (set in .env for local dev, Render dashboard for prod)
-----------------------------------------------------------------------------
SECRET_KEY          Django secret key (required in production)
DEBUG               "True" locally, unset or "False" on Render
DATABASE_URL        Postgres URL on Render (omit for SQLite dev)
ALLOWED_HOSTS       Comma-separated hostnames (e.g. "myapp.onrender.com")
FRONTEND_URL        React app origin for CORS (e.g. "https://myapp.onrender.com")

Quick-start commands
--------------------
    pip install -r requirements.txt
    python manage.py migrate
    python manage.py seed_tables
    python manage.py createsuperuser
    python manage.py runserver
"""

import os
from pathlib import Path

# ── Base directory ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent


# ── Core ────────────────────────────────────────────────────────────────────────
SECRET_KEY: str = os.environ.get(
    "SECRET_KEY",
    "django-insecure-change-me-before-deploying-xxxxxxxxxxxxxxxxxxxxxxxx",
)
DEBUG: bool = os.environ.get("DEBUG", "True") == "True"

_raw_hosts = os.environ.get("ALLOWED_HOSTS", "")
ALLOWED_HOSTS: list[str] = (
    _raw_hosts.split(",") if _raw_hosts else ["localhost", "127.0.0.1"]
)


# ── Installed apps ──────────────────────────────────────────────────────────────
INSTALLED_APPS: list[str] = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",    # Serve static files via WhiteNoise in dev too
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "rest_framework.authtoken",
    "corsheaders",
    # Our app
    "game",
]

MIDDLEWARE: list[str] = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",   # Must come right after SecurityMiddleware
    "corsheaders.middleware.CorsMiddleware",        # Must come before CommonMiddleware
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF: str = "config.urls"
WSGI_APPLICATION: str = "config.wsgi.application"


# ── Templates ───────────────────────────────────────────────────────────────────
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# ── Database ─────────────────────────────────────────────────────────────────────
# SQLite for local dev; switch to Postgres on Render by setting DATABASE_URL.
_database_url = os.environ.get("DATABASE_URL", "")

if _database_url:
    # Production: parse the DATABASE_URL provided by Render
    # Requires dj-database-url:  pip install dj-database-url
    import dj_database_url  # type: ignore[import]
    DATABASES = {
        "default": dj_database_url.config(
            default=_database_url,
            conn_max_age=600,
            conn_health_checks=True,
        )
    }
else:
    # Local development: SQLite
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

DEFAULT_AUTO_FIELD: str = "django.db.models.BigAutoField"


# ── Cache ────────────────────────────────────────────────────────────────────────
# LocMemCache is fine for a single-process dev server.
# In production on Render (where multiple Gunicorn workers run), use Redis so
# the dirty flag set by one worker is visible to the SSE worker.
#
# Redis setup (optional for single-worker Render deployment):
#     pip install django-redis
#     CACHES = {
#         "default": {
#             "BACKEND": "django_redis.cache.RedisCache",
#             "LOCATION": os.environ["REDIS_URL"],
#             "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
#         }
#     }
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "blackjack-cache",
    }
}


# ── Auth & passwords ─────────────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# ── Django REST Framework ────────────────────────────────────────────────────────
REST_FRAMEWORK: dict = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        # SessionAuthentication left out intentionally — token-only for this SPA.
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        # BrowsableAPIRenderer is useful during development — remove in production
        # to reduce surface area.
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "20/minute",
        "user": "120/minute",
    },
    # Return Decimal values as strings to preserve precision on the client side
    "COERCE_DECIMAL_TO_STRING": True,
}


# ── CORS ─────────────────────────────────────────────────────────────────────────
# Allows the React dev server (Vite default: 5173) to reach the Django API.
# In production, restrict to your actual frontend URL.
_frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:5173")

CORS_ALLOWED_ORIGINS: list[str] = [
    "http://localhost:5173",     # Vite dev server
    "http://localhost:3000",     # CRA / fallback
    _frontend_url,
]

# SSE streams need credentials (auth token) — but since we use a query param,
# CORS_ALLOW_CREDENTIALS is not strictly necessary. Set True if you add cookies.
CORS_ALLOW_CREDENTIALS: bool = False


# ── Static files (WhiteNoise for Render.com) ─────────────────────────────────────
STATIC_URL: str = "/static/"
STATIC_ROOT: str = str(BASE_DIR / "staticfiles")

# Django 4.2+: use STORAGES instead of the deprecated STATICFILES_STORAGE setting.
STORAGES: dict = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    }
}

# If serving the React build from Django, the Vite build outputs to
# <repo-root>/dist (see frontend/vite.config.ts).  collectstatic copies
# everything in STATICFILES_DIRS into STATIC_ROOT for WhiteNoise to serve.
# The directory only exists after `npm run build`, so we skip it when absent.
_FRONTEND_DIST = BASE_DIR.parent / "dist"
STATICFILES_DIRS: list = [_FRONTEND_DIST] if _FRONTEND_DIST.is_dir() else []


# ── Internationalization ─────────────────────────────────────────────────────────
LANGUAGE_CODE: str = "en-us"
TIME_ZONE: str = "UTC"
USE_I18N: bool = True
USE_TZ: bool = True


# ── Logging ──────────────────────────────────────────────────────────────────────
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "DEBUG" if DEBUG else "INFO",
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "game":   {"handlers": ["console"], "level": "DEBUG", "propagate": False},
    },
}