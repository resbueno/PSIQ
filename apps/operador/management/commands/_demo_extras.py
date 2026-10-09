"""Dados complementares do consultorio de demonstracao: cobrem os modulos que o basico do popular_demo nao toca
(series, repasses, convenios, anexos, consentimento, delegacao, importacao, calendario, LGPD, contratos...).
Cada bloco roda isolado: se um falhar, os demais seguem e o erro e listado no fim. Tudo fictício."""

from datetime import date, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import RequestFactory
from django.utils import timezone

from apps.agenda import servico as agenda
from apps.agenda.models import Consulta, SolicitacaoHorario
from apps.calendarios.models import ContaCalendario, EventoExterno
from apps.contas.models import Usuario
from apps.financeiro import servico as financeiro
from apps.financeiro.models import AtendimentoConvenio, CobrancaRecorrente, Convenio, Lancamento, Repasse, TabelaValor
from apps.importacao import servico as importacao
from apps.operador import servico as operador
from apps.pacientes import servico as pacientes_srv
from apps.pacientes.models import GrupoAtendimento, Pagador, Paciente
from apps.plataforma.models import Contrato, PagamentoPlataforma
from apps.portal import servico as portal
from apps.prontuario import servico as prontuario
from apps.prontuario.models import ModeloDocumento
from apps.relatorios import exportacao
from apps.relatorios.models import Exportacao

SP = ZoneInfo("America/Sao_Paulo")
D = Decimal
MARCADOR = "Helena Duarte (demo)"


def ja_populado(consultorio):
    return Paciente.objects.filter(consultorio=consultorio, nome=MARCADOR).exists()


def _cpf(base: int) -> str:
    """CPF valido a partir de 9 digitos."""
    digitos = [int(c) for c in f"{base:09d}"]
    for tamanho in (9, 10):
        soma = sum(d * p for d, p in zip(digitos, range(tamanho + 1, 1, -1)))
        digitos.append((soma * 10 % 11) % 10)
    return "".join(map(str, digitos))


def _quando(dias, hora, minuto=0):
    return (timezone.now().astimezone(SP) + timedelta(days=dias)).replace(hour=hora, minute=minuto, second=0, microsecond=0)


