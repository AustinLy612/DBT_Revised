"""Translate stored choice labels without changing their database values."""

from django import template
from django.utils.translation import get_language, gettext


register = template.Library()


@register.filter
def localize_label(value):
    return gettext(str(value)) if value is not None else ""


@register.filter
def localize_list(values):
    if not values:
        return ""
    separator = ", " if (get_language() or "").startswith("en") else "、"
    return separator.join(gettext(str(value)) for value in values)
