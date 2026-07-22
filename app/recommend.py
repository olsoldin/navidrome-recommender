"""
Turns a Navidrome library into recommendations.

Approach (no API key required):
  1. Pull the library's artists, play counts (getAlbumList2 type=frequent),
     and starred items from Navidrome to find the "seed" artists the
     listener actually plays.
  2. Look each seed artist up on MusicBrainz to get its MBID.
  3. Ask ListenBrainz's public "similar artists" dataset for each MBID.
  4. Aggregate + rank candidates, dropping anything already in the library.

MusicBrainz asks anonymous callers to stay near 1 request/second, so
lookups are cached to disk and paced accordingly.
"""
import json
import random
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

from .navidrome_client import SubsonicClient

USER_AGENT = "navidrome-recommender/1.0 (+self-hosted music discovery tool)"
MB_SEARCH_URL = "https://musicbrainz.org/ws/2/artist/"
LB_SIMILAR_URL = "https://labs.api.listenbrainz.org/similar-artists/json"
LB_ALGORITHM = (
    "session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30"
)
CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "lookup_cache.json"


def _normalize(name: str) -> str:
    name = (name or "").lower().strip()
    name = re.sub(r"^the\s+", "", name)
    name = re.sub(r"[^a-z0-9]+", "", name)
    return name


