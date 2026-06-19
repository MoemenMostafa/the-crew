"""Entrypoint: load .env + crew.yaml and run the Crew until interrupted."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv

from .config import load_config
from .service import Crew


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env")
    config = load_config(root / "crew.yaml")

    crew = Crew(config)
    try:
        asyncio.run(crew.run_forever())
    except KeyboardInterrupt:
        print("\nCrew stopped.")


if __name__ == "__main__":
    main()
