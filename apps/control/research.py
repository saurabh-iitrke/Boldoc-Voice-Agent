"""Web research for long documents: the writer (Hermes) searches and reads the web through control, and
only for a document whose caller said yes on the call ("Main internet se jaankari bhi jod sakti hoon... Jod doon?").

  web_search(query)            Serper (Google's index, results for India): titles, links and snippets. Only the query
                               leaves India, and it is screened first (Privacy): nothing that identifies the caller
                               (their name, organisation, street address, phone, email, PAN, Aadhaar, account numbers,
                               their own figures). Places and general terms (a scheme's or a bank's name) may go.
  read_page(url, looking_for)  control fetches the page itself, in Mumbai: https on port 443 to a public address only,
                               every redirect checked again, at most 3 MB of HTML, text or PDF; the passages that
                               match what the writer is looking for come back as quoted data, never as instructions.
A page must come from this document's search results or an official Indian site (.gov.in, .nic.in, ...), and its
link is screened like a query, so a page read can't make the writer send the caller's details anywhere. Per
document: at most MAX_SEARCHES searches and MAX_PAGES pages (bigdoc.BigDocs keeps the count, the sources and the
Sources list the document ends with).
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import time
from html.parser import HTMLParser
from typing import Awaitable, Callable, Dict, List, Optional, Protocol, Tuple
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from .pdftext import pdf_text

MAX_SEARCHES = 15
MAX_PAGES = 25
MAX_BYTES = 3_000_000
MAX_CHARS = 4_000      # of a page, per read: every later model call of the run carries the pages read so far
RESULTS = 6
PDF_PAGES = 40
ROBOT = "BolDocBot"   # what a site's robots.txt names us by
UA = f"Mozilla/5.0 (compatible; {ROBOT}/1.0; +https://boldoc.in)"
ROBOTS_TTL_S = 3600
PAGE_TIMEOUT_S = 45    # a page, start to end (a slow site, or a long PDF)
_READABLE = ("text/html", "application/xhtml", "text/plain", "application/pdf")
# Official Indian sources, readable without having been found by a search first
OFFICIAL = (".gov.in", ".nic.in", "rbi.org.in", "mudra.org.in", "sidbi.in", "nabard.org", "icai.org", "cbse.gov.in")


class ResearchError(Exception):
    """A search or a page that didn't work out: the message goes back to the writer as the tool's error."""


# ------------------------------------------------------------------------------------------------ privacy
_SPLIT = re.compile(r"[\s,.;:/|()\[\]{}\"'!?*&+=<>#@~`^%$\\_–—-]+")
_YEAR = re.compile(r"(?:19|20)\d\d")
# Words that name a kind of thing or place, not someone: never what makes a query the caller's ("dairy farm subsidy"
# stays fine for a caller whose business is "Sharma Dairy Farm"; "sharma" doesn't)
_COMMON_WORDS = """
the and for with from into about what which how why when where who are was were has have had not but all any can its
this that these those of in on at to by or an a as is be it our your their his her my me we you they them per vs new
pvt private ltd limited llp co company firm group india indian international national global
dairy farm farms shop store stores mart traders trading enterprise enterprises industries industry services service
solutions solution technologies technology tech software systems foods food agro agri products product
manufacturing works workshop studio centre center consultancy consultants consulting associates
college university school institute academy hospital clinic medical pharmacy bank finance financial
hotel restaurant cafe bakery kirana general textiles garments boutique salon beauty
road street lane marg nagar colony sector block phase floor flat house apartment apartments building tower plot
near opposite behind village gaon district city state town mohalla ward post office
owner proprietor manager engineer developer intern internship trainee teacher student director founder guide
mr mrs ms shri smt sri ji dr prof messrs
com net org gmail yahoo hotmail outlook rediffmail icloud www http https html pdf
"""
_COMMON = frozenset(_COMMON_WORDS.split())
# A fact's label says what its value is: an organisation's name, a person's name, an address. The intake writes the
# labels in its own words ("promoter", "business", "college and guide"), so they are matched loosely.
_ORG_NAME = re.compile(r"company|firm|organi[sz]ation|employer|college|school|institute|"
                       r"(?:business|shop|dukaan|store)(?:'s)? (?:name|ka naam)|name of the", re.I)
_ORG_ISH = re.compile(r"business|shop|dukaan|store|stall|cafe", re.I)  # a name, or a description of the trade
_PERSON = re.compile(r"(?<!business )(?<!shop )(?<!company )\bname\b|naam|guide|promoter|owner|proprietor|applicant|"
                     r"founder|partner|director|signatory|father|husband|wife|contact person", re.I)
_ADDRESS = re.compile(r"address|pata", re.I)
_PATTERNS = (
    ("an email address or UPI id", re.compile(r"\S+@\S+"), True),
    ("a PAN", re.compile(r"\b[a-z]{5}\d{4}[a-z]\b", re.I), True),
    ("an IFSC", re.compile(r"\b[a-z]{4}0[a-z0-9]{6}\b", re.I), True),
    ("an Aadhaar number", re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)"), True),
    ("a phone number", re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)"), True),
    ("a link", re.compile(r"https?://|www\.", re.I), False),
    ("a long number", re.compile(r"\d{6,}"), False),
)


def _tokens(text: str) -> List[str]:
    return [w for w in _SPLIT.split(str(text or "").lower()) if w]


def _numbers(text: str) -> List[str]:
    return [n.replace(",", "") for n in re.findall(r"\d[\d,]*", str(text or ""))]


def _name_part(value: str) -> str:
    """The name a value starts with: "Saurabh Kumar, 34, B.Com" -> "Saurabh Kumar"."""
    return " ".join(re.split(r"[,(;:|/\d]|\s[-–]\s", str(value or ""))[0].split()[:5])


def _proper(name: str) -> bool:
    """Written as a name ("Kumar Tea & Snacks"), not as a description ("chai ki dukaan", "a tea stall")."""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'&.]*", name) if len(w) >= 3]
    return bool(words) and all(w[0].isupper() for w in words)


def _street(address: str) -> str:
    """An address without its town: the street, house and locality identify someone; the town alone doesn't."""
    a = re.sub(r"[\s,-]*\d{6}\s*$", "", str(address or "")).strip(" ,")  # the PIN code goes with the town
    parts = [p.strip() for p in a.split(",") if p.strip()]
    if len(parts) > 1:
        return ", ".join(parts[:-1])
    words = a.split()
    return " ".join(words[:-1]) if len(words) > 1 else ""


