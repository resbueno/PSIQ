import io
from datetime import timedelta

import pyotp
import qrcode
import qrcode.image.svg
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.sessions.backends.db import SessionStore
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.auditoria import servico as auditoria
from apps.auditoria.models import Auditoria
from apps.core.permissoes import perfil_requerido
from apps.core.tenancy import contexto
from apps.plataforma.models import Consultorio

from .forms import CodigoForm, EntrarForm, NovoUsuarioForm
from . import totp
from .models import Perfil, Profissional, SessaoDispositivo, Usuario, Vinculo

def _utilizavel():
    """Consultorios ativos, e os encerrados ainda dentro dos 90 dias de exportacao."""
    return Q(~Q(consultorio__status="encerrado") | Q(consultorio__encerrado_em__gte=timezone.now() - timedelta(days=90)))


PRE_2FA = "pre_2fa_usuario_id"
PRE_2FA_CONSULTORIO = "pre_2fa_consultorio_id"
MENSAGEM_LOGIN_INVALIDO = "E-mail ou senha incorretos, ou acesso temporariamente bloqueado."


# --------------------------------------------------------------------------- login


def _consultorio_do_slug(slug):
    return get_object_or_404(Consultorio.objects.exclude(status=Consultorio.Status.ENCERRADO), slug=slug)


def entrar(request, slug=None):
    consultorio = _consultorio_do_slug(slug) if slug else None
    if request.user.is_authenticated:
        return redirect("painel")
    form = EntrarForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        usuario = Usuario.objects.filter(email=email).first()

        if usuario and usuario.bloqueado:
            auditoria.registrar(request, "login_bloqueado", "usuario", usuario.pk, usuario=usuario)
            messages.error(request, MENSAGEM_LOGIN_INVALIDO)
            return render(request, "contas/entrar.html", {"form": form, "consultorio": consultorio}, status=429)

        autenticado = authenticate(request, username=email, password=form.cleaned_data["senha"])
        if autenticado is None:
            if usuario:
                usuario.registrar_falha_login()
            auditoria.registrar(
                request, "login_falha", "usuario", usuario.pk if usuario else "", usuario=usuario, email=email
            )
            messages.error(request, MENSAGEM_LOGIN_INVALIDO)
            return render(request, "contas/entrar.html", {"form": form, "consultorio": consultorio}, status=401)

        if consultorio is not None and not _tem_vinculo_no_consultorio(autenticado, consultorio):
            # Credenciais validas, mas sem acesso a ESTE consultorio: mesma mensagem, para nao revelar qual parte falhou.
            auditoria.registrar(
                request, "login_consultorio_incorreto", "usuario", autenticado.pk, usuario=autenticado,
                consultorio_id=consultorio.pk,
            )
            messages.error(request, MENSAGEM_LOGIN_INVALIDO)
            return render(request, "contas/entrar.html", {"form": form, "consultorio": consultorio}, status=401)

        if autenticado.segundo_fator_ativo:
            request.session.flush()
            request.session[PRE_2FA] = str(autenticado.pk)
            request.session["pre_2fa_em"] = timezone.now().timestamp()
            if consultorio is not None:
                request.session[PRE_2FA_CONSULTORIO] = str(consultorio.pk)
            return redirect("contas:verificar_2fa")
        return _concluir_login(request, autenticado, segundo_fator=False, consultorio_alvo=consultorio)
    return render(request, "contas/entrar.html", {"form": form, "consultorio": consultorio})


def _tem_vinculo_no_consultorio(usuario, consultorio):
    with contexto(usuario_id=usuario.pk):
        return Vinculo.objects.filter(usuario=usuario, consultorio=consultorio, ativo=True).filter(_utilizavel()).exists()


def verificar_2fa(request):
    usuario_id = request.session.get(PRE_2FA)
    emitido_em = request.session.get("pre_2fa_em", 0)
    if not usuario_id or timezone.now().timestamp() - emitido_em > 300:
        request.session.flush()
        return redirect("contas:entrar")
    usuario = get_object_or_404(Usuario, pk=usuario_id, is_active=True)
    consultorio_alvo_id = request.session.get(PRE_2FA_CONSULTORIO)

    form = CodigoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if usuario.bloqueado:
            messages.error(request, "Acesso temporariamente bloqueado. Tente mais tarde.")
            return render(request, "contas/verificar_2fa.html", {"form": form}, status=429)
        if totp.consumir(usuario, form.codigo_limpo()):  # cada codigo vale uma unica vez
            request.session.flush()
            consultorio_alvo = Consultorio.objects.filter(pk=consultorio_alvo_id).first() if consultorio_alvo_id else None
            return _concluir_login(request, usuario, segundo_fator=True, consultorio_alvo=consultorio_alvo)
        usuario.registrar_falha_login()
        auditoria.registrar(request, "2fa_falha", "usuario", usuario.pk, usuario=usuario)
        form.add_error("codigo", "Código incorreto ou expirado.")
    return render(request, "contas/verificar_2fa.html", {"form": form})


