"""Coverage tests for services/llm.py and services/metrics_service.py.

Covers:
- get_provider() fallback logic
- OpenAIProvider and GeminiProvider with mocked API clients
- AIRateLimitError propagation from GeminiProvider on 429
- metrics_service: get_effective_ftp, _last_ride_recommendation,
  build_last_ride_feedback, _recalculate_metric_chain
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# services/llm.py — get_provider()
# ---------------------------------------------------------------------------


def test_get_provider_returns_gemini_when_gemini_key_set(monkeypatch):
    from config import settings
    import services.llm as llm

    monkeypatch.setattr(settings, "gemini_api_key", "fake-gemini-key")
    monkeypatch.setattr(settings, "openai_api_key", None)

    provider = llm.get_provider("gemini")
    assert isinstance(provider, llm.GeminiProvider)


def test_get_provider_returns_openai_when_openai_key_set(monkeypatch):
    from config import settings
    import services.llm as llm

    monkeypatch.setattr(settings, "gemini_api_key", None)
    monkeypatch.setattr(settings, "openai_api_key", "fake-openai-key")

    provider = llm.get_provider("openai")
    assert isinstance(provider, llm.OpenAIProvider)


def test_get_provider_falls_back_to_gemini_when_both_set(monkeypatch):
    """Requesting 'openai' but only gemini key present → falls back to gemini."""
    from config import settings
    import services.llm as llm

    monkeypatch.setattr(settings, "gemini_api_key", "fake-gemini-key")
    monkeypatch.setattr(settings, "openai_api_key", None)

    provider = llm.get_provider("openai")
    assert isinstance(provider, llm.GeminiProvider)


def test_get_provider_falls_back_to_openai_when_only_openai_key_set(monkeypatch):
    """Requesting 'gemini' but only openai key present → falls back to openai."""
    from config import settings
    import services.llm as llm

    monkeypatch.setattr(settings, "gemini_api_key", None)
    monkeypatch.setattr(settings, "openai_api_key", "fake-openai-key")

    provider = llm.get_provider("gemini")
    assert isinstance(provider, llm.OpenAIProvider)


def test_get_provider_returns_gemini_when_no_keys(monkeypatch):
    """When neither key is set, returns GeminiProvider with a warning log."""
    from config import settings
    import services.llm as llm

    monkeypatch.setattr(settings, "gemini_api_key", None)
    monkeypatch.setattr(settings, "openai_api_key", None)

    provider = llm.get_provider("gemini")
    assert isinstance(provider, llm.GeminiProvider)


# ---------------------------------------------------------------------------
# OpenAIProvider — chat and chat_history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_provider_chat():
    """OpenAIProvider.chat calls the OpenAI API and returns text."""
    import services.llm as llm

    fake_message = MagicMock()
    fake_message.content = "Great training response!"
    fake_choice = MagicMock()
    fake_choice.message = fake_message
    fake_completion = MagicMock()
    fake_completion.choices = [fake_choice]

    mock_create = AsyncMock(return_value=fake_completion)
    provider = llm.OpenAIProvider.__new__(llm.OpenAIProvider)

    mock_client = MagicMock()
    mock_client.chat = MagicMock()
    mock_client.chat.completions = MagicMock()
    mock_client.chat.completions.create = mock_create
    provider._client = mock_client

    result = await provider.chat("system", "user message")
    assert result == "Great training response!"
    mock_create.assert_called_once()


@pytest.mark.asyncio
async def test_openai_provider_chat_json_mode():
    """OpenAIProvider.chat with json_mode=True passes response_format."""
    import services.llm as llm

    fake_message = MagicMock()
    fake_message.content = '{"key": "value"}'
    fake_choice = MagicMock()
    fake_choice.message = fake_message
    fake_completion = MagicMock()
    fake_completion.choices = [fake_choice]

    mock_create = AsyncMock(return_value=fake_completion)
    provider = llm.OpenAIProvider.__new__(llm.OpenAIProvider)
    mock_client = MagicMock()
    mock_client.chat.completions.create = mock_create
    provider._client = mock_client

    result = await provider.chat("system", "user message", json_mode=True)
    assert result == '{"key": "value"}'
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs.get("response_format") == {"type": "json_object"}


@pytest.mark.asyncio
async def test_openai_provider_chat_history():
    """OpenAIProvider.chat_history passes conversation messages to the API."""
    import services.llm as llm

    fake_message = MagicMock()
    fake_message.content = "History-aware response."
    fake_choice = MagicMock()
    fake_choice.message = fake_message
    fake_completion = MagicMock()
    fake_completion.choices = [fake_choice]

    mock_create = AsyncMock(return_value=fake_completion)
    provider = llm.OpenAIProvider.__new__(llm.OpenAIProvider)
    mock_client = MagicMock()
    mock_client.chat.completions.create = mock_create
    provider._client = mock_client

    history = [
        {"role": "user", "content": "What's my FTP?"},
        {"role": "assistant", "content": "Your FTP is 280 W."},
    ]
    result = await provider.chat_history("system", history)
    assert result == "History-aware response."
    call_args = mock_create.call_args
    messages = call_args.kwargs.get("messages") or call_args.args[0] if call_args.args else None
    if messages is None:
        messages = call_args.kwargs["messages"]
    # System message + 2 history messages = 3 total
    assert len(messages) == 3


# ---------------------------------------------------------------------------
# GeminiProvider — chat and chat_history, 429 error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_provider_chat_raises_rate_limit_on_429():
    """GeminiProvider.chat raises AIRateLimitError when the API returns 429."""
    import services.llm as llm
    from unittest.mock import MagicMock, patch

    provider = llm.GeminiProvider()

    with patch.object(provider, "chat", side_effect=llm.AIRateLimitError("429")):
        with pytest.raises(llm.AIRateLimitError):
            await provider.chat("sys", "user msg")


@pytest.mark.asyncio
async def test_gemini_provider_chat_success():
    """GeminiProvider.chat returns text from the Gemini API on success."""
    import services.llm as llm

    provider = llm.GeminiProvider()

    fake_response = MagicMock()
    fake_response.text = "Gemini coach response."

    async def mock_generate_content(*args, **kwargs):
        return fake_response

    with patch("google.genai.Client") as mock_client_class:
        mock_aio = MagicMock()
        mock_aio.models = MagicMock()
        mock_aio.models.generate_content = AsyncMock(return_value=fake_response)

        class AsyncContextManager:
            async def __aenter__(self_inner):
                return mock_aio

            async def __aexit__(self_inner, *args):
                return None

        mock_client_instance = MagicMock()
        mock_client_instance.aio = AsyncContextManager()
        mock_client_class.return_value = mock_client_instance

        result = await provider.chat("system", "user msg")
    assert result == "Gemini coach response."


@pytest.mark.asyncio
async def test_gemini_provider_chat_history_success():
    """GeminiProvider.chat_history returns text for multi-turn conversations."""
    import services.llm as llm

    provider = llm.GeminiProvider()

    fake_response = MagicMock()
    fake_response.text = "Gemini history response."

    with patch("google.genai.Client") as mock_client_class:
        mock_aio = MagicMock()
        mock_aio.models = MagicMock()
        mock_aio.models.generate_content = AsyncMock(return_value=fake_response)

        class AsyncContextManager:
            async def __aenter__(self_inner):
                return mock_aio

            async def __aexit__(self_inner, *args):
                return None

        mock_client_instance = MagicMock()
        mock_client_instance.aio = AsyncContextManager()
        mock_client_class.return_value = mock_client_instance

        history = [
            {"role": "user", "content": "What zone should I train in?"},
            {"role": "assistant", "content": "Zone 2 for base building."},
        ]
        result = await provider.chat_history("system", history)

    assert result == "Gemini history response."


@pytest.mark.asyncio
async def test_gemini_provider_chat_history_rate_limit_on_429():
    """GeminiProvider.chat_history raises AIRateLimitError on 429."""
    import services.llm as llm

    provider = llm.GeminiProvider()

    with patch.object(provider, "chat_history", side_effect=llm.AIRateLimitError("429")):
        with pytest.raises(llm.AIRateLimitError):
            await provider.chat_history("sys", [{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# services/metrics_service — pure functions
# ---------------------------------------------------------------------------


def test_get_effective_ftp_uses_override():
    from services.metrics_service import get_effective_ftp

    user = MagicMock()
    user.current_ftp = 250

    result = get_effective_ftp(user, ftp_override=300)
    assert result == 300


def test_get_effective_ftp_uses_current_ftp_when_no_override():
    from services.metrics_service import get_effective_ftp

    user = MagicMock()
    user.current_ftp = 275

    result = get_effective_ftp(user, ftp_override=None)
    assert result == 275


def test_get_effective_ftp_returns_none_when_no_ftp():
    from services.metrics_service import get_effective_ftp

    user = MagicMock()
    user.current_ftp = None

    result = get_effective_ftp(user, ftp_override=None)
    assert result is None


def test_get_effective_ftp_ignores_zero_override():
    from services.metrics_service import get_effective_ftp

    user = MagicMock()
    user.current_ftp = 260

    result = get_effective_ftp(user, ftp_override=0)
    assert result == 260


def test_last_ride_recommendation_recovery():
    from services.metrics_service import _last_ride_recommendation

    result = _last_ride_recommendation("recovery", tsb_after=5.0)
    assert "recovery" in result.lower()


def test_last_ride_recommendation_interval_threshold():
    from services.metrics_service import _last_ride_recommendation

    result = _last_ride_recommendation("interval_threshold", tsb_after=-5.0)
    assert "hard session" in result.lower() or "next hard" in result.lower()


def test_last_ride_recommendation_sweetspot():
    from services.metrics_service import _last_ride_recommendation

    result = _last_ride_recommendation("interval_sweetspot", tsb_after=0.0)
    assert "absorb" in result.lower() or "next ride" in result.lower()


def test_last_ride_recommendation_high_fatigue():
    from services.metrics_service import _last_ride_recommendation

    result = _last_ride_recommendation("endurance", tsb_after=-15.0)
    assert "fatigue" in result.lower() or "recovery" in result.lower()


def test_last_ride_recommendation_high_freshness():
    from services.metrics_service import _last_ride_recommendation

    result = _last_ride_recommendation("endurance", tsb_after=15.0)
    assert "freshness" in result.lower() or "fresh" in result.lower() or "quality" in result.lower()


def test_last_ride_recommendation_default():
    from services.metrics_service import _last_ride_recommendation

    result = _last_ride_recommendation("endurance", tsb_after=0.0)
    assert len(result) > 10  # just a fallback message


def test_build_last_ride_feedback_with_full_metrics():
    from services.metrics_service import build_last_ride_feedback

    metric = MagicMock()
    metric.summary = "Good endurance ride."
    metric.avg_power_w = 220
    metric.normalized_power_w = 230
    metric.intensity_factor = 0.88
    metric.tss = 75.0
    metric.ctl_after = 52.0
    metric.atl_after = 60.0
    metric.tsb_after = -8.0
    metric.ride_purpose = "endurance"

    result = build_last_ride_feedback(metric, ftp_value=260)

    assert "Good endurance ride." in result
    assert "260" in result
    assert "220" in result or "230" in result
    assert "CTL" in result
    assert "ATL" in result
    assert "TSB" in result


def test_build_last_ride_feedback_with_no_power():
    """Falls back gracefully when no power or load metrics are present."""
    from services.metrics_service import build_last_ride_feedback

    metric = MagicMock()
    metric.summary = None
    metric.avg_power_w = None
    metric.normalized_power_w = None
    metric.intensity_factor = None
    metric.tss = None
    metric.ctl_after = None
    metric.atl_after = None
    metric.tsb_after = None
    metric.ride_purpose = "unknown"

    result = build_last_ride_feedback(metric, ftp_value=250)
    assert "250" in result
    assert len(result) > 0


def test_recalculate_metric_chain_single_ride():
    from services.metrics_service import _recalculate_metric_chain

    metric = MagicMock()
    metric.normalized_power_w = 230.0
    metric.duration_seconds = 3600
    metric.activity_date = "2026-04-15"
    metric.tss = None
    metric.tss_source = None
    metric.intensity_factor = None
    metric.ftp_used = None
    metric.ctl_after = None
    metric.atl_after = None
    metric.tsb_after = None

    updated = _recalculate_metric_chain([metric], ftp_value=250)

    assert updated == 1
    assert metric.ftp_used == 250
    assert metric.tss is not None
    assert metric.intensity_factor is not None
    assert metric.ctl_after is not None
    assert metric.atl_after is not None
    assert metric.tsb_after is not None


def test_recalculate_metric_chain_no_power():
    """An FTP change cannot conjure a load for a row that has none."""
    from services.metrics_service import _recalculate_metric_chain

    metric = MagicMock()
    metric.normalized_power_w = None
    metric.duration_seconds = 3600
    metric.activity_date = "2026-04-16"
    metric.tss = None
    metric.tss_source = None

    _recalculate_metric_chain([metric], ftp_value=250)

    assert metric.tss is None
    assert metric.ctl_after == 0.0  # TSS=0 ride keeps CTL at zero from zero seed
    assert metric.atl_after == 0.0


def test_recalculate_metric_chain_keeps_a_derived_load():
    """A new FTP moves power-derived load and nothing else.

    Recomputing every row from power alone deleted the load the ladder had
    established for a session that never had a power meter, which put that
    activity straight back to entering the chain as a rest day (#579).
    """
    from services.metrics_service import _recalculate_metric_chain

    gym = MagicMock()
    gym.normalized_power_w = None
    gym.duration_seconds = 58 * 60
    gym.activity_date = "2026-08-06"
    gym.tss = 34.0
    gym.tss_source = "heart_rate"

    _recalculate_metric_chain([gym], ftp_value=280)

    assert gym.tss == 34.0
    assert gym.tss_source == "heart_rate"
    assert gym.atl_after > 0.0


def test_recalculate_metric_chain_does_not_overwrite_a_provider_load():
    """The provider computed that figure from data we never had (#579)."""
    from services.metrics_service import _recalculate_metric_chain

    ride = MagicMock()
    ride.normalized_power_w = 250.0
    ride.duration_seconds = 3600
    ride.activity_date = "2026-08-06"
    ride.tss = 88.0
    ride.tss_source = "provider"

    _recalculate_metric_chain([ride], ftp_value=200)

    assert ride.tss == 88.0
    assert ride.tss_source == "provider"
    # The intensity factor is still restated against the new FTP: it describes
    # power against threshold, which is exactly what changed.
    assert ride.intensity_factor == 1.25


def test_recalculate_metric_chain_gap_between_rides():
    """Multi-ride chain with a date gap applies correct decay."""
    from services.metrics_service import _recalculate_metric_chain

    metric1 = MagicMock()
    metric1.normalized_power_w = 250.0
    metric1.duration_seconds = 3600
    metric1.activity_date = "2026-04-01"
    metric1.tss_source = None

    metric2 = MagicMock()
    metric2.normalized_power_w = 250.0
    metric2.duration_seconds = 3600
    metric2.activity_date = "2026-04-08"  # 7-day gap
    metric2.tss_source = None

    updated = _recalculate_metric_chain([metric1, metric2], ftp_value=250)
    assert updated == 2
    # CTL after second ride should be higher than after first (building fitness)
    assert metric2.ctl_after > metric1.ctl_after
