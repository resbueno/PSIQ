from django import forms

from apps.core.validadores import cpf_valido, somente_digitos

from .models import GrupoAtendimento, Paciente, Pagador, ResponsavelLegal


class _CpfTelefoneMixin:
    def clean_cpf(self):
        cpf = somente_digitos(self.cleaned_data.get("cpf", ""))
        if cpf and not cpf_valido(cpf):
            raise forms.ValidationError("CPF inválido.")
        return cpf

    def clean_telefone(self):
        return somente_digitos(self.cleaned_data.get("telefone", ""))


class PacienteForm(_CpfTelefoneMixin, forms.ModelForm):
    cpf = forms.CharField(label="CPF", max_length=14, required=False, help_text="Pode ficar vazio (ex.: criança).")

    class Meta:
        model = Paciente
        fields = ["nome", "cpf", "nascimento", "email", "telefone", "convenio", "carteirinha", "exige_aprovacao", "ativo"]
        widgets = {"nascimento": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def __init__(self, *args, consultorio, **kwargs):
        super().__init__(*args, **kwargs)
        self.consultorio = consultorio
        self.duplicado = None

    def clean_cpf(self):
        cpf = super().clean_cpf()
        if cpf:
            existente = (
                Paciente.objects.filter(consultorio=self.consultorio, cpf=cpf, mesclado_em__isnull=True)
                .exclude(pk=self.instance.pk)
                .first()
            )
            if existente:
                self.duplicado = existente
                raise forms.ValidationError(f"Já existe um paciente com este CPF: {existente.nome}.")
        return cpf


class ResponsavelForm(_CpfTelefoneMixin, forms.ModelForm):
    cpf = forms.CharField(label="CPF", max_length=14, required=False)

    class Meta:
        model = ResponsavelLegal
        fields = ["nome", "cpf", "telefone", "email", "recebe_avisos", "pode_pagar"]


class PagadorForm(forms.ModelForm):
    cpf = forms.CharField(label="CPF", max_length=14)

    class Meta:
        model = Pagador
        fields = ["nome", "cpf"]

    def clean_cpf(self):
        cpf = somente_digitos(self.cleaned_data.get("cpf", ""))
        if not cpf_valido(cpf):
            raise forms.ValidationError("CPF inválido.")
        return cpf


class GrupoForm(forms.ModelForm):
    participantes = forms.ModelMultipleChoiceField(queryset=Paciente.objects.none(), label="Participantes")

    class Meta:
        model = GrupoAtendimento
        fields = ["tipo", "nome"]

    def __init__(self, *args, pacientes, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["participantes"].queryset = pacientes

    def clean_participantes(self):
        escolhidos = self.cleaned_data["participantes"]
        if len(escolhidos) < 2:
            raise forms.ValidationError("Escolha pelo menos dois participantes.")
        return escolhidos


class GrupoEdicaoForm(GrupoForm):
    class Meta(GrupoForm.Meta):
        fields = ["tipo", "nome", "ativo"]


class MesclarForm(forms.Form):
    origem = forms.ModelChoiceField(
        queryset=Paciente.objects.none(),
        label="Cadastro duplicado a incorporar",
        help_text="O histórico dele passa para este paciente e o cadastro duplicado fica arquivado.",
    )

    def __init__(self, *args, candidatos, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["origem"].queryset = candidatos
