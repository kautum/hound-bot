"""Builds a per-workspace Slack Web API client from its encrypted bot token."""

from slack_sdk.web.async_client import AsyncWebClient

from app.core.security import TokenCipher
from app.models.workspace import Workspace


def build_client_for_workspace(workspace: Workspace, cipher: TokenCipher) -> AsyncWebClient:
    bot_token = cipher.decrypt(workspace.bot_token_enc, workspace.key_version)
    return AsyncWebClient(token=bot_token)
