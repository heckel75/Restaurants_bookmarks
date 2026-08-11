"""Audit curation signals for eligible Paris Map MVP candidates.

Google Sheets access is strictly read-only. Eligibility and authentication are
delegated to audit_map_mvp_readiness so both audits use exactly the same rules.
Only local CSV reports are written.
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from audit_map_mvp_readiness import (
    ARRONDISSEMENTS,
    TARGET_PER_ARRONDISSEMENT,
    cell,
    evaluate_rows,
    get_worksheet,
    write_csv,
)


SCRIPT_DIR = Path(__file__).resolve().parent
REPORT_DIR = SCRIPT_DIR / "reports" / "map_mvp_curation"
READINESS_CANDIDATES_REPORT = (
    SCRIPT_DIR / "reports" / "map_mvp_readiness" / "eligible_candidates.csv"
)

SIGNAL_FIELDS = (
    "Favorite",
    "Cuisine",
    "Vibe",
    "Features",
    "Notes",
    "Website",
    "Instagram",
    "Facebook",
    "LLM Tagged at",
    "LLM Confidence",
    "Delivery",
    "Takeaway",
)

ABSENT_SIGNAL_VALUES = {
    "-",
    "n/a",
    "na",
    "none",
    "null",
    "unknown",
}

CANDIDATE_COLUMNS = (
    "Sheet Row Number",
    "Name",
    "Arrondissement",
    "Favorite",
    "Cuisine",
    "Vibe",
    "Features",
    "Notes",
    "Website",
    "Instagram",
    "Facebook",
    "LLM Tagged at",
    "LLM Confidence",
    "Delivery",
    "Takeaway",
    "Google Place ID",
    "Latitude",
    "Longitude",
)

SUMMARY_COLUMNS = (
    "Arrondissement",
    "Eligible Candidates",
    "Favorite TRUE",
    "Cuisine Non-Empty",
    "Vibe Non-Empty",
    "Features Non-Empty",
    "Notes Non-Empty",
    "Website",
    "Instagram",
    "Facebook",
    "LLM Tagged",
    "LLM Confidence",
    "Delivery",
    "Takeaway",
    "At Least One Structured Tag",
    "No Useful Curation Signal",
    "Basic Google Identity/Location Only",
)

DISTRIBUTION_COLUMNS = ("Tag", "Candidate Count", "Eligible Candidate Percentage")


def parse_tags(value: str) -> list[str]:
    """Split tags and discard empty or explicitly unavailable placeholders."""
    return [
        tag.strip()
        for tag in value.split(",")
        if tag.strip() and tag.strip().casefold() not in ABSENT_SIGNAL_VALUES
    ]


def is_favorite(candidate: dict[str, str]) -> bool:
    return candidate["Favorite"].strip().casefold() == "true"


def has_text(candidate: dict[str, str], field: str) -> bool:
    value = candidate[field].strip()
    return bool(value) and value.casefold() not in ABSENT_SIGNAL_VALUES


def candidate_tags(candidate: dict[str, str], field: str) -> list[str]:
    return parse_tags(candidate[field])


def has_structured_tag(candidate: dict[str, str]) -> bool:
    return any(candidate_tags(candidate, field) for field in ("Cuisine", "Vibe", "Features"))


def has_no_useful_signal(candidate: dict[str, str]) -> bool:
    """Apply the task's exact definition of no useful curation signal."""
    return (
        not is_favorite(candidate)
        and not has_structured_tag(candidate)
        and not any(
            has_text(candidate, field)
            for field in ("Notes", "Website", "Instagram", "Facebook")
        )
    )


def has_only_basic_identity_location(candidate: dict[str, str]) -> bool:
    """Return true when every audited curation/enrichment signal is absent."""
    return (
        has_no_useful_signal(candidate)
        and not any(
            has_text(candidate, field)
            for field in (
                "LLM Tagged at",
                "LLM Confidence",
                "Delivery",
                "Takeaway",
            )
        )
    )


