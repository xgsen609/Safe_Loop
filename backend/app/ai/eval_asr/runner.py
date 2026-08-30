"""Score the manually invoked speech corpus with WER and Mandarin CER."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Final, Literal, Sequence, TypeVar
import unicodedata

from pydantic import BaseModel, Field

from app.ai.transcription import TranscriptionFailure, TranscriptionProvider

_ROOT: Final = Path(__file__).parent
_FIXTURES_PATH: Final = _ROOT / "fixtures.json"
_WORD = re.compile(r"[\w'-]+", re.UNICODE)
_Token = TypeVar("_Token")


class ASRFixture(BaseModel):
    id: str = Field(min_length=1)
    file: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    hint_locale: str = Field(min_length=2)
    reference: str = Field(min_length=1)
    category: Literal[
        "mandarin",
        "english",
        "code-switched",
        "noisy-mandarin",
        "noisy-english",
    ]

    @property
    def path(self) -> Path:
        path = (_ROOT / self.file).resolve()
        if _ROOT.resolve() not in path.parents:
            raise ValueError("ASR fixture path escapes the corpus")
        return path


@dataclass(frozen=True)
class ASRCaseResult:
    fixture_id: str
    category: str
    reference: str
    hypothesis: str
    detected_locale: str
    character_errors: int
    reference_characters: int
    word_errors: int
    reference_words: int
    failure: str | None = None

    @property
    def cer(self) -> float:
        return (
            self.character_errors / self.reference_characters
            if self.reference_characters
            else 0.0
        )

    @property
    def wer(self) -> float:
        return (
            self.word_errors / self.reference_words
            if self.reference_words
            else 0.0
        )


@dataclass(frozen=True)
class ASRReport:
    cases: tuple[ASRCaseResult, ...]

    @property
    def mandarin_cer(self) -> float:
        cases = tuple(
            case
            for case in self.cases
            if case.category in {"mandarin", "noisy-mandarin"}
        )
        errors = sum(case.character_errors for case in cases)
        characters = sum(case.reference_characters for case in cases)
        return errors / characters if characters else 0.0

    @property
    def english_wer(self) -> float:
        cases = tuple(
            case
            for case in self.cases
            if case.category in {"english", "noisy-english"}
        )
        errors = sum(case.word_errors for case in cases)
        words = sum(case.reference_words for case in cases)
        return errors / words if words else 0.0


def _normalised_characters(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return [
        character
        for character in normalized
        if not character.isspace()
        and not unicodedata.category(character).startswith(("P", "S"))
    ]


def _normalised_words(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return _WORD.findall(normalized)


def edit_distance(reference: Sequence[_Token], hypothesis: Sequence[_Token]) -> int:
    """Return Levenshtein distance without adding an eval-only dependency."""
    previous = list(range(len(hypothesis) + 1))
    for reference_index, reference_item in enumerate(reference, start=1):
        current = [reference_index]
        for hypothesis_index, hypothesis_item in enumerate(hypothesis, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[hypothesis_index] + 1,
                    previous[hypothesis_index - 1]
                    + (reference_item != hypothesis_item),
                )
            )
        previous = current
    return previous[-1]


def load_fixtures(path: Path = _FIXTURES_PATH) -> tuple[ASRFixture, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("ASR fixtures must be a list")
    fixtures = tuple(ASRFixture.model_validate(item) for item in raw)
    if len(fixtures) != 10:
        raise ValueError("the ASR evaluation must contain exactly 10 fixtures")
    if len({fixture.id for fixture in fixtures}) != len(fixtures):
        raise ValueError("ASR fixture IDs must be unique")
    required_categories = {
        "mandarin",
        "english",
        "code-switched",
        "noisy-mandarin",
        "noisy-english",
    }
    if {fixture.category for fixture in fixtures} != required_categories:
        raise ValueError("ASR fixtures do not cover every required category")
    for fixture in fixtures:
        if not fixture.path.is_file():
            raise ValueError(f"missing ASR audio fixture: {fixture.file}")
    return fixtures


async def evaluate(
    provider: TranscriptionProvider,
    fixtures: tuple[ASRFixture, ...] | None = None,
) -> ASRReport:
    results: list[ASRCaseResult] = []
    for fixture in fixtures or load_fixtures():
        result = await provider.transcribe(
            fixture.path.read_bytes(),
            fixture.mime_type,
            fixture.hint_locale,
        )
        reference_characters = _normalised_characters(fixture.reference)
        reference_words = _normalised_words(fixture.reference)
        if isinstance(result, TranscriptionFailure):
            results.append(
                ASRCaseResult(
                    fixture_id=fixture.id,
                    category=fixture.category,
                    reference=fixture.reference,
                    hypothesis="",
                    detected_locale="unavailable",
                    character_errors=len(reference_characters),
                    reference_characters=len(reference_characters),
                    word_errors=len(reference_words),
                    reference_words=len(reference_words),
                    failure=result.code,
                )
            )
            continue
        transcript = result
        hypothesis_characters = _normalised_characters(transcript.text)
        hypothesis_words = _normalised_words(transcript.text)
        results.append(
            ASRCaseResult(
                fixture_id=fixture.id,
                category=fixture.category,
                reference=fixture.reference,
                hypothesis=transcript.text,
                detected_locale=transcript.detected_locale,
                character_errors=edit_distance(
                    reference_characters, hypothesis_characters
                ),
                reference_characters=len(reference_characters),
                word_errors=edit_distance(reference_words, hypothesis_words),
                reference_words=len(reference_words),
            )
        )
    return ASRReport(cases=tuple(results))


def render_report(report: ASRReport) -> str:
    lines = [
        "SafeLoop ASR evaluation (manual, never CI)",
        f"Mandarin CER: {report.mandarin_cer:.2%}",
        f"English WER: {report.english_wer:.2%}",
    ]
    for case in report.cases:
        detail = (
            f"failure={case.failure}"
            if case.failure
            else (
                f"CER={case.cer:.2%} WER={case.wer:.2%} "
                f"detected={case.detected_locale}"
            )
        )
        lines.append(f"{case.fixture_id}: {detail}")
        if not case.failure:
            lines.append(f"  ref: {case.reference}")
            lines.append(f"  hyp: {case.hypothesis}")
    return "\n".join(lines)
