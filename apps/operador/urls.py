from django.urls import path

from . import views

app_name = "operador"

urlpatterns = [
    path("operador/", views.painel, name="painel"),
    path("operador/novo/", views.novo_consultorio, name="novo"),
    path("operador/consultorios/<uuid:pk>/", views.consultorio, name="consultorio"),
    path("operador/consultorios/<uuid:pk>/alterar/", views.alterar, name="alterar"),
    path("operador/consultorios/<uuid:pk>/pagamentos/<uuid:pagamento_pk>/pagar/", views.pagar, name="pagar"),
    path("operador/consultorios/<uuid:pk>/suporte/entrar/", views.suporte_entrar, name="suporte_entrar"),
    path("operador/suporte/sair/", views.suporte_sair, name="suporte_sair"),
    path("suporte/", views.suporte_cliente, name="suporte_cliente"),
    path("suporte/<uuid:pk>/revogar/", views.suporte_revogar, name="suporte_revogar"),
]
