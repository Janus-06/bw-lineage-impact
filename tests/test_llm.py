from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from bwli.config import ConfigError, LlmConfig, LlmRuntimeConfig
from bwli.field_lineage import SqlFragment, SqlParseResult, parse_native_sql_view
from bwli.llm.explainer import (
    LlmCitationError,
    LlmEvidenceError,
    build_sql_explainer_request,
    explain_sql_with_llm,
)
from bwli.llm.openai_compatible import OpenAICompatibleClient, write_llm_audit_log
from bwli.llm.sanitizer import REDACTED, sanitize_llm_evidence, sanitize_text
from bwli.llm.sql_assistant import build_sql_draft_request


def _sample_sql_result() -> SqlParseResult:
    return parse_native_sql_view(
        """
        CREATE VIEW ZSQL_SALES_VIEW AS
        SELECT s.customer_id, SUM(s.amount) AS amount
        FROM raw.sales_orders AS s
        WHERE s.calendar_year = '2025'
        GROUP BY s.customer_id
        """,
        view_id="ZSQL_SALES_VIEW",
    )


def test_sanitizer_redacts_secrets_and_omits_forbidden_raw_or_bw_keys() -> None:
    payload = {
        "safe": "source table: raw.sales_orders",
        "password": "x",
        "api_key": "y",
        "token": "z",
        "user": "u",
        "raw_snapshot": {"payload": "must not be sent"},
        "bw_credentials": {"password": "p"},
        "structured_credential": "structured-secret",
        "credentials": "structured-credentials-secret",
        "nested": {
            "BW_PASSWORD": "p",
            "comment": "Authorization: Bearer *** should be removed",
        },
    }

    sanitized = sanitize_llm_evidence(payload)

    assert sanitized.data["safe"] == "source table: raw.sales_orders"
    assert sanitized.data["password"] == REDACTED
    assert sanitized.data["api_key"] == REDACTED
    assert sanitized.data["token"] == REDACTED
    assert sanitized.data["user"] == REDACTED
    assert "raw_snapshot" not in sanitized.data
    assert "bw_credentials" not in sanitized.data
    assert sanitized.data["structured_credential"] == REDACTED
    assert sanitized.data["credentials"] == REDACTED
    assert "BW_PASSWORD" not in sanitized.data["nested"]
    assert "Bearer" not in sanitized.data["nested"]["comment"]
    dumped = json.dumps(sanitized.model_dump(mode="json"))
    assert '"x"' not in dumped
    assert '"y"' not in dumped
    assert "must not be sent" not in dumped


def test_sanitizer_redacts_structured_environment_identifiers() -> None:
    sanitized = sanitize_llm_evidence(
        {"client": "100", "MANDT": "200", "server": "PRD", "host": "sapbw"}
    )

    dumped = json.dumps(sanitized.model_dump(mode="json"))
    for forbidden in ["100", "200", "PRD", "sapbw"]:
        assert forbidden not in dumped
    assert sanitized.data["client"] == REDACTED
    assert sanitized.data["MANDT"] == REDACTED
    assert sanitized.data["server"] == REDACTED
    assert sanitized.data["host"] == REDACTED


def test_sanitizer_redacts_text_identifiers_before_llm_prompting() -> None:
    text = (
        "WHERE owner_email = 'person@example.invalid' "
        "AND ip_addr = 10.0.0.8 "
        "AND host = sapbw.internal "
        "AND MANDT = '100' "
        "AND client=200"
    )

    sanitized = sanitize_text(text)

    assert "person@example.invalid" not in sanitized
    assert "10.0.0.8" not in sanitized
    assert "sapbw.internal" not in sanitized
    assert "MANDT = '100'" not in sanitized
    assert "client=200" not in sanitized
    assert sanitized.count(REDACTED) >= 4


def test_sanitizer_redacts_credential_assignments_before_llm_prompting() -> None:
    sanitized = sanitize_text(
        "WHERE credential = 'should-not-render' AND credentials='also-hidden'"
    )

    assert "should-not-render" not in sanitized
    assert "also-hidden" not in sanitized
    assert sanitized.count(REDACTED) == 2