class Privacy:
    """What may never leave in a query or a link: the caller's name, their organisation's name, their
    street, a way to reach them, an ID or account number, and the figures they gave. Places (a town) and general
    terms (a scheme's or a bank's name, their trade) may."""

    def __init__(self):
        self.words: set = set()      # a person's names, a street's words, an organisation's own word
        self.phrases: set = set()    # an organisation's whole name ("sharma dairy farm")
        self.numbers: set = set()    # the caller's figures (not years)

    def _person(self, name: Optional[str]) -> None:
        self.words |= {t for t in _tokens(name) if len(t) >= 3 and t not in _COMMON and not t.isdigit()}

    def _org(self, name: Optional[str]) -> None:
        toks = [t for t in _tokens(name) if not t.isdigit()]
        if len(toks) > 1:
            self.phrases.add(" ".join(toks))
        own = next((t for t in toks if len(t) >= 3 and t not in _COMMON), None)
        if own:  # "Sunrise Tea Stall": "sunrise" names them; "tea stall" is their trade
            self.words.add(own)

    def _figures(self, text: Optional[str]) -> None:
        self.numbers |= {n for n in _numbers(text) if len(n) >= 3 and not _YEAR.fullmatch(n)}

    @classmethod
    def of(cls, brief: dict, caller: Optional[str] = None) -> Privacy:
        """From the brief: the caller's saved details, the facts that name or reach them, and every figure they
        gave."""
        p = cls()
        saved = brief.get("saved_details") or {}
        p._person(saved.get("name"))
        p._org(saved.get("organisation"))
        p._person(_street(saved.get("address") or ""))
        for v in saved.values():
            p._figures(v)
        for label, value in (brief.get("facts") or {}).items():
            label, value = str(label), str(value or "")
            p._figures(value)
            part = _name_part(value)
            if _ORG_NAME.search(label):
                p._org(part)
            elif _PERSON.search(label):
                p._person(part)
            elif _ADDRESS.search(label):
                p._person(_street(value))
            elif _ORG_ISH.search(label) and _proper(part):
                p._org(part)
            if "@" in value:  # an email or UPI id: the name before the @
                p._person(" ".join(w.split("@")[0] for w in value.split() if "@" in w))
        if caller:
            p.numbers.add(re.sub(r"\D", "", caller)[-10:])
        return p

    def problem(self, text: str, link: bool = False) -> Optional[str]:
        """What in `text` (a query, or a link when `link`) is the caller's, or None."""
        for what, pattern, in_links in _PATTERNS:
            if (in_links or not link) and pattern.search(text or ""):
                return what
        toks = _tokens(text)
        joined = f" {' '.join(toks)} "
        if self.words & set(toks) or any(f" {ph} " in joined for ph in self.phrases):
            return "the caller's name or details"
        if self.numbers & set(_numbers(text)):
            return "the caller's own figures"
        return None


