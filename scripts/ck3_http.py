"""Small HTTP safety helpers shared by local and remote LLM providers."""
from __future__ import annotations

import urllib.request
from typing import BinaryIO


class RedirectRejectedError(RuntimeError):
    """Raised when an API attempts an HTTP redirect."""


class _RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        response: BinaryIO,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> urllib.request.Request:
        del request, message, headers, new_url
        response.close()
        raise RedirectRejectedError(
            f"The API returned HTTP {code}, but redirects are not allowed. "
            "Use the API endpoint directly."
        )


def urlopen_no_redirect(
    request: urllib.request.Request,
    *,
    timeout: float,
) -> object:
    """Open one HTTP request while rejecting every redirect before follow-up."""
    opener = urllib.request.build_opener(_RejectRedirectHandler())
    return opener.open(request, timeout=timeout)