def test_sanitizer_redacts_credential_sql_predicates_before_llm_prompting() -> None:
    sanitized = sanitize_text(
        "WHERE api_key IN ('should-not-render') "
        "OR password <> 'also-hidden' "
        "OR token LIKE 'third-hidden'"
    )

    for forbidden in [
        "api_key",
        "password",
        "token",
        "should-not-render",
        "also-hidden",
        "third-hidden",
    ]:
        assert forbidden not in sanitized.lower()
    assert sanitized.count(REDACTED) == 3


def test_sanitizer_redacts_suffixed_credential_user_fields_before_llm_prompting() -> None:
    sanitized = sanitize_text(
        "WHERE password_hash = 'abc123' AND bw_user = 'ALICE' AND user_id = 'BOB'"
    )

    for forbidden in ["password_hash", "abc123", "bw_user", "ALICE", "user_id", "BOB"]:
        assert forbidden.lower() not in sanitized.lower()
    assert sanitized.count(REDACTED) == 3


def test_sanitizer_redacts_function_wrapped_secret_predicates_before_prompting() -> None:
    result = parse_native_sql_view(
        "CREATE VIEW Z AS SELECT * FROM users "
        "WHERE LOWER(password) = 'hunter2' AND TRIM(api_key) IN ('abc')",
        view_id="Z",
    )

    prompt = "\n".join(message.content for message in build_sql_explainer_request(result).messages)

    for forbidden in ["password", "api_key", "hunter2", "abc"]:
        assert forbidden.lower() not in prompt.lower()
    assert REDACTED in prompt


def test_sanitizer_redacts_qualified_function_wrapped_secret_predicates() -> None:
    result = parse_native_sql_view(
        "CREATE VIEW Z AS SELECT * FROM users u WHERE LOWER(u.password) = 'hunter2'",
        view_id="Z",
    )

    prompt = "\n".join(message.content for message in build_sql_explainer_request(result).messages)

    for forbidden in ["password", "hunter2"]:
        assert forbidden.lower() not in prompt.lower()
    assert REDACTED in prompt


def test_sanitizer_redacts_escaped_sql_string_secret_predicates() -> None:
    result = parse_native_sql_view(
        "CREATE VIEW Z AS SELECT * FROM users WHERE password = 'abc''def'",
        view_id="Z",
    )

    prompt = "\n".join(message.content for message in build_sql_explainer_request(result).messages)

    for forbidden in ["password", "abc", "def"]:
        assert forbidden.lower() not in prompt.lower()
    assert REDACTED in prompt


def test_sanitizer_redacts_bw_client_predicates_before_prompting() -> None:
    for sql in [
        "CREATE VIEW Z AS SELECT * FROM sales s WHERE s.mandt <> '100'",
        "CREATE VIEW Z AS SELECT * FROM sales WHERE MANDT IN ('100','200')",
        "CREATE VIEW Z AS SELECT * FROM sales WHERE client BETWEEN 100 AND 200",
    ]:
        result = parse_native_sql_view(sql, view_id="Z")
        prompt = "\n".join(
            message.content for message in build_sql_explainer_request(result).messages
        )

        for forbidden in ["mandt", "client", "100", "200"]:
            assert forbidden.lower() not in prompt.lower()
        assert REDACTED in prompt


def test_sanitizer_redacts_reversed_sensitive_predicates_and_alias_literals() -> None:
    for sql in [
        "CREATE VIEW Z AS SELECT * FROM users WHERE 'hunter2' = password",
        "CREATE VIEW Z AS SELECT * FROM sales WHERE '100' = MANDT",
        "CREATE VIEW Z AS SELECT 'hunter2' AS password",
        "CREATE VIEW Z AS SELECT '100' AS MANDT",
    ]:
        result = parse_native_sql_view(sql, view_id="Z")
        prompt = "\n".join(
            message.content for message in build_sql_explainer_request(result).messages
        )

        for forbidden in ["hunter2", "password", "mandt", "100"]:
            assert forbidden.lower() not in prompt.lower()
        assert REDACTED in prompt


