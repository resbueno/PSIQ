from django import forms

from apps.contas.models import Profissional

from .models import CobrancaRecorrente, Convenio, Lancamento, RegraRepasse, TabelaValor


class _ComProfissionais:
    """Limita o campo profissional aos profissionais ativos do consultorio."""

    def __init__(self, *args, profissionais=None, **kwargs):
        super().__init__(*args, **kwargs)
        if profissionais is not None and "profissional" in self.fields:
            self.fields["profissional"].queryset = profissionais
            self.fields["profissional"].label_from_instance = lambda p: p.usuario.nome


class ConvenioForm(forms.ModelForm):
    class Meta:
        model = Convenio
        fields = ["operadora", "ativo"]

    def __init__(self, *args, profissionais=None, **kwargs):
        super().__init__(*args, **kwargs)


class TabelaValorForm(_ComProfissionais, forms.ModelForm):
    class Meta:
        model = TabelaValor
        fields = ["profissional", "convenio", "tipo", "valor"]
        help_texts = {"convenio": "Deixe vazio para o valor particular."}

    def __init__(self, *args, consultorio=None, **kwargs):
        super().__init__(*args, **kwargs)
        if consultorio is not None:
            self.fields["convenio"].queryset = Convenio.objects.filter(consultorio=consultorio, ativo=True)
        self.fields["convenio"].required = False


class RegraRepasseForm(_ComProfissionais, forms.ModelForm):
    class Meta:
        model = RegraRepasse
        fields = ["profissional", "aplicacao", "percentual", "valor_fixo"]
        help_texts = {"percentual": "Preencha o percentual OU o valor fixo."}

    def __init__(self, *args, consultorio=None, **kwargs):
        super().__init__(*args, **kwargs)

    def clean(self):
        dados = super().clean()
        if (dados.get("percentual") is None) == (dados.get("valor_fixo") is None):
            raise forms.ValidationError("Preencha o percentual ou o valor fixo (apenas um dos dois).")
        percentual = dados.get("percentual")
        if percentual is not None and not (0 <= percentual <= 100):
            self.add_error("percentual", "O percentual fica entre 0 e 100.")
        return dados


class CobrancaRecorrenteForm(_ComProfissionais, forms.ModelForm):
    class Meta:
        model = CobrancaRecorrente
        fields = ["profissional", "descricao", "valor", "dia_vencimento", "ativa"]

    def __init__(self, *args, consultorio=None, **kwargs):
        super().__init__(*args, **kwargs)

    def clean_dia_vencimento(self):
        dia = self.cleaned_data["dia_vencimento"]
        if not 1 <= dia <= 28:
            raise forms.ValidationError("Use um dia entre 1 e 28.")
        return dia


class PagamentoForm(forms.Form):
    forma_pagamento = forms.ChoiceField(label="Forma de pagamento", choices=Lancamento.Forma.choices)


class NotaExternaForm(forms.Form):
    numero_nota = forms.CharField(label="Número da nota fiscal emitida fora do PSIQ", max_length=40)


class LancamentoManualForm(_ComProfissionais, forms.Form):
    paciente = forms.ModelChoiceField(queryset=None, label="Paciente")
    profissional = forms.ModelChoiceField(queryset=Profissional.objects.none(), label="Profissional")
    descricao = forms.CharField(label="Descrição", max_length=160, required=False)
    valor = forms.DecimalField(label="Valor (R$)", max_digits=10, decimal_places=2, min_value=0)
    vencimento = forms.DateField(label="Vencimento", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))

    def __init__(self, *args, pacientes, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["paciente"].queryset = pacientes


class ValorConsultaForm(forms.Form):
    valor = forms.DecimalField(label="Valor da sessão (R$)", max_digits=10, decimal_places=2, min_value=0)


class FaturarForm(forms.Form):
    demonstrativo = forms.CharField(label="Lote ou demonstrativo", max_length=60, required=False)


class RetornoForm(forms.Form):
    valor_recebido = forms.DecimalField(label="Valor recebido da operadora (R$)", max_digits=10, decimal_places=2, min_value=0)
    motivo = forms.CharField(label="Motivo da glosa (se houve)", max_length=200, required=False)
    demonstrativo = forms.CharField(label="Demonstrativo", max_length=60, required=False)
