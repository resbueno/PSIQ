from django.contrib import admin

from .models import Auditoria


@admin.register(Auditoria)
class AuditoriaAdmin(admin.ModelAdmin):
    """Somente leitura. Sob RLS, o operador sem contexto não enxerga linhas de consultório."""

    list_display = ("criado_em", "acao", "objeto", "usuario_id", "consultorio_id")
    list_filter = ("acao",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
