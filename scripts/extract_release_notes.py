"""Extract one version's section from CHANGELOG.md as Velopack notes."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def extract_release_notes(changelog: str, version: str) -> str:
    """Return the ``## <version>`` section verbatim, without surrounding gaps.

    Heading 匹配与 ``automation_prepare._update_changelog`` 保持同一约定
    （可选括号、行尾任意后缀），保证 release prepare 写入的段落能被再次
    取出注入 Velopack feed。
    """

    heading = re.compile(rf"(?m)^## (?:\[)?{re.escape(version)}(?:\])?.*$")
    match = heading.search(changelog)
    if match is None:
        raise ValueError(f"CHANGELOG.md has no section for version {version}")
    following = re.search(r"(?m)^## ", changelog[match.end() :])
    end = match.end() + following.start() if following else len(changelog)
    section = changelog[match.start() : end].strip()
    if not section:
        raise ValueError(f"CHANGELOG.md section for version {version} is empty")
    return section


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--changelog", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        changelog = args.changelog.read_text(encoding="utf-8")
        section = extract_release_notes(changelog, args.version)
    except (OSError, ValueError) as error:
        parser.exit(1, f"release notes extraction failed: {error}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(section + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
