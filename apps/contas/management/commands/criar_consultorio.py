from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.operador.servico import ErroOperador, criar_consultorio_com_admin


class Command(BaseCommand):
    help = "Cria um consultório e o primeiro administrador (a escrita do vínculo exige contexto de RLS)."

    def add_arguments(self, parser):
        parser.add_argument("--nome", required=True, help="Nome do consultório")
        parser.add_argument("--admin-email", required=True)
        parser.add_argument("--admin-nome", required=True)
        parser.add_argument("--admin-senha", required=True, help="Mínimo de 12 caracteres")

    def handle(self, *args, **opcoes):
        try:
            consultorio, admin = criar_consultorio_com_admin(
                nome=opcoes["nome"], admin_nome=opcoes["admin_nome"], admin_email=opcoes["admin_email"], admin_senha=opcoes["admin_senha"]
            )
        except ValidationError as erro:
            raise CommandError("; ".join(erro.messages))
        except ErroOperador as erro:
            raise CommandError(str(erro))
        self.stdout.write(self.style.SUCCESS(f"Consultório '{consultorio.nome}' criado. Administrador: {admin.email}"))
