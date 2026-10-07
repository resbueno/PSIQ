from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.password_validation import validate_password
from django.db import transaction

from apps.contas.models import Perfil, Usuario, Vinculo
from apps.core.tenancy import contexto
from apps.plataforma.models import Consultorio


class Command(BaseCommand):
    help = "Cria um consultório e o primeiro administrador (a escrita do vínculo exige contexto de RLS)."

    def add_arguments(self, parser):
        parser.add_argument("--nome", required=True, help="Nome do consultório")
        parser.add_argument("--admin-email", required=True)
        parser.add_argument("--admin-nome", required=True)
        parser.add_argument("--admin-senha", required=True, help="Mínimo de 12 caracteres")

    @transaction.atomic
    def handle(self, *args, **opcoes):
        email = opcoes["admin_email"].strip().lower()
        try:
            validate_password(opcoes["admin_senha"])
        except ValidationError as erro:
            raise CommandError("; ".join(erro.messages))
        if Usuario.objects.filter(email=email).exists():
            raise CommandError("Já existe um usuário com esse e-mail.")

        consultorio = Consultorio.objects.create(nome=opcoes["nome"])
        usuario = Usuario.objects.create_user(email, opcoes["admin_senha"], nome=opcoes["admin_nome"])
        with contexto(consultorio_id=consultorio.pk, usuario_id=usuario.pk):
            Vinculo.objects.create(usuario=usuario, consultorio=consultorio, perfil=Perfil.ADMIN)
        self.stdout.write(self.style.SUCCESS(f"Consultório '{consultorio.nome}' criado. Administrador: {email}"))
