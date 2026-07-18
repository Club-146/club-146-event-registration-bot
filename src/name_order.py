"""Detect and gently fix Russian name/surname order mixups.

People often type Western order (Имя Фамилия) where we expect Russian order
(Фамилия Имя), or swap the two donate-form fields. Wrong order breaks
person matching via name_key.

Policy:
  - high confidence → auto-correct (keep the data usable)
  - medium confidence → flag for soft UI confirm, do not force
  - low / ambiguous → leave as entered

Matching should always try both orders (see name_keys_both_orders).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

# Common Russian given names (lowercase). Precision over recall: unknown tokens
# stay neutral so we do not "fix" foreign or rare names.
_GIVEN_NAMES = frozenset(
    {
        "александр",
        "александра",
        "алексей",
        "алина",
        "алиса",
        "алла",
        "анастасия",
        "анатолий",
        "анжела",
        "анжелика",
        "андрей",
        "анна",
        "антон",
        "арина",
        "аркадий",
        "арсений",
        "артём",
        "артем",
        "артур",
        "богдан",
        "борис",
        "вадим",
        "валентин",
        "валентина",
        "валерий",
        "валерия",
        "варвара",
        "василий",
        "василиса",
        "вера",
        "вероника",
        "виктор",
        "виктория",
        "виталий",
        "владимир",
        "владислав",
        "владислава",
        "всеволод",
        "вячеслав",
        "галина",
        "геннадий",
        "георгий",
        "глеб",
        "григорий",
        "даниил",
        "данил",
        "данила",
        "дарья",
        "демид",
        "денис",
        "диана",
        "дмитрий",
        "евгений",
        "евгения",
        "егор",
        "екатерина",
        "елена",
        "елизавета",
        "ефим",
        "жанна",
        "захар",
        "зинаида",
        "зоя",
        "иван",
        "игорь",
        "илья",
        "инна",
        "ирина",
        "карина",
        "кирилл",
        "клавдия",
        "константин",
        "ксения",
        "лариса",
        "лев",
        "леонид",
        "лидия",
        "лилия",
        "любовь",
        "людмила",
        "максим",
        "маргарита",
        "марина",
        "мария",
        "матвей",
        "милана",
        "михаил",
        "надежда",
        "наталья",
        "наталия",
        "никита",
        "николай",
        "нина",
        "оксана",
        "олег",
        "ольга",
        "павел",
        "пётр",
        "петр",
        "платон",
        "полина",
        "раиса",
        "регина",
        "роман",
        "руслан",
        "светлана",
        "святослав",
        "семен",
        "семён",
        "сергей",
        "софия",
        "софья",
        "станислав",
        "степан",
        "тамара",
        "татьяна",
        "тимофей",
        "тимур",
        "ульяна",
        "фаина",
        "фёдор",
        "федор",
        "филипп",
        "эдуард",
        "эльвира",
        "эмилия",
        "юлия",
        "юрий",
        "яков",
        "яна",
        "ярослав",
        "ярослава",
        # clear diminutives / short forms
        "саша",
        "саня",
        "шура",
        "лёша",
        "леша",
        "лёня",
        "леня",
        "дима",
        "миша",
        "коля",
        "костя",
        "вася",
        "петя",
        "вова",
        "женя",
        "света",
        "катя",
        "настя",
        "оля",
        "таня",
        "юля",
        "даша",
        "маша",
        "паша",
        "гоша",
        "гриша",
        "толя",
        "витя",
        "слава",
        "ксюша",
        "ира",
    }
)
# Lookup with ё→е so "Пётр" and "петр" both hit.
_GIVEN_NAMES_NORM = frozenset(n.replace("ё", "е") for n in _GIVEN_NAMES)

_SURNAME_SUFFIXES: Tuple[str, ...] = (
    "овский",
    "евский",
    "инский",
    "ынский",
    "овская",
    "евская",
    "инская",
    "ынская",
    "цкий",
    "цкая",
    "ский",
    "ская",
    "енко",
    "ченко",
    "ёнок",
    "енок",
    "ова",
    "ева",
    "ёва",
    "ина",
    "ына",
    "ов",
    "ев",
    "ёв",
    "ин",
    "ын",
    "ук",
    "юк",
    "як",
    "ян",
    "дзе",
    "швили",
)

_PATRONYMIC_SUFFIXES: Tuple[str, ...] = (
    "ович",
    "евич",
    "овна",
    "евна",
    "ична",
    "инична",
)

_MARGIN_HIGH = 1.2
_MARGIN_MEDIUM = 0.7


def norm_token(s: str) -> str:
    return (s or "").strip().lower().replace("ё", "е")


def name_key(surname: str, first_name: str) -> str:
    """Canonical person key: normalized 'фамилия имя'."""
    return " ".join(f"{surname} {first_name}".lower().replace("ё", "е").split())


def name_keys_both_orders(surname: str, first_name: str) -> List[str]:
    """Keys for entered order and swapped order (deduped, non-empty only)."""
    keys: List[str] = []
    for k in (name_key(surname, first_name), name_key(first_name, surname)):
        if k and k not in keys:
            keys.append(k)
    return keys


def _ends_with_any(token: str, suffixes: Sequence[str]) -> bool:
    return any(token.endswith(sfx) for sfx in suffixes)


def is_patronymic(token: str) -> bool:
    t = norm_token(token)
    if len(t) < 5:
        return False
    return _ends_with_any(t, _PATRONYMIC_SUFFIXES)


def given_name_score(token: str) -> float:
    """0..1+ — higher means more like a given name."""
    t = norm_token(token)
    if not t or len(t) < 2:
        return 0.0
    if is_patronymic(t):
        return 0.05
    score = 0.0
    if t in _GIVEN_NAMES_NORM:
        score += 1.0
    # Short tokens are more often given-name diminutives than surnames.
    if 2 <= len(t) <= 4 and t.isalpha():
        score += 0.15
    return score


def surname_score(token: str) -> float:
    """0..1+ — higher means more like a surname."""
    t = norm_token(token)
    if not t or len(t) < 2:
        return 0.0
    if is_patronymic(t):
        return 0.1
    if t in _GIVEN_NAMES_NORM:
        base = 0.15  # rare overlap (Роман, Павел as surnames)
    else:
        base = 0.0
    for sfx in _SURNAME_SUFFIXES:
        if t.endswith(sfx) and len(t) > len(sfx) + 1:
            base = max(base, 0.55 + min(0.45, len(sfx) * 0.05))
            break
    if len(t) >= 6 and base < 0.3:
        base += 0.05
    return base


@dataclass(frozen=True)
class FieldOrderGuess:
    first_name: str
    surname: str
    swapped: bool
    confidence: str  # "high" | "medium" | "low"
    reason: str = ""


def guess_field_order(first_name: str, surname: str) -> FieldOrderGuess:
    """Guess correct (Имя, Фамилия) for donate-style separate fields.

    Only optionally swaps the two values — never invents tokens.
    """
    fn = (first_name or "").strip()
    sn = (surname or "").strip()
    if not fn or not sn:
        return FieldOrderGuess(fn, sn, False, "low", "missing_field")

    # Multi-token fields (e.g. "Иван Иванович") — leave alone.
    if len(fn.split()) > 1 or len(sn.split()) > 1:
        return FieldOrderGuess(fn, sn, False, "low", "multi_token")

    as_is = given_name_score(fn) + surname_score(sn)
    swapped_score = given_name_score(sn) + surname_score(fn)
    delta = swapped_score - as_is

    if delta >= _MARGIN_HIGH:
        return FieldOrderGuess(sn, fn, True, "high", f"delta={delta:.2f}")
    if delta >= _MARGIN_MEDIUM:
        return FieldOrderGuess(sn, fn, True, "medium", f"delta={delta:.2f}")
    return FieldOrderGuess(fn, sn, False, "low", f"delta={delta:.2f}")


def maybe_autocorrect_fields(
    first_name: str, surname: str, *, min_confidence: str = "high"
) -> Tuple[str, str, bool]:
    """Return (first, surname, did_swap) applying auto-correct at/above min_confidence."""
    rank = {"low": 0, "medium": 1, "high": 2}
    guess = guess_field_order(first_name, surname)
    if guess.swapped and rank.get(guess.confidence, 0) >= rank.get(min_confidence, 2):
        return guess.first_name, guess.surname, True
    return (first_name or "").strip(), (surname or "").strip(), False


@dataclass(frozen=True)
class FioOrderGuess:
    """Normalized Russian-order FIO pieces from a free-text line."""

    full_name: str  # storage form: Фамилия Имя [Отчество]
    surname: str
    first_name: str  # may include patronymic as trailing tokens
    swapped_from_input: bool
    confidence: str
    reason: str = ""


def parse_fio_line(full_name: str) -> FioOrderGuess:
    """Parse free-text FIO. Bot convention: store as Фамилия Имя [Отчество].

    If the user clearly typed Western order (Имя Фамилия), reorders when
    confidence is high or medium with clear morphology on both sides.
    """
    raw = " ".join((full_name or "").split())
    if not raw:
        return FioOrderGuess("", "", "", False, "low", "empty")

    parts = raw.split()
    if len(parts) == 1:
        return FioOrderGuess(parts[0], "", parts[0], False, "low", "single")

    core = list(parts)
    patronymics: List[str] = []
    while len(core) >= 3 and is_patronymic(core[-1]):
        patronymics.insert(0, core.pop())

    if len(core) == 1:
        first = " ".join(core + patronymics)
        return FioOrderGuess(first, "", first, False, "low", "no_surname")

    t0, t1 = core[0], core[1]
    extra = core[2:]

    if extra:
        # Ambiguous multi-part: keep as entered (Russian assumption).
        surname = t0
        first = " ".join([t1, *extra, *patronymics])
        return FioOrderGuess(
            f"{surname} {first}".strip(), surname, first, False, "low", "extra_tokens"
        )

    russian = surname_score(t0) + given_name_score(t1)
    western = given_name_score(t0) + surname_score(t1)
    delta = western - russian

    if delta >= _MARGIN_HIGH:
        surname, given = t1, t0
        swapped, conf = True, "high"
    elif delta >= _MARGIN_MEDIUM:
        surname, given = t1, t0
        swapped, conf = True, "medium"
    else:
        surname, given = t0, t1
        swapped, conf = False, "low"

    first = " ".join([given, *patronymics]).strip()
    stored = f"{surname} {first}".strip()
    return FioOrderGuess(stored, surname, first, swapped, conf, f"delta={delta:.2f}")


def split_for_donate_form(full_name: str) -> Tuple[str, str]:
    """Map free-text FIO → (name/first, surname) for /donate query params."""
    g = parse_fio_line(full_name)
    if not g.full_name:
        return "", ""
    if not g.surname:
        return g.first_name, ""
    return g.first_name, g.surname


def looks_swapped_fields(first_name: str, surname: str) -> bool:
    g = guess_field_order(first_name, surname)
    return g.swapped and g.confidence in ("high", "medium")
