import mimetypes
from functools import wraps

from django.contrib import messages
from django.core import signing
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from apps.agenda.models import Consulta
from apps.agenda.servico import profissionais_do_consultorio
from apps.auditoria import servico as auditoria
from apps.contas.models import Perfil, Usuario
from apps.core.permissoes import PERFIS_INTERNOS, perfil_requerido, segundo_fator_satisfeito
from apps.core.tenancy import contexto
from apps.pacientes.models import Paciente
from apps.pacientes.servico import pacientes_visiveis, profissional_da_requisicao

from . import acesso, documentos, servico
from .forms import (
    AnexoForm, ConsentimentoForm, DelegarForm, DocumentoForm, EscolherModeloForm, ExclusaoForm, LiberacaoForm,
    ModeloDocumentoForm, NovaVersaoForm, RegistroForm,
)
from .models import (
    Anexo, ConsentimentoPaciente, Documento, LiberacaoLeitura, ModeloDocumento, Prontuario, RegistroClinico, RegistroVersao,
)
from .servico import ErroProntuario


def area_de_prontuario(view):
    """Perfil interno + 2FA ativo (obrigatorio para quem acessa prontuario) + nada de cache no navegador."""

    @wraps(view)
    def com_2fa(request, *args, **kwargs):
        if not segundo_fator_satisfeito(request):
            messages.warning(request, "Ative a verificação em duas etapas para acessar prontuários.")
            return redirect("contas:configurar_2fa")
        return view(request, *args, **kwargs)

    return never_cache(perfil_requerido(*PERFIS_INTERNOS)(com_2fa))


def _prontuario(request, pk):
    prontuario = get_object_or_404(
        Prontuario.objects.select_related("paciente", "grupo", "profissional__usuario"),
        pk=pk, consultorio=request.consultorio, excluido_em__isnull=True,
    )
    return prontuario, acesso.exigir_leitura(request, prontuario)


def _resposta_arquivo(dados, nome):
    resposta = HttpResponse(dados, content_type=mimetypes.guess_type(nome)[0] or "application/octet-stream")
    resposta["Content-Disposition"] = content_disposition_header(True, nome)
    return resposta


def _consultas_do_prontuario(prontuario):
    filtro = {"paciente": prontuario.paciente} if prontuario.paciente_id else {"grupo": prontuario.grupo}
    return Consulta.objects.filter(consultorio=prontuario.consultorio, profissional=prontuario.profissional, **filtro)


# --------------------------------------------------------------------------- listas e abertura


@area_de_prontuario
def lista(request):
    proprios, liberados = acesso.prontuarios_acessiveis(request)
    return render(request, "prontuario/lista.html", {"proprios": proprios, "liberados": liberados})


@area_de_prontuario
@require_POST
def abrir(request, paciente_pk):
    profissional = profissional_da_requisicao(request)
    if profissional is None:
        raise Http404
    paciente = get_object_or_404(pacientes_visiveis(request), pk=paciente_pk)
    try:
        prontuario = servico.abrir_prontuario(request, profissional, paciente=paciente)
    except ErroProntuario as erro:
        messages.error(request, str(erro))
        return redirect("pacientes:detalhe", pk=paciente_pk)
    return redirect("prontuario:detalhe", pk=prontuario.pk)


@area_de_prontuario
def detalhe(request, pk):
    prontuario, papel = _prontuario(request, pk)
    auditoria.registrar(request, "prontuario_aberto", "prontuario", prontuario.pk, papel=papel)
    return render(request, "prontuario/detalhe.html", {
        "prontuario": prontuario, "papel": papel, "eh_dono": papel == acesso.DONO,
        "registros": prontuario.registros.prefetch_related("versoes"),
        "anexos": prontuario.anexos.all(), "documentos": prontuario.documentos.all(),
        "liberacoes": prontuario.liberacoes.filter(revogado_em__isnull=True).select_related("usuario"),
        "prazo": prontuario.prazo_guarda_ate,
    })


