"""
Weekly article finder for jocdoc.github.io

What this does, in plain terms:
  1. For each topic below, it asks PubMed for recent papers
     from trusted sports medicine journals.
  2. It skips anything already shown on the site before.
  3. It picks the newest paper per topic (free-to-read ones first).
  4. It saves the picks into articles.json, which the website reads.
  5. It writes a short summary that becomes the review email.

It uses only tools built into Python, so nothing extra needs installing.
"""

import json
import os
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date

# ---------------------------------------------------------------
# SETTINGS YOU CAN EDIT
# ---------------------------------------------------------------

# One card per topic. The name on the left is the label shown on the card.
# The text on the right is a PubMed search. [tiab] means "in the title or abstract".
TOPICS = {
    "Performance": (
        '("endurance training"[tiab] OR "aerobic training"[tiab] OR '
        '"interval training"[tiab] OR "running economy"[tiab] OR '
        '"athletic performance"[tiab])'
    ),
    "Injury Prevention": (
        '("injury prevention"[tiab] OR "injury risk"[tiab] OR '
        'hamstring[tiab] OR "anterior cruciate ligament"[tiab] OR '
        '"overuse injury"[tiab] OR "overuse injuries"[tiab])'
    ),
    "Recovery": (
        '(recovery[ti] OR sleep[tiab] OR "heart rate variability"[tiab] OR '
        'overtraining[tiab] OR "training load"[tiab])'
    ),
    # To add a concussion card, delete the # at the start of the next 4 lines.
    # "Concussion": (
    #     '(concussion[tiab] OR "mild traumatic brain injury"[tiab] OR '
    #     '"return to play"[tiab] OR "return to learn"[tiab])'
    # ),
}

# Only these journals are searched. Names must match PubMed's abbreviations.
JOURNALS = [
    "Br J Sports Med",
    "Am J Sports Med",
    "Sports Med",
    "Sports Health",
    "Clin J Sport Med",
    "J Athl Train",
    "Med Sci Sports Exerc",
    "Orthop J Sports Med",
    "J Orthop Sports Phys Ther",
]

# Only stronger study designs. Retractions, letters and editorials are excluded.
STUDY_TYPES = (
    '(systematic review[pt] OR meta-analysis[pt] OR '
    'randomized controlled trial[pt] OR review[pt])'
)
EXCLUDE = (
    'NOT (retracted publication[pt] OR retraction of publication[pt] OR '
    'comment[pt] OR editorial[pt] OR letter[pt])'
)

# How far back to look for new papers, in days.
LOOKBACK_DAYS = 90

# NCBI asks scripts to identify themselves in case something goes wrong.
CONTACT_EMAIL = "Pkilpatrick@novanthealth.org"

# ---------------------------------------------------------------
# THE MACHINERY (no need to edit below here)
# ---------------------------------------------------------------

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
DATA_FILE = "articles.json"


def ncbi_get(endpoint, params):
    """Send one request to PubMed and return the raw response text."""
    params = dict(params, tool="jocdoc-site", email=CONTACT_EMAIL)
    url = EUTILS + endpoint + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as response:
        text = response.read().decode("utf-8")
    time.sleep(0.5)  # PubMed allows 3 requests per second without a key
    return text


def search_ids(topic_query, free_only):
    """Return a list of PubMed IDs matching the topic, newest first."""
    journal_filter = "(" + " OR ".join(f'"{j}"[ta]' for j in JOURNALS) + ")"
    term = f"{topic_query} AND {journal_filter} AND {STUDY_TYPES} {EXCLUDE}"
    if free_only:
        term += " AND free full text[sb]"
    raw = ncbi_get("esearch.fcgi", {
        "db": "pubmed",
        "term": term,
        "retmax": 20,
        "retmode": "json",
        "datetype": "edat",
        "reldate": LOOKBACK_DAYS,
    })
    return json.loads(raw)["esearchresult"]["idlist"]


def text_of(element):
    """Get all the text inside an XML tag, including italics etc."""
    return "".join(element.itertext()).strip() if element is not None else ""


