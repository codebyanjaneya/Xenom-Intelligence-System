"""Cleaning/normalisation and structural quality checks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.adapters.ingestion.base import clean_article_text, clean_text, fix_mojibake
from app.services.normalization import normalize_url
from app.services.quality_checks import validate_record

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


# ---- cleaning -------------------------------------------------------------------------------------------------
def test_script_and_style_bodies_are_removed_not_just_their_tags() -> None:
    html = "<p>Hello</p><script>alert('x')</script><style>.a{color:red}</style><noscript>enable js</noscript> world"
    assert clean_text(html) == "Hello world"


def test_html_comments_entities_and_block_tags() -> None:
    assert clean_text("A<!-- hidden -->B&amp;C<br>D</p><p>E") == "A B&C D E"


def test_google_news_summary_html_becomes_plain_text() -> None:
    raw = '<a href="https://news.google.com/rss/articles/CBMi?oc=5" target="_blank">Action Construction Equipment AGM</a>&nbsp;&nbsp;<font color="#6f6f6f">Kalkine India</font>'
    assert clean_text(raw) == "Action Construction Equipment AGM Kalkine India"


@pytest.mark.parametrize(
    "broken,fixed",
    [
        ("Modiâ€™s plan", "Modi’s plan"),
        ("â€œquoteâ€\x9d", "“quote”"),
        ("CafÃ©", "Café"),
        ("already fine ’ text", "already fine ’ text"),
        ("plain ascii", "plain ascii"),
    ],
)
def test_mojibake_is_repaired_without_touching_good_text(broken: str, fixed: str) -> None:
    assert fix_mojibake(broken) == fixed


def test_zero_width_and_control_characters_are_stripped() -> None:
    assert clean_text("a​b­c\x07d") == "abcd"


def test_article_text_drops_boilerplate_and_duplicate_paragraphs() -> None:
    paragraphs = [
        "Advertisement",
        "JCB India opened a new plant.",
        "JCB India opened a new plant.",
        "Read more: related story",
        "Subscribe to our newsletter",
        "It will employ 500 people.",
        "© 2026 Example Media. All rights reserved.",
    ]
    assert clean_article_text(paragraphs) == "JCB India opened a new plant. It will employ 500 people."


def test_useful_content_is_not_destroyed() -> None:
    text = clean_article_text(["Revenue rose 19.5% to ₹840.29 crore in Q1 FY27 (YoY).", "PAT: ₹119.49 crore."])
    assert "₹840.29 crore" in text and "19.5%" in text and "PAT" in text


# ---- URL normalisation / tracking cleanup -----------------------------------------------------------------------
def test_tracking_parameters_are_removed_and_query_sorted() -> None:
    url = "https://Example.com:443/a/b/?utm_source=x&b=2&fbclid=z&a=1&igshid=q&spm=s&_ga=9"
    assert normalize_url(url) == "https://example.com/a/b?a=1&b=2"


def test_content_params_survive() -> None:
    assert "id=42" in normalize_url("https://x.com/read?id=42&utm_medium=email")


# ---- structural quality checks -------------------------------------------------------------------------------------
def _ok(**over):
    base = dict(
        title="JCB India expands Pune plant capacity", url="https://example.com/a", published_at=NOW - timedelta(hours=3),
        text="Full article text goes here for the check.", require_date=True, now=NOW,
    )
    base.update(over)
    return validate_record(**base)


def test_valid_record_has_no_problems() -> None:
    assert _ok() == []


@pytest.mark.parametrize(
    "over,code",
    [
        ({"title": ""}, "EMPTY_TITLE"),
        ({"title": None}, "EMPTY_TITLE"),
        ({"title": "News"}, "TITLE_TOO_SHORT"),
        ({"title": "SHOCKING NEWS YOU WILL NOT BELIEVE TODAY"}, "ALL_CAPS_TITLE"),
        ({"title": "Best casino welcome bonus for new players"}, "SPAM_PATTERN"),
        ({"url": ""}, "EMPTY_URL"),
        ({"url": "ftp://example.com/x"}, "INVALID_URL"),
        ({"url": "https://localhost/x"}, "INVALID_URL"),
        ({"published_at": None}, "MISSING_DATE"),
        ({"published_at": NOW + timedelta(days=30)}, "FUTURE_DATE"),
        ({"published_at": datetime(1970, 1, 1, tzinfo=timezone.utc)}, "IMPLAUSIBLE_DATE"),
        ({"title": "Two words here", "text": ""}, "CONTENT_TOO_SHORT"),
    ],
)
def test_each_structural_problem_is_reported(over, code) -> None:
    assert code in _ok(**over)


def test_short_official_titles_pass_when_the_record_has_content_but_bare_fragments_do_not() -> None:
    row = "LinkedIn Workshop Mr. A. Speaker (Talent Acquisition) BBA batch aim: profile building"
    assert "TITLE_TOO_SHORT" not in _ok(title="LinkedIn Workshop", text=row)
    for fragment in ("#Dussehra", "Read More", "News"):
        assert "TITLE_TOO_SHORT" in _ok(title=fragment, text="")


def test_missing_date_allowed_when_not_required() -> None:
    assert "MISSING_DATE" not in _ok(published_at=None, require_date=False)


def test_naive_datetimes_are_treated_as_utc() -> None:
    assert "FUTURE_DATE" not in _ok(published_at=datetime(2026, 9, 20, 10, 0))
