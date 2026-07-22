# Sidechain

A small self-hosted web app that looks at your [Navidrome](https://www.navidrome.org/)
library — what you actually play, what you've starred, your genre mix — and
recommends new artists that aren't in your collection yet.

It uses two free, keyless public datasets to do the matching:

- **[MusicBrainz](https://musicbrainz.org/)** — to resolve artist names to MBIDs
- **[ListenBrainz](https://listenbrainz.org/)** — for its public "similar artists" dataset

No accounts, API keys, or paid services required. Your Navidrome password
never leaves the server side — the browser only talks to this app's own
backend, which reads credentials from a local `.env` file.

## How it works

1. Pulls your artist list, most-played albums (`getAlbumList2?type=frequent`),
   and starred items from Navidrome via the Subsonic API.
2. Scores artists by play count + starred weight to find your top ~15
   "seed" artists.
3. Looks each seed up on MusicBrainz, then asks ListenBrainz for similar
   artists.
4. Aggregates the results, drops anything already in your library, and
   ranks what's left.

Lookups are cached to `data/lookup_cache.json` so re-running a scan later
is much faster and lighter on the public APIs.

## Setup

```bash
cp .env.example .env
# edit .env with your Navidrome URL, username, and password

pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then open `http://localhost:8000`.

### Docker

```bash
docker build -t sidechain .
docker run -p 8000:8000 --env-file .env -v sidechain-data:/app/data sidechain
```

## Notes & limitations

- MusicBrainz asks anonymous clients to stay near 1 request/second, so a
  first scan (looking up ~15 seed artists) typically takes 15-30 seconds.
  Later scans reuse the cache and are much faster.
- If an artist name doesn't resolve cleanly on MusicBrainz (unusual
  punctuation, "Various Artists," etc.) it's silently skipped rather than
  failing the whole scan.
- ListenBrainz's similar-artists dataset is community-listening-based, so
  very obscure or new artists may not have neighbors yet.
- This app only *reads* from Navidrome — it never writes, scrobbles, or
  modifies your library or ratings.

## Project layout

```
app/
  main.py             FastAPI app + job endpoints
  config.py           .env-based settings
  navidrome_client.py Subsonic API client
  recommend.py        MusicBrainz/ListenBrainz recommendation engine
static/
  index.html, style.css, app.js   the dashboard
```
