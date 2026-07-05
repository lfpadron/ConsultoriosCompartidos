from django.core.management import call_command


def test_django_project_check_passes() -> None:
    call_command("check", verbosity=0)


def test_migrations_are_in_sync(db: object) -> None:
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)
