from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from app.config import settings

ROOT = Path(__file__).resolve().parent.parent

# Everything a server needs, and nothing else: an allowlist cannot pick up stray exports or logs.
INCLUDE = [
    "app", "scripts", "tests", "alembic", "docs",
    "pyproject.toml", "alembic.ini", "Dockerfile", ".dockerignore",
    "docker-compose.selfhost.yml", "docker-compose.cloud.yml", "docker-compose.gpu.yml",
    "install.sh", ".env.example", "README.md", "DEPLOY.md",
]
SKIPPED_PARTS = {"__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}


def _files(root: Path) -> list[Path]:
    found: list[Path] = []
    for entry in INCLUDE:
        path = root / entry
        if path.is_file():
            found.append(path)
        elif path.is_dir():
            found.extend(
                p for p in path.rglob("*") if p.is_file() and not SKIPPED_PARTS & set(p.parts)
            )
    return found


def _sqlite_path(database_url: str, root: Path) -> Path | None:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return None
    path = Path(url.database)
    return path if path.is_absolute() else root / path


def build_bundle(output: Path, root: Path = ROOT, with_data: bool = True, database_url: str = "") -> Path:
    """Pack the project, its settings and a consistent snapshot of the database into one zip.
    zipfile rather than a shell tool: Windows tools write backslash paths Linux unpacks wrongly."""
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in _files(root):
            archive.write(path, path.relative_to(root).as_posix())
        if with_data and (root / ".env").exists():
            archive.write(root / ".env", ".env")
        for configured in (settings.stacks_path, settings.seen_chats_path):
            path = root / configured  # an absolute setting (/data/...) replaces root here
            if with_data and path.exists():
                archive.write(path, f"data/{path.name}")

        database = _sqlite_path(database_url or settings.database_url, root)
        if with_data and database is not None and database.exists():
            with tempfile.TemporaryDirectory() as workdir:
                snapshot = Path(workdir) / "bot.db"
                # backup() stays consistent even while the bot is running and writing.
                source = sqlite3.connect(database)
                target = sqlite3.connect(snapshot)
                with target:
                    source.backup(target)
                source.close()
                target.close()
                archive.write(snapshot, "data/bot.db")
    return output


def main() -> None:
    """
    Make the archive to carry to a server. Run with:  python -m app.bundle
    Then on the server:  unzip digest-bot-*.zip -d digest-bot && cd digest-bot && sh install.sh
    """
    parser = argparse.ArgumentParser(description="Pack the bot for moving to a server")
    parser.add_argument("--code-only", action="store_true", help="without .env and the database")
    args = parser.parse_args()

    output = ROOT / f"digest-bot-{datetime.now():%Y%m%d-%H%M}.zip"
    build_bundle(output, with_data=not args.code_only)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(f"Готово: {output.name} ({output.stat().st_size / 1_000_000:.1f} МБ)")
    if not args.code_only:
        print("В архиве токены и вся переписка: передавайте его только по защищённому каналу")
        print("и удалите после установки.")


if __name__ == "__main__":
    main()
