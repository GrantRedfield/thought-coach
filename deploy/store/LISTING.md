# Chrome Web Store submission: Thought Coach

Everything to paste into the Chrome Web Store developer dashboard
(https://chrome.google.com/webstore/devconsole). Upload `thoughtcoach-<version>.zip` from this folder.

## Store listing tab

**Name** (from the manifest): Thought Coach

**Summary** (from the manifest, 121 characters):
Records your arrows, piece pickups and (optional) voice in your lichess games, then reviews your thinking after the game.

**Description:**

Thought Coach reviews how you think in your lichess games, not just the moves you played.

While you play, draw arrows for the moves you consider (yours and your opponent's) and, if you like, think out loud. Nothing is shown during the game. When lichess says the game is over, Thought Coach checks your thinking against the Stockfish chess engine and shows you:

• the threats you saw, and the ones you missed
• the good moves you never considered
• the moment the game turned, and who struck first
• a short story of your game, with one thing to focus on next time
• patterns across your games: where your win chance goes, and why

How to start: sign in with lichess at thoughtcoach.org/start, install the extension, and play. The setup page connects the extension to your account for you.

Fair play: the extension only reads the board. It shows nothing, adds nothing to the page and never touches your moves. A game is reviewed only after lichess says it is over, and only if you played in it.

Privacy: signing in only tells us your lichess username. Voice recordings are deleted once they are transcribed. No ads, no tracking, and you can delete your data at any time. Free and open source.

Not affiliated with lichess.org.

**Category:** Productivity → Education (or "Education" if offered at the top level)

**Language:** English

**Graphic assets:**
- Store icon 128×128: `extension/icon128.png`
- Screenshots 1280×800: `1-story.png`, `2-review.png`, `4-setup.png`
- Small promo tile 440×280: `promo-440x280.png`

**Official URL / homepage:** https://thoughtcoach.org
**Support URL:** https://thoughtcoach.org/start

## Privacy practices tab

**Single purpose:**
Record the thinking a player shows during their own lichess games (arrows drawn, pieces picked up and, optionally, their voice) and send it to Thought Coach after the game ends, so the game can be reviewed against a chess engine.

**Permission justifications:**
- `storage`: keeps the recording of the game in progress, the user's settings (voice on/off) and their Thought Coach account key.
- `unlimitedStorage`: a game's voice recording can exceed the default storage quota; it is kept only until the finished game has been sent.
- `alarms`: retries sending a finished game if the server could not be reached, and checks when its review is ready.
- `notifications`: tells the user when their game's review is ready.
- Host permission `https://thoughtcoach.org/*`: sends finished games to the Thought Coach service for review, and lets the setup page connect the extension to the user's account.
- Host permissions `http://127.0.0.1/*`, `http://localhost/*`: lets users who run the open-source Thought Coach server on their own computer send games to it instead.
- Content script on `https://lichess.org/*`: reads the board on lichess game pages to record the arrows the user draws and the pieces they pick up. It does not change the page.

**Remote code:** No, I am not using remote code.

**Data usage (tick these):**
- Personally identifiable information: **No** (the lichess username comes from signing in on the website, not from the extension).
- Authentication information: **Yes**: an account key that links the extension to the user's Thought Coach account.
- Website content: **Yes**: the board positions and the arrows the user draws in their own lichess games.
- User activity: **Yes**: when pieces are picked up and arrows drawn during the user's games.
- Personal communications: **Yes**: the user's voice while playing (optional), sent with the finished game for transcription; the audio is deleted once transcribed.
- Everything else: **No**.

Certify all three:
- I do not sell or transfer user data to third parties, outside of the approved use cases.
- I do not use or transfer user data for purposes that are unrelated to my item's single purpose.
- I do not use or transfer user data to determine creditworthiness or for lending purposes.

**Privacy policy URL:** https://thoughtcoach.org/privacy

## Distribution tab

- Visibility: **Public** (or **Unlisted** to test with friends first: only people with the link can find it).
- Regions: all regions.
