"""Rebuild the database from data/, with: uv run -m backend.seed"""

from backend.database import engine, location, seed


def main() -> None:
    """Rebuild the database and say what it holds."""
    db = engine()
    counts = seed(db)
    print(
        f"Seeded {counts['claims']} claims, {counts['policies']} policies, "
        f"{counts['customers']} customers and {counts['handlers']} handlers "
        f"into {location(db)}"
    )


if __name__ == "__main__":
    main()