@area_de_prontuario
def consolidado(request, paciente_pk):
    """Linha do tempo unica com os registros de todos os prontuarios do paciente que o usuario pode ler."""
    paciente = get_object_or_404(pacientes_visiveis(request), pk=paciente_pk)
    acessiveis = acesso.prontuarios_do_paciente(request, paciente)
    if not acessiveis:
        raise Http404
    linha = []
    for prontuario, papel in acessiveis:
        registros = prontuario.registros.select_related("consulta").prefetch_related("versoes")
        for reg in registros:
            versao = reg.versao_atual()
            conteudo, cid = servico.ler_versao(request, versao, auditar=False)
            linha.append({
                "prontuario": prontuario, "registro": reg, "versao": versao, "conteudo": conteudo, "cid": cid,
                "versoes": len(reg.versoes.all()),
            })
        auditoria.registrar(request, "prontuario_consolidado_lido", "prontuario", prontuario.pk, papel=papel, registros=len(registros))
    linha.sort(key=lambda item: item["registro"].criado_em, reverse=True)
    return render(request, "prontuario/consolidado.html", {
        "paciente": paciente, "acessiveis": acessiveis, "linha": linha,
        "anexos": [(p, p.anexos.all()) for p, _ in acessiveis],
        "documentos": [(p, p.documentos.all()) for p, _ in acessiveis],
    })


# --------------------------------------------------------------------------- registros


@area_de_prontuario
def registro_novo(request, pk):
    prontuario, _ = _prontuario(request, pk)
    acesso.exigir_dono(request, prontuario)
    form = RegistroForm(request.POST or None, consultas=_consultas_do_prontuario(prontuario))
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            registro = servico.criar_registro(request, prontuario, tipo=d["tipo"], conteudo=d["conteudo"], cid=d["cid"], consulta=d["consulta"])
        except ErroProntuario as erro:
            form.add_error(None, str(erro))
        else:
            messages.success(request, "Registro salvo.")
            return redirect("prontuario:registro", pk=pk, registro_pk=registro.pk)
    return render(request, "prontuario/registro_form.html", {"form": form, "prontuario": prontuario, "titulo": "Novo registro"})


def _registro(prontuario, registro_pk):
    return get_object_or_404(RegistroClinico, pk=registro_pk, prontuario=prontuario)


@area_de_prontuario
def registro(request, pk, registro_pk):
    prontuario, papel = _prontuario(request, pk)
    reg = _registro(prontuario, registro_pk)
    versao = reg.versao_atual()
    conteudo, cid = servico.ler_versao(request, versao)
    return render(request, "prontuario/registro.html", {
        "prontuario": prontuario, "registro": reg, "versao": versao, "conteudo": conteudo, "cid": cid,
        "versoes": reg.versoes.order_by("-numero"), "eh_dono": papel == acesso.DONO,
    })


@area_de_prontuario
def registro_versao(request, pk, registro_pk, numero):
    prontuario, _ = _prontuario(request, pk)
    reg = _registro(prontuario, registro_pk)
    versao = get_object_or_404(RegistroVersao, registro=reg, numero=numero)
    conteudo, cid = servico.ler_versao(request, versao)
    return render(request, "prontuario/registro.html", {
        "prontuario": prontuario, "registro": reg, "versao": versao, "conteudo": conteudo, "cid": cid,
        "versoes": reg.versoes.order_by("-numero"), "eh_dono": False, "versao_antiga": True,
    })