def candidate_sort_key(candidate: dict[str, str]) -> tuple[Any, ...]:
    cuisine_populated = bool(candidate_tags(candidate, "Cuisine"))
    structured = has_structured_tag(candidate)
    return (
        int(candidate["Arrondissement"]),
        not is_favorite(candidate),
        not cuisine_populated,
        not structured,
        candidate["Name"].casefold(),
        int(candidate["Sheet Row Number"]),
    )


def validate_headers(values: list[list[str]]) -> tuple[list[str], dict[str, int]]:
    if not values:
        raise RuntimeError("The configured worksheet is empty.")
    headers = [str(header).strip() for header in values[0]]
    used_headers = set(CANDIDATE_COLUMNS[1:]) | set(SIGNAL_FIELDS)
    duplicates = sorted(
        header
        for header, count in Counter(headers).items()
        if header in used_headers and count > 1
    )
    if duplicates:
        raise RuntimeError(
            "Duplicate curation-signal headers: " + ", ".join(duplicates)
        )
    missing = sorted(header for header in used_headers if header not in headers)
    if missing:
        raise RuntimeError(
            "Missing curation-signal columns: " + ", ".join(missing)
        )
    return headers, {header: index for index, header in enumerate(headers)}


def build_candidates(
    values: list[list[str]],
    readiness_candidates: list[dict[str, str]],
) -> list[dict[str, str]]:
    _, column_indexes = validate_headers(values)
    candidates: list[dict[str, str]] = []
    for readiness_candidate in readiness_candidates:
        row_number = int(readiness_candidate["Sheet Row Number"])
        row = values[row_number - 1]
        candidates.append(
            {
                "Sheet Row Number": str(row_number),
                "Name": cell(row, column_indexes, "Name"),
                "Arrondissement": readiness_candidate["Arrondissement"],
                "Favorite": cell(row, column_indexes, "Favorite"),
                "Cuisine": cell(row, column_indexes, "Cuisine"),
                "Vibe": cell(row, column_indexes, "Vibe"),
                "Features": cell(row, column_indexes, "Features"),
                "Notes": cell(row, column_indexes, "Notes"),
                "Website": cell(row, column_indexes, "Website"),
                "Instagram": cell(row, column_indexes, "Instagram"),
                "Facebook": cell(row, column_indexes, "Facebook"),
                "LLM Tagged at": cell(row, column_indexes, "LLM Tagged at"),
                "LLM Confidence": cell(row, column_indexes, "LLM Confidence"),
                "Delivery": cell(row, column_indexes, "Delivery"),
                "Takeaway": cell(row, column_indexes, "Takeaway"),
                "Google Place ID": cell(row, column_indexes, "Google Place ID"),
                "Latitude": cell(row, column_indexes, "Latitude"),
                "Longitude": cell(row, column_indexes, "Longitude"),
            }
        )
    candidates.sort(key=candidate_sort_key)
    return candidates


def count_where(
    candidates: list[dict[str, str]],
    predicate: Callable[[dict[str, str]], bool],
) -> int:
    return sum(predicate(candidate) for candidate in candidates)