def test_sanitizer_redacts_unseparated_and_camelcase_credential_identifiers() -> None:
    sanitized = sanitize_text(
        "WHERE authToken = 'aaa' AND passwordHash = 'bbb' "
        "AND dbpassword = 'ccc' AND apiKey = 'ddd' AND userId = 'eee'"
    )

    for forbidden in [
        "authToken",
        "passwordHash",
        "dbpassword",
        "apiKey",
        "userId",
        "aaa",
        "bbb",
        "ccc",
        "ddd",
        "eee",
    ]:
        assert forbidden.lower() not in sanitized.lower()
    assert sanitized.count(REDACTED) == 5


def test_build_sql_explainer_request_specifies_bracketed_citation_format() -> None:
    request = build_sql_explainer_request(_sample_sql_result())
    system_prompt = request.messages[0].content.lower()

    assert "square-bracket" in system_prompt
    assert "[sqlfrag:where:1]" in system_prompt


def test_build_sql_draft_request_omits_explainer_task_messages() -> None:
    request = build_sql_draft_request(
        _sample_sql_result(),
        question="draft a HANA view query",
        target_dialect="sap-hana-sql",
    )
    prompt = "\n".join(message.content for message in request.messages).lower()

    assert "explain the view logic" not in prompt
    assert "task: create an advisory sql draft" in prompt
    assert "draft a hana view query" in prompt
    assert "sanitized cited evidence json" in prompt
    assert request.citation_ids


def test_build_sql_explainer_request_preserves_unique_citations_after_sensitive_redaction() -> None:
    result = parse_native_sql_view(
        "CREATE VIEW Z AS SELECT * FROM auth.passwords p JOIN auth.tokens t ON p.id = t.id",
        view_id="Z",
    )

    request = build_sql_explainer_request(result)
    prompt = "\n".join(message.content for message in request.messages)
    redacted_reference_ids = [
        citation_id for citation_id in request.citation_ids if citation_id.startswith("sqlref:Z:")
    ]

    assert len(redacted_reference_ids) == 2
    assert len(set(redacted_reference_ids)) == 2
    assert all(":h" in citation_id for citation_id in redacted_reference_ids)
    assert "password" not in prompt.lower()
    assert "auth.passwords" not in prompt.lower()
    assert "auth.tokens" not in prompt.lower()


def test_sanitizer_redacts_value_side_function_fragments_for_sensitive_predicates() -> None:
    for sql in [
        "CREATE VIEW Z AS SELECT * FROM users WHERE password = LOWER('hunter2')",
        "CREATE VIEW Z AS SELECT * FROM users WHERE password = CONCAT('hunter','2')",
    ]:
        result = parse_native_sql_view(sql, view_id="Z")
        prompt = "\n".join(
            message.content for message in build_sql_explainer_request(result).messages
        )

        for forbidden in ["hunter", "hunter2", "'2'", "password"]:
            assert forbidden.lower() not in prompt.lower()
        assert REDACTED in prompt


def test_sanitizer_redacts_qualified_reversed_sensitive_predicates() -> None:
    result = parse_native_sql_view(
        "CREATE VIEW Z AS SELECT * FROM users u WHERE 'hunter2' = u.password",
        view_id="Z",
    )

    prompt = "\n".join(message.content for message in build_sql_explainer_request(result).messages)

    for forbidden in ["hunter2", "password"]:
        assert forbidden.lower() not in prompt.lower()
    assert REDACTED in prompt


def test_build_sql_explainer_request_bounds_large_evidence_payload() -> None:
    result = _sample_sql_result()
    result = result.model_copy(
        update={
            "fragments": [
                SqlFragment(
                    id=f"sqlfrag:where:{index}",
                    kind="where",
                    text=f"WHERE amount > {index}",
                )
                for index in range(200)
            ]
        }
    )

    request = build_sql_explainer_request(result)
    prompt = "\n".join(message.content for message in request.messages)

    assert len(prompt) <= 16_000
    assert len(request.citation_ids) <= 80
    assert "evidence_truncation" in prompt


