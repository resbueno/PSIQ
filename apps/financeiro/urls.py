from django.urls import path

from . import views

app_name = "financeiro"

urlpatterns = [
    path("financeiro/", views.lancamentos, name="lancamentos"),
    path("financeiro/novo/", views.lancamento_manual, name="lancamento_manual"),
    path("financeiro/lancamentos/<uuid:pk>/", views.lancamento, name="lancamento"),
    path("financeiro/consultas/<uuid:consulta_pk>/lancar/", views.lancar_consulta, name="lancar_consulta"),
    path("financeiro/recibos/<uuid:recibo_pk>/", views.recibo_baixar, name="recibo_baixar"),
    path("financeiro/convenios/fila/", views.convenio_fila, name="convenio"),
    path("financeiro/convenios/faturar/", views.convenio_faturar, name="convenio_faturar"),
    path("financeiro/convenios/<uuid:pk>/retorno/", views.convenio_retorno, name="convenio_retorno"),
    path("financeiro/repasses/", views.repasses, name="repasses"),
    path("financeiro/repasses/<uuid:pk>/pagar/", views.repasse_pagar, name="repasse_pagar"),
    path("financeiro/config/<slug:chave>/", views.config_lista, name="config_lista"),
    path("financeiro/config/<slug:chave>/novo/", views.config_form, name="config_novo"),
    path("financeiro/config/<slug:chave>/<uuid:pk>/", views.config_form, name="config_editar"),
    path("financeiro/config/<slug:chave>/<uuid:pk>/remover/", views.config_remover, name="config_remover"),
]
