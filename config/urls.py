from django.contrib import admin
from django.urls import include, path

from apps.core import views as core_views

urlpatterns = [
    path("", core_views.painel, name="painel"),
    path("sw.js", core_views.service_worker, name="service_worker"),
    path("manifest.webmanifest", core_views.manifesto, name="manifesto"),
    path("", include("apps.contas.urls")),
    path("", include("apps.pacientes.urls")),
    path("", include("apps.agenda.urls")),
    path("", include("apps.prontuario.urls")),
    path("", include("apps.financeiro.urls")),
    path("", include("apps.portal.urls")),
    path("", include("apps.calendarios.urls")),
    path("admin/", admin.site.urls),
]
