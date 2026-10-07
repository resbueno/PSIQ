from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(DJANGO_DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")

DEBUG = env("DJANGO_DEBUG")
SECRET_KEY = env("DJANGO_SECRET_KEY", default="inseguro-apenas-para-desenvolvimento" if DEBUG else None)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core",
    "apps.plataforma",
    "apps.contas",
    "apps.auditoria",
    "apps.pacientes",
    "apps.agenda",
    "apps.prontuario",
    "apps.financeiro",
    "apps.portal",
    "apps.calendarios",
    "apps.operador",
    "apps.relatorios",
    "apps.importacao",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "apps.core.middleware.PoliticaDeConteudoMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Depois da autenticacao: define o consultorio da sessao no banco (RLS).
    "apps.core.middleware.ContextoConsultorioMiddleware",
    "apps.core.middleware.SegundoFatorObrigatorioMiddleware",
    "apps.core.middleware.InatividadeProntuarioMiddleware",
    "apps.core.middleware.SomenteLeituraMiddleware",
    "apps.core.middleware.SuporteAuditoriaMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.contexto_psiq",
            ],
        },
    },
]

# PostgreSQL real e obrigatorio: o isolamento por consultorio depende de RLS.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", default="psiq"),
        "USER": env("DB_USER", default="psiq_app"),
        "PASSWORD": env("DB_PASSWORD", default="psiq_dev"),
        "HOST": env("DB_HOST", default="localhost"),
        "PORT": env("DB_PORT", default="5432"),
        "CONN_MAX_AGE": 0,  # o contexto de RLS vive na conexao; nao reutilizar entre requisicoes
    }
}

AUTH_USER_MODEL = "contas.Usuario"
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
LOGIN_URL = "contas:entrar"
LOGIN_REDIRECT_URL = "painel"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True
LOCALE_PATHS = [BASE_DIR / "locale"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Sessao: guardada no banco, expira por inatividade, cookie protegido.
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = env.int("PSIQ_INATIVIDADE_SEGUNDOS", default=1800)
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# Instalacao sob um prefixo (ex.: https://host/psiq). O proxy remove o prefixo antes de chamar a aplicacao;
# o Django passa a gerar todos os enderecos com ele. Cookies com nome e caminho proprios nao colidem com
# outros sistemas no mesmo dominio.
PSIQ_PREFIXO = env("PSIQ_PREFIXO", default="").rstrip("/")
SESSION_COOKIE_NAME = "psiq_sessionid"
CSRF_COOKIE_NAME = "psiq_csrftoken"
if PSIQ_PREFIXO:
    FORCE_SCRIPT_NAME = PSIQ_PREFIXO
    SESSION_COOKIE_PATH = PSIQ_PREFIXO
    CSRF_COOKIE_PATH = PSIQ_PREFIXO
CSRF_TRUSTED_ORIGINS = env.list("PSIQ_ORIGENS_CONFIAVEIS", default=[])
# Absoluto e com o prefixo: o WhiteNoise serve em /psiq/static/ e o {% static %} gera o mesmo endereco.
STATIC_URL = f"{PSIQ_PREFIXO}/static/"

# Em producao tudo e HTTPS. Homologacao sem terminador TLS define PSIQ_HTTPS=false.
if not DEBUG and env.bool("PSIQ_HTTPS", default=True):
    SECURE_SSL_REDIRECT = True
    SECURE_REDIRECT_EXEMPT = [r"(^|/)saude/$"]  # o healthcheck do contêiner chama por HTTP, de dentro
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env.int("PSIQ_HSTS_SEGUNDOS", default=31536000)  # 0 quando o proxy ja define HSTS
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("PSIQ_HSTS_SUBDOMINIOS", default=True) and SECURE_HSTS_SECONDS > 0
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"

# Chave mestra (Fernet) para segredos cifrados. Fica fora do banco e do backup.
PSIQ_CHAVE_MESTRA = env("PSIQ_CHAVE_MESTRA", default="")

# Bloqueio progressivo de login: apos N falhas, espera dobra a cada falha (maximo em minutos).
PSIQ_LOGIN_FALHAS_ANTES_DO_BLOQUEIO = 5
PSIQ_LOGIN_BLOQUEIO_MAXIMO_MINUTOS = 60
PSIQ_NOME_EMISSOR_2FA = "PSIQ"

# Avisos e links
PSIQ_URL_BASE = env("PSIQ_URL_BASE", default="http://localhost:8000")
PSIQ_JITSI_URL = env("PSIQ_JITSI_URL", default="https://meet.jit.si")
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=25)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=False)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="PSIQ <nao-responder@localhost>")

