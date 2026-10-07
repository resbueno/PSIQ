from datetime import date

from django import forms
from django.contrib.auth.password_validation import validate_password

from apps.plataforma.models import SLUGS_RESERVADOS, Consultorio, Plano

from .servico import DURACOES_SUPORTE_HORAS


class PlanoForm(forms.Form):
    plano = forms.ModelChoiceField(queryset=Plano.objects.all(), required=False, empty_label="Sem plano", label="Plano")


class StatusForm(forms.Form):
    status = forms.ChoiceField(choices=Consultorio.Status.choices, label="Situação")


class PagamentoForm(forms.Form):
    competencia = forms.DateField(label="Mês de referência", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"), initial=lambda: date.today().replace(day=1))
    valor = forms.DecimalField(label="Valor (R$)", max_digits=10, decimal_places=2, min_value=0)
    vencimento = forms.DateField(label="Vencimento", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))


class NovoConsultorioForm(forms.Form):
    nome = forms.CharField(label="Nome do consultório", max_length=160)
    slug = forms.SlugField(
        label="Link de acesso", max_length=60,
        help_text="Endereço do consultório, ex.: “clinica-bem-estar” vira /clinica-bem-estar/.",
    )
    documento = forms.CharField(label="CNPJ ou CPF", max_length=18, required=False)
    plano = forms.ModelChoiceField(queryset=Plano.objects.all(), required=False, empty_label="Sem plano", label="Plano")
    admin_nome = forms.CharField(label="Nome do administrador", max_length=160)
    admin_email = forms.EmailField(label="E-mail do administrador")
    admin_senha = forms.CharField(label="Senha inicial", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}), help_text="Mínimo de 12 caracteres.")

    def clean_admin_senha(self):
        validate_password(self.cleaned_data["admin_senha"])
        return self.cleaned_data["admin_senha"]

    def clean_slug(self):
        slug = self.cleaned_data["slug"].lower()
        if slug in SLUGS_RESERVADOS:
            raise forms.ValidationError("Este link é reservado para o sistema. Escolha outro.")
        if Consultorio.objects.filter(slug=slug).exists():
            raise forms.ValidationError("Já existe um consultório com este link.")
        return slug


class AutorizarSuporteForm(forms.Form):
    operador_email = forms.EmailField(label="E-mail do atendente do MeuPSIQ")
    horas = forms.TypedChoiceField(label="Por quanto tempo", coerce=int, choices=[(h, f"{h} hora{'s' if h > 1 else ''}") for h in DURACOES_SUPORTE_HORAS])
    motivo = forms.CharField(label="Motivo", max_length=300, widget=forms.Textarea(attrs={"rows": 3}))
