"""Forms for simulated integration workflows."""

from typing import Any

from django import forms


class AccessRevokeForm(forms.Form):
    reason = forms.CharField(
        label="Motivo de revocación",
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["reason"].widget.attrs["class"] = "form-control"
