"""PII Sanitizer 单测。"""

from __future__ import annotations

import pytest

from backend.tools.sanitizer import (
    DEFAULT_PII_PATTERNS,
    Sanitizer,
    sanitize,
    sanitize_with_stats,
)

# ---------- 各模式命中 ----------


def test_redacts_email():
    out = sanitize("Reach me at alice@example.com please")
    assert "alice@example.com" not in out
    assert "[REDACTED]" in out


def test_redacts_phone_cn():
    out = sanitize("我的手机：13812345678 周末可联系")
    assert "13812345678" not in out
    assert "[REDACTED]" in out


def test_redacts_phone_cn_with_country_code():
    out = sanitize("Call +86 13912345678 anytime")
    assert "13912345678" not in out


def test_redacts_phone_us_parens_format():
    out = sanitize("Office: (415) 555-2671")
    assert "(415) 555-2671" not in out
    assert "[REDACTED]" in out


def test_redacts_phone_us_dash_format():
    out = sanitize("Direct: 415-555-2671 ext 12")
    assert "415-555-2671" not in out


def test_redacts_chinese_id_card():
    out = sanitize("身份证号 110101199001011234 仅供核验")
    assert "110101199001011234" not in out
    assert "[REDACTED]" in out


def test_redacts_chinese_id_card_with_trailing_x():
    out = sanitize("ID: 11010119900101123X end")
    assert "11010119900101123X" not in out


def test_redacts_ssn():
    out = sanitize("SSN on file: 123-45-6789")
    assert "123-45-6789" not in out


def test_redacts_credit_card_with_spaces():
    out = sanitize("VISA 4111 1111 1111 1111 expires 12/29")
    assert "4111 1111 1111 1111" not in out


def test_redacts_credit_card_with_dashes():
    out = sanitize("Card 4111-1111-1111-1111 on file")
    assert "4111-1111-1111-1111" not in out


def test_redacts_openai_api_key():
    out = sanitize("export OPENAI_API_KEY=sk-abcdef1234567890ABCDEF12")
    assert "sk-abcdef1234567890ABCDEF12" not in out


def test_redacts_bearer_token():
    out = sanitize("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM")
    assert "eyJhbGciOiJIUzI1NiJ9" not in out


# ---------- 不命中 ----------


def test_keeps_innocent_text():
    text = "ClickUp pricing starts at $5 / user / month, 14-day trial"
    assert sanitize(text) == text


def test_keeps_short_numbers():
    """短数字（< 13 位）不应被信用卡正则吞掉。"""
    text = "Released v1.0.0 with 12 new features, used by 9999 teams"
    assert sanitize(text) == text


def test_empty_input_safe():
    assert sanitize("") == ""
    assert sanitize(None) is None  # type: ignore[arg-type]


# ---------- 统计接口 ----------


def test_stats_counts_each_pattern():
    text = "Email alice@x.com or call 13812345678 or 13987654321"
    clean, stats = sanitize_with_stats(text)
    assert "[REDACTED]" in clean
    assert stats.hits_by_name.get("email") == 1
    assert stats.hits_by_name.get("phone_cn") == 2
    assert stats.total == 3


def test_stats_zero_on_clean_text():
    _, stats = sanitize_with_stats("nothing to see here")
    assert stats.total == 0
    assert stats.hits_by_name == {}


# ---------- 自定义 Sanitizer ----------


def test_redact_label_includes_type():
    s = Sanitizer(redact_label=True)
    out = s.sanitize("Mail: a@b.com phone: 13812345678")
    assert "[REDACTED:EMAIL]" in out
    assert "[REDACTED:PHONE_CN]" in out


def test_custom_pattern_subset():
    # 只用 email 一个模式
    email_only = [p for p in DEFAULT_PII_PATTERNS if p.name == "email"]
    s = Sanitizer(patterns=email_only)
    out = s.sanitize("alice@x.com 13812345678 4111111111111111")
    assert "alice@x.com" not in out
    # 电话与卡号保留
    assert "13812345678" in out
    assert "4111111111111111" in out


# ---------- 边界 ----------


def test_does_not_double_redact():
    """已经是 [REDACTED] 的文本再脱敏一次应保持稳定。"""
    once = sanitize("alice@x.com")
    twice = sanitize(once)
    assert once == twice


def test_handles_multiline():
    text = "Contact:\n  email: bob@y.com\n  phone: 13812345678\n  ssn: 123-45-6789\n"
    out, stats = sanitize_with_stats(text)
    assert "bob@y.com" not in out
    assert "13812345678" not in out
    assert "123-45-6789" not in out
    assert stats.total == 3


@pytest.mark.parametrize(
    "text,should_redact",
    [
        ("alice@example.com", True),
        ("contact@x.io", True),
        ("not.an.email@", False),  # 无 TLD
        ("@nodomain.com", False),
    ],
)
def test_email_edge_cases(text, should_redact):
    out = sanitize(text)
    if should_redact:
        assert "[REDACTED]" in out
    else:
        assert out == text
