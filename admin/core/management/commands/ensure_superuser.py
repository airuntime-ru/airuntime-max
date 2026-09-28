from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create or update Django admin superuser"

    def handle(self, *args, **options):
        User = get_user_model()
        import os

        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@airuntime.ru")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "admin123")

        user, created = User.objects.get_or_create(
            email=email, defaults={"is_staff": True, "is_superuser": True}
        )
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.set_password(password)
        user.save()

        action = "created" if created else "updated"
        self.stdout.write(self.style.SUCCESS(f"Superuser {email} {action}"))
