from datetime import timedelta

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.agenda.models import Consulta
from apps.auditoria import servico as auditoria
from apps.core.permissoes import PERFIS_INTERNOS, perfil_requerido
from apps.prontuario import acesso as prontuario_acesso
from apps.core.validadores import somente_digitos

from . import servico
from .forms import GrupoEdicaoForm, GrupoForm, MesclarForm, PacienteForm, PagadorForm, ResponsavelForm
from .models import GrupoAtendimento, ParticipanteGrupo, Paciente, Pagador, ResponsavelLegal


def _paciente(request, pk):
    return get_object_or_404(servico.pacientes_visiveis(request, incluir_mesclados=True), pk=pk)


@perfil_requerido(*PERFIS_INTERNOS)
def lista(request):
    pacientes = servico.pacientes_visiveis(request)
    if request.GET.get("arquivados") != "1":
        pacientes = pacientes.filter(ativo=True)
    termo = request.GET.get("q", "").strip()
    if termo:
        filtro = Q(nome__icontains=termo)
        digitos = somente_digitos(termo)
        if digitos:
            filtro |= Q(cpf__startswith=digitos) | Q(telefone__contains=digitos)
        pacientes = pacientes.filter(filtro)

    dias = request.GET.get("sem_consulta_ha", "")
    if dias.isdigit() and int(dias) > 0:
        limite = timezone.now() - timedelta(days=int(dias))
        pacientes = pacientes.filter(Q(ultimo_atendimento_em__lt=limite) | Q(ultimo_atendimento_em__isnull=True))

    pacientes = pacientes.distinct()
    plano = request.consultorio.plano
    ativos = Paciente.objects.filter(consultorio=request.consultorio, ativo=True, mesclado_em__isnull=True).count()
    acima_do_limite = bool(plano and ativos > plano.limite_pacientes_ativos)
    return render(
        request,
        "pacientes/lista.html",
        {"pacientes": pacientes[:200], "q": termo, "sem_consulta_ha": dias, "acima_do_limite": acima_do_limite, "plano": plano, "ativos": ativos},
    )


@perfil_requerido(*PERFIS_INTERNOS)
def novo(request):
    form = PacienteForm(request.POST or None, consultorio=request.consultorio)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            paciente = form.save(commit=False)
            paciente.consultorio = request.consultorio
            paciente.save()
            profissional = servico.profissional_da_requisicao(request)
            if profissional:
                servico.vincular_profissional(paciente, profissional)
            auditoria.registrar(request, "paciente_criado", "paciente", paciente.pk)
        messages.success(request, "Paciente cadastrado.")
        return redirect("pacientes:detalhe", pk=paciente.pk)
    return render(request, "pacientes/form.html", {"form": form, "titulo": "Novo paciente"})


@perfil_requerido(*PERFIS_INTERNOS)
def detalhe(request, pk):
    paciente = _paciente(request, pk)
    agora = timezone.now()
    consultas = Consulta.objects.filter(paciente=paciente).select_related("profissional__usuario")
    return render(
        request,
        "pacientes/detalhe.html",
        {
            "paciente": paciente,
            "responsaveis": paciente.responsaveis.all(),
            "pagador": getattr(paciente, "pagador", None),
            "eh_profissional": servico.profissional_da_requisicao(request) is not None,
            "tem_consolidado": bool(prontuario_acesso.prontuarios_do_paciente(request, paciente)) if not paciente.mesclado_em else False,
            "documentos_comparecimento": paciente.documentos.filter(chave__isnull=True)[:10],
            "proximas": consultas.filter(inicio__gte=agora, status__in=Consulta.ATIVAS)[:10],
            "anteriores": consultas.filter(inicio__lt=agora).order_by("-inicio")[:10],
        },
    )


@perfil_requerido(*PERFIS_INTERNOS)
def editar(request, pk):
    paciente = _paciente(request, pk)
    if paciente.mesclado_em:
        messages.error(request, "Este cadastro foi mesclado e não pode ser editado.")
        return redirect("pacientes:detalhe", pk=pk)
    form = PacienteForm(request.POST or None, instance=paciente, consultorio=request.consultorio)
    if request.method == "POST" and form.is_valid():
        form.save()
        auditoria.registrar(request, "paciente_alterado", "paciente", paciente.pk)
        messages.success(request, "Dados atualizados.")
        return redirect("pacientes:detalhe", pk=pk)
    return render(request, "pacientes/form.html", {"form": form, "titulo": f"Editar {paciente.nome}", "paciente": paciente})


