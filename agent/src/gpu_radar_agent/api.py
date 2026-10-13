"""HTTPS JSON transport with redirects disabled to protect bearer credentials."""

import json
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class APIError(Exception):
    def __init__(self, status: Optional[int], message: str):
        self.status = status
        super().__init__(message)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, server: str, timeout: float = 10):
        self.server = server.rstrip("/")
        self.timeout = timeout
        self.opener = build_opener(NoRedirect())

    def request(self, method: str, path: str, body: Optional[dict] = None, token: Optional[str] = None):
        headers = {"Accept": "application/json", "User-Agent": "gpu-radar-agent/0.1.1"}
        data = None
        if body is not None:
            data = json.dumps(body, allow_nan=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(self.server + "/api/v1" + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status == 204:
                    return None
                result = response.read(2 * 1024 * 1024 + 1)
                if len(result) > 2 * 1024 * 1024:
                    raise APIError(response.status, "Server response exceeded the size limit.")
                return json.loads(result)
        except HTTPError as error:
            # Do not log an arbitrary remote body, which could echo credentials.
            messages = {401: "Credential expired or revoked; log in again.", 403: "Access denied.", 404: "Server or API route not found.", 429: "Server rate limit reached."}
            raise APIError(error.code, messages.get(error.code, f"Server returned HTTP {error.code}.")) from None
        except (URLError, TimeoutError, OSError) as error:
            raise APIError(None, f"Network request failed ({type(error).__name__}).") from None
        except (ValueError, UnicodeError):
            raise APIError(None, "Server returned invalid JSON.") from None
