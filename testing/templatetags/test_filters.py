from django import template

register = template.Library()


@register.filter
def index(seq, i):
    """Return seq[i]. Works for strings and lists."""
    try:
        return seq[int(i)]
    except (IndexError, TypeError, ValueError):
        return ""
