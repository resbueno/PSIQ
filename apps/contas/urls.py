from django.urls import path

from . import views

app_name = "contas"

urlpatterns = [
    path("entrar/", views.entrar, name="entrar"),
    path("entrar/2fa/", views.verificar_2fa, name="verificar_2fa"),
    path("sair/", views.sair, name="sair"),
    path("conta/ver-como/<uuid:usuario_pk>/", views.ver_como, name="ver_como"),
    path("consultorio/escolher/", views.escolher_consultorio, name="escolher_consultorio"),
    path("conta/2fa/configurar/", views.configurar_2fa, name="configurar_2fa"),
    path("conta/dispositivos/", views.dispositivos, name="dispositivos"),
    path("conta/dispositivos/<uuid:pk>/revogar/", views.revogar_dispositivo, name="revogar_dispositivo"),
    path("usuarios/", views.usuarios, name="usuarios"),
    path("usuarios/novo/", views.novo_usuario, name="novo_usuario"),
    path("usuarios/<uuid:pk>/alternar/", views.alternar_vinculo, name="alternar_vinculo"),
    path("auditoria/", views.auditoria_lista, name="auditoria"),
]
