# Thought Coach on AWS

Scale to zero: the upload API, storage and the website are always on (pennies a month); the review
worker (Stockfish, Whisper, the speech tagger) starts when a game is uploaded and switches itself
off after 10 idle minutes. Nobody installs Stockfish or any model: only the browser extension.

Players sign in with lichess at `/start` (OAuth with PKCE: no app to register, we only learn the
username and give the token straight back). Each player gets their own upload key, which the
setup page hands to the extension; their reviews live under `/u/<lichess id>/`. Only the two
players of a game can send it, at most 30 reviews per player per day.

```
you       -> https://<site>/start                  sign in with lichess, install + connect the extension
extension -> https://<site>/api/uploads           one-time signed link (checks the upload key and
                                                   that lichess says the game is over)
          -> PUT straight to S3                    (recordings with voice are too big for one API call)
          -> https://<site>/api/recordings/<id>/uploaded
                                                   queue the review, wake the worker (0 -> 1)
worker    -> reviews, publishes /u/<user>/games/<id> (+ /story) and /u/<user>/, removes the audio, switches off
```

| Folder | What |
|---|---|
| `worker/` | the worker image (Dockerfile): Stockfish 19, Whisper small.en, Ollama with qwen2.5:3b, this code |
| `api/` | sign-in and upload API (Lambda) |
| `site/` | landing page, `/start` setup page; the extension zip is added at deploy time |
| `cdk/` | everything on AWS, as code (CDK, Python) |
| `.upload-key` | your upload key (never committed, never bundled): paste it into the extension |

## Deploy or update

From `deploy/cdk` (first time: `python -m venv .venv` and `.venv/Scripts/pip install -r requirements.txt`):

```bash
npx aws-cdk@2 deploy ThoughtCoach -c upload_key="$(cat ../.upload-key)" -c domain=thoughtcoach.org -c owner=lucidchess --outputs-file outputs.json
```

`owner`: the lichess account the shared upload key (`.upload-key`, for scripts) sends games as.
The upload key also signs the sign-in cookies: changing it signs everyone out. Sign-in needs
`-c domain=...` (lichess sends players back to that address).
Leave out `-c domain=...` to use the CloudFront address only. The domain needs its Route 53
hosted zone (created when the domain is registered in Route 53).

## Build the worker image (after any change to the code)

The image is built in AWS (CodeBuild), not on your PC:

```bash
aws codebuild start-build --project-name thoughtcoach-worker-build
```

It takes about 10-15 minutes; the next worker that starts uses the new image.

## Point the extension at it

Nothing to type: open `https://thoughtcoach.org/start`, sign in with lichess, and the page connects
the extension (it ticks each step off). The extension's default server is thoughtcoach.org; your
own PC's server is under "Advanced" in its popup.

## Costs and privacy

- Worker: c7i.xlarge, only while reviewing (a few cents per game). Max 1 worker for now
  (`max_capacity` in `cdk/app.py` to allow more at once).
- Audio is removed from the stored recording once the game is reviewed; the transcript is kept.
- Failed reviews go to the `FailedReviews` queue for 14 days.

## Chrome Web Store

`store/` holds the submission: `thoughtcoach-<version>.zip` (upload this), `LISTING.md` (every field
to paste: description, permission reasons, privacy answers) and the screenshots / promo tile.
After each extension change: bump `version` in `extension/manifest.json`, rebuild the zip, upload it
as a new version. Once the listing is approved, put its URL in `STORE_URL` in `site/start/index.html`
and redeploy: the setup page then shows one "Add to Chrome" button instead of Developer-mode steps.
