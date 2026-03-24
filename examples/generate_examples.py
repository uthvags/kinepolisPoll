"""
Example usage of matrix_vote_generator.

Run from the repo root:
    python examples/generate_examples.py
"""

import os
import sys

# Add parent dir to path so we can import matrix_vote_generator
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matrix_vote_generator import (
    generate_from_csv,
    generate_from_json,
    generate_voting_page,
    parse_csv_to_poll_data,
)

EXAMPLES_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(EXAMPLES_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ─── 1. From JSON file ──────────────────────────────────────────────────────────

def example_from_json():
    """Generate a lunch poll from a JSON file."""
    html = generate_from_json(os.path.join(EXAMPLES_DIR, "team_lunch.json"))
    path = os.path.join(OUTPUT_DIR, "lunch_from_json.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Created: {path}")


# ─── 2. From CSV file ───────────────────────────────────────────────────────────

def example_from_csv():
    """Generate a game night poll from a CSV file."""
    html = generate_from_csv(
        os.path.join(EXAMPLES_DIR, "game_night.csv"),
        title="Board Game Night Poll",
        storage_prefix="gamenight",
    )
    path = os.path.join(OUTPUT_DIR, "game_night_from_csv.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Created: {path}")


# ─── 3. From a Python dict (inline) ─────────────────────────────────────────────

def example_from_dict():
    """Build poll data in Python and generate directly."""
    poll_data = {
        "title": "Friday Drinks &mdash; Where should we go?",
        "storage_prefix": "drinks",
        "row_label": "Bar",
        "columns": ["This Friday", "Next Friday"],
        "items": [
            {
                "name": "The Pub",
                "rating": "4.3",
                "category": "Beer, Casual",
                "slots": {
                    "This Friday": ["17:00", "18:00", "19:00"],
                    "Next Friday": ["17:00", "18:00"],
                },
            },
            {
                "name": "Cocktail Lounge",
                "rating": "4.7",
                "category": "Cocktails, Upscale",
                "slots": {
                    "This Friday": ["18:00", "19:00", "20:00"],
                    "Next Friday": ["19:00", "20:00"],
                },
            },
            {
                "name": "Wine Bar",
                "rating": "4.5",
                "category": "Wine, Tapas",
                "slots": {
                    "This Friday": ["18:00", "19:00"],
                },
            },
        ],
    }
    html = generate_voting_page(poll_data)
    path = os.path.join(OUTPUT_DIR, "drinks_from_dict.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Created: {path}")


# ─── 4. CSV parse → modify → generate ───────────────────────────────────────────

def example_csv_then_modify():
    """Parse a CSV, modify the data programmatically, then generate."""
    poll_data = parse_csv_to_poll_data(
        os.path.join(EXAMPLES_DIR, "team_lunch.csv"),
        title="Team Lunch (Filtered)",
        storage_prefix="lunch_filtered",
    )

    # Filter: only keep items with rating >= 4.0
    poll_data["items"] = [
        item for item in poll_data["items"]
        if float(item.get("rating") or "0") >= 4.0
    ]

    # Add a custom row_label
    poll_data["row_label"] = "Restaurant"

    print(f"  Kept {len(poll_data['items'])} items after filtering (rating >= 4.0)")

    html = generate_voting_page(poll_data)
    path = os.path.join(OUTPUT_DIR, "lunch_filtered.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Created: {path}")


# ─── 5. Sprint planning from JSON ───────────────────────────────────────────────

def example_sprint_planning():
    """Generate a sprint planning vote page."""
    html = generate_from_json(os.path.join(EXAMPLES_DIR, "sprint_planning.json"))
    path = os.path.join(OUTPUT_DIR, "sprint_planning.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Created: {path}")


# ─── Run all ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Generating example voting pages...\n")

    print("1. Team lunch from JSON:")
    example_from_json()

    print("\n2. Game night from CSV:")
    example_from_csv()

    print("\n3. Friday drinks from Python dict:")
    example_from_dict()

    print("\n4. CSV parse -> filter -> generate:")
    example_csv_then_modify()

    print("\n5. Sprint planning from JSON:")
    example_sprint_planning()

    print(f"\nAll done! Open any HTML file in {OUTPUT_DIR}")
    print("Tip: add ?admin=true to the URL to see the admin editor.")
