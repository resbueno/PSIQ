from django.apps import AppConfig


class PortalConfig(AppConfig):
    name = "apps.portal"
    label = "portal"
    verbose_name = "Portal do paciente"

    def ready(self):
        from apps.agenda import avisos

        from .push import CanalPush

        canal = CanalPush()
        avisos.CANAIS[canal.nome] = canal
