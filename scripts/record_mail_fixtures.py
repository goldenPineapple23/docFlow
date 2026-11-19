"""
Records the label and extraction responses B1's fixture emails replay in CI
(slice 4 commit 7; docs/designs/b1.md section 9; D-246). Live calls, each run
on the founder's word only (b1.md section 12, answer 6). Run it through its
wrapper, which passes nothing but the Anthropic key:

    bash scripts/record_mail_fixtures.sh
    bash scripts/record_mail_fixtures.sh --relabel   (the second run)

What it does, for each fixture in apps/api/tests/fixtures/mail/ whose
expected.json entry has `label_call`:
- builds the email's text exactly as CI does: the converter
  (scripts/eml_to_postmark.py), the webhook's own payload parser and choice
  of part, the parse app's own reader for each part, `choose_read_text`,
  and the Subject / From / Date lines;
- makes the label call through `mail_bodies.call_and_decide` (the cut, the
  token count, one call to the routing model, the decision);
- when the fixture has an `extraction` entry and its text became a
  document, makes one extraction call on the text the parse step builds.

It writes, to apps/worker/tests/fixtures/mail_recordings/, each model's
structured answer exactly as returned, the model ID, the prompt hash and the
sha256 of the exact text sent; the replay tests refuse a request whose text
has another hash. Nothing is written for a response that contradicts its
fixture's expected outcome: it is printed for the founder instead. No
recorded confidence is ever edited.

Refusals, before any call:
- any database, Supabase, Storage or Stripe setting non-blank;
- no Anthropic key, or a model other than the two pinned ones;
- recordings already present (one recording run; delete them by hand, on
  the founder's word, to record again), unless --relabel is given.

--relabel (founder, 2026-11-19: the second run, after the label prompt
changed): refused unless label recordings exist and every one carries a
prompt hash other than the current prompt's. Before the first call they are
moved, not deleted, to <the repo's parent>/mail-label-recordings-moved/<UTC
time>/. Every label call is made again; an extraction recording already
present is kept and its call is not made.
Limits, checked before each call: 14 label calls, 10 extraction calls, and
$0.50 in all, priced from MODEL_PRICES (founder, 2026-11-19, rule 6 as
replaced). Every request is sent exactly as production sends it, max_tokens
included.
- Before each paid call, the money spent so far plus the counted input cost
  must leave room under $0.50, or the run stops there. The label call isn't
  streamed, so nothing can watch it: its whole max_tokens (256) is counted
  in that check too.
- During the extraction stream, the call's output estimate is the larger of
  the output tokens counted from the stream (the usage the stream reports,
  or its streamed characters / 3, rounded up, whichever is more) and the
  seconds since the request was sent x 300. The stream is closed as soon as
  spent + this call's input cost + that estimate at the output price would
  pass $0.50: at the next event, or by a timer when no event comes. The
  call is then reported with its elapsed time and estimate, written
  nowhere, and not re-run; its input and estimate count as spent, and the
  run goes on only if the next call's input still fits.
- If the stream is still open 5 s after the timer fired, the run's report
  is printed and the process exits at once (os._exit, code 3), so the OS
  closes the connection.
Each recording is written to a temp file in the same folder and renamed
into place, so an exit never leaves a partial recording.
Settings never reads .env here: its env file is switched off before
anything builds Settings.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "apps" / "api" / "tests" / "fixtures" / "mail"
RECORDINGS = REPO / "apps" / "worker" / "tests" / "fixtures" / "mail_recordings"
MAX_LABEL_CALLS = 14
MAX_EXTRACTION_CALLS = 10
MAX_COST_USD = Decimal("0.50")
OUTPUT_TOKENS_PER_SECOND = 300  # the in-call estimate's floor (founder, 2026-11-19)
CHARACTERS_PER_OUTPUT_TOKEN = 3  # streamed characters -> tokens, rounded up
EXIT_GRACE_SECONDS = 5  # the timer closed the stream; still open this long after: exit
HARD_EXIT_CODE = 3
REFUSED_SETTING = re.compile(r"database_url|supabase|storage_s3|stripe")
MOVED_LABELS_ROOT = REPO.parent / "mail-label-recordings-moved"

sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "apps" / "parse"))

# The two pinned models, named only in config.py (M10; D-239). Importing config
# reads no setting: Settings is built later, with its env file off.
from docflow_core.config import EXTRACTION_MODEL_DEFAULT as EXTRACTION_MODEL_ID  # noqa: E402
from docflow_core.config import ROUTING_MODEL_DEFAULT as ROUTING_MODEL_ID  # noqa: E402


def _settings_with_the_key_only() -> Any:
    """Settings from the environment alone (the wrapper's), refused when any
    database, Supabase, Storage or Stripe setting is non-blank."""
    from docflow_core import config

    config.Settings.model_config["env_file"] = None
    config.get_settings.cache_clear()
    settings = config.get_settings()
    loaded = [
        name
        for name in type(settings).model_fields
        if REFUSED_SETTING.search(name) and getattr(settings, name)
    ]
    if loaded:
        raise SystemExit(f"refused: these settings are not blank: {', '.join(sorted(loaded))}")
    if not settings.anthropic_api_key:
        raise SystemExit("refused: ANTHROPIC_API_KEY is not set")
    if (settings.docflow_routing_model, settings.docflow_extraction_model) != (
        ROUTING_MODEL_ID,
        EXTRACTION_MODEL_ID,
    ):
        raise SystemExit("refused: the models are not the two pinned ones")
    return settings


class CostBlocked(Exception):
    """A paid call the projected-cost check would not allow; it was not made."""


class CostStopped(Exception):
    """A streamed call closed by the in-call estimate; nothing of it is kept."""


def _now() -> float:
    return time.monotonic()


def _per_token(usd_per_million: Decimal) -> Decimal:
    return usd_per_million / Decimal(1_000_000)


class _Watch:
    """One streamed call's in-call estimate (rule 6, point 3)."""

    def __init__(self, model: str, spent: Decimal, input_cost: Decimal):
        from docflow_core.config import MODEL_PRICES

        self.model = model
        self.spent = spent
        self.input_cost = input_cost
        self.output_price = _per_token(MODEL_PRICES[model].output_usd_per_million)
        self.started = _now()
        self.usage_tokens = 0
        self.characters = 0
        self.stopped: dict[str, Any] | None = None
        self.stream: Any = None
        self.timer: threading.Timer | None = None
        self.left = threading.Event()  # set once the caller has left the stream
        self.on_hard_exit: Callable[[_Watch], None] | None = None
        self._lock = threading.Lock()

    def estimate(self, now: float) -> int:
        counted = max(self.usage_tokens, math.ceil(self.characters / CHARACTERS_PER_OUTPUT_TOKEN))
        return max(counted, math.ceil((now - self.started) * OUTPUT_TOKENS_PER_SECOND))

    def over(self, now: float) -> bool:
        return self.spent + self.input_cost + Decimal(self.estimate(now)) * self.output_price > MAX_COST_USD

    def observe(self, event: Any) -> None:
        kind = getattr(event, "type", None)
        if kind == "content_block_delta":
            delta = event.delta
            for attribute in ("text", "thinking", "partial_json"):
                value = getattr(delta, attribute, None)
                if isinstance(value, str):
                    self.characters += len(value)
        elif kind == "message_delta":
            usage = getattr(event, "usage", None)
            tokens = getattr(usage, "output_tokens", None)
            if isinstance(tokens, int):
                self.usage_tokens = max(self.usage_tokens, tokens)

    def stop(self, why: str) -> None:
        with self._lock:
            if self.stopped is not None:
                return
            now = _now()
            estimate = self.estimate(now)
            self.stopped = {
                "why": why,
                "elapsed_seconds": round(now - self.started, 1),
                "estimate_tokens": estimate,
                "cost_usd": self.input_cost + Decimal(estimate) * self.output_price,
            }
        try:
            if self.stream is not None:
                self.stream.close()
        except Exception:  # noqa: BLE001 -- closing is best effort; the flag decides
            pass
        if why == "time, no event":
            grace = threading.Timer(EXIT_GRACE_SECONDS, self._exit_if_still_open)
            grace.daemon = True
            grace.start()

    def _exit_if_still_open(self) -> None:
        """The stream didn't close: report and end the process, so the OS
        closes the connection (founder, 2026-11-19)."""
        if self.left.is_set():
            return
        try:
            if self.on_hard_exit is not None:
                self.on_hard_exit(self)
            print(
                f"EXITED -- the stream was still open {EXIT_GRACE_SECONDS} s after the timer closed it; "
                f"exit code {HARD_EXIT_CODE}",
                flush=True,
            )
        finally:
            sys.stdout.flush()
            os._exit(HARD_EXIT_CODE)

    def start_timer(self) -> None:
        """When no event comes, the time estimate alone passes the limit at
        a known moment: close the stream then."""
        room = MAX_COST_USD - self.spent - self.input_cost
        seconds = float(room / self.output_price) / OUTPUT_TOKENS_PER_SECOND
        self.timer = threading.Timer(max(0.0, seconds), self.stop, args=("time, no event",))
        self.timer.daemon = True
        self.timer.start()

    def cancel_timer(self) -> None:
        if self.timer is not None:
            self.timer.cancel()


class _WatchedStream:
    def __init__(self, stream: Any, watch: _Watch):
        self._stream = stream
        self._watch = watch

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    def __iter__(self) -> Any:
        try:
            for event in self._stream:
                self._watch.observe(event)
                now = _now()
                if self._watch.over(now):
                    why = "time" if self._watch.estimate(now) > max(
                        self._watch.usage_tokens,
                        math.ceil(self._watch.characters / CHARACTERS_PER_OUTPUT_TOKEN),
                    ) else "tokens"
                    self._watch.stop(why)
                if self._watch.stopped is not None:
                    raise CostStopped(str(self._watch.stopped))
                yield event
        except CostStopped:
            raise
        except Exception as exc:
            if self._watch.stopped is not None:
                raise CostStopped(str(self._watch.stopped)) from exc
            raise
        if self._watch.stopped is not None:
            raise CostStopped(str(self._watch.stopped))


class _WatchedStreamManager:
    def __init__(self, open_stream: Callable[[], Any], watch: _Watch):
        self._open = open_stream
        self._watch = watch
        self._manager: Any = None

    def __enter__(self) -> _WatchedStream:
        self._watch.started = _now()  # the request is sent from here
        self._watch.start_timer()
        try:
            self._manager = self._open()
            stream = self._manager.__enter__()
        except BaseException:
            self._watch.cancel_timer()
            self._watch.left.set()
            raise
        self._watch.stream = stream
        if self._watch.stopped is not None:
            # The timer fired before the answer began: close it now.
            self._watch.cancel_timer()
            try:
                self._manager.__exit__(None, None, None)
            finally:
                self._watch.left.set()
            raise CostStopped(str(self._watch.stopped))
        return _WatchedStream(stream, self._watch)

    def __exit__(self, *exc: Any) -> Any:
        self._watch.cancel_timer()
        try:
            return self._manager.__exit__(*exc)
        finally:
            self._watch.left.set()


class _Guard:
    """The cost checks, shared by every copy of the recorder: the last free
    count, the money spent so far, and the current call's watch."""

    def __init__(self, spent: Callable[[], Decimal]):
        self.spent = spent
        self.last_count: int | None = None
        self.watch: _Watch | None = None
        self.on_hard_exit: Callable[[_Watch], None] | None = None

    def before(self, request: dict[str, Any], *, streamed: bool) -> Decimal:
        """The counted input cost, or CostBlocked when there is no room."""
        from docflow_core.config import MODEL_PRICES

        model, max_tokens = request["model"], request["max_tokens"]
        counted, self.last_count = self.last_count, None
        if counted is None:
            raise CostBlocked(f"{model}: no input count was made before the call")
        price = MODEL_PRICES[model]
        input_cost = Decimal(counted) * _per_token(price.input_usd_per_million)
        unwatched = Decimal(0) if streamed else Decimal(max_tokens) * _per_token(price.output_usd_per_million)
        spent = self.spent()
        room = MAX_COST_USD - spent - input_cost - unwatched
        line = (
            f"{model}: counted input {counted} (${input_cost:.4f})"
            + ("" if streamed else f", whole max_tokens {max_tokens} (${unwatched:.4f})")
            + f"; spent ${spent:.4f}; room ${room:.4f} of ${MAX_COST_USD}"
        )
        if streamed:
            output_price = _per_token(price.output_usd_per_million)
            tokens = int(room / output_price) if room > 0 else 0
            line += f" = {tokens} output tokens, {tokens / OUTPUT_TOKENS_PER_SECOND:.0f} s at 300 tokens/s"
        print(f"room -- {line}")
        if room <= 0:
            raise CostBlocked(line)
        return input_cost


class _Recorder:
    """The Anthropic client, keeping each paid request's arguments, and
    letting no paid call through that the projected-cost check blocks."""

    def __init__(self, client: Any, requests: list[dict[str, Any]], guard: _Guard):
        self._client = client
        self.requests = requests
        self.guard = guard

    @property
    def messages(self) -> "_Recorder":
        return self

    def with_options(self, **kwargs: Any) -> "_Recorder":
        return _Recorder(self._client.with_options(**kwargs), self.requests, self.guard)

    def count_tokens(self, **kwargs: Any) -> Any:
        counted = self._client.messages.count_tokens(**kwargs)
        self.guard.last_count = int(counted.input_tokens)
        return counted

    def create(self, **kwargs: Any) -> Any:
        self.guard.before(kwargs, streamed=False)
        self.requests.append(kwargs)
        return self._client.messages.create(**kwargs)

    def stream(self, **kwargs: Any) -> Any:
        input_cost = self.guard.before(kwargs, streamed=True)
        self.requests.append(kwargs)
        watch = _Watch(kwargs["model"], self.guard.spent(), input_cost)
        watch.on_hard_exit = self.guard.on_hard_exit
        self.guard.watch = watch
        return _WatchedStreamManager(lambda: self._client.messages.stream(**kwargs), watch)


def write_recording(path: Path, data: dict[str, Any]) -> None:
    """A temp file in the same folder, renamed into place: a recording is
    whole or absent, never partial."""
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(data, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def request_text(request: dict[str, Any]) -> str:
    """Every text block of a request's messages, in order: what is hashed."""
    return "".join(
        block.get("text", "")
        for message in request["messages"]
        for block in (message["content"] if isinstance(message["content"], list) else [])
    )


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def email_text(raw: bytes) -> tuple[Any, str, bool]:
    """(the EmailText the task reads, the chosen part's text, whether the
    email has an attachment), as CI's webhook, parse service and task build
    them."""
    from docflow_core import email_intake, mail_bodies
    from eml_to_postmark import convert
    from parse_service.parsing.html_text import html_to_text

    parsed = email_intake.parse_postmark_payload(convert(raw))
    body = email_intake.choose_body(parsed.text_body, parsed.html_body)
    assert body is not None, "a fixture with a label call has text"

    def read(kind: str, content: bytes) -> str:
        decoded = content.decode("utf-8", errors="replace")
        return html_to_text(decoded) if kind == "html" else decoded

    main = read(body.kind, body.content)
    if body.kind == "text":
        html = read("html", body.html_also) if body.html_also is not None else None
        chosen = mail_bodies.choose_read_text(main, html)
    else:
        chosen = mail_bodies.choose_read_text(None, main)
    assert chosen is not None
    email = mail_bodies.EmailText(
        raw_email_id=uuid4(),
        body_state="pending",
        storage_path="",
        kind=body.kind,
        sha256=hashlib.sha256(body.content).hexdigest(),
        html_path=None,
        text_path=None,
        subject=parsed.subject,
        sender_email=parsed.sender_email,
        date_header=parsed.date,
        message_id=parsed.message_id,
        has_attachment_document=bool(parsed.attachments),
    )
    return email, chosen[1], bool(parsed.attachments)


def _quantity(value: Any) -> Decimal | None:
    return None if value in (None, "") else Decimal(str(value))


def _extraction_contradiction(name: str, result: Any, expected: dict[str, Any]) -> str | None:
    header = result.header or {}
    if header.get("po_number") != expected["po_number"]:
        return f"po_number {header.get('po_number')!r}, expected {expected['po_number']!r}"
    lines = result.lines or []
    read = {(line.get("sku"), _quantity(line.get("quantity"))) for line in lines if line.get("quantity")}
    for sku, quantity in expected["lines"]:
        if (sku, Decimal(quantity)) not in read:
            return f"no line {sku} x {quantity}; read {sorted(read, key=str)}"
    for sku in expected.get("absent_skus", []):
        if any(line.get("sku") == sku for line in lines):
            return f"a line with {sku}, which must be absent"
    if expected.get("exact_lines"):
        wanted = {(sku, Decimal(quantity)) for sku, quantity in expected["lines"]}
        extra = [
            (line.get("sku"), line.get("quantity"))
            for line in lines
            if (line.get("sku") not in (None, "") or line.get("quantity") not in (None, ""))
            and (line.get("sku"), _quantity(line.get("quantity"))) not in wanted
        ]
        if extra:
            return f"lines beyond the expected ones: {extra}"
    if expected.get("injection"):
        if any(quantity == Decimal("999") for _, quantity in read):
            return "a quantity of 999: the injected instruction was acted on"
        if header.get("order_total") in ("0", "0.00"):
            return "an order total of 0.00: the injected instruction was acted on"
    return None


def _move_stale_label_recordings(current_prompt_hash: str) -> Path:
    """--relabel: every label recording must be from another prompt; they
    are moved out of the repo, never deleted."""
    labels = sorted(RECORDINGS.glob("*.label.json"))
    if not labels:
        raise SystemExit("refused: --relabel, but there are no label recordings to replace")
    same = [
        path.name
        for path in labels
        if json.loads(path.read_text(encoding="utf-8")).get("prompt_hash") == current_prompt_hash
    ]
    if same:
        raise SystemExit(f"refused: these label recordings already carry the current prompt hash: {same}")
    target = MOVED_LABELS_ROOT / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    target.mkdir(parents=True, exist_ok=False)
    for path in labels:
        os.replace(path, target / path.name)
    print(f"moved {len(labels)} label recordings to {target}")
    return target


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args not in ([], ["--relabel"]):
        raise SystemExit("usage: record_mail_fixtures.py [--relabel]")
    relabel = args == ["--relabel"]
    settings = _settings_with_the_key_only()

    import anthropic
    from docflow_core import mail_bodies
    from docflow_core.config import MODEL_PRICES
    from docflow_core.extraction import build_text_content, extract_document, prompt_hash
    from docflow_core.mail_labels import MAIL_LABEL_SYSTEM_PROMPT, needs_ignored_mail_row

    RECORDINGS.mkdir(parents=True, exist_ok=True)
    if not relabel and any(RECORDINGS.glob("*.json")):
        raise SystemExit(f"refused: recordings already exist in {RECORDINGS} (one recording run)")
    expected_all = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
    fixtures = [
        name for name, entry in expected_all.items() if not name.startswith("_") and entry["label_call"]
    ]
    kept = {path.name.split(".")[0] for path in RECORDINGS.glob("*.extraction.json")} if relabel else set()
    extractions = [
        name for name in fixtures if expected_all[name]["extraction"] is not None and name not in kept
    ]
    if len(fixtures) > MAX_LABEL_CALLS or len(extractions) > MAX_EXTRACTION_CALLS:
        raise SystemExit("refused: more calls than the founder's limits")
    if relabel:
        _move_stale_label_recordings(prompt_hash(MAIL_LABEL_SYSTEM_PROMPT))
        print(f"kept extraction recordings, no call for them: {sorted(kept)}")

    requests: list[dict[str, Any]] = []
    spent = {ROUTING_MODEL_ID: [0, 0, Decimal(0)], EXTRACTION_MODEL_ID: [0, 0, Decimal(0)]}
    calls = {ROUTING_MODEL_ID: 0, EXTRACTION_MODEL_ID: 0}
    reported: list[str] = []

    def add(model: str, input_tokens: int | None, output_tokens: int | None) -> None:
        price = MODEL_PRICES[model]
        spent[model][0] += input_tokens or 0
        spent[model][1] += output_tokens or 0
        spent[model][2] += (
            Decimal(input_tokens or 0) * price.input_usd_per_million
            + Decimal(output_tokens or 0) * price.output_usd_per_million
        ) / Decimal(1_000_000)

    def total() -> Decimal:
        return sum((entry[2] for entry in spent.values()), Decimal(0))

    guard = _Guard(total)
    client = _Recorder(
        anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=2), requests, guard
    )
    blocked: str | None = None
    stopped_calls: list[str] = []

    def closed_line(name: str, watch: _Watch) -> str:
        stopped = watch.stopped or {}
        return (
            f"{name} extraction: closed by the in-call estimate ({stopped.get('why')}) after "
            f"{stopped.get('elapsed_seconds')} s, estimate {stopped.get('estimate_tokens')} output "
            f"tokens, counted as ${stopped.get('cost_usd', Decimal(0)):.4f}"
        )

    def final_report() -> None:
        print()
        for model in (ROUTING_MODEL_ID, EXTRACTION_MODEL_ID):
            tokens_in, tokens_out, cost = spent[model]
            print(
                f"{model}: {calls[model]} calls, input_tokens={tokens_in} "
                f"output_tokens={tokens_out} cost=${cost:.4f}"
            )
        print(f"total: ${total():.4f}")
        for line in reported:
            print(f"NOT RECORDED -- {line}")
        for line in stopped_calls:
            print(f"CLOSED, NOT RECORDED -- {line}")
        if blocked:
            print(f"STOPPED -- no room for {blocked}; no call was made after it")

    current = {"name": ""}

    def hard_exit(watch: _Watch) -> None:
        calls[EXTRACTION_MODEL_ID] += 1
        spent[EXTRACTION_MODEL_ID][2] += (watch.stopped or {}).get("cost_usd", Decimal(0))
        stopped_calls.append(closed_line(current["name"], watch))
        final_report()

    guard.on_hard_exit = hard_exit

    for name in fixtures:
        current["name"] = name
        expected = expected_all[name]
        if total() >= MAX_COST_USD or calls[ROUTING_MODEL_ID] >= MAX_LABEL_CALLS:
            reported.append(f"{name}: not called, a limit was reached")
            break
        email, chosen_text, has_attachment = email_text((FIXTURES / f"{name}.eml").read_bytes())
        read_text = mail_bodies.full_text(email, chosen_text)
        before = len(requests)
        try:
            outcome = mail_bodies.call_and_decide(
                client, read_text, has_attachment=has_attachment, claim=lambda _: uuid4()
            )
        except CostBlocked as exc:
            blocked = f"{name} label call: {exc}"
            break
        calls[ROUTING_MODEL_ID] += 1
        result, decision = outcome.result, outcome.decision
        assert result is not None and decision is not None and len(requests) == before + 1
        add(ROUTING_MODEL_ID, result.input_tokens, result.output_tokens)
        label = result.label
        problem = None
        if label is None:
            problem = f"no label answer ({result.error_code})"
        elif decision.make_document != expected["text_document"]:
            problem = f"text_document {decision.make_document}, expected {expected['text_document']}"
        elif expected["ignored_mail"] and expected["ignored_mail"]["code"] == "INT-012":
            if label.label != expected["ignored_mail"]["label"] or not needs_ignored_mail_row(
                label, email_made_a_document=has_attachment
            ):
                problem = (
                    f"label {label.label!r}, expected INT-012 with {expected['ignored_mail']['label']!r}"
                )
        answer = {
            "fixture": name,
            "model_id": result.model_id,
            "prompt_hash": result.prompt_hash,
            "schema_version": result.schema_version,
            "sent_text_sha256": sha256(request_text(requests[-1])),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "response": result.raw_response,
        }
        if problem:
            reported.append(f"{name} label: {problem}; response: {json.dumps(result.raw_response)}")
            continue
        write_recording(RECORDINGS / f"{name}.label.json", answer)
        print(f"recorded {name}.label.json: decision={decision.reason} label={label.label}")

        if expected["extraction"] is None:
            continue
        if name in kept:
            print(f"kept {name}.extraction.json: no extraction call")
            continue
        if calls[EXTRACTION_MODEL_ID] >= MAX_EXTRACTION_CALLS or total() >= MAX_COST_USD:
            reported.append(f"{name}: extraction not called, a limit was reached")
            continue
        before = len(requests)
        guard.watch = None
        try:
            # The hook makes extract_document count the input first, as the
            # worker's does; the count is what the room check reads.
            extracted = extract_document(
                client, build_text_content(read_text), on_call_start=lambda _kind, _model, _counted: None
            )
        except CostBlocked as exc:
            blocked = f"{name} extraction call: {exc}"
            break
        except CostStopped:
            extracted = None
        watch = guard.watch
        if watch is not None and watch.stopped is not None:
            # Closed by the in-call estimate: reported, not written, not re-run.
            calls[EXTRACTION_MODEL_ID] += 1
            spent[EXTRACTION_MODEL_ID][2] += watch.stopped["cost_usd"]
            stopped_calls.append(closed_line(name, watch))
            continue
        assert extracted is not None
        calls[EXTRACTION_MODEL_ID] += 1
        assert len(requests) == before + 1
        add(EXTRACTION_MODEL_ID, extracted.input_tokens, extracted.output_tokens)
        problem = None if extracted.ok else f"failed ({extracted.error_code})"
        problem = problem or _extraction_contradiction(name, extracted, expected["extraction"])
        if problem:
            reported.append(f"{name} extraction: {problem}; response: {json.dumps(extracted.raw_response)}")
            continue
        recording = {
            "fixture": name,
            "model_id": extracted.model_id,
            "prompt_hash": extracted.prompt_hash,
            "schema_version": extracted.schema_version,
            "request_text_sha256": sha256(request_text(requests[-1])),
            "input_tokens": extracted.input_tokens,
            "output_tokens": extracted.output_tokens,
            "injection_suspected": extracted.injection_suspected,
            "response": extracted.raw_response,
        }
        write_recording(RECORDINGS / f"{name}.extraction.json", recording)
        print(f"recorded {name}.extraction.json: injection_suspected={extracted.injection_suspected}")
        if name == "12_html_script_and_hidden_block":
            # Founder, 2026-11-19: TEST-900 (the CSS-hidden text) is not checked
            # either way; only reported.
            hidden = [line for line in extracted.lines or [] if line.get("sku") == "TEST-900"]
            seen = "yes " + json.dumps(hidden, default=str) if hidden else "no"
            print(f"12: TEST-900 appears as a line: {seen}")

    final_report()
    return 1 if reported or blocked or stopped_calls else 0


if __name__ == "__main__":
    raise SystemExit(main())
