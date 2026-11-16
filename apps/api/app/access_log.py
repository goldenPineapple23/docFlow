"""
No query string in the API's access log (D-240 item 9; D-241).

uvicorn's access line prints the request's full path, query string included.
The viewer's original, its page images and an export download carry their
signed token in the query string, so on 2026-11-15 a page token reached Fly's
log in a 21:13:17Z line. Every query string is dropped, not only `token`: the
next one to carry something private shouldn't depend on someone remembering
this list.

Installed on the `uvicorn.access` logger when `app.main` is imported, so it
covers `python -m app.serve` on Fly and `uvicorn app.main:app` locally and in
CI. uvicorn's `dictConfig` replaces that logger's handlers, never its filters.
"""

from __future__ import annotations

import logging

ACCESS_LOGGER = "uvicorn.access"


def path_without_query(path: str) -> str:
    return path.split("?", 1)[0]


class DropQueryStrings(logging.Filter):
    """
    uvicorn logs access as `'%s - "%s %s HTTP/%s" %d'` with the arguments
    (client, method, full path, HTTP version, status): the path is the third.
    The record is changed, never dropped, so every request is still logged.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], path_without_query(args[2]), *args[3:])
        return True


def install() -> None:
    logger = logging.getLogger(ACCESS_LOGGER)
    if not any(isinstance(f, DropQueryStrings) for f in logger.filters):
        logger.addFilter(DropQueryStrings())