def build_summary(candidates: list[dict[str, str]]) -> list[dict[str, int]]:
    summary: list[dict[str, int]] = []
    for arrondissement in ARRONDISSEMENTS:
        arrondissement_candidates = [
            candidate
            for candidate in candidates
            if int(candidate["Arrondissement"]) == arrondissement
        ]
        summary.append(
            {
                "Arrondissement": arrondissement,
                "Eligible Candidates": len(arrondissement_candidates),
                "Favorite TRUE": count_where(arrondissement_candidates, is_favorite),
                "Cuisine Non-Empty": count_where(
                    arrondissement_candidates,
                    lambda candidate: bool(candidate_tags(candidate, "Cuisine")),
                ),
                "Vibe Non-Empty": count_where(
                    arrondissement_candidates,
                    lambda candidate: bool(candidate_tags(candidate, "Vibe")),
                ),
                "Features Non-Empty": count_where(
                    arrondissement_candidates,
                    lambda candidate: bool(candidate_tags(candidate, "Features")),
                ),
                "Notes Non-Empty": count_where(
                    arrondissement_candidates,
                    lambda candidate: has_text(candidate, "Notes"),
                ),
                "Website": count_where(
                    arrondissement_candidates,
                    lambda candidate: has_text(candidate, "Website"),
                ),
                "Instagram": count_where(
                    arrondissement_candidates,
                    lambda candidate: has_text(candidate, "Instagram"),
                ),
                "Facebook": count_where(
                    arrondissement_candidates,
                    lambda candidate: has_text(candidate, "Facebook"),
                ),
                "LLM Tagged": count_where(
                    arrondissement_candidates,
                    lambda candidate: has_text(candidate, "LLM Tagged at"),
                ),
                "LLM Confidence": count_where(
                    arrondissement_candidates,
                    lambda candidate: has_text(candidate, "LLM Confidence"),
                ),
                "Delivery": count_where(
                    arrondissement_candidates,
                    lambda candidate: has_text(candidate, "Delivery"),
                ),
                "Takeaway": count_where(
                    arrondissement_candidates,
                    lambda candidate: has_text(candidate, "Takeaway"),
                ),
                "At Least One Structured Tag": count_where(
                    arrondissement_candidates,
                    has_structured_tag,
                ),
                "No Useful Curation Signal": count_where(
                    arrondissement_candidates,
                    has_no_useful_signal,
                ),
                "Basic Google Identity/Location Only": count_where(
                    arrondissement_candidates,
                    has_only_basic_identity_location,
                ),
            }
        )
    return summary


def build_tag_counter(
    candidates: list[dict[str, str]],
    field: str,
) -> Counter[str]:
    counter: Counter[str] = Counter()
    for candidate in candidates:
        # A duplicated tag within one cell still describes one candidate, so
        # count each distinct tag at most once per candidate.
        counter.update(set(candidate_tags(candidate, field)))
    return counter


def distribution_rows(
    counter: Counter[str],
    total_candidates: int,
) -> list[dict[str, Any]]:
    return [
        {
            "Tag": tag,
            "Candidate Count": count,
            "Eligible Candidate Percentage": f"{count / total_candidates * 100:.2f}",
        }
        for tag, count in sorted(
            counter.items(),
            key=lambda item: (-item[1], item[0].casefold(), item[0]),
        )
    ]


def coverage_results(candidates: list[dict[str, str]]) -> list[tuple[str, int, float]]:
    metrics: tuple[tuple[str, Callable[[dict[str, str]], bool]], ...] = (
        ("Favorite TRUE", is_favorite),
        ("Cuisine", lambda candidate: bool(candidate_tags(candidate, "Cuisine"))),
        ("Vibe", lambda candidate: bool(candidate_tags(candidate, "Vibe"))),
        ("Features", lambda candidate: bool(candidate_tags(candidate, "Features"))),
        ("Notes", lambda candidate: has_text(candidate, "Notes")),
        ("Website", lambda candidate: has_text(candidate, "Website")),
        ("Instagram", lambda candidate: has_text(candidate, "Instagram")),
        ("Facebook", lambda candidate: has_text(candidate, "Facebook")),
        ("LLM Tagged at", lambda candidate: has_text(candidate, "LLM Tagged at")),
        ("LLM Confidence", lambda candidate: has_text(candidate, "LLM Confidence")),
        ("Delivery", lambda candidate: has_text(candidate, "Delivery")),
        ("Takeaway", lambda candidate: has_text(candidate, "Takeaway")),
    )
    total = len(candidates)
    return [
        (label, count := count_where(candidates, predicate), count / total * 100)
        for label, predicate in metrics
    ]


