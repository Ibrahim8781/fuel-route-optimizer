from django.urls import include, path

urlpatterns = [
    path("", include("routing.urls")),
]

# JSON instead of Django's HTML error pages, so every error has the same shape.
handler404 = "routing.views.not_found"
handler500 = "routing.views.server_error"
