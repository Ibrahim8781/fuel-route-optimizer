"""Settings for the test suite: no .env needed, no network, isolated cache."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-key")

from fuelroute.settings import *  # noqa: E402, F403

ORS_API_KEY = "test-key"
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "tests"}}
REST_FRAMEWORK = {**REST_FRAMEWORK, "DEFAULT_THROTTLE_CLASSES": []}  # noqa: F405
