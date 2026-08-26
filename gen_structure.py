import os
from pathlib import Path

# Folders and files to exclude from the structure
EXCLUDE_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "venv",
    "env",
    "node_modules",
    ".idea",
    ".vscode",
    "build",
    "dist",
    ".mypy_cache",
    ".ruff_cache"
}
EXCLUDE_FILES = {".DS_Store", "project_structure.txt"}


def generate_tree(dir_path: Path, prefix: str = "") -> str:
    """Recursively generates a visual tree structure of the directory."""
    lines = []

    # Get sorted list of valid items in directory
    try:
        entries = sorted(
            [
                e
                for e in dir_path.iterdir()
                if e.name not in EXCLUDE_DIRS and e.name not in EXCLUDE_FILES
            ],
            key=lambda e: (not e.is_dir(), e.name.lower()),
        )
    except PermissionError:
        return ""

    total_entries = len(entries)

    for i, entry in enumerate(entries):
        is_last = i == total_entries - 1
        connector = "└── " if is_last else "├── "

        lines.append(f"{prefix}{connector}{entry.name}")

        if entry.is_dir():
            extension = "    " if is_last else "│   "
            lines.append(generate_tree(entry, prefix=prefix + extension))

    return "\n".join(filter(None, lines))


def save_project_structure(
    root_dir: str = ".", output_file: str = "project_structure.txt"
):
    root_path = Path(root_dir).resolve()
    tree_content = f"{root_path.name}/\n" + generate_tree(root_path)

    output_path = root_path / output_file
    output_path.write_text(tree_content, encoding="utf-8")

    print(f"Project structure successfully saved to: {output_path}")


if __name__ == "__main__":
    # Runs on the current working directory by default
    save_project_structure()