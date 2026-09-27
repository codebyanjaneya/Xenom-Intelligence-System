"""Adapters: table rows (ISM/VIPS formats), link listings, Definedge JSON API, Google News feed, robots."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import httpx
import pytest

from app.adapters.ingestion.base import AdapterError
from app.adapters.ingestion.google_news import GoogleNewsAdapter
from app.adapters.ingestion.site_adapters import (
    DefinedgeBlogAdapter,
    LinkListingAdapter,
    TableRowsAdapter,
    _ROBOTS_CACHE,
    parse_loose_date,
    robots_allowed,
)

SRC = uuid.uuid4()
NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)

ISM_TABLE = """
<table><thead><tr><th>S.No</th><th>Title</th><th>Date</th><th>Action</th></tr></thead><tbody>
<tr><td>1.</td><td>Union Minister Ashwini Vaishnaw addressed media on Semicon 2.0, Notifications for all 6 Pillars of Semicon 2.0 issued</td>
    <td>31 Aug 2026</td><td><a href="https://d2p5j06zete1i7.cloudfront.net/Cms/admin/PressRelease/1788180277.pdf">View</a></td></tr>
<tr><td>2.</td><td>India's Biggest Semiconductor Event to be held in Yashobhoomi, New Delhi from 17th - 19th September 2026</td>
    <td>18 Aug 2026</td><td><a href="/files/event.pdf">View</a></td></tr>
<tr><td></td><td></td><td></td><td></td></tr>
</tbody></table>"""

VIPS_ACTIVITIES = """
<table><tr><th>S.No</th><th>Event Name</th><th>Date</th><th>Speaker</th><th>Benefitting Year</th><th>Aim</th></tr>
<tr><td>1</td><td>Industry Academia Connect (IAC)</td><td>8th April 2026</td><td>Mr. Ajay Sharma (President: Supply Chain Management - Usha International Ltd.)</td><td>MBA</td><td></td></tr>
<tr><td>2</td><td>Mock Personal Interviews</td><td>23rd February 2026</td><td>Ms. Isha Arora (Finance Associate @ BCG)</td><td>BBA</td><td></td></tr></table>"""

VIPS_NOTICES = """
<table><tr><th>Publish Date</th><th>Description</th><th></th></tr>
<tr><td>Tue Jun 13 2023</td><td>End Term Examination (June 2023)</td><td><a href="https://firebasestorage.googleapis.com/v0/b/x/o/Announcement%2Fa.pdf?alt=media">View</a></td></tr></table>"""


def test_ism_press_release_rows_become_dated_documents_with_pdf_links() -> None:
    docs = TableRowsAdapter().parse_html(ISM_TABLE, "https://www.ism.gov.in/notifications/press-release", source_id=SRC, retrieved_at=NOW)
    assert len(docs) == 2, "blank rows are skipped"
    first = docs[0]
    assert first.title.startswith("Union Minister Ashwini Vaishnaw addressed media on Semicon 2.0")
    assert first.published_at == datetime(2026, 8, 31, tzinfo=timezone.utc)
    assert first.canonical_url.endswith("1788180277.pdf")
    assert docs[1].canonical_url == "https://www.ism.gov.in/files/event.pdf"
    assert first.metadata["adapter"] == "table_rows" and first.metadata["publisher_domain"] == "www.ism.gov.in"


def test_vips_activity_rows_without_links_get_unique_stable_urls() -> None:
    url = "https://www.vips.edu/vsbs/Placements/Recent_Activities"
    a = TableRowsAdapter().parse_html(VIPS_ACTIVITIES, url, source_id=SRC, retrieved_at=NOW)
    b = TableRowsAdapter().parse_html(VIPS_ACTIVITIES, url, source_id=SRC, retrieved_at=NOW)
    assert [d.title for d in a] == ["Industry Academia Connect (IAC)", "Mock Personal Interviews"]
    assert a[0].published_at == datetime(2026, 4, 8, tzinfo=timezone.utc)
    assert len({d.canonical_url for d in a}) == 2 and [d.canonical_url for d in a] == [d.canonical_url for d in b]
    assert "Ajay Sharma" in a[0].normalized_content


def test_vips_notice_dates_in_weekday_format_parse() -> None:
    (doc,) = TableRowsAdapter().parse_html(VIPS_NOTICES, "https://vips.edu/Notices", source_id=SRC, retrieved_at=NOW)
    assert doc.title == "End Term Examination (June 2023)"
    assert doc.published_at == datetime(2023, 6, 13, tzinfo=timezone.utc)


def test_page_without_a_table_is_a_reported_failure_not_zero_docs() -> None:
    with pytest.raises(AdapterError, match="no table rows"):
        TableRowsAdapter().parse_html("<html><body><p>hi</p></body></html>", "https://x.com/", source_id=SRC, retrieved_at=NOW)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("31 Aug 2026", (2026, 8, 31)),
        ("8th April 2026", (2026, 4, 8)),
        ("Tue Jun 13 2023", (2023, 6, 13)),
        ("2026-09-18T00:00:00.000Z", (2026, 9, 18)),
        ("2026-08-10T00:00:00.000Z", (2026, 8, 10)),   # ISO must not be day-first swapped (was 8 Oct)
        ("2026-07-10", (2026, 7, 10)),
        ("Updated: 30-04-2026 15:55 IST", (2026, 4, 30)),
        ("no date here", None),
        ("", None),
    ],
)
def test_loose_date_parsing(text: str, expected) -> None:
    got = parse_loose_date(text)
    assert (got.year, got.month, got.day) == expected if expected else got is None


def test_listing_link_extraction_filters_and_dedupes() -> None:
    html = """
    <a href="/tech/technology/fujifilm-bets-on-semiconductors/articleshow/134350286.cms">Fujifilm bets on semiconductors to drive India growth</a>
    <a href="/tech/technology/fujifilm-bets-on-semiconductors/articleshow/134350286.cms#comments"></a>
    <a href="/news/politics">Politics</a><a href="/x/articleshow/1.pdf">pdf</a>
    <a href="https://other.com/a/articleshow/9.cms">Other host article</a>"""
    links = LinkListingAdapter(link_pattern=r"/articleshow/").extract_links(html, "https://economictimes.indiatimes.com/topic/x")
    assert list(links) == [
        "https://economictimes.indiatimes.com/tech/technology/fujifilm-bets-on-semiconductors/articleshow/134350286.cms",
        "https://other.com/a/articleshow/9.cms",
    ]
    assert links["https://economictimes.indiatimes.com/tech/technology/fujifilm-bets-on-semiconductors/articleshow/134350286.cms"].startswith("Fujifilm")


def test_jcb_news_link_pattern_matches_dated_paths_only() -> None:
    html = '<a href="/en-IN/explore/insight/news/2026/08/hydrogen-jcb-hydromax">Hydromax</a><a href="/en-IN/explore/insight/news/">News</a>'
    links = LinkListingAdapter(link_pattern=r"/insight/news/\d{4}/").extract_links(html, "https://www.jcb.com/en-IN/news")
    assert list(links) == ["https://www.jcb.com/en-IN/explore/insight/news/2026/08/hydrogen-jcb-hydromax"]


def test_definedge_api_posts_become_documents_with_absolute_images() -> None:
    posts = [
        {"postId": 359, "title": "Nifty vs Mega IPOs: What NSE IPO Means for India’s Next Bull Run", "slug": "nifty-vs-mega-ipos",
         "excerpt": "Explore how Nifty 50 performed after mega IPOs.", "ogImage": "/uploads/1789710617943_nse.jpg",
         "publishDate": "2026-09-18T00:00:00.000Z", "author": "Brijesh Bhatia"},
        {"postId": 360, "title": "", "slug": "no-title"},
    ]
    adapter = DefinedgeBlogAdapter()
    docs = adapter.parse_posts(posts, source_id=SRC, retrieved_at=NOW)
    assert len(docs) == 1 and adapter.errors == ["post 360 missing slug/title"]
    doc = docs[0]
    assert doc.canonical_url == "https://www.definedgesecurities.com/blog/nifty-vs-mega-ipos"
    assert doc.published_at == datetime(2026, 9, 18, tzinfo=timezone.utc)
    assert doc.metadata["image_candidates"] == [["rss_media", "https://www.definedgesecurities.com/uploads/1789710617943_nse.jpg"]]


GOOGLE_FEED = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>Action Construction Equipment Share Price near 52-week high - Univest</title>
<link>https://news.google.com/rss/articles/CBMipgFBVV95cUxQbmZY?oc=5</link><guid>CBMipgFBVV95cUxQbmZY</guid>
<pubDate>Thu, 17 Sep 2026 07:03:00 GMT</pubDate>
<description>&lt;a href="https://news.google.com/rss/articles/CBMipgFBVV95cUxQbmZY?oc=5" target="_blank"&gt;Action Construction Equipment Share Price near 52-week high&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font color="#6f6f6f"&gt;Univest&lt;/font&gt;</description>
<source url="https://univest.in">Univest</source></item>
<item><title></title><link>https://news.google.com/rss/articles/x</link></item></channel></rss>"""


