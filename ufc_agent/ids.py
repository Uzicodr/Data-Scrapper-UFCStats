"""Row ids and slugs shared by the seed script and the agent, so both produce the same values."""
import re
import uuid

from ufc_agent.normalize import name_key

SOURCE = "ufcstats"
UUID_NAMESPACE = uuid.UUID("6f1d3c52-6a8e-4d55-9a57-3b8f4c0e2a11")


def row_uuid(kind, source_id):
    """Deterministic id for a fighter, event, fight or ranking row from its ufcstats source id."""
    return str(uuid.uuid5(UUID_NAMESPACE, f"{SOURCE}:{kind}:{source_id}"))


def slugify(text):
    return "-".join(name_key(text).split())


def unique_slug(base, suffix, taken):
    """base, or base-suffix when base is already in taken. Adds the result to taken."""
    slug = base or suffix
    if slug in taken:
        slug = f"{base}-{suffix}"
    taken.add(slug)
    return slug


def event_slug_base(name, event_date):
    """Matches Cito's style: 'ufc-332', 'ufc-fight-night-october-11-2026'."""
    numbered = re.match(r"UFC (\d+)\b", name)
    if numbered:
        return f"ufc-{numbered.group(1)}"
    if name.startswith("UFC Fight Night"):
        return f"ufc-fight-night-{event_date.strftime('%B-%d-%Y').lower()}"
    return slugify(name)
