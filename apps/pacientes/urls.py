from django.urls import path

from . import views

app_name = "pacientes"

urlpatterns = [
    path("pacientes/", views.lista, name="lista"),
    path("pacientes/novo/", views.novo, name="novo"),
    path("pacientes/<uuid:pk>/", views.detalhe, name="detalhe"),
    path("pacientes/<uuid:pk>/editar/", views.editar, name="editar"),
    path("pacientes/<uuid:pk>/responsaveis/novo/", views.responsavel_novo, name="responsavel_novo"),
    path("pacientes/<uuid:pk>/responsaveis/<uuid:responsavel_pk>/remover/", views.responsavel_remover, name="responsavel_remover"),
    path("pacientes/<uuid:pk>/pagador/", views.pagador_definir, name="pagador"),
    path("pacientes/<uuid:pk>/mesclar/", views.mesclar, name="mesclar"),
    path("grupos/", views.grupos, name="grupos"),
    path("grupos/novo/", views.grupo_novo, name="grupo_novo"),
]
