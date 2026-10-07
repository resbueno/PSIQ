from django.contrib import admin
from django.urls import include, path

from apps.contas import views as contas_views
from apps.core import views as core_views
from apps.portal import views as portal_views

urlpatterns = [
    path("", core_views.painel, name="painel"),
    path("sw.js", core_views.service_worker, name="service_worker"),
    path("saude/", core_views.saude, name="saude"),
    path("manifest.webmanifest", core_views.manifesto, name="manifesto"),
    path("", include("apps.contas.urls")),
    path("", include("apps.pacientes.urls")),
    path("", include("apps.agenda.urls")),
    path("", include("apps.prontuario.urls")),
    path("", include("apps.financeiro.urls")),
    path("", include("apps.portal.urls")),
    path("", include("apps.calendarios.urls")),
    path("", include("apps.operador.urls")),
    path("", include("apps.relatorios.urls")),
    path("", include("apps.importacao.urls")),
    path("admin/", admin.site.urls),
    # Link de cada consultório (escolhido pelo admin ao criar); tem que vir por último,
    # senão um slug igual ao primeiro trecho de uma rota acima nunca seria alcançado.
    path("<slug:slug>/agenda/", portal_views.publico_agenda, name="publico_agenda"),
    path("<slug:slug>/", contas_views.entrar, name="entrar_consultorio"),
]
