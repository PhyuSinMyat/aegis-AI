from __future__ import annotations

import json
import re
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from urllib.parse import urlparse

from llm_service import generate_response

ALLOWED_RESOURCE_TYPES = {"youtube", "web", "documentation", "course"}
TRUSTED_YOUTUBE_CHANNELS = [
    # Science & Math
    "The Organic Chemistry Tutor",
    "CrashCourse",
    "Professor Dave Explains",
    "3Blue1Brown",
    "Khan Academy",
    # Statistics & Data
    "StatQuest with Josh Starmer",
    "Guy in a Cube",
    "Maven Analytics",
    # Programming & Tech
    "Corey Schafer",
    "NetworkChuck",
    "Traversy Media",
    "FreeCodeCamp",
    # Languages
    "Yoyo Chinese",
    "ChinesePod",
]
TRUSTED_DOMAINS = [
    "khanacademy.org",
    "coursera.org",
    "edx.org",
    "udemy.com",
    "udacity.com",
    "codecademy.com",
    "pluralsight.com",
    "skillshare.com",
    "linkedin.com",
    "ted.com",
    "elementsofai.com",
    "youtube.com",
    "youtu.be",
    "developer.mozilla.org",
    "w3schools.com",
    "javascript.info",
    "css-tricks.com",
    "theodinproject.com",
    "freecodecamp.org",
    "geeksforgeeks.org",
    "stackoverflow.com",
    "leetcode.com",
    "hackerrank.com",
    "realpython.com",
    "automatetheboringstuff.com",
    "eloquentjavascript.net",
    "docs.python.org",
    "doc.rust-lang.org",
    "go.dev",
    "kotlinlang.org",
    "swift.org",
    "typescriptlang.org",
    "php.net",
    "ruby-lang.org",
    "react.dev",
    "reactnative.dev",
    "nodejs.org",
    "expressjs.com",
    "nextjs.org",
    "vuejs.org",
    "angular.io",
    "svelte.dev",
    "rubyonrails.org",
    "docs.djangoproject.com",
    "flask.palletsprojects.com",
    "fastapi.tiangolo.com",
    "docs.docker.com",
    "kubernetes.io",
    "git-scm.com",
    "github.com",
    "numpy.org",
    "pandas.pydata.org",
    "scikit-learn.org",
    "tensorflow.org",
    "pytorch.org",
    "huggingface.co",
    "deeplearning.ai",
    "fast.ai",
    "kaggle.com",
    "distill.pub",
    "arxiv.org",
    "postgresql.org",
    "postgresqltutorial.com",
    "mysql.com",
    "mongodb.com",
    "learn.mongodb.com",
    "redis.io",
    "sqlite.org",
    "restfulapi.net",
    "graphql.org",
    "owasp.org",
    "netacad.com",
    "cisco.com",
    "developer.apple.com",
    "developer.android.com",
    "flutter.dev",
    "docs.flutter.dev",
    "aws.amazon.com",
    "docs.aws.amazon.com",
    "azure.microsoft.com",
    "learn.microsoft.com",
    "cloud.google.com",
    "developers.google.com",
    "mit.edu",
    "ocw.mit.edu",
    "harvard.edu",
    "cs50.harvard.edu",
    "stanford.edu",
    "online.stanford.edu",
    "purdue.edu",
    "owl.purdue.edu",
    "openstax.org",
    "britannica.com",
    "wikipedia.org",
    "history.com",
    "nationalgeographic.com",
    "smithsonianmag.com",
    "bbc.co.uk",
    "bbc.com",
    "gutenberg.org",
    "sparknotes.com",
    "cliffsnotes.com",
    "nature.com",
    "scientificamerican.com",
    "physicsclassroom.com",
    "chemistrylibretexts.org",
    "biologylibretexts.org",
    "chemguide.co.uk",
    "investopedia.com",
    "econlib.org",
    "federalreserve.gov",
    "imf.org",
    "worldbank.org",
    "atlassian.com",
    "agilealliance.org",
    "pmi.org",
    "ubuntu.com",
    "linuxcommand.org",
    "ryanstutorials.net",
    "pages.cs.wisc.edu",
    "figma.com",
    "interaction-design.org",
    "nngroup.com",
    # General Academics & STEM
    "brilliant.org",
    "quizlet.com",
    "ck12.org",
    "hyperphysics.phy-astr.gsu.edu",
    "study.com",
    "wolframalpha.com",
    
    # Python, Web Development & Backend
    "programiz.com",
    "tutorialspoint.com",
    "mdn.github.io", 
    "pypi.org",
    
    # Data, Statistics & BI (Very high quality for these fields)
    "datacamp.com",
    "towardsdatascience.com",
    "sqlbi.com",           # Industry standard for DAX/PowerBI
    "radacad.com",         # Excellent Power BI resource
    "stattrek.com",        # Great for hypothesis testing / distributions
    "machinelearningmastery.com",

    # Cybersecurity & IT
    "tryhackme.com",
    "hackthebox.com",
    "cybrary.it",
    "portswigger.net",     # Creators of Burp Suite, excellent web sec docs
    "sans.org",

    # Language Learning
    "yoyochinese.com",
    "digmandarin.com",
    "duolingo.com",
]


