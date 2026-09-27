"""
Entity configuration and client-specific relevance taxonomy.

Two consumers share this one registry:

* ``StrictEntityMatcher`` (candidate generation) uses the original fields:
  legal_names, aliases, subsidiaries, products, official_domains, industry_terms,
  excluded_entities, excluded_keywords, required_context_terms, confidence_threshold.
* ``client_relevance`` (final arbiter of "is this article about THIS client?")
  uses the taxonomy fields: strong_keywords, ambiguous_names,
  case_sensitive_acronyms, context_keywords, executive_keywords,
  location_keywords, topic_keywords, compound_rules, negative_keywords,
  hard_negative_keywords, trusted_sources, source_priority.

Facts here come from official/company pages, the supplied source URLs, or the
2026-09 JCB/ACE research pass. Anything unverified is deliberately left out
rather than guessed (for example executives for Vikas Group and VIPS).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class CompoundRule:
    """Evidence that only counts when EVERY group has at least one hit.

    Used where the client has no unique name in the text, e.g. the India
    Semiconductor Mission is often described as "India" + "chip" + "plant".
    """

    name: str
    groups: tuple[tuple[str, ...], ...]
    weight: int = 30
    official_implies_first_group: bool = False
    """For the client's own official source the first (identity/region) group is implied by the source itself;
    the topical groups must still match."""


@dataclass
class EntityConfig:
    """Strict configuration for a single entity/client."""

    canonical_name: str
    legal_names: list[str]
    aliases: list[str]
    subsidiaries: list[str]
    products: list[str]
    official_domains: list[str]
    industry_terms: list[str]
    excluded_entities: list[str]
    excluded_keywords: list[str]
    required_context_terms: Optional[list[str]] = None
    confidence_threshold: int = 60

    # ---- client-specific relevance taxonomy -------------------------------
    display_name: str = ""
    strong_keywords: list[str] = field(default_factory=list)
    """Unambiguous names: a hit is entity evidence on its own."""
    ambiguous_names: list[str] = field(default_factory=list)
    """Names shared with other things; only count next to a context keyword."""
    ambiguous_context: list[str] = field(default_factory=list)
    """Disambiguators required for ``ambiguous_names`` (falls back to ``context_keywords`` when empty).
    Use this when the generic context words are too common to prove identity."""
    case_sensitive_acronyms: list[str] = field(default_factory=list)
    """Acronyms matched case-sensitively (VIPS != VIPs, ACE != ace); need context."""
    context_keywords: list[str] = field(default_factory=list)
    executive_keywords: list[str] = field(default_factory=list)
    location_keywords: list[str] = field(default_factory=list)
    topic_keywords: dict[str, list[str]] = field(default_factory=dict)
    compound_rules: list[CompoundRule] = field(default_factory=list)
    negative_keywords: list[str] = field(default_factory=list)
    """Soft negatives: subtract points."""
    hard_negative_keywords: list[str] = field(default_factory=list)
    """Reject unless a strong keyword appears in the title."""
    trusted_sources: list[str] = field(default_factory=list)
    """Publisher domains that reliably cover this client."""
    source_priority: dict[str, int] = field(
        default_factory=lambda: {"official": 15, "trusted": 8, "other": 0}
    )


# Business-action topics shared by every client. Each client adds its own on top.
COMMON_TOPICS: dict[str, list[str]] = {
    "product launch": ["launch", "launches", "launched", "unveil", "unveils", "unveiled", "introduces", "new model"],
    "contracts/orders": ["order", "orders", "contract", "bags", "wins", "secures", "supply agreement"],
    "capacity expansion": ["expansion", "expands", "new plant", "new facility", "capacity", "greenfield", "inaugurates", "inaugurated", "commissioned"],
    "strategic partnership": ["joint venture", "partnership", "partners with", "tie-up", "collaboration", "mou", "alliance", "technology transfer"],
    "investment/funding": ["investment", "invests", "funding", "raises", "acquisition", "acquires", "stake", "backers"],
    "leadership": ["appoints", "appointed", "chairman", "managing director", "ceo", "board of directors", "joins board", "takes helm"],
    "financial performance": ["share price", "shares", "results", "revenue", "profit", "dividend", "quarter", "earnings", "order book"],
}

# ---------------------------------------------------------------------------
# VIKAS GROUP - automotive thermal management / emission / e-mobility
# ---------------------------------------------------------------------------
VIKAS_CONFIG = EntityConfig(
    canonical_name="VIKAS",
    legal_names=[
        "Vikas Group",
        "Sanden Vikas",
        "Sanden Vikas (India) Pvt. Ltd.",
        "Sanden Vikas India",
        "Pranav Vikas",
        "Pranav Vikas (India) Pvt. Ltd.",
        "SATA Vikas",
        "SATA Vikas India",
        "SJG Sejong Vikas",
        "SJG Sejong Vikas Pvt. Ltd.",
        "Sanden Vikas Precision Parts",
        "Sanden Vikas Precision Parts Pvt. Ltd.",
        "Kenmore Vikas",
        "Kenmore Vikas (India) Pvt. Ltd.",
        "Auto Vikas",
        "Vikas Doowon",
        "Vikas Doowon India",
        "Ecocat",
        "Ecocat India",
    ],
    aliases=[
        "Vikas automotive",
        "Vikas thermal",
        "Vikas precision",
        "Sanden Vikas",
        "Pranav Vikas",
        "Ecocat",
    ],
    subsidiaries=[
        "Sanden Vikas",
        "Pranav Vikas",
        "Ecocat",
        "SATA Vikas",
        "SJG Sejong Vikas",
        "Kenmore Vikas",
        "Auto Vikas",
        "Vikas Doowon",
    ],
    products=[
        "thermal management",
        "air conditioning",
        "heat exchanger",
        "emission control",
        "e-mobility",
        "precision machining",
        "extrusion",
        "defence power",
        "cooling solutions",
    ],
    official_domains=[
        "vikasgroup.in",
        "www.vikasgroup.in",
        "sanden-vikas.com",
        "ecocat.co.in",
    ],
    industry_terms=[
        "automotive",
        "manufacturing",
        "industrial equipment",
        "thermal solutions",
        "machinery",
    ],
    excluded_entities=[
        "Vidya Vikas",
        "Vikas Infrastructure",
        "Vikas Construction",
        "Vikas Education",
        "Vikas University",
        "Vikas School",
        "Vikas Institute",
        "Nyaya Vikas",
        "Bharat Vikas Parishad",
        "Vikas Samvardhana",
    ],
    excluded_keywords=[
        "school",
        "education",
        "university",
        "institute",
        "college",
        "infrastructure",
        "construction",
        "real estate",
        "housing",
        "government",
    ],
    required_context_terms=[
        "automotive",
        "thermal",
        "manufacturing",
        "industrial",
        "equipment",
    ],
    confidence_threshold=35,
    display_name="Vikas Group",
    strong_keywords=[
        "Pranav Vikas",
        "Sanden Vikas",
        "Ecocat India",
        "SATA Vikas",
        "SJG Sejong Vikas",
        "Kenmore Vikas",
        "Vikas Doowon",
        "vikasgroup.in",
    ],
    ambiguous_names=["Vikas Group", "Vikas automotive", "Vikas thermal", "Vikas precision", "Auto Vikas"],
    context_keywords=[
        "automotive", "thermal management", "hvac", "air conditioning", "air-conditioning", "compressor",
        "heat exchanger", "emission control", "emission after-treatment", "exhaust", "bus", "buses",
        "e-mobility", "ev", "electric vehicle", "hybrid", "battery cooling", "precision machining",
        "extrusion", "defence", "faridabad", "sanden", "oem", "oems", "prawaas",
    ],
    location_keywords=["Faridabad", "Haryana"],
    topic_keywords={
        **COMMON_TOPICS,
        "EV/e-mobility": ["ev", "electric vehicle", "e-mobility", "hybrid", "battery"],
        "thermal/HVAC": ["hvac", "thermal management", "air conditioning", "compressor", "heat exchanger"],
        "trade shows": ["exhibition", "expo", "prawaas", "auto expo"],
    },
    negative_keywords=[
        "school", "university", "college", "education", "real estate", "housing",
        "municipal", "ministry", "yojana", "panchayat",
    ],
    hard_negative_keywords=["Vikas Ecotech", "Vikas Lifecare", "Vikas Multicorp", "Vikas Global One", "Vikas WSP"],
    trusted_sources=["motorindiaonline.in", "autocarpro.in", "economictimes.indiatimes.com", "business-standard.com"],
)


# ---------------------------------------------------------------------------
# ACE - Action Construction Equipment Ltd (NSE: ACE, BSE: 532762)
# ---------------------------------------------------------------------------
ACE_CONFIG = EntityConfig(
    canonical_name="ACE",
    legal_names=[
        "Action Construction Equipment",
        "Action Construction Equipment Ltd",
        "Action Construction Equipment Limited",
        "Action Construction Equipment Ltd.",
        "ACE Cranes",
        "ACE Construction Equipment",
    ],
    aliases=[
        "Action Construction Equipment",
        "ACE cranes",
        "ACE construction",
    ],
    subsidiaries=["ACE KATO"],
    products=[
        "mobile crane",
        "tower crane",
        "crawler crane",
        "truck mounted crane",
        "lorry loader",
        "backhoe loader",
        "forklift",
        "telehandler",
        "aerial work platform",
        "scissor lift",
        "motor grader",
        "vibratory roller",
        "piling rig",
        "concrete boom",
        "combine harvester",
        "tractor",
    ],
    official_domains=[
        "ace-cranes.com",
        "www.ace-cranes.com",
        "actionequipment.com",
    ],
    industry_terms=[
        "construction equipment",
        "material handling",
        "heavy equipment",
        "machinery",
        "crane",
        "loader",
        "forklift",
    ],
    excluded_entities=[
        "Ace Turtle",
        "ACE Turtle",
        "ACE Fashion",
        "ACE Digital",
        "ACE Marketing",
        "ACE Retail",
        "ACE Education",
        "ACE University",
    ],
    excluded_keywords=[
        "fashion",
        "retail",
        "clothing",
        "apparel",
        "digital",
        "marketing",
        "education",
        "university",
        "sports",
        "generic",
    ],
    required_context_terms=[
        "construction",
        "equipment",
        "crane",
        "machinery",
        "loader",
        "forklift",
        "industrial",
    ],
    confidence_threshold=35,
    display_name="Action Construction Equipment",
    strong_keywords=[
        "Action Construction Equipment",
        "ACE Cranes",
        "ACE KATO",
        "ACE-KATO",
        "ace-cranes.com",
        "Vijay Aggarwal",
        "Vijay Agarwal",
    ],
    ambiguous_names=["ACE Ltd", "ACE Limited"],
    case_sensitive_acronyms=["ACE"],
    context_keywords=[
        "crane", "cranes", "pick and carry", "pick & carry", "tower crane", "backhoe", "forklift",
        "material handling", "construction equipment", "faridabad", "palwal", "nse: ace", "nse:ace",
        "share price", "shares", "dividend", "quarterly results", "order book", "kato works",
    ],
    executive_keywords=["Vijay Aggarwal", "Vijay Agarwal", "Mona Agarwal", "Sorab Agarwal"],
    location_keywords=["Faridabad", "Palwal", "Haryana"],
    topic_keywords={
        **COMMON_TOPICS,
        "cranes/equipment": ["crane", "cranes", "forklift", "backhoe", "tower crane", "pick and carry"],
        "JV/KATO": ["kato works", "ace kato", "joint venture"],
    },
    negative_keywords=[],
    hard_negative_keywords=[
        "Ace Hardware", "Chubb", "ACE Group", "ACE inhibitor", "ACE inhibitors", "ACE2", "tennis", "Ace Turtle",
        "ACE Awards", "Ace Combat", "Ace Attorney", "ace of spades", "football", "cricket",
    ],
    trusted_sources=[
        "autocarpro.in", "business-standard.com", "constructionworld.in", "economictimes.indiatimes.com",
        "worldconstructionnetwork.com", "screener.in", "trendlyne.com",
    ],
)


# ---------------------------------------------------------------------------
# ISM - India Semiconductor Mission (NOT ISM Limited, NOT Institute for Supply Management)
# ---------------------------------------------------------------------------
ISM_CONFIG = EntityConfig(
    canonical_name="ISM",
    legal_names=[
        "India Semiconductor Mission",
        "Indian Semiconductor Mission",
    ],
    aliases=[
        "ISM 2.0",
        "Semicon India",
        "Semiconductor Mission",
    ],
    subsidiaries=[],
    products=[
        "semiconductor fab",
        "OSAT",
        "ATMP",
        "compound semiconductor",
        "display fab",
        "chip design",
        "design linked incentive",
        "SPECS",
    ],
    official_domains=[
        "ism.gov.in",
        "www.ism.gov.in",
    ],
    industry_terms=[
        "semiconductor",
        "semiconductors",
        "chip",
        "chips",
        "fab",
        "fabrication",
        "wafer",
        "electronics manufacturing",
    ],
    excluded_entities=[
        "Institute for Supply Management",
        "ISM Manufacturing PMI",
        "ISM Services PMI",
        "ISM Manufacturing Index",
        "Indian School of Mines",
        "IIT ISM",
        "ISM Dhanbad",
    ],
    excluded_keywords=[],
    required_context_terms=None,
    confidence_threshold=40,
    display_name="India Semiconductor Mission",
    strong_keywords=[
        "India Semiconductor Mission",
        "Indian Semiconductor Mission",
        "Semicon India",
        "ISM 2.0",
        "Semicon 2.0",
        "ism.gov.in",
        "Amitesh Kumar Sinha",
    ],
    ambiguous_names=["Semiconductor Mission"],
    case_sensitive_acronyms=["ISM"],
    context_keywords=[
        "semiconductor", "semiconductors", "chip", "chips", "fab", "fabs", "osat", "atmp", "wafer",
        "meity", "vaishnaw", "electronics manufacturing", "specs", "design linked incentive",
        "tata electronics", "micron", "dholera", "sanand",
    ],
    executive_keywords=["Amitesh Kumar Sinha", "Ashwini Vaishnaw"],
    location_keywords=["India", "New Delhi", "Dholera", "Sanand"],
    topic_keywords={
        **COMMON_TOPICS,
        "semiconductor policy": ["incentive", "incentives", "scheme", "policy", "subsidy", "cabinet", "approves", "approved", "mission"],
        "fabs/OSAT": ["fab", "fabs", "osat", "atmp", "fabrication", "wafer", "plant", "plants"],
        "supply chain/ecosystem": ["supply chain", "ecosystem", "workforce", "talent", "skilling"],
        "design/R&D": ["chip design", "design linked", "r&d", "research"],
        "events": ["semicon india", "summit", "conference"],
    },
    compound_rules=[
        CompoundRule(
            name="india_semiconductor_policy",
            groups=(
                ("india", "indian", "bharat", "new delhi", "meity"),
                ("semiconductor", "semiconductors", "chip", "chips", "fab", "fabs", "osat", "atmp"),
                ("mission", "scheme", "incentive", "incentives", "policy", "plant", "plants", "approves",
                 "approved", "cabinet", "subsidy", "investment", "manufacturing", "supply chain", "push"),
            ),
            weight=30,
            official_implies_first_group=True,
        ),
    ],
    negative_keywords=[],
    hard_negative_keywords=[
        "Institute for Supply Management", "ISM Manufacturing PMI", "ISM Services PMI", "ISM Manufacturing Index",
        "ISM Non-Manufacturing", "Indian School of Mines", "IIT ISM", "IIT (ISM)", "ISM Dhanbad",
        "Purchasing Managers", "PMI",
    ],
    trusted_sources=[
        "economictimes.indiatimes.com", "yourstory.com", "indianexpress.com", "india-briefing.com", "the-ken.com",
        "timesofindia.indiatimes.com", "pib.gov.in", "meity.gov.in", "business-standard.com", "livemint.com", "thehindu.com",
    ],
)


# ---------------------------------------------------------------------------
# JCB - JCB India Ltd / J.C. Bamford Excavators (construction equipment)
# ---------------------------------------------------------------------------
JCB_CONFIG = EntityConfig(
    canonical_name="JCB",
    legal_names=[
        "JCB",
        "JCB Limited",
        "JCB India",
        "JCB India Limited",
        "JCB Excavators",
    ],
    aliases=[
        "JCB",
        "JCB excavators",
        "JCB India",
    ],
    subsidiaries=[
        "JCB India",
    ],
    products=[
        "excavator",
        "backhoe loader",
        "fastrac",
        "robotics",
        "hydrogen",
        "construction equipment",
    ],
    official_domains=[
        "jcb.com",
        "www.jcb.com",
        "jcbindia.com",
        "www.jcbindia.com",
    ],
    industry_terms=[
        "construction equipment",
        "excavator",
        "manufacturing",
        "machinery",
    ],
    excluded_entities=[],
    excluded_keywords=[],
    required_context_terms=None,
    confidence_threshold=60,
    display_name="JCB",
    strong_keywords=[
        "JCB India", "JCB India Ltd", "JCB India Limited", "J.C. Bamford", "J C Bamford", "JCB Hydromax",
        "JCB Dieselmax", "JCB Loadall", "JCB Fastrac", "JCB 3DX", "3DX Super", "3DX Plus", "Deepak Shetty",
    ],
    ambiguous_names=[],
    case_sensitive_acronyms=["JCB"],
    context_keywords=[
        "backhoe", "backhoe loader", "excavator", "excavators", "telehandler", "loader", "loaders",
        "construction equipment", "earthmoving", "earth moving", "dealer", "plant", "ballabgarh", "jaipur",
        "pune", "vadodara", "halol", "stage 5", "cev v", "bauma", "conexpo", "skid steer", "hydrogen engine",
        "livelink", "mini excavator", "wheel loader",
    ],
    executive_keywords=["Deepak Shetty"],
    location_keywords=["Ballabgarh", "Jaipur", "Pune", "Vadodara", "Halol", "Rocester"],
    topic_keywords={
        **COMMON_TOPICS,
        "construction equipment": ["backhoe", "excavator", "loader", "telehandler", "skid steer"],
        "hydrogen/clean tech": ["hydrogen", "hydromax", "stage 5", "electric", "emission"],
        "exports": ["export", "exports"],
        "trade shows": ["bauma", "conexpo", "expo"],
    },
    negative_keywords=["toy", "meme"],
    hard_negative_keywords=[
        "JCB Co., Ltd", "JCB Co.", "JCB card", "JCB credit card", "JCB Prize", "JCB Literary Prize",
        "JCB ki khudai", "for sale", "Purple Wave", "auction", "used JCB", "cashback", "credit card",
    ],
    trusted_sources=[
        "constructionworld.in", "autocarpro.in", "ibef.org", "manufacturingtodayindia.com", "autoguideindia.com",
        "truckbusindia.com", "economictimes.indiatimes.com",
    ],
)


# ---------------------------------------------------------------------------
# Definedge Securities (Pune wealth-tech / brokerage)
# ---------------------------------------------------------------------------
DEFINEDGE_CONFIG = EntityConfig(
    canonical_name="Definedge",
    legal_names=[
        "Definedge",
        "Definedge Securities",
        "Definedge Securities Pvt. Ltd.",
        "Definedge Securities Private Limited",
    ],
    aliases=[
        "Definedge",
        "Definedge Securities",
    ],
    subsidiaries=[],
    products=[
        "algo trading",
        "trading platform",
        "financial technology",
        "securities",
        "derivatives",
    ],
    official_domains=[
        "definedge.com",
        "www.definedge.com",
        "definedgesecurities.com",
        "www.definedgesecurities.com",
        "forum.definedgesecurities.com",
    ],
    industry_terms=[
        "fintech",
        "trading",
        "securities",
        "financial",
        "technology",
    ],
    excluded_entities=[],
    excluded_keywords=[],
    required_context_terms=[
        "trading",
        "fintech",
        "securities",
        "financial",
    ],
    confidence_threshold=65,
    display_name="Definedge Securities",
    strong_keywords=["Definedge Securities", "Definedge", "definedgesecurities.com"],
    ambiguous_names=[],
    case_sensitive_acronyms=[],
    context_keywords=[
        "wealth-tech", "wealth tech", "wealthtech", "brokerage", "broker", "trading", "investor education",
        "investors", "pune", "securities", "stock broking", "algo trading", "charting", "fintech",
        "capital markets", "sebi",
    ],
    executive_keywords=["Prashant Shah", "Rajesh Badiye", "Hemant Luthra"],
    location_keywords=["Pune"],
    topic_keywords={
        **COMMON_TOPICS,
        "wealth-tech/platform": ["wealth-tech", "wealth tech", "platform", "app", "trading tools", "analytics"],
        "investor education": ["investor education", "education", "community", "webinar", "course"],
        "regulatory": ["sebi", "regulator", "circular", "penalty", "compliance"],
    },
    negative_keywords=[],
    hard_negative_keywords=[],
    trusted_sources=[
        "finsmes.com", "devdiscourse.com", "passionateinmarketing.com", "economictimes.indiatimes.com",
        "business-standard.com", "moneycontrol.com", "livemint.com", "entrackr.com", "inc42.com",
    ],
)


# ---------------------------------------------------------------------------
# VIPS - Vivekananda Institute of Professional Studies (Technical Campus), Delhi
# ---------------------------------------------------------------------------
VIPS_CONFIG = EntityConfig(
    canonical_name="VIPS",
    legal_names=[
        "Vivekananda Institute of Professional Studies",
        "Vivekananda Institute of Professional Studies - Technical Campus",
        "VIPS-TC",
        "VIPS TC",
        "Vivekananda School of Business Studies",
    ],
    aliases=[
        "VIPS-TC",
        "Vivekananda Institute of Professional Studies",
    ],
    subsidiaries=[],
    products=[
        "admissions",
        "placements",
        "MBA",
        "BBA",
        "BCA",
        "LLB",
    ],
    official_domains=[
        "vips.edu",
        "www.vips.edu",
    ],
    industry_terms=[
        "education",
        "university",
        "college",
        "institute",
        "campus",
        "students",
        "GGSIPU",
    ],
    excluded_entities=[
        "very important person",
        "very important persons",
    ],
    excluded_keywords=[],
    required_context_terms=None,
    confidence_threshold=40,
    display_name="Vivekananda Institute of Professional Studies",
    strong_keywords=[
        "Vivekananda Institute of Professional Studies",
        "VIPS-TC",
        "VIPS TC",
        "VIPS Technical Campus",
        "Vivekananda School of Business Studies",
        "vips.edu",
    ],
    ambiguous_names=["Vivekananda Institute"],
    ambiguous_context=["pitampura", "ggsipu", "ip university", "guru gobind singh", "professional studies", "technical campus", "delhi", "new delhi"],
    case_sensitive_acronyms=["VIPS"],
    context_keywords=[
        "admission", "admissions", "placement", "placements", "campus", "students", "student", "course",
        "courses", "programme", "program", "mba", "bba", "bca", "llb", "b.tech", "ggsipu", "ip university",
        "delhi", "pitampura", "faculty", "recruiters", "internship", "naac", "nba", "aicte", "nirf",
        "college", "institute", "university", "school",
    ],
    executive_keywords=[],
    location_keywords=["Pitampura", "Delhi", "New Delhi"],
    topic_keywords={
        "admissions": ["admission", "admissions", "counselling", "seats", "application"],
        "placements": ["placement", "placements", "recruiter", "recruiters", "campus recruitment", "lpa", "ctc", "package",
                       "interview", "interviews", "internship", "internships", "orientation", "guest lecture", "industry visit"],
        "academic programs": ["course", "courses", "programme", "program", "curriculum", "specialisation", "new program"],
        "industry partnerships": ["mou", "industry partnership", "collaboration", "tie-up", "partnership", "industry academia"],
        "research/events": ["research", "conference", "seminar", "workshop", "webinar", "summit", "fest", "hackathon"],
        "accreditation/rankings": ["naac", "nba", "aicte", "nirf", "ranking", "accreditation", "rank"],
        "faculty": ["faculty", "professor", "appointed", "dean", "director"],
        "student achievement": ["student achievement", "wins", "winner", "award", "topper"],
        "campus/infrastructure": ["campus", "infrastructure", "lab", "new building"],
        "technology/AI education": ["ai", "artificial intelligence", "data science", "cyber security", "machine learning"],
    },
    negative_keywords=[],
    hard_negative_keywords=[
        "very important person", "VIP Industries", "VIP pass", "VIP treatment", "VIP access", "VIP lounge",
    ],
    trusted_sources=[
        "shiksha.com", "collegedunia.com", "college360.co.in", "careers360.com", "timesofindia.indiatimes.com",
        "hindustantimes.com", "indianexpress.com",
    ],
)


# Entity configuration mapping
ENTITY_CONFIGS = {
    "VIKAS": VIKAS_CONFIG,
    "ACE": ACE_CONFIG,
    "ISM": ISM_CONFIG,
    "JCB": JCB_CONFIG,
    "Definedge": DEFINEDGE_CONFIG,
    "VIPS": VIPS_CONFIG,
}
