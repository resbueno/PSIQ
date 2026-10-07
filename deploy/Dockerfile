# Imagem de producao do MeuPSIQ (Django + gunicorn). Node nao e necessario: nao ha build de front ainda.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN useradd --create-home --uid 10001 psiq
ARG INSTALAR_DEV=0
COPY requirements.txt requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements.txt && if [ "$INSTALAR_DEV" = "1" ]; then pip install --no-cache-dir -r requirements-dev.txt; fi

COPY . .
RUN DJANGO_SECRET_KEY=build DJANGO_DEBUG=false python manage.py collectstatic --noinput

# Diretorios de dados com dono psiq: volumes novos herdam o dono da imagem (sem isso o upload falha com Permission denied).
RUN mkdir -p /data/anexos /var/lib/psiq && chown psiq:psiq /data/anexos /var/lib/psiq

USER psiq
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/saude/', timeout=4).status == 200 else 1)"
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "30", "--access-logfile", "-"]
