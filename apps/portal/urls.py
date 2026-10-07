from django.urls import path

from . import views

app_name = "portal"

urlpatterns = [
    path("portal/<uuid:consultorio_id>/entrar/", views.entrar, name="entrar"),
    path("portal/<uuid:consultorio_id>/codigo/", views.codigo, name="codigo"),
    path("portal/", views.home, name="home"),
    path("portal/sair/", views.sair, name="sair"),
    path("portal/paciente/", views.trocar_paciente, name="trocar_paciente"),
    path("portal/consultas/<uuid:pk>/", views.consulta_acao, name="consulta_acao"),
    path("portal/agendar/", views.agendar, name="agendar"),
    path("portal/pagamentos/", views.pagamentos, name="pagamentos"),
    path("portal/recibos/<uuid:recibo_pk>/", views.recibo, name="recibo"),
    path("portal/documentos/", views.documentos, name="documentos"),
    path("portal/documentos/<uuid:documento_pk>/", views.documento, name="documento"),
    path("portal/privacidade/", views.privacidade, name="privacidade"),
    path("portal/privacidade/consentimentos/<uuid:pk>/revogar/", views.revogar_consentimento, name="revogar_consentimento"),
    path("lgpd/", views.lgpd_lista, name="lgpd_lista"),
    path("lgpd/<uuid:pk>/atender/", views.lgpd_atender, name="lgpd_atender"),
]