@perfil_requerido(*PERFIS_INTERNOS)
def responsavel_novo(request, pk):
    paciente = _paciente(request, pk)
    form = ResponsavelForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        responsavel = form.save(commit=False)
        responsavel.consultorio = request.consultorio
        responsavel.paciente = paciente
        responsavel.save()
        auditoria.registrar(request, "responsavel_criado", "paciente", paciente.pk)
        return redirect("pacientes:detalhe", pk=pk)
    return render(request, "pacientes/form.html", {"form": form, "titulo": "Novo responsável legal", "paciente": paciente})


@perfil_requerido(*PERFIS_INTERNOS)
@require_POST
def responsavel_remover(request, pk, responsavel_pk):
    paciente = _paciente(request, pk)
    get_object_or_404(ResponsavelLegal, pk=responsavel_pk, paciente=paciente).delete()
    auditoria.registrar(request, "responsavel_removido", "paciente", paciente.pk)
    return redirect("pacientes:detalhe", pk=pk)


@perfil_requerido(*PERFIS_INTERNOS)
def pagador_definir(request, pk):
    paciente = _paciente(request, pk)
    existente = Pagador.objects.filter(paciente=paciente).first()
    form = PagadorForm(request.POST or None, instance=existente)
    if request.method == "POST" and form.is_valid():
        pagador = form.save(commit=False)
        pagador.consultorio = request.consultorio
        pagador.paciente = paciente
        pagador.save()
        auditoria.registrar(request, "pagador_definido", "paciente", paciente.pk)
        return redirect("pacientes:detalhe", pk=pk)
    return render(request, "pacientes/form.html", {"form": form, "titulo": "Pagador do recibo", "paciente": paciente})


@perfil_requerido(*PERFIS_INTERNOS)
def mesclar(request, pk):
    destino = _paciente(request, pk)
    if destino.mesclado_em:
        messages.error(request, "Este cadastro já foi mesclado.")
        return redirect("pacientes:detalhe", pk=pk)
    candidatos = servico.pacientes_visiveis(request).exclude(pk=destino.pk)
    form = MesclarForm(request.POST or None, candidatos=candidatos)
    if request.method == "POST" and form.is_valid():
        servico.mesclar(request, destino, form.cleaned_data["origem"])
        messages.success(request, "Cadastros mesclados. O histórico foi preservado.")
        return redirect("pacientes:detalhe", pk=destino.pk)
    return render(request, "pacientes/mesclar.html", {"form": form, "paciente": destino})


@perfil_requerido(*PERFIS_INTERNOS)
def grupos(request):
    return render(request, "pacientes/grupos.html", {"grupos": GrupoAtendimento.objects.filter(consultorio=request.consultorio)})


@perfil_requerido(*PERFIS_INTERNOS)
def grupo_novo(request):
    form = GrupoForm(request.POST or None, pacientes=servico.pacientes_visiveis(request).filter(ativo=True))
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            grupo = form.save(commit=False)
            grupo.consultorio = request.consultorio
            grupo.save()
            for paciente in form.cleaned_data["participantes"]:
                ParticipanteGrupo.objects.create(consultorio=request.consultorio, grupo=grupo, paciente=paciente)
            auditoria.registrar(request, "grupo_criado", "grupo", grupo.pk)
        return redirect("pacientes:grupos")
    return render(request, "pacientes/form.html", {"form": form, "titulo": "Novo atendimento em grupo"})


@perfil_requerido(*PERFIS_INTERNOS)
def grupo_editar(request, pk):
    grupo = get_object_or_404(GrupoAtendimento, pk=pk, consultorio=request.consultorio)
    atuais = list(ParticipanteGrupo.objects.filter(grupo=grupo).values_list("paciente_id", flat=True))
    candidatos = Paciente.objects.filter(
        Q(pk__in=servico.pacientes_visiveis(request).filter(ativo=True).values("pk")) | Q(pk__in=atuais)
    )
    form = GrupoEdicaoForm(request.POST or None, instance=grupo, pacientes=candidatos, initial={"participantes": atuais})
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            grupo = form.save()
            escolhidos = {p.pk: p for p in form.cleaned_data["participantes"]}
            ParticipanteGrupo.objects.filter(grupo=grupo).exclude(paciente_id__in=escolhidos).delete()
            for pk_paciente, paciente in escolhidos.items():
                if pk_paciente not in atuais:
                    ParticipanteGrupo.objects.create(consultorio=request.consultorio, grupo=grupo, paciente=paciente)
            auditoria.registrar(request, "grupo_editado", "grupo", grupo.pk)
        messages.success(request, "Atendimento em grupo atualizado.")
        return redirect("pacientes:grupos")
    return render(request, "pacientes/form.html", {"form": form, "titulo": f"Editar {grupo.nome}"})
