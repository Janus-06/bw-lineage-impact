from __future__ import annotations

from bwli.redact import redact_text


def test_redact_text_removes_declared_secret_and_host_but_keeps_sap_context() -> None:
    text = (
        "HTTP 401 from https://bw.example.invalid/sap/bw/modeling?"
        "sap-client=100&sap-language=KO password=mock-secret-value"
    )

    scrubbed = redact_text(
        text,
        secret_values=["mock-secret-value"],
        urls=["https://bw.example.invalid"],
    )

    assert "mock-secret-value" not in scrubbed
    assert "bw.example.invalid" not in scrubbed
    assert "sap-client=100" in scrubbed
    assert "sap-language=KO" in scrubbed
    assert "[BW_HOST]" in scrubbed or "[BW_URL]" in scrubbed
    assert "[REDACTED]" in scrubbed


def test_redact_text_keeps_sap_context_after_generic_secret_query_param() -> None:
    text = (
        "HTTP 401 from https://bw.example.invalid/sap/bw/modeling?"
        "password=mock-leaked-value&sap-client=100&sap-language=KO"
    )

    scrubbed = redact_text(text, urls=["https://bw.example.invalid"])

    assert "mock-leaked-value" not in scrubbed
    assert "bw.example.invalid" not in scrubbed
    assert "password=[REDACTED]" in scrubbed
    assert "sap-client=100" in scrubbed
    assert "sap-language=KO" in scrubbed


def test_redact_text_keeps_sap_context_after_authorization_bearer_query_param() -> None:
    text = (
        "HTTP 401 from https://bw.example.invalid/sap/bw/modeling?"
        "authorization=Bearer abc123&sap-client=100&sap-language=KO"
    )

    scrubbed = redact_text(text, urls=["https://bw.example.invalid"])

    assert "abc123" not in scrubbed
    assert "bw.example.invalid" not in scrubbed
    assert "authorization=[REDACTED]" in scrubbed
    assert "sap-client=100" in scrubbed
    assert "sap-language=KO" in scrubbed


def test_redact_text_removes_url_userinfo_and_token_like_values() -> None:
    text = (
        "GET https://user:***@bw.example.invalid/sap/bw "
        "Authorization:Bearer *** token=xyz api_key=qwerty"
    )

    scrubbed = redact_text(text, urls=["https://bw.example.invalid"])

    assert "user:pass" not in scrubbed
    assert "abc" not in scrubbed
    assert "xyz" not in scrubbed
    assert "qwerty" not in scrubbed
    assert "https://[REDACTED]@" in scrubbed
    assert "Authorization:[REDACTED]" in scrubbed
