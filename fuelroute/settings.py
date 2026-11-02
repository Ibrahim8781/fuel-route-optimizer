"""
Django settings for the fuel route optimizer.

All environment-specific values come from environment variables (a local
`.env` file is loaded automatically for development). See `.env.example`.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


# --- Core -------------------------------------------------------------------

DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off.")
    SECRET_KEY = "dev-only-insecure-key"

ALLOWED_HOSTS = [
    host.strip() for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host.strip()
]

# Deliberately minimal: no admin, auth or sessions. The API is public and read-only.
INSTALLED_APPS = [
    "rest_framework",
    "routing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "fuelroute.urls"
WSGI_APPLICATION = "fuelroute.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    },
]

# Station data is static and read once into memory, so SQLite is enough here.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

# --- Cache ------------------------------------------------------------------
# Route results are cached so repeated requests (and the map page) never call
# the routing API again. Use Redis in production so all workers share it.

if os.environ.get("REDIS_URL"):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": os.environ["REDIS_URL"],
        }
    }
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# --- Django REST framework --------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
    # Protects the free routing API quota from a single noisy client.
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"anon": os.environ.get("API_RATE_LIMIT", "60/minute")},
}

# --- Routing provider (OpenRouteService) ------------------------------------

ORS_API_KEY = os.environ.get("ORS_API_KEY", "")
ORS_BASE_URL = os.environ.get("ORS_BASE_URL", "https://api.openrouteservice.org")
ORS_PROFILE = os.environ.get("ORS_PROFILE", "driving-hgv")
ORS_TIMEOUT_SECONDS = env_float("ORS_TIMEOUT_SECONDS", 20)

# --- Fuel planning ----------------------------------------------------------

FUEL_MAX_RANGE_MILES = env_float("FUEL_MAX_RANGE_MILES", 500)
FUEL_MPG = env_float("FUEL_MPG", 10)
# Stations up to this far from the route count as "on the route".
FUEL_CORRIDOR_MILES = env_float("FUEL_CORRIDOR_MILES", 5)
# The truck starts empty, so its first fill-up must be within this many miles.
FUEL_START_WINDOW_MILES = env_float("FUEL_START_WINDOW_MILES", 25)
ROUTE_CACHE_SECONDS = int(env_float("ROUTE_CACHE_SECONDS", 24 * 60 * 60))

# --- Logging ----------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "loggers": {"routing": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")}},
}

# --- Security (effective when DEBUG is off and served over HTTPS) -------------

if not DEBUG:
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
