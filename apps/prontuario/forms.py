from django import forms

from .models import ModeloDocumento, RegistroClinico


class RegistroForm(forms.Form):
    tipo = forms.ChoiceField(label="Tipo de registro", choices=RegistroClinico.Tipo.choices)
    conteudo = forms.CharField(label="Registro", widget=forms.Textarea(attrs={"rows": 14}))
    cid = forms.CharField(label="CID (opcional)", max_length=20, required=False)
    consulta = forms.ModelChoiceField(queryset=None, required=False, label="Consulta relacionada (opcional)")

    def __init__(self, *args, consultas, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["consulta"].queryset = consultas


class NovaVersaoForm(forms.Form):
    conteudo = forms.CharField(label="Registro", widget=forms.Textarea(attrs={"rows": 14}))
    cid = forms.CharField(label="CID (opcional)", max_length=20, required=False)


class AnexoForm(forms.Form):
    arquivo = forms.FileField(label="Arquivo")
    importado_historico = forms.BooleanField(label="É um prontuário antigo (histórico importado)", required=False)


class DocumentoForm(forms.Form):
    titulo = forms.CharField(label="Título", max_length=160)
    texto = forms.CharField(label="Texto do documento", widget=forms.Textarea(attrs={"rows": 12}))


class EscolherModeloForm(forms.Form):
    modelo = forms.ModelChoiceField(queryset=ModeloDocumento.objects.none(), label="Modelo")
    consulta = forms.ModelChoiceField(queryset=None, required=False, label="Consulta (para preencher data e horário)")

    def __init__(self, *args, modelos, consultas, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["modelo"].queryset = modelos
        self.fields["consulta"].queryset = consultas


class ModeloDocumentoForm(forms.ModelForm):
    texto_base = forms.CharField(
        label="Texto do modelo", widget=forms.Textarea(attrs={"rows": 10}),
        help_text="Marcadores disponíveis: " + ", ".join("{{%s}}" % k for k in
                  ("paciente_nome", "paciente_cpf", "data_consulta", "hora_inicio", "hora_fim", "profissional_nome",
                   "registro_profissional", "consultorio_nome", "data_hoje")),
    )

    class Meta:
        model = ModeloDocumento
        fields = ["titulo", "texto_base"]


class LiberacaoForm(forms.Form):
    usuario = forms.ModelChoiceField(queryset=None, label="Liberar leitura para")

    def __init__(self, *args, usuarios, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["usuario"].queryset = usuarios
        self.fields["usuario"].label_from_instance = lambda u: f"{u.nome} ({u.email})"


class ConsentimentoForm(forms.Form):
    profissional = forms.ModelChoiceField(queryset=None, label="Profissional que vai ler")

    def __init__(self, *args, profissionais, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["profissional"].queryset = profissionais
        self.fields["profissional"].label_from_instance = lambda p: f"{p.usuario.nome} ({p.conselho} {p.numero}/{p.uf})"


class ExclusaoForm(forms.Form):
    confirmacao_nome = forms.CharField(label="Digite o nome do paciente para confirmar", max_length=160)
    motivo = forms.CharField(label="Motivo (pedido do paciente)", max_length=300, widget=forms.Textarea(attrs={"rows": 3}))


class DelegarForm(forms.Form):
    prontuario = forms.ModelChoiceField(queryset=None, label="Prontuário")
    novo_profissional = forms.ModelChoiceField(queryset=None, label="Novo dono")
    motivo = forms.CharField(label="Motivo", max_length=300, required=False)

    def __init__(self, *args, prontuarios, profissionais, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["prontuario"].queryset = prontuarios
        self.fields["prontuario"].label_from_instance = lambda p: f"{p.paciente or p.grupo} - dono: {p.profissional.usuario.nome}"
        self.fields["novo_profissional"].queryset = profissionais
        self.fields["novo_profissional"].label_from_instance = lambda p: p.usuario.nome
