"""
Fail CI if CLAUDE.md is over its size cap.

CLAUDE.md is loaded on every turn, so every character in it is paid for on
every turn. The founder capped it at 28k tokens (CLAUDE.md, "CLAUDE.md size";
DECISIONS.md D-215). Tokens can't be counted here without the model's
tokenizer, so the cap is enforced as characters: the file's measured ratio
when the rule was added (70,053 characters at 24.1k tokens) scaled to 28k,
rounded down.

Characters, not bytes: the file is read as UTF-8 and CRLF is turned into LF
before counting, so a Windows checkout (core.autocrlf) and CI's LF checkout
give the same number.

Usage:
  python scripts/ci/check_claude_md_size.py [<path to CLAUDE.md>]

With no argument it checks CLAUDE.md at the repository root.
"""

import sys
from pathlib import Path

LIMIT_CHARS = 81389  # 70,053 x 28 / 24.1, rounded down (D-215)
CAP_K_TOKENS = 28

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "CLAUDE.md"


def count_chars(path: Path) -> int:
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    return len(text.replace("\r\n", "\n"))


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    count = count_chars(path)
    approx_k_tokens = count / LIMIT_CHARS * CAP_K_TOKENS
    print(f"CLAUDE.md: {count} / {LIMIT_CHARS} characters (~{approx_k_tokens:.1f} k tokens)")
    if count <= LIMIT_CHARS:
        return 0
    print(
        f"::error::CLAUDE.md is {count - LIMIT_CHARS} characters over its cap of "
        f"{LIMIT_CHARS} (~{CAP_K_TOKENS}k tokens). See the founder rule \"CLAUDE.md size\" "
        "in CLAUDE.md (DECISIONS.md D-215): new decisions go in DECISIONS.md, and an "
        "addition that would pass the cap waits for the founder to decide what moves out. "
        "Sections 0, 3, 7 and 10 are never trimmed, reworded or moved to make room."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
