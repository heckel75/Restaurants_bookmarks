"""
Temporary debug/helper script for Session 11 Google Places matching.

This script does not read from or write to Google Sheets. It builds a fake
restaurant row from command-line arguments, calls Google Places, and prints the
same candidate scoring explanations used by enrich_bookmarks.py.
"""

import argparse
import os
import subprocess
import sys


def maybe_reexec_with_venv():
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    venv_python = os.path.join(repo_dir, ".venv", "Scripts", "python.exe")

    if os.name != "nt" or not os.path.exists(venv_python):
        return

    if os.path.abspath(sys.executable).lower() == os.path.abspath(venv_python).lower():
        return

    completed = subprocess.run([venv_python] + sys.argv)
    sys.exit(completed.returncode)


maybe_reexec_with_venv()

from enrich_bookmarks import (
    build_location_expectation,
    clean_title,
    evaluate_google_candidate,
    google_place_candidate_details,
    print_candidate_explanations,
    select_best_candidate,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Debug Google Places candidate scoring without sheet access."
    )
    parser.add_argument("--name", required=True)
    parser.add_argument("--arrondissement")
    parser.add_argument("--town")
    parser.add_argument("--website")
    parser.add_argument("--instagram")
    parser.add_argument("--address")
    parser.add_argument("--max-candidates", type=int, default=5)
    return parser.parse_args()


def build_debug_row(args):
    name = clean_title(args.name)

    if args.arrondissement:
        folder = f"Paris {args.arrondissement}"
        city_hint = f"Paris {args.arrondissement}"
    elif args.town:
        folder = f"Suburb {args.town}"
        city_hint = args.town
    else:
        folder = "Unsorted"
        city_hint = ""

    return {
        "folder": folder,
        "title": name,
        "url": args.website or args.instagram or "",
        "website": args.website or "",
        "instagram": args.instagram or "",
        "arrondissement": args.arrondissement or "",
        "town": args.town or "",
        "city": "",
        "address": args.address or "",
        "postal_code": "",
        "city_hint": city_hint,
    }


def main():
    args = parse_args()
    max_candidates = max(1, min(args.max_candidates, 10))
    row = build_debug_row(args)

    location_expectation = build_location_expectation(
        row["folder"],
        arrondissement_hint=row["arrondissement"],
        town_hint=row["town"],
        city_hint=row["city_hint"],
    )

    query = f"{row['title']} {row['city_hint']}".strip()
    print(f"Debug query: {query}")
    print(f"Google Places candidates per search: {max_candidates}")

    google_candidates = google_place_candidate_details(query, max_candidates)
    evaluations = [
        evaluate_google_candidate(
            bookmark_title=row["title"],
            input_website=row["website"],
            candidate=candidate,
            location_expectation=location_expectation,
            address_hint=row["address"],
            postal_hint=row["postal_code"],
        )
        for candidate in google_candidates
    ]
    selected_candidate, review_reason = select_best_candidate(evaluations)

    if evaluations:
        print_candidate_explanations(evaluations, selected_candidate, review_reason)
    else:
        print("  -> No Google candidates returned")

    if selected_candidate:
        print(
            "ACCEPT "
            f"{selected_candidate.name} | "
            f"{selected_candidate.place_id} | "
            f"score {selected_candidate.score:g}"
        )
    else:
        print(f"REVIEW {review_reason}")


if __name__ == "__main__":
    main()
