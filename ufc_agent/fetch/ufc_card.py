"""Compact fight-card digest of a ufc.com event page.

ufc.com updates its event pages during the event, one result at a time, so live_event watches them.
The flattened page text separates each "Win"/"Loss" label from its fighter, so this reads the
fight listing markup instead and returns one line per bout. The digest is both the change signal
(its hash) and what the live agent reads.
"""
import datetime
import hashlib

from selectolax.lexbor import LexborHTMLParser


def _text(node):
    return " ".join(node.text(separator=" ").split()) if node is not None else ""


def _outcome(corner):
    """'Win', 'Loss', 'Draw', 'NC' or '' for one corner."""
    for node in corner.css("[class*='c-listing-fight__outcome--']") if corner is not None else []:
        text = _text(node)
        if text:
            return text
    return ""


def parse_card(html):
    """Return a list of bouts: {fight_id, weight_class, red, blue, red_outcome, blue_outcome, round, time, method}."""
    tree = LexborHTMLParser(html)
    bouts = []
    for fight in tree.css(".c-listing-fight"):
        results = {}
        for result in fight.css(".c-listing-fight__results--desktop .c-listing-fight__result"):
            label = _text(result.css_first(".c-listing-fight__result-label"))
            value = _text(result.css_first(".c-listing-fight__result-text"))
            if label and value:
                results[label.lower()] = value
        bouts.append({
            "fight_id": fight.attributes.get("data-fmid") or "",
            "weight_class": _text(fight.css_first(".c-listing-fight__class--desktop .c-listing-fight__class-text"))
                            or _text(fight.css_first(".c-listing-fight__class-text")),
            "red": _text(fight.css_first(".c-listing-fight__corner-name--red")),
            "blue": _text(fight.css_first(".c-listing-fight__corner-name--blue")),
            "red_outcome": _outcome(fight.css_first(".c-listing-fight__corner-body--red")),
            "blue_outcome": _outcome(fight.css_first(".c-listing-fight__corner-body--blue")),
            "round": results.get("round", ""),
            "time": results.get("time", ""),
            "method": results.get("method", ""),
        })
    return bouts


def bout_line(bout):
    def corner(name, outcome):
        return f"{name} [{outcome}]" if outcome else name

    line = f"{bout['weight_class']}: {corner(bout['red'], bout['red_outcome'])} vs {corner(bout['blue'], bout['blue_outcome'])}"
    if bout["method"]:
        line += f" | {bout['method']} | round {bout['round']} | {bout['time']}"
    return line


def has_result(bout):
    return bool(bout["method"] and (bout["red_outcome"] or bout["blue_outcome"]))


def card_fingerprint(bouts):
    """Hash of every bout's names and result; changes when a result is posted, not on odds or timers."""
    lines = sorted(f"{b['fight_id']}|{bout_line(b)}" for b in bouts)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def event_start(html):
    """Earliest card-section start time on a ufc.com event page, as a UTC datetime, or None.

    Each section ("Early Prelims", "Prelims", "Main Card") carries a Unix timestamp in
    .c-event-fight-card-broadcaster__time[data-timestamp].
    """
    tree = LexborHTMLParser(html)
    stamps = [int(node.attributes["data-timestamp"])
              for node in tree.css(".c-event-fight-card-broadcaster__time[data-timestamp]")
              if (node.attributes.get("data-timestamp") or "").isdigit()]
    return datetime.datetime.fromtimestamp(min(stamps), datetime.timezone.utc) if stamps else None
