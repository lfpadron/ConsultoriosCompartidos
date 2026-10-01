"""Periodic finance tasks."""

from celery import shared_task

from apps.finance.services.account_statement_service import (
    generate_due_account_statements,
)


@shared_task(name="finance.generate_due_account_statements")
def generate_due_account_statements_task() -> int:
    """Emit owner and tenant statements whose closing date has elapsed."""

    return len(generate_due_account_statements())
