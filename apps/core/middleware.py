import logging
import time
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse

from django.utils import timezone

from apps.contas.models import Perfil, SessaoDispositivo, Vinculo
from apps.plataforma.models import Consultorio

from .tenancy import definir_contexto, limpar_contexto

logger = logging.getLogger(__name__)

METODOS_SEGUROS = {"GET", "HEAD", "OPTIONS"}


class ContextoConsultorioMiddleware:
    """Resolve o consultorio ativo da sessao e o grava na conexao do banco para as politicas de RLS.

    O contexto e sempre limpo ao fim da requisicao, para nunca vazar para a proxima.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _marcar_uso(request):
        """Atualiza o ultimo uso do dispositivo no maximo a cada 5 minutos."""
        agora = timezone.now().timestamp()
        if agora - request.session.get("uso_marcado_em", 0) > 300:
            SessaoDispositivo.objects.filter(session_key=request.session.session_key).update(
                ultimo_uso=timezone.now()
            )
            request.session["uso_marcado_em"] = agora

    def __call__(self, request):
        request.consultorio = None
        request.vinculo = None
        timezone.deactivate()
        try:
            usuario = request.user
            if usuario.is_authenticated:
                definir_contexto(usuario_id=usuario.pk)
                consultorio_id = request.session.get("consultorio_id")
                if consultorio_id:
                    vinculo = (
                        Vinculo.objects.select_related("consultorio")
                        .filter(usuario=usuario, consultorio_id=consultorio_id, ativo=True)
                        .first()
                    )
                    if vinculo and vinculo.consultorio.status != Consultorio.Status.ENCERRADO:
                        request.vinculo = vinculo
                        request.consultorio = vinculo.consultorio
                        timezone.activate(ZoneInfo(vinculo.consultorio.fuso))
                        definir_contexto(consultorio_id=consultorio_id, usuario_id=usuario.pk)
                        self._marcar_uso(request)
                    else:
                        request.session.pop("consultorio_id", None)
            return self.get_response(request)
        finally:
            timezone.deactivate()
            try:
                limpar_contexto()
            except Exception:  # conexao ja invalida: o proximo uso abre outra, sem contexto
                logger.exception("Falha ao limpar o contexto de consultorio")


class SegundoFatorObrigatorioMiddleware:
    """Quem e profissional em algum consultorio precisa ter o 2FA configurado para usar o sistema."""

    LIVRES = ("/conta/2fa/", "/sair/", "/static/", "/admin/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = request.user
        if usuario.is_authenticated:
            if usuario.segundo_fator_ativo and not request.session.get("segundo_fator_ok"):
                logout(request)
                return redirect("contas:entrar")
            if not usuario.segundo_fator_ativo and not request.path.startswith(self.LIVRES):
                eh_profissional = Vinculo.objects.filter(
                    usuario=usuario, perfil=Perfil.PROFISSIONAL, ativo=True
                ).exists()
                if eh_profissional:
                    messages.warning(request, "Ative a verificação em duas etapas para continuar.")
                    return redirect("contas:configurar_2fa")
        return self.get_response(request)


class SomenteLeituraMiddleware:
    """Consultorio inadimplente apos a carencia: ve e exporta, nao cria nem altera."""

    LIVRES = ("/sair/", "/conta/", "/exportacao/", "/admin/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        consultorio = getattr(request, "consultorio", None)
        if (
            consultorio
            and consultorio.somente_leitura
            and request.method not in METODOS_SEGUROS
            and not request.path.startswith(self.LIVRES)
        ):
            return HttpResponseForbidden(
                "Este consultório está em modo somente leitura por pendência de pagamento. "
                "Você pode consultar e exportar os dados."
            )
        return self.get_response(request)


class InatividadeProntuarioMiddleware:
    """Na area de prontuario a sessao expira por inatividade mais cedo (padrao 15 min) que no resto do sistema."""

    AREA = "/prontuarios/"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            agora = time.time()
            ultimo = request.session.get("ultima_atividade")
            if request.path.startswith(self.AREA) and ultimo and agora - ultimo > settings.PSIQ_INATIVIDADE_PRONTUARIO_SEGUNDOS:
                SessaoDispositivo.objects.filter(session_key=request.session.session_key).update(revogada_em=timezone.now())
                logout(request)
                messages.warning(request, "Sua sessão expirou por inatividade. Entre novamente para abrir prontuários.")
                return redirect("contas:entrar")
            request.session["ultima_atividade"] = agora
        return self.get_response(request)