# Prontuario
PSIQ_ANEXOS_DIR = env("PSIQ_ANEXOS_DIR", default=str(BASE_DIR / "armazenamento"))
PSIQ_ANEXO_MAX_BYTES = 10 * 1024 * 1024
PSIQ_ANEXO_EXTENSOES = ("pdf", "png", "jpg", "jpeg", "txt", "doc", "docx")
PSIQ_INATIVIDADE_PRONTUARIO_SEGUNDOS = env.int("PSIQ_INATIVIDADE_PRONTUARIO_SEGUNDOS", default=900)

# Portal do paciente
PSIQ_PORTAL_CODIGO_VALIDADE_MINUTOS = 10
PSIQ_PORTAL_MAX_TENTATIVAS = 5
PSIQ_PORTAL_MAX_CODIGOS_POR_HORA = 5
PSIQ_PORTAL_IDADE_MINIMA_ACESSO_PROPRIO = 16

# Notificacoes push (Web Push). Gere as chaves VAPID com: npx web-push generate-vapid-keys
PSIQ_VAPID_PUBLIC_KEY = env("PSIQ_VAPID_PUBLIC_KEY", default="")
PSIQ_VAPID_PRIVATE_KEY = env("PSIQ_VAPID_PRIVATE_KEY", default="")
PSIQ_VAPID_SUBJECT = env("PSIQ_VAPID_SUBJECT", default="mailto:contato@localhost")

# Calendarios externos (OAuth). Registre o app no Google Cloud e no Azure e informe as credenciais.
PSIQ_GOOGLE_CLIENT_ID = env("PSIQ_GOOGLE_CLIENT_ID", default="")
PSIQ_GOOGLE_CLIENT_SECRET = env("PSIQ_GOOGLE_CLIENT_SECRET", default="")
PSIQ_MICROSOFT_CLIENT_ID = env("PSIQ_MICROSOFT_CLIENT_ID", default="")
PSIQ_MICROSOFT_CLIENT_SECRET = env("PSIQ_MICROSOFT_CLIENT_SECRET", default="")
PSIQ_MICROSOFT_TENANT = env("PSIQ_MICROSOFT_TENANT", default="common")
PSIQ_CALENDARIO_JANELA_DIAS = 60

# Inadimplencia: aviso, carencia de 15 dias, depois somente leitura (exportacao sempre liberada)
PSIQ_CARENCIA_DIAS = 15

# Importacao de pacientes por planilha
PSIQ_IMPORTACAO_MAX_BYTES = 5 * 1024 * 1024
PSIQ_IMPORTACAO_MAX_LINHAS = 5000

# Monitoramento (python manage.py verificar_saude, via cron)
PSIQ_ALERTA_EMAIL = env("PSIQ_ALERTA_EMAIL", default="")
PSIQ_BACKUP_MARCADOR = env("PSIQ_BACKUP_MARCADOR", default="")  # arquivo que o job de backup atualiza ao terminar com sucesso
PSIQ_BACKUP_MAX_HORAS = 26
PSIQ_ALERTA_FALHAS_DE_AVISO = 10      # por hora
PSIQ_ALERTA_LOGINS_FALHOS = 10        # falhas acumuladas em uma conta
PSIQ_ALERTA_EXPORTACOES = 5           # por hora, em um consultorio
