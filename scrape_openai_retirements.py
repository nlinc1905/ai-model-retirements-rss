import re
import csv
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from typing import List, Dict, Optional

URL = "https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/model-retirements"
OUTPUT_CSV = "azure_foundry_model_deprecations.csv"

# Pattern to find ISO dates in text (YYYY-MM-DD)
ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def extract_earliest_date(text: str) -> Optional[str]:
    """
    Find all date substrings in text of the form YYYY-MM-DD,
    parse them, and return the earliest (ISO format).
    """
    matches = ISO_DATE_RE.findall(text or "")
    if not matches:
        return None

    # Parse all found matches and pick the earliest
    dates = []
    for m in matches:
        try:
            dates.append(datetime.strptime(m, "%Y-%m-%d").date())
        except ValueError:
            pass

    if not dates:
        return None

    earliest = min(dates)
    return earliest.strftime("%Y-%m-%d")


def scrape_azure_foundry_retirements() -> List[Dict[str, str]]:
    """
    Scrape the Azure AI Foundry OpenAI retirements table under
    Current models → Text generation and return a list of
    {model_name, retirement_date, recommended_replacement}.
    """
    resp = requests.get(URL, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # Find the "Text generation" table
    # Microsoft Learn page uses <h3> for the subheadings like "Text generation"
    text_gen_header = soup.find(
        lambda tag: tag.name in ("h3", "h4") and "Text generation" in tag.text
    )
    if not text_gen_header:
        raise RuntimeError("Could not find Text generation section")

    table = text_gen_header.find_next("table")
    if not table:
        raise RuntimeError("Could not find the table after Text generation")

    # Extract headers
    headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
    try:
        model_idx = headers.index("model name")
    except ValueError:
        raise RuntimeError("Unable to find Model Name column")

    # "Retirement Date" is specifically that column
    try:
        retirement_idx = headers.index("retirement date")
    except ValueError:
        raise RuntimeError("Unable to find Retirement Date column")

    # "Replacement Model" if present
    replacement_idx = None
    if "replacement model" in headers:
        replacement_idx = headers.index("replacement model")

    rows: List[Dict[str, str]] = []

    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) <= retirement_idx or len(tds) <= model_idx:
            continue

        raw_model = tds[model_idx].get_text(" ", strip=True)
        raw_retirement = tds[retirement_idx].get_text(" ", strip=True)
        raw_replacement = (
            tds[replacement_idx].get_text(" ", strip=True)
            if replacement_idx is not None and replacement_idx < len(tds)
            else ""
        )

        # Extract earliest ISO date in the retirement text
        retirement_date = extract_earliest_date(raw_retirement)

        if not retirement_date:
            # Skip if no parseable retirement date
            continue

        rows.append(
            {
                "model_name": raw_model,
                "retirement_date": retirement_date,
                "recommended_replacement": raw_replacement,
            }
        )

    return rows



def deduplicate_by_earliest_retirement(
    rows: List[Dict[str, str]]
) -> List[Dict[str, str]]:
    """
    Deduplicate rows by model_name, keeping the row with the earliest
    retirement_date. Assumes retirement_date is in YYYY-MM-DD format.
    """
    best_rows: dict[str, Dict[str, str]] = {}

    for row in rows:
        model = row["model_name"]
        current_date = datetime.strptime(row["retirement_date"], "%Y-%m-%d").date()

        if model not in best_rows:
            best_rows[model] = row
            continue

        existing = best_rows[model]
        existing_date = datetime.strptime(
            existing["retirement_date"], "%Y-%m-%d"
        ).date()

        # Keep the earliest retirement date
        if current_date < existing_date:
            best_rows[model] = row

    return list(best_rows.values())


def write_csv(rows: List[Dict[str, str]], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["model_name", "retirement_date", "recommended_replacement"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


if __name__ == "__main__":
    rows = scrape_azure_foundry_retirements()
    if not rows:
        raise RuntimeError("No rows scraped — page or structure may have changed")
    rows = deduplicate_by_earliest_retirement(rows)
    write_csv(rows, OUTPUT_CSV)
    print(f"Wrote {len(rows)} rows to {OUTPUT_CSV}")
