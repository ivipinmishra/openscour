"""Chunk extracted pages and score the chunks against the query with BM25."""

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

STOPWORDS = frozenset(
    """a an and are as at be but by for from has have how i in is it its of on or
    that the this to was were what when where which who why will with you your do
    does did can""".split()
)

CHUNK_WORDS = 90

_MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_NUMBER_WORD = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|dozen|hundred|thousand|million|billion|trillion)"

# What kind of text answers a question, keyed by how the question starts.
INTENTS: list[tuple[re.Pattern, re.Pattern]] = [
    (
        re.compile(r"^\s*(when\b|what (year|date|day|month|time)\b|which (year|date|day)\b)", re.I),
        re.compile(
            rf"\b{_MONTH}\.? \d{{1,2}}\b|\b\d{{1,2}} {_MONTH}\b|\b(1[0-9]|20)\d{{2}}\b|\b\d{{1,2}}/\d{{1,2}}/\d{{2,4}}\b",
            re.I,
        ),
    ),
    (
        re.compile(r"^\s*(how (many|much|tall|long|far|old|big|high|deep|large|fast|heavy)\b|what is the (population|price|cost|size)\b)", re.I),
        re.compile(rf"\d|\b{_NUMBER_WORD}\b", re.I),
    ),
    (
        # "latest Python release", "what is the current version of Node.js"
        re.compile(r"\b(latest|current|newest)\b.*\b(version|release)s?\b", re.I),
        re.compile(r"\b[vV]?\d+\.\d+(?:\.\d+)?\b"),
    ),
    (
        re.compile(r"^\s*who\b", re.I),
        # "Barack Obama", "John F. Kennedy", "JD Vance", "J. D. Vance"
        re.compile(r"\b(?:[A-Z][a-z]+|[A-Z]{1,2}\.?(?: [A-Z]\.)?)(?: [A-Z]\.)? [A-Z][a-z]+\b"),
    ),
]

# "Who is the X?" asks about the present holder, not the history of the office.
PRESENT_TENSE = re.compile(r"^\s*(who|what) (is|are)\b", re.I)
# Only explicit cues: "since" and recent years also appear in history ("Since the
# 1830s…", "Kamala Harris, 2021-2025") and promoted former officeholders.
CURRENT = re.compile(r"\b(current(ly)?|incumbent|latest stable)\b", re.I)

# Forms of address that look like "Firstname Lastname" to the name pattern.
HONORIFICS = frozenset(
    """mr mrs ms dr sir madam his her their the honorable excellency majesty highness
    president senator governor speaker justice""".split()
)

# Navigation, references and other page chrome that matches keywords but answers nothing.
BOILERPLATE = re.compile(
    r"\bretrieved\b|external links|\[edit\]|\bcookies?\b|\bsubscribe\b|\bnewsletter\b|"
    r"all rights reserved|\bwishlist\b|sign up|log in|\badvertisement\b|share this|related articles|"
    r"archived from the original|\bisbn\b|\bdoi:|↑",
    re.I,
)


def tokenize(text: str) -> list[str]:
    return [t for t in _WORD.findall(text.lower()) if t not in STOPWORDS]


def query_terms(query: str, vocabulary: set[str] | None = None) -> list[str]:
    """Query tokens plus joined neighbours, so "mid term" also matches "midterm".

    When the joined form occurs in the pages, its halves are dropped: on its
    own, "term" would match unrelated text like "presidential term".
    """
    tokens = tokenize(query)
    joined = [(a, b, a + b) for a, b in zip(tokens, tokens[1:])]
    if vocabulary is None:
        return tokens + [j for _, _, j in joined]
    drop = {t for a, b, j in joined if j in vocabulary for t in (a, b)}
    return [t for t in tokens if t not in drop] + [j for _, _, j in joined if j in vocabulary]


def answer_pattern(query: str) -> re.Pattern | None:
    for question, answer in INTENTS:
        if question.search(query):
            return answer
    return None


_BARE_YEAR = re.compile(r"(1[0-9]|20)\d{2}")


def answer_strength(answer: re.Pattern, sentence: str, query: set[str]) -> int:
    """0 = no answer, 1 = vague answer (a bare year), 2 = specific answer.

    Matches made only of the question's own words don't count: "Vice President"
    looks like a name and "2026" like a date, but they don't answer the question.
    """
    best = 0
    for m in answer.finditer(sentence):
        words = set(_WORD.findall(m.group(0).lower()))
        if words - query - HONORIFICS - STOPWORDS:
            best = max(best, 1 if _BARE_YEAR.fullmatch(m.group(0)) else 2)
    return best


