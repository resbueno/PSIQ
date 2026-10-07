"""Canal de notificacao push (Web Push, VAPID). Registrado em `apps.agenda.avisos.CANAIS` quando o app carrega.

O texto e o mesmo resumo neutro do e-mail: nada de tipo de atendimento nem de conteudo clinico."""

import json
import logging

from django.conf import settings
from pywebpush import WebPushException, webpush

from apps.agenda.avisos import Canal
from apps.agenda.models import Aviso

from .models import AssinaturaPush

logger = logging.getLogger(__name__)
PREFIXO = "push:"


def configurado() -> bool:
    return bool(settings.MEUPSIQ_VAPID_PUBLIC_KEY and settings.MEUPSIQ_VAPID_PRIVATE_KEY)


class CanalPush(Canal):
    nome = Aviso.Canal.PUSH

    def enderecos(self, destinatario):
        if not configurado():
            return []
        ids = AssinaturaPush.objects.filter(paciente=destinatario["paciente"], ativa=True).values_list("pk", flat=True)
        return [f"{PREFIXO}{pk}" for pk in ids]

    def enviar(self, endereco, assunto, corpo, resumo, link):
        assinatura = AssinaturaPush.objects.get(pk=endereco.removeprefix(PREFIXO))
        try:
            webpush(
                subscription_info={"endpoint": assinatura.endpoint, "keys": {"p256dh": assinatura.p256dh, "auth": assinatura.auth}},
                data=json.dumps({"title": assunto, "body": resumo, "url": link}),
                vapid_private_key=settings.MEUPSIQ_VAPID_PRIVATE_KEY,
                vapid_claims={"sub": settings.MEUPSIQ_VAPID_SUBJECT},
                ttl=60 * 60 * 12,
            )
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):  # aparelho desinscreveu: para de tentar
                AssinaturaPush.objects.filter(pk=assinatura.pk).update(ativa=False)
            raise
