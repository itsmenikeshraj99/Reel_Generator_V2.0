# Music library

Background music for reels. **Drop a track into the folder that matches its mood and it is picked up
automatically** - there is nothing to register or configure. The worker scans this folder every time it
needs music, so new files work on the next reel.

```
worker/assets/music/
  calm/        slow, relaxed, thoughtful      (talks, lectures, explainers)
  upbeat/      energetic, positive            (vlogs, lifestyle, tips)
  serious/     dramatic, tense, weighty       (news, warnings, hard topics)
  inspiring/   uplifting, motivational        (stories, achievements)
```

The planner picks one mood word per reel (calm, upbeat, serious or inspiring); a track is chosen from
that folder. If the chosen folder is empty, the reel simply has no music (it never fails because of it).

## How to add a track
1. Pick a free-to-use track whose licence allows use in social videos (for example from the Pixabay music
   library). Check the licence yourself before you add it.
2. Copy the file into the right mood folder. Formats: `.mp3`, `.wav`, `.m4a`, `.ogg`, `.flac`.
3. **Recommended:** next to the track, add a small text file with the same name and `.txt`, so there is a
   record of where it came from (needed for the legal check before launch). For `Sunrise.mp3`:

   ```
   Sunrise.txt
   -----------
   Source:  Pixabay
   URL:     https://pixabay.com/music/...
   Licence: Pixabay Content Licence
   Artist:  (name)
   Added:   2026-10-17
   ```

## What makes a good track
- 60 to 180 seconds (longer is fine; the reel uses what it needs and fades out).
- **No vocals** - singing competes with the speaker. Instrumental only.
- Steady energy, no sudden loud parts (the music is automatically lowered under speech and the whole reel
  is loudness-normalised, but a calm track stays calmer).
- Keep file names simple (letters, numbers, spaces, dashes).

## Notes
- This folder ships inside the worker's Docker image (the whole `assets/` folder is copied), so tracks
  you add here are available to the deployed worker after the next build.
- Audio files are not git-ignored. A few tracks (3-5 MB each) are fine to commit; if the library grows
  large we will move it to cloud storage instead.
- The `.gitkeep` files only keep the empty folders in git; they can be deleted once a folder has a track.
