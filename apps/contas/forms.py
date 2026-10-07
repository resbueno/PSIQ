from django import forms
from django.contrib.auth.password_validation import validate_password

from .models import Perfil, Profissional, Usuario


class EntrarForm(forms.Form):
    email = forms.EmailField(label="E-mail", widget=forms.EmailInput(attrs={"autocomplete": "username", "autofocus": True}))
    senha = forms.CharField(label="Senha", widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))


class CodigoForm(forms.Form):
    codigo = forms.RegexField(
        label="Código de 6 dígitos",
        regex=r"^\s*\d{3}\s?\d{3}\s*$",
        error_messages={"invalid": "Digite os 6 números exibidos no aplicativo."},
        widget=forms.TextInput(
            attrs={"inputmode": "numeric", "autocomplete": "one-time-code", "autofocus": True, "maxlength": 7}
        ),
    )

    def codigo_limpo(self):
        return "".join(self.cleaned_data["codigo"].split())


class NovoUsuarioForm(forms.Form):
    nome = forms.CharField(label="Nome completo", max_length=160)
    email = forms.EmailField(label="E-mail")
    perfil = forms.ChoiceField(label="Perfil", choices=Perfil.choices)
    senha = forms.CharField(
        label="Senha inicial",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Mínimo de 12 caracteres. Informe-a ao usuário por um canal seguro; ele deve trocá-la.",
        required=False,
    )
    tipo = forms.ChoiceField(
        label="Tipo de profissional", choices=[("", "—")] + list(Profissional.Tipo.choices), required=False
    )
    numero = forms.CharField(label="Número do registro no conselho", max_length=20, required=False)
    uf = forms.CharField(label="UF do registro", max_length=2, required=False)

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean_uf(self):
        return self.cleaned_data["uf"].strip().upper()

    def clean(self):
        dados = super().clean()
        email = dados.get("email")
        existente = Usuario.objects.filter(email=email).select_related().first() if email else None
        self.usuario_existente = existente

        if existente is None:
            senha = dados.get("senha")
            if not senha:
                self.add_error("senha", "Defina a senha inicial do novo usuário.")
            else:
                try:
                    validate_password(senha)
                except forms.ValidationError as erro:
                    self.add_error("senha", erro)

        if dados.get("perfil") == Perfil.PROFISSIONAL:
            ja_tem_registro = existente is not None and Profissional.objects.filter(usuario=existente).exists()
            if not ja_tem_registro:
                for campo in ("tipo", "numero", "uf"):
                    if not dados.get(campo):
                        self.add_error(campo, "Obrigatório para profissionais.")
        return dados
