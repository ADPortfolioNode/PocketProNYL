"""Suggestion-date vs expected-draw-date labels.

Every suggestion carries two dates, and they answer different questions:

- ``suggestion_date``: the lottery-timezone date the suggestion was
  served. This is the date the suggestion belongs to.
- ``expected_draw_date`` (+ session): the draw the suggestion is
  expected to be judged against — the next scheduled draw strictly
  after it was served. Accuracy is the comparison of the suggestion
  against that draw (or, when a draw is skipped, the draw actually
  graded against, reported separately as ``actual_draw_date``).

Ledger entries already store their expected draw (``target_draw_date``,
set from the schedule at serve time, or the requested date). Experiment
records do NOT: their ``next_draw_date`` is the serve date mislabeled.
So the rule here is: a stored ledger target wins; an experiment's
expected draw is computed from the schedule, unless a newer experiment
record already carries an explicit ``expected_draw_date`` (saved since
this labeling landed). Labeling never fails grading: on any error the
labels are None and the row still grades.
"""

from __future__ import annotations

from datetime import datetime, timezone


def parse_served(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def suggestion_date(served_at) -> str | None:
    """Lottery-timezone date a suggestion was served, or None."""
    served = parse_served(served_at)
    if served is None:
        return None
    try:
        from services.draw_schedule import lottery_timezone

        return served.astimezone(lottery_timezone()).date().isoformat()
    except Exception:
        return served.date().isoformat()


def expected_draw(game: str, served_at, *, target_date=None,
                  target_session=None, target_basis=None,
                  expected_date=None, expected_session=None):
    """(expected_draw_date, expected_draw_session) for a suggestion.

    Stored ledger targets win (they were fixed at serve time). For
    experiments, a stored explicit expected date wins; otherwise the
    next scheduled draw after serving is computed — never the record's
    ``next_draw_date``, which is the serve date, not a draw date.
    """
    if target_basis == "experiment":
        if expected_date:
            return str(expected_date)[:10], expected_session
    elif target_date:
        return str(target_date)[:10], target_session
    served = parse_served(served_at)
    if served is None:
        return None, None
    try:
        from services.draw_schedule import next_draw

        nxt = next_draw(game, served)
    except Exception:
        return None, None
    return nxt.get("draw_date"), nxt.get("draw_session")
