"""Tests du bandeau de marché (réponses d'API simulées, aucun accès réseau)."""
import json
from datetime import date

import pytest

from veille.config import MarketSymbol
from veille.markets import fetch_quotes, last_two
from veille.models import MarketQuote

SP500 = MarketSymbol(name="S&P 500", provider="fred", symbol="SP500", kind="indice")
FED = MarketSymbol(name="Taux Fed", provider="fred", symbol="DFEDTARU", kind="taux", symbol_low="DFEDTARL")
CADUSD = MarketSymbol(name="CAD/USD", provider="valet", symbol="FXCADUSD", kind="devise")
BOC = MarketSymbol(name="Taux BdC", provider="valet", symbol="V39079", kind="taux")
TSX = MarketSymbol(name="TSX", provider="twelvedata", symbol="GSPTSE", kind="indice")
KEYS = {"fred": "cle-fred", "twelvedata": "cle-td"}


def d(day: int) -> date:
    return date(2026, 11, day)


def fred(*rows: tuple[str, str]) -> bytes:
    """Réponse de FRED : observations de la plus récente à la plus ancienne."""
    return json.dumps({"observations": [{"date": day, "value": value} for day, value in rows]}).encode()


def valet(series: str, *rows: tuple[str, str]) -> bytes:
    """Réponse de l'API Valet : observations dans l'ordre que l'API choisit."""
    return json.dumps({"observations": [{"d": day, series: {"v": value}} for day, value in rows]}).encode()


RESPONSES = {
    "series_id=SP500": fred(("2026-11-13", "."), ("2026-11-12", "6012.0"), ("2026-11-11", "6024.05")),
    "series_id=DFEDTARU": fred(("2026-11-13", "4.00"), ("2026-11-12", "4.00")),
    "series_id=DFEDTARL": fred(("2026-11-13", "3.75"), ("2026-11-12", "3.75")),
    "FXCADUSD": valet("FXCADUSD", ("2026-11-12", "0.7015"), ("2026-11-10", "0.7052")),
    "V39079": valet("V39079", ("2026-11-10", "2.75"), ("2026-11-12", "2.50")),
    "symbol=GSPTSE": json.dumps({"status": "ok", "values": [
        {"datetime": "2026-11-12", "close": "25104.32"}, {"datetime": "2026-11-11", "close": "25004.30"}]}).encode(),
}


def fixture_fetch(url: str) -> bytes:
    for marker, body in RESPONSES.items():
        if marker in url:
            return body
    raise AssertionError(f"URL inattendue : {url}")


def fails_for(marker: str):
    def fetch(url: str) -> bytes:
        if marker in url:
            raise ConnectionError("panne")
        return fixture_fetch(url)
    return fetch


def test_last_two_skips_missing_values_whatever_the_order():
    obs = [(d(10), 6024.0), (d(13), None), (d(12), 6012.0), (d(11), None)]
    assert last_two(obs) == ((d(12), 6012.0), (d(10), 6024.0))


def test_last_two_with_one_or_no_valid_observation():
    assert last_two([(d(12), 5.0), (d(11), None)]) == ((d(12), 5.0), None)
    assert last_two([(d(12), None)]) is None
    assert last_two([]) is None


def test_index_change_is_percent():
    q = fetch_quotes((SP500,), fixture_fetch, KEYS)[0]
    assert (q.name, q.kind, q.value, q.as_of) == ("S&P 500", "indice", 6012.0, date(2026, 11, 12))
    assert q.change_pct == pytest.approx(-0.2, abs=0.001)
    assert q.value_low is None


def test_rate_change_is_points_and_range_has_low():
    q = fetch_quotes((FED,), fixture_fetch, KEYS)[0]
    assert (q.value, q.value_low, q.change_pct) == (4.00, 3.75, 0.0)


def test_valet_currency_and_rate():
    cad, boc = fetch_quotes((CADUSD, BOC), fixture_fetch, KEYS)
    assert (cad.value, cad.as_of) == (0.7015, date(2026, 11, 12))
    assert cad.change_pct == pytest.approx(-0.5247, abs=0.001)
    assert (boc.value, boc.change_pct) == (2.50, pytest.approx(-0.25))


def test_twelvedata_index():
    q = fetch_quotes((TSX,), fixture_fetch, KEYS)[0]
    assert (q.value, q.as_of) == (25104.32, date(2026, 11, 12))
    assert q.change_pct == pytest.approx(0.4, abs=0.001)


def test_api_keys_are_sent_to_their_provider():
    urls = []
    fetch_quotes((SP500, TSX), lambda url: urls.append(url) or fixture_fetch(url), KEYS)
    assert "api_key=cle-fred" in urls[0] and "apikey=cle-td" in urls[1]


def test_failing_symbol_gives_empty_quote_and_keeps_the_rest():
    quotes = fetch_quotes((SP500, TSX), fails_for("twelvedata"), KEYS)
    assert quotes[1] == MarketQuote(name="TSX", kind="indice", value=None, value_low=None,
                                    change_pct=None, as_of=None)
    assert quotes[0].value == 6012.0


def test_error_payload_gives_empty_quote():
    body = json.dumps({"status": "error", "code": 403, "message": "plan insuffisant"}).encode()
    assert fetch_quotes((TSX,), lambda url: body, KEYS)[0].value is None
    assert fetch_quotes((SP500,), lambda url: b"<html>oups</html>", KEYS)[0].value is None


def test_missing_key_gives_empty_quote_without_calling_network():
    def never(url: str) -> bytes:
        raise AssertionError("aucun appel réseau attendu sans clé")
    quotes = fetch_quotes((SP500, TSX), never, {"fred": None})
    assert [q.value for q in quotes] == [None, None]


def test_single_observation_gives_value_without_change():
    q = fetch_quotes((SP500,), lambda url: fred(("2026-11-12", "6012.0")), KEYS)[0]
    assert (q.value, q.change_pct) == (6012.0, None)


def test_failure_log_never_contains_the_api_key(caplog):
    def leaky(url: str) -> bytes:
        raise ConnectionError(f"échec pour {url}")
    fetch_quotes((SP500,), leaky, KEYS)
    assert "cle-fred" not in caplog.text and "S&P 500" in caplog.text