def _concluir_login(request, usuario, segundo_fator, consultorio_alvo=None):
    usuario.registrar_sucesso_login()
    login(request, usuario, backend="django.contrib.auth.backends.ModelBackend")
    if segundo_fator:
        request.session["segundo_fator_ok"] = True
    request.session.save()
    SessaoDispositivo.objects.create(
        usuario=usuario,
        session_key=request.session.session_key,
        dispositivo=request.META.get("HTTP_USER_AGENT", "")[:200],
        ip=request.META.get("REMOTE_ADDR") or None,
    )

    with contexto(usuario_id=usuario.pk):
        vinculos = list(Vinculo.objects.filter(usuario=usuario, ativo=True).filter(_utilizavel()))

    if consultorio_alvo is not None:
        escolhido = next((v for v in vinculos if v.consultorio_id == consultorio_alvo.pk), None)
        if escolhido is None:
            # Vinculo removido entre a senha e o 2FA: nao deixa entrar em outro consultorio por engano.
            logout(request)
            messages.error(request, "Sua conta não tem mais acesso a este consultório.")
            return redirect("contas:entrar")
        request.session["consultorio_id"] = str(escolhido.consultorio_id)
        with contexto(consultorio_id=escolhido.consultorio_id, usuario_id=usuario.pk):
            auditoria.registrar(request, "login", "usuario", usuario.pk, usuario=usuario, consultorio_id=escolhido.consultorio_id)
        return redirect("painel")

    if len(vinculos) == 1:
        consultorio_id = vinculos[0].consultorio_id
        request.session["consultorio_id"] = str(consultorio_id)
        with contexto(consultorio_id=consultorio_id, usuario_id=usuario.pk):
            auditoria.registrar(request, "login", "usuario", usuario.pk, usuario=usuario, consultorio_id=consultorio_id)
        return redirect("painel")

    auditoria.registrar(request, "login", "usuario", usuario.pk, usuario=usuario, consultorio_id=None)
    if not vinculos and usuario.is_staff:
        return redirect("operador:painel")
    return redirect("contas:escolher_consultorio")


@require_POST
def sair(request):
    if request.user.is_authenticated:
        SessaoDispositivo.objects.filter(session_key=request.session.session_key).update(revogada_em=timezone.now())
        auditoria.registrar(request, "logout", "usuario", request.user.pk)
    logout(request)
    return redirect("contas:entrar")


@login_required
def escolher_consultorio(request):
    vinculos = list(
        Vinculo.objects.select_related("consultorio")
        .filter(usuario=request.user, ativo=True)
        .filter(_utilizavel())
        .order_by("consultorio__nome")
    )
    if request.method == "POST":
        escolhido = next((v for v in vinculos if str(v.consultorio_id) == request.POST.get("consultorio_id")), None)
        if escolhido:
            request.session["consultorio_id"] = str(escolhido.consultorio_id)
            with contexto(consultorio_id=escolhido.consultorio_id, usuario_id=request.user.pk):
                auditoria.registrar(
                    request, "consultorio_selecionado", "consultorio", escolhido.consultorio_id,
                    consultorio_id=escolhido.consultorio_id,
                )
            return redirect("painel")
        messages.error(request, "Escolha um consultório da lista.")
    return render(request, "contas/escolher_consultorio.html", {"vinculos": vinculos})


# --------------------------------------------------------------------------- 2FA


def _qr_svg(uri):
    imagem = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8)
    buffer = io.BytesIO()
    imagem.save(buffer)
    svg = buffer.getvalue().decode()
    return svg[svg.index("<svg"):]


@login_required
def configurar_2fa(request):
    usuario = request.user
    if usuario.segundo_fator_ativo:
        messages.info(request, "A verificação em duas etapas já está ativa nesta conta.")
        return redirect("painel")

    segredo = request.session.get("novo_segredo_2fa")
    if not segredo:
        segredo = pyotp.random_base32()
        request.session["novo_segredo_2fa"] = segredo

    form = CodigoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        passo = totp.passo_valido(segredo, form.codigo_limpo())
        if passo is not None:
            usuario.definir_segredo_2fa(segredo)
            usuario.segundo_fator_ativo = True
            usuario.ultimo_passo_totp = passo  # o codigo usado na ativacao nao serve para entrar de novo
            usuario.save(update_fields=["segundo_fator_segredo_cifrado", "segundo_fator_ativo", "ultimo_passo_totp", "atualizado_em"])
            request.session.pop("novo_segredo_2fa", None)
            request.session["segundo_fator_ok"] = True
            auditoria.registrar(request, "2fa_ativado", "usuario", usuario.pk)
            messages.success(request, "Verificação em duas etapas ativada.")
            return redirect("painel")
        form.add_error("codigo", "Código incorreto. Confira o horário do celular e tente de novo.")

    uri = pyotp.TOTP(segredo).provisioning_uri(name=usuario.email, issuer_name=settings.MEUPSIQ_NOME_EMISSOR_2FA)
    return render(request, "contas/configurar_2fa.html", {"form": form, "segredo": segredo, "qr_svg": _qr_svg(uri)})