def classify_diversity(summary: list[dict[str, int]]) -> tuple[str, list[int], list[int]]:
    cuisine_ready = [
        row["Arrondissement"]
        for row in summary
        if row["Cuisine Non-Empty"] >= TARGET_PER_ARRONDISSEMENT
    ]
    structured_ready = [
        row["Arrondissement"]
        for row in summary
        if row["At Least One Structured Tag"] >= TARGET_PER_ARRONDISSEMENT
    ]
    if len(cuisine_ready) == len(list(ARRONDISSEMENTS)):
        classification = "strong"
    elif len(structured_ready) == len(list(ARRONDISSEMENTS)):
        classification = "partial"
    else:
        classification = "weak"
    return classification, cuisine_ready, structured_ready


def readiness_report_row_numbers() -> set[int] | None:
    if not READINESS_CANDIDATES_REPORT.exists():
        return None
    with READINESS_CANDIDATES_REPORT.open(
        "r", encoding="utf-8", newline=""
    ) as report_file:
        return {
            int(row["Sheet Row Number"])
            for row in csv.DictReader(report_file)
        }


def assert_audit_integrity(
    readiness_summary: list[dict[str, Any]],
    readiness_candidates: list[dict[str, str]],
    candidates: list[dict[str, str]],
    summary: list[dict[str, int]],
    tag_counters: dict[str, Counter[str]],
) -> None:
    assert len(summary) == 20, "Expected exactly 20 arrondissement summary rows."
    assert {row["Arrondissement"] for row in summary} == set(ARRONDISSEMENTS)

    readiness_total = sum(int(row["Eligible Rows"]) for row in readiness_summary)
    assert readiness_total == len(readiness_candidates) == len(candidates), (
        "Eligible total does not match the readiness audit."
    )

    readiness_rows = {
        int(candidate["Sheet Row Number"]) for candidate in readiness_candidates
    }
    curation_rows = {int(candidate["Sheet Row Number"]) for candidate in candidates}
    assert len(curation_rows) == len(candidates), "Duplicate candidates found."
    assert curation_rows == readiness_rows, "An ineligible row entered the audit."

    prior_report_rows = readiness_report_row_numbers()
    if prior_report_rows is not None:
        assert curation_rows == prior_report_rows, (
            "Eligible rows do not match the existing readiness audit report."
        )

    for field, counter in tag_counters.items():
        expected_assignments = sum(
            len(set(candidate_tags(candidate, field))) for candidate in candidates
        )
        assert sum(counter.values()) == expected_assignments, (
            f"{field} tag distribution does not reconcile."
        )

    assert candidates == sorted(candidates, key=candidate_sort_key), (
        "Candidate sorting is incorrect."
    )
    assert sum(row["Eligible Candidates"] for row in summary) == len(candidates)


def print_table(summary: list[dict[str, int]]) -> None:
    columns = (
        ("Arr", "Arrondissement"),
        ("Eligible", "Eligible Candidates"),
        ("Fav TRUE", "Favorite TRUE"),
        ("Cuisine", "Cuisine Non-Empty"),
        ("Vibe", "Vibe Non-Empty"),
        ("Features", "Features Non-Empty"),
        ("Notes", "Notes Non-Empty"),
        ("Website", "Website"),
        ("Instagram", "Instagram"),
        ("LLM tagged", "LLM Tagged"),
        ("Structured", "At Least One Structured Tag"),
        ("No useful", "No Useful Curation Signal"),
    )
    widths = [
        max(len(label), max(len(str(row[field])) for row in summary))
        for label, field in columns
    ]

    def format_row(values: list[Any]) -> str:
        return "  ".join(
            str(value).rjust(width) for value, width in zip(values, widths)
        )

    print(format_row([label for label, _ in columns]))
    print(format_row(["-" * width for width in widths]))
    for row in summary:
        print(format_row([row[field] for _, field in columns]))


