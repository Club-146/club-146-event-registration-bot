"""Bot-side name order heuristics (shared logic with newsite)."""

from src.name_order import parse_fio_line, split_for_donate_form
from src.pay_url import normalize_full_name


def test_normalize_western_to_russian():
    assert normalize_full_name("Петр Лавров") == "Лавров Петр"


def test_normalize_russian_kept():
    assert normalize_full_name("Лавров Петр") == "Лавров Петр"


def test_parse_high_confidence_swap():
    g = parse_fio_line("Анна Смирнова")
    # Western: Анна (given) + Смирнова (surname)
    assert g.swapped_from_input
    assert g.full_name == "Смирнова Анна"


def test_split_donate_fields():
    assert split_for_donate_form("Смирнова Анна") == ("Анна", "Смирнова")
