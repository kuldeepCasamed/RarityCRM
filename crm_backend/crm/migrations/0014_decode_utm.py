from urllib.parse import unquote_plus

from django.db import migrations

FIELDS = ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content")


def clean(v):
    for _ in range(4):
        d = unquote_plus(v)
        if d == v:
            break
        v = d
    return v


def decode(apps, schema_editor):
    for name in ("Lead", "ContactClick"):
        M = apps.get_model("crm", name)
        for obj in M.objects.all().iterator():
            changed = {}
            for f in FIELDS:
                v = getattr(obj, f, None)
                if v and (("%" in v) or ("+" in v)):
                    nv = clean(v)
                    if nv != v:
                        changed[f] = nv
            if changed:
                M.objects.filter(pk=obj.pk).update(**changed)


class Migration(migrations.Migration):
    dependencies = [("crm", "0013_contact_click")]
    operations = [migrations.RunPython(decode, migrations.RunPython.noop)]
