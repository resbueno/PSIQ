from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Profissional, Usuario


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    ordering = ("email",)
    list_display = ("email", "nome", "is_active", "is_staff", "segundo_fator_ativo")
    search_fields = ("email", "nome")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Dados", {"fields": ("nome",)}),
        ("Acesso", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Segurança", {"fields": ("segundo_fator_ativo", "falhas_login", "bloqueado_ate")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "nome", "password1", "password2")}),
    )
    readonly_fields = ("segundo_fator_ativo",)


@admin.register(Profissional)
class ProfissionalAdmin(admin.ModelAdmin):
    """O operador valida o registro no conselho preenchendo `validado_em`."""

    list_display = ("usuario", "tipo", "conselho", "numero", "uf", "validado_em", "ativo")
    list_filter = ("tipo", "ativo")
    search_fields = ("usuario__nome", "usuario__email", "numero")