def _extract_similar(raw: Any, seed_mbid: str) -> List[Dict[str, Any]]:
    """Defensively parse the ListenBrainz labs response — it's served
    straight out of a generic dataset hoster, so field names have shifted
    before. Try the documented shape first, fall back gracefully."""
    results: List[Dict[str, Any]] = []
    rows = raw if isinstance(raw, list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        mbid = row.get("artist_mbid") or row.get("similar_artist_mbid") or row.get("mbid")
        name = row.get("name") or row.get("artist_name") or row.get("similar_artist_name")
        score = row.get("score")
        if score is None:
            score = row.get("similarity") or row.get("total_listen_count") or 0
        if not mbid or not name or mbid == seed_mbid:
            continue
        results.append({"mbid": mbid, "name": name, "score": float(score or 0)})
    return results


class RecommendationEngine:
    def __init__(self):
        self._cache: Dict[str, Any] = self._load_cache()

    def _load_cache(self) -> Dict[str, Any]:
        if CACHE_PATH.exists():
            try:
                return json.loads(CACHE_PATH.read_text())
            except Exception:
                return {}
        return {}

    def _save_cache(self) -> None:
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(json.dumps(self._cache))
        except Exception:
            pass  # cache is a pure optimization; never let it break a scan

    def lookup_mbid(self, artist_name: str) -> Optional[str]:
        key = f"mbid:{_normalize(artist_name)}"
        if key in self._cache:
            return self._cache[key]
        mbid = None
        try:
            resp = requests.get(
                MB_SEARCH_URL,
                params={"query": f'artist:"{artist_name}"', "fmt": "json", "limit": 5},
                headers={"User-Agent": USER_AGENT},
                timeout=15,
            )
            resp.raise_for_status()
            candidates = resp.json().get("artists", []) or []
            for cand in candidates:
                if _normalize(cand.get("name", "")) == _normalize(artist_name):
                    mbid = cand.get("id")
                    break
            if mbid is None and candidates:
                mbid = candidates[0].get("id")
        except Exception:
            mbid = None
        finally:
            self._cache[key] = mbid
            self._save_cache()
            time.sleep(1.0)  # respect MusicBrainz's ~1 req/sec guidance
        return mbid

    def similar_artists(self, mbid: str) -> List[Dict[str, Any]]:
        key = f"similar:{mbid}"
        if key in self._cache:
            return self._cache[key]
        results: List[Dict[str, Any]] = []
        try:
            resp = requests.get(
                LB_SIMILAR_URL,
                params={"artist_mbids": mbid, "algorithm": LB_ALGORITHM},
                headers={"User-Agent": USER_AGENT},
                timeout=15,
            )
            resp.raise_for_status()
            results = _extract_similar(resp.json(), mbid)
        except Exception:
            results = []
        finally:
            self._cache[key] = results
            self._save_cache()
            time.sleep(0.3)
        return results

    def _artist_genre_multipliers(
        self,
        client: SubsonicClient,
        genre_weights: Dict[str, float],
        progress: Callable[[str], None],
    ) -> Dict[str, float]:
        """Fetch which artists belong to each *non-neutral* weighted genre
        (skipping any genre left at 1.0x, since it wouldn't change
        anything) and return artist name -> combined multiplier. An
        artist tagged under more than one weighted genre gets the average
        of those weights; an artist matching none of them is left out
        entirely, and callers should treat that as neutral (1.0x)."""
        hits: Dict[str, List[float]] = {}
        active = {g: w for g, w in (genre_weights or {}).items() if abs(w - 1.0) > 1e-6}
        for genre_name, weight in active.items():
            progress(f"Applying genre weight for {genre_name} ({weight:.1f}x)...")
            try:
                albums = client.get_albums_by_genre(genre_name, size=500)
            except Exception:
                continue
            for album in albums:
                name = album.get("artist")
                if name:
                    hits.setdefault(name, []).append(weight)
        return {name: sum(ws) / len(ws) for name, ws in hits.items()}

    def build_recommendations(
        self,
        client: SubsonicClient,
        progress: Callable[[str], None] = lambda msg: None,
        top_n_seeds: int = 15,
        max_recommendations: int = 30,
        genre_weights: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        progress("Reading your library...")
        artists = client.get_artists()
        library_names = {_normalize(a["name"]) for a in artists if a.get("name")}

        progress("Checking play history...")
        play_scores: Dict[str, float] = {}
        diagnostics: Dict[str, Any] = {}
        try:
            frequent = client.get_album_list2("frequent", size=500)
            diagnostics["frequent_albums_returned"] = len(frequent)
            diagnostics["frequent_albums_with_playcount"] = sum(
                1 for a in frequent if (a.get("playCount") or 0) > 0
            )
        except Exception as e:
            frequent = []
            diagnostics["frequent_error"] = str(e)
        for album in frequent:
            name = album.get("artist")
            if name:
                play_scores[name] = play_scores.get(name, 0.0) + float(album.get("playCount", 0) or 0)

        progress("Checking starred favorites...")
        try:
            starred = client.get_starred2()
            diagnostics["starred_albums"] = len(starred.get("album") or [])
            diagnostics["starred_artists"] = len(starred.get("artist") or [])
            diagnostics["starred_songs"] = len(starred.get("song") or [])
        except Exception as e:
            starred = {}
            diagnostics["starred_error"] = str(e)
        for album in starred.get("album", []) or []:
            name = album.get("artist")
            if name:
                play_scores[name] = play_scores.get(name, 0.0) + 5
        for artist in starred.get("artist", []) or []:
            name = artist.get("name")
            if name:
                play_scores[name] = play_scores.get(name, 0.0) + 8
        for song in starred.get("song", []) or []:
            name = song.get("artist")
            if name:
                play_scores[name] = play_scores.get(name, 0.0) + 3

        has_real_play_data = bool(play_scores) and max(play_scores.values()) > 0

        artist_multipliers: Dict[str, float] = {}
        if genre_weights:
            artist_multipliers = self._artist_genre_multipliers(client, genre_weights, progress)

        if has_real_play_data:
            if artist_multipliers:
                for name in list(play_scores.keys()):
                    play_scores[name] *= artist_multipliers.get(name, 1.0)
            seeds = sorted(play_scores.items(), key=lambda kv: kv[1], reverse=True)[:top_n_seeds]
        else:
            # No usable play-count or starred data (common if listens happen
            # through a client that doesn't report back to Navidrome, or the
            # library is brand new). Sample across the whole library instead
            # of just taking the first N — which, sorted alphabetically,
            # skews toward names starting with digits/symbols. Weight by
            # album count as a rough proxy for "artists you're invested in."
            progress("No play history found — sampling across your library instead...")
            pool = [a for a in artists if a.get("name")]
            weights = [
                max(0.001, max(1, int(a.get("albumCount", 1) or 1)) * artist_multipliers.get(a["name"], 1.0))
                for a in pool
            ]
            sample_size = min(top_n_seeds, len(pool))
            chosen: List[Dict[str, Any]] = []
            chosen_names = set()
            pool_copy, weights_copy = list(pool), list(weights)
            while len(chosen) < sample_size and pool_copy:
                picked = random.choices(pool_copy, weights=weights_copy, k=1)[0]
                idx = pool_copy.index(picked)
                pool_copy.pop(idx)
                weights_copy.pop(idx)
                if picked["name"] not in chosen_names:
                    chosen.append(picked)
                    chosen_names.add(picked["name"])
            seeds = [(a["name"], 1.0) for a in chosen]
        seed_names = [s[0] for s in seeds]
        seed_weight = dict(seeds)
        max_seed_score = max(seed_weight.values()) if seed_weight else 1.0

        progress("Reading genre breakdown...")
        try:
            genres_raw = client.get_genres()
        except Exception:
            genres_raw = []
        top_genres = sorted(
            (
                {"name": g.get("value"), "songCount": g.get("songCount", 0)}
                for g in genres_raw
                if g.get("value")
            ),
            key=lambda g: g["songCount"],
            reverse=True,
        )[:8]

        candidates: Dict[str, Dict[str, Any]] = {}
        for i, name in enumerate(seed_names):
            progress(f"Finding music similar to {name} ({i + 1}/{len(seed_names)})...")
            mbid = self.lookup_mbid(name)
            if not mbid:
                continue
            weight = seed_weight.get(name, 1.0) / max_seed_score
            for cand in self.similar_artists(mbid):
                cand_name = cand.get("name")
                if not cand_name or _normalize(cand_name) in library_names:
                    continue
                entry = candidates.setdefault(
                    cand_name,
                    {"name": cand_name, "mbid": cand.get("mbid"), "score": 0.0, "because_of": set()},
                )
                entry["score"] += cand.get("score", 0.0) * weight
                entry["because_of"].add(name)

        progress("Ranking recommendations...")
        ranked = sorted(candidates.values(), key=lambda c: c["score"], reverse=True)[:max_recommendations]
        top_score = ranked[0]["score"] if ranked else 1.0
        for r in ranked:
            r["because_of"] = sorted(r["because_of"])[:3]
            r["match_pct"] = round(min(100.0, (r["score"] / top_score) * 100)) if top_score else 0

        return {
            "library_artist_count": len(artists),
            "top_genres": top_genres,
            "seed_artists": seed_names,
            "used_play_history": has_real_play_data,
            "diagnostics": diagnostics,
            "recommendations": ranked,
        }