# ------------------------------------------------------------------------------------------------ search
class Searcher(Protocol):
    async def search(self, query: str) -> List[Dict[str, str]]: ...


class Serper:
    """Google results through Serper (serper.dev): about $1 per 1,000 searches, and the best at Indian scheme and
    government pages. Anything with search(query) -> [{title, url, snippet}] can replace it (Brave, say)."""

    URL = "https://google.serper.dev/search"

    def __init__(self, key: str, http: Optional[httpx.AsyncClient] = None):
        self.key = key
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5))

    async def search(self, query: str) -> List[Dict[str, str]]:
        try:
            r = await self.http.post(self.URL, headers={"X-API-KEY": self.key},
                                     json={"q": query, "gl": "in", "hl": "en", "num": 10})
        except httpx.HTTPError as e:
            raise ResearchError(f"the search didn't go through ({type(e).__name__}); try again") from e
        if r.status_code != 200:
            raise ResearchError(f"the search didn't go through (HTTP {r.status_code})")
        out = []
        for item in r.json().get("organic") or []:
            url = str(item.get("link") or "")
            if url.startswith(("https://", "http://")):
                out.append({"title": " ".join(str(item.get("title") or "").split())[:200], "url": url,
                            "snippet": " ".join(str(item.get("snippet") or "").split())[:300],
                            **({"date": str(item["date"])[:40]} if item.get("date") else {})})
        return out[:RESULTS]


# ------------------------------------------------------------------------------------------------ pages
_SKIP = {"script", "style", "noscript", "svg", "template", "head", "iframe", "object", "canvas", "select", "button"}
_CHROME = {"nav", "header", "footer", "aside", "form"}   # menus and boilerplate, unless that's all there is
_BREAK = {"p", "div", "section", "article", "main", "li", "ul", "ol", "dl", "dt", "dd", "h1", "h2", "h3", "h4",
          "h5", "h6", "tr", "table", "br", "blockquote", "pre", "figcaption", "caption", "hr", "tbody", "thead"}


class _Text(HTMLParser):
    def __init__(self, chrome: bool):
        super().__init__(convert_charrefs=True)
        self.skip_tags = _SKIP | (set() if chrome else _CHROME)
        self.depth: Dict[str, int] = {}
        self.parts: List[str] = []
        self.title = ""
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag == "body":  # an unclosed <head> (allowed in HTML) ends here
            self.depth.pop("head", None)
        if tag in self.skip_tags:
            self.depth[tag] = self.depth.get(tag, 0) + 1
        if tag in _BREAK:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_startendtag(self, tag, attrs):
        if tag in _BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if self.depth.get(tag):
            self.depth[tag] -= 1
        if tag in _BREAK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not any(self.depth.values()):
            self.parts.append(data)