# --------------------------------------------------------------------------- dispositivos


@login_required
def dispositivos(request):
    sessoes = SessaoDispositivo.objects.filter(usuario=request.user, revogada_em__isnull=True)
    return render(
        request,
        "contas/dispositivos.html",
        {"sessoes": sessoes, "sessao_atual": request.session.session_key},
    )


@login_required
@require_POST
def revogar_dispositivo(request, pk):
    sessao = get_object_or_404(SessaoDispositivo, pk=pk, usuario=request.user, revogada_em__isnull=True)
    era_a_atual = sessao.session_key == request.session.session_key
    SessionStore(session_key=sessao.session_key).delete()
    sessao.revogada_em = timezone.now()
    sessao.save(update_fields=["revogada_em", "atualizado_em"])
    auditoria.registrar(request, "dispositivo_revogado", "sessao_dispositivo", sessao.pk)
    if era_a_atual:
        return redirect("contas:entrar")
    messages.success(request, "Dispositivo desconectado.")
    return redirect("contas:dispositivos")


# --------------------------------------------------------------------------- usuarios (admin)


@perfil_requerido(Perfil.ADMIN)
def usuarios(request):
    # A politica de RLS tambem deixa o usuario ver os proprios vinculos de outros consultorios; filtrar aqui.
    vinculos = (
        Vinculo.objects.filter(consultorio=request.consultorio)
        .select_related("usuario", "usuario__profissional")
        .order_by("usuario__nome")
    )
    return render(request, "contas/usuarios.html", {"vinculos": vinculos})


@perfil_requerido(Perfil.ADMIN)
def novo_usuario(request):
    form = NovoUsuarioForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        dados = form.cleaned_data
        consultorio = request.consultorio

        if dados["perfil"] == Perfil.PROFISSIONAL and consultorio.plano_id:
            ativos = Vinculo.objects.filter(consultorio=consultorio, perfil=Perfil.PROFISSIONAL, ativo=True).count()
            if ativos >= consultorio.plano.limite_profissionais:
                form.add_error(None, "O plano do consultório atingiu o limite de profissionais. Mude de plano para continuar.")
                return render(request, "contas/novo_usuario.html", {"form": form})

        with transaction.atomic():
            usuario = form.usuario_existente
            if usuario is None:
                usuario = Usuario.objects.create_user(dados["email"], dados["senha"], nome=dados["nome"])
            if dados["perfil"] == Perfil.PROFISSIONAL and not Profissional.objects.filter(usuario=usuario).exists():
                conselho = "CRM" if dados["tipo"] == Profissional.Tipo.MEDICO else "CRP"
                Profissional.objects.create(
                    usuario=usuario, tipo=dados["tipo"], conselho=conselho, numero=dados["numero"], uf=dados["uf"]
                )
            vinculo, criado = Vinculo.objects.get_or_create(
                usuario=usuario, consultorio=consultorio, defaults={"perfil": dados["perfil"]}
            )
            if not criado:
                vinculo.perfil, vinculo.ativo = dados["perfil"], True
                vinculo.save(update_fields=["perfil", "ativo", "atualizado_em"])
            auditoria.registrar(request, "vinculo_criado", "vinculo", vinculo.pk, perfil=dados["perfil"])
        messages.success(request, "Usuário adicionado ao consultório.")
        return redirect("contas:usuarios")
    return render(request, "contas/novo_usuario.html", {"form": form})


@perfil_requerido(Perfil.ADMIN)
@require_POST
def alternar_vinculo(request, pk):
    vinculo = get_object_or_404(Vinculo, pk=pk, consultorio=request.consultorio)
    if vinculo.usuario_id == request.user.pk:
        messages.error(request, "Você não pode desativar o próprio acesso.")
        return redirect("contas:usuarios")
    vinculo.ativo = not vinculo.ativo
    vinculo.save(update_fields=["ativo", "atualizado_em"])
    auditoria.registrar(request, "vinculo_ativado" if vinculo.ativo else "vinculo_desativado", "vinculo", vinculo.pk)
    return redirect("contas:usuarios")


@perfil_requerido(Perfil.ADMIN)
def auditoria_lista(request):
    eventos = Auditoria.objects.all()[:200]
    return render(request, "contas/auditoria.html", {"eventos": eventos})
