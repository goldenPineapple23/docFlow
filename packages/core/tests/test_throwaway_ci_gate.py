"""THROWAWAY -- never merge. Proves a red CI check blocks merging into main.

The pull request carrying this file is closed and its branch deleted once
GitHub has refused the merge.
"""


def test_deliberately_fails_to_prove_the_merge_gate() -> None:
    assert False, "deliberate failure: this PR must not be mergeable"
