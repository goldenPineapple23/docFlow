"""
Point DocFlow's Storage settings at the local Supabase stack's S3 endpoint,
for CI (Stage 3b item 10: the suites run against a real bucket -- this
runner's own).

Reads `supabase status -o env` and appends the four STORAGE_S3_* settings to
$GITHUB_ENV. Fails loudly, naming what is missing, if the CLI ever renames
its output, rather than letting the suites fail later as an unexplained
StorageUnavailableError. Also refuses any endpoint that is not on this
runner: CI must never write to a real project's bucket.
"""

from __future__ import annotations

import os
import subprocess
import sys

# Each setting, and the names the Supabase CLI has used for it (first found wins).
WANTED = {
    "STORAGE_S3_ENDPOINT": ("STORAGE_S3_URL", "S3_PROTOCOL_URL"),
    "STORAGE_S3_ACCESS_KEY_ID": ("S3_PROTOCOL_ACCESS_KEY_ID",),
    "STORAGE_S3_SECRET_ACCESS_KEY": ("S3_PROTOCOL_ACCESS_KEY_SECRET",),
    "STORAGE_S3_REGION": ("S3_PROTOCOL_REGION",),
}


def main() -> int:
    out = subprocess.run(
        ["supabase", "status", "-o", "env"], check=True, capture_output=True, text=True
    ).stdout
    status: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line:
            name, _, value = line.partition("=")
            status[name.strip()] = value.strip().strip('"')

    resolved: dict[str, str] = {}
    missing: list[str] = []
    for setting, names in WANTED.items():
        value = next((status[n] for n in names if status.get(n)), "")
        if not value:
            missing.append(f"{setting} (looked for {', '.join(names)})")
        resolved[setting] = value
    if missing:
        print("supabase status -o env did not provide: " + "; ".join(missing), file=sys.stderr)
        print("It printed: " + ", ".join(sorted(status)), file=sys.stderr)
        return 1

    endpoint = resolved["STORAGE_S3_ENDPOINT"]
    if not endpoint.startswith(("http://127.0.0.1", "http://localhost")):
        print(f"Refusing: the S3 endpoint {endpoint!r} is not on this runner.", file=sys.stderr)
        return 1

    anon = status.get("ANON_KEY", "")
    if not anon:
        print("supabase status -o env did not provide ANON_KEY", file=sys.stderr)
        return 1

    with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as env:
        for setting, value in resolved.items():
            env.write(f"{setting}={value}\n")
        # For test_storage_bucket_live.py only: proves the anon key reaches no
        # file. A test-only name, so no product setting changes in these jobs.
        env.write(f"DOCFLOW_TEST_ANON_KEY={anon}\n")
    print("Storage -> this runner's local stack:", endpoint)
    return 0


if __name__ == "__main__":
    sys.exit(main())
