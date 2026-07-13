#!/usr/bin/env python3
"""Fail if obvious local secret files are tracked by Git.

This intentionally checks file names only. It does not print file contents.
"""
from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import PurePosixPath

BLOCKED_PATTERNS = (
    "*kubeconfig*",
    ".kube/*",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.crt",
    "*.cert",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "secrets/*",
    "*.secret",
)


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return [item for item in result.stdout.decode().split("\0") if item]


def is_blocked(path: str) -> bool:
    normalized = PurePosixPath(path).as_posix()
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in BLOCKED_PATTERNS)


def main() -> int:
    blocked = sorted(path for path in tracked_files() if is_blocked(path))
    if blocked:
        print("Tracked secret-like files are not allowed:", file=sys.stderr)
        for path in blocked:
            print(f"- {path}", file=sys.stderr)
        print("Remove these from Git and rotate any exposed credentials.", file=sys.stderr)
        return 1
    print("No tracked secret-like file names found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