def _clean(text: str) -> str:
    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip(" |")
        if line:
            lines.append(line)
    return "\n".join(lines)


def html_text(html: str) -> Tuple[str, str]:
    """(title, text) of an HTML page: menus, headers and footers left out unless the page is nothing else."""
    title, text = "", ""
    for chrome in (False, True):
        p = _Text(chrome)
        try:
            p.feed(html)
            p.close()
        except Exception:  # malformed markup: what was parsed so far
            pass
        title, text = " ".join(p.title.split()), _clean("".join(p.parts))
        if len(text) >= 80:
            break
    return title, text


_STOP_WORDS = ("the and for with from what which how why when where who are was were has have not about into its "
               "this that these those per under over than more most also india indian")
_STOP = frozenset(_STOP_WORDS.split())  # never what a passage is about


def excerpt(text: str, looking_for: str = "", limit: int = MAX_CHARS) -> Tuple[str, bool]:
    """The page's passages about `looking_for` (in page order, with the line before each for context), else its
    start; at most `limit` characters. (text, whether anything was left out)."""
    paras = [p for p in text.split("\n") if p.strip()]
    if sum(len(p) + 1 for p in paras) <= limit:
        return "\n".join(paras), False
    terms = [w for w in _tokens(looking_for) if len(w) >= 3 and w not in _STOP]
    picked: List[int] = []
    if terms:
        scores = [sum(1 for w in terms if w in p.lower()) for p in paras]
        ranked = sorted((i for i, s in enumerate(scores) if s), key=lambda i: (-scores[i], i))
        size = 0
        for i in ranked:
            want = [j for j in (i - 1, i) if j >= 0 and j not in picked]
            add = sum(len(paras[j]) + 1 for j in want)
            if size + add > limit:
                continue
            picked += want
            size += add
    if not picked:
        out, size = [], 0
        for p in paras:
            if size + len(p) + 1 > limit:
                break
            out.append(p)
            size += len(p) + 1
        return "\n".join(out), True
    picked.sort()
    out = []
    for n, j in enumerate(picked):
        if n and j != picked[n - 1] + 1:
            out.append("…")
        out.append(paras[j])
    return "\n".join(out), True


Resolve = Callable[[str], Awaitable[List[str]]]