def _answer_sentences(chunk: str) -> list[str]:
    # Headings like "When is the election?" contain dates but are not answers.
    return [s for s in _SENTENCE_END.split(chunk) if not s.rstrip().endswith("?")]


def answer_first(chunk: str, query: set[str], answer: re.Pattern | None, present: bool) -> str:
    """Trim leading sentences so the passage starts at its best answering sentence."""
    if answer is None:
        return chunk
    sentences = _SENTENCE_END.split(chunk)

    def quality(s: str) -> int:
        if s.rstrip().endswith("?") or not query & set(tokenize(s)):
            return 0
        strength = answer_strength(answer, s, query)
        return strength + (1 if strength and present and CURRENT.search(s) else 0)

    best = max(range(len(sentences)), key=lambda i: quality(sentences[i]))
    if quality(sentences[best]) == 0:
        return chunk
    return " ".join(sentences[best:])


def chunk_multiplier(
    chunk: str,
    position: int,
    query: set[str],
    title: set[str],
    answer: re.Pattern | None,
    present: bool = False,
) -> float:
    m = 1.0
    # Pages tend to state the key fact near the top.
    m *= 1 + 0.25 * max(0.0, 1 - position / 8)

    if title:
        m *= 1 + 0.2 * len(title & set(tokenize(chunk))) / len(title)

    hits = len(BOILERPLATE.findall(chunk))
    if hits:
        m *= 0.75 ** min(hits, 6)

    if answer is not None:
        strengths = [(answer_strength(answer, s, query), s) for s in _answer_sentences(chunk)]
        answering = [(k, s) for k, s in strengths if k]
        near = [(k, s) for k, s in answering if query & set(tokenize(s))]
        if near:
            # An answer-shaped value right next to the question's words; a full
            # date beats a bare year.
            m *= 1.6 if max(k for k, _ in near) == 2 else 1.3
            if present and any(CURRENT.search(s) for _, s in near):
                m *= 2.0
        elif answering:
            m *= 1.1
        else:
            # Explainer pages repeat the question's keywords a lot; without an
            # answer-shaped value they shouldn't outrank a passage that has one.
            m *= 0.6
    return m


_LIST_ITEM = re.compile(r"(?:^|\s)(?:[-*•]|\d+\.)\s+")


def is_heading_list(chunk: str) -> bool:
    """A list of short headings, like a table of contents or a navigation menu.

    It matches many query words ("Best practices for RAG chunking") but says
    nothing; real list content has longer items or full sentences.
    """
    items = [i.strip() for i in _LIST_ITEM.split(chunk) if i.strip()]
    if len(items) < 3:
        return False
    avg_words = sum(len(i.split()) for i in items) / len(items)
    sentences = sum(1 for i in items if i.rstrip("*_ ").endswith((".", "!", "?")))
    return avg_words <= 9 and sentences / len(items) < 0.3