def test_build_sql_explainer_request_uses_cited_sanitized_advisory_evidence_only() -> None:
    result = _sample_sql_result()
    secret_fragment = SqlFragment(
        id="sqlfrag:where:secret",
        kind="where",
        text="WHERE api_key = 'x' AND password = 'y'",
    )
    result = result.model_copy(update={"fragments": [*result.fragments, secret_fragment]})

    request = build_sql_explainer_request(result)
    prompt = "\n".join(message.content for message in request.messages)

    assert result.fragments[0].id in prompt
    assert result.reference_edges[0].id in prompt
    assert "advisory only" in prompt.lower()
    assert "no sql rewrite" in prompt.lower()
    assert "no db object change" in prompt.lower()
    assert "'x'" not in prompt
    assert "'y'" not in prompt
    assert "api_key" not in prompt.lower()
    assert "password" not in prompt.lower()
    assert request.metadata["view_id"] == "ZSQL_SALES_VIEW"
    assert set(request.citation_ids) >= {result.fragments[0].id, result.reference_edges[0].id}


def test_sanitizer_redacts_credential_between_sql_predicate_before_prompting() -> None:
    result = parse_native_sql_view(
        "CREATE VIEW Z AS SELECT * FROM users WHERE password BETWEEN 'a' AND 'b'",
        view_id="Z",
    )

    prompt = "\n".join(message.content for message in build_sql_explainer_request(result).messages)

    assert "password" not in prompt.lower()
    assert "'a'" not in prompt
    assert "'b'" not in prompt
    assert REDACTED in prompt


def test_sanitizer_redacts_quoted_credential_predicate_before_prompting() -> None:
    result = parse_native_sql_view(
        "CREATE VIEW Z AS SELECT * FROM users WHERE \"password\" = 'should-not-render'",
        view_id="Z",
    )

    prompt = "\n".join(message.content for message in build_sql_explainer_request(result).messages)

    assert "password" not in prompt.lower()
    assert "should-not-render" not in prompt
    assert REDACTED in prompt


def test_build_sql_explainer_request_rejects_unknown_sql_raw_fragment() -> None:
    result = parse_native_sql_view("CREATE VIEW Z AS SELECT * FROM /** not valid", view_id="Z")

    with pytest.raises(LlmEvidenceError):
        build_sql_explainer_request(result)


