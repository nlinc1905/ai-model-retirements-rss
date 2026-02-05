import csv
import re
import requests
import xml.etree.ElementTree as ET
from email.utils import format_datetime
from pathlib import Path
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from typing import List, Dict, Optional

###############################################################################
# Constants
###############################################################################

CLAUDE_URL = "https://platform.claude.com/docs/en/about-claude/model-deprecations"
AWS_URL = "https://docs.aws.amazon.com/bedrock/latest/userguide/model-lifecycle.html"
AZURE_URL = "https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements"

OUTPUT_PATH = "output"
OUTPUT_CSV = "model_retirements.csv"

GITHUB_PAGES_LINK = "https://nlinc1905.github.io/azure-ai-model-retirements-rss/"

DATE_SUFFIX_RE = re.compile(r"-\d{8}$")
CLAUDE_DATE_RE = re.compile(r"([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})")
AWS_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

###############################################################################
# Shared normalization utilities
###############################################################################

def normalize_model_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return name
    return DATE_SUFFIX_RE.sub("", name.strip())


def normalize_date_from_text(text: Optional[str]) -> Optional[str]:
    """
    Extract the earliest date from text and normalize to YYYY-MM-DD.
    Supports:
      - February 19, 2026
      - Not sooner than February 19, 2026
      - 9/23/2025
      - ISO dates embedded in text
    """
    if not text:
        return None

    # ISO dates first
    iso_matches = ISO_DATE_RE.findall(text)
    dates = []

    for m in iso_matches:
        try:
            dates.append(datetime.strptime(m, "%Y-%m-%d").date())
        except ValueError:
            pass

    # Claude-style dates
    match = CLAUDE_DATE_RE.search(text)
    if match:
        month, day, year = match.groups()
        try:
            dates.append(
                datetime.strptime(f"{month} {day} {year}", "%B %d %Y").date()
            )
        except ValueError:
            pass

    # AWS-style dates
    match = AWS_DATE_RE.search(text)
    if match:
        month, day, year = match.groups()
        try:
            dates.append(datetime(int(year), int(month), int(day)).date())
        except ValueError:
            pass

    if not dates:
        return None

    return min(dates).strftime("%Y-%m-%d")


