import csv
import re
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from typing import List, Dict, Optional

URL = "https://docs.aws.amazon.com/bedrock/latest/userguide/model-lifecycle.html"
OUTPUT_CSV = "aws_model_deprecations.csv"

# Regex to match dates like "No sooner than 9/23/2025"
AWS_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def normalize_retirement_date(raw_date: Optional[str]) -> Optional[str]:
    """
    Normalize AWS-style dates to YYYY-MM-DD.
    Handles values like:
      - "No sooner than 9/23/2025"
      - "9/23/2025"
    """
    if not raw_date:
        return None

    match = AWS_DATE_RE.search(raw_date)
    if not match:
        return None

    month, day, year = match.groups()
    try:
        dt = datetime(int(year), int(month), int(day))
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


def scrape_aws_bedrock_active_versions() -> List[Dict[str, str]]:
    resp = requests.get(URL, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    rows: List[Dict[str, str]] = []

    # Find the "Active versions" section header
    header = soup.find(lambda tag: tag.name == "h2" and "Active versions" in tag.text)
    if not header:
        return rows

    # The Active Versions table should be right after the header
    table = header.find_next("table")
    if not table:
        return rows

    # Identify column indices for "Model name" and "EOL date"
    headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
    try:
        model_idx = headers.index("model name")
    except ValueError:
        return rows

    # EOL date could be labelled "eol date"
    try:
        eol_idx = headers.index("eol date")
    except ValueError:
        return rows

    # Extract rows
    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) <= max(model_idx, eol_idx):
            continue

        model_name = tds[model_idx].get_text(" ", strip=True)
        raw_eol = tds[eol_idx].get_text(" ", strip=True)
        retirement_date = normalize_retirement_date(raw_eol)

        # Skip rows with no valid date
        if not retirement_date:
            continue

        rows.append({
            "model_name": model_name,
            "retirement_date": retirement_date,
        })

    return rows


def write_csv(rows: List[Dict[str, str]], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["model_name", "retirement_date"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


if __name__ == "__main__":
    rows = scrape_aws_bedrock_active_versions()
    if not rows:
        raise RuntimeError("No active versions found — page structure may have changed.")
    write_csv(rows, OUTPUT_CSV)
    print(f"Wrote {len(rows)} rows to {OUTPUT_CSV}")
