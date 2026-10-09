"""Deterministic normalization of raw listing fields.

Every function here is pure (no I/O) and conservative: when a value can't
be established from the text, it returns None rather than a guess. That
"unknown" is preserved all the way to the UI, which says "Posting date
unavailable" instead of inventing one.
"""

from __future__ import annotations

import html as html_lib
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import nh3
from bs4 import BeautifulSoup

# --------------------------------------------------------------------- dates


def parse_datetime(value) -> datetime | None:
    """ISO-8601, RFC-822 (RSS) or epoch seconds/milliseconds -> aware UTC
    datetime. Anything else -> None (never "now")."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        number = float(value)
        if number > 1e12:  # milliseconds (Lever)
            number /= 1000
        if number < 1e8:  # too small to be a plausible epoch
            return None
        return datetime.fromtimestamp(number, tz=timezone.utc)
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# ---------------------------------------------------------------------- urls

_TRACKING_PARAMS = re.compile(r"^(utm_.*|gh_src|ref|source|lever-source.*|lever-origin|src|trk|refId|trackingId)$", re.I)


def canonical_url(url: str) -> str:
    """One stable form per vacancy URL: lowercase host without www, no
    tracking params or fragment, no trailing slash, and ATS "apply"
    sub-pages folded into the posting itself."""
    if not url:
        return ""
    parts = urlparse(url.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+$", "", parts.path)
    if host == "jobs.ashbyhq.com":
        path = re.sub(r"/application$", "", path)
    if host == "jobs.lever.co":
        path = re.sub(r"/apply$", "", path)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if not _TRACKING_PARAMS.match(k)]
    return urlunparse(("https", host, path, "", urlencode(query), ""))


def is_specific_listing_url(url: str, source: str) -> bool:
    """False for search-result / category pages that discovery sometimes
    returns in place of an individual vacancy (e.g. linkedin.com/jobs/
    backend-jobs). Only individual postings are stored as jobs."""
    parts = urlparse(url)
    path = parts.path.lower()
    if source == "linkedin":
        return "/jobs/view/" in path
    if source == "indeed":
        return "viewjob" in path or "jk=" in parts.query or "/rc/clk" in path
    return True


# ---------------------------------------------------------------------- html

_ALLOWED_TAGS = {
    "p", "br", "ul", "ol", "li", "strong", "b", "em", "i", "u", "h1", "h2", "h3", "h4", "h5", "h6",
    "a", "blockquote", "code", "pre", "hr", "span", "div", "table", "thead", "tbody", "tr", "td", "th",
}


def sanitize_html(raw: str | None) -> str | None:
    """Job descriptions are untrusted third-party HTML: keep structure
    (headings, lists, links), drop scripts, styles, handlers, iframes."""
    if not raw:
        return None
    unescaped = html_lib.unescape(raw) if "&lt;" in raw else raw  # Greenhouse double-escapes
    cleaned = nh3.clean(
        unescaped,
        tags=_ALLOWED_TAGS,
        attributes={"a": {"href", "title"}},
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer nofollow",
    )
    return cleaned.strip() or None


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    soup = BeautifulSoup(html_lib.unescape(raw) if "&lt;" in raw else raw, "html.parser")
    for li in soup.find_all("li"):
        li.insert_before("\n• ")
    for tag in soup.find_all(["p", "br", "h1", "h2", "h3", "h4", "div", "ul", "ol"]):
        tag.insert_after("\n")
    text = soup.get_text()
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


# -------------------------------------------------------------------- salary

_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}
_CURRENCY_CODES = ("USD", "EUR", "GBP", "INR", "CAD", "AUD", "SGD")
# Indian grouping (12,00,000) first, then Western (1,200,000), then plain numbers.
_NUM = r"(\d{1,3}(?:,\d{2})+,\d{3}|\d{1,3}(?:[,\s]\d{3})+|\d+(?:\.\d+)?)\s*([kKmM]|lpa|lakhs?|lacs?|l\b|cr)?"
_RANGE_RE = re.compile(_NUM + r"\s*(?:-|–|—|to)\s*[$€£₹]?\s*" + _NUM, re.I)
_SINGLE_RE = re.compile(_NUM, re.I)


def _to_number(digits: str, suffix: str | None) -> float:
    value = float(re.sub(r"[,\s]", "", digits))
    s = (suffix or "").lower()
    if s == "k":
        value *= 1_000
    elif s == "m":
        value *= 1_000_000
    elif s in ("lpa", "lakh", "lakhs", "lac", "lacs", "l"):
        value *= 100_000
    elif s == "cr":
        value *= 10_000_000
    return value


def parse_salary(text: str | None) -> dict | None:
    """'$120k - $150k', '₹18-25 LPA', '120,000-150,000 USD / year' ->
    {min, max, currency, period}. None unless a figure is clearly stated."""
    if not text or not re.search(r"\d", text):
        return None
    lowered = text.lower()
    currency = next((code for sym, code in _CURRENCY_SYMBOLS.items() if sym in text), None)
    currency = currency or next((c for c in _CURRENCY_CODES if c.lower() in lowered), None)
    if re.search(r"\b(lpa|lakhs?|lacs?|crore)\b", lowered):
        currency = currency or "INR"
    if currency is None:
        return None  # a bare number could be anything (years, team size...)

    match = _RANGE_RE.search(text)
    if match:
        low_suffix, high_suffix = match.group(2), match.group(4)
        low = _to_number(match.group(1), low_suffix or high_suffix)
        high = _to_number(match.group(3), high_suffix or low_suffix)
    else:
        single = _SINGLE_RE.search(text)
        if not single:
            return None
        low = high = _to_number(single.group(1), single.group(2))
    if low > high:
        low, high = high, low

    if re.search(r"(/|per\s*)\s*(hr|hour)|hourly", lowered):
        period = "hour"
    elif re.search(r"(/|per\s*)\s*(mo|month)|monthly", lowered):
        period = "month"
    else:
        period = "year"
    if period == "year" and high < 1000:
        return None  # "$120 - $150" with no k: not a plausible annual figure
    return {"min": low, "max": high, "currency": currency, "period": period}


def annual_amount(amount: float | None, period: str | None) -> float | None:
    if amount is None:
        return None
    return {"hour": amount * 2080, "month": amount * 12}.get(period or "year", amount)


# -------------------------------------------------------- skills vocabulary

# canonical name -> extra aliases (the canonical name itself always matches)
SKILL_VOCAB: dict[str, tuple[str, ...]] = {
    "Python": (), "Go": ("golang",), "Java": (), "Kotlin": (), "Scala": (), "Rust": (),
    "TypeScript": (), "JavaScript": ("node.js", "nodejs"), "C++": (), "C#": (".net",), "Ruby": ("rails",),
    "PHP": (), "Elixir": (), "SQL": (),
    "FastAPI": (), "Django": (), "Flask": (), "Spring": ("spring boot",), "Express": (), "GraphQL": (),
    "gRPC": (), "REST APIs": ("rest api", "restful"), "React": (), "Next.js": (),
    "PostgreSQL": ("postgres",), "MySQL": (), "MongoDB": (), "Redis": (), "Elasticsearch": ("opensearch",),
    "Cassandra": (), "DynamoDB": (), "ClickHouse": (), "Snowflake": (), "BigQuery": (),
    "Kafka": (), "RabbitMQ": (), "Celery": (), "Airflow": (), "Spark": ("pyspark",), "Flink": (),
    "AWS": ("amazon web services",), "GCP": ("google cloud",), "Azure": (),
    "Docker": (), "Kubernetes": ("k8s",), "Terraform": (), "Helm": (), "CI/CD": ("github actions", "jenkins"),
    "Linux": (), "Microservices": ("microservice",), "Distributed systems": ("distributed system",),
    "System design": (), "Observability": ("prometheus", "grafana", "opentelemetry"),
    "LLM": ("llms", "large language model"), "LangChain": (), "LangGraph": (), "LlamaIndex": (),
    "RAG": ("retrieval augmented", "retrieval-augmented"), "Vector databases": ("pgvector", "pinecone", "weaviate", "qdrant", "milvus"),
    "Prompt engineering": (), "OpenAI API": ("openai",), "Anthropic API": ("anthropic", "claude"),
    "Machine learning": ("ml models", "machine-learning"), "PyTorch": (), "TensorFlow": (),
    "MLOps": (), "Agents": ("ai agents", "agentic"), "Fine-tuning": ("fine tuning", "finetuning"),
    "RBAC": ("access control",), "OAuth": ("oauth2", "openid connect", "oidc"), "Casbin": (),
    "Security": ("appsec",), "Payments": (), "Multi-tenancy": ("multi-tenant", "multitenant"),
}

_GENERIC_SKILLS = {"SQL", "Linux", "REST APIs", "Security"}  # too common to count as a "gap"


def _skill_pattern(term: str) -> re.Pattern:
    escaped = re.escape(term.lower())
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])")


_SKILL_PATTERNS = [
    (name, [_skill_pattern(t) for t in (name, *aliases)]) for name, aliases in SKILL_VOCAB.items()
]


def extract_skills(text: str | None) -> list[str]:
    if not text:
        return []
    lowered = text.lower()
    return [name for name, patterns in _SKILL_PATTERNS if any(p.search(lowered) for p in patterns)]


def is_generic_skill(name: str) -> bool:
    return name in _GENERIC_SKILLS


# ------------------------------------------------- seniority / category / misc

SENIORITY_LEVELS = ("intern", "junior", "mid", "senior", "staff", "principal", "lead", "manager", "director")
_SENIORITY_RULES = [
    ("intern", r"\b(intern|internship|trainee)\b"),
    ("director", r"\b(director|vp|vice president|head of)\b"),
    ("manager", r"\b(engineering manager|manager)\b"),
    ("principal", r"\bprincipal\b"),
    ("staff", r"\bstaff\b"),
    ("lead", r"\b(lead|tech lead)\b"),
    ("senior", r"\b(senior|sr\.?|sde[- ]?(iii|3)|engineer (iii|3)|level iii)\b"),
    ("junior", r"\b(junior|jr\.?|entry[- ]level|graduate|new grad|associate)\b"),
    ("mid", r"\b(mid[- ]level|sde[- ]?(i|ii|1|2)|engineer (i|ii|1|2))\b"),
]


def detect_seniority(title: str | None) -> str | None:
    lowered = (title or "").lower()
    for level, pattern in _SENIORITY_RULES:
        if re.search(pattern, lowered):
            return level
    return None


JOB_CATEGORIES = (
    "backend", "platform", "ai_ml", "fullstack", "frontend", "data", "devops_sre", "mobile",
    "security", "solutions", "support", "management", "other",
)
_CATEGORY_RULES = [
    ("management", r"\b(engineering manager|director|head of|vp\b)"),
    ("support", r"\b(support|helpdesk|customer success|technical account)\b"),
    ("solutions", r"\b(solutions? (architect|engineer)|sales engineer|pre-?sales|forward deployed)\b"),
    ("ai_ml", r"\b(ai|ml|machine learning|llm|genai|applied scientist|nlp)\b"),
    ("platform", r"\b(platform|infrastructure|infra)\b"),
    ("devops_sre", r"\b(devops|sre|site reliability|cloud engineer)\b"),
    ("data", r"\b(data engineer|analytics engineer|data platform|etl)\b"),
    ("security", r"\b(security|appsec)\b"),
    ("mobile", r"\b(ios|android|mobile)\b"),
    ("frontend", r"\b(front[- ]?end|ui engineer|react developer)\b"),
    ("fullstack", r"\b(full[- ]?stack)\b"),
    ("backend", r"\b(back[- ]?end|server[- ]side|api engineer)\b"),
]


def detect_category(title: str | None) -> str:
    lowered = (title or "").lower()
    for category, pattern in _CATEGORY_RULES:
        if re.search(pattern, lowered):
            return category
    if re.search(r"\b(software|sde|developer|engineer)\b", lowered):
        return "backend" if "software" in lowered or "sde" in lowered else "other"
    return "other"


_EXPERIENCE_RE = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*)?\+?\s*(?:years?|yrs?)(?:\s+of)?(?:\s+\w+){0,4}?\s+(?:experience|exp\b)",
    re.I,
)


def extract_experience(text: str | None) -> tuple[float | None, str | None]:
    """Smallest stated minimum years of experience + the phrase it came from."""
    if not text:
        return None, None
    best: tuple[float, str] | None = None
    for m in _EXPERIENCE_RE.finditer(text):
        years = float(m.group(1))
        if years > 25:
            continue
        if best is None or years < best[0]:
            best = (years, m.group(0).strip())
    return (best[0], best[1]) if best else (None, None)


_EMPLOYMENT_TYPES = [
    ("internship", r"\b(internship|intern)\b"),
    ("contract", r"\b(contract|contractor|freelance|c2h|contract-to-hire)\b"),
    ("part_time", r"\b(part[- ]time)\b"),
    ("temporary", r"\b(temporary|temp)\b"),
    ("full_time", r"\b(full[- ]time|permanent|fulltime)\b"),
]


def normalize_employment_type(value: str | None) -> str | None:
    """Maps source vocabularies (Lever 'Full-time', Ashby 'FullTime',
    JSON-LD 'FULL_TIME') to one set; None when not stated."""
    if not value:
        return None
    lowered = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value).lower().replace("_", " ")
    for kind, pattern in _EMPLOYMENT_TYPES:
        if re.search(pattern, lowered):
            return kind
    return None


def classify_remote(location: str | None, workplace_type: str | None = None, text: str | None = None) -> str | None:
    if workplace_type:
        w = workplace_type.lower()
        for kind in ("remote", "hybrid"):
            if kind in w:
                return kind
        if "onsite" in w or "on-site" in w or "office" in w:
            return "onsite"
    loc = (location or "").lower()
    if "remote" in loc or "anywhere" in loc or "worldwide" in loc:
        return "remote"
    if "hybrid" in loc:
        return "hybrid"
    if loc.strip():
        return "onsite"
    if text and re.search(r"\b(fully remote|100% remote|remote-first|work from anywhere)\b", text.lower()):
        return "remote"
    return None


def normalize_company(name: str | None) -> str:
    name = (name or "").strip()
    return re.sub(r"\s+", " ", name)


def dedupe_key(company: str, title: str, location: str | None) -> str:
    """Strict identity for cross-source duplicates: same company, same
    title, same location after normalization. Similar-but-different titles
    ("Backend Engineer" vs "Senior Backend Engineer") stay separate jobs."""
    def norm(s: str | None) -> str:
        s = (s or "").lower()
        s = re.sub(r"\b(inc|llc|ltd|gmbh|pvt|private|limited|corp|co)\b\.?", "", s)
        return re.sub(r"[^a-z0-9]+", " ", s).strip()
    return f"{norm(company)}|{norm(title)}|{norm(location)}"