def deduplicate_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    Deduplicate by model_name within a source.
    Keep earliest retirement_date.
    Prefer non-empty recommended_replacement.
    """
    best: dict[str, Dict[str, str]] = {}

    for row in rows:
        key = row["model_name"]
        date = datetime.strptime(row["retirement_date"], "%Y-%m-%d").date()

        if key not in best:
            best[key] = row
            continue

        existing = best[key]
        existing_date = datetime.strptime(
            existing["retirement_date"], "%Y-%m-%d"
        ).date()

        if date < existing_date:
            best[key] = row
        elif date == existing_date:
            if row.get("recommended_replacement") and not existing.get("recommended_replacement"):
                best[key] = row

    return list(best.values())

###############################################################################
# Scrapers
###############################################################################

def scrape_claude() -> List[Dict[str, str]]:
    resp = requests.get(CLAUDE_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    rows = []

    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if not any("retire" in h for h in headers):
            continue

        header_map = {}
        for i, h in enumerate(headers):
            if "retire" in h:
                header_map["retirement_date"] = i
            elif "model" in h:
                header_map.setdefault("model_name", i)
            elif "replacement" in h:
                header_map["recommended_replacement"] = i

        for tr in table.find_all("tr")[1:]:
            tds = tr.find_all("td")
            if len(tds) < len(header_map):
                continue

            raw_date = tds[header_map["retirement_date"]].get_text(" ", strip=True)
            raw_model = tds[header_map["model_name"]].get_text(" ", strip=True)
            raw_repl = (
                tds[header_map["recommended_replacement"]].get_text(" ", strip=True)
                if "recommended_replacement" in header_map
                else ""
            )

            date = normalize_date_from_text(raw_date)
            if not date:
                continue

            rows.append({
                "source": CLAUDE_URL,
                "model_name": normalize_model_name(raw_model),
                "retirement_date": date,
                "recommended_replacement": normalize_model_name(raw_repl),
            })

    return deduplicate_rows(rows)


def scrape_aws() -> List[Dict[str, str]]:
    resp = requests.get(AWS_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    header = soup.find(lambda t: t.name == "h2" and "Active versions" in t.text)
    if not header:
        return []

    table = header.find_next("table")
    headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]

    model_idx = headers.index("model name")
    date_idx = headers.index("eol date")

    rows = []

    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        raw_date = tds[date_idx].get_text(" ", strip=True)
        date = normalize_date_from_text(raw_date)
        if not date:
            continue

        rows.append({
            "source": AWS_URL,
            "model_name": normalize_model_name(tds[model_idx].get_text(strip=True)),
            "retirement_date": date,
            "recommended_replacement": "",
        })

    return deduplicate_rows(rows)


def scrape_azure() -> List[Dict[str, str]]:
    resp = requests.get(AZURE_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Find the start and end headers
    start_h2 = soup.find("h2", string=lambda s: s and "current models" in s.lower())
    end_h2 = soup.find("h2", string=lambda s: s and "fine-tuned models" in s.lower())

    if not start_h2 or not end_h2:
        raise RuntimeError("Could not locate expected h2 boundaries")

    # Collect everything between the two h2 headers
    section_nodes = []
    node = start_h2.next_sibling
    while node and node != end_h2:
        if getattr(node, "name", None):
            section_nodes.append(node)
        node = node.next_sibling

    # Wrap extracted content in a new soup fragment for downstream parsing
    current_models_soup = BeautifulSoup(
        "".join(str(n) for n in section_nodes),
        "html.parser",
    )

    rows = []

    # Iterate ALL tables in all tabs (Text, Audio, Image/Video, Embeddings)
    for table in current_models_soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]

        if "model name" not in headers or "retirement date" not in headers:
            continue

        model_idx = headers.index("model name")
        date_idx = headers.index("retirement date")
        repl_idx = headers.index("replacement model") if "replacement model" in headers else None

        for tr in table.find_all("tr")[1:]:
            tds = tr.find_all("td")
            if len(tds) < max(model_idx, date_idx) + 1:
                continue

            raw_date = tds[date_idx].get_text(" ", strip=True)
            date = normalize_date_from_text(raw_date)
            if not date:
                continue

            rows.append({
                "source": AZURE_URL,
                "model_name": normalize_model_name(
                    tds[model_idx].get_text(strip=True)
                ),
                "retirement_date": date,
                "recommended_replacement": (
                    normalize_model_name(tds[repl_idx].get_text(strip=True))
                    if repl_idx is not None and repl_idx < len(tds)
                    else ""
                ),
            })

    return deduplicate_rows(rows)

###############################################################################
# RSS functions
###############################################################################

def write_rss(rows: List[Dict[str, str]], path: str) -> None:
    """
    Write model retirement changes to an RSS 2.0 feed.
    """
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = "AI Model Retirement Updates"
    ET.SubElement(channel, "link").text = GITHUB_PAGES_LINK
    ET.SubElement(channel, "description").text = (
        "Updates to retirement dates and replacements for AI foundation models "
        "from Claude, AWS Bedrock, and Azure OpenAI."
    )

    now = datetime.now(timezone.utc)
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(now)

    for row in rows:
        item = ET.SubElement(channel, "item")

        title = f"{row['model_name']} retirement update"
        ET.SubElement(item, "title").text = title

        description = (
            f"Source: {row['source']}\n"
            f"Model: {row['model_name']}\n"
            f"Retirement date: {row['retirement_date']}"
        )

        if row.get("recommended_replacement"):
            description += (
                f"\nRecommended replacement: {row['recommended_replacement']}"
            )

        ET.SubElement(item, "description").text = description
        ET.SubElement(item, "guid").text = (
            f"{row['source']}|{row['model_name']}|{row['retirement_date']}"
        )
        ET.SubElement(item, "pubDate").text = format_datetime(now)

    tree = ET.ElementTree(rss)
    tree.write(path, encoding="utf-8", xml_declaration=True)

###############################################################################
# Main
###############################################################################

def load_existing_csv(path: str) -> Dict[tuple, Dict[str, str]]:
    """
    Load existing CSV into a dict keyed by (source, model_name).
    """
    existing = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row["source"], row["model_name"])
            existing[key] = row
    return existing


def diff_rows(
    new_rows: List[Dict[str, str]],
    existing_rows: Dict[tuple, Dict[str, str]],
) -> List[Dict[str, str]]:
    """
    Return rows that are new or have changed fields.
    """
    changes = []

    for row in new_rows:
        key = (row["source"], row["model_name"])

        if key not in existing_rows:
            changes.append(row)
            continue

        existing = existing_rows[key]

        if (
            row["retirement_date"] != existing["retirement_date"]
            or row.get("recommended_replacement", "")
            != existing.get("recommended_replacement", "")
        ):
            changes.append(row)

    return changes


def write_csv(rows: List[Dict[str, str]], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "source",
                "model_name",
                "retirement_date",
                "recommended_replacement",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    all_rows = []
    all_rows.extend(scrape_claude())
    all_rows.extend(scrape_aws())
    all_rows.extend(scrape_azure())

    if not all_rows:
        raise RuntimeError("No data scraped — page structures may have changed.")

    output_path = Path(OUTPUT_PATH + "/" + OUTPUT_CSV)

    # First run: no existing file
    if not output_path.exists():
        write_csv(all_rows, OUTPUT_PATH + "/" + OUTPUT_CSV)
        write_rss(all_rows, OUTPUT_PATH + "/" + "rss.xml")
        print(f"Wrote {len(all_rows)} rows to {OUTPUT_PATH + "/" + OUTPUT_CSV} and initial rss.xml")
        raise SystemExit(0)

    # Subsequent runs: diff against existing data
    existing = load_existing_csv(OUTPUT_PATH + "/" + OUTPUT_CSV)
    changes = diff_rows(all_rows, existing)

    if not changes:
        print("No changes detected.")
        raise SystemExit(0)

    changes_csv = OUTPUT_PATH + "/" + "model_retirements_changes.csv"
    write_csv(changes, changes_csv)
    write_rss(all_rows, OUTPUT_PATH + "/" + "rss.xml")

    print(f"Wrote {len(changes)} changed rows to {changes_csv} and updated rss.xml")
