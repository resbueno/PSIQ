from django.db import migrations, models


def renomear_origem(apps, schema_editor):
    EventoExterno = apps.get_model("calendarios", "EventoExterno")
    EventoExterno.objects.filter(origem="psiq").update(origem="meupsiq")


def reverter_origem(apps, schema_editor):
    EventoExterno = apps.get_model("calendarios", "EventoExterno")
    EventoExterno.objects.filter(origem="meupsiq").update(origem="psiq")


class Migration(migrations.Migration):

    dependencies = [
        ('calendarios', '0002_rls'),
    ]

    operations = [
        migrations.RunPython(renomear_origem, reverter_origem),
        migrations.AlterField(
            model_name='eventoexterno',
            name='origem',
            field=models.CharField(choices=[('meupsiq', 'Criado pelo MeuPSIQ'), ('externo', 'Compromisso externo')], max_length=8),
        ),
    ]