def test_explain_sql_with_llm_rejects_unknown_sql_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BWLI_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("BWLI_LLM_MODEL", "local-fixture-model")
    monkeypatch.setenv("BWLI_LLM_API_KEY", "dummy-key")
    result = parse_native_sql_view("CREATE VIEW Z AS SELECT * FROM /** not valid", view_id="Z")
    calls = 0

    def forbidden_transport(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("unknown SQL evidence must not be sent to the LLM")

    with pytest.raises(LlmEvidenceError):
        explain_sql_with_llm(result, LlmConfig(enabled=True), transport=forbidden_transport)
    assert calls == 0


def test_openai_compatible_client_posts_chat_completion_with_auditable_redacted_metadata() -> None:
    runtime = LlmRuntimeConfig(
        base_url="http://127.0.0.1:11434/v1",
        model="local-fixture-model",
        api_key=SecretStr("fixture-runtime-key"),
    )
    request = build_sql_explainer_request(_sample_sql_result())
    observed: dict[str, object] = {}

    def handler(http_request: httpx.Request) -> httpx.Response:
        observed["url"] = str(http_request.url)
        observed["authorization"] = http_request.headers.get("Authorization")
        observed["payload"] = json.loads(http_request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-local-fixture",
                "choices": [{"message": {"content": "Advisory explanation."}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            },
        )

    client = OpenAICompatibleClient(runtime=runtime, transport=handler)
    completion = client.chat(request)

    assert str(observed["url"]).endswith("/v1/chat/completions")
    assert observed["authorization"] == "Bearer fixture-runtime-key"
    assert observed["payload"]["model"] == "local-fixture-model"  # type: ignore[index]
    assert completion.content == "Advisory explanation."
    assert completion.audit.model == "local-fixture-model"
    assert completion.audit.request_citation_ids == request.citation_ids
    assert completion.audit.citation_validation == "not_validated"
    assert len(completion.audit.prompt_sha256) == 64
    assert len(completion.audit.sanitized_input_sha256) == 64
    assert completion.audit.response_timestamp
    audited = json.dumps(completion.model_dump(mode="json"))
    assert "fixture-runtime-key" not in audited
    assert "127.0.0.1" not in audited
    assert "Authorization" not in audited


def test_write_llm_audit_log_persists_local_redacted_audit_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BWLI_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("BWLI_LLM_MODEL", "local-fixture-model")
    monkeypatch.setenv("BWLI_LLM_API_KEY", "fixture-runtime-key")
    request = build_sql_explainer_request(_sample_sql_result())

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-local-fixture",
                "choices": [
                    {"message": {"content": f"Advisory explanation. [{request.citation_ids[0]}]"}}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            },
        )

    completion = explain_sql_with_llm(
        _sample_sql_result(),
        LlmConfig(enabled=True),
        transport=handler,
    )
    assert completion is not None
    path = write_llm_audit_log(completion, tmp_path / "audit")

    record = json.loads(path.read_text(encoding="utf-8"))
    dumped = json.dumps(record)
    assert record["audit"]["model"] == "local-fixture-model"
    assert record["audit"]["citation_validation"] == "passed"
    assert record["audit"]["request_citation_ids"]
    assert len(record["audit"]["prompt_sha256"]) == 64
    assert len(record["audit"]["sanitized_input_sha256"]) == 64
    assert record["audit"]["response_timestamp"]
    assert "fixture-runtime-key" not in dumped
    assert "127.0.0.1" not in dumped


def test_openai_compatible_client_redacts_provider_usage_metadata() -> None:
    runtime = LlmRuntimeConfig(
        base_url="http://127.0.0.1:11434/v1",
        model="local-fixture-model",
        api_key=SecretStr("fixture-runtime-key"),
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-local-fixture",
                "choices": [{"message": {"content": "Advisory explanation."}}],
                "usage": {
                    "prompt_tokens": 10,
                    "api_key": "x",
                    "nested": {"password": "y"},
                },
            },
        )

    completion = OpenAICompatibleClient(runtime=runtime, transport=handler).chat(
        build_sql_explainer_request(_sample_sql_result())
    )

    audited = json.dumps(completion.model_dump(mode="json"))
    assert '"x"' not in audited
    assert '"y"' not in audited
    assert completion.audit.usage is not None
    assert completion.audit.usage["prompt_tokens"] == 10
    assert completion.audit.usage["api_key"] == REDACTED
    assert completion.audit.usage["nested"]["password"] == REDACTED


def test_openai_compatible_client_disables_environment_proxy_trust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = LlmRuntimeConfig(
        base_url="http://127.0.0.1:11434/v1",
        model="local-fixture-model",
        api_key=SecretStr("fixture-runtime-key"),
    )
    request = build_sql_explainer_request(_sample_sql_result())
    observed: dict[str, object] = {}

    class SpyClient:
        def __init__(self, **kwargs: object) -> None:
            observed.update(kwargs)

        def __enter__(self) -> SpyClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def post(self, *_args: object, **_kwargs: object) -> httpx.Response:
            return httpx.Response(
                200,
                request=httpx.Request("POST", "http://127.0.0.1:11434/v1/chat/completions"),
                json={"choices": [{"message": {"content": "Advisory explanation."}}]},
            )

    monkeypatch.setattr("bwli.llm.openai_compatible.httpx.Client", SpyClient)

    OpenAICompatibleClient(runtime=runtime).chat(request)

    assert observed["trust_env"] is False


