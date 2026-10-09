"""Cria um consultorio FICTICIO completo para demonstracao e testes manuais (homologacao). Nunca use em producao.

Papeis criados: administrador, psicologa, psiquiatra, assistente, operador da plataforma e pacientes do portal
(adulto, crianca com responsavel). Tudo com e-mails @<dominio> inexistentes e senhas aleatorias.
Idempotente: se o consultorio demo ja existir, nao faz nada.
Com --consultorio <slug>, popula um consultorio JA existente (ex.: clinica-homologacao), reaproveitando seu administrador;
nesse caso nao faz nada se ele ja tiver pacientes."""

import secrets
from datetime import date, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pyotp
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.test import RequestFactory
from django.test.utils import override_settings
from django.utils import timezone

from apps.agenda import servico as agenda
from apps.agenda.models import AgendaRegra, Consulta
from apps.contas.models import Perfil, Profissional, Usuario, Vinculo
from apps.core.tenancy import contexto
from apps.financeiro import servico as financeiro
from apps.financeiro.models import AtendimentoConvenio, CobrancaRecorrente, Convenio, Lancamento, RegraRepasse, TabelaValor
from apps.operador import servico as operador
from apps.pacientes.models import GrupoAtendimento, Paciente, ParticipanteGrupo, ResponsavelLegal
from apps.plataforma.models import Consultorio, PagamentoPlataforma, Plano
from apps.prontuario import servico as prontuario

from . import _demo_extras

SP = ZoneInfo("America/Sao_Paulo")
NOME_DEMO = "Clínica Demo MeuPSIQ"
D = Decimal


def _senha():
    return "Demo-" + secrets.token_urlsafe(12).replace("-", "x").replace("_", "y")


