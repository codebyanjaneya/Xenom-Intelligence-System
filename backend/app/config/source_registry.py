"""Client source registry: every supplied source, how it is ingested, and why.

A registered source is NOT trusted content: everything fetched from it still goes
through the client-relevance stage. ``client`` only says which client the source
was registered for (used for reporting and source-trust priors).

Fetch modes
-----------
rss                 public RSS/Atom feed (often a whole-publication feed; relevance filters it)
seed_article        one specific supplied article URL
link_listing        listing page -> follow article links (``render=True`` for JS pages)
table_rows          notice / press-release tables, one document per row
definedge_blog_api  Definedge blog's own public JSON API
google_news         Google News RSS search built from ``clients.py`` queries
reference_only      registered for provenance but NOT crawled (see ``note``)

Reference-only rationale (verified 2026-09-20 with polite requests):
* LinkedIn: robots.txt disallows automated access; scraping is prohibited by its terms.
* Shiksha / Collegedunia: HTTP 403 "Access Denied" for automated clients; not bypassed.
* AmbitionBox reviews, Justdial, corporate home pages: profile/review/directory pages with
  no article stream (employee reviews, business listings, static company pages).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

FetchMode = Literal[
    "rss", "seed_article", "link_listing", "table_rows", "definedge_blog_api", "google_news", "reference_only",
    "arxiv_api", "huggingface_hub", "openalex_api", "semantic_scholar_api", "crossref_api",
]


@dataclass(frozen=True)
class SourceSpec:
    key: str
    client: str | None            # ENTITY_CONFIGS key (VIKAS/VIPS/Definedge/ISM/JCB/ACE) or None for cross-client
    name: str
    url: str                      # unique canonical URL stored in ``sources``
    fetch_mode: FetchMode
    db_source_type: str = "HTML"  # sources.source_type CHECK constraint values
    tier: str = "TIER_2"
    source_kind: str | None = None  # official | directory | reference | research | None (news)
    feed_url: str | None = None
    require_date: bool = True
    render: bool = False
    link_pattern: str | None = None
    note: str = ""
    query: str | None = None       # research APIs (arxiv_api / huggingface_hub / openalex_api): the search expression
    max_results: int = 25

    @property
    def enabled(self) -> bool:
        return self.fetch_mode != "reference_only"


def _ref(key: str, client: str, name: str, url: str, note: str, kind: str = "reference") -> SourceSpec:
    return SourceSpec(
        key=key, client=client, name=f"{name} (reference only)", url=url, fetch_mode="reference_only",
        db_source_type="OTHER_PUBLIC", tier="TIER_3", source_kind=kind, note=note,
    )


def _seed(key: str, client: str, name: str, url: str, tier: str = "TIER_2") -> SourceSpec:
    # Seed articles are supplied on purpose; some publishers (e.g. Motor India) print no date on the page.
    return SourceSpec(key=key, client=client, name=name, url=url, fetch_mode="seed_article", tier=tier, require_date=False)


LINKEDIN_NOTE = "robots.txt disallows automated access and LinkedIn terms prohibit scraping; kept as a provenance reference"
BLOCKED_NOTE = "HTTP 403 Access Denied to automated clients; not bypassed"
PROFILE_NOTE = "profile/review/directory page with no article stream"

SOURCE_REGISTRY: list[SourceSpec] = [
    # ---------------------------------------------------------- AI / TECHNOLOGY COMPANIES
    SourceSpec(
        key="openai-news", client=None, name="OpenAI News", url="https://openai.com/news/rss.xml",
        fetch_mode="rss", db_source_type="RSS", tier="TIER_1", source_kind="technology",
        feed_url="https://openai.com/news/rss.xml",
        note="official OpenAI announcements and research news; cross-client technology evidence",
    ),
    SourceSpec(
        key="google-ai-blog", client=None, name="Google AI Blog", url="https://blog.google/technology/ai/rss/",
        fetch_mode="rss", db_source_type="RSS", tier="TIER_1", source_kind="technology",
        feed_url="https://blog.google/technology/ai/rss/",
        note="official Google AI and machine-learning technology news; cross-client technology evidence",
    ),
    SourceSpec(
        key="nvidia-deep-learning", client=None, name="NVIDIA Deep Learning Blog",
        url="https://blogs.nvidia.com/blog/category/deep-learning/feed/", fetch_mode="rss",
        db_source_type="RSS", tier="TIER_1", source_kind="technology",
        feed_url="https://blogs.nvidia.com/blog/category/deep-learning/feed/",
        note="official NVIDIA AI, accelerated computing and deep-learning announcements",
    ),
    SourceSpec(
        key="hugging-face-blog", client=None, name="Hugging Face Blog", url="https://huggingface.co/blog/feed.xml",
        fetch_mode="rss", db_source_type="RSS", tier="TIER_1", source_kind="technology",
        feed_url="https://huggingface.co/blog/feed.xml",
        note="official open-model and machine-learning ecosystem announcements",
    ),
    SourceSpec(
        key="crossref-semiconductor-research", client=None, name="Crossref semiconductor research",
        url="https://api.crossref.org/works?query.title=semiconductor", fetch_mode="crossref_api",
        db_source_type="RESEARCH_API", tier="TIER_1", source_kind="research", require_date=True,
        query="semiconductor", max_results=15, note="No-key research fallback when OpenAlex/arXiv/Semantic Scholar are unavailable",
    ),
    SourceSpec(
        key="openalex-ai-research", client=None, name="OpenAlex artificial intelligence research",
        url="https://api.openalex.org/works?search=artificial%20intelligence", fetch_mode="openalex_api",
        db_source_type="RESEARCH_API", tier="TIER_1", source_kind="research", require_date=True,
        query="artificial intelligence", max_results=30, note="Targeted public AI research papers with authors and affiliations when stated",
    ),
    SourceSpec(
        key="openalex-robotics-research", client=None, name="OpenAlex robotics research",
        url="https://api.openalex.org/works?search=robotics", fetch_mode="openalex_api",
        db_source_type="RESEARCH_API", tier="TIER_1", source_kind="research", require_date=True,
        query="robotics", max_results=30, note="Targeted public robotics research papers with authors and affiliations when stated",
    ),
    SourceSpec(
        key="openalex-computer-vision-research", client=None, name="OpenAlex computer vision research",
        url="https://api.openalex.org/works?search=computer%20vision", fetch_mode="openalex_api",
        db_source_type="RESEARCH_API", tier="TIER_1", source_kind="research", require_date=True,
        query="computer vision", max_results=30, note="Targeted public computer-vision research papers with authors and affiliations when stated",
    ),
    # ------------------------------------------------------------------ VIKAS
    _ref("vikas-official", "VIKAS", "Vikas Group official site", "https://www.vikasgroup.in/",
         "static corporate pages (about, milestones, products); no news stream", kind="official"),
    _ref("vikas-linkedin", "VIKAS", "Vikas Group LinkedIn", "https://www.linkedin.com/company/vikas-group/", LINKEDIN_NOTE),
    _ref("vikas-ambitionbox", "VIKAS", "Vikas Group AmbitionBox overview",
         "https://www.ambitionbox.com/overview/vikas-group-overview", PROFILE_NOTE + " (employer profile)"),
    _ref("vikas-ambitionbox-reviews", "VIKAS", "Vikas Group AmbitionBox reviews",
         "https://www.ambitionbox.com/reviews/vikas-group-reviews/faridabad-location",
         PROFILE_NOTE + " (employee reviews are not news)"),
    _seed("vikas-et-pranav-ev", "VIKAS", "Economic Times: Pranav Vikas EV and hybrid focus",
          "https://economictimes.indiatimes.com/pranav-vikas-group-to-focus-on-ev-and-hybrid-vehicle-technology/articleshow/62938149.cms"),
    _seed("vikas-motorindia-hvac", "VIKAS", "Motor India: Vikas Group bus HVAC solutions",
          "https://www.motorindiaonline.in/vikas-group-offering-value-added-hvac-solutions-for-bus-industry/"),
    SourceSpec(
        key="motorindia-feed", client=None, name="Motor India (RSS)", url="https://www.motorindiaonline.in/feed/",
        fetch_mode="rss", db_source_type="RSS", tier="TIER_2", feed_url="https://www.motorindiaonline.in/feed/",
        note="whole-publication automotive feed; only client-relevant items survive the relevance stage",
    ),
    # ------------------------------------------------------------------- VIPS
    _ref("vips-official-home", "VIPS", "VIPS official home", "https://vips.edu/",
         "single-page app shell; news lives on the Notices and Recent Activities pages registered below", kind="official"),
    _ref("vips-linkedin", "VIPS", "VIPS LinkedIn",
         "https://www.linkedin.com/school/vivekananda-institute-of-professional-studies/?originalSubdomain=in", LINKEDIN_NOTE),
    _ref("vips-justdial", "VIPS", "VIPS Justdial listing",
         "https://www.justdial.com/Delhi/Vivekananda-Institute-of-Professional-Studies-Au-Block-Near-Delhi-School-Of-Business-Prashant-Vihar/011PXX11-XX11-111206155657-N6I2_BZDET",
         PROFILE_NOTE + " (business directory)", kind="directory"),
    SourceSpec(
        key="vips-notices", client="VIPS", name="VIPS official notices", url="https://vips.edu/Notices",
        fetch_mode="table_rows", render=True, db_source_type="OFFICIAL_NEWSROOM", tier="TIER_1", source_kind="official",
        require_date=False, note="single-page app; rendered with Playwright, one document per notice row",
    ),
    SourceSpec(
        key="vips-placement-activities", client="VIPS", name="VIPS placement recent activities",
        url="https://www.vips.edu/vsbs/Placements/Recent_Activities", fetch_mode="table_rows", render=True,
        db_source_type="OFFICIAL_NEWSROOM", tier="TIER_1", source_kind="official", require_date=False,
        note="single-page app; rendered with Playwright, one document per activity row",
    ),
    SourceSpec(
        key="vips-college360", client="VIPS", name="College360 VIPS news",
        url="https://college360.co.in/college/Vivekananda-Institute-of-Professional-Studies-VIPS-New-Delhi-3708/news",
        fetch_mode="link_listing", render=True, db_source_type="OTHER_PUBLIC", tier="TIER_3", source_kind="directory",
        require_date=False, link_pattern=r"college360\.co\.in/news/",
        note="JS-rendered education-news feed that is NOT VIPS-specific; the relevance stage filters it hard",
    ),
    _ref("vips-shiksha", "VIPS", "Shiksha VIPS news",
         "https://www.shiksha.com/college/vips-vivekananda-institute-of-professional-studies-pitampura-delhi-28593/news-articles",
         BLOCKED_NOTE, kind="directory"),
    _ref("vips-collegedunia", "VIPS", "Collegedunia VIPS news",
         "https://collegedunia.com/college/9185-vivekananda-institute-of-professional-studies-vips-new-delhi/news",
         BLOCKED_NOTE, kind="directory"),
    # -------------------------------------------------------------- DEFINEDGE
    _ref("definedge-official", "Definedge", "Definedge official site", "https://www.definedgesecurities.com/",
         "corporate/product home page; news lives on the blog and forum announcements", kind="official"),
    SourceSpec(
        key="definedge-blog", client="Definedge", name="Definedge blog (API)",
        url="https://www.definedgesecurities.com/blog", fetch_mode="definedge_blog_api",
        db_source_type="OFFICIAL_NEWSROOM", tier="TIER_1", source_kind="official",
        note="Next.js blog; posts come from its own public JSON API",
    ),
    SourceSpec(
        key="definedge-forum-announcements", client="Definedge", name="Definedge forum announcements",
        url="https://forum.definedgesecurities.com/category/1/announcements", fetch_mode="link_listing",
        db_source_type="OFFICIAL_NEWSROOM", tier="TIER_1", source_kind="official", require_date=False,
        link_pattern=r"/topic/\d+/", note="its RSS feed is malformed XML, so topics are read from the listing",
    ),
    _seed("definedge-devdiscourse", "Definedge", "Devdiscourse: Hemant Luthra takes helm at Definedge",
          "https://www.devdiscourse.com/article/technology/3891925-industry-veteran-hemant-luthra-takes-helm-at-punes-definedge-securities"),
    _seed("definedge-finsmes", "Definedge", "FinSMEs: Definedge raises funding round",
          "https://www.finsmes.com/2025/11/definedge-raises-funding-round.html"),
    _seed("definedge-pim", "Definedge", "Passionate in Marketing: Definedge board appointments",
          "https://www.passionateinmarketing.com/definedge-securities-accelerates-wealth-tech-vision-with-hemant-luthra-as-chairman-ajay-srivastava-and-d-prasad-join-board/"),
    SourceSpec(
        key="pim-feed", client=None, name="Passionate in Marketing (RSS)", url="https://www.passionateinmarketing.com/feed/",
        fetch_mode="rss", db_source_type="RSS", tier="TIER_2", feed_url="https://www.passionateinmarketing.com/feed/",
        note="whole-publication marketing/business feed; relevance filters it",
    ),
    # -------------------------------------------------------------------- ISM
    SourceSpec(
        key="ism-press-releases", client="ISM", name="India Semiconductor Mission press releases",
        url="https://www.ism.gov.in/notifications/press-release", fetch_mode="table_rows",
        db_source_type="PRESS_RELEASE", tier="TIER_1", source_kind="official", require_date=True,
        note="server-rendered table (title, date, PDF); official ISM communications",
    ),
    _ref("ism-official-home", "ISM", "ISM official home", "https://www.ism.gov.in/",
         "home page; press releases are ingested from the dedicated press-release table", kind="official"),
    SourceSpec(
        key="et-india-semiconductor", client="ISM", name="Economic Times: India semiconductor topic",
        url="https://economictimes.indiatimes.com/topic/india-semiconductor", fetch_mode="link_listing",
        db_source_type="HTML", tier="TIER_2", link_pattern=r"/articleshow/",
        note="topic listing of ET articles; each article page is parsed for date/image",
    ),
    _seed("ism-yourstory-semicon", "ISM", "YourStory: ISM CEO pitches mega gathering",
          "https://yourstory.com/2026/09/semicon-india-ism-ceo-pitches-mega-gathering"),
    _seed("ism-indianexpress-semicon", "ISM", "Indian Express: Semicon India 2026 chip push",
          "https://indianexpress.com/article/explained/semicon-india-2026-pm-modi-semiconductor-chip-push-10881503/"),
    _seed("ism-indiabriefing-fab", "ISM", "India Briefing: setting up a fab in India",
          "https://www.india-briefing.com/news/setting-up-a-semiconductor-fabrication-plant-in-india-what-foreign-investors-should-know-22009.html/"),
    _seed("ism-theken-mission2", "ISM", "The Ken: India Semiconductor Mission 2.0",
          "https://the-ken.com/newsletter/make-india-competitive-again/india-semiconductor-mission-2-0-has-a-supply-plan-the-demand-gap-remains/"),
    _seed("ism-toi-modi-chips", "ISM", "Times of India: PM Modi semiconductor push",
          "https://timesofindia.indiatimes.com/business/india-business/pm-modi-independence-day-speech-indias-big-semiconductor-push-pm-modi-says-5-8-plants-coming-up-in-few-years-stresses-self-reliance-in-chips/articleshow/133255271.cms"),
    SourceSpec(
        key="yourstory-feed", client=None, name="YourStory (RSS)", url="https://yourstory.com/feed",
        fetch_mode="rss", db_source_type="RSS", tier="TIER_2", feed_url="https://yourstory.com/feed",
        note="whole-publication startup/tech feed; relevance filters it",
    ),
    # -------------------------------------------------------------------- JCB
    SourceSpec(
        key="jcb-india-news", client="JCB", name="JCB India news",
        url="https://www.jcb.com/en-IN/news", fetch_mode="link_listing", db_source_type="OFFICIAL_NEWSROOM",
        tier="TIER_1", source_kind="official", require_date=False, link_pattern=r"/insight/news/\d{4}/",
        note="official India newsroom (verified live 2026-09-20)",
    ),
    SourceSpec(
        key="autoguide-feed", client=None, name="Autoguide India (RSS)", url="https://www.autoguideindia.com/feed",
        fetch_mode="rss", db_source_type="RSS", tier="TIER_2", feed_url="https://www.autoguideindia.com/feed",
        note="automotive/commercial-vehicle trade feed; relevance filters it",
    ),
    SourceSpec(
        key="truckbus-feed", client=None, name="TruckBus India (RSS)", url="https://www.truckbusindia.com/feed/",
        fetch_mode="rss", db_source_type="RSS", tier="TIER_2", feed_url="https://www.truckbusindia.com/feed/",
        note="truck/bus/construction-equipment trade feed; relevance filters it",
    ),
    # -------------------------------------------------------------------- ACE
    _ref("ace-official-media", "ACE", "ACE official media", "https://www.ace-cranes.com/home/media",
         "media index page; the news page below lists award PDFs only (no dates, no article text)", kind="official"),
    _ref("ace-official-news", "ACE", "ACE official press releases", "https://www.ace-cranes.com/home/media-press-release",
         "lists 2023 award PDFs without dates or text; nothing that can be parsed into dated articles", kind="official"),
    _ref("ace-linkedin", "ACE", "ACE LinkedIn", "https://in.linkedin.com/company/action-construction-equipment-ltd", LINKEDIN_NOTE),
]

# Client-specific Google News discovery is generated from clients.py, not duplicated here.
GOOGLE_NEWS_MODE = "google_news"


def enabled_specs(client: str | None = None, keys: set[str] | None = None) -> list[SourceSpec]:
    return [
        s for s in SOURCE_REGISTRY
        if s.enabled and (client is None or s.client == client) and (keys is None or s.key in keys)
    ]


def spec_by_key(key: str) -> SourceSpec:
    return next(s for s in SOURCE_REGISTRY if s.key == key)
