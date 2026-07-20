from platform_core.redaction import redact


def test_safe_005_redacts_nested_sensitive_values() -> None:
    result = redact(
        {
            "authorization": "Bearer sentinel-value",
            "nested": {"token": "provider-sentinel"},
            "safe": "paper",
        }
    )

    assert result == {
        "authorization": "[REDACTED]",
        "nested": {"token": "[REDACTED]"},
        "safe": "paper",
    }


def test_safe_005_redacts_common_header_and_provider_secret_variants() -> None:
    result = redact(
        {
            "x-api-key": "sentinel-api-key",
            "client_secret": "sentinel-provider-secret",
            "nested": {"provider-token": "sentinel-token"},
        }
    )

    assert result == {
        "x-api-key": "[REDACTED]",
        "client_secret": "[REDACTED]",
        "nested": {"provider-token": "[REDACTED]"},
    }


def test_safe_005_redacts_camel_case_provider_and_header_variants() -> None:
    result = redact(
        {
            "apiKey": "sentinel-api-key",
            "accessToken": "sentinel-token",
            "clientSecret": "sentinel-secret",
            "authorizationHeader": "Bearer sentinel-header",
        }
    )

    assert result == {
        "apiKey": "[REDACTED]",
        "accessToken": "[REDACTED]",
        "clientSecret": "[REDACTED]",
        "authorizationHeader": "[REDACTED]",
    }
