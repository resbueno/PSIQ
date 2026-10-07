from django.db import migrations, models
from django.utils.text import slugify


def preencher_slugs(apps, schema_editor):
    Consultorio = apps.get_model("plataforma", "Consultorio")
    usados = set()
    for consultorio in Consultorio.objects.all().order_by("criado_em"):
        base = slugify(consultorio.nome)[:50] or "consultorio"
        slug, sufixo = base, 1
        while slug in usados or Consultorio.objects.filter(slug=slug).exclude(pk=consultorio.pk).exists():
            sufixo += 1
            slug = f"{base}-{sufixo}"
        usados.add(slug)
        consultorio.slug = slug
        consultorio.save(update_fields=["slug"])


class Migration(migrations.Migration):

    dependencies = [
        ('plataforma', '0004_pagamentoplataforma_vencimento'),
    ]

    operations = [
        migrations.AddField(
            model_name='consultorio',
            name='slug',
            field=models.SlugField(
                blank=True, default='', max_length=60,
                help_text='Usado no endereço de login e de agendamento do consultório, ex.: /nome-da-clinica/',
                verbose_name='link de acesso',
            ),
        ),
        migrations.RunPython(preencher_slugs, migrations.RunPython.noop),
    ]