@area_de_prontuario
def registro_editar(request, pk, registro_pk):
    prontuario, _ = _prontuario(request, pk)
    acesso.exigir_dono(request, prontuario)
    reg = _registro(prontuario, registro_pk)
    if request.method == "POST":
        form = NovaVersaoForm(request.POST)
        if form.is_valid():
            try:
                servico.nova_versao(request, reg, conteudo=form.cleaned_data["conteudo"], cid=form.cleaned_data["cid"])
            except ErroProntuario as erro:
                form.add_error(None, str(erro))
            else:
                messages.success(request, "Nova versão salva. A anterior continua consultável.")
                return redirect("prontuario:registro", pk=pk, registro_pk=registro_pk)
    else:
        conteudo, cid = servico.ler_versao(request, reg.versao_atual())
        form = NovaVersaoForm(initial={"conteudo": conteudo, "cid": cid})
    return render(request, "prontuario/registro_form.html", {"form": form, "prontuario": prontuario, "titulo": "Editar registro (cria nova versão)"})


# --------------------------------------------------------------------------- anexos e documentos


@area_de_prontuario
def anexo_enviar(request, pk):
    prontuario, _ = _prontuario(request, pk)
    acesso.exigir_dono(request, prontuario)
    form = AnexoForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            servico.anexar(request, prontuario, form.cleaned_data["arquivo"], importado_historico=form.cleaned_data["importado_historico"])
        except ErroProntuario as erro:
            form.add_error("arquivo", str(erro))
        else:
            messages.success(request, "Arquivo guardado, cifrado.")
            return redirect("prontuario:detalhe", pk=pk)
    return render(request, "prontuario/registro_form.html", {"form": form, "prontuario": prontuario, "titulo": "Enviar anexo", "multipart": True})


@area_de_prontuario
def anexo_baixar(request, pk, anexo_pk):
    prontuario, _ = _prontuario(request, pk)
    anexo = get_object_or_404(Anexo, pk=anexo_pk, prontuario=prontuario)
    try:
        dados = servico.baixar_anexo(request, anexo)
    except ErroProntuario as erro:
        messages.error(request, str(erro))
        return redirect("prontuario:detalhe", pk=pk)
    return _resposta_arquivo(dados, anexo.nome)


@area_de_prontuario
def documento_novo(request, pk):
    prontuario, _ = _prontuario(request, pk)
    acesso.exigir_dono(request, prontuario)
    if prontuario.paciente_id is None:
        messages.error(request, "Emita documentos pelo prontuário individual do paciente.")
        return redirect("prontuario:detalhe", pk=pk)
    modelos = servico.garantir_modelos_padrao(request.consultorio)
    escolha = EscolherModeloForm(request.GET or None, modelos=modelos, consultas=_consultas_do_prontuario(prontuario))
    inicial = {}
    if request.GET and escolha.is_valid():
        modelo, consulta = escolha.cleaned_data["modelo"], escolha.cleaned_data["consulta"]
        dados = documentos.valores(paciente=prontuario.paciente, profissional=prontuario.profissional, consultorio=request.consultorio, consulta=consulta)
        inicial = {"titulo": modelo.titulo, "texto": documentos.preencher(modelo.texto_base, dados)}
    form = DocumentoForm(request.POST or None, initial=inicial)
    if request.method == "POST" and form.is_valid():
        try:
            documento = servico.emitir_documento_clinico(request, prontuario, titulo=form.cleaned_data["titulo"], texto=form.cleaned_data["texto"])
        except ErroProntuario as erro:
            form.add_error(None, str(erro))
        else:
            messages.success(request, "Documento emitido e guardado no prontuário.")
            return redirect("prontuario:detalhe", pk=pk)
    return render(request, "prontuario/documento_form.html", {"escolha": escolha, "form": form, "prontuario": prontuario, "preenchido": bool(inicial)})


@area_de_prontuario
@require_POST
def documento_liberar(request, pk, documento_pk):
    prontuario, _ = _prontuario(request, pk)
    acesso.exigir_dono(request, prontuario)
    documento = get_object_or_404(Documento, pk=documento_pk, prontuario=prontuario)
    documento.liberado_ao_paciente = not documento.liberado_ao_paciente
    documento.save(update_fields=["liberado_ao_paciente", "atualizado_em"])
    auditoria.registrar(request, "documento_liberado" if documento.liberado_ao_paciente else "documento_retirado", "documento", documento.pk)
    return redirect("prontuario:detalhe", pk=pk)