async def _resolve(host: str) -> List[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return [str(i[4][0]) for i in infos]


def official(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == d.lstrip(".") or host.endswith(d if d.startswith(".") else "." + d) for d in OFFICIAL)


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


class Pages:
    """Fetches pages for the writer, from control, guarded: https, the standard port, public addresses only (an
    internal service or the cloud's metadata address is never reached), every redirect checked again, a size cap,
    and only what the site's robots.txt lets BolDocBot read. One page at a time, asked for by the writer: no crawl."""

    def __init__(self, http: Optional[httpx.AsyncClient] = None, resolve: Resolve = _resolve):
        self.http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(12, connect=5),
            headers={"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8"})
        self.resolve = resolve
        self.robots: Dict[str, Tuple[float, RobotFileParser]] = {}

    async def _allowed(self, url: str) -> str:
        if url.startswith("http://"):
            url = "https://" + url[len("http://"):]  # the same page over https: plain http is never fetched
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise ResearchError("only https pages can be read")
        try:
            port = parts.port
        except ValueError:
            raise ResearchError("that link isn't valid") from None
        if port not in (None, 443):
            raise ResearchError("only pages on the standard https port can be read")
        host = parts.hostname
        try:
            addrs = [host] if _is_ip(host) else await asyncio.wait_for(self.resolve(host), 5)
        except (OSError, asyncio.TimeoutError):
            raise ResearchError("that site couldn't be found") from None
        if not addrs or not all(_is_public(a) for a in addrs):
            raise ResearchError("that address isn't a public website")
        return url

    async def _get(self, url: str, max_bytes: int = MAX_BYTES,
                   any_type: bool = False) -> Tuple[int, str, str, bytes, Optional[str]]:
        """(status, final url, content type, body, charset): redirects followed, each checked again."""
        for _ in range(5):
            url = await self._allowed(url)
            try:
                async with self.http.stream("GET", url, follow_redirects=False) as r:
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        url = urljoin(url, r.headers["location"])
                        continue
                    if r.status_code != 200:
                        return r.status_code, url, "", b"", None
                    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    if ctype and not any_type and not ctype.startswith(_READABLE):
                        raise ResearchError(f"not a web page or PDF ({ctype})")
                    body = bytearray()
                    async for chunk in r.aiter_bytes():
                        body += chunk
                        if len(body) > max_bytes:
                            raise ResearchError(f"the page is too big to read (over {max_bytes // 1_000_000} MB)")
                    return 200, url, ctype, bytes(body), r.charset_encoding
            except httpx.HTTPError as e:
                raise ResearchError(f"the page couldn't be fetched ({type(e).__name__})") from None
        raise ResearchError("the page redirects too many times")

    async def _robots(self, url: str) -> RobotFileParser:
        """The site's robots.txt, kept an hour (RFC 9309: none, or a 4xx, allows everything; a site that can't
        say, 5xx or unreachable, allows nothing for now)."""
        host = urlsplit(url).hostname or ""
        hit = self.robots.get(host)
        if hit is not None and time.time() - hit[0] < ROBOTS_TTL_S:
            return hit[1]
        rp = RobotFileParser()
        try:
            status, _, _, body, charset = await self._get(f"https://{host}/robots.txt", 500_000, any_type=True)
            if status == 200:
                rp.parse(body.decode(charset or "utf-8", errors="replace").splitlines())
            elif 400 <= status < 500:
                rp.parse([])
            else:
                rp.parse(["User-agent: *", "Disallow: /"])
        except ResearchError:
            rp.parse(["User-agent: *", "Disallow: /"])
        self.robots[host] = (time.time(), rp)
        return rp

    async def fetch(self, url: str) -> Tuple[str, str, bytes, Optional[str]]:
        """(final url, content type, body, charset) of a page the site lets robots read."""
        url = await self._allowed(url)  # a link that can't be fetched is refused before its site is asked
        if not (await self._robots(url)).can_fetch(ROBOT, url):
            raise ResearchError("the site asks robots not to read this page: use another source")
        status, final, ctype, body, charset = await self._get(url)
        if status != 200:
            raise ResearchError(f"the page answered HTTP {status}")
        return final, ctype, body, charset

    async def read(self, url: str, looking_for: str = "") -> Dict[str, object]:
        """{url, title, text, more}: the page's text about `looking_for`, at most MAX_CHARS."""
        try:
            return await asyncio.wait_for(self._read(url, looking_for), PAGE_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise ResearchError("the page took too long to read: use another source") from None

    async def _read(self, url: str, looking_for: str) -> Dict[str, object]:
        final, ctype, body, charset = await self.fetch(url)
        if ctype == "application/pdf" or body[:5] == b"%PDF-":
            try:
                title, text, _ = await pdf_text(body, PDF_PAGES)
            except ValueError as e:
                raise ResearchError(str(e)) from None
            text = _clean(text)
        else:
            html = body.decode(charset or _sniff_charset(body) or "utf-8", errors="replace")
            title, text = html_text(html) if ctype != "text/plain" else ("", _clean(html))
        if len(text) < 40:
            raise ResearchError("the page has no readable text (it may need a browser to show it)")
        text, more = excerpt(text, looking_for)
        return {"url": final, "title": title[:200], "text": text, "more": more}


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _is_public(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def _sniff_charset(body: bytes) -> Optional[str]:
    m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:4096], re.I)
    if not m:
        return None
    name = m.group(1).decode("ascii", "replace")
    try:
        "".encode(name)
        return name
    except LookupError:
        return None


class Research:
    """The two tools' engines for BigDocs: a search provider and the page reader."""

    def __init__(self, searcher: Searcher, pages: Optional[Pages] = None):
        self.searcher, self.pages = searcher, pages or Pages()