def normalize_topic(topic: str) -> str:
    return re.sub(r"\s+", " ", (topic or "").strip())


def is_trusted_url(url: str) -> bool:
    try:
        hostname = urlparse(url).netloc.lower().lstrip("www.")
        return any(hostname == domain or hostname.endswith(f".{domain}") for domain in TRUSTED_DOMAINS)
    except Exception:
        return False


def _is_youtube_url(url: str) -> bool:
    try:
        hostname = urlparse(url).netloc.lower().lstrip("www.")
        return hostname in {"youtube.com", "youtu.be", "m.youtube.com"} or hostname.endswith(".youtube.com")
    except Exception:
        return False


def _canonical_trusted_channel_name(value: str) -> str | None:
    candidate = (value or "").strip().lower()
    if not candidate:
        return None
    for channel in TRUSTED_YOUTUBE_CHANNELS:
        ch = channel.lower()
        if candidate == ch or ch in candidate:
            return channel
    return None


def _is_direct_youtube_video_url(url: str) -> bool:
    """Accept direct video links only, not search/channel/playlist pages."""
    if not _is_youtube_url(url):
        return False
    try:
        parsed = urllib_parse.urlsplit(url)
        host = parsed.netloc.lower().lstrip("www.")
        path = (parsed.path or "").strip()
        query = urllib_parse.parse_qs(parsed.query or "")

        if host == "youtu.be":
            # Example: https://youtu.be/<video_id>
            return bool(path and path != "/")

        if path == "/watch":
            # Example: https://www.youtube.com/watch?v=<video_id>
            return bool(query.get("v") and query.get("v")[0].strip())

        # Example: https://www.youtube.com/shorts/<video_id>
        if path.startswith("/shorts/"):
            return len(path.split("/")) >= 3 and bool(path.split("/")[2].strip())

        return False
    except Exception:
        return False


def normalize_resource_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urllib_parse.urlsplit(raw)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            return ""
        host = parsed.netloc.lower()
        path = parsed.path or "/"

        # Keep only stable query params that are required for canonical links.
        keep = {"v", "list", "search", "search_query", "q", "query", "k"}
        q_items = urllib_parse.parse_qsl(parsed.query, keep_blank_values=False)
        filtered_q = [(k, v) for k, v in q_items if k.lower() in keep]
        query = urllib_parse.urlencode(filtered_q, doseq=True)

        return urllib_parse.urlunsplit((scheme, host, path, query, ""))
    except Exception:
        return raw


def _has_expiry_like_tokens(url: str) -> bool:
    lower = (url or "").lower()
    risky_tokens = ["expires=", "signature=", "sig=", "token=", "x-amz-", "auth=", "session="]
    return any(tok in lower for tok in risky_tokens)


