"""Supabase Management OAuth, deliberately separate from worker Auth.

Fixed official endpoints, S256 PKCE, finite reads, no redirects or automatic
replay of a single-use code/refresh token. Browser state is handled by installer.
"""

import asyncio
import base64
import hashlib
import json
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, Field, SecretStr

from .client import bounded_response, RpcError
from .releases import OperatorError


class OAuthTokens(BaseModel):
    access_token: SecretStr = Field(repr=False)
    refresh_token: SecretStr = Field(repr=False)
    expires_in: int = Field(strict=True, ge=1, le=86400)
    token_type: str = "Bearer"

    def private_values(self):
        return {
            "access_token": self.access_token.get_secret_value(),
            "refresh_token": self.refresh_token.get_secret_value(),
            "expires_in": self.expires_in,
        }


class SupabaseOAuth:
    def __init__(self, client_id, client_secret, callback_url, *, client=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.callback_url = callback_url
        self.client = client or httpx.AsyncClient(timeout=10, follow_redirects=False)
        self.owns_client = client is None

    async def close(self):
        if self.owns_client:
            await self.client.aclose()

    def authorize_url(self, state, verifier):
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        return "https://api.supabase.com/v1/oauth/authorize?" + urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.callback_url,
                "response_type": "code",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )

    async def exchange(self, values):
        try:
            async with asyncio.timeout(10):
                async with self.client.stream(
                    "POST",
                    "https://api.supabase.com/v1/oauth/token",
                    data=values,
                    auth=httpx.BasicAuth(self.client_id, self.client_secret),
                    follow_redirects=False,
                ) as response:
                    if response.status_code != 200:
                        raise OperatorError("oauth_exchange_failed_reauthorize")
                    content = await bounded_response(response, 65536)
            data = json.loads(content)
            tokens = OAuthTokens.model_validate(data)
            if tokens.token_type.lower() != "bearer" or any(
                not 1 <= len(value.get_secret_value()) <= 8192
                for value in (tokens.access_token, tokens.refresh_token)
            ):
                raise ValueError()
            return tokens
        except (httpx.HTTPError, TimeoutError):
            raise OperatorError("oauth_exchange_ambiguous_reauthorize") from None
        except (ValueError, KeyError, TypeError, RpcError):
            raise OperatorError("invalid_oauth_response_reauthorize") from None

    async def redeem(self, code, verifier):
        return await self.exchange(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.callback_url,
                "code_verifier": verifier,
            }
        )

    async def refresh(self, refresh_token):
        return await self.exchange(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        )
