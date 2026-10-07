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
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
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

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Sessao: guardada no banco, expira por inatividade, cookie protegido.
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = env.int("PSIQ_INATIVIDADE_SEGUNDOS", default=1800)
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
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
