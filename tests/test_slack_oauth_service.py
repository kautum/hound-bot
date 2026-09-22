import httpx
import pytest
import respx

from app.services.slack_oauth import (
    SLACK_OAUTH_ACCESS_URL,
    SlackOAuthError,
    build_install_url,
    exchange_code_for_token,
)


class TestBuildInstallUrl:
    def test_includes_least_privilege_scopes_and_state(self):
        url = build_install_url(
            client_id="123.456",
            redirect_uri="https://example.ngrok-free.app/slack/oauth/callback",
            state="the-state-value",
        )
        assert url.startswith("https://slack.com/oauth/v2/authorize?")
        assert "client_id=123.456" in url
        assert "state=the-state-value" in url
        assert "app_mentions%3Aread" in url  # scope=... url-encoded
        assert "chat%3Awrite" in url


class TestExchangeCodeForToken:
    @respx.mock
    async def test_returns_bot_token_and_team_id_on_success(self):
        respx.post(SLACK_OAUTH_ACCESS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "ok": True,
                    "access_token": "xoxb-fake-token",
                    "team": {"id": "T12345", "name": "Test Workspace"},
                },
            )
        )

        async with httpx.AsyncClient() as client:
            bot_token, team_id, authed_user_id = await exchange_code_for_token(
                client,
                client_id="123.456",
                client_secret="shh",
                code="the-code",
                redirect_uri="https://example.ngrok-free.app/slack/oauth/callback",
            )

        assert bot_token == "xoxb-fake-token"
        assert team_id == "T12345"
        assert authed_user_id is None

    @respx.mock
    async def test_raises_when_slack_returns_ok_false(self):
        respx.post(SLACK_OAUTH_ACCESS_URL).mock(
            return_value=httpx.Response(200, json={"ok": False, "error": "invalid_code"})
        )

        async with httpx.AsyncClient() as client:
            with pytest.raises(SlackOAuthError, match="invalid_code"):
                await exchange_code_for_token(
                    client,
                    client_id="123.456",
                    client_secret="shh",
                    code="bad-code",
                    redirect_uri="https://example.ngrok-free.app/slack/oauth/callback",
                )

    @respx.mock
    async def test_returns_authed_user_id_when_present(self):
        respx.post(SLACK_OAUTH_ACCESS_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "ok": True,
                    "access_token": "xoxb-fake-token",
                    "team": {"id": "T12345", "name": "Test Workspace"},
                    "authed_user": {"id": "U12345"},
                },
            )
        )

        async with httpx.AsyncClient() as client:
            bot_token, team_id, authed_user_id = await exchange_code_for_token(
                client,
                client_id="123.456",
                client_secret="shh",
                code="the-code",
                redirect_uri="https://example.ngrok-free.app/slack/oauth/callback",
            )

        assert bot_token == "xoxb-fake-token"
        assert team_id == "T12345"
        assert authed_user_id == "U12345"

    @respx.mock
    async def test_raises_on_unexpected_response_shape_rather_than_returning_garbage(self):
        """Fail loud, not plausible — see ENGINEERING.md's correctness rules."""
        respx.post(SLACK_OAUTH_ACCESS_URL).mock(
            return_value=httpx.Response(200, json={"ok": True})  # missing access_token/team
        )

        async with httpx.AsyncClient() as client:
            with pytest.raises(SlackOAuthError, match="unexpected"):
                await exchange_code_for_token(
                    client,
                    client_id="123.456",
                    client_secret="shh",
                    code="the-code",
                    redirect_uri="https://example.ngrok-free.app/slack/oauth/callback",
                )