@perfil_requerido(*PERFIS_INTERNOS)
@never_cache
def documento_baixar(request, documento_pk):
    """Documento clinico: exige leitura do prontuario (e 2FA). Sem conteudo clinico: qualquer perfil que ve o paciente."""
    documento = get_object_or_404(Documento, pk=documento_pk, consultorio=request.consultorio)
    if documento.chave_id:
        if not segundo_fator_satisfeito(request):
            return redirect("contas:configurar_2fa")
        acesso.exigir_leitura(request, documento.prontuario)
    elif not pacientes_visiveis(request).filter(pk=documento.paciente_id).exists():
        raise Http404
    pdf = servico.baixar_documento(request, documento)
    return _resposta_arquivo(pdf, f"{documento.titulo}.pdf")


@perfil_requerido(*PERFIS_INTERNOS)
@require_POST
def comparecimento(request, consulta_pk):
    consultas = Consulta.objects.filter(consultorio=request.consultorio)
    proprio = profissional_da_requisicao(request)
    if proprio:
        consultas = consultas.filter(profissional=proprio)
    consulta = get_object_or_404(consultas.select_related("paciente", "profissional__usuario"), pk=consulta_pk)
    try:
        documento = servico.emitir_comparecimento(request, consulta)
    except ErroProntuario as erro:
        messages.error(request, str(erro))
        return redirect("agenda:consulta", pk=consulta_pk)
    return redirect("prontuario:documento_baixar", documento_pk=documento.pk)


@perfil_requerido(Perfil.PROFISSIONAL, Perfil.ADMIN)
def modelos(request):
    return render(request, "prontuario/modelos.html", {"modelos": servico.garantir_modelos_padrao(request.consultorio)})


@perfil_requerido(Perfil.PROFISSIONAL, Perfil.ADMIN)
def modelo_editar(request, modelo_pk):
    modelo = get_object_or_404(ModeloDocumento, pk=modelo_pk, consultorio=request.consultorio)
    form = ModeloDocumentoForm(request.POST or None, instance=modelo)
    if request.method == "POST" and form.is_valid():
        form.save()
        auditoria.registrar(request, "modelo_documento_alterado", "modelo_documento", modelo.pk)
        return redirect("prontuario:modelos")
    return render(request, "prontuario/modelo_form.html", {"form": form, "modelo": modelo})


# --------------------------------------------------------------------------- liberacao, consentimento, exclusao


@area_de_prontuario
def liberacoes(request, pk):
    prontuario, _ = _prontuario(request, pk)
    acesso.exigir_dono(request, prontuario)
    usuarios = Usuario.objects.filter(vinculos__consultorio=request.consultorio, vinculos__ativo=True).exclude(pk=request.user.pk).distinct()
    form = LiberacaoForm(request.POST or None, usuarios=usuarios)
    if request.method == "POST" and form.is_valid():
        try:
            servico.conceder_liberacao(request, prontuario, form.cleaned_data["usuario"])
        except ErroProntuario as erro:
            messages.error(request, str(erro))
        else:
            messages.success(request, "Leitura liberada.")
        return redirect("prontuario:liberacoes", pk=pk)
    return render(request, "prontuario/liberacoes.html", {
        "prontuario": prontuario, "form": form,
        "liberacoes": prontuario.liberacoes.filter(revogado_em__isnull=True).select_related("usuario"),
    })


@area_de_prontuario
@require_POST
def liberacao_revogar(request, pk, liberacao_pk):
    prontuario, _ = _prontuario(request, pk)
    acesso.exigir_dono(request, prontuario)
    liberacao = get_object_or_404(LiberacaoLeitura, pk=liberacao_pk, prontuario=prontuario, revogado_em__isnull=True)
    servico.revogar_liberacao(request, liberacao)
    messages.success(request, "Liberação revogada.")
    return redirect("prontuario:liberacoes", pk=pk)