def print_results(
    candidates: list[dict[str, str]],
    summary: list[dict[str, int]],
    tag_counters: dict[str, Counter[str]],
    classification: str,
    cuisine_ready: list[int],
    structured_ready: list[int],
) -> None:
    print_table(summary)
    total = len(candidates)

    print("\nOverall field coverage (blank/UNKNOWN placeholders are unavailable):")
    for label, count, percentage in coverage_results(candidates):
        print(f"  {label}: {count}/{total} ({percentage:.2f}%)")

    print("\nDistinct tag counts:")
    for field in ("Cuisine", "Vibe", "Features"):
        print(f"  {field}: {len(tag_counters[field])}")

    print("\nCuisine tag frequency (candidates per tag):")
    for row in distribution_rows(tag_counters["Cuisine"], total):
        print(f"  {row['Tag']}: {row['Candidate Count']}")

    basic_only_count = count_where(candidates, has_only_basic_identity_location)
    no_useful_count = count_where(candidates, has_no_useful_signal)
    print(
        "\nCandidates containing only basic Google identity/location data: "
        f"{basic_only_count}/{total} ({basic_only_count / total * 100:.2f}%)"
    )
    print(
        "Candidates with no useful curation signal: "
        f"{no_useful_count}/{total} ({no_useful_count / total * 100:.2f}%)"
    )
    print(
        "Arrondissements with at least 15 Cuisine-tagged candidates: "
        + (", ".join(map(str, cuisine_ready)) or "none")
    )
    print(
        "Arrondissements with at least 15 candidates containing any structured tag: "
        + (", ".join(map(str, structured_ready)) or "none")
    )
    print(f"Automated diversity selection classification: {classification.upper()}")
    if classification == "strong":
        print("Automated diversity selection realistic: YES")
    elif classification == "partial":
        print("Automated diversity selection realistic: PARTIALLY")
    else:
        print("Automated diversity selection realistic: NO")


def main() -> int:
    try:
        worksheet = get_worksheet()
        values = worksheet.get_all_values()
        readiness_summary, readiness_candidates, _, _ = evaluate_rows(values)
        candidates = build_candidates(values, readiness_candidates)
        summary = build_summary(candidates)
        tag_counters = {
            field: build_tag_counter(candidates, field)
            for field in ("Cuisine", "Vibe", "Features")
        }

        assert_audit_integrity(
            readiness_summary,
            readiness_candidates,
            candidates,
            summary,
            tag_counters,
        )
        classification, cuisine_ready, structured_ready = classify_diversity(summary)

        low_information_candidates = [
            candidate for candidate in candidates if has_no_useful_signal(candidate)
        ]
        write_csv(
            REPORT_DIR / "curation_signal_summary.csv",
            SUMMARY_COLUMNS,
            summary,
        )
        write_csv(
            REPORT_DIR / "cuisine_distribution.csv",
            DISTRIBUTION_COLUMNS,
            distribution_rows(tag_counters["Cuisine"], len(candidates)),
        )
        write_csv(
            REPORT_DIR / "vibe_distribution.csv",
            DISTRIBUTION_COLUMNS,
            distribution_rows(tag_counters["Vibe"], len(candidates)),
        )
        write_csv(
            REPORT_DIR / "feature_distribution.csv",
            DISTRIBUTION_COLUMNS,
            distribution_rows(tag_counters["Features"], len(candidates)),
        )
        write_csv(
            REPORT_DIR / "low_information_candidates.csv",
            CANDIDATE_COLUMNS,
            low_information_candidates,
        )
        write_csv(
            REPORT_DIR / "curation_signal_candidates.csv",
            CANDIDATE_COLUMNS,
            candidates,
        )

        print_results(
            candidates,
            summary,
            tag_counters,
            classification,
            cuisine_ready,
            structured_ready,
        )
        print("\nAssertions: PASS")
        print(f"Local reports written to: {REPORT_DIR}")
        return 0
    except AssertionError as error:
        print(f"Audit assertion failed: {error}", file=sys.stderr)
    except Exception as error:
        print(f"Audit failed: {type(error).__name__}: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
