from django.contrib import admin
from django.urls import include, path

from apps.core import views as core_views

urlpatterns = [
    path("", core_views.painel, name="painel"),
    path("", include("apps.contas.urls")),
    path("admin/", admin.site.urls),
]
