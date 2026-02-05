import csv
import requests
import re
from datetime import datetime
from bs4 import BeautifulSoup
from typing import List, Dict, Optional


URL = "https://platform.claude.com/docs/en/about-claude/model-deprecations"
OUTPUT_CSV = "claude_model_deprecations.csv"
DATE_SUFFIX_RE = re.compile(r"-\d{8}$")
DATE_RE = re.compile(
    r"(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4})"
)


def normalize_retirement_date(raw_date: Optional[str]) -> Optional[str]:
    """
    Normalize Claude retirement dates to YYYY-MM-DD.

    Handles:
        - "February 19, 2026"
        - "Not sooner than February 19, 2026"

    Returns:
        ISO-formatted date string (YYYY-MM-DD) or None
    """
    if not raw_date:
        return None

    match = DATE_RE.search(raw_date)
    if not match:
        raise ValueError(f"Unrecognized date format: {raw_date}")

    date_str = f"{match.group('month')} {match.group('day')} {match.group('year')}"
    parsed = datetime.strptime(date_str, "%B %d %Y")

    return parsed.strftime("%Y-%m-%d")


def normalize_model_name(model_name: Optional[str]) -> Optional[str]:
    """
    Remove a trailing YYYYMMDD date suffix from a Claude model name.

    Examples:
        claude-3-5-haiku-20241022 -> claude-3-5-haiku
        claude-opus-4-1-20250805 -> claude-opus-4-1
        claude-2.1 -> claude-2.1 (unchanged)
    """
    if not model_name:
        return model_name

    return DATE_SUFFIX_RE.sub("", model_name)


def deduplicate_deprecations(
    rows: List[Dict[str, str]]
) -> List[Dict[str, str]]:
    """
    Deduplicate rows by deprecated_model_name.

    Preference rules:
    - Keep the row with a non-empty recommended_replacement
    - If multiple rows have replacements, keep the first encountered
    - If none have replacements, keep the first encountered
    """
    deduped: dict[str, Dict[str, str]] = {}

    for row in rows:
        key = row["deprecated_model_name"]

        if key not in deduped:
            deduped[key] = row
            continue

        existing = deduped[key]

        existing_has_replacement = bool(existing.get("recommended_replacement"))
        current_has_replacement = bool(row.get("recommended_replacement"))

        # Replace only if the new row is strictly better
        if not existing_has_replacement and current_has_replacement:
            deduped[key] = row

    return list(deduped.values())


def get_text_content_of_table_cell(tds: List[BeautifulSoup], idx: int | str) -> str:
    """
    Get the text content of a table cell by index.
    
    :param tds: List of table cell elements.
    :param idx: Index of the table cell.

    :return: Text content of the cell or an empty string if the index is invalid.
    """
    if isinstance(idx, str):
        return ""
    return tds[idx].get_text(" ", strip=True)


def scrape_model_deprecations() -> List[Dict[str, str]]:
    """
    Scrape Claude's model deprecations page and return a list of deprecation entries.
    Each entry is a dictionary with keys: "retirement_date", "deprecated_model_name", "recommended_replacement".
    """
    resp = requests.get(URL, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    rows = []

    # The page currently presents deprecations in sections with tables.
    # We look for all tables and try to infer column meaning by headers.
    tables = soup.find_all("table")

    for table in tables:
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]

        # Heuristic: must contain these concepts
        if not any("retire" in h for h in headers):
            continue
        if not any("model" in h for h in headers):
            continue

        header_map = {}
        for i, h in enumerate(headers):
            if "retire" in h:
                header_map["retirement_date"] = i
            elif "deprecated" in h or "model" in h:
                header_map.setdefault("deprecated_model_name", i)
            elif "replacement" in h or "recommend" in h:
                header_map["recommended_replacement"] = i

        for tr in table.find_all("tr")[1:]:
            tds = tr.find_all("td")
            if len(tds) < len(header_map):
                continue

            retirement_date = get_text_content_of_table_cell(tds, header_map["retirement_date"])
            deprecated_model = get_text_content_of_table_cell(tds, header_map["deprecated_model_name"])
            replacement = get_text_content_of_table_cell(tds, header_map.get("recommended_replacement", ""))

            # Skip empty or malformed rows
            if not retirement_date or not deprecated_model:
                continue

            rows.append(
                {
                    "retirement_date": retirement_date,
                    "deprecated_model_name": deprecated_model,
                    "recommended_replacement": replacement,
                }
            )

    return rows


def write_csv(rows: List[Dict[str, str]], path: str) -> None:
    """
    Write a list of dictionaries to a CSV file.

    :param rows: List of dictionaries representing rows to write.
    :param path: Path to the output CSV file.
    """
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "retirement_date",
                "deprecated_model_name",
                "recommended_replacement",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


if __name__ == "__main__":
    # Scrape
    rows = scrape_model_deprecations()
    if not rows:
        raise RuntimeError("No deprecation data found — page structure may have changed.")

    # Normalize retirement dates and model names
    for row in rows:
        row["retirement_date"] = normalize_retirement_date(
            row["retirement_date"]
        )
        row["deprecated_model_name"] = normalize_model_name(
            row["deprecated_model_name"]
        )
        row["recommended_replacement"] = normalize_model_name(
            row.get("recommended_replacement")
        )

    # Deduplicate
    rows = deduplicate_deprecations(rows)

    write_csv(rows, OUTPUT_CSV)
    print(f"Wrote {len(rows)} rows to {OUTPUT_CSV}")
