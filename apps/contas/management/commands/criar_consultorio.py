from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

from apps.operador.servico import ErroOperador, criar_consultorio_com_admin
from apps.plataforma.models import Consultorio


def _slug_disponivel(nome):
    base = slugify(nome)[:50] or "consultorio"
    slug, sufixo = base, 1
    while Consultorio.objects.filter(slug=slug).exists():
        sufixo += 1
        slug = f"{base}-{sufixo}"
    return slug


class Command(BaseCommand):
    help = "Cria um consultório e o primeiro administrador (a escrita do vínculo exige contexto de RLS)."

    def add_arguments(self, parser):
        parser.add_argument("--nome", required=True, help="Nome do consultório")
        parser.add_argument("--slug", help="Link de acesso, ex.: clinica-bem-estar. Gerado a partir do nome se omitido.")
        parser.add_argument("--admin-email", required=True)
        parser.add_argument("--admin-nome", required=True)
        parser.add_argument("--admin-senha", required=True, help="Mínimo de 12 caracteres")

    def handle(self, *args, **opcoes):
        slug = opcoes["slug"] or _slug_disponivel(opcoes["nome"])
        try:
            consultorio, admin = criar_consultorio_com_admin(
                nome=opcoes["nome"], slug=slug, admin_nome=opcoes["admin_nome"], admin_email=opcoes["admin_email"],
                admin_senha=opcoes["admin_senha"],
            )
        except ValidationError as erro:
            raise CommandError("; ".join(erro.messages))
        except ErroOperador as erro:
            raise CommandError(str(erro))
        self.stdout.write(self.style.SUCCESS(f"Consultório '{consultorio.nome}' criado (/{consultorio.slug}/). Administrador: {admin.email}"))