def test_google_feed_items_carry_publisher_domain_and_clean_text() -> None:
    docs = GoogleNewsAdapter()._parse_feed(GOOGLE_FEED, str(SRC), NOW, "Action Construction Equipment")
    assert len(docs) == 1
    doc = docs[0]
    assert doc.normalized_content == "Action Construction Equipment Share Price near 52-week high Univest"
    assert "<" not in doc.normalized_content
    assert doc.metadata["publisher_domain"] == "univest.in" and doc.metadata["source_name"] == "Univest"
    assert doc.metadata["wrapper_url"] is True
    assert doc.published_at == datetime(2026, 9, 17, 7, 3, tzinfo=timezone.utc)


def test_malformed_google_feed_is_an_adapter_error() -> None:
    with pytest.raises(AdapterError):
        GoogleNewsAdapter()._parse_feed("<rss><channel><item>", str(SRC), NOW, "q")


# ---- robots.txt ----------------------------------------------------------------------------------------------------
def _robots_client(status: int, body: str = "") -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status, text=body)))


def test_robots_disallow_is_honoured_and_allow_paths_pass() -> None:
    _ROBOTS_CACHE.clear()

    async def run():
        async with _robots_client(200, "User-agent: *\nDisallow: /company/\n") as c:
            return (
                await robots_allowed("https://blocked.example/company/vikas-group/", c),
                await robots_allowed("https://blocked.example/news/story", c),
            )

    assert asyncio.run(run()) == (False, True)


def test_missing_robots_allows_but_server_error_disallows() -> None:
    _ROBOTS_CACHE.clear()

    async def run():
        async with _robots_client(404) as c404:
            allowed = await robots_allowed("https://norobots.example/a", c404)
        async with _robots_client(503) as c503:
            denied = await robots_allowed("https://broken.example/a", c503)
        return allowed, denied

    assert asyncio.run(run()) == (True, False)
