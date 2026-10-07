from django.urls import path

from . import views

app_name = "calendarios"

urlpatterns = [
    path("calendarios/", views.lista, name="lista"),
    path("calendarios/conectar/<slug:provedor>/", views.conectar, name="conectar"),
    path("calendarios/retorno/<slug:provedor>/", views.retorno, name="retorno"),
    path("calendarios/<uuid:pk>/sincronizar/", views.sincronizar, name="sincronizar"),
    path("calendarios/<uuid:pk>/nome-do-paciente/", views.alternar_nome, name="alternar_nome"),
    path("calendarios/<uuid:pk>/desconectar/", views.desconectar, name="desconectar"),
]
