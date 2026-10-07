from django import forms

from apps.pacientes.models import GrupoAtendimento, Paciente

from .models import TipoAtendimento

DATA_HORA = {"input_formats": ["%Y-%m-%dT%H:%M"], "widget": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")}


class ConsultaForm(forms.Form):
    profissional = forms.ModelChoiceField(queryset=None, label="Profissional")
    paciente = forms.ModelChoiceField(queryset=Paciente.objects.none(), required=False, label="Paciente")
    grupo = forms.ModelChoiceField(queryset=GrupoAtendimento.objects.none(), required=False, label="Ou grupo de atendimento")
    inicio = forms.DateTimeField(label="Data e hora", **DATA_HORA)
    duracao_minutos = forms.IntegerField(label="Duração (minutos)", min_value=10, max_value=480, initial=50)
    tipo = forms.ChoiceField(label="Tipo de atendimento", choices=TipoAtendimento.choices)
    repetir = forms.ChoiceField(
        label="Repetir",
        choices=[("0", "Não repetir"), ("1", "Toda semana"), ("2", "A cada 2 semanas")],
        initial="0",
    )
    repeticoes = forms.IntegerField(
        label="Número de sessões da série", min_value=2, max_value=52, required=False, initial=8,
        help_text="Conta a primeira. Cancelar uma sessão não apaga as outras.",
    )

    def __init__(self, *args, profissionais, pacientes, grupos, profissional_fixo=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["profissional"].queryset = profissionais
        self.fields["paciente"].queryset = pacientes
        self.fields["grupo"].queryset = grupos
        if profissional_fixo is not None:
            self.fields["profissional"].initial = profissional_fixo
            self.fields["profissional"].queryset = profissionais.filter(pk=profissional_fixo.pk)

    def clean(self):
        dados = super().clean()
        if bool(dados.get("paciente")) == bool(dados.get("grupo")):
            raise forms.ValidationError("Escolha um paciente ou um grupo de atendimento (apenas um dos dois).")
        if dados.get("repetir") != "0" and not dados.get("repeticoes"):
            self.add_error("repeticoes", "Informe quantas sessões a série terá.")
        return dados


class RemarcarForm(forms.Form):
    inicio = forms.DateTimeField(label="Novo horário", **DATA_HORA)


class SolicitacaoForm(forms.Form):
    paciente = forms.ModelChoiceField(queryset=Paciente.objects.none(), label="Paciente")
    profissional = forms.ModelChoiceField(queryset=None, label="Profissional")
    horario = forms.DateTimeField(label="Horário desejado", **DATA_HORA)
    tipo = forms.ChoiceField(label="Tipo de atendimento", choices=TipoAtendimento.choices)
    observacao = forms.CharField(
        label="Observação", max_length=300, required=False, help_text="Não registre informação clínica aqui."
    )

    def __init__(self, *args, profissionais, pacientes, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["profissional"].queryset = profissionais
        self.fields["paciente"].queryset = pacientes