def is_url_reachable(url: str, timeout_seconds: float = 5.0) -> bool:
    """Best-effort check that URL is accessible and not returning 404/410."""
    if not url:
        return False

    headers = {
        "User-Agent": "Mozilla/5.0 (AegisSummaryBot/1.0)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    # Try HEAD first; some hosts block HEAD so we fall back to GET.
    for method in ("HEAD", "GET"):
        try:
            req = urllib_request.Request(url, headers=headers, method=method)
            with urllib_request.urlopen(req, timeout=timeout_seconds) as resp:
                code = getattr(resp, "status", 200)
                if 200 <= code < 400:
                    return True
                if code in {404, 410}:
                    return False
        except urllib_error.HTTPError as exc:
            if exc.code in {404, 410}:
                return False
            # 405 on HEAD is common, try GET next.
            if method == "HEAD" and exc.code == 405:
                continue
            return False
        except Exception:
            return False

    return False


def build_fallback_resources(topic: str) -> list[dict[str, str]]:
    q = urllib_parse.quote_plus(topic or "study topic")
    return [
        {
            "title": f"Wikipedia search: {topic}",
            "url": f"https://en.wikipedia.org/w/index.php?search={q}",
            "type": "web",
            "source": "Wikipedia",
            "description": "Overview articles and key terms related to this topic.",
        },
        {
            "title": f"Khan Academy search: {topic}",
            "url": f"https://www.khanacademy.org/search?page_search_query={q}",
            "type": "course",
            "source": "Khan Academy",
            "description": "Lessons and practice resources from Khan Academy.",
        },
        {
            "title": f"Coursera catalog search: {topic}",
            "url": f"https://www.coursera.org/search?query={q}",
            "type": "course",
            "source": "Coursera",
            "description": "University-style courses and guided learning paths.",
        },
        {
            "title": f"edX catalog search: {topic}",
            "url": f"https://www.edx.org/search?q={q}",
            "type": "course",
            "source": "edX",
            "description": "Search edX for topic-specific free and paid courses.",
        },
    ]


def ensure_working_resources(resources: list[dict], topic: str, *, minimum_count: int = 4, check_reachability: bool = True) -> list[dict[str, str]]:
    """Filter invalid links and fill with stable fallback links.
    
    Args:
        resources: List of resource dicts to validate
        topic: Topic name for fallback resource generation
        minimum_count: Minimum number of resources to return
        check_reachability: If True, make network requests to verify URLs are accessible (slow).
                           If False, skip network checks (fast, for loading from database).
    """
    verified: list[dict[str, str]] = []
    seen: set[str] = set()

    for item in resources or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        url = normalize_resource_url(str(item.get("url", "")).strip())
        resource_type = str(item.get("type", "web")).strip().lower() or "web"
        source = str(item.get("source", "")).strip()
        description = str(item.get("description", "")).strip()

        # Enforce trusted YouTube channels and direct video URLs.
        is_youtube = resource_type == "youtube" or _is_youtube_url(url)
        if is_youtube:
            trusted_channel = _canonical_trusted_channel_name(source) or _canonical_trusted_channel_name(title)
            if not trusted_channel:
                continue
            if not _is_direct_youtube_video_url(url):
                continue
            resource_type = "youtube"
            source = trusted_channel

        if not title or not url:
            continue
        if resource_type not in ALLOWED_RESOURCE_TYPES:
            resource_type = "web"
        if not is_trusted_url(url):
            continue
        if _has_expiry_like_tokens(url):
            continue
        if url.lower() in seen:
            continue
        # Skip network reachability check when loading from database (already verified on save)
        if check_reachability and not is_url_reachable(url):
            continue

        verified.append(
            {
                "title": title,
                "url": url,
                "type": resource_type,
                "source": source,
                "description": description,
            }
        )
        seen.add(url.lower())

    if len(verified) < minimum_count:
        for fb in build_fallback_resources(topic):
            url = normalize_resource_url(fb.get("url", ""))
            if not url or url.lower() in seen:
                continue
            if not is_trusted_url(url):
                continue
            # Fallback search links are stable endpoints; skip live check to avoid network flakiness.
            verified.append({
                "title": str(fb.get("title", "")).strip(),
                "url": url,
                "type": str(fb.get("type", "web")).strip().lower() or "web",
                "source": str(fb.get("source", "")).strip(),
                "description": str(fb.get("description", "")).strip(),
            })
            seen.add(url.lower())
            if len(verified) >= 8:
                break

    return verified[:8]


def extract_json_object(raw_text: str) -> dict:
    if not raw_text:
        raise ValueError("Empty model response")

    clean = re.sub(r"```json|```", "", raw_text, flags=re.IGNORECASE).strip()

    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", clean)
        if not match:
            raise ValueError("Model returned non-JSON output")
        candidate = match.group()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
            return json.loads(repaired)


def sanitize_output(payload: dict, topic: str) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Model output must be a JSON object")

    difficulty = str(payload.get("difficulty", "intermediate")).strip().lower()
    if difficulty not in {"beginner", "intermediate", "advanced"}:
        difficulty = "intermediate"

    summary = str(payload.get("summary", "")).strip()
    if not summary:
        summary = f"A curated set of resources to help you learn about {topic}."

    resources = payload.get("resources", [])
    if not isinstance(resources, list):
        resources = []

    cleaned: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for item in resources:
        if not isinstance(item, dict):
            continue

        title = str(item.get("title", "")).strip()
        url = normalize_resource_url(str(item.get("url", "")).strip())
        resource_type = str(item.get("type", "web")).strip().lower()
        source = str(item.get("source", "")).strip()
        description = str(item.get("description", "")).strip()

        # Enforce YouTube policy: trusted channels only, direct video URLs only.
        is_youtube = resource_type == "youtube" or _is_youtube_url(url)
        if is_youtube:
            trusted_channel = _canonical_trusted_channel_name(source) or _canonical_trusted_channel_name(title)
            if not trusted_channel:
                continue
            if not _is_direct_youtube_video_url(url):
                continue
            resource_type = "youtube"
            source = trusted_channel

        if not title or not url:
            continue

        if resource_type not in ALLOWED_RESOURCE_TYPES:
            resource_type = "web"

        if not is_trusted_url(url):
            continue

        if url.lower() in seen_urls:
            continue

        cleaned.append({
            "title": title,
            "url": url,
            "type": resource_type,
            "source": source,
            "description": description,
        })
        seen_urls.add(url.lower())

    cleaned = ensure_working_resources(cleaned, topic)

    return {
        "summary": summary,
        "difficulty": difficulty,
        "resources": cleaned[:8],
    }


def find_resources_with_bedrock(topic: str, model_id: str | None = None) -> dict:
    normalized_topic = normalize_topic(topic)
    if not normalized_topic:
        raise ValueError("topic is required")

    trusted_domains_str = "\n".join(f"  - {domain}" for domain in TRUSTED_DOMAINS)
    trusted_channels_str = "\n".join(f"  - {channel}" for channel in TRUSTED_YOUTUBE_CHANNELS)
    system_prompt = f"""You are LearnLink, an educational resource recommendation assistant.

A student will give you a topic — it could be ANYTHING: world history, guitar, calculus, cooking, philosophy, machine learning, ancient Rome, climate change, etc.

Your job is to recommend 5–8 high-quality, real, free learning resources that are DIRECTLY and SPECIFICALLY about that exact topic.

STRICT RULES:
1. Resources must come ONLY from the following trusted domains:
{trusted_domains_str}

2. YouTube policy is STRICT:
    - Recommend YouTube videos ONLY from these trusted channels:
{trusted_channels_str}
    - URL must open the video directly (examples: https://www.youtube.com/watch?v=... or https://youtu.be/...).
    - NEVER use search-result, channel, or playlist URLs for YouTube resources.
    - For YouTube resources, set "source" to the exact trusted channel name.

3. Only use REAL URLs that genuinely exist on those domains. Do NOT invent or guess paths.
4. Every resource must be DIRECTLY about the topic.
5. Balance formats when possible: youtube, web, documentation, course.
6. Prefer free resources. Prefer beginner-friendly unless the topic demands otherwise.
7. Return ONLY a valid JSON object. No explanation, no markdown, no preamble.

JSON format:
{{
  "summary": "2–3 sentence explanation of what this topic is and why it matters for learners",
  "difficulty": "beginner|intermediate|advanced",
  "resources": [
    {{
      "title": "Exact resource title",
      "url": "https://real-url-on-trusted-domain.com/exact-path",
      "type": "youtube|web|documentation|course",
      "source": "Platform or site name",
      "description": "One sentence: what this resource covers and who it is best for"
    }}
  ]
}}"""

    user_prompt = f"Find me the best learning resources for this topic: {normalized_topic}"
    raw_response = generate_response(
        content_blocks=[{"text": user_prompt}],
        system_prompt=system_prompt,
        model_id=model_id,
        max_tokens=1800,
    )
    parsed = extract_json_object(raw_response)
    return sanitize_output(parsed, topic=normalized_topic)
