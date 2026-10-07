from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('plataforma', '0005_consultorio_slug'),
    ]

    operations = [
        migrations.AlterField(
            model_name='consultorio',
            name='slug',
            field=models.SlugField(
                max_length=60, unique=True,
                help_text='Usado no endereço de login e de agendamento do consultório, ex.: /nome-da-clinica/',
                verbose_name='link de acesso',
            ),
        ),
    ]