def chunk_text(text: str, target_words: int = CHUNK_WORDS) -> list[str]:
    """Split into roughly target_words-sized chunks on sentence boundaries."""
    chunks: list[str] = []
    for para in (p.strip() for p in text.split("\n")):
        if not para:
            continue
        current: list[str] = []
        count = 0
        for sentence in _SENTENCE_END.split(para):
            n = len(sentence.split())
            if current and count + n > target_words:
                chunks.append(" ".join(current))
                current, count = [], 0
            current.append(sentence)
            count += n
        if current:
            chunks.append(" ".join(current))

    # Merge tiny chunks (headings, captions) into their neighbour.
    merged: list[str] = []
    for c in chunks:
        if merged and len(merged[-1].split()) < target_words // 3:
            merged[-1] = f"{merged[-1]} {c}"
        else:
            merged.append(c)
    return merged


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = docs
        self.tf = [Counter(d) for d in docs]
        self.avgdl = (sum(len(d) for d in docs) / len(docs)) if docs else 0.0
        df: Counter[str] = Counter()
        for d in docs:
            df.update(set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, query: list[str], i: int) -> float:
        tf, dl = self.tf[i], len(self.docs[i])
        s = 0.0
        for t in query:
            f = tf.get(t)
            if not f:
                continue
            s += self.idf.get(t, 0.0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
        return s


# Words that ask for recency rather than name the topic.
FRESHNESS_WORDS = frozenset("latest newest recent recently new current news update updates today".split())
_YEAR = re.compile(r"\b(?:19[5-9]\d|20\d{2})\b")
_THIS_YEAR = date.today().year


def wants_fresh(query: str) -> bool:
    return bool(set(_WORD.findall(query.lower())) & FRESHNESS_WORDS)


def entity_terms(query: str) -> set[str]:
    """Capitalised words after the first, e.g. "AWS" and "Bedrock" in "Latest AWS Bedrock updates"."""
    words = re.findall(r"[A-Za-z0-9][\w.+-]*", query)[1:]
    return {
        t
        for w in words
        if w[0].isupper()
        for t in tokenize(w)
        if t not in FRESHNESS_WORDS
    }


def recency_multiplier(text: str, published: str | None = None) -> float:
    """Favour text dated this year; years after this one are plans, not news."""
    years = [int(y) for y in _YEAR.findall(f"{text} {published or ''}") if int(y) <= _THIS_YEAR]
    if not years:
        return 1.0
    newest = max(years)
    if newest == _THIS_YEAR:
        return 1.3
    if newest == _THIS_YEAR - 1:
        return 1.1
    return 0.8


def topic_multiplier(chunk_terms: set[str], topic: set[str], entities: set[str]) -> float:
    """Demote passages that miss part of the topic.

    BM25 saturates, so a passage repeating "AWS" and "updates" can outscore one
    about Bedrock; named things in the query ("Bedrock") must actually appear.
    """
    m = 1.0
    if topic:
        m *= 0.5 + 0.5 * len(topic & chunk_terms) / len(topic)
    return m * 0.6 ** len(entities - chunk_terms)


@dataclass
class Doc:
    url: str
    title: str
    text: str
    rank: int  # position in the search engine's result list
    published: str | None = None
    chunks: list[str] = field(default_factory=list)


@dataclass
class Ranked:
    doc: Doc
    score: float
    content: str


def rank(query: str, docs: list[Doc], chunks_per_doc: int) -> list[Ranked]:
    if not docs:
        return []
    answer = answer_pattern(query)
    version_question = answer is not None and answer.pattern == INTENTS[2][1].pattern
    present = bool(PRESENT_TENSE.search(query)) or version_question
    # Factual questions have their own answer-shaped scoring; topic searches
    # ("Latest AWS Bedrock updates") get topic coverage and freshness instead.
    topic_search = answer is None
    fresh = (topic_search or version_question) and wants_fresh(query)
    topic = set(tokenize(query)) - FRESHNESS_WORDS
    entities = entity_terms(query)

    index: list[tuple[int, int]] = []  # (doc idx, chunk idx)
    corpus: list[list[str]] = []
    doc_terms: list[set[str]] = []
    for di, d in enumerate(docs):
        d.chunks = chunk_text(d.text) or [d.text]
        terms = set(tokenize(d.title))
        for ci, c in enumerate(d.chunks):
            tokens = tokenize(c)
            index.append((di, ci))
            corpus.append(tokens)
            terms.update(tokens)
        doc_terms.append(terms)

    bm25 = BM25(corpus)
    q = query_terms(query, vocabulary=set(bm25.idf))
    qset = set(q)
    titles = [set(tokenize(d.title)) for d in docs]
    per_doc: dict[int, list[tuple[float, int]]] = {}
    for i, (di, ci) in enumerate(index):
        s = bm25.score(q, i)
        if s:
            chunk = docs[di].chunks[ci]
            s *= chunk_multiplier(chunk, ci, qset, titles[di], answer, present)
            if topic_search:
                s *= topic_multiplier(set(corpus[i]), topic, entities)
                if is_heading_list(chunk):
                    s *= 0.4
            if fresh:
                s *= recency_multiplier(chunk)
        per_doc.setdefault(di, []).append((s, ci))

    raw: list[tuple[float, int, list[int]]] = []
    for di, scored in per_doc.items():
        scored.sort(reverse=True)
        top = scored[:chunks_per_doc]
        best = top[0][0]
        # Coverage: share of query terms that appear anywhere in the page.
        coverage = len(qset & doc_terms[di]) / len(qset) if qset else 0.0
        # The search engine's own ordering is a useful prior.
        prior = 1.0 / (1 + 0.15 * docs[di].rank)
        score = best * (0.5 + 0.5 * coverage) * (0.7 + 0.3 * prior)
        if fresh:
            score *= recency_multiplier(docs[di].title, docs[di].published) ** 0.5
        raw.append((score, di, sorted(ci for _, ci in top)))

    top_score = max(s for s, _, _ in raw) or 1.0
    out = [
        Ranked(
            doc=docs[di],
            score=round(s / top_score, 4),
            content=" … ".join(answer_first(docs[di].chunks[ci], qset, answer, present) for ci in cis),
        )
        for s, di, cis in raw
    ]
    out.sort(key=lambda r: r.score, reverse=True)
    return out
