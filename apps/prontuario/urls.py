from django.urls import path

from . import views

app_name = "prontuario"

urlpatterns = [
    path("prontuarios/", views.lista, name="lista"),
    path("prontuarios/paciente/<uuid:paciente_pk>/abrir/", views.abrir, name="abrir"),
    path("prontuarios/paciente/<uuid:paciente_pk>/consolidado/", views.consolidado, name="consolidado"),
    path("prontuarios/delegar/", views.delegar, name="delegar"),
    path("prontuarios/modelos/", views.modelos, name="modelos"),
    path("prontuarios/modelos/<uuid:modelo_pk>/", views.modelo_editar, name="modelo_editar"),
    path("prontuarios/documentos/<uuid:documento_pk>/baixar/", views.documento_baixar, name="documento_baixar"),
    path("prontuarios/comparecimento/<uuid:consulta_pk>/", views.comparecimento, name="comparecimento"),
    path("prontuarios/<uuid:pk>/", views.detalhe, name="detalhe"),
    path("prontuarios/<uuid:pk>/registros/novo/", views.registro_novo, name="registro_novo"),
    path("prontuarios/<uuid:pk>/registros/<uuid:registro_pk>/", views.registro, name="registro"),
    path("prontuarios/<uuid:pk>/registros/<uuid:registro_pk>/editar/", views.registro_editar, name="registro_editar"),
    path("prontuarios/<uuid:pk>/registros/<uuid:registro_pk>/versoes/<int:numero>/", views.registro_versao, name="registro_versao"),
    path("prontuarios/<uuid:pk>/anexos/novo/", views.anexo_enviar, name="anexo_enviar"),
    path("prontuarios/<uuid:pk>/anexos/<uuid:anexo_pk>/", views.anexo_baixar, name="anexo_baixar"),
    path("prontuarios/<uuid:pk>/documentos/novo/", views.documento_novo, name="documento_novo"),
    path("prontuarios/<uuid:pk>/documentos/<uuid:documento_pk>/liberar/", views.documento_liberar, name="documento_liberar"),
    path("prontuarios/<uuid:pk>/liberacoes/", views.liberacoes, name="liberacoes"),
    path("prontuarios/<uuid:pk>/liberacoes/<uuid:liberacao_pk>/revogar/", views.liberacao_revogar, name="liberacao_revogar"),
    path("prontuarios/<uuid:pk>/consentimentos/", views.consentimentos, name="consentimentos"),
    path("prontuarios/<uuid:pk>/excluir/", views.excluir, name="excluir"),
    path("termo/<str:token>/", views.termo_publico, name="termo_publico"),
]
