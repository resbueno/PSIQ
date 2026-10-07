from django.urls import path

from . import views

app_name = "relatorios"

urlpatterns = [
    path("relatorios/", views.indice, name="indice"),
    path("relatorios/<slug:tipo>/", views.ver, name="ver"),
    path("exportacao/", views.pagina_exportacao, name="exportacao"),
    path("exportacao/consultorio/", views.exportar_consultorio, name="exportar_consultorio"),
    path("exportacao/prontuarios/", views.exportar_prontuarios, name="exportar_prontuarios"),
]
