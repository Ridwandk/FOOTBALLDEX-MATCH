"""
Two ways to bulk-assign footballer positions, both backed by the same model:

- AssignPositionActionForm: the quick changelist action (select rows -> pick one
  position -> confirm). Good for touch-ups on a filtered/searched selection.
- BulkPositionForm: the dedicated "Bulk-assign positions" page. One tab per
  position, each a searchable two-pane picker; submitting resyncs everything
  in one go. Good for going through your whole roster position by position.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from django import forms
from django.contrib.admin.widgets import FilteredSelectMultiple
from django_admin_action_forms import AdminActionForm

from .models import Ball, Position


class AssignPositionActionForm(AdminActionForm):
    position = forms.ChoiceField(
        label="Position",
        choices=[(code, label) for code, label in Position.choices if code != Position.NA],
    )

    class Meta:
        list_objects = True
        help_text = "This position will be applied to every footballer selected below."
        confirm_button_text = "Assign position"


class BallMultipleChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj: Ball) -> str:
        current = "unassigned" if obj.position == Position.NA else obj.get_position_display()
        return f"{obj.country} (currently {current})"


class BulkPositionForm(forms.Form):
    """
    One tab per position (rendered as one fieldset each; the template shows
    only one at a time and switches with plain CSS/JS, no page reload).
    Submitting is a full resync: a ball in exactly one box gets that position,
    a ball in none of them gets reset to N/A.
    """

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        balls_qs = Ball.objects.only("id", "country", "position").order_by("country")
        balls = list(balls_qs)  # evaluate once, reused for every field's initial value

        for code, label in Position.choices:
            if code == Position.NA:
                continue
            field_name = self._field_name(code)
            initial = [b.pk for b in balls if b.position == code]
            self.fields[field_name] = BallMultipleChoiceField(
                queryset=balls_qs,
                required=False,
                initial=initial,
                label=label,
                widget=FilteredSelectMultiple(label, is_stacked=False),
            )

    @staticmethod
    def _field_name(code: str) -> str:
        return f"position_{code}"

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        picks: defaultdict[int, list[str]] = defaultdict(list)

        for code, _label in Position.choices:
            if code == Position.NA:
                continue
            for ball in cleaned.get(self._field_name(code)) or []:
                picks[ball.pk].append(code)

        conflicts = sorted(
            {
                str(Ball.objects.get(pk=ball_id))
                for ball_id, codes in picks.items()
                if len(codes) > 1
            }
        )
        if conflicts:
            raise forms.ValidationError(
                "These players were selected under more than one position tab - "
                f"a player can only have one: {', '.join(conflicts)}"
            )
        return cleaned

    def save(self) -> tuple[int, int]:
        """Apply the resync. Returns (assigned_count, cleared_count)."""
        assigned: dict[int, str] = {}
        for code, _label in Position.choices:
            if code == Position.NA:
                continue
            for ball in self.cleaned_data.get(self._field_name(code)) or []:
                assigned[ball.pk] = code

        assigned_count = 0
        for ball_id, code in assigned.items():
            assigned_count += (
                Ball.objects.filter(pk=ball_id).exclude(position=code).update(position=code)
            )

        cleared_count = (
            Ball.objects.exclude(pk__in=assigned.keys())
            .exclude(position=Position.NA)
            .update(position=Position.NA)
        )
        return assigned_count, cleared_count
