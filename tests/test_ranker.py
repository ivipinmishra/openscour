from openscour.server.fetcher import strip_back_matter
from openscour.server.providers import RawResult, filter_results
from openscour.server.ranker import Doc, answer_pattern, chunk_text, query_terms, rank


def test_who_question_ignores_names_from_the_question():
    docs = [
        Doc(
            url="https://refs.com",
            title="Vice President of the United States",
            text=(
                'Gup, Ted (1982). "Vice President Was Ready". The Washington Post. Archived from the original. '
                "Retrieved July 24, 2018. ↑ Vice President of the United States, United States Senate. ISBN 123."
            ),
            rank=0,
        ),
        Doc(
            url="https://intro.com",
            title="Vice President of the United States",
            text="JD Vance is the 50th and current vice president of the United States since January 20, 2025.",
            rank=1,
        ),
    ]
    assert rank("Who is vice president in the USA?", docs, chunks_per_doc=1)[0].doc.url == "https://intro.com"


def test_footnote_markers_are_removed():
    from openscour.server.fetcher import _FOOTNOTE

    assert _FOOTNOTE.sub("", r"were advanced.<sup>\[30\]</sup> The first[31] one") == "were advanced. The first one"


def test_who_is_prefers_current_holder_and_starts_at_answer():
    infobox = "| Style | Mr. Vice President (informal) The Honorable (formal) His Excellency (diplomatic) |"
    history = (
        "Vice President Kamala Harris served from 2021 to 2025. "
        "Since the 1830s, vice presidents have occupied offices near the Senate Chamber."
    )
    lead = (
        "Many vice presidents previously served in Congress. John Adams was the first vice president. "
        "JD Vance is the 50th and current vice president since January 20, 2025."
    )
    title = "Vice President of the United States"
    docs = [
        Doc(url="https://a.com", title=title, text=infobox, rank=0),
        Doc(url="https://b.com", title=title, text=history, rank=1),
        Doc(url="https://c.com", title=title, text=lead, rank=2),
    ]
    top = rank("Who is vice president in the USA?", docs, chunks_per_doc=1)[0]
    assert top.doc.url == "https://c.com"
    assert top.content.startswith("JD Vance is the 50th and current vice president")


def test_full_date_beats_bare_year():
    docs = [
        Doc(url="https://year.com", title="Elections", text="Don't miss the important 2026 midterm elections.", rank=0),
        Doc(url="https://date.com", title="Elections", text="The 2026 midterm election will be held on Tuesday, November 3.", rank=1),
    ]
    assert rank("When is the midterm election?", docs, chunks_per_doc=1)[0].doc.url == "https://date.com"


def test_back_matter_is_cut():
    body = "Main article text. " * 50
    text = f"{body}\n\n## References\n\n1. Some citation. Retrieved 2018.\n\n## External links\n\n- link"
    assert strip_back_matter(text) == body.rstrip()
    # A "Notes" heading near the top is part of the content, not back matter.
    early = f"## Notes\n\n{body}"
    assert strip_back_matter(early) == early


def test_when_question_prefers_passage_with_date():
    explainer = (
        "A midterm election is an election held halfway through the presidential term. "
        "Midterm elections decide control of Congress. Every midterm election matters for "
        "the midterm election cycle."
    )
    dated = "The 2026 midterm election is on Tuesday, Nov. 3, 2026. Polls open at 7 a.m."
    docs = [
        Doc(url="https://explainer.com", title="What is a midterm election", text=explainer, rank=0),
        Doc(url="https://state.gov", title="Upcoming election", text=dated, rank=1),
    ]
    ranked = rank("When is the mid term election?", docs, chunks_per_doc=1)
    assert ranked[0].doc.url == "https://state.gov"


def test_question_headings_are_not_answers():
    docs = [
        Doc(url="https://a.com", title="Election", text="When is the 2026 midterm election? Read on.", rank=0),
        Doc(url="https://b.com", title="Election", text="The midterm election is on November 3, 2026.", rank=1),
    ]
    assert rank("When is the midterm election?", docs, chunks_per_doc=1)[0].doc.url == "https://b.com"


def test_intents_and_joined_terms():
    assert answer_pattern("When did WW2 end?").search("It ended in 1945")
    assert answer_pattern("How tall is the Eiffel Tower?").search("330 metres")
    assert answer_pattern("Who founded SpaceX?").search("founded by Elon Musk")
    assert answer_pattern("What is RAG?") is None
    assert "midterm" in query_terms("mid term election")
    assert query_terms("mid term election", vocabulary={"midterm", "election"}) == ["election", "midterm"]
    assert query_terms("mid term election", vocabulary={"election"}) == ["mid", "term", "election"]


def test_boilerplate_passage_is_demoted():
    text = (
        "External links. Retrieved May 2010. Python release history, subscribe to our newsletter.\n"
        + "Filler sentence about something else entirely here. " * 20
        + "\nPython was first released in 1991 by Guido van Rossum as Python release 0.9."
    )
    docs = [Doc(url="https://a.com", title="Python history", text=text, rank=0)]
    assert "1991" in rank("When was Python first released?", docs, chunks_per_doc=1)[0].content


def test_relevant_page_ranks_first():
    docs = [
        Doc(url="https://a.com", title="Cooking pasta", text="Boil water. Add salt. Cook pasta for ten minutes.", rank=0),
        Doc(
            url="https://b.com",
            title="BM25 explained",
            text="BM25 is a ranking function. BM25 scores documents using term frequency and inverse document frequency.",
            rank=1,
        ),
    ]
    ranked = rank("how does bm25 ranking work", docs, chunks_per_doc=1)
    assert ranked[0].doc.url == "https://b.com"
    assert ranked[0].score == 1.0


def test_chunking_respects_target_size():
    text = " ".join(f"Sentence number {i} is here." for i in range(200))
    chunks = chunk_text(text, target_words=50)
    assert len(chunks) > 5
    assert all(len(c.split()) <= 60 for c in chunks)


def test_filter_dedupes_and_applies_domains():
    results = [
        RawResult("x", "https://www.docs.python.org/3/", ""),
        RawResult("x", "https://docs.python.org/3", ""),
        RawResult("y", "https://spam.example.com/page", ""),
        RawResult("z", "ftp://docs.python.org/file", ""),
    ]
    out = filter_results(results, include_domains=["python.org"], exclude_domains=[])
    assert [r.url for r in out] == ["https://www.docs.python.org/3/"]
    out = filter_results(results, include_domains=[], exclude_domains=["example.com"])
    assert len(out) == 1
