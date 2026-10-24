# ⚡ FlashBeat

A stream-first Telegram voice-chat music bot (Kurigram + py-tgcalls + yt-dlp). Written from scratch to fix the
main problem of older bots of this type: **waiting for a whole file to download before the first note plays.**

## What makes it fast

| Old approach | FlashBeat |
|---|---|
| download the whole song, then play | resolve a **direct stream URL** and play it immediately |
| one search provider, one at a time | providers are **raced**; a slower yt-dlp/SoundCloud search is hedged in only after 1.5 s |
| every request hits yt-dlp again | search results cached 15 min, stream URLs cached until their own `expire` stamp, concurrent identical lookups collapsed |
| next song starts cold | the **next track is pre-resolved** while the current one plays |
| database call on every action | all state lives in memory, written back in batches (Mongo **or** a local JSON file) |
| thumbnail blocks the reply | text card goes out instantly, the cover image replaces it a moment later |
| yt-dlp / TLS cold start | warm-up on boot (opens the connection, primes yt-dlp's JS solver) |
| stream dies → song skipped | **self-healing**: re-resolve, then download, and resume at the exact second |
| bots boot serially | bot, assistants and resolver boot in parallel |

## Features

`/play` `/vplay` `/playnext` `/playforce` `/search` (pick from 6 results) · playlists · SoundCloud, direct/radio/HLS links,
Telegram audio/video files · `/pause /resume /skip /stop /seek /seekback /seekto /loop (off/track/queue) /volume /shuffle /remove /move /clear /queue /np` ·
`/lyrics` (lrclib.net) · `/fav /favs /playfav` · `/history /top` · `/autoplay` (related songs forever) ·
`/settings` · `/auth /unauth` · sudo: `/stats /ping /active /broadcast /ban /unban /addsudo /rmsudo /logs /clearcache /restart`.
Controls are also inline buttons on the now-playing card; the requester can always skip their own song.
`/ping` shows measured search / resolve / play-start times so you can see the speed on your own server.

## Setup

1. Python 3.12, **ffmpeg** and **deno** installed (deno is needed by current yt-dlp for YouTube). Or just use the Dockerfile.
2. `pip install -r requirements.txt`
3. `cp sample.env .env` and fill it in (`SESSION` = assistant account session string).
4. Optional but recommended: put a YouTube `cookies.txt` in `cookies/`.
5. `python -m flashbeat` (or `./start`).

Add the bot to a group as admin (*Invite users via link*), start a voice chat, send `/play <song>`.

## Layout

```
flashbeat/core/      resolver (search+stream), player (state machine), engine (py-tgcalls), store, cache, queue, cards, lyrics
flashbeat/helpers/   ui (text + keyboards), flow (shared play flow), perms, filters, text utils
flashbeat/plugins/   start, play, controls, queue_cmds, library, admin, sudo, callbacks
tests/               offline tests (python3 tests/test_core.py, test_resolver.py, test_player.py, test_plugins_smoke.py)
```

## Honest notes

* The offline tests cover caching, racing/hedging, queue, store, the player state machine and the handler flow using
  fakes. **They cannot prove behaviour against live Telegram, YouTube or a real py-tgcalls build** - run it on your server and check `/ping`.
* The YouTube internal-search parser is undocumented API surface; that is exactly why it is raced against `py-yt-search`
  and yt-dlp. If YouTube changes it, search silently falls back.
* `/volume` and passing HTTP headers to ffmpeg depend on your py-tgcalls version; FlashBeat detects support and degrades gracefully.
* No `/eval` / shell commands are included on purpose.
* Fonts in `assets/fonts` are Inter and Raleway (SIL OFL, licences included).
