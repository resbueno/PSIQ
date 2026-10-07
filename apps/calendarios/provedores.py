"""Clientes REST de Google Calendar (v3) e Microsoft Graph. Interface comum, trocavel nos testes.

Eventos exportados mostram so 'Consulta' e o horario (o nome do paciente depende de opcao do profissional)."""

from datetime import datetime, timedelta, timezone as tz
from urllib.parse import urlencode

import requests
from django.conf import settings

TIMEOUT = 15


class ErroProvedor(Exception):
    """Falha ao falar com o provedor; a mensagem e curta e sem dados pessoais."""


def _checar(resposta):
    if resposta.status_code >= 400:
        raise ErroProvedor(f"HTTP {resposta.status_code}")
    return resposta


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(texto: str) -> datetime:
    """Datas do provedor: com offset, com Z ou (Graph, em UTC) sem sufixo; fracoes longas sao truncadas (Python 3.11+)."""
    dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=tz.utc)


class Provedor:
    nome = ""

    def configurado(self) -> bool:
        raise NotImplementedError

    def url_autorizacao(self, state: str, redirect_uri: str) -> str:
        raise NotImplementedError

    def trocar_codigo(self, codigo: str, redirect_uri: str) -> dict:
        """Retorna {acesso, refresh, expira_em, email}."""
        raise NotImplementedError

    def renovar(self, refresh: str) -> dict:
        """Retorna {acesso, refresh (ou ''), expira_em}."""
        raise NotImplementedError

    def listar(self, acesso: str, inicio: datetime, fim: datetime) -> list:
        """Eventos ocupados: lista de {id, inicio, fim} (datas com fuso). Ignora cancelados e 'livre'."""
        raise NotImplementedError

    def criar(self, acesso: str, titulo: str, inicio: datetime, fim: datetime) -> str:
        raise NotImplementedError

    def atualizar(self, acesso: str, id_externo: str, titulo: str, inicio: datetime, fim: datetime) -> None:
        raise NotImplementedError

    def apagar(self, acesso: str, id_externo: str) -> None:
        raise NotImplementedError

    @staticmethod
    def _expira(segundos) -> datetime:
        return datetime.now(tz.utc) + timedelta(seconds=int(segundos or 3600))


class Google(Provedor):
    nome = "google"
    AUTORIZAR = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN = "https://oauth2.googleapis.com/token"
    USUARIO = "https://www.googleapis.com/oauth2/v2/userinfo"
    API = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
    ESCOPOS = "https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/userinfo.email"

    def configurado(self):
        return bool(settings.MEUPSIQ_GOOGLE_CLIENT_ID and settings.MEUPSIQ_GOOGLE_CLIENT_SECRET)

    def url_autorizacao(self, state, redirect_uri):
        return self.AUTORIZAR + "?" + urlencode({
            "client_id": settings.MEUPSIQ_GOOGLE_CLIENT_ID, "redirect_uri": redirect_uri, "response_type": "code",
            "scope": self.ESCOPOS, "access_type": "offline", "prompt": "consent", "state": state,
        })

    def _token(self, dados):
        resposta = _checar(requests.post(self.TOKEN, data={
            "client_id": settings.MEUPSIQ_GOOGLE_CLIENT_ID, "client_secret": settings.MEUPSIQ_GOOGLE_CLIENT_SECRET, **dados,
        }, timeout=TIMEOUT)).json()
        return {"acesso": resposta["access_token"], "refresh": resposta.get("refresh_token", ""), "expira_em": self._expira(resposta.get("expires_in"))}

    def trocar_codigo(self, codigo, redirect_uri):
        tokens = self._token({"grant_type": "authorization_code", "code": codigo, "redirect_uri": redirect_uri})
        usuario = _checar(requests.get(self.USUARIO, headers=self._h(tokens["acesso"]), timeout=TIMEOUT)).json()
        return {**tokens, "email": usuario.get("email", "")}

    def renovar(self, refresh):
        return self._token({"grant_type": "refresh_token", "refresh_token": refresh})

    @staticmethod
    def _h(acesso):
        return {"Authorization": f"Bearer {acesso}"}

    def listar(self, acesso, inicio, fim):
        eventos, pagina = [], None
        while True:
            parametros = {"timeMin": _iso_utc(inicio), "timeMax": _iso_utc(fim), "singleEvents": "true", "maxResults": 250}
            if pagina:
                parametros["pageToken"] = pagina
            dados = _checar(requests.get(self.API, headers=self._h(acesso), params=parametros, timeout=TIMEOUT)).json()
            for item in dados.get("items", []):
                if item.get("status") == "cancelled" or item.get("transparency") == "transparent":
                    continue
                inicio_i, fim_i = item.get("start", {}), item.get("end", {})
                if "dateTime" not in inicio_i:  # evento de dia inteiro: nao bloqueia horarios
                    continue
                eventos.append({"id": item["id"], "inicio": _parse(inicio_i["dateTime"]), "fim": _parse(fim_i["dateTime"])})
            pagina = dados.get("nextPageToken")
            if not pagina:
                return eventos

    def _corpo(self, titulo, inicio, fim):
        return {"summary": titulo, "start": {"dateTime": _iso_utc(inicio), "timeZone": "UTC"}, "end": {"dateTime": _iso_utc(fim), "timeZone": "UTC"}}

    def criar(self, acesso, titulo, inicio, fim):
        return _checar(requests.post(self.API, headers=self._h(acesso), json=self._corpo(titulo, inicio, fim), timeout=TIMEOUT)).json()["id"]

    def atualizar(self, acesso, id_externo, titulo, inicio, fim):
        _checar(requests.patch(f"{self.API}/{id_externo}", headers=self._h(acesso), json=self._corpo(titulo, inicio, fim), timeout=TIMEOUT))

    def apagar(self, acesso, id_externo):
        resposta = requests.delete(f"{self.API}/{id_externo}", headers=self._h(acesso), timeout=TIMEOUT)
        if resposta.status_code not in (200, 204, 404, 410):
            raise ErroProvedor(f"HTTP {resposta.status_code}")