def test_openai_compatible_client_rejects_public_runtime_endpoint_before_network() -> None:
    runtime = LlmRuntimeConfig(
        base_url="https://api.openai.com/v1",
        model="local-fixture-model",
        api_key=SecretStr("fixture-runtime-key"),
    )
    calls = 0

    def forbidden_transport(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("public endpoint must be rejected before network I/O")

    client = OpenAICompatibleClient(runtime=runtime, transport=forbidden_transport)

    with pytest.raises(ConfigError):
        client.chat(build_sql_explainer_request(_sample_sql_result()))
    assert calls == 0


def test_openai_compatible_client_rejects_link_local_metadata_endpoint_before_network() -> None:
    runtime = LlmRuntimeConfig(
        base_url="http://169.254.169.254/v1",
        model="local-fixture-model",
        api_key=SecretStr("fixture-runtime-key"),
    )
    calls = 0

    def forbidden_transport(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("link-local metadata endpoint must be rejected before network I/O")

    client = OpenAICompatibleClient(runtime=runtime, transport=forbidden_transport)

    with pytest.raises(ConfigError):
        client.chat(build_sql_explainer_request(_sample_sql_result()))
    assert calls == 0


def test_openai_compatible_client_rejects_private_non_loopback_endpoint_before_network() -> None:
    runtime = LlmRuntimeConfig(
        base_url="http://10.0.0.1/v1",
        model="local-fixture-model",
        api_key=SecretStr("fixture-runtime-key"),
    )
    calls = 0

    def forbidden_transport(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("private non-loopback endpoint must be rejected before network I/O")

    client = OpenAICompatibleClient(runtime=runtime, transport=forbidden_transport)

    with pytest.raises(ConfigError):
        client.chat(build_sql_explainer_request(_sample_sql_result()))
    assert calls == 0


def test_build_sql_explainer_request_sanitizes_header_view_id() -> None:
    result = _sample_sql_result().model_copy(
        update={"view": _sample_sql_result().view.model_copy(update={"id": "ZSQL password=hidden"})}
    )

    request = build_sql_explainer_request(result)
    prompt = "\n".join(message.content for message in request.messages)

    assert "hidden" not in prompt
    assert "password" not in prompt.lower()


def test_openai_compatible_client_accepts_httpx_mock_transport() -> None:
    runtime = LlmRuntimeConfig(
        base_url="http://localhost:8000/v1",
        model="local-fixture-model",
        api_key=SecretStr("mock-key"),
    )
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Mock transport response."}}]},
        )
    )

    completion = OpenAICompatibleClient(runtime=runtime, transport=transport).chat(
        build_sql_explainer_request(_sample_sql_result())
    )

    assert completion.content == "Mock transport response."
    assert "mock-key" not in json.dumps(completion.model_dump(mode="json"))


def test_sanitizer_redacts_full_authorization_header_and_preserves_usage_counts() -> None:
    sanitized = sanitize_llm_evidence(
        {
            "comment": "Authorization: Bearer abc123 should be removed",
            "basic": "Authorization: Basic abc123 should be removed",
            "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
        }
    )

    dumped = json.dumps(sanitized.model_dump(mode="json"))
    assert "abc123" not in dumped
    assert sanitized.data["usage"]["prompt_tokens"] == 10
    assert sanitized.data["usage"]["completion_tokens"] == 3
    assert sanitized.data["usage"]["total_tokens"] == 13


def test_explain_sql_with_llm_rejects_uncited_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BWLI_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("BWLI_LLM_MODEL", "local-fixture-model")
    monkeypatch.setenv("BWLI_LLM_API_KEY", "dummy-key")

    def uncited_transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Uncited advisory explanation."}}]},
        )

    with pytest.raises(LlmCitationError):
        explain_sql_with_llm(
            _sample_sql_result(),
            LlmConfig(enabled=True),
            transport=uncited_transport,
        )


def test_explain_sql_with_llm_rejects_multiline_completion_with_uncited_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BWLI_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("BWLI_LLM_MODEL", "local-fixture-model")
    monkeypatch.setenv("BWLI_LLM_API_KEY", "dummy-key")
    result = _sample_sql_result()
    citation_id = result.reference_edges[0].id

    def partially_cited_transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                f"Uses deterministic source evidence [{citation_id}].\n"
                                "This extra optimization claim has no citation."
                            )
                        }
                    }
                ]
            },
        )

    with pytest.raises(LlmCitationError):
        explain_sql_with_llm(
            result,
            LlmConfig(enabled=True),
            transport=partially_cited_transport,
        )