def shorten(text, limit=240):
    """Trim long text to about two lines, ending on a whole word."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "..."


def parse_articles(xml_text):
    """Turn PubMed's XML into a simple list of article details."""
    root = ET.fromstring(xml_text)
    results = []
    for item in root.findall("PubmedArticle"):
        citation = item.find("MedlineCitation")
        article = citation.find("Article")
        pmid = text_of(citation.find("PMID"))

        pub_date = article.find("Journal/JournalIssue/PubDate")
        year = text_of(pub_date.find("Year")) if pub_date is not None else ""
        if not year and pub_date is not None:
            year = text_of(pub_date.find("MedlineDate"))[:4]

        # Prefer the authors' conclusion as the draft description.
        parts = article.findall("Abstract/AbstractText")
        summary = ""
        for part in parts:
            label = (part.get("Label", "") + part.get("NlmCategory", "")).upper()
            if "CONCLUSION" in label:
                summary = text_of(part)
                break
        if not summary and parts:
            summary = text_of(parts[-1])

        pmc = ""
        for article_id in item.findall("PubmedData/ArticleIdList/ArticleId"):
            if article_id.get("IdType") == "pmc":
                pmc = text_of(article_id)

        if pmc:
            link = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmc}/"
        else:
            link = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

        results.append({
            "pmid": pmid,
            "title": text_of(article.find("ArticleTitle")).rstrip("."),
            "description": shorten(summary),
            "source": f"{text_of(article.find('Journal/ISOAbbreviation'))}, {year}",
            "url": link,
            "free": bool(pmc),
        })
    return results


def find_new_article(topic_query, already_shown):
    """Find the newest unseen paper for one topic, or None."""
    for free_only in (True, False):  # try free-to-read papers first
        ids = [i for i in search_ids(topic_query, free_only) if i not in already_shown]
        if ids:
            raw = ncbi_get("efetch.fcgi", {
                "db": "pubmed",
                "id": ",".join(ids[:5]),
                "retmode": "xml",
            })
            articles = parse_articles(raw)
            order = {pmid: n for n, pmid in enumerate(ids)}
            articles.sort(key=lambda a: order.get(a["pmid"], 99))
            if articles:
                return articles[0]
    return None


def main():
    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)

    shown = set(data.get("shown", []))
    current = {a["category"]: a for a in data.get("articles", [])}
    new_cards, fresh = [], []

    for category, query in TOPICS.items():
        pick = find_new_article(query, shown)
        if pick:
            pick["category"] = category
            shown.add(pick["pmid"])  # so another topic can't pick the same paper
            new_cards.append(pick)
            fresh.append(pick)
        elif category in current:
            new_cards.append(current[category])  # nothing new, keep last week's

    body_path = os.path.join(os.environ.get("RUNNER_TEMP", "."), "pr_body.md")

    if not fresh:
        with open(body_path, "w", encoding="utf-8") as f:
            f.write("No new articles this week.")  # the next step expects this file
        print("No new articles this week. Nothing changed.")
        return

    data["articles"] = new_cards
    data["shown"] = sorted(p for p in shown if p)
    data["updated"] = date.today().isoformat()

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    # Build the review note that GitHub emails you.
    lines = [
        "New article picks for the From the Field section.",
        "",
        "**To publish:** click Merge pull request, then Confirm merge.",
        "**To skip this week:** click Close pull request. The current cards stay up.",
        "",
        "Descriptions are the authors' own conclusions from the abstract. "
        "To reword one before publishing, open the Files changed tab, "
        "click the ... menu on articles.json, and choose Edit file.",
        "",
    ]
    for a in fresh:
        access = "free full text" if a["free"] else "abstract only, paywalled"
        lines += [
            f"### {a['category']}",
            f"**{a['title']}**",
            f"{a['source']} ({access})",
            a["url"],
            "",
            f"> {a['description']}",
            "",
        ]
    with open(body_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Proposed {len(fresh)} new article(s).")


if __name__ == "__main__":
    main()
