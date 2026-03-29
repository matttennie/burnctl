#!/usr/bin/env python3
"""Generate an aider-style repo-map for this repository.

Uses tree-sitter AST parsing + PageRank to produce a token-efficient
structural summary of the codebase. Output is saved to .repo-map at
the repo root.

Requirements: pip install aider-chat

Usage: python scripts/generate-repo-map.py [--tokens 8192]
"""

import argparse
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Generate repo-map")
    parser.add_argument("--tokens", type=int, default=8192,
                        help="Token budget for the map (default: 8192)")
    parser.add_argument("--root", type=str, default=None,
                        help="Repository root (default: git root or cwd)")
    args = parser.parse_args()

    try:
        from aider.repomap import RepoMap
        from aider.io import InputOutput
        from aider.models import Model
    except ImportError:
        print("Error: aider-chat not installed. Run: pip install aider-chat", file=sys.stderr)
        sys.exit(1)

    # Determine repo root
    root = args.root
    if not root:
        root = os.popen("git rev-parse --show-toplevel 2>/dev/null").read().strip()
    if not root:
        root = os.getcwd()

    # Collect source files (skip node_modules, dist, .git)
    skip_dirs = {"node_modules", "dist", ".git", ".next", "build", "coverage", "__pycache__"}
    source_exts = {
        ".js", ".jsx", ".ts", ".tsx", ".cjs", ".mjs",
        ".py", ".rs", ".go", ".java", ".rb", ".swift",
        ".c", ".cpp", ".h", ".hpp", ".cs",
    }

    all_files = []
    for path in Path(root).rglob("*"):
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.suffix in source_exts and path.is_file():
            all_files.append(str(path))

    if not all_files:
        print(f"No source files found in {root}", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning {len(all_files)} source files in {root}")

    io = InputOutput(yes=True)
    model = Model("gpt-4o")  # Used only for token counting, no API calls
    rm = RepoMap(map_tokens=args.tokens, root=root, main_model=model, io=io, verbose=False)

    repo_map = rm.get_repo_map(chat_files=[], other_files=all_files)

    if not repo_map:
        print("Failed to generate repo-map", file=sys.stderr)
        sys.exit(1)

    output_path = os.path.join(root, ".repo-map")
    with open(output_path, "w") as f:
        f.write(repo_map)

    est_tokens = model.token_count(repo_map)
    line_count = repo_map.count("\n")
    print(f"Saved {output_path} ({est_tokens} tokens, {line_count} lines)")


if __name__ == "__main__":
    main()