def popular_extras(consultorio, dominio, *, admin, assistente, psicologa, psiquiatra, prof_psi, prof_med, aviso, blocos=None):
    """`aviso(texto)` imprime progresso. Devolve a lista de blocos que falharam."""

    def req(usuario):
        r = RequestFactory().get("/")
        r.user, r.consultorio, r.vinculo = usuario, consultorio, None
        return r

    r_assist, r_psi, r_med, r_admin = req(assistente), req(psicologa), req(psiquiatra), req(admin)
    falhas = []
    estado = {}

    def bloco(nome):
        def decorador(fn):
            if blocos and nome.split()[0].rstrip(":") not in blocos:
                return fn
            try:
                with transaction.atomic():
                    fn()
                aviso(f"  ok: {nome}")
            except Exception as exc:  # noqa: BLE001 - o bloco e opcional; o resto da carga segue
                falhas.append(f"{nome}: {type(exc).__name__}: {exc}")
                aviso(f"  FALHOU: {nome}: {type(exc).__name__}: {exc}")
            return fn
        return decorador

    ocupados = set()

    def livre(prof, dias, hora):
        while (prof.pk, dias, hora) in ocupados:
            hora += 1
            if hora > 19:
                hora, dias = 8, dias - 1
        ocupados.add((prof.pk, dias, hora))
        return dias, hora

    def lanc(consulta):
        return Lancamento.objects.select_related("consultorio", "paciente", "pagador", "profissional__usuario", "consulta").get(consulta=consulta)

    def realizada(prof, paciente=None, grupo=None, dias=1, hora=9, tipo="presencial", req_=None):
        dias, hora = livre(prof, -dias, hora)
        (c,) = agenda.agendar(req_ or r_assist, profissional=prof, inicio=_quando(dias, hora), duracao_minutos=50, tipo=tipo, paciente=paciente, grupo=grupo)
        agenda.marcar_realizada(r_assist, Consulta.objects.select_related("consultorio", "paciente", "grupo", "profissional__usuario").get(pk=c.pk))
        return Consulta.objects.select_related("consultorio", "paciente", "profissional__usuario").get(pk=c.pk)

    def futura(prof, paciente=None, grupo=None, dias=4, hora=9, tipo="presencial"):
        dias, hora = livre(prof, dias, hora)
        return agenda.agendar(r_assist, profissional=prof, inicio=_quando(dias, hora), duracao_minutos=50, tipo=tipo, paciente=paciente, grupo=grupo)[0]

    if not consultorio.cobra_falta_tardia:
        consultorio.cobra_falta_tardia = True
        consultorio.save(update_fields=["cobra_falta_tardia", "atualizado_em"])

    # ------------------------------------------------------------------ pacientes variados
    @bloco("pacientes (inativo, aprovação, pagador, convênios, duplicado mesclado)")
    def _pacientes():
        def novo(nome, i, **campos):
            return Paciente.objects.create(consultorio=consultorio, nome=nome, cpf=_cpf(710000000 + i), email=campos.pop("email", ""), **campos)

        p = estado
        p["helena"] = novo(MARCADOR, 1, nascimento=date(1992, 5, 4), email=f"helena@{dominio}", telefone="11955550001")
        p["bruno"] = novo("Bruno Teixeira (demo)", 2, nascimento=date(1983, 11, 30), email=f"bruno@{dominio}", telefone="11955550002")
        p["clara"] = novo("Clara Nunes (demo)", 3, nascimento=date(1975, 2, 17), email=f"clara@{dominio}", telefone="11955550003")
        p["fernanda"] = novo("Fernanda Costa (demo)", 4, nascimento=date(1995, 7, 22), email=f"fernanda@{dominio}", telefone="11955550004")
        p["fernanda_dup"] = Paciente.objects.create(consultorio=consultorio, nome="Fernanda Costa (cadastro duplicado, demo)", telefone="11955550004")
        p["gustavo"] = novo("Gustavo Rocha (demo, inativo)", 5, nascimento=date(1980, 9, 9), ativo=False)
        p["isabela"] = novo("Isabela Moura (demo, pagador: pai)", 6, nascimento=date(2001, 4, 12), email=f"isabela@{dominio}", telefone="11955550006")
        p["otavio"] = novo("Otávio Ramos (demo, Bradesco)", 7, nascimento=date(1969, 1, 25), email=f"otavio@{dominio}", convenio="Bradesco Saúde", carteirinha="BR-778899")
        p["patricia"] = novo("Patrícia Lemos (demo, Amil)", 8, nascimento=date(1987, 12, 3), email=f"patricia@{dominio}", convenio="Amil", carteirinha="AM-445566")
        p["renan"] = novo("Renan Alves (demo, exige aprovação)", 9, nascimento=date(1998, 6, 18), email=f"renan@{dominio}", exige_aprovacao=True)
        Pagador.objects.create(consultorio=consultorio, paciente=p["isabela"], nome="Ricardo Moura (pai, demo)", cpf=_cpf(720000001))
        pacientes_srv.vincular_profissional(p["helena"], prof_psi)
        pacientes_srv.mesclar(r_assist, p["fernanda"], p["fernanda_dup"])

    if "helena" not in estado:  # blocos reexecutados: recarrega os pacientes ja criados
        achados = {c: Paciente.objects.filter(consultorio=consultorio, nome__startswith=n).first() for c, n in (
            ("helena", "Helena Duarte"), ("bruno", "Bruno Teixeira"), ("clara", "Clara Nunes"), ("fernanda", "Fernanda Costa (demo)"),
            ("gustavo", "Gustavo Rocha"), ("isabela", "Isabela Moura"), ("otavio", "Otávio Ramos"), ("patricia", "Patrícia Lemos"), ("renan", "Renan Alves"))}
        if not all(achados.values()):
            return falhas
        estado.update(achados)
    p = estado

    # ------------------------------------------------------------------ agenda
    @bloco("agenda: série recorrente, remarcação, confirmação, cancelamentos, solicitações")
    def _agenda():
        serie = agenda.agendar(r_assist, profissional=prof_psi, inicio=_quando(2, 16), duracao_minutos=50, tipo="presencial",
                               paciente=p["helena"], repetir_cada_semanas=1, repeticoes=4)
        for _, hora in ((0, 16),):
            ocupados.add((prof_psi.pk, 2, hora))
        confirmada = futura(prof_psi, p["bruno"], dias=3, hora=11)
        agenda.confirmar(r_assist, Consulta.objects.select_related("consultorio", "paciente", "profissional__usuario").get(pk=confirmada.pk))
        remarcar = futura(prof_psi, p["fernanda"], dias=5, hora=10)
        agenda.remarcar(r_assist, Consulta.objects.select_related("consultorio", "paciente", "profissional__usuario").get(pk=remarcar.pk), _quando(9, 10))
        cancelada = futura(prof_psi, p["helena"], dias=6, hora=15, tipo="online")
        agenda.cancelar(r_assist, Consulta.objects.select_related("consultorio", "paciente", "profissional__usuario").get(pk=cancelada.pk), por="equipe")
        # cancelamento tardio pelo paciente (gera cobranca)
        tardia = futura(prof_med, p["otavio"], dias=0, hora=19)
        agenda.cancelar(r_assist, Consulta.objects.select_related("consultorio", "paciente", "profissional__usuario").get(pk=tardia.pk), por="equipe", tardia=True)
        # cancelamento pelo proprio paciente, dentro do prazo
        pelo_paciente = futura(prof_psi, p["fernanda"], dias=16, hora=13)
        agenda.cancelar(r_assist, Consulta.objects.select_related("consultorio", "paciente", "profissional__usuario").get(pk=pelo_paciente.pk), por="paciente")
        # solicitacoes: uma aprovada, uma recusada, uma pendente
        s1 = agenda.criar_solicitacao(r_assist, paciente=p["renan"], profissional=prof_psi, horario=_quando(11, 9), tipo="presencial", observacao="Primeira consulta (demo)")
        s2 = agenda.criar_solicitacao(r_assist, paciente=p["patricia"], profissional=prof_med, horario=_quando(12, 10), tipo="online", observacao="Retorno (demo)")
        agenda.criar_solicitacao(r_assist, paciente=p["isabela"], profissional=prof_psi, horario=_quando(13, 14), tipo="presencial", observacao="Horário de tarde (demo)")
        agenda.aprovar_solicitacao(r_admin, SolicitacaoHorario.objects.get(pk=s1.pk))
        agenda.recusar_solicitacao(r_admin, SolicitacaoHorario.objects.get(pk=s2.pk))
        assert serie

    # ------------------------------------------------------------------ financeiro
    @bloco("financeiro: formas de pagamento, atrasos, repasse pago, recibo de terceiro, convênios, glosas")
    def _financeiro():
        bradesco = Convenio.objects.get_or_create(consultorio=consultorio, operadora="Bradesco Saúde")[0]
        amil = Convenio.objects.get_or_create(consultorio=consultorio, operadora="Amil")[0]
        for conv, valor in ((bradesco, "150.00"), (amil, "130.00")):
            TabelaValor.objects.get_or_create(consultorio=consultorio, profissional=prof_med, convenio=conv, tipo="consulta", defaults={"valor": D(valor)})
        CobrancaRecorrente.objects.get_or_create(consultorio=consultorio, profissional=prof_psi, descricao="Aluguel de sala (psicóloga)", defaults={"valor": D("600.00"), "dia_vencimento": 5})
        financeiro.gerar_cobrancas_do_mes(r_admin, consultorio, date.today())

        formas = ["pix", "dinheiro", "cartao", "transferencia", "outro"]
        pagas = []
        for i, (pac, prof, dias) in enumerate(((p["helena"], prof_psi, 40), (p["helena"], prof_psi, 33), (p["bruno"], prof_psi, 26),
                                               (p["fernanda"], prof_psi, 19), (p["isabela"], prof_psi, 12), (p["bruno"], prof_psi, 9))):
            c = realizada(prof, pac, dias=dias, hora=9 + i)
            financeiro.registrar_pagamento(r_assist, lanc(c), forma=formas[i % len(formas)])
            pagas.append(c)
        # recibo em nome do pagador (pai da Isabela) + nota externa
        c_isabela = pagas[4]
        recibo = financeiro.emitir_recibo(r_assist, lanc(c_isabela))
        financeiro.registrar_nota_externa(r_assist, recibo, "NF-2026-0042")
        # pendentes/atrasados e um cancelado
        atrasadas = [realizada(prof_psi, p["fernanda"], dias=d, hora=14 + i) for i, d in enumerate((50, 45))]
        for c in atrasadas:
            Lancamento.objects.filter(consulta=c).update(vencimento=date.today() - timedelta(days=30))
        c_cancel = realizada(prof_psi, p["bruno"], dias=15, hora=17)
        financeiro.cancelar_lancamento(r_assist, lanc(c_cancel))
        c_desfeito = realizada(prof_psi, p["helena"], dias=20, hora=11)
        financeiro.registrar_pagamento(r_assist, lanc(c_desfeito), forma="pix")
        financeiro.desfazer_pagamento(r_assist, lanc(c_desfeito))
        # repasse: marca um como pago, os outros ficam a pagar
        repasse = Repasse.objects.filter(consultorio=consultorio, status=Repasse.Status.A_PAGAR).order_by("criado_em").first()
        if repasse:
            financeiro.marcar_repasse_pago(r_admin, Repasse.objects.select_related("lancamento", "profissional__usuario").get(pk=repasse.pk))
        # convenios: pago integral, glosa total, glosa parcial, faturado aguardando
        ats = []
        for i, (pac, dias) in enumerate(((p["otavio"], 30), (p["otavio"], 23), (p["patricia"], 16), (p["patricia"], 8))):
            c = realizada(prof_med, pac, dias=dias, hora=10 + i, req_=r_assist)
            ats.append(AtendimentoConvenio.objects.select_related("lancamento__consultorio", "lancamento__profissional").get(lancamento__consulta=c))
        financeiro.faturar(r_assist, ats[:3], "LOTE-DEMO-02")
        financeiro.registrar_retorno(r_assist, ats[0], valor_recebido=ats[0].lancamento.valor)
        financeiro.registrar_retorno(r_assist, ats[1], valor_recebido=D("0.00"), motivo="Beneficiário sem cobertura na data (demo)")
        financeiro.faturar(r_assist, [ats[3]], "LOTE-DEMO-03")

    # ------------------------------------------------------------------ prontuario
    @bloco("prontuário: individual, conjunto, anexo, documentos, consentimento, liberação, delegação, exclusão")
    def _prontuario():
        consultorio_modelos = prontuario.garantir_modelos_padrao(consultorio)
        pr_helena = prontuario.abrir_prontuario(r_psi, prof_psi, paciente=p["helena"])
        reg = prontuario.criar_registro(r_psi, pr_helena, tipo="anamnese", conteudo="[DEMO] Anamnese fictícia: ansiedade em contexto acadêmico. Dados inventados.", cid="F41.1")
        prontuario.criar_registro(r_psi, pr_helena, tipo="evolucao", conteudo="[DEMO] Sessão 1 fictícia. Boa adesão.")
        prontuario.criar_registro(r_psi, pr_helena, tipo="evolucao", conteudo="[DEMO] Sessão 2 fictícia. Técnicas de respiração.")
        prontuario.nova_versao(r_psi, reg, conteudo="[DEMO] Anamnese fictícia (revisada): ansiedade acadêmica e insônia leve.", cid="F41.1")
        prontuario.anexar(r_psi, pr_helena, SimpleUploadedFile("escala-ansiedade-demo.pdf", b"%PDF-1.4\n% Documento ficticio de demonstracao\n%%EOF", content_type="application/pdf"))
        prontuario.anexar(r_psi, pr_helena, SimpleUploadedFile("historico-importado-demo.txt", "Histórico importado fictício.".encode(), content_type="text/plain"), importado_historico=True)
        for tipo, titulo, texto in (("atestado", "Atestado", "Atesto que Helena Duarte (demo) esteve em atendimento. Documento fictício."),
                                    ("relatorio", "Relatório", "Relatório fictício de acompanhamento de Helena Duarte (demo).")):
            modelo = consultorio_modelos.filter(tipo=tipo).first()
            prontuario.emitir_documento_clinico(r_psi, pr_helena, titulo=titulo, texto=texto, modelo=modelo)

        pr_joao = prontuario.abrir_prontuario(r_med, prof_med, paciente=Paciente.objects.get(consultorio=consultorio, nome="João Pereira (demo)"))
        prontuario.criar_registro(r_med, pr_joao, tipo="anamnese", conteudo="[DEMO] Avaliação psiquiátrica fictícia. Dados inventados.", cid="F32.1")
        prontuario.criar_registro(r_med, pr_joao, tipo="evolucao", conteudo="[DEMO] Retorno fictício. Ajuste de conduta (inventado).")

        grupo = GrupoAtendimento.objects.get(consultorio=consultorio, nome="Casal Silva (demo)")
        pr_casal = prontuario.abrir_prontuario(r_psi, prof_psi, grupo=grupo)
        prontuario.criar_registro(r_psi, pr_casal, tipo="evolucao", conteudo="[DEMO] Sessão de casal fictícia. Comunicação como foco.")

        # consentimento: aceito + liberacao (psicologa -> psiquiatra); outro pendente (casal); outro revogado
        (aceito,) = prontuario.solicitar_consentimento(r_psi, pr_helena, prof_med)
        prontuario.aceitar_consentimento(aceito, "203.0.113.10")
        prontuario.conceder_liberacao(r_psi, pr_helena, psiquiatra)
        prontuario.solicitar_consentimento(r_psi, pr_casal, prof_med)  # fica pendente
        revogado = prontuario.solicitar_consentimento(r_med, pr_joao, prof_psi)[0]
        prontuario.aceitar_consentimento(revogado, "203.0.113.11")
        prontuario.revogar_consentimento(revogado, "203.0.113.11")

        # delegacao (saida de profissional) e exclusao antecipada
        pr_bruno = prontuario.abrir_prontuario(r_psi, prof_psi, paciente=p["bruno"])
        prontuario.criar_registro(r_psi, pr_bruno, tipo="evolucao", conteudo="[DEMO] Registro fictício para demonstrar delegação.")
        prontuario.delegar(r_admin, pr_bruno, prof_med, motivo="Redistribuição de pacientes (demo)")
        pr_clara = prontuario.abrir_prontuario(r_psi, prof_psi, paciente=p["clara"])
        prontuario.criar_registro(r_psi, pr_clara, tipo="evolucao", conteudo="[DEMO] Registro fictício que será excluído a pedido do titular.")
        prontuario.excluir_antecipadamente(r_psi, pr_clara, motivo="Pedido do titular (demo)", confirmacao_nome=p["clara"].nome)

    # ------------------------------------------------------------------ portal / LGPD
    @bloco("portal: solicitações LGPD (aberta e atendida)")
    def _lgpd():
        portal.abrir_solicitacao_lgpd(r_assist, p["helena"], "copia", "Gostaria de uma cópia dos meus dados (demo).")
        atendida = portal.abrir_solicitacao_lgpd(r_assist, p["renan"], "correcao", "Corrigir meu telefone (demo).")
        portal.atender_solicitacao_lgpd(r_admin, atendida)

    # ------------------------------------------------------------------ importacao
    @bloco("importação: lote concluído, lote em prévia (com erro e duplicado) e lote cancelado")
    def _importacao():
        cabecalho = "nome;cpf;nascimento;email;telefone;convenio;carteirinha\r\n"
        ok = cabecalho + "".join(
            f"{nome};{_cpf(730000000 + i)};{nasc};{nome.split()[0].lower()}@{dominio};11944440{i:03d};;\r\n"
            for i, (nome, nasc) in enumerate((("Sérgio Batista (importado, demo)", "03/03/1971"), ("Tânia Freitas (importada, demo)", "21/08/1984"),
                                              ("Ubirajara Melo (importado, demo)", "14/01/1990")), start=1)
        )
        lote = importacao.criar_lote(r_assist, SimpleUploadedFile("pacientes-demo.csv", ok.encode(), content_type="text/csv"))
        importacao.confirmar(r_assist, lote)
        misto = cabecalho + (
            f"Valéria Prado (prévia, demo);{_cpf(740000001)};10/10/1982;;11933330001;;\r\n"
            f"Wagner Lopes (CPF inválido, demo);123.456.789-00;05/05/1979;;;;\r\n"
            f"Maria Souza (demo);52998224725;15/03/1985;;;;\r\n"
        )
        importacao.criar_lote(r_assist, SimpleUploadedFile("pacientes-previa-demo.csv", misto.encode(), content_type="text/csv"))
        descartado = importacao.criar_lote(r_assist, SimpleUploadedFile("pacientes-descartado-demo.csv", (cabecalho + f"Xavier Dias (demo);{_cpf(750000001)};;;;;\r\n").encode(), content_type="text/csv"))
        importacao.cancelar(r_assist, descartado)

    # ------------------------------------------------------------------ calendarios
    @bloco("calendário externo: conta Google com compromissos que bloqueiam horários")
    def _calendario():
        conta, _ = ContaCalendario.objects.get_or_create(
            consultorio=consultorio, profissional=prof_psi, provedor=ContaCalendario.Provedor.GOOGLE,
            defaults={"email_conta": f"helena.agenda@{dominio}", "ultima_sincronizacao": timezone.now()},
        )
        conta.guardar_tokens("token-demo-nao-valido", "refresh-demo-nao-valido", timezone.now() + timedelta(days=365))
        conta.save()
        for i, (titulo, dias, hora) in enumerate((("Dentista (compromisso pessoal)", 4, 8), ("Almoço com a família", 5, 12), ("Congresso (manhã)", 7, 9))):
            ini = _quando(dias, hora)
            EventoExterno.objects.get_or_create(
                consultorio=consultorio, conta=conta, id_externo=f"demo-evento-{i}",
                defaults={"origem": EventoExterno.Origem.EXTERNO, "titulo": titulo, "inicio": ini, "fim": ini + timedelta(hours=2)},
            )

    # ------------------------------------------------------------------ plataforma / operador
    @bloco("plataforma: contratos, histórico de pagamentos, suporte autorizado, exportações")
    def _plataforma():
        hoje = date.today()
        Contrato.objects.get_or_create(consultorio=consultorio, tipo=Contrato.Tipo.CONSULTORIO, defaults={"vigencia_inicio": hoje - timedelta(days=210)})
        Contrato.objects.get_or_create(consultorio=consultorio, tipo=Contrato.Tipo.INDIVIDUAL, profissional=prof_psi, defaults={"vigencia_inicio": hoje - timedelta(days=120)})
        for meses in (3, 2, 1):
            mes = (hoje.replace(day=1) - timedelta(days=30 * meses)).replace(day=1)
            if not PagamentoPlataforma.objects.filter(consultorio=consultorio, competencia=mes).exists():
                pagamento = operador.criar_pagamento(consultorio, competencia=mes, valor=D("199.00"), vencimento=mes + timedelta(days=10))
                operador.marcar_pago(pagamento)
        staff = Usuario.objects.filter(is_staff=True, is_active=True).order_by("criado_em").first()
        if staff:
            operador.autorizar_suporte(r_admin, staff, horas=24, motivo="Demonstração do fluxo de suporte (demo)")
        r_admin.user = admin
        exportacao.gerar_zip_consultorio(r_admin)
        Exportacao.objects.create(consultorio=consultorio, solicitada_por_id=admin.pk, tipo=Exportacao.Tipo.CONSULTORIO, arquivos=Paciente.objects.filter(consultorio=consultorio).count())
        Exportacao.objects.create(consultorio=consultorio, solicitada_por_id=psicologa.pk, tipo=Exportacao.Tipo.PRONTUARIOS, arquivos=3)

    # ------------------------------------------------------------------ volume para os relatorios
    @bloco("volume: atendimentos extras nos últimos 60 dias para relatórios")
    def _volume():
        nomes = [p["helena"], p["fernanda"], p["isabela"], p["renan"], p["otavio"], p["patricia"]]
        for i, pac in enumerate(nomes):
            prof = prof_med if pac.convenio else prof_psi
            for j in range(3):
                c = realizada(prof, pac, dias=3 + i * 5 + j * 11, hora=9 + (i + j) % 8)
                if j < 2 and not pac.convenio:
                    financeiro.registrar_pagamento(r_assist, lanc(c), forma=["pix", "cartao", "dinheiro"][(i + j) % 3])
        for k in range(4):
            futura(prof_psi, p["renan"] if k % 2 else p["helena"], dias=14 + k * 7, hora=10 + k)

    return falhas