@area_de_prontuario
def consentimentos(request, pk):
    prontuario, _ = _prontuario(request, pk)
    acesso.exigir_dono(request, prontuario)
    profissionais = profissionais_do_consultorio(request.consultorio).exclude(pk=prontuario.profissional_id)
    form = ConsentimentoForm(request.POST or None, profissionais=profissionais)
    if request.method == "POST" and form.is_valid():
        try:
            servico.solicitar_consentimento(request, prontuario, form.cleaned_data["profissional"])
        except ErroProntuario as erro:
            messages.error(request, str(erro))
        else:
            messages.success(request, "Pedido enviado ao paciente por e-mail.")
        return redirect("prontuario:consentimentos", pk=pk)
    pacientes = acesso.pacientes_do_prontuario(prontuario)
    historico = ConsentimentoPaciente.objects.filter(
        paciente__in=pacientes, profissional_origem=prontuario.profissional
    ).select_related("paciente", "profissional_destino__usuario").order_by("-criado_em")
    return render(request, "prontuario/consentimentos.html", {"prontuario": prontuario, "form": form, "historico": historico})


@area_de_prontuario
def excluir(request, pk):
    prontuario, _ = _prontuario(request, pk)
    acesso.exigir_dono(request, prontuario)
    form = ExclusaoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            servico.excluir_antecipadamente(request, prontuario, motivo=form.cleaned_data["motivo"], confirmacao_nome=form.cleaned_data["confirmacao_nome"])
        except ErroProntuario as erro:
            form.add_error("confirmacao_nome", str(erro))
        else:
            messages.success(request, "Prontuário excluído. O fato ficou registrado na auditoria.")
            return redirect("prontuario:lista")
    return render(request, "prontuario/excluir.html", {"form": form, "prontuario": prontuario})


@perfil_requerido(Perfil.ADMIN)
def delegar(request):
    """O admin ve so quem e dono de que (metadados) e troca o dono. Nunca ve o conteudo."""
    prontuarios = Prontuario.objects.filter(consultorio=request.consultorio, excluido_em__isnull=True).select_related("paciente", "grupo", "profissional__usuario")
    form = DelegarForm(request.POST or None, prontuarios=prontuarios, profissionais=profissionais_do_consultorio(request.consultorio))
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            servico.delegar(request, d["prontuario"], d["novo_profissional"], d["motivo"])
        except ErroProntuario as erro:
            form.add_error(None, str(erro))
        else:
            messages.success(request, "Prontuário delegado. A troca ficou registrada.")
            return redirect("prontuario:delegar")
    return render(request, "prontuario/delegar.html", {"form": form, "prontuarios": prontuarios})


# --------------------------------------------------------------------------- aceite do paciente (publico)


@require_http_methods(["GET", "POST"])
def termo_publico(request, token):
    from .termos import TERMO_ATUAL, TERMOS

    try:
        dados = servico.ler_token_consentimento(token)
    except signing.BadSignature:
        return render(request, "agenda/acao_invalida.html", status=404)
    ip = request.META.get("REMOTE_ADDR") or None
    with contexto(consultorio_id=dados["k"]):
        consentimento = get_object_or_404(
            ConsentimentoPaciente.objects.select_related("profissional_origem__usuario", "profissional_destino__usuario", "consultorio"),
            pk=dados["n"],
        )
        resultado = None
        if request.method == "POST":
            try:
                if request.POST.get("acao") == "aceitar":
                    servico.aceitar_consentimento(consentimento, ip)
                    resultado = "Autorização registrada. Você pode revogá-la a qualquer momento por esta página."
                elif request.POST.get("acao") == "revogar":
                    servico.revogar_consentimento(consentimento, ip)
                    resultado = "Autorização revogada."
            except ErroProntuario as erro:
                resultado = str(erro)
            consentimento.refresh_from_db()
        return render(request, "prontuario/termo_publico.html", {
            "c": consentimento, "termo": TERMOS[consentimento.termo_versao or TERMO_ATUAL], "resultado": resultado,
        })
