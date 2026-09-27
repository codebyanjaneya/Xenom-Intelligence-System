"""Explicit event dates: only what the sentence states; never the publication date."""
from datetime import datetime, timezone

from app.services.stored_dates import extract_event_date

PUB = datetime(2026, 9, 16, tzinfo=timezone.utc)


def _d(sentence, pub=PUB):
    r = extract_event_date(sentence, pub)
    return (r.when.date().isoformat(), r.year_inferred) if r else None


def test_full_dates_in_several_formats():
    assert _d("JCB launched a backhoe on 15 September 2026.") == ("2026-09-15", False)
    assert _d("JCB launched a backhoe on September 3, 2026.") == ("2026-09-03", False)
    assert _d("On 2026-09-10 JCB launched a backhoe.") == ("2026-09-10", False)
    assert _d("National, 30 th April, 2026 : Definedge today announced the appointment.", datetime(2026, 5, 1, tzinfo=timezone.utc)) == ("2026-04-30", False)


def test_day_and_month_take_the_year_from_publication_and_are_flagged():
    assert _d("JCB unveiled 10 products at the expo on 15 September, with the company.") == ("2026-09-15", True)
    assert _d("JCB unveiled a product on 28 December.", datetime(2026, 1, 3, tzinfo=timezone.utc)) == ("2025-12-28", True)


def test_no_date_when_none_stated_or_no_publication_year_available():
    assert _d("JCB launched a backhoe today.") is None
    assert _d("JCB unveiled a product on 15 September.", None) is None            # cannot infer the year
    assert _d("JCB launched in September 2026.") is None                          # month only


def test_future_planned_or_ambiguous_dates_are_rejected():
    assert _d("JCB will launch a backhoe on 20 September.") is None               # future tense
    assert _d("JCB launched a backhoe on 30 September.") is None                  # after publication -> previous year, too old
    assert _d("JCB launched a backhoe on 20 September 2026.") is None             # explicit date after publication
    assert _d("JCB launched x on 3 March and 4 March 2026.") is None              # two distinct dates
    assert _d("JCB launched a backhoe on 15 March.") is None                      # beyond the 120-day lookback


def test_publication_metadata_is_never_an_event_date():
    assert _d("Published : 16 September 2026 JCB launched a backhoe.") is None
    assert _d("Updated on 3 September 2026 JCB launched a backhoe.") is None
    assert _d("Posted: 15 September JCB launched a backhoe.") is None


def test_modal_may_is_not_the_month():
    assert _d("Machines may 10 times faster, JCB launched a backhoe.") is None
    assert _d("JCB launched a backhoe on May 5.", datetime(2026, 5, 20, tzinfo=timezone.utc)) == ("2026-05-05", True)


def test_invalid_calendar_dates_are_ignored():
    assert _d("JCB launched a backhoe on 31 February 2026.") is None