class Microsoft(Provedor):
    nome = "microsoft"
    API = "https://graph.microsoft.com/v1.0/me"
    ESCOPOS = "offline_access Calendars.ReadWrite User.Read"

    def _base(self):
        return f"https://login.microsoftonline.com/{settings.MEUPSIQ_MICROSOFT_TENANT}/oauth2/v2.0"

    def configurado(self):
        return bool(settings.MEUPSIQ_MICROSOFT_CLIENT_ID and settings.MEUPSIQ_MICROSOFT_CLIENT_SECRET)

    def url_autorizacao(self, state, redirect_uri):
        return self._base() + "/authorize?" + urlencode({
            "client_id": settings.MEUPSIQ_MICROSOFT_CLIENT_ID, "redirect_uri": redirect_uri, "response_type": "code",
            "scope": self.ESCOPOS, "response_mode": "query", "state": state, "prompt": "select_account",
        })

    def _token(self, dados):
        resposta = _checar(requests.post(self._base() + "/token", data={
            "client_id": settings.MEUPSIQ_MICROSOFT_CLIENT_ID, "client_secret": settings.MEUPSIQ_MICROSOFT_CLIENT_SECRET,
            "scope": self.ESCOPOS, **dados,
        }, timeout=TIMEOUT)).json()
        return {"acesso": resposta["access_token"], "refresh": resposta.get("refresh_token", ""), "expira_em": self._expira(resposta.get("expires_in"))}

    def trocar_codigo(self, codigo, redirect_uri):
        tokens = self._token({"grant_type": "authorization_code", "code": codigo, "redirect_uri": redirect_uri})
        eu = _checar(requests.get(self.API, headers=self._h(tokens["acesso"]), timeout=TIMEOUT)).json()
        return {**tokens, "email": eu.get("mail") or eu.get("userPrincipalName", "")}

    def renovar(self, refresh):
        return self._token({"grant_type": "refresh_token", "refresh_token": refresh})

    @staticmethod
    def _h(acesso):
        return {"Authorization": f"Bearer {acesso}", "Prefer": 'outlook.timezone="UTC"'}

    def listar(self, acesso, inicio, fim):
        eventos = []
        url, parametros = f"{self.API}/calendarView", {"startDateTime": _iso_utc(inicio), "endDateTime": _iso_utc(fim), "$top": 250}
        while url:
            dados = _checar(requests.get(url, headers=self._h(acesso), params=parametros, timeout=TIMEOUT)).json()
            for item in dados.get("value", []):
                if item.get("isCancelled") or item.get("showAs") == "free" or item.get("isAllDay"):
                    continue
                eventos.append({"id": item["id"], "inicio": _parse(item["start"]["dateTime"]), "fim": _parse(item["end"]["dateTime"])})
            url, parametros = dados.get("@odata.nextLink"), None
        return eventos

    def _corpo(self, titulo, inicio, fim):
        fmt = lambda d: d.astimezone(tz.utc).strftime("%Y-%m-%dT%H:%M:%S")
        return {"subject": titulo, "start": {"dateTime": fmt(inicio), "timeZone": "UTC"}, "end": {"dateTime": fmt(fim), "timeZone": "UTC"}}

    def criar(self, acesso, titulo, inicio, fim):
        return _checar(requests.post(f"{self.API}/events", headers=self._h(acesso), json=self._corpo(titulo, inicio, fim), timeout=TIMEOUT)).json()["id"]

    def atualizar(self, acesso, id_externo, titulo, inicio, fim):
        _checar(requests.patch(f"{self.API}/events/{id_externo}", headers=self._h(acesso), json=self._corpo(titulo, inicio, fim), timeout=TIMEOUT))

    def apagar(self, acesso, id_externo):
        resposta = requests.delete(f"{self.API}/events/{id_externo}", headers=self._h(acesso), timeout=TIMEOUT)
        if resposta.status_code not in (200, 204, 404, 410):
            raise ErroProvedor(f"HTTP {resposta.status_code}")


PROVEDORES = {"google": Google(), "microsoft": Microsoft()}