class Command(BaseCommand):
    help = "Cria um consultório fictício completo, com todos os papéis e dados de exemplo (apenas homologação)."

    def add_arguments(self, parser):
        parser.add_argument("--dominio", default="demo.meupsiq.local", help="Domínio fictício dos e-mails")
        parser.add_argument("--consultorio", default="", help="Slug de um consultório existente a popular (em vez de criar o demo)")
        parser.add_argument("--complementar", action="store_true", help="Com --consultorio: acrescenta dados de todos os módulos a um consultório já populado")
        parser.add_argument("--blocos", default="", help="Com --complementar: reexecuta só estes blocos (ex.: agenda,portal)")
        parser.add_argument("--forcar", action="store_true", help="Permite rodar com DEBUG desligado (homologação)")

    def handle(self, *args, **opcoes):
        if not settings.DEBUG and not opcoes["forcar"]:
            raise CommandError("Use --forcar para rodar fora do modo DEBUG. Isto é só para homologação, nunca produção.")
        existente = None
        if opcoes["consultorio"]:
            try:
                existente = Consultorio.objects.get(slug=opcoes["consultorio"])
            except Consultorio.DoesNotExist:
                raise CommandError(f"Consultório '{opcoes['consultorio']}' não encontrado.")
            if opcoes["complementar"]:
                return self._complementar(existente, opcoes["dominio"], [b for b in opcoes["blocos"].split(",") if b])
            with contexto(consultorio_id=existente.pk):
                ja_populado = Paciente.objects.exists()
            if ja_populado:
                self.stdout.write("O consultório já tem pacientes. Nada a fazer.")
                return
        elif Consultorio.objects.filter(nome=NOME_DEMO).exists():
            self.stdout.write("O consultório demo já existe. Nada a fazer.")
            return
        dominio = opcoes["dominio"]
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.dummy.EmailBackend"):  # sem e-mails na carga
            with transaction.atomic():
                credenciais, consultorio = self._criar(dominio, existente)
        self._imprimir(credenciais, consultorio)

    def _complementar(self, consultorio, dominio, blocos):
        with contexto(consultorio_id=consultorio.pk):
            if not blocos and _demo_extras.ja_populado(consultorio):
                self.stdout.write("Os dados complementares já existem. Nada a fazer.")
                return
            usuarios = {nome: Usuario.objects.get(email=f"{nome}@{dominio}") for nome in ("assistente", "psicologa", "psiquiatra")}
            admin = Vinculo.objects.filter(consultorio=consultorio, perfil=Perfil.ADMIN).select_related("usuario").first().usuario
            with override_settings(EMAIL_BACKEND="django.core.mail.backends.dummy.EmailBackend"):
                self._extras(consultorio, dominio, admin, usuarios["assistente"], usuarios["psicologa"], usuarios["psiquiatra"],
                             usuarios["psicologa"].profissional, usuarios["psiquiatra"].profissional, blocos)

    def _extras(self, consultorio, dominio, admin, assistente, psicologa, psiquiatra, prof_psi, prof_med, blocos=None):
        self.stdout.write("Dados complementares:")
        falhas = _demo_extras.popular_extras(
            consultorio, dominio, admin=admin, assistente=assistente, psicologa=psicologa, psiquiatra=psiquiatra,
            prof_psi=prof_psi, prof_med=prof_med, aviso=self.stdout.write, blocos=blocos,
        )
        if falhas:
            self.stdout.write(self.style.WARNING(f"{len(falhas)} bloco(s) falharam (os demais foram gravados)."))

    # ------------------------------------------------------------------ montagem

    def _criar(self, dominio, existente=None):
        credenciais = []

        def usuario(email_local, nome, papel, *, perfil=None, staff=False, com_2fa=False):
            email, senha = f"{email_local}@{dominio}", _senha()
            u = Usuario.objects.create_user(email, senha, nome=nome, is_staff=staff)
            segredo = ""
            if com_2fa:
                segredo = pyotp.random_base32()
                u.definir_segredo_2fa(segredo)
                u.segundo_fator_ativo = True
                u.save()
            credenciais.append({"papel": papel, "email": email, "senha": senha, "segredo": segredo, "nome": nome})
            return u

        plano = Plano.objects.get_or_create(
            nome="Demonstração", defaults={"limite_pacientes_ativos": 50, "limite_profissionais": 5, "preco": D("199.00")}
        )[0]
        if existente:
            consultorio = existente
            if consultorio.plano_id is None:
                consultorio.plano = plano
                consultorio.save()
            with contexto(consultorio_id=consultorio.pk):
                admin = Vinculo.objects.filter(consultorio=consultorio, perfil=Perfil.ADMIN).select_related("usuario").first().usuario
            credenciais.append({"papel": "Administrador do consultório (já existente, senha inalterada)", "email": admin.email, "senha": "(a atual)", "segredo": "", "nome": admin.nome})
        else:
            consultorio = Consultorio.objects.create(
                nome=NOME_DEMO, slug="clinica-demo-meupsiq", documento="", plano=plano, cobra_falta_tardia=True
            )
            usuario("operador", "Atendente da Plataforma", "Operador da plataforma (equipe MeuPSIQ)", staff=True, com_2fa=True)
            admin = usuario("admin", "Beatriz Admin", "Administrador do consultório")
        assistente = usuario("assistente", "Carla Assistente", "Assistente (secretária)", com_2fa=True)
        psicologa = usuario("psicologa", "Dra. Helena Psicóloga", "Profissional: psicóloga (CRP)", com_2fa=True)
        psiquiatra = usuario("psiquiatra", "Dr. Marcos Psiquiatra", "Profissional: psiquiatra (CRM)", com_2fa=True)

        ahora = timezone.now()
        prof_psi = Profissional.objects.create(usuario=psicologa, tipo="psicologo", conselho="CRP", numero="06/65432" if existente else "06/54321", uf="SP", validado_em=ahora)
        prof_med = Profissional.objects.create(usuario=psiquiatra, tipo="medico", conselho="CRM", numero="654321" if existente else "123456", uf="SP", validado_em=ahora)

        with contexto(consultorio_id=consultorio.pk):
            for u, perfil in ((admin, Perfil.ADMIN), (assistente, Perfil.ASSISTENTE), (psicologa, Perfil.PROFISSIONAL), (psiquiatra, Perfil.PROFISSIONAL)):
                if existente and u is admin:
                    continue
                Vinculo.objects.create(usuario=u, consultorio=consultorio, perfil=perfil)
            self._dados(consultorio, dominio, credenciais, admin, assistente, psicologa, psiquiatra, prof_psi, prof_med)
        return credenciais, consultorio

    def _dados(self, consultorio, dominio, credenciais, admin, assistente, psicologa, psiquiatra, prof_psi, prof_med):
        def req(usuario):
            r = RequestFactory().get("/")
            r.user, r.consultorio, r.vinculo = usuario, consultorio, None
            return r

        r_assist, r_psi, r_med, r_admin = req(assistente), req(psicologa), req(psiquiatra), req(admin)

        def quando(dias, hora):
            return (timezone.now().astimezone(SP) + timedelta(days=dias)).replace(hour=hora, minute=0, second=0, microsecond=0)

        # agenda aberta e valores
        for prof in (prof_psi, prof_med):
            for dia in range(5):
                for inicio, fim in ((time(9), time(12)), (time(14), time(17))):
                    AgendaRegra.objects.create(consultorio=consultorio, profissional=prof, dia_semana=dia, hora_inicio=inicio, hora_fim=fim, duracao_minutos=50)
        unimed = Convenio.objects.create(consultorio=consultorio, operadora="Unimed")
        for prof, particular, primeira in ((prof_psi, "200.00", "250.00"), (prof_med, "400.00", "500.00")):
            TabelaValor.objects.create(consultorio=consultorio, profissional=prof, tipo="consulta", valor=D(particular))
            TabelaValor.objects.create(consultorio=consultorio, profissional=prof, tipo="primeira_consulta", valor=D(primeira))
        TabelaValor.objects.create(consultorio=consultorio, profissional=prof_med, convenio=unimed, tipo="consulta", valor=D("120.00"))
        RegraRepasse.objects.create(consultorio=consultorio, profissional=prof_psi, percentual=D("40"))
        RegraRepasse.objects.create(consultorio=consultorio, profissional=prof_med, percentual=D("50"))
        CobrancaRecorrente.objects.create(consultorio=consultorio, profissional=prof_med, descricao="Aluguel de sala", valor=D("800.00"), dia_vencimento=10)
        financeiro.gerar_cobrancas_do_mes(r_admin, consultorio, date.today())

        # pacientes (portal: entram pelo e-mail, com codigo)
        maria = Paciente.objects.create(consultorio=consultorio, nome="Maria Souza (demo)", cpf="52998224725", nascimento=date(1985, 3, 15), email=f"maria@{dominio}", telefone="11988887777")
        joao = Paciente.objects.create(consultorio=consultorio, nome="João Pereira (demo)", cpf="11144477735", nascimento=date(1979, 8, 2), email=f"joao@{dominio}", telefone="11977776666", convenio="Unimed", carteirinha="0012345")
        pedro = Paciente.objects.create(consultorio=consultorio, nome="Pedro Lima (demo, criança)", cpf="", nascimento=date.today().replace(year=date.today().year - 9), telefone="11966665555")
        ResponsavelLegal.objects.create(consultorio=consultorio, paciente=pedro, nome="Ana Lima (responsável)", cpf="12345678909", telefone="11966665555", email=f"responsavel@{dominio}", recebe_avisos=True, pode_pagar=True)
        luiza = Paciente.objects.create(consultorio=consultorio, nome="Luíza Silva (demo)", cpf="39053344705", nascimento=date(1990, 1, 20), email=f"luiza@{dominio}")
        rafael = Paciente.objects.create(consultorio=consultorio, nome="Rafael Silva (demo)", nascimento=date(1988, 6, 9), email=f"rafael@{dominio}")
        casal = GrupoAtendimento.objects.create(consultorio=consultorio, tipo="casal", nome="Casal Silva (demo)")
        for p in (luiza, rafael):
            ParticipanteGrupo.objects.create(consultorio=consultorio, grupo=casal, paciente=p)
        for conta, texto in (("maria", "Maria (adulta)"), ("responsavel", "Ana Lima (responsável do Pedro)"), ("joao", "João (convênio)"), ("luiza", "Luíza (casal)")):
            credenciais.append({"papel": f"Paciente no portal: {texto}", "email": f"{conta}@{dominio}", "senha": "(sem senha: código por e-mail)", "segredo": "", "nome": texto})

        def realizada(prof, paciente=None, grupo=None, dias=0, hora=10, tipo="presencial"):
            (c,) = agenda.agendar(r_assist, profissional=prof, inicio=quando(-dias, hora), duracao_minutos=50, tipo=tipo, paciente=paciente, grupo=grupo)
            c = Consulta.objects.select_related("consultorio", "paciente", "grupo", "profissional__usuario").get(pk=c.pk)
            agenda.marcar_realizada(r_assist, c)
            return Consulta.objects.select_related("consultorio", "paciente", "profissional__usuario").get(pk=c.pk)

        def futura(prof, paciente=None, grupo=None, dias=3, hora=10, tipo="presencial"):
            (c,) = agenda.agendar(r_assist, profissional=prof, inicio=quando(dias, hora), duracao_minutos=50, tipo=tipo, paciente=paciente, grupo=grupo)
            return c

        # Maria com a psicologa: 3 sessoes realizadas e 2 marcadas
        c1 = realizada(prof_psi, maria, dias=21)
        c2 = realizada(prof_psi, maria, dias=14, hora=11)
        realizada(prof_psi, maria, dias=7)
        futura(prof_psi, maria, dias=3)
        futura(prof_psi, maria, dias=10, tipo="online")
        # financeiro: 1a paga com recibo, 2a paga, 3a pendente (atrasada pelo vencimento)
        for consulta, pagar in ((c1, True), (c2, True)):
            l = Lancamento.objects.select_related("consultorio", "paciente", "pagador", "profissional__usuario", "consulta").get(consulta=consulta)
            financeiro.registrar_pagamento(r_assist, l, forma="pix")
            if consulta == c1:
                financeiro.emitir_recibo(r_assist, Lancamento.objects.select_related("consultorio", "paciente", "pagador", "profissional__usuario", "consulta").get(pk=l.pk))
        prontuario.emitir_comparecimento(r_assist, c1)

        # prontuario da Maria (psicologa): evolucoes, nova versao, documento liberado, leitura liberada a assistente
        pront = prontuario.abrir_prontuario(r_psi, prof_psi, paciente=maria)
        reg = prontuario.criar_registro(r_psi, pront, tipo="anamnese", conteudo="[DEMO] Anamnese fictícia: queixa de estresse no trabalho. Dados inventados para teste.", cid="F43.2")
        prontuario.criar_registro(r_psi, pront, tipo="evolucao", conteudo="[DEMO] Primeira sessão fictícia. Paciente colaborativa.")
        prontuario.nova_versao(r_psi, reg, conteudo="[DEMO] Anamnese fictícia (revisada): queixa de estresse e sono irregular. Dados inventados para teste.", cid="F43.2")
        doc = prontuario.emitir_documento_clinico(r_psi, pront, titulo="Declaração de acompanhamento", texto="Declaro que Maria Souza (demo) está em acompanhamento. Documento fictício.")
        doc.liberado_ao_paciente = True
        doc.save()
        prontuario.conceder_liberacao(r_psi, pront, assistente)

        # Joao com a psiquiatra, convenio: atendimento faturado com glosa parcial; falta; futura
        cj = realizada(prof_med, joao, dias=10)
        atendimento = AtendimentoConvenio.objects.select_related("lancamento__consultorio", "lancamento__profissional").get(lancamento__consulta=cj)
        financeiro.faturar(r_assist, [atendimento], "LOTE-DEMO-01")
        financeiro.registrar_retorno(r_assist, atendimento, valor_recebido=D("100.00"), motivo="Código do procedimento divergente (demo)")
        (cf,) = agenda.agendar(r_assist, profissional=prof_med, inicio=quando(-3, 15), duracao_minutos=50, tipo="presencial", paciente=joao)
        agenda.marcar_falta(r_assist, Consulta.objects.get(pk=cf.pk))
        futura(prof_med, joao, dias=5, hora=15)

        # Pedro (crianca) e casal
        realizada(prof_psi, pedro, dias=5, hora=14)
        futura(prof_psi, pedro, dias=6, hora=14)
        agenda.criar_solicitacao(r_assist, paciente=pedro, profissional=prof_psi, horario=quando(12, 9), tipo="presencial", observacao="Pedido da mãe (demo)")
        realizada(prof_psi, grupo=casal, dias=2, hora=16, tipo="online")
        futura(prof_psi, grupo=casal, dias=8, hora=16, tipo="online")

        # cobranca da plataforma ao consultorio (mes atual, vence em 10 dias)
        operador.criar_pagamento(consultorio, competencia=date.today(), valor=D("199.00"), vencimento=date.today() + timedelta(days=10))
        self._extras(consultorio, dominio, admin, assistente, psicologa, psiquiatra, prof_psi, prof_med)

    def _imprimir(self, credenciais, consultorio):
        base = settings.MEUPSIQ_URL_BASE.rstrip("/")
        self.stdout.write(self.style.SUCCESS(f"\nConsultório fictício criado: {consultorio.nome}"))
        self.stdout.write(f"Entrar (equipe): {base}/entrar/ ou {base}/{consultorio.slug}/")
        self.stdout.write(f"Agendamento público: {base}/{consultorio.slug}/agenda/")
        self.stdout.write(f"Portal do paciente: {base}/portal/{consultorio.pk}/entrar/\n")
        for c in credenciais:
            self.stdout.write(f"- {c['papel']}\n    e-mail: {c['email']}\n    senha:  {c['senha']}")
            if c["segredo"]:
                uri = pyotp.TOTP(c["segredo"]).provisioning_uri(name=c["email"], issuer_name="MeuPSIQ Demo")
                self.stdout.write(f"    2FA (chave do autenticador): {c['segredo']}\n    2FA (URI): {uri}")
        self.stdout.write("\nOs códigos do portal não saem por e-mail aqui (SMTP não configurado): leia nos logs do contêiner.")
