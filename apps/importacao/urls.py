from django.urls import path

from . import views

app_name = "importacao"

urlpatterns = [
    path("importacao/", views.inicio, name="inicio"),
    path("importacao/modelo.csv", views.modelo, name="modelo"),
    path("importacao/<uuid:pk>/", views.previa, name="previa"),
    path("importacao/<uuid:pk>/confirmar/", views.confirmar, name="confirmar"),
    path("importacao/<uuid:pk>/cancelar/", views.cancelar, name="cancelar"),
]