def test_explain_sql_with_llm_rejects_prefix_only_citation_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BWLI_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("BWLI_LLM_MODEL", "local-fixture-model")
    monkeypatch.setenv("BWLI_LLM_API_KEY", "dummy-key")
    result = _sample_sql_result()
    citation_id = result.reference_edges[0].id

    def prefix_only_transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": f"Unsupported claim [{citation_id}:not-real]."}}
                ]
            },
        )

    with pytest.raises(LlmCitationError):
        explain_sql_with_llm(
            result,
            LlmConfig(enabled=True),
            transport=prefix_only_transport,
        )


def test_explain_sql_with_llm_rejects_mixed_valid_and_fabricated_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BWLI_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("BWLI_LLM_MODEL", "local-fixture-model")
    monkeypatch.setenv("BWLI_LLM_API_KEY", "dummy-key")
    result = _sample_sql_result()
    citation_id = result.reference_edges[0].id

    def mixed_citation_transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": f"Unsupported claim [{citation_id}] [sqlref:not-real]."
                        }
                    }
                ]
            },
        )

    with pytest.raises(LlmCitationError):
        explain_sql_with_llm(
            result,
            LlmConfig(enabled=True),
            transport=mixed_citation_transport,
        )


def test_explain_sql_with_llm_accepts_bracket_safe_sanitized_citation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BWLI_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("BWLI_LLM_MODEL", "local-fixture-model")
    monkeypatch.setenv("BWLI_LLM_API_KEY", "dummy-key")
    result = _sample_sql_result()
    secret_edge = result.reference_edges[0].model_copy(
        update={"id": "sqlref:password:secret"}
    )
    result = result.model_copy(update={"reference_edges": [secret_edge]})
    request = build_sql_explainer_request(result)
    citation_id = request.citation_ids[0]

    assert "[" not in citation_id
    assert "]" not in citation_id
    assert "password" not in citation_id.lower()
    assert "secret" not in citation_id.lower()

    def cited_transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": f"Cited sanitized edge [{citation_id}]."}}]},
        )

    completion = explain_sql_with_llm(
        result,
        LlmConfig(enabled=True),
        transport=cited_transport,
    )

    assert completion is not None


def test_explain_sql_with_llm_allows_redacted_placeholder_with_valid_citation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BWLI_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("BWLI_LLM_MODEL", "local-fixture-model")
    monkeypatch.setenv("BWLI_LLM_API_KEY", "dummy-key")
    result = _sample_sql_result()
    citation_id = result.reference_edges[0].id

    def redacted_cited_transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": f"Redacted predicate {REDACTED} is cited [{citation_id}]."
                        }
                    }
                ]
            },
        )

    completion = explain_sql_with_llm(
        result,
        LlmConfig(enabled=True),
        transport=redacted_cited_transport,
    )

    assert completion is not None


def test_explain_sql_with_llm_accepts_completion_with_citation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BWLI_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("BWLI_LLM_MODEL", "local-fixture-model")
    monkeypatch.setenv("BWLI_LLM_API_KEY", "dummy-key")
    result = _sample_sql_result()
    citation_id = result.reference_edges[0].id

    def cited_transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": f"Uses source table [{citation_id}]."}}]},
        )

    completion = explain_sql_with_llm(
        result,
        LlmConfig(enabled=True),
        transport=cited_transport,
    )

    assert completion is not None
    assert citation_id in completion.content


def test_disabled_llm_config_does_not_perform_network_io() -> None:
    calls = 0

    def forbidden_transport(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("disabled LLM config must not call transport")

    completion = explain_sql_with_llm(
        _sample_sql_result(),
        LlmConfig(),
        transport=forbidden_transport,
    )

    assert completion is None
    assert calls == 0
