from unittest.mock import patch

import pytest

from sportscards.pricing.cert_lookup import (
    CardLadderCertLookup,
    CertLookupResult,
    CertNotFoundError,
    RateLimitedError,
)


def _fake_response(status_code=200, json_data=None):
    class R:
        def __init__(self):
            self.status_code = status_code
            self._json = json_data or {}
        def json(self):
            return self._json
        def raise_for_status(self):
            if self.status_code >= 400:
                from requests import HTTPError
                raise HTTPError(response=self)
    return R()


def test_get_cert_returns_parsed_result():
    client = CardLadderCertLookup(api_base="https://example/api", api_key="k")
    payload = {
        "cert_number": "12345678",
        "grader": "PSA",
        "grade": 10,
        "last_sold": {"price": 500.0, "date": "2026-05-01"},
        "card_ladder_value": 525.0,
    }
    with patch("sportscards.pricing.cert_lookup.requests.get",
               return_value=_fake_response(200, payload)) as mock_get:
        result = client.get_cert("12345678")
    assert isinstance(result, CertLookupResult)
    assert result.grader == "PSA"
    assert result.grade == 10.0
    assert result.last_sold_price == 500.0
    assert result.card_ladder_value == 525.0
    assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer k"


def test_get_cert_404_raises():
    client = CardLadderCertLookup(api_base="https://example/api", api_key="k")
    with patch("sportscards.pricing.cert_lookup.requests.get",
               return_value=_fake_response(404, {})):
        with pytest.raises(CertNotFoundError):
            client.get_cert("nope")


def test_get_cert_rate_limit_raises_retryable():
    client = CardLadderCertLookup(api_base="https://example/api", api_key="k")
    with patch("sportscards.pricing.cert_lookup.requests.get",
               return_value=_fake_response(429, {})):
        with pytest.raises(RateLimitedError):
            client.get_cert("x")
