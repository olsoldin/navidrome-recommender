"""
Minimal client for the Subsonic API, which Navidrome implements.
Docs: https://opensubsonic.netlify.app/docs/api-reference/ (and the
original https://www.subsonic.org/pages/api.jsp)
"""
import hashlib
import secrets
from typing import Any, Dict, List, Optional

import requests

APP_NAME = "navidrome-recommender"
API_VERSION = "1.16.1"


class SubsonicError(Exception):
    """Raised when the Subsonic server returns a non-ok status."""


class SubsonicClient:
    def __init__(self, base_url: str, username: str, password: str, verify_ssl: bool = True):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.session = requests.Session()

    def _auth_params(self) -> Dict[str, str]:
        # Token-based auth: token = md5(password + salt). Never send the
        # plaintext password over the wire.
        salt = secrets.token_hex(6)
        token = hashlib.md5((self.password + salt).encode("utf-8")).hexdigest()
        return {
            "u": self.username,
            "t": token,
            "s": salt,
            "v": API_VERSION,
            "c": APP_NAME,
            "f": "json",
        }

    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/rest/{endpoint}.view"
        all_params = self._auth_params()
        if params:
            all_params.update(params)
        resp = self.session.get(url, params=all_params, timeout=25, verify=self.verify_ssl)
        resp.raise_for_status()
        payload = resp.json().get("subsonic-response", {})
        if payload.get("status") != "ok":
            err = payload.get("error", {})
            raise SubsonicError(err.get("message", "Unknown Subsonic API error"))
        return payload

    def ping(self) -> bool:
        self._get("ping")
        return True

    def get_artists(self) -> List[Dict[str, Any]]:
        """All artists in the library (ID3 view), flattened out of their
        alphabetical index buckets."""
        data = self._get("getArtists")
        indexes = data.get("artists", {}).get("index", []) or []
        artists: List[Dict[str, Any]] = []
        for idx in indexes:
            artists.extend(idx.get("artist", []) or [])
        return artists

    def get_album_list2(self, list_type: str, size: int = 500, offset: int = 0) -> List[Dict[str, Any]]:
        """type can be e.g. 'frequent', 'starred', 'newest', 'random'."""
        data = self._get("getAlbumList2", {"type": list_type, "size": size, "offset": offset})
        return data.get("albumList2", {}).get("album", []) or []

    def get_starred2(self) -> Dict[str, Any]:
        data = self._get("getStarred2")
        return data.get("starred2", {}) or {}

    def get_genres(self) -> List[Dict[str, Any]]:
        data = self._get("getGenres")
        return data.get("genres", {}).get("genre", []) or []
