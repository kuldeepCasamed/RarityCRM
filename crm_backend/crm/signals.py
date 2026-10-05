from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Appointment, ContactLog, Lead
from .scoring import rescore


@receiver(post_save, sender=Lead)
def _lead_saved(sender, instance, **kwargs):
    rescore(instance)  # uses queryset.update(), so this cannot recurse


@receiver([post_save, post_delete], sender=ContactLog)
@receiver([post_save, post_delete], sender=Appointment)
def _child_changed(sender, instance, **kwargs):
    rescore(instance.lead_id)
