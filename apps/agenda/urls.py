from django.urls import path

from . import views

app_name = "agenda"

urlpatterns = [
    path("agenda/", views.semana, name="semana"),
    path("agenda/consultas/nova/", views.nova_consulta, name="nova_consulta"),
    path("agenda/consultas/<uuid:pk>/", views.consulta, name="consulta"),
    path("agenda/solicitacoes/", views.solicitacoes, name="solicitacoes"),
    path("agenda/solicitacoes/nova/", views.nova_solicitacao, name="nova_solicitacao"),
    path("agenda/solicitacoes/<uuid:pk>/decidir/", views.decidir_solicitacao, name="decidir_solicitacao"),
    path("c/<str:token>/", views.acao_publica, name="acao_publica"),
]
