from django.contrib import admin

from .models import Consultorio, Contrato, PagamentoPlataforma, Plano


@admin.register(Plano)
class PlanoAdmin(admin.ModelAdmin):
    list_display = ("nome", "tipo_cobranca", "limite_pacientes_ativos", "limite_profissionais", "preco")


@admin.register(Consultorio)
class ConsultorioAdmin(admin.ModelAdmin):
    list_display = ("nome", "slug", "plano", "status", "criado_em")
    list_filter = ("status", "plano")
    search_fields = ("nome", "slug", "documento")
    prepopulated_fields = {"slug": ("nome",)}


@admin.register(Contrato)
class ContratoAdmin(admin.ModelAdmin):
    list_display = ("consultorio", "tipo", "profissional", "vigencia_inicio", "status")
    list_filter = ("tipo", "status")


@admin.register(PagamentoPlataforma)
class PagamentoPlataformaAdmin(admin.ModelAdmin):
    list_display = ("consultorio", "competencia", "valor", "status", "pago_em")
    list_filter = ("status",)
