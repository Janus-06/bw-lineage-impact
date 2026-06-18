from __future__ import annotations

import pytest

from bwli.redact import redact_text
from bwli.store.secret_guard import SecretPersistenceError, assert_no_persisted_secrets


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


def test_cookie_file_redaction_and_no_snapshot_persistence() -> None:
    text = (
        "Cookie: SAP_SESSIONID=file-session; __VCAP_ID__=app-instance\n"
        "Set-Cookie: MYSAPSSO2=sso-ticket; Path=/; HttpOnly\n"
        "GET /sap/bw/modeling?cookie=SAP_SESSIONID=query-session"
        "&sap-client=100&sap-language=KO\n"
        "BW_COOKIE_FILE=/tmp/unsafe-cookie-path"
    )

    scrubbed = redact_text(text)

    for secret_fragment in [
        "SAP_SESSIONID",
        "__VCAP_ID__",
        "MYSAPSSO2",
        "file-session",
        "app-instance",
        "sso-ticket",
        "query-session",
        "unsafe-cookie-path",
    ]:
        assert secret_fragment not in scrubbed
    assert "sap-client=100" in scrubbed
    assert "sap-language=KO" in scrubbed

    for unsafe in [
        {"metadata": {"cookie": "SAP_SESSIONID=file-session"}},
        {"metadata": {"Set-Cookie": "MYSAPSSO2=sso-ticket"}},
        "Cookie: SAP_SESSIONID=file-session",
        "Set-Cookie: MYSAPSSO2=sso-ticket",
        "__VCAP_ID__=app-instance",
        "SAP_SESSIONID=file-session",
        "BW_COOKIE_FILE=/tmp/unsafe-cookie-path",
        "cookie_file=/tmp/bw-cookies.txt",
        "cookie_path=/tmp/bw-cookies.txt",
        "cookie-jar=/tmp/bw-cookies.txt",
        "cookie jar: /tmp/bw-cookies.txt",
    ]:
        try:
            assert_no_persisted_secrets(unsafe)
        except SecretPersistenceError:
            continue
        raise AssertionError(f"secret guard allowed cookie-bearing value: {unsafe!r}")

    assert_no_persisted_secrets({"metadata": {"sap-client": "100", "sap-language": "KO"}})


@pytest.mark.parametrize(
    "line",
    [
        "BW_COOKIE_FILE=/tmp/bw-cookies.txt",
        "cookie_file: /tmp/bw-cookies.txt",
        "cookie_path=/tmp/bw-cookies.txt",
        "cookie jar: /tmp/bw-cookies.txt",
        "cookie-jar=/tmp/bw-cookies.txt",
        "cookies_file: /tmp/cookies.txt",
    ],
)
def test_cookie_path_redaction_accepts_colon_and_equals_separators(line: str) -> None:
    scrubbed = redact_text(f"{line} sap-client=100 sap-language=KO")

    assert "/tmp/" not in scrubbed
    assert "bw-cookies.txt" not in scrubbed
    assert "cookies.txt" not in scrubbed
    assert "[COOKIE_REDACTED]" in scrubbed
    assert "sap-client=100" in scrubbed
    assert "sap-language=KO" in scrubbed


def test_multi_suffix_sap_session_cookie_redacts_name_and_value() -> None:
    scrubbed = redact_text("SAP_SESSIONID_ABC_100=file-session")

    assert "SAP_SESSIONID_ABC_100" not in scrubbed
    assert "file-session" not in scrubbed
    assert "[COOKIE_REDACTED]" in scrubbed


def test_multi_suffix_sap_session_cookie_is_rejected_for_persistence() -> None:
    with pytest.raises(SecretPersistenceError):
        assert_no_persisted_secrets("SAP_SESSIONID_ABC_100=file-session")
