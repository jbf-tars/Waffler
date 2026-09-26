# Changelog

All notable changes to Waffler will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [3.14.100] - 2026-09-25

Two things the app claimed that were not true, and a dictation fix. Its
window fonts now ship inside the app, so opening Waffler no longer contacts
Google. The Mac build now declares the macOS version it really needs (14.0),
measured from the binaries inside it, instead of 10.13. And vocabulary
corrections now apply when Whisper hyphenates a mishearing. A change of mind
spoken across sentences ("Tuesday. No, wait, Wednesday. Actually,
Thursday") is still pasted word for word: a fix was tried and held back,
because every version of it made the clean-up model silently delete words.

On Windows the clean-up model was being sent a garbled copy of its
instructions; it now gets the same text as on a Mac. The Usage panel now
counts Groq's 10-second minimum per transcription and marks Cerebras costs
as estimates. And the setup wizard now describes Groq's free plan and
OpenAI's prepaid billing correctly.

The window got a round of fixes too. The status pill no longer sticks on
"Done", nothing animates while Waffler sits in the tray, the setup wizard's
messages and keycaps can be seen, hotkey and error messages say plainly what
happened, Settings shows what Waffler really uses, the Usage panel no longer
reads like a bill, and a Journal search with no matches says so.

And a dictation can no longer sit on "processing" for ever, or be lost. After
you let go of the hotkey the pill now stays up and shows it is working, with
the seconds counting and an X to cancel. If the wait passes 8 seconds it asks
whether to keep waiting (Esc then stops the wait and keeps the recording),
and every step has a deadline, so each dictation ends in a tick or a plain
message. A recording that could not be turned into text becomes a "Not sent"
card in the Journal with Try again, and Waffler sends it by itself once your
provider answers again. A request Waffler stopped waiting for is not paid for
twice: if it answers late, its words go into the card.

### Fixed
- **Every launch sent the user's IP address to Google, for fonts that never
  loaded.** Since 3.14.20, `ui/style.css` pulled Inter and Source Serif 4 with
  an `@import` from fonts.googleapis.com. That broke Waffler's rule that the
  only network calls are your chosen AI provider (on your own key) and the
  GitHub update check. It was also malformed: `opsz,wght@8..60,400;500;...`
  gives single values where the two-axis syntax needs pairs, so Google
  answered **HTTP 400** and neither font ever loaded. The wordmark has been
  drawn in the system UI font (Segoe UI on Windows) and the Journal's serif
  text in Georgia ever since, while the request still went out on every
  launch. Verified by reading the platform font Chrome actually rendered
  with (DevTools `getPlatformFontsForNode`), not the CSS it asked for.
- **Fix:** Inter and Source Serif 4 are bundled in `ui/fonts/` as variable
  WOFF2 files (latin and latin-ext, the same subsets and unicode ranges Google
  serves) and loaded with local `@font-face` rules. Weights are declared as
  400 to 800, the range the old import requested. Source Serif 4 includes its
  optical-size axis (8 to 60) and a true italic, because the Journal's date
  dividers and entry timestamps are set in serif italic and would otherwise be
  a slanted fake. Both families are SIL Open Font License 1.1; the licence
  texts and provenance are in `ui/fonts/`. About 600 KB added.

- **The Mac app claimed to run on macOS 10.13 but needs macOS 14.** A scan of
  every Mach-O binary in the shipped v3.14.99 bundle (105 of them, all Apple
  Silicon) found 92 built for macOS 11.0 and 13 built for macOS 14.0: all of
  NumPy. The macOS 14 build runner installs NumPy 2.4.6's `macosx_14_0_arm64`
  wheel, which links Apple's Accelerate framework and calls 23
  `$NEWLAPACK$ILP64` functions that older macOS releases do not have. NumPy is
  imported at start-up (`src/audio.py`, via `app.py`), so on an older Mac the
  app failed to open rather than macOS saying it needs a newer version.
  `LSMinimumSystemVersion` in `Waffler_mac.spec` is now `14.0.0`, so macOS
  refuses it cleanly with its standard message instead.
- **Vocabulary corrections silently failed when Whisper hyphenated a
  mishearing.** Running a real recording of "check the Postgres migration"
  through Waffler, Whisper wrote "post-grass". The corrector's two-word pass
  found the match ("post grass" -> Postgres), but the replacement searched for
  the space-separated phrase, so the correction was found and then never
  applied; "Nash-can" -> Ashkan failed the same way. Multi-word corrections
  now accept a space or a hyphen between the words. Four new checks in
  `tests/test_vocab_false_positives.py`, including one that ordinary
  hyphenated words ("well-known", "long-term") are left alone. Measured on the
  real recording: 0 of 5 runs produced "Postgres" before, 5 of 5 after.
- **Vocabulary names were written over ordinary phrases.** To catch a name
  that Whisper splits in two ("Ashkan" heard as "Nash can"), the corrector
  also joins each pair of neighbouring words and compares the join with
  your vocabulary. That check used a looser bar than single words and had no
  guard for everyday words, so with "Aidan" in the vocabulary "add an", "and
  an" and "said and" became Aidan (17 times in one person's real
  dictations), "Isobel" turned "is hotel" and "is model" into Isobel,
  "Clubcard" turned "colour card" and "blue card" into Clubcard, and
  "Sinéad" turned "the sign had" into "the Sinéad".
- **Fix:** a pair of words is joined into a name only when the join spells
  it exactly ("club card"), or when both words are at least three letters,
  the join is within one letter of the name's length, and: if both are
  everyday English words (a new list of about 4,100, `src/common_words.py`,
  built from public word-frequency and dictionary data, no personal data),
  the join differs from the name only in its vowels and doubled letters
  ("post grass" for Postgres); otherwise the old bar still applies but the
  consonant sounds may differ by at most one ("Nash can" for Ashkan). The
  single-word corrections are unchanged (identical results on 14,000 words
  against 40 names). Checked offline by joining every pair of the 3,000
  most common English words and comparing each join with 56 names and
  product words: the old rule rewrote 2,784 pairs, the new one 196, and
  nearly all of those involve word fragments nobody says on their own
  ("ver cell", "supp base"). Real phrases still joined include "power paint"
  (PowerPoint), "soup base" (Supabase), "fast time" (FaceTime) and "air Dan"
  (Aidan). With the 6,000 most common words it is 5,795 before and 773
  after; most of the rest pair a common word with one outside the list
  ("Meg hand" for Meghan, "clue card" for Clubcard), which is where "Nash
  can" sits too. `tests/test_vocab_bigram_join.py` (85 checks) runs every phrase
  above both ways: 22 ordinary phrases are left alone (the old rule rewrote
  all 22) and the split names "Nash can", "Nash-can", "club card", "post
  grass", "post-grass" and "Ash can" are still joined.
- **The release build now checks this.** `scripts/check_macos_minos.py` reads
  the minimum macOS of every binary in the built `Waffler.app` (including each
  slice of a universal binary) and fails the macOS release if any needs a
  newer version than the bundle declares. It runs straight after PyInstaller,
  before signing, so a bad build fails in about a minute. A dependency upgrade
  can raise the real minimum without anyone noticing; this is what caught
  NumPy. On the real v3.14.99 bundle it fails at 10.13.0 and passes at 14.0.0.
- **On Windows the clean-up model was sent a garbled prompt.**
  `OpenAIStyler` opened `prompts/normal.txt` with a bare `open(path, 'r')`.
  With no encoding, Python uses the system's: UTF-8 on a Mac, cp1252 on
  Windows. The prompt is UTF-8, so on Windows each of its 12 em-dashes
  reached the model as "â€”", and its 6 arrows, "≥", 4 "…", "€" and "£"
  were garbled the same way, while a Mac sent the real text. The two
  platforms have been running different instructions. The same bare calls
  read `settings.json`, `vocab.json`, `snippets.json`,
  `setup_complete.json`, `config.yaml` and the `.env` key file, and three
  of the writers to `app.log` used them too, so on Windows a log line with a
  character cp1252 has no code for (the overlay's "✓ Subprocess started")
  was silently dropped.
- **Fix:** every text-mode `open()`, `read_text()` and `write_text()` in
  `src/` and `app.py` now names its encoding. Writes are UTF-8. Files a
  person might edit by hand (`settings.json`, `vocab.json`, `snippets.json`,
  `config.json`, `config.yaml`, `.env`) are read as `utf-8-sig`, so a
  byte-order mark added by an editor such as Notepad is ignored. Before,
  `json.loads` rejected the mark and the loaders swallowed the error, so the
  words or settings silently disappeared. python-dotenv now reads `.env` the
  same way: a mark there hid the first key, and the app asked for a key it
  already had. The overlay's pipes are now decoded as UTF-8 to match the
  overlay process, which already writes UTF-8. Windows decoded them as
  cp1252, which garbled non-ASCII lines in `app.log`, and a byte cp1252
  cannot map would have stopped the thread reading them. Files written by
  earlier versions still read correctly: the app wrote its JSON as plain
  ASCII (or already as UTF-8), and keys are ASCII.
- **The Usage panel under-counted short Groq dictations.** Groq bills every
  speech-to-text request as at least 10 seconds of audio ("Minimum Billed
  Length: 10 seconds", console.groq.com/docs/speech-to-text), but the panel
  priced each clip at its real length, so a 3-second dictation was counted
  at under a third of what Groq charges for it. The sums are small (10
  seconds on Groq is $0.0003), but they were wrong.
- **Fix:** the Groq transcription rate now records the 10-second minimum and
  its source, and the cost uses it. The stored `duration_seconds` stays the
  real length. OpenAI documents no minimum billed length for
  gpt-4o-mini-transcribe (its pricing page, checked 2026-09-25), so none is
  applied there. The pricing arithmetic is now one function, `_usage_cost`
  in `app.py`, and `scripts/recost_usage.py` uses it too, so recomputing
  history applies the same rule instead of quietly undoing it.
- **Cerebras costs were never shown as estimates.** Since 3.14.95 Cerebras
  has been priced at the same model's Groq rate and flagged unverified,
  because Cerebras publishes no per-token price, and the rate table said the
  Usage panel would label it. Nothing read the flag. The panel's "By
  provider" list now shows an "estimate" tag, and a "~" before the cost, for
  any provider with calls priced at an unpublished rate. Cerebras entries
  from before the flag existed count as estimates too.

- **With only a Groq key, one network blip could lose every dictation for
  up to an hour.** Both speech clients run with no automatic retries, and any
  Groq failure paused Groq for 30 seconds (an hour for anything that looked
  like a permissions error, which a VPN often causes). With no OpenAI key,
  which is the setup the wizard recommends, every dictation in that window
  failed with "no transcription backend available" and was saved as audio
  instead. Real use logged 7 Groq connection errors and 5 lost dictations.
- **Fix:** when Groq is the only speech provider it is never paused. The last
  provider left to try is retried up to twice on a timeout, a server error or
  a dropped connection, after a short randomised wait (about 0.6 and then 1.2
  seconds), and only while the time already spent stays under 20 seconds, so
  a request that has already waited out its timeout is not repeated. With an
  OpenAI key as well, a Groq failure still moves straight to OpenAI without
  waiting, and the pause after a permissions error is now 60 seconds instead
  of an hour. `tests/test_groq_only_reliability.py` (30 checks, no network)
  covers a 500 then a success, a retried connection error, Groq never skipped
  when alone, the retry limit and time budget, and the error classes.
- **A locked file could lose a dictation, and cancelling wiped the
  clipboard.** On Windows, saving `usage.json` or `history.json` fails with
  "Access is denied" while anything else has the file open for a moment (the
  app's own stats panel, antivirus, search indexing). The usage save runs
  before the paste, so the dictation stopped with "Something went wrong" and
  was never pasted: 6 real dictations were lost this way. A history save
  failing after the paste then copied the raw transcript over the tidied
  text the user had just pasted. And cancelling a recording cleared the
  clipboard, although Waffler had put nothing there yet (23 times in one
  log).
- **Fix:** the save is retried for up to about three quarters of a second
  while the file is locked (`src/atomic_json.py`). If it still fails, the
  dictation carries on: a usage record is skipped and logged, and a History
  entry that cannot be saved gets a "Not saved to History" notice while the
  text is still pasted. The error handler no longer copies the raw transcript
  once the tidied text is on the clipboard, and cancelling leaves the
  clipboard alone. `tests/test_bookkeeping_never_fails_dictation.py` (12
  checks) runs `app.py`'s own save and cancel code with a file that stays
  locked and with a fake clipboard.
- **Waffler reserved about 1.7 GB of memory it never used.** NumPy's maths
  library starts one idle worker thread per processor core as soon as it is
  loaded, and reserves memory for each. Waffler only uses NumPy to measure
  how loud the microphone is, which never needs those threads. The installed
  app held about 878 MB (main window) and 811 MB (recording pill) of private
  memory, almost all of it this pool. The recording pill paid it too,
  because `app.py` loaded the whole app, including the AI provider
  libraries, before noticing it had been started as the pill.
- **Fix:** the thread pools are capped at one thread before anything loads
  NumPy, by a new start-up hook registered in both the Windows and Mac builds
  (`hooks/rthook_thread_caps.py`; the Windows build registered no hook at
  all) and at the top of `app.py` for runs from source. Measured on this PC,
  loading NumPy went from 754 MB and 27 threads to 15 MB and 4. The
  recording pill now starts before the main app's libraries load, so it
  skips them entirely. The saving in the installed app, and the pill's
  faster start, are estimates until measured on a new build.
  `tests/test_startup_memory.py` (8 checks) checks the hook, that both
  builds register it, and that `app.py` sets the caps and starts the pill
  before any heavy import.
- **A `.env` file elsewhere on the computer could replace your keys.** After
  reading Waffler's own key file, `src/config.py` also called
  `load_dotenv(override=True)` with no path, which searches upward from the
  app's folder and, in an installed app, through your home folder. A
  developer's `~/.env` holding `GROQ_API_KEY` or `OPENAI_API_KEY` silently
  won over the key entered in Waffler. Now only the repo's own `.env` is read
  as a fallback, by exact path, and it can only fill a key nothing else set.
  `tests/test_env_precedence.py` (4 checks) loads the config from a folder
  with an unrelated `.env` above it.
- **The window flashed dark on every launch, and "System" was dark on a
  light computer.** The window was always created with the dark theme's
  background, although Cream is the default. It now takes the saved theme's
  colour; the theme is kept in `settings.json` as well as the page's own
  storage, because the window is painted before the page loads. The System
  theme had no light version, so on a light computer it fell back to the
  dark colours; it now follows the computer's setting, including when that
  changes while Waffler is open.
- **A second copy of Waffler did start-up work before closing.** It wrote a
  start-up line to the log, checked for a VPN, and could use up the note
  about a pending update meant for the copy already running. The
  one-copy-only check is now the first thing Waffler does.
- **Packaging:** UPX compression is off in both builds (packed programs are
  a common cause of false antivirus alarms, and Waffler has had one), and
  the Windows installer now removes uninstallers left behind by older
  installs. One real install folder held two, with only the newer one
  registered. `tests/test_startup_config.py` (20 checks) covers these and
  the theme colours.
- **The status pill in the top bar broke on the first dictation.** Every
  status update replaced the pill's classes, which dropped its styling, and
  then failed on a recording overlay that no longer exists. So the pill lost
  its colours while recording, and after the first dictation it stuck on
  "Done". It now changes only its state, uses one set of labels (Ready,
  Recording, Cleaning up), shows its colours in the dark theme too, and
  goes back to Ready 3 seconds after Done. The idle label "Waiting for
  activation" is now "Ready".
- **Waffler kept drawing about 60 frames a second while it sat in the
  tray.** Idle, it used about 11% of one processor core, almost all of it in
  the WebView2 graphics process, which was animating the status dot (pulsing
  even at Ready) and the streak logo (wobbling for ever, with a shadow). The
  dot now pulses only while recording or cleaning up, and the logo wobbles
  once when the Journal opens. Every animation pauses while the window is
  hidden, minimised or in the tray: `app.py` now tells the page when the
  window hides, minimises and comes back, because a minimised WebView2
  window can still count as visible. One rule honours the computer's
  "reduce motion" setting, and the setup's animated waffle stops once setup
  is over (it used to run for the rest of the session). In headless Chrome
  the idle Journal now draws no frames in 5 seconds, against 300 before, and
  has no running animations. The installed app's processor use has not been
  measured again yet.
- **The setup wizard could strand people, and hid its own messages.**
  - Every message shown during setup sat behind the wizard, so none was
    ever seen. Messages now show above it, and above the hotkey dialog, at
    the top centre, clear of its Back, Next and Finish Setup buttons (bottom
    right, where messages sit elsewhere, the one after Send in Try it covered
    Finish Setup, and the first click only closed it).
  - The Windows logo, the "Win" label and the Mac's fn globe were drawn in
    near-black on the black keycaps, and the Hotkey and Try-it steps then
    replaced each keycap with plain dark text, so the keys looked blank.
    Keycaps, the tiles under them and the Try-it chips now have light
    labels and are drawn from the saved hotkey.
  - A disabled "Finish Setup" kept its bright green and looked like the
    main button. It now looks disabled, and a quiet "Skip for now" finishes
    setup when the test dictation won't work, so a microphone problem no
    longer leaves anyone stuck on the last step.
  - Back after the hotkey step moved on by itself showed a stale "Hotkey
    detected, advancing" that never advanced. The step now starts afresh.
  - With no key saved, the Try-it step said "Complete Step 1 first", which
    is the wrong step. It now says "Go back a step and add your key.", and a
    test recording that can't start says so in a sentence instead of
    showing the error text.
  - On a Mac the two permission cards were stacked about two screens tall
    instead of side by side, because each step was shown with a style that
    overrode the grid.
- **A Journal search with no matches said "Your journal is empty."**, as if
  the history had gone. It now says 'No entries match "..."', with a "Clear
  search" button that shows every entry again. Search also waits for a
  150 ms pause in typing instead of rebuilding every entry on every key: in
  headless Chrome with 3,290 entries, typing "invoice" rebuilt the list once
  instead of 7 times, the slowest key took 14 ms instead of 539 ms, and the
  word cost 99 ms of work instead of 1,061 ms.
- **A dictation could sit on "processing" for ever, with nothing to show it
  was working and no way to stop it.** Letting go of the hotkey hid the pill
  at once, and the "Transcribing" label was then drawn on the hidden pill.
  Nothing limited the wait as a whole: one speech request could run for
  240 seconds, a fallback and a retry could chain three of them, and the
  longest wait seen from release to paste was 103 seconds. Esc only worked
  while recording. A paste keystroke or a clipboard that never returned held
  the dictation, and the History save after it, for good.
- **Fix:** one watchdog now looks after every dictation from release to the
  end (`src/pipeline_watchdog.py`):
  - The pill stays on screen in a working look: the cells ripple slowly, the
    seconds count on the waffle, and one X in the middle cancels. It ends
    with a short tick, or quietly when a message is showing. Windows and Mac
    draw the same thing.
  - Stopping the recording, speech to text, the clean-up, the copy and the
    paste each run with a deadline, and an error on any of them becomes a
    plain message instead of a stuck thread. Speech to text as a whole gets
    45 seconds for an ordinary dictation (more for long recordings, up to
    5.5 minutes for a 12-minute one), then the recording is kept as Not
    sent. A clean-up that does not finish is pasted as you said it. A paste
    that does not finish leaves the text on the clipboard and in the Journal
    and says "Press Ctrl+V to paste it" (Cmd+V on a Mac).
  - Stopping the recording gets 15 seconds, and the recorder can take longer
    while it rebuilds a device after a microphone change. The pill then says
    "Your mic was slow to stop" and the dictation ends, and the recording is
    kept as Not sent once it comes through, and sent from there, instead of
    being lost. One with no speech in it is not kept.
  - After 8 seconds (or 0.4 times the recording's length, if longer) the
    pill asks: "Keep waiting", "Send later" or "Cancel" while it waits for
    speech to text, and "Paste as is", "Keep waiting" or "Cancel" while it
    waits for the clean-up. This replaces the "Taking longer than usual"
    message, which offered nothing to do.
  - The pill's X and the offer's Cancel cancel the dictation being
    processed, on Windows and on a Mac, until the paste starts. Esc stops
    the wait only while that offer is on screen, and keeps what exists: the
    recording becomes a Not sent card marked cancelled, which only Try again
    sends, or during the clean-up the words go to the Journal as you said
    them. Nothing is pasted either way. Esc also reaches the app in front,
    as it always has, so straight after letting go it is left to that app: a
    reflex Esc (closing the emoji picker a Mac's Fn key can open, the Start
    menu, an autocomplete list) never touches the dictation.
  - If something outside those steps stops making progress, the watchdog
    gives up on the dictation, keeps the recording as Not sent if no words
    exist yet, frees the pill and the window, and never pastes later into
    whatever you are doing by then.
  - The window's status pill counts too ("Cleaning up · 4 s") and ends with
    Done, Cancelled, Not sent or "Something went wrong", then Ready. The tray
    tooltip (Windows) and menu bar tooltip (Mac) say when Waffler is
    recording or working; while working the Windows tray icon gets an amber
    dot and the Mac menu bar icon dims.
- **Letting go of the hotkey could wait on the window before processing
  started.** The release told the window "processing" first, and on a Mac
  that call waits for the window with no time limit, so a window that did
  not answer stopped the dictation outright, with the pill frozen. Every
  update to the window now goes through one queue that one thread sends,
  and a dictation never waits for it. On Windows the audit measured about
  0.44 seconds between release and the start of processing, most of it this
  call; that has not been measured again in the installed app yet.
- **Recordings that were not sent could not be sent again.** When speech to
  text failed, the recording was saved and a card said it was "saved so you
  can retry", but nothing in the app could reach the file (one user had five
  waiting since June). **Fix:** it is now a "Not sent" card in the Journal
  that says what happened in plain words, with Try again, Show the file and
  Delete (which asks first). Try again sends the saved recording with your
  own keys, puts the words into the card and keeps its place in the
  Journal; nothing is pasted. Waffler also sends waiting recordings by
  itself: right after a dictation goes through, and every 30 seconds while
  any wait, for recordings from the last day, at most three times each,
  stopping at the first one your provider still refuses. It only ever sends
  recordings a Journal card names, never any other file in that folder.
  Settings (Data) shows how many are waiting, with "Send now". A speech
  request Waffler stopped waiting for (after "Send later" or the deadline)
  keeps running and is usually billed, so its answer is not thrown away: the
  words go into the card, and while it runs nothing sends that recording
  again (Try again waits for its answer instead), so no recording is paid
  for twice. If that request fails the card is sent again as usual, and if
  it heard nothing the card says so and is not retried. A late answer that
  arrives during a dictation goes in as you said it rather than compete for
  the clean-up.
- **Pressing the hotkey again while a dictation was still being cleaned up
  threw away its finished words.** An early return treated the newer
  recording like a cancel. The older dictation now goes to the Journal (it
  is not pasted, because the newer one owns the window and the clipboard,
  and it no longer writes the clipboard either), and it no longer resets
  the newer recording's "Recording" label.
- **Two recordings that failed in the same second shared one file name**,
  so the second overwrote the first. Names are now unique.
- **A Not sent card added about 20 words to the word counts**: its note was
  counted as if you had said it. It now adds none.
- **"Something went wrong" said your text had been copied when there was
  no text.** It now says "That dictation didn't go through. Please try
  again." unless your words really are on the clipboard.
- **A clean-up that ran out of time was reported as "Connection failed".**
  It now says "Pasted without the clean-up: the clean-up took too long, so
  your words went in as you said them." whether the clean-up ran out of its
  own 30-second budget or reached Waffler's backstop deadline.
- **Clicking the pill's stop button could send the paste to the wrong
  place.** A click on the pill made Waffler's overlay the active app, so the
  Ctrl+V (Cmd+V on a Mac) that followed went to the overlay instead of the
  app you were typing in, and the words were only on the clipboard and in
  the Journal. On Windows the app could not take the focus back, because
  Windows only lets the process that got the click move it. The offer's
  "Keep waiting" and "Paste as is" come just before a paste too. The pill
  and its messages now take clicks without taking the focus: on Windows they
  are marked not to activate, and on a Mac they are non-activating panels
  that never take the keyboard. Found by reading the code; clicking through
  it on a real Windows PC and a real Mac is still to do.
- **The "We couldn't hear you" message closed whatever message came after
  it.** Its 4-second clean-up now closes only itself.

### Changed
- **Error messages are plain sentences instead of raw error text.** Checking
  a key while offline used to show "Connection error:
  HTTPSConnectionPool(host='api.groq.com', ...)", a VPN block said the key
  "may be expired or revoked", the update check said "GitHub API returned
  HTTP 403", a failed download showed the download tool's output, and an
  update with no installer for this computer said "Refusing to download
  from an untrusted URL." Key checks now say, for example, "Couldn't reach
  Groq. Check you're online. If you use a VPN, turn it off and try again.",
  "Groq didn't accept that key. Make a new one and click Copy again.", "Groq
  blocked this connection. This usually means a VPN is on. Turn it off and
  try again." or "Groq is busy for a moment. Try again in a few seconds.",
  and an OpenAI key with no credit says so. The update check says
  "Couldn't check for updates. Try again later." A failed download or
  install says so in one sentence and points to the download page, and a
  release with no installer for this computer is flagged so the app can open
  its page instead. The underlying error still goes to the log. The
  sentences live in one place, `src/user_messages.py`;
  `tests/test_plain_error_messages.py` (53 checks) runs the key checks, the
  update check and the download with the providers' own error types and no
  network.
- **The pill's messages after a limit or a failed clean-up are plain too.**
  They said "Groq limit hit · resets in about 16 minutes" over "Pasted raw",
  followed by "Add a Cerebras key for fallback" (Cerebras is the optional
  third choice), "Rate limit reached" with "Add another provider key in
  Settings → API Keys for instant fallback", and "Auth blocked" with "provider
  blocked the request... Try another provider key". Now a limit reads
  "Clean-up paused for about 17 minutes: Groq says you've reached your limit
  for now. Your words were pasted as you said them." (the time is Groq's own
  wait, rounded up). The others say "Pasted without the clean-up" and why in
  one sentence: Groq refused the connection, which usually means a VPN is on
  or the key has stopped working; Waffler couldn't reach Groq; the clean-up
  took too long; or no key is set up for it yet. A limit that stops a
  dictation says "Limit reached: Groq says you've reached your limit for now.
  Try again in about 17 minutes." None of them sends you off to add a
  second provider's key. The "Mic reset" message lost its dash as well.
- **Saving a hotkey now says plainly whether it worked.** Settings offered
  the Mac hotkeys (Fn, Command + Shift, Option + Shift) on Windows too, and
  the setup wizard offered Ctrl + Alt + Space, which Windows never accepted.
  The app refused them with "Unknown key: fn", which no screen showed, so the
  hotkey looked changed when it was not. The rules now live in
  `src/hotkey_rules.py` and answer per platform: every Windows choice (Win +
  Ctrl, Ctrl + Shift, a custom combination) saves, and the answer includes
  the keys actually saved so the screen can redraw from them. A Mac key on
  Windows gets "Fn is a Mac key, so Windows can't use it for the hotkey.
  Choose Win + Ctrl or Ctrl + Shift instead.", and Space gets a sentence
  explaining that it switches on hands-free mode. On Windows the keys are
  always named in one order, so the default reads "Win + Ctrl" everywhere,
  as on the website, whichever key was pressed first. On a Mac the same
  keys pass and fail as before, and the answer now names them in words
  ("Command + Shift") like the rest of the app. `tests/test_hotkey_save.py`
  (30 checks, one more on each platform against its own key table).
- **Settings and the setup wizard offer only this computer's hotkeys, and
  show the answer.** Windows now gets Win + Ctrl, Ctrl + Shift and Custom
  (which opens the existing key-recording dialog; nothing opened it before),
  and a Mac gets Fn, Command + Shift and Option + Shift. Both screens check
  whether the save worked: a refused hotkey shows the app's sentence and
  changes nothing, where Settings used to flash green and the wizard said
  the hotkey had changed. After a save, the top bar, Settings and the
  wizard's keycaps are redrawn from the keys actually saved. The hotkey is
  called "Win + Ctrl" everywhere, as on the website; "press Ctrl first,
  then Win" is now a tip under it rather than a different name.
- **The window shows the plain messages instead of error text.** The update
  dialog shows the app's sentences as they are ("Couldn't check for
  updates" / "Try again later." rather than "GitHub API returned HTTP 403").
  A failed download or install is one sentence with an "Open download page"
  button (it said "Download in browser"). An install the app refuses no
  longer leaves "Installing..." on screen, and a release with no installer
  for this computer opens that release's page instead of trying a download
  that failed as an "untrusted URL". Saving a key, clearing History,
  resetting usage, saving logs, opening System Settings and finishing setup
  no longer show the raw error (often "Error: " and whatever the call
  raised); they say what failed in plain words and keep the detail for the
  log. The setup's OpenAI
  tab says "Backup if Groq is busy" instead of "Last-resort fallback".
- **Settings now says what Waffler really uses.**
  - Keys are listed Groq, OpenAI, then Cerebras (optional). Cerebras, which
    can't do speech to text, was listed above the recommended Groq.
  - Provider Order greys out a provider with no key ("No key yet, so
    Waffler skips it"). It can still be moved.
  - With no order saved, Settings showed Groq, Cerebras, OpenAI while the
    app ran Groq, OpenAI, Cerebras. The screen and the app now start from
    the same list, and a test keeps them equal.
  - "Active Backends: STT: Groq Whisper · LLM: Groq gpt-oss-120b" is now
    "In use: Speech to text: Groq · Clean-up: Groq", naming the first
    provider in your order that has a key for each step. Any Cerebras key
    used to make it say Cerebras for clean-up, even with Groq first.
  - About said "Powered by Groq + Whisper + LLaMA" for everyone. It now
    names the models in use ("Powered by Whisper large v3 and
    gpt-oss-120b"); clean-up hasn't used LLaMA since Groq retired it.
  - The "Normal" menu in the top bar is gone. Normal was its only real
    choice, so it took space and did nothing.
- **The Usage panel no longer reads like a bill.** It opened with four
  dollar figures ("$0.06 all time"), which people on Groq's free plan took
  as a charge. It now shows how many dictations and words first, then
  "Estimated cost" with the line "Estimated at each provider's published
  paid rates. Waffler can't see your bill.", and the per-provider list is
  marked "estimated, all time".
- **The app now looks the way 3.14.20 intended.** Because the fonts finally
  load, the "Waffler" wordmark is Inter and the Journal text, date dividers
  and timestamps are Source Serif 4. This is a visible change from the
  system and Georgia fallbacks people have been seeing.
- **The setup wizard now says what Groq's free plan and OpenAI's billing
  really are.** The Groq tab said "100k tokens/day free" and the intro said
  the free tier "covers most people without ever paying". Groq's free plan
  allows 200,000 tokens a day and 8,000 a minute for openai/gpt-oss-120b
  (console.groq.com/docs/rate-limits), and a clean-up uses about 5,800
  tokens: about 30 clean-ups a day, and roughly one a minute. The wizard now
  says "about 30". The OpenAI steps said "Credit card needed" and
  "Pay-as-you-go", but OpenAI's API is prepaid: you buy credit first, at
  least $5, and unused credit expires after a year. The cost line now says
  dictations cost nothing within Groq's daily and per-minute limits, and
  gives about a sixth of a cent as Groq's paid-plan figure (on OpenAI a
  typical dictation is nearer 0.4 cents). The "No styling provider" notice,
  which also said "100k tokens/day", now matches.

### Note
- **Not fixed by this release: a change of mind spoken across sentences is
  still pasted as spoken.** Saying "Let's meet on Tuesday. No, wait,
  Wednesday. Actually, Thursday at two." pastes exactly those words, wrong
  days and all. Whisper writes each attempt as its own sentence, and the
  clean-up model (Groq gpt-oss-120b) deletes the lead-in along with the
  wrong days. The safety check that stops the model silently dropping
  content sees only 3 of 11 words survive, refuses that answer, and pastes
  the transcript instead. Measured with the prompt read as UTF-8, as every
  platform now reads it: 0 of 5 runs corrected, and no word lost.
- **Why the fix was held back.** Four rewrites of the SELF-CORRECTION rules
  in `prompts/normal.txt` were measured with
  `scripts/test_self_correction_corpus.py` (Groq gpt-oss-120b, 5 runs of
  each of 45 cases). The best one corrected that sentence in 5 of 5 runs and
  raised the nine lead-in cases from 21 of 45 runs to 27. But every one of
  them also deleted words that the current prompt kept in all 5 of its runs,
  and the safety check let those answers through with no warning, because
  enough of the words survived or the sentence was too short to check. The
  best one dropped "then" from "We could do the review on Tuesday at noon
  then. Actually, Thursday works too." in 4 of 10 runs, and the "no" or "no,
  wait" from "but no, wait until you see this" in 3 of 10. The others turned
  "No, wait until you see the numbers." into "Wait until you see the
  numbers." (8 of 10), dropped "really" from "I actually really enjoyed
  working on this project" (up to 5 of 10), and deleted Tuesday from "Let's
  meet on Tuesday to go through the numbers. Actually, Thursday works too if
  you're busy." (2 of 10). The version first committed for this fix pasted
  an answer with a word missing in 17 of 225 runs and with broken grammar or
  a stray comma in 15, against 15 and 3 for the current prompt. In total the
  best rewrite damaged slightly fewer runs than the current prompt (13 with a
  word missing and 2 garbled, against 15 and 3), so this is not a case of the
  old rules being clean. The difference is where the damage lands: most of
  the current prompt's damage is in sentences that contain a spoken
  correction, while the rewrites added new losses to ordinary sentences with
  no correction in them ("Actually, Thursday works too", "then", "No, wait
  until..."), which people say every day. Uncorrected words lose nothing,
  while a sentence with a word silently missing can say something the speaker
  did not, so the rules stay as they are in 3.14.99.
  Each loss above was seen in at least 2 of 10 runs against 0 of 5 for the
  current prompt, which is weak evidence one case at a time (the current
  prompt may drop "really" too, at a rate 5 runs did not show), but every
  candidate had at least one loss that came back when its case was run 5
  more times. The "no, wait" comparison holds only for the dash form ("but
  no, wait until you see this" set off by dashes); with commas instead, the
  current prompt and the best rewrite both drop words in 4 of 5 runs.
- **Known limits of the rules this release ships**, measured the same way:
  159 of 225 runs pass, 21 of 45 on the nine lead-in cases. In five cases
  the pasted answer has a word missing (15 runs) or broken grammar (3). A
  correction inside a spoken numbered list ("book the room for Tuesday, no
  Monday") comes out as "Book the room for Tuesday." in 5 of 5 runs. "but
  no, wait until you see this, it took six" loses the "no", or the whole
  aside, in 4 of 5. "Let's meet on Tuesday. Actually, scratch that, let's
  just do a quick call." becomes "Let's meet on Tuesday. Let's just do a
  quick call." in 4 of 5. "Let's meet on Tuesday, actually Thursday works
  too." becomes "Let's meet on Thursday works too." in 2 of 5. And "Can we
  shift it to Tuesday, actually Monday works better." becomes "Can we shift
  it to Monday works better." in 1 of 5. Eight-word corrections split by a
  full stop ("Can you send it on Monday. Sorry, Tuesday.") never reach the
  model, because the short-input shortcut does not recognise the marker, so
  they are pasted as spoken. `scripts/auto_test_corpus.py` passes 106 of
  107; the one failure is a correction inside a spoken numbered list, left
  as spoken. These are the rules 3.14.99 has. On Windows it sent them
  garbled (see Fixed), so these figures are what both platforms now get.
  One shape changes on Windows because of that fix: an aside like "We
  shipped it on Monday, no, wait until you hear this, with zero bugs" set
  off by dashes right after a day or time now loses the aside (about 8 of 10
  runs), as it already did on a Mac; Windows 3.14.99 kept it in about 5 of 7.

### Verified
- With every non-local request blocked, the UI renders with zero network
  requests, both themes use the bundled faces (wordmark: Inter, serif:
  Source Serif 4), latin-ext text such as "Łukasz" renders entirely in the
  bundled font, and there are no console errors.
- PyInstaller's own datas expansion of the existing `('ui', 'ui')` entry in
  `Waffler_windows.spec` and `Waffler_mac.spec` includes all nine files in
  `ui/fonts/`, so both builds ship them without spec changes.
- Version ordering: the updater compares integer tuples, so 3.14.100 is
  correctly newer than 3.14.99.
- `tests/test_macos_minimum_version.py` (12 checks) covers the Mach-O parsing
  with synthetic binaries (modern and legacy version records, universal
  binaries, Java class files that share the universal magic number), the
  pass and fail paths on a fake bundle, and that the release workflow runs
  the check after PyInstaller and before signing.
- `tests/test_ui_no_remote_assets.py` (7 checks) fails if any UI stylesheet
  or page loads a remote font, script, style or image on start-up, if a
  bundled font the CSS references is missing, if the true italic or a
  licence is dropped, or if either spec stops bundling `ui/`. Four of its
  checks fail against the old stylesheet.
- `tests/test_truncation_guard_self_correction.py` (82 checks, no network
  or keys) pins the safety check on the cross-sentence correction: "Let's
  meet on Thursday at two." is pasted unchanged, "Thursday at two." is
  refused with every spoken word kept, an answer cut off by the token limit
  and a long dictation cut below half are still caught, and inputs under 8
  words are never checked. It records the check's blind spot: 20 answers
  seen in these measurements that lost or garbled words (Tuesday deleted,
  "no, wait" dropped, "James, by") are pasted unchanged. It checks that the
  live harness scores each of them as LOSS or GARBLE and the right answers
  as clean, and that it reads a candidate prompt as UTF-8 where the default
  is cp1252. And it fails if the SELF-CORRECTION section changes from the
  measured text (the test names the measurement to run first), loses its
  worked examples or its list of what is not a correction, or brings back
  the wording the rejected versions used to teach a correction across a
  full stop. Each of the four rejected versions fails 3 to 6 of those
  checks.
- `scripts/test_self_correction_corpus.py` gains 14 lead-in and
  negative-control cases, and can run each case several times, against a
  candidate prompt, recording the model's answer before the safety check.
  `scripts/auto_test_corpus.py` gains `--prompt-file` and `--json`.
- `scripts/test_self_correction_corpus.py` now counts silent damage
  separately, because it had been passing broken answers: "Can we shift it
  to Monday works better.", "Send it to James, Wednesday at three." (the
  "by" gone) and "...but until you see this, it took six." (the "no, wait"
  gone). A run now also fails as LOSS when a word of the transcript is
  missing that is neither a filler nor a value the speaker took back, or
  when every correction marker is deleted but the value it took back stays
  ("The quote was £500, £750 for the whole package." reads as if both
  stand). It fails as GARBLE on a stray comma ("the, updated", "to James,
  by") or a lead-in glued onto a replacement that brings its own verb. The
  table, totals and JSON count those runs on the pasted text and on the
  model's answer. Seven cases are added from a review of the held-back fix:
  an added option after a comma, and at 11 and 14 words; a rhetorical "no,
  wait" mid-sentence; two replacements that bring their own verb; and an
  email with fillers ("I'll bring the, uh, updated numbers"). `--rescore`
  re-scores a recorded run with the current checks at no cost, and the
  harness refuses to start if a check would fail the speaker's own words.
  Both harnesses now read a `--prompt-file` as UTF-8, as the app does, and
  the key file as `utf-8-sig`. On Windows they had decoded candidates as
  cp1252, so the first measurements of the held-back fix were taken on the
  garbled prompt; every self-correction figure in this entry was measured
  after that change. `scripts/auto_test_corpus.py` now clears a
  provider cooldown and retries a case when every provider failed, and fails
  the case if it still fell back. Before, one dropped connection parked Groq
  for 30 seconds and the next 55 cases were scored on the unstyled fallback
  text as if the model had written it.
- `tests/test_utf8_file_io.py` (40 checks, no network or keys) builds the
  real `OpenAIStyler` with an `open()` that behaves like Windows (no
  encoding means cp1252), so it catches the bug on a Mac too, and checks the
  loaded prompt equals `prompts/normal.txt` and `prompts/email.txt` decoded
  as UTF-8, with no "â€" in it. It checks the vocabulary and settings
  loaders accept a byte-order mark. And it scans `src/*.py` and `app.py` for
  any text-mode `open()`, `io.open()`, `os.fdopen()`, `Path.open()`,
  `read_text()`, `write_text()` or text-mode temporary file with no
  encoding (binary modes are exempt), with 34 checks that the scanner flags
  what it should and nothing else. The prompt checks and the scan fail
  against the old code.
- `tests/test_usage_pricing.py` gains 12 checks that run `app.py`'s own
  pricing code: a 3-second or 0.4-second Groq clip costs the same as 10
  seconds, a 29-second clip is unchanged, an OpenAI clip has no minimum,
  clean-up costs are untouched, `record_usage` bills the minimum while
  storing the real duration, `scripts/recost_usage.py` prices a short clip
  the same way, and `get_usage_stats` counts Cerebras calls, with and
  without the stored flag, as estimates, which the panel renders.
- **The tests no longer write into the real data folder.** Modules built
  `~/.waffler-hosted` themselves, at import time, so every test run appended
  fake events to the real `app.log`: of 346 "SUSPICIOUS" transcript retries
  in the maintainer's log, 322 came from tests, which made the real retry
  rate (3.2%) look like 56%. Every module now asks one function,
  `src/data_paths.py`, which honours a `WAFFLER_DATA_DIR` variable, and
  `tests/conftest.py` points it at a temporary folder before collection and
  at a fresh one for each test. The installed app never sets the variable,
  so nothing changes for users. A bare `python -m pytest` from the repo root
  also collected the live harnesses in `scripts/`, which load real keys and
  can call providers when imported; `pytest.ini` now limits it to `tests/`,
  as CI already did. `tests/test_data_dir_isolation.py` (11 checks) fails if
  any module builds the folder itself again. A full run with the home folder
  pointed at an empty folder leaves it empty.
- `tests/test_ui_logic.py` (61 checks, no network or keys) runs the window's
  pure logic, `ui/logic.js`, in Node: the status labels, the hotkeys each
  platform is offered (every one passes `src/hotkey_rules.py` on its own
  platform), the update messages (the same sentences as
  `src/user_messages.py`), the provider order (equal to the app's, and
  cleaned the same way), the "In use" and About lines, the Usage figures,
  the search delay and the no-matches state. It also runs `app.py`'s
  `get_settings()` against the real speech and clean-up classes built with
  made-up keys. It also checks that a message over the setup wizard goes to
  the top centre. Without Node the Node checks are skipped and the source
  checks still run. `tests/test_idle_motion.py` (9 checks) reads the
  stylesheet, `app.js` and `app.py` for the idle-animation rules.
- In the audit harness (the real `ui/` in headless Chrome with a stand-in
  for the app), every screen was captured before and after on Windows and
  Mac sizes. There are no page errors in the status, wizard, Settings,
  Journal or update screens, a message shown during setup is the top
  element where it appears, the keycap labels are 14.7:1 against the key,
  the Mac permission cards are side by side (the step scrolls 909 px in
  its 714 px box, down from 1,540 px), and a refused hotkey shows the
  sentence with no green flash on either screen. After Send in Try it, and
  after a Custom hotkey is saved, the message now covers none of Back, Next
  or Finish Setup at the Windows and Mac sizes and their minimum sizes (it
  covered 96% of Finish Setup on Windows and 98% on a Mac), the point in the
  middle of Finish Setup is the button, and one click on it finishes setup.
  Outside the wizard messages still sit bottom right.
- `tests/test_processing_watchdog.py` (47 checks, no network or keys) runs
  the real `_process` from `app.py` with stand-ins for the recorder, the
  providers, the clipboard, the overlay and the window, with the limits
  shrunk so a "30-second" hang takes a second. A provider that hangs gets
  the offer at the threshold, and the pill's X and the offer's Cancel each
  end the dictation with nothing pasted or kept. At the deadline the
  recording becomes a Not sent card; "Send later", "Keep waiting" and
  "Paste as is" do what they say. A paste that never returns lets the
  dictation finish with the words saved and a "Not pasted" message. An
  exception in speech to text, in the clean-up and in the pipeline itself
  each ends in a plain message and frees the pill. A hang outside any step
  is given up on and never pastes later. A newer recording keeps the older
  one's words and its own pill. Window updates never block, even when the
  window never answers. Esc straight after letting go is left to the app in
  front and the dictation is pasted as usual; Esc while the offer is up ends
  the wait with nothing pasted and the recording kept as a cancelled card
  (not counted as waiting, not sent by itself), whose words arrive when the
  request it stopped waiting for answers; during the clean-up it keeps the
  words unpasted; after "Keep waiting" it is left alone again; and a click
  on Cancel wins over Esc. The listeners pass Esc on while the offer is
  armed, on Windows (the real keyboard hook procedure) and on a Mac. A
  recording whose stop takes longer than its limit ends the dictation at
  once, and is then kept and sent when stop() returns; one with no speech in
  it is not kept. A step given up on still hands over its late result.
- `tests/test_unsent_recordings.py` (38 checks) runs the SR3 promise end to
  end: with the provider down the recording is saved and its card shows;
  with it back, Try again turns the card into a normal entry at the same
  time and removes the file, and nothing is pasted. It also checks the
  automatic sending (after a dictation works; stops at the first refusal;
  three tries; only the last day; never during a dictation), that a WAV no
  card names is never sent, that names pointing outside `unsent/` are
  refused, that two failures in one second keep both files, the card's
  sentences in Node, and the Settings count. A speech request that outlives
  the deadline (or "Send later") fills the card when it answers, with the
  recording sent once in all: the automatic sending leaves it alone while it
  runs, Try again waits for it, a resend that runs out of time is kept the
  same way, a late failure leaves the card to be sent again, a late answer
  with no words stops the retries, one that arrives during a dictation goes
  in as said, and a card deleted meanwhile stays deleted.
- `tests/test_working_pill.py` (14 checks) drives the real Windows overlay
  (Tk) through the working look, the tick, a message that stays up, and the
  offer's buttons, checks that the Mac overlay handles the same commands
  and draws the same ripple and time, and checks the controller's
  commands. The Windows pill and its messages keep their "do not activate"
  style through Tk's own show and stay-on-top calls, and a click on the
  offer's button still reaches it; the Mac pill and messages are built as
  non-activating panels that cannot take the keyboard (read from the
  source, since PyObjC does not load here). `tests/test_tray_state.py` (4 checks) builds the Windows
  "working" icon from the real `icon.ico`.
- In the audit harness the new states were captured on Windows and Mac
  sizes (the window's pill while working and at each ending, Not sent
  cards, Try again in flight, failing and working, Delete's question, the
  Settings count, and the overlay's working pill, tick and new messages)
  with no page errors. The Windows overlay images come from the real
  drawing code; the Mac ones from a canvas port of it. The new plain
  messages (a limit, a skipped clean-up, Cancelled, a slow stop) were drawn
  with the real Windows overlay code, and each heading fits on one line.
  Nothing here has run on a real Mac yet: the Mac overlay (now a
  non-activating panel), Esc on a Mac and the menu bar dimming need a check
  there, and a click on "Keep waiting" or "Paste as is" followed by the paste
  needs trying on both platforms.
- Suite: 901 passed, 2 skipped (one key-table check per platform runs only
  on that platform). A full run leaves the real `app.log` byte for byte
  unchanged.

## [3.14.99] - 2026-09-22

Windows Defender started deleting `Waffler.exe` mid-install this morning, so
Setup failed with "CreateProcess failed; code 225 - the file contains a virus
or potentially unwanted software" and the app could not be installed at all.

### Fixed
- **Defender was classifying the running app as credential-stealing malware,
  and the clipboard poll is why.** The detection was
  `Behavior:Win32/CredentialAccess.A!ml`, severity 5, `DidThreatExecute:
  True` - a behavioural ML verdict on the live process, not a signature match
  on the file. The downloaded installer was verified **byte-identical** to the
  asset our own CI built (SHA-256 `47103517...d88e9d7c`), so nothing had
  tampered with it and the verdict was wrong. The behaviour behind it was
  nonetheless real: the setup wizard called `peek_clipboard_key` on a
  **1200 ms `setInterval`**, and that call reads the clipboard and regex-matches
  it against API-key shapes (`sk-`, `gsk_`, `csk-`). Roughly 50 clipboard
  secret-scans a minute, for a key that arrives once, in an unsigned binary
  that also installs a `WH_KEYBOARD_LL` hook - behaviourally indistinguishable
  from an infostealer. Defender's signatures updated 2026-09-21 20:07 and the
  first detection followed at 10:05 the next morning; no Waffler code had
  changed since 3.14.98 on 11 Sept, so a model update is what moved.
  **Fix:** the pickup now runs on **window focus** instead of on a timer,
  debounced at 400 ms, with the listener removed when step 3 closes. The user
  experience is unchanged, because the alt-tab back from the provider's
  website was the only moment the poll ever caught anything - and that moment
  is exactly a focus event. Same pickup, ~1/50th of the reads, and no standing
  clipboard surveillance in a dictation app. The keyboard hook stays: it *is*
  push-to-talk.

### Note
- Not fixed by this release: the binary is still unsigned, which is the other
  half of why a no-reputation executable trips ML heuristics at all. The
  signing step has been wired into `windows-release.yml` since 3.14.96 and
  skips only because no `WINDOWS_CERT_PFX` secret is set - see
  `docs/CODE_SIGNING.md`. A false-positive report to Microsoft is the free
  route to having the verdict withdrawn for every user rather than allowed
  per-machine.

### Tests
- `tests/test_no_clipboard_polling.py` (5 cases): no `setInterval`/`setTimeout`
  body may reach the clipboard key API, the focus trigger is wired, the
  listener is removed again, the check is debounced, and `app.py` keeps exactly
  one clipboard reader so the read surface cannot grow unnoticed. Verified
  non-vacuous - the timer check fails against the previous `ui/app.js`.
  Suite: **318 passed, 1 skipped**.

## [3.14.98] - 2026-09-11

Reported from the Mac, on 3.14.97: an email dictation lost its "Thank you,
James." sign-off entirely, and "really appreciate your time today" pasted as
"really appreciate Nour time today". Both reproduced here, and neither was
what it first looked like.

### Fixed
- **A dictated sign-off could be deleted outright by the styler.** Raw ASR
  ended `...which version that you're on? Thank you, James.`; the styled
  output ended `...which version that you're on?`. Reproduced deterministically
  against Groq `openai/gpt-oss-120b` with a 12-cell matrix, 3 runs per cell,
  and the trigger is narrow: a greeting must be present (which engages the
  model's email machinery) AND the closing must be "Thank you" **with** a
  comma before the name. `Thanks, James.` survived 3/3, `Thank you James.`
  without the comma survived 3/3, and every form survived with no greeting.
  Root cause: "Thank you" was absent from the prompt's Recognised sign-offs
  list (the same half-closed gap noted in 3.14.83), so with the comma the
  model read "Thank you, James" as thanking James mid-body and dropped it as
  a pleasantry. Two fixes, because a prompt is a request and not a guarantee:
  `prompts/normal.txt` now lists "Thank you" and its variants plus an
  explicit **NEVER DELETE A SIGN-OFF** hard rule, and a new deterministic
  `_restore_dropped_signoff()` puts back any closing present at the end of
  the raw transcript but missing from the styled output — provider-independent,
  idempotent, and narrow enough that a closing used mid-body ("thanks for
  meeting today, James, it was really useful") never triggers it. This is the
  same reasoning that moved email *layout* into code in 3.14.80: deleting the
  user's words is a worse failure than misplacing them. Matrix now 12/12.
- **A short vocabulary entry could overwrite ordinary English words.** With
  `Nour` in `vocab.json`, "your" pasted as "Nour" — and so did "our", "hour",
  "tour" and "pour". This was **not** the Whisper prompt (3.14.97 stopped
  sending vocabulary to the decoder); it was the post-hoc fuzzy corrector
  `apply_vocab_corrections()`. At the default 0.75 similarity threshold a
  four-character vocab entry matches any four-character word one edit away,
  because `1 - 1/4` is exactly 0.75 — sitting precisely on the bar, so the
  most common words in English were the ones it hit. Vocabulary entries
  shorter than 5 characters are now **exact-match only**, and a protected
  list of ~150 everyday words may never be overwritten by any entry however
  close. Short entries still correct on an exact match, and genuine
  mishearings of longer names are untouched (`cobia` -> `COBie`, `Nash can`
  -> `Ashkan` both still pass). A surname misheard as a capitalised proper
  noun ("roman" -> `Rohan`) is deliberately still corrected: that is the
  feature working.

### Tests
- `tests/test_signoff_preservation.py` (12 cases) and
  `tests/test_vocab_false_positives.py` (18 cases). Suite: **313 passed, 1
  skipped**, up from 283.
- Styling regression corpus re-run after the prompt change: **106/107 on
  Groq**, sole failure the long-known M5 self-correction flake, which fails
  identically on the previous prompt.

## [3.14.97] - 2026-09-10

Two long dictations this morning came back ending "...and the rest of the
team." and "Thank you for watching!" with a chunk of what was said missing,
and the raw transcript view showed the same thing. For the first time the
failing audio was still on disk (the last-10-recordings retention from
3.14.85), so this was diagnosed against the actual recording rather than
guessed at.

### Fixed
- **The custom-vocabulary prompt was derailing Whisper on long recordings.**
  Re-running the retained 80 s clip through Groq `whisper-large-v3` with the
  app's prompt (the comma-separated vocabulary list: `Ashkan, COBie, Morta,
  ... Malak, XBim`) reproduced the stored result exactly, twice: 159 words,
  final 20 s of speech replaced by "and the rest of the team." The same audio
  with no prompt returned all 214 words, twice. Every list-shaped prompt
  variant derailed the same clip ("Thank you for watching!", "Subtitles by
  the Amara.org community", "and so on."). The mechanism: a list of
  colleagues' names reads to the decoder like a roll-call, so any uncertain
  window becomes the natural continuation of that list. History bears it
  out: "and the rest of the team" had ended 27 transcripts since June, and
  2% of all recordings ended on a known derailment phrase. On the same day's
  clips the prompt gave no spelling benefit ("craic" came back as "Craig"
  with and without it; the post-transcription vocabulary matcher fixed it
  either way). Groq Whisper is now called **without** the vocabulary prompt;
  the Levenshtein/bigram matcher (`apply_vocab_corrections`) remains the
  vocabulary mechanism. `WAFFLER_WHISPER_PROMPT=1` restores the old call for
  anyone who wants to compare. Verified live: the 09:42 clip transcribes to
  214 words with the correct ending through the real pipeline.
- **The speech measure was undercounting a normal mic by ~2.5x, which
  blinded every safety net.** `_speech_seconds` used a fixed RMS floor of
  150, a fine cut point for splitting audio at silence but far above a
  normally gained laptop mic (median window RMS ~50). The 80 s clip with 64 s
  of audible speech measured as 23 s, so the "impossibly few words for this
  much speech" retry saw 6.9 w/s and stood down; the 08:47 clip that lost
  half its words looked like 3.5 w/s instead of the real 1.3. The threshold
  is now noise-floor-relative (`max(12, 3x the clip's 10th-percentile RMS)`,
  capped at the old value so it can only ever count more speech than before).
  On the ten retained recordings it now tracks the capture diagnostics
  within ~10%.
- **Retry now fires on the derailment signature, not only on word rate.** A
  clip that loses just its final window keeps a healthy overall rate (2.8
  w/s for the 09:42 clip), so the rate gate alone can never catch it. A
  transcript that ends on a known derailment phrase standing as its own
  sentence ("and the rest of the team", "Thank you for watching", "and the
  likes", "Subtitles by ...") after 10 s+ of measured speech is now retried
  on the alternate provider, and a modestly fuller alternate (1.10x rather
  than 1.25x) is accepted, still subject to the semantic-safety checks from
  3.14.93. The phrase inside a real sentence ("send it to Malak and the rest
  of the team") does not trigger.
- **The hallucination filter knows the list-completion family.** An
  own-sentence "and the rest of the team." / "Thank you for watching!" tail
  is stripped after real speech; the bare phrase on a near-silent clip (the
  08:35 one-second tap that produced six words) is discarded; the phrase
  inside a sentence is untouched.

### Changed
- Word-rate gate recalibrated for the corrected speech measure: 1.0 -> 1.5
  words per speech-second in both the transcriber retry and the quality
  signals. Across 160 real recordings measured the new way the healthy
  distribution is p5 = 1.74, median 3.0; the confirmed losses sat at 0.47,
  0.73 and 1.31.
- Raw Whisper output without the prompt is occasionally less punctuated on
  long clips (the styler re-punctuates anything it processes; this only
  affects the "Show transcript" view of pass-through clips).

### Tests
- `tests/test_prompt_derailment.py` (17 cases, offline): Groq is called
  without the prompt and the env override restores it; a quiet mic is
  counted and hiss is not; each derailment tail triggers the retry while the
  phrase inside a sentence does not; the recalibrated rate catches the
  half-lost clip and leaves slow speech alone; the filter strips own-sentence
  tails and keeps in-sentence uses. Suite: 283 passed, 1 skipped.

## [3.14.96] - 2026-09-09

Setup was doing more work than it needed to before a new user could speak a
single word. This is the first pass at that.

### Changed
- **The key step asks for one key, not a choice of three.** It opened by
  explaining a three-provider fallback chain to someone who had not yet
  dictated anything, offered Cerebras as a first-run option even though
  Cerebras cannot transcribe at all (so a setup using only that key could never
  work), and still described the retired ordering "Groq to Cerebras to OpenAI".
  It now says one Groq key does both jobs, and the Cerebras tab is gone.
  Cerebras remains available in Settings for anyone who wants it.
- **The hotkey step says the default already works.** It was never a blocking
  step, but "Listening for hotkey press..." reads like an instruction, so
  people stopped and configured something. It now says to press Next to keep
  the default.

### Added
- **A key copied on the provider's site fills itself in.** While the key step
  is open, Waffler notices a key-shaped clipboard entry, drops it in the right
  provider's field, switches to that tab and validates it. That removes the
  fiddliest part of setup: alt-tab back, find the field, paste.

  It only ever returns text matching a known key shape, so ordinary clipboard
  contents are never read into the UI, logged or stored. 16 tests cover the
  refusals as well as the matches, including that a Cerebras key starting
  "csk-" is not claimed by the OpenAI "sk-" pattern.
- **Code signing is wired into the Windows release, pending a certificate.**
  Unsigned installers raise SmartScreen's "unknown publisher" dialog before the
  user has seen anything, which is the worst moment in the product and the
  likeliest place to lose someone. The release workflow now signs and verifies
  the installer when `WINDOWS_CERT_PFX` and `WINDOWS_CERT_PASSWORD` exist, and
  skips with a note in the log until then, so builds are unaffected in the
  meantime. `docs/CODE_SIGNING.md` covers what to buy, what it costs and how to
  wire it up.

## [3.14.95] - 2026-09-09

### Fixed
- **The Usage panel was confidently wrong, in both directions.** Costs were
  keyed by provider rather than by the model actually called, and the constants
  had drifted from what the app runs. Verified against published rates on
  2026-09-09: Groq cleanup was priced as Llama 3.3 70B at $0.59/$0.79 long
  after the app moved to `openai/gpt-oss-120b` at $0.15/$0.60, overstating it
  about fourfold; Groq transcription used $0.168/hour against a published
  $0.111; OpenAI cleanup used gpt-4o-mini's $0.15/$0.60 while the app calls
  gpt-4.1-mini at $0.40/$1.60, understating it; OpenAI transcription used
  whisper-1's $0.006/min while the app calls gpt-4o-mini-transcribe at $0.003.
  Worst of all, **Cerebras had no branch at all**, so every Cerebras call was
  billed at OpenAI's rates. Rates now live in a table keyed by model, each
  recording its source and the date checked, and each usage entry stores the
  model it was billed as so this cannot go quietly stale again.
- **Cerebras is priced as an estimate, and says so.** Cerebras publishes no
  per-token rate on its pricing page or inference docs (both checked, the docs
  URL redirects to the pricing page, which lists only tier prices). Rather than
  invent a figure, its rate mirrors the same model's published Groq rate and is
  flagged unverified so it can be shown as an estimate.
- **Stale model names in the Usage panel.** It advertised "Llama 3.3 70B" and
  "Qwen-3 235B", both retired. A wrong label there is a claim about what you
  are being billed for.

### Added
- `scripts/recost_usage.py` recomputes stored history with the corrected rates.
  Every entry already keeps what its cost was derived from (duration for
  transcription, token counts for cleanup), so the past is recoverable rather
  than written off. Writes a timestamped backup first and supports `--dry-run`.
  On the maintainer's own 4,926 entries this corrected an all-time total of
  $6.20 to $3.86.

## [3.14.94] - 2026-09-09

Closes the external review. All fourteen findings are now addressed.

### Fixed
- **A wedged overlay could cost you a finished recording.** `on_hotkey_release`
  called `overlay.hide()` before starting the processing thread. A child
  process that has died raises immediately and was always handled, but one that
  is alive and has simply stopped reading its stdin is worse: the pipe fills and
  the write blocks forever. The audio was then never snapshotted and a recording
  the user had already finished speaking was lost. Processing now starts first,
  so keeping your words never depends on the UI being responsive. Separately,
  every write to the overlay is now bounded: a child that will not drain within
  two seconds is judged wedged and terminated rather than allowed to stall the
  app, and VU level frames (about thirty a second, purely cosmetic) take the
  write lock only if it is free and are dropped otherwise.
- **Two callers could spawn two overlay processes.** `_start_process` had no
  serialisation and no liveness re-check, so `prestart()` racing `show()`, or
  two restarts arriving together, each launched a child. The second assignment
  to `self._process` orphaned the first, leaving a stray overlay running with
  nothing managing it. Starting is now serialised and skipped when a live child
  exists. The reader threads were also started without arguments, so they read
  the mutable `self._process` field and after a restart an old reader drained
  the new child's pipes; each reader is now handed its own process.
- **Holding the hotkey could start, stop and restart a recording.** In the
  polling fallback (used when the low-level keyboard hook cannot be installed)
  key state is only sampled, so a combination still held after a state change
  read as a brand new press. Holding Ctrl+Win+Space produced push-to-talk, then
  an instant "cancel sticky", then a fresh push-to-talk, with the user never
  moving a finger, which then fed the recording-loss paths fixed earlier in this
  series. A held combination is now one activation until the keys are seen
  released. Verified by driving the real poll loop over the reported key
  sequence: the tests fail without the latch and pass with it.

## [3.14.93] - 2026-09-09

### Fixed
- **A longer transcript could silently reverse what you said.** When the first
  transcription comes back impossibly short for the measured speech, Waffler
  retries on the other provider and keeps whichever is better. "Better" was
  decided on word count alone, which is no evidence that the two are even the
  same utterance. External review reproduced the consequence:

      original  : "Do not transfer the money to that account."
      alternate : "Please transfer the money to that account right now
                   without any delay."

  The alternate wins on count and inverts the instruction. Replacing a
  transcript is destructive, so the alternate must now look like *more of the
  same speech* rather than different speech: most of the original's words have
  to survive in it, a negation present in the original may not vanish, and a
  figure in the original may not change. Each check can only ever refuse a
  replacement, so the worst outcome is keeping the transcript already in hand.
  When a longer result is refused, the recording is flagged `retry_rejected`,
  because it is then known to be both suspect and unrecovered, which is worth
  saying rather than leaving to be discovered in a sent email.
- **Capture problems left no evidence.** PortAudio reports dropped input
  through the callback's `status`, and the callback swallowed every exception
  so it could never crash the audio thread. Both were discarded, so a recording
  that had genuinely lost chunks looked identical to a clean one. Overflows and
  callback errors are now counted per recording and reported at stop, outside
  the real-time callback.
- **macOS: a failed DMG detach was treated as success.** `hdiutil detach` can
  return non-zero without raising, usually because the volume is still busy.
  The exit status was discarded, leaving a volume mounted and nothing in the
  log. It is now checked, retried once since "busy" is typically transient, and
  reported if it still fails. It never raises: by that point the update has
  either happened or already failed.

## [3.14.92] - 2026-09-09

Concurrency work from the external review. `is_recording` and the capture
buffer were global to the recorder with no notion of which dictation owned
them, so overlapping presses corrupted each other. Recordings now carry a
session, and interrupted work is no longer confused with cancelled work.

### Fixed
- **Starting a second dictation destroyed the first one's finished
  transcript.** Pressing the hotkey again while the previous recording was
  still transcribing or styling looked identical to cancelling it, so the
  completed result was dropped before it reached History: nothing pasted,
  nothing saved, no error. The two are now distinguished. Superseded work is
  still the user's words and is kept; only an explicit cancel discards
  anything. Pasting is about the present (whose window and clipboard is this),
  keeping is about the past (did the user get words out of it). The rule lives
  in `src/pipeline_policy.py` with its own tests rather than buried in the
  pipeline.
- **A late stop could consume and kill the next recording.** `stop()` sleeps
  through the post-roll BEFORE taking ownership, so a press landing in that
  window started a new recording, and the old stop then set
  `is_recording=False` and drained the new recording's buffer. A stop now only
  touches shared state if it still owns the session it began with; otherwise it
  leaves the newer recording alone and returns nothing.
- **A slow cold start could resurrect a stopped recording.** `start()` waits up
  to two seconds for live audio outside the stream lock, and its only check on
  return was that a stream still existed, so a stop or cancel during warm-up
  was undone and capture silently restarted. A start now finalises only if it
  still holds its session, which `stop()`, `force_rebuild()` and `shutdown()`
  all invalidate.
- **The last callback could append to a cleared buffer.** The audio callback
  tested `is_recording` outside `_lock` and appended inside it, so a chunk
  admitted just before a stop could land in the buffer after the snapshot had
  taken and cleared it, and was then discarded by the next recording. The test
  and the append now happen under the same lock the snapshot uses, so an
  admitted chunk either makes it into the returned audio or is dropped. RMS is
  still computed outside the lock, so the real-time callback holds it for one
  list append.
- **A paste could be sent to the wrong window.** The target was read from
  shared state at paste time, which the next press had already overwritten.
  Each dictation now snapshots its own target when processing begins.

## [3.14.91] - 2026-09-09

Three deterministic ways to lose speech, found by an external code review and
confirmed here by reading the code. None needs unusual timing, and all three
reported success while losing the user's words.

### Fixed
- **The microphone picker did not control recording.** `AudioRecorder` took no
  device argument and had no setter, and `_resolve_input_device()` resolved the
  OS default independently. Choosing a microphone in Settings saved an index
  that never reached stream creation, so the app kept capturing from a
  different source while reporting the choice had been applied. If the selected
  mic was the one you were actually speaking into, the recording was of
  something else, or of nothing. The selection now flows Settings to pipeline
  to recorder to `InputStream(device=...)`, wins over the Bluetooth-avoidance
  heuristic (choosing AirPods is a decision, not an accident), and falls back
  safely if the device is unplugged or has no input channels.
- **Deliberate short answers were deleted.** Any press under 500 ms was
  discarded on press duration alone, before the audio was looked at: no
  transcription, no history, no toast, not even retained debug audio. Saying
  "Yes", "No" or a single number quickly was silently thrown away. A brush of
  the hotkey and a real one-word dictation are only distinguishable by what was
  captured, so the decision now rests on measured speech: below 0.15s of voiced
  audio it is a tap, above it the words are transcribed.
- **A failed clipboard write still pasted.** `ClipboardManager.copy()` returns
  False on failure and the pipeline discarded that value, so it went on to send
  the paste keystroke anyway. Whatever unrelated text was already on the
  clipboard replaced the user's selection, and the run was reported as
  successful. Paste is now conditional on the copy succeeding; the transcript
  is still written to History either way, and a toast points there.

## [3.14.90] - 2026-09-09

### Fixed
- **A sign-off introduced by a comma now moves onto its own lines.** Reported
  from a real dictation: "Hi Darren, thanks for your email, can you please send
  me over the powerpoint? Greatly appreciate that, thank you James." came back
  with the greeting split correctly and the body capitalised, but "thank you
  James" still glued to the end of the last sentence. The matcher only accepted
  a full stop, question mark or exclamation mark before a closing phrase, and
  in speech a sign-off very often follows a comma instead. A comma is now a
  valid boundary, and because the clause it was joining has moved to its own
  paragraph, that comma becomes a full stop rather than leaving the body
  dangling. The guard against false positives is unchanged and still holds:
  "I wanted to thank you, Sarah did a great job on the launch" continues past
  the name, so it is not a sign-off and is left alone.

  Verified on the reported dictation: 4 runs out of 4 now produce the intended
  layout. Email consistency across the category is **0 failures in 198 runs**,
  up from 1/198, with the full corpus steady at 106/107.

## [3.14.89] - 2026-09-09

Both bugs below were found by consistency-testing the new Groq model rather
than by a single pass. Each was invisible to the existing suite.

### Fixed
- **Waffler no longer signs your email with the recipient's name.** Dictating
  "Hi James, the docs are live. Cheers." returned a sign-off of "Cheers," then
  "James" on roughly one run in three: the model took the name from the
  GREETING, so the message was signed as the person it was addressed to. Those
  are words the speaker never said, which the prompt already forbids, but
  nothing enforced it. A deterministic guard now removes a sign-off name when
  the raw transcript's closing had no name after it. A genuinely dictated
  "Cheers, James." is untouched. Verified live: 5 runs out of 5 clean, where
  the same input previously failed about a third of the time.
- **The email body is capitalised after the greeting is split.** "Hi James, the
  docs are now live." became "Hi James," followed by a lowercase "the docs are
  now live", on 5 live runs out of 5. Splitting one sentence into two makes the
  second half a new sentence, so it now starts like one. This applies whether
  Waffler splits the greeting itself or the model has already done it, which is
  the common case and the reason the first version of the fix never fired.
  Words carrying an internal capital (iPhone, eBay) are left alone.

### Testing
- Groq `openai/gpt-oss-120b` consistency-tested across every styling category,
  6 runs per case: email 1/198 failing (was 6/198 before these fixes),
  numbered lists 0/48, bulleted lists 1/18, self-correction 0/24, prose 2/240.
  Full corpus 106/107, up from 105/107. The one remaining failure
  (`M5 self-correction`) predates this work and fails identically on every
  provider and prompt version tried.

## [3.14.88] - 2026-09-09

### Fixed
- **Groq cleanup worked again after its model was retired.** Groq removed the
  Llama chat models from the catalogue this key can reach: `app.log` carried
  **433** `model_not_found` 404s for `llama-3.3-70b-versatile`, and a live
  `models.list()` returned no Llama chat model at all. Because Groq is first in
  the chain, every dictation was paying a failed round-trip before falling
  through. Cleanup now uses `openai/gpt-oss-120b`, which the same key can
  reach, overridable via `GROQ_STYLE_MODEL`. Validated against the full 107-case
  regression corpus pinned to Groq: **105/107**, with the two failures
  (`M5 self-correction`, `EM21 Cheers no name`) byte-identical to the Cerebras
  run, so neither is a regression from the swap.
- **The Groq cleanup call was missing `reasoning_effort`.** gpt-oss is a
  reasoning model: without it, the whole output budget goes on thinking and the
  call returns empty or truncated text. A live probe with no flag came back as
  `''`. The Cerebras path has needed this since v3.14.74; the Groq path now
  sends it too.

### Changed
- **Groq is the recommended provider, with OpenAI as the backup.** Groq is the
  only provider that covers both halves of a dictation, speech to text and then
  cleanup, and it is the faster of the two at each. Measured on identical audio:
  Groq Whisper **664ms** against OpenAI Whisper **1298ms**, same transcript.
  Default order is now Groq, then OpenAI, then Cerebras.
- **Cerebras is no longer recommended.** It has no speech-to-text endpoint, so
  it could never run a dictation on its own and always required a second
  provider alongside it. Existing Cerebras keys keep working and it stays in the
  fallback chain; it is simply no longer suggested to new users. Settings now
  labels it "cleanup only, no speech-to-text" instead of "fastest".
- Settings, README and the website updated to match, including corrected
  pricing. The published figures were derived from Cerebras rates and
  understated the cost; recomputed from Groq's rates (gpt-oss-120b at
  $0.15/$0.60 per million tokens, whisper-large-v3 at $0.111/hour) a 30 second
  dictation costs about $0.0018, so the free tier covers roughly 18 a day and
  30 a day works out near a dollar a month.

## [3.14.87] - 2026-09-08

### Fixed
- **"Mic dropped out" no longer appears over a perfectly good transcript.** The
  detector flagged any recording where >=30% of windows were digital silence,
  on the reasoning that a real microphone always has a noise floor, so exact
  zeros could only mean a dead stream. Modern capture breaks that assumption:
  noise suppression (Windows Voice Focus, headset DSP, Krisp-style filters)
  emits **exact zeros** whenever you are not speaking, so pausing to think was
  indistinguishable from the mic dying. Measured across 861 real recordings it
  fired 4 times and was wrong all 4 times - every one transcribed completely,
  at 1.56-3.05 words per second of live audio, while telling the user to
  re-record. What actually separates the two cases is *shape*, not amount:
  gated pauses are many short dead runs with speech after each, whereas a dead
  stream is one long run that never recovers. Detection now measures the
  longest **contiguous** dead run and requires it to still be running when the
  recording ends. Because even that is not conclusive - stopping talking before
  releasing the hotkey also ends on silence - the warning is now deferred until
  the transcript confirms words are genuinely missing.

### Added
- **Per-recording quality signals.** Every dictation is assessed locally and
  instantly from measured audio and observed pipeline behaviour: too few words
  for the speech duration, cleanup discarding an unusual amount, cleanup not
  running at all, output ending mid-clause, a provider retry, an expired
  styling budget. Flagged recordings get a badge in the history list explaining
  why; a clean recording shows nothing, so the signal cannot become noise. It
  reports - it never blocks the paste or alters the text.

  Deliberately *not* an LLM reviewing each transcript: judging a transcript
  from its text cannot detect omission, because the words that come back read
  perfectly whatever is missing. The only ground truth is the audio.

  Calibrating against 2,851 real recordings produced a finding worth acting on:
  **10% of recordings end without terminal punctuation and 2.2% end on a
  dangling function word** ("Then based on the", "I'm also sure that") - near-
  certain mid-clause truncation that nothing was measuring. The two are graded
  separately because an unterminated content word is often a legitimate title.
- `~/.waffler-hosted/quality.jsonl` - one metadata-only row per recording (no
  transcript text, so it is safe to read, share and aggregate), bounded at
  ~2 MB, as the dataset for reviewing quality trends over time.

## [3.14.86] - 2026-09-08

Maintainer audit release. Every item below was reproduced offline before being
changed; see `docs/improvement/AUDIT.md` for the evidence and for what remains
unproven.

### Fixed
- **The transcript filter was deleting real speech.** `_strip_hallucinations`
  matched stock Whisper outros anchored only to the end of the text, with no
  grammatical guard and no reference to the audio. Reproduced: *"Please send
  the deck to Priya and thank you."* → *"…to Priya and"*; *"I'll sign it. Over
  to you."* → *"…Over to"*; *"…onboarding, payroll, benefits and more."* →
  *"…benefits"*; *"The tutorial ends by saying thanks for watching."* → *"…by
  saying"*; and a bare *"Thank you."* was erased entirely. Every one is silent,
  unrecoverable loss of the speaker's own words, and the dangling function word
  ("Over to") is the signature of a phrase that was *integrated speech*, never
  an appended outro. Three guards now apply: the phrase must start its own
  sentence; removing it must not leave a dangling conjunction/preposition; and
  **measured** speech duration decides the ambiguous cases, so a transcript is
  never blanked when the recording actually contained speech. Evidence of
  silence still licenses the original aggressive filtering, which is the case
  that behaviour was written for. 29 negative/positive control tests.
- **A silently failed update looked exactly like a successful one.** A
  v3.14.85 update passed the SHA-256 digest gate and the Authenticode
  advisory, restarted, and came back running v3.14.84 with no error anywhere —
  the reason "I updated and it's the same version" kept recurring. The install
  batch discarded the installer's exit code, relaunched unconditionally and
  deleted itself, and nothing compared the running version against the
  requested one. Waffler now records the intended version before restarting,
  captures the installer exit code, reconciles the two on the next start, logs
  the outcome unconditionally and shows a banner on failure. (The underlying
  cause of that specific failure remains unproven; `%TEMP%` cleanup and a
  recorded Defender detection were both excluded.)
- **An unavailable model was re-probed on every single dictation.** `app.log`
  carried 458 Groq `404 model_not_found` responses for
  `llama-3.3-70b-versatile`. The error chain set cooldowns for 429s,
  connection failures and 401/403 but let a 404 fall through with no cooldown,
  so every recording paid a wasted round-trip before failing over. Both the
  Groq and Cerebras paths now start a 1-hour cooldown and raise a distinct
  `MODEL_UNAVAILABLE` error naming the model. The model is deliberately *not*
  swapped automatically: the provider message is ambiguous between "retired"
  and "this key lacks access".
- **"Download Logs" never opened the folder.** `download_logs()` called
  `subprocess.Popen` without importing `subprocess` in that scope, so it raised
  `NameError` on every use — swallowed by a bare `except`. The zip was written
  and nothing said why the folder did not appear.

### Added
- **The untouched speech-recognition response is now preserved.** Waffler has
  four distinct artifacts — captured audio, ASR response, filtered transcript,
  formatted output — but only the last two were kept: the filtered text was
  saved as history `text` and shown under a button labelled "Show original", so
  the provider's actual words existed nowhere and any filtering mistake was
  permanently unrecoverable. History entries now carry `asr_text` (when
  filtering changed something) and a `text_is` stamp making the field's meaning
  explicit. Entries without that stamp pre-date this change and are documented
  as filtered by an older filter, **not** relabelled as recovered originals.
- `docs/improvement/` — durable audit, status and evaluation records, including
  an explicit list of what is still unmeasured.

### Changed
- UI: **"Show original" → "Show transcript"**. It shows the pre-styling
  transcript, which is not the untouched original; the old label claimed a
  provenance the data never had.
- Per-provider styling timeout 30 s → 15 s (every measured healthy p95 < 6 s).

### Internal
- **CI could not fail.** The pyflakes step ended in `|| echo`, so it exited 0
  whatever it reported — it had been reporting the `subprocess` NameError
  above. `undefined name` now hard-fails; cosmetic findings are printed.
- **The offline test suite was reading private data.** `pytest tests/` imported
  two live benchmark scripts at collection time, which loaded the maintainer's
  real API keys and private `history.json` while contributing zero tests. They
  are excluded from collection; the suite is verified to pass with a redirected
  home directory and no provider keys.
- A test module ran a hardcoded list of functions that had drifted out of sync
  with itself — naming a renamed function and omitting a new one. Replaced with
  discovery. CI now runs the whole suite rather than named files, and a
  **Windows job** was added: the updater batch, hotkey handler and installer
  paths previously had no CI coverage on a product that ships a Windows build.

## [3.14.85] - 2026-07-29

### Fixed
- **"Big recording, only half transcribed" — root-caused with hard evidence, and now both detected and auto-recovered.** Forensics across the full log ruled out everything downstream: audio capture is byte-complete (480 recordings, wall-clock vs captured = 100% even on the longest), and cleanup keeps 86–104% of its input (0 of 42 big recordings lost content). The loss is at the **Whisper API layer**, and it was caught red-handed: a 73.8s recording with **57 measured seconds of speech** came back as **18 words** (0.32 words/sec of speech — impossible for real dictation; every healthy recording measures ≥1.17). ~1% of recordings are hit, which is why it felt random. Three-part fix:
  1. **Incomplete-transcript detector + cross-provider retry.** After transcription, Waffler compares the word count against the measured seconds of speech (per-window RMS, same maths as the silence splitter). Below 1.0 words/speech-second on ≥10s of speech, the transcript is near-certainly broken: it logs `SUSPICIOUS transcript` and immediately retries on the alternate provider, keeping whichever transcript is meaningfully fuller (≥1.25×). A silent 85% loss becomes an automatic recovery; any retry failure keeps the original, so it can only improve the result.
  2. **Near-max uploads no longer gamble on the network.** Live testing proved a 23.2MB WAV is a coin flip: it failed on Groq with a bare "Connection error.", transcribed 100% on OpenAI, then failed on OpenAI 20 minutes later — a ~23MB POST needs a sustained ~3Mbps uplink to fit the 60s client timeout. The single-shot gate drops 24MB → **18MB**: bigger clips (>~9.4 min, rarer than 1 in 200) split at silence into ~120s chunks whose ~4MB uploads are reliably small. **Verified end-to-end: a 180-numbered-sentence 11.5-min clip transcribed 180/180, 0 missing, all six chunks on Groq, 18s total** — on the same network that failed it single-shot. Upload timeouts also now scale with file size (60s base + ~6s/MB, capped 240s) instead of strangling big uploads at a flat 60s, and Groq is skipped outright above 18MB as an independent safety net.
  3. **The last 10 recordings' audio is kept locally** (`~/.waffler-hosted/debug_audio/`, date-stamped, auto-pruned by file age — fixing v3.14.78's rotation bug that deleted the newest files). Local-only, same privacy class as history.json, excluded from the Download Logs bundle. Exists because every previous "half transcribed" report was undiagnosable guesswork without the audio; the next one gets re-run against the actual recording in minutes.

## [3.14.84] - 2026-07-15

### Changed
- **Styling prompt cut by a third (7,629 → ~5,158 tokens) — every dictation is now cheaper and faster, with measured-better quality.** The cleanup prompt had grown to 30.5 KB of accumulated scar tissue, ~80% worked examples, shipped on every single dictation. Combined with the output-token budget, each request weighed ~10k tokens — which is why Groq's free tier (12k/min, 100k/day) was being exhausted after a handful of dictations, knocking out the fast provider daily. The rewrite was **validated the hard way**, per the "test it rigorously, on the loop, keep self-correcting" instruction: an exhaustive rule-extraction pass (99 behavioural invariants preserved as a contract), three independent condensed drafts adversarially audited, then four measured self-correction iterations against the live regression corpus and a new flakiness harness. The loop caught and fixed three regressions before ship: (1) numbered lists inside emails broke on Cerebras when a count word had a connective prefix ("And third, …") — 100%→0% after patch; (2) rare paragraph-shatter on long rambles — 12%→0% after promoting the anti-shatter rule into the top HARD RULES block; (3) numbered lists broke on gpt-4.1-mini specifically — bisected to the condensed list section itself, resolved by transplanting the original battle-tested numbered-list text back (worth its tokens). **Final scorecard vs the old prompt on identical tests: corpus 105-106/107 vs 99/101 baseline; email-category flakiness 1/198 vs 3/198; paragraphing 0/60 vs 12% failing; lists 0/64 across Cerebras + OpenAI.** Sole persistent failure (M5) fails identically on the old prompt. Note: Groq could not be directly validated — its daily token quota (drained partly by this very testing) rejected every pinned attempt; mitigations: Groq sees the unchanged original list section, all layout consistency is enforced by provider-independent deterministic code, and a Groq spot-check is scheduled once quota frees.

### Added
- **True `--provider` pinning in the test harnesses.** `_normalize_provider_order` deliberately re-appends missing providers (so the app's fallback chain can't be emptied) — which silently un-pinned single-provider test runs: a "Groq-pinned" corpus run was found to have been styled 87/101 by Cerebras. Both `auto_test_corpus.py` and `flaky_check.py` now override the normalised order post-construction so a pin means what it says.

## [3.14.83] - 2026-07-15

### Fixed
- **"One thing per line" — continuous speech no longer shatters into a paragraph per sentence.** User report: dictations came back with every sentence as its own paragraph ("it's just a bit shit at the moment") — one real recording became **20 paragraphs**. Root cause, isolated with a controlled A/B test: the prompt listed `"ok so" / "okay so" / "right so"` as paragraph-break cues, and the model over-generalised that to **any sentence starting with "So"** — which is how the user naturally speaks. Same sentences WITH sentence-initial "So": broken 50% of runs; WITHOUT: 0%. The bug was intermittent (temperature 0.1), so single-shot tests always missed it — measured properly it failed **38% of runs** on the worst real transcript. Fix: the paragraph rule now **defaults to one paragraph** and explicitly forbids breaking on sentence-initial connectives (So/And/But/Then/Also/Right/Okay/Yeah/Now/Because), with the user's real failing transcript as the worked example. Re-measured after the fix: **0 failures in 48 runs** (was 12% overall / 38% worst-case). Six regression cases added (`OPL1-6` in the corpus), each a verbatim transcript from the user's history.
- **Email sign-off is now deterministic: always `Closing,` newline `Name`.** The sign-off split was applied by the LLM only ~83% of the time (measured: EM23/EM24 failed 17% of pinned runs), and "Thank you" wasn't even in the prompt's recognised sign-off list — so "Thanks, James" split onto two lines while "Thank you, James" stayed on one. That inconsistency is exactly what the user kept seeing. New `_split_signoff_name()` post-pass normalises a trailing sign-off paragraph to the canonical two-line form (closing + comma, name on its own line, no trailing period) **in code**, regardless of which provider ran. Only fires when the final paragraph is entirely a recognised closing + name, so "I'll see you on Monday, James." is never touched. Re-measured: **0 email-layout failures in 198 pinned runs** (sole remaining flake is the unrelated numbered-list case).
- **Exotic Unicode hyphens normalised to ASCII.** Cerebras emitted `rate‑limit` with U+2011 NON-BREAKING HYPHEN for spoken "rate-limit" — visually identical, but the pasted text breaks search/grep/diffs. U+2010/U+2011/U+2212 now map to `-` in the dash-cleanup pass (em/en-dash → comma behaviour unchanged).

### Added
- **`scripts/flaky_check.py` — consistency harness.** Runs each corpus case N times against a PINNED provider and reports a per-case failure rate. Exists because both bugs above were *intermittent*: a bug that fires 1-in-3 passes a single-shot test 67% of the time, which is why "it doesn't work consistently" reports never reproduced. Also surfaces silent fallback (a run that fell through to another provider is not scored as a pass).
- **`--provider` flag on `scripts/auto_test_corpus.py`** to pin the styler to one provider with no fallback. Previously the harness silently fell through when Groq hit its daily cap — so it was testing OpenAI (which doesn't have the paragraph bug) while claiming to test the configured chain. Cerebras was not wired into the harness at all, despite being the provider that produced the user's broken output.

## [3.14.82] - 2026-07-14

### Fixed
- **Windows auto-update could never install — it aborted every single time after a 100% successful download.** The updater required the downloaded installer to carry a valid **Authenticode signature** and failed closed. But Waffler's Windows installers are **not code-signed** (there is no Windows signing cert in CI — only the Mac build is signed/notarized), so `Get-AuthenticodeSignature` returned `NotSigned` and the install was refused: `install failed: Authenticode verification failed for Waffler-Setup-3.14.80.exe: status='NotSigned'`. Users were silently stranded on old versions (a live install was found still running v3.14.79 after repeated "successful" update attempts). The check was self-defeating from the day it landed. **Fix:** the enforced trust anchor is now the **SHA-256 digest GitHub publishes for every release asset**. It is fetched from the GitHub API *in-process* (never accepted from the webview JS bridge, which is untrusted) and the downloaded bytes must match it exactly — still **failing closed** on any mismatch, malformed digest, or missing digest. Authenticode is now advisory (logged, not fatal) until a signing certificate exists. Verified end-to-end against the exact installer that had been failing on a real machine: its SHA-256 matches GitHub's published digest and it now installs. 17 new tests in `tests/test_updater_digest.py`. *Honest limitation, documented in the code: a digest from the same API is a weaker anchor than a code signature (it proves "these bytes are what the release published", not "our key built this"). When a Windows signing cert exists, the hard Authenticode gate should be restored.*
- **"Pastes raw if the cleanup doesn't come through" now actually happens — and a hung provider can no longer stall a dictation for over a minute.** Styling had a **per-provider** timeout but **no aggregate one**, so when a provider hung or rate-limited, `style()` just ground on to the next one (30s each), stacking into 52–78 second waits. The promised raw-paste fallback therefore almost never fired: it only triggered if *every* provider failed outright — **9 times in 2,347 real recordings**, while **42 recordings sat >10s and 24 sat >30s** because a provider eventually answered. Users waited instead of getting their words. **Fix:** the whole styling step now has a wall-clock budget. Every provider call is bounded by whatever is *left* on that clock (`timeout=` per request), so the step cannot outrun it however the fallback chain goes; when the budget is spent, Waffler stops trying and pastes the (lightly-cleaned) raw transcript — keeping every word — with a neutral "cleanup skipped" reason rather than blaming a rate limit. The budget **scales with input length** (8s base + ~30ms/word, floor 12s, cap 30s) precisely so it does **not** clip a genuinely long dictation, which can legitimately need ~30s of generation since `max_out_tokens` scales to 8192. Per-provider cap also cut 30s → 15s (every healthy p95 is under 6s). 10 new tests in `tests/test_style_deadline.py`.

### Changed
- **Per-provider styling timeout 30s → 15s** (`_STYLE_TIMEOUT_S`). 30s let one hung provider stall a dictation for half a minute, and two hops could stack to 60s+. Measured healthy p95 for all three providers is under 6s, so 15s keeps generous headroom.

### Note on provider order (no code change)
- Diagnosing a "processing is unbearably slow" report against 2,347 logged recordings showed the median TOTAL had roughly **doubled** (1,017ms → 1,872ms) and p95 nearly **quadrupled**. Root cause was **configuration, not code**: the provider order had been set to **Cerebras-first**, putting the slowest, most rate-limited provider on the hot path of every recording (Cerebras p95 7,081ms vs Groq 1,451ms; 1,097 Cerebras "We're experiencing high traffic" 429s in the log — which is Cerebras *capacity* load-shedding, not an account quota, so a paid plan does not avoid it). Restoring **Groq-first** returned styling to ~700–900ms immediately. The fixes above address the *tail* (the 30–78s stalls), which is a separate axis from the median.

## [3.14.81] - 2026-07-10

### Added
- **`logging.log_transcripts` is a real flag now, rather than a promise nothing kept.** It has sat in `config.yaml` since the open-source prep spec asked for it ("Don't log transcribed speech unless `logging.log_transcripts: true`") without a single line of code ever reading the key. It now gates whether transcript text may be written to `app.log` - the file `download_logs` bundles into a bug report, and therefore the file a user hands to a stranger. The default `false` preserves today's behaviour exactly: lengths only, never words. Set it to `true` to chase a transcription bug the way v3.14.78 was hand-instrumented, then set it back. `history.json` is deliberately untouched - that is the History feature, and `download_logs` already excludes it as PII. The flag fails closed: a missing key, a missing `logging:` block, or a non-boolean value all read as `false`. Pinned by `tests/test_log_transcripts.py` (now run in CI), including a regression guard against the v3.14.79 wizard path that interpolated `_wizard_result[:80]` straight into the log.

### Fixed
- **Upgrades no longer leave dead files behind in the install directory.** `installer/windows/Waffler.iss` copies each new build over the old one with `ignoreversion recursesubdirs`, which overwrites and adds files but never removes orphans, so anything dropped from a later build survived forever. A live v3.14.79 install was found still carrying 14 `src/*.py` modules deleted back around v2.0.1; a `python313.dll` and 211 `cp313` `.pyd` files left behind by a Python 3.13-era build (releases ship on 3.11, so none of them can even load); and a 23 MB `WafflerOverlay` bundle from an overlay architecture nothing has referenced in months. A new `[InstallDelete]` step wipes `{app}\_internal` before the new files land, so every install starts from exactly what the build produced. Scope is only `_internal` - the uninstaller stays, and user data in `~/.waffler-hosted` / `~/.waffler` is never touched.

### Removed
- **`.github/workflows/build-windows.yml`.** A stale duplicate of `windows-release.yml`. It triggered on `release: created` - an event `windows-release.yml` itself causes - and then failed every time it ran: it installed `requirements-windows.txt` (the file is `requirements_windows.txt`), pinned Python 3.9, and zipped `dist/Waffler.exe` although the onedir build emits `dist/Waffler/Waffler.exe`. `windows-release.yml` already builds and publishes the real Inno Setup installer.

## [3.14.80] - 2026-06-09

### Fixed
- **Email sign-off and greeting now land on their own lines, identically on every machine.** User report: the same dictated email formatted perfectly on their PC ("…at 10.30am?" / blank line / "Thank you, James.") but on their Mac the sign-off was glued onto the previous line — same app version, same provider order, same keys. Root cause: line-break placement was left **entirely to the LLM**, and the three styler providers (Cerebras gpt-oss-120b / Groq Llama 3.3 / OpenAI gpt-4.1-mini) apply the "sign-off on its own line" convention with different reliability. Because a Mac and PC can land on **different providers** for the same text (rate-limit/availability differs moment-to-moment, and the keys + their limits are shared across machines), the *same* dictation came out formatted on one and run-on on the other. **Fix:** a new deterministic post-pass `_format_email_layout()` runs on the final styled text regardless of which provider produced it, so the layout is now identical everywhere. It promotes a sign-off that's glued to the last sentence (`…10.30am? Thank you, James.`) onto its own paragraph, and pushes a greeting glued to the first sentence onto its own line. It's deliberately conservative — it only reflows email/multi-paragraph-shaped text, the sign-off must be a recognised closing glued to a real sentence end and sitting at the very end, and it's idempotent (already-correct text is never touched). Covers 20+ closings (Thanks / Thank you / Cheers / Kind regards / Best wishes / Regards / …), with or without a name. 11 new regression tests in `tests/test_email_layout.py`, including the exact user example and false-positive guards ("Big thanks to Marco who fixed the build" is left alone because the sentence continues past the name).

### Changed
- **Transcript chunking effectively removed — it's now a file-size safety net, never a duration limit.** Following the v3.14.79 finding that Groq Whisper transcribes multi-minute clips fully in one call (and that splitting by *duration* was itself causing the truncation it was meant to prevent), `_split_audio_on_silence` no longer triggers on length. The only trigger is now **file size**: a clip is split only if its WAV exceeds **24 MB**, the threshold that protects against the provider's ~25 MB upload cap. At 16 kHz mono 16-bit (32 KB/s) that's ~12.5 minutes, and Waffler auto-stops recording at 12 minutes — so in practice **nothing ever splits**: every real dictation goes single-shot, which is the path that reliably transcribes the whole thing. The split logic is retained only so a freak oversized upload degrades gracefully (bounded ~120 s chunks cut at silence) instead of erroring. Chunking tests updated to assert the byte-size gate.

## [3.14.79] - 2026-06-09

### Fixed
- **Transcriptions no longer cut off — the chunking "fix" was the cause, now corrected.** Using the audio captured by the v3.14.78 diagnostic build, I ran the user's *actual* recordings through Groq Whisper directly: 51s, 64s and a 111s clip all transcribed **fully and correctly in a single call**. Groq Whisper does **not** truncate long audio. The real culprit was the client-side chunking added in v3.14.71 (on a false premise): it split one reliable Groq call into 3+ separate calls, and on the rate-limited free tier — or when a silence boundary left a chunk mostly quiet — the later chunks returned nearly empty. The new logging caught it red-handed: a 55.5s clip came back as chunks of **70 / 9 / 0 words**. That's exactly why the loss was inconsistent (sometimes the start, middle, or end) — it depended on which chunk degraded. **Fix:** raised the chunking threshold from 30s to **150s**, so every normal dictation now goes single-shot (the path that reliably transcribes the whole thing). Chunking is reserved only for genuinely huge clips (>150s) that risk the provider's ~25 MB file-size limit, and those now use ~120s chunks (proven to transcribe fully) to minimise calls. Verified end-to-end against the user's real 64s and 111s audio: full transcripts, correct endings. The temporary debug audio-saving from v3.14.78 has been removed.

## [3.14.78] - 2026-06-05

### Diagnostics
- **Capture real audio + real chunking logs to diagnose sporadic transcription truncation.** A user's 37.5 s recording came back as 35 words (truncated mid-word) while a 33 s one transcribed fully (89 words) — and a clean 51 s synthesized test clip also transcribed fully (107 words). So the truncation is content/acoustic-specific, not length-based, and can't be reproduced with synthetic audio. This build (a) saves the last 8 raw recording WAVs to `~/.waffler-hosted/debug_audio/` so the actual failing audio can be re-tested directly against Whisper, and (b) routes the chunking diagnostics (`clip=Ns -> N chunk(s)`, per-chunk word counts) through the file logger so they finally appear in `app.log` — previously they used `print()`, which the windowed build never captured, making it impossible to tell whether chunking even ran. No behaviour change to transcription itself; this is instrumentation to enable a verified fix.

## [3.14.77] - 2026-06-05

### Fixed
- **Phantom recording from a single Ctrl press, correctly this time (Windows).** User pinned the exact repro: finish a Win+Ctrl dictation, then press **Ctrl alone within ~1 second** and Waffler fires a phantom recording; wait longer and it's fine. Root cause: when a Win/Alt keydown triggers the combo it gets **suppressed** (the hook returns 1 to stop the Start menu), and Windows then may never deliver the matching key-**up** — so `_key_states['win']` was left stuck "held" in the cache, and a later Ctrl alone completed the combo from stale state. (The previous attempt, v3.14.75, tried to detect this by polling `GetAsyncKeyState` and broke the hotkey entirely, because the OS can't see a suppressed key as down either — reverted in v3.14.76.) The real fix is purely in our own state cache: `_clear_key_states()` resets all configured keys to not-held whenever a recording **stops** (push-to-talk release, sticky cancel, Esc). A finished recording means the combo was released, so a clean slate is correct — the next recording just needs fresh keydowns, which real key presses rebuild instantly. Because it only touches our cache and never gates a keypress, it **cannot block a genuine press** the way the reverted guard did. Covered by `tests/test_windows_hotkey_clear_on_stop.py` (no phantom even with a simulated missed Win key-up; rapid re-record still works; Ctrl-alone never fires from a clean state).

## [3.14.76] - 2026-06-05

### Reverted
- **Reverted the v3.14.75 "phantom overlay" hotkey guard — it broke the hotkey entirely on Windows.** v3.14.75 added a guard that re-polled `GetAsyncKeyState` before firing the Win+Ctrl combo, to stop a stale cached modifier from triggering a phantom overlay. The fatal flaw: Waffler **suppresses the Win keydown** (returns 1 from the low-level hook to stop the Start menu opening), which means the OS never registers Win as pressed — so `GetAsyncKeyState(VK_LWIN)` reports it as *up* even while it's physically held. The guard therefore concluded "stale cache" on every legitimate press and blocked the combo, leaving the user unable to record at all. Reverted to the known-good v3.14.74 hotkey behaviour. The original phantom-overlay annoyance (pressing Ctrl alone occasionally pops a stale overlay after the hook misses a key-up) remains a known, recoverable issue — cycling the real hotkey clears it — and needs a different fix that doesn't rely on hardware polling of a suppressed key.

## [3.14.74] - 2026-06-05

### Fixed
- **Cleanup no longer chops off the end of longer dictations (Cerebras reasoning-model truncation).** A 130-word dictation came back as 85 words, cut off mid-sentence at "…but surely their one should". Root cause, confirmed with a live API test: the Cerebras default model `gpt-oss-120b` is a **reasoning model** — it spends output tokens "thinking" before emitting the cleaned text. Measured live: with no reasoning control it burned **1,243 completion tokens** on reasoning for a one-line cleanup, blowing the `max_tokens` budget so the actual text was truncated (`finish_reason=length`, sometimes **0 words** of real output). Three-part fix: (1) send `reasoning_effort=low` to Cerebras — drops reasoning from ~1,100 tokens to ~70 and lets the full text complete; (2) raised the output-token floor 1024 → 2048 for headroom; (3) added a precise **`finish_reason=="length"` truncation guard** across all three providers (Groq / Cerebras / OpenAI) — if a model ever hits its token cap, Waffler now falls back to the lightly-cleaned raw transcript so it keeps **every word** instead of silently dropping the tail. The old guard only caught drops below 50%; this one had kept 65% (85/130) and slipped through. Verified end-to-end: the exact failing input now returns all 131 words, ending correctly.

## [3.14.73] - 2026-06-05

### Fixed
- **In-app updates on Windows now actually update (they were silently doing nothing).** Confirmed from a user stuck on v3.14.49 through many "successful" updates: the download worked perfectly, the installer ran, the app relaunched — but the version never changed. Root cause: Waffler runs a second `Waffler.exe` (the overlay subprocess), and the v3.14.49 updater's relaunch batch only waited for the *main* PID to exit. The overlay child stayed alive and **kept the `_internal\` Python DLLs locked**, so Inno Setup silently skipped every locked file (with `/NORESTART` it just leaves them) — the install "succeeded" without replacing anything. The give-away was a pile of `waffler_update_*.bat` scripts in `%TEMP%` that never reached their self-delete. Fix: the relaunch batch now **force-kills every `Waffler.exe`** (main + overlay + any zombie), loops until none remain, then installs with `/VERYSILENT /SUPPRESSMSGBOXES` (so a hidden "files in use" dialog can't hang the headless script) and writes an install log to `%TEMP%\waffler_install.log`. The installer itself (`Waffler.iss`) also gained `CloseApplications=force` + `AppMutex` as defense-in-depth for manual re-runs. **Note:** because the *old* updater is broken, this fix can't arrive via in-app update — install v3.14.73 manually once, and every update after it will work.

## [3.14.72] - 2026-06-05

### Added
- **Choose your own provider fallback order (Settings → Provider Order).** The order Waffler tried providers in was hardcoded (Groq → Cerebras → OpenAI). Now there's a reorderable list in Settings — move Cerebras / Groq / OpenAI up or down and Waffler tries them top-to-bottom. Put your fast paid provider first to avoid free-tier slowdowns. Applies to **both** the transcription and cleanup steps from one unified list; takes effect on the next recording with **no restart** (it live-applies to the running pipeline). Cerebras is tagged "cleanup only" and auto-skipped for transcription because it has no speech-to-text endpoint — only Groq and OpenAI offer Whisper.

### Fixed
- **A wedged provider can no longer hang a dictation for minutes.** The OpenAI SDK's default request timeout is 600 seconds — a real recording showed a cleanup call stuck for **602 seconds (10 minutes)** before it gave up, blocking the whole dictation. Every cloud client (Groq / Cerebras / OpenAI, transcription and cleanup) now has an explicit timeout (30 s cleanup, 60 s transcription) and `max_retries=0`, so a slow or hung provider is abandoned quickly and Waffler fails over to the next one in your order instead of stalling. This is the main cause of the "sometimes it takes ages to process" reports.

## [3.14.71] - 2026-06-05

### Fixed
- **Long recordings no longer lose most of what you said (the "I spoke for a minute and only the first bit survived + 'Thank you for watching!'" bug).** Root cause confirmed from real history + logs: a 90-second recording was being *fully captured* (2.9 MB of audio on disk) but Whisper's decoder terminates early on long audio — it transcribes the first ~20-30 s, emits an end-of-transcript token that surfaces as a hallucinated outro ("Thank you for watching!", "and so on", "and others"), and silently drops the remaining 60+ seconds. A 90 s clip that should be ~200 words was coming back as 45. **Fix:** `transcribe_whisper._split_audio_on_silence` now splits any clip over ~30 s into ≤25-30 s chunks at silence boundaries (so it never cuts mid-word), transcribes each chunk independently, strips per-chunk hallucinated outros, and stitches the parts back together. Each chunk is short enough that Whisper runs to completion. Short clips (the common case) are completely unaffected — they pass through the unchanged single-shot path. Malformed/odd-format audio falls back to single-shot too. Covered by `tests/test_audio_chunking.py` (5 cases incl. a simulated 92 s recording → 4 chunks, zero audio lost).
- **The cleanup AI no longer reorders your sentences.** Confirmed from a real recording: Groq Llama 3.3 70B took a faithful transcript and *reversed the order of the last four sentences* to "improve the flow" — the speaker said "I'm in a bit of paralysis" LAST, and the clean version moved it to the middle, so it read as if content was missing. Cause: the "don't restructure" instruction was a soft line buried in the grammar-smoothing section, not one of the top HARD RULES, so the model treated it as optional. Promoted "NEVER reorder or rearrange what the speaker said — keep every sentence in the exact order spoken" to a HARD RULE with the exact real-world failure as a worked example. Applies to all three styler providers (Groq / Cerebras / OpenAI).

## [3.14.70] - 2026-06-03

### Fixed
- **Stale `focus.signal` no longer triggers a spurious `window.show()` during pywebview bootstrap.** When the focus watcher in `src/single_instance.py:start_focus_watcher` started up, it initialised `last_processed_mtime = [0.0]`, so any leftover `~/.waffler-hosted/focus.signal` from a previous run (one that crashed or was killed before the watcher could consume the file) had an mtime > 0 and would immediately fire `window.show()` + `window.restore()` against a pywebview window that was still mid-bootstrap — surfacing as a spurious focus glitch or black-screen race on the first launch after a crash. Two-line fix: proactively `unlink()` any pre-existing signal file at watcher-start (catches the common case), plus initialise `last_processed_mtime` to `time.time()` so any signal whose mtime predates the watcher is ignored (catches the slow-FS / clock-skew edge case where the proactive unlink couldn't keep up). Only signals freshly written by an actual duplicate launch *after* we start polling will now trigger focus. Closes the last MEDIUM-severity behaviour item from the original OVERNIGHT_AUDIT.md.

## [3.14.69] - 2026-06-02

### Fixed
- **Waffle pill no longer reappears after dismissing the "We couldn't hear you" toast.** User report on a sticky/auto recording (Fn + Space) that produced silence: the popup showed, the user dismissed it, and then the pill briefly came back on its own — with no Fn press. Caught it cleanly in the new `[overlay-dbg]` logs from v3.14.68:
  ```
  Empty transcription result
  Showing 'couldn't hear you' toast
  [overlay-dbg] event=parent.show.enter gen=11   ← phantom show.show() — root cause
  [overlay-dbg] event=parent.send type=show gen=11
  [overlay.py] show_toast: style=error, heading="We couldn't hear you"
  ```
  Root cause: `_show_no_audio_toast` called `self.overlay.show()` immediately before `show_toast`, which set `_visible=True` in the overlay subprocess. `_show_toast` then `orderOut`'d the pill (to make room for the toast), but when the user dismissed, `_hide_toast` saw `_visible=True` and `orderFront`'d the pill back — even though the recording was already over and the user hadn't touched Fn. Removed the spurious `show()`/`hide()` pair: the toast positions itself from `_waffle_x/_waffle_y` (set by the prior recording — exactly the right screen), and `show_toast` already handles subprocess-alive on its own, so the pre-show was unnecessary as well as buggy.

## [3.14.68] - 2026-06-01

### Fixed
- **Overlay pill reliably appears on every Mac Space — properly this time** *(Mac only)*. The v3.14.51 fix mostly worked but stayed unreliable when swiping between full-screen apps. Diagnosed via two parallel investigation agents (code-path archaeology + macOS-API research); both converged on the same root causes, none of which the previous heartbeat actually fixed:
  - **`NSTimer` was registered on `NSDefaultRunLoopMode`.** During trackpad Space swipes the WindowServer puts non-foreground accessory apps' runloops into a *tracking* mode, in which default-mode timers DO NOT FIRE. So the entire animTick (which also drains the IPC command queue and the heartbeat) was paused for the whole swipe — a `show` command that arrived mid-swipe sat un-dispatched until the swipe ended, by which time the window was being placed into the wrong Space. **Fix:** schedule the timer on `NSRunLoopCommonModes` so it fires across all standard runloop modes.
  - **`SpaceChangeObserver.spaceDidChange_` ran on whatever thread the WindowServer posted on**, not guaranteed main — and PyObjC AppKit calls (`setCollectionBehavior_`, `orderFrontRegardless`) from a non-main thread silently no-op. **Fix:** the observer now enqueues a `{"type":"_space_changed"}` command and the main-thread dispatcher (animTick) re-asserts the window, so every AppKit call happens on main.
  - **Child stdin was block-buffered.** `for line in sys.stdin:` uses `TextIOWrapper` iteration which can do internal read-ahead, holding a short `{"type":"show"}\n` line in a CPython buffer until a full block (~8 KB) of data arrives. Combined with the throttled runloop, a show command could be invisibly delayed by hundreds of milliseconds. **Fix:** the stdin reader now uses `sys.stdin.buffer.readline()` (the raw `BufferedReader`), so each command is available the instant its newline arrives in the pipe.
  - **Once `_visible=True`, the heartbeat never re-ordered the window front again** (steady-state assumption). So if a show landed silently mid-transition and the window was orphaned in a stale Space, it stayed orphaned until the *next* Space change. **Fix:** every show (and every Space-change re-assert) now schedules a verify-and-retry — at 150 ms, 300 ms, 600 ms — that checks `NSWindow.isOnActiveSpace()` and re-asserts if it's False. Up to 3 attempts, then it logs `retry.give_up`.
  - **`NSWindowCollectionBehaviorStationary` removed** from the collection-behavior bundle. Apple's docs target Stationary at desktop-pinned widgets (Dashboard-style), not cross-Space follow-the-user pills; mainstream overlay apps (Hammerspoon canvas, Maccy, Raycast-likes) use `CanJoinAllSpaces | FullScreenAuxiliary` without it. Setting it alongside `CanJoinAllSpaces` can confuse the WindowServer's per-Space bookkeeping.
- **Added comprehensive overlay diagnostic logging.** Every step of the show pipeline now emits a single-line `[overlay-dbg]` record to `app.log`: the parent's `show()` enter/sent, every command arriving at the child's stdin, `show.enter`/`show.posted`, `reassert.done` with the window's actual `isVisible`/`isOnActiveSpace`/`occlusionState`/`level`/`collectionBehavior`, `space.changed` with the thread it fired on, `space.handle.start`/`done`, `retry.miss`/`ok`/`give_up`, and `stdin.cmd`/`stdin.error`. Each record carries a `gen=N` per-show counter so the full end-to-end trail for a specific show can be isolated by `grep '[overlay-dbg]' app.log | grep gen=N`. If the bug recurs, the logs will pinpoint exactly which layer is failing.

## [3.14.67] - 2026-05-27

### Fixed
- **Styling no longer falls through to raw text when Groq is rate-limited.** User report: a "Groq failed — add a Cerebras key for fallback" toast *despite* a working Cerebras key, with the unstyled transcript pasted. Root cause (confirmed in `app.log`): Cerebras **retired the hardcoded model** `qwen-3-235b-a22b-instruct-2507` mid-day — it styled fine at 18:09 and started returning `404 "Model … does not exist or you do not have access to it"` by 19:14. So once Groq hit its daily-token cap, the Cerebras fallback 404'd too and styling dropped to `basic_clean` (raw text); the toast then surfaced the Groq rate-limit reason. Repointed the default Cerebras model to **`gpt-oss-120b`** (verified live against the key — it styles transcripts correctly; the only other available model, `zai-glm-4.7`, returns a non-standard response shape and is skipped). The `CEREBRAS_MODEL` env var still overrides the default, so a future Cerebras model rotation can be hot-patched without shipping a build.

## [3.14.66] - 2026-05-27

### Fixed
- **Cmd-Q now actually quits Waffler.** Since v3.14.52 the close button hides to the menu bar — `_on_window_closing` returns `False` for every close — but pywebview routes Cmd-Q through that same `closing` event, so Cmd-Q was being swallowed too and the app could only be killed with Force Quit. It now inspects the triggering `NSEvent`: a genuine **Cmd-Q key-down** sets `_should_quit` and allows the real termination, while the red-X (and Cmd-W, mouse clicks, etc.) still fall through to hide. Fails safe — anything that isn't an unambiguous Cmd-Q hides rather than quits, so it can't terminate by accident.
- **AirPods no longer drop your music to call quality.** Waffler keeps a continuous mic stream open for instant dictation, but opening an AirPods/Bluetooth *microphone* forces the headset from high-quality stereo (A2DP) into call-quality mono (HFP) — so while Waffler ran, music was permanently degraded. Per the user's choice, `audio.py` now detects when the OS default input is a Bluetooth mic and records from a non-Bluetooth mic instead (preferring the built-in one), so Waffler never opens the AirPods mic and they stay output-only in A2DP. Best-effort, name-based detection (`airpods`/`bluetooth`/`beats`/`buds`/…); any failure falls back to the default input, so it can never block recording. Trade-off the user accepted: dictation is captured by the built-in mic rather than the AirPods mic while AirPods are connected.

## [3.14.65] - 2026-05-27

### Fixed
- **History entries can no longer be lost to concurrent writes.** The `history.json` read-modify-write (load → append → save) wasn't serialised, so overlapping writers (a processing thread plus a `clear_history` from the UI, say) could clobber each other. Added a lock + an atomic `append_history()` helper used by all writers. (Verified: 80 parallel appends, zero lost.)
- **The UI can't get stuck showing "recording"/"processing".** `_process` reset the status to idle at nine separate return sites by hand; a `try/finally` now guarantees it on every non-success exit, so any future early-return can't strand the UI. The success "done" status is preserved (the finally only fires when "done" wasn't reached).

## [3.14.64] - 2026-05-27

Fixes from a full code review (7 parallel review agents: security, app.py core, audio/hotkey, overlay, transcription/styling/updater, UI/XSS, repo presentation).

### Security
- **Auto-update is no longer an arbitrary-code-execution path.** `src/updater.py` now **verifies the downloaded installer's code signature before executing it** — `codesign --verify --deep --strict` + `spctl --assess` on macOS, Authenticode on Windows — and **fails closed** if it can't be verified. The macOS install is now atomic (stage → verify → swap → roll back on failure) instead of `rm`-before-copy, and the DMG mount point is parsed via `hdiutil -plist` and always detached. On the bridge side (`app.py`), `start_update_download` only accepts https GitHub release-asset URLs, and `install_update_and_restart` only installs the exact file the updater downloaded — together these close the chain where a crafted webview-bridge call or tampered update metadata could download-and-run anything.
- **Stopped logging transcript text.** Dictation content was written to `app.log`, which ships in the "Download Logs" diagnostic bundle — so sharing logs leaked transcripts. `app.py` and `transcribe_whisper.py` now log word/char counts only. (The bundle already excluded `.env` and `history.json`.)
- **Fixed a UI XSS.** The journal escaped transcript *body* text but interpolated `item.timestamp` (and the `formatTime` fallback) raw into HTML attributes; the webview has full API-bridge access. Now escaped, and `escHtml` is null-safe + escapes single quotes.

### Fixed
- **Cancelled text no longer gets pasted ("ghost paste").** A quick re-press cleared the cancel flag out from under an in-flight processing thread, which then pasted the cancelled transcript into the active app. The recording state machine now opens a new generation id on press and treats a superseded generation as cancelled.
- **No more double transcription / double paste.** Two racing stop events (overlay ■ + hotkey-up, or the auto-stop racing a manual release) could both spawn processing. The `is_recording` check+flip is now atomic under one lock.
- **AirPods/Bluetooth cold-start hang.** The 2 s warm-up added in v3.14.63 ran while holding the audio stream lock, so Esc-cancel / quit / device-switch blocked for 2 s right after a cold start. The warm-up now runs outside the lock.
- **Overlay:** the recording pill no longer looks frozen on slow stages on macOS (the `progress` message is now handled), the toast auto-hide timer is actually cancelled (`.cancel()`, was a no-op `.invalidate()`), the pill is re-shown after a broken-pipe subprocess restart, and the restart backoff no longer sleeps while holding its lock.
- **Update version check** tolerates suffix tags (e.g. `v3.14.63-hotfix`) instead of mis-ranking them, and never offers the release web page as a "download".
- **Profanity restoration** is order-stable for multi-swear transcripts (no cross-clause leak or stray-character artifacts; ambiguous cases are left as-is).

### Changed
- Redacted personal identifiers (legal name, Apple Team ID, home paths) from the planning docs and release runbook ahead of going public; removed dead UI code, leftover debug logging, and a malformed CSS rule.

## [3.14.63] - 2026-05-27

### Fixed
- **Clicking the Dock icon now reopens the window** *(the main report)*. Since v3.14.52 the red close-button hides the window to the menu bar and the app keeps running — but pywebview owns the `NSApplication` delegate and never re-showed the `orderOut`'d window on reopen, so clicking the Dock icon did nothing and the *only* way back was the menu-bar "Show Waffler". Added `_install_mac_reopen_handler()` (`app.py`): an `NSApplicationDidBecomeActive` observer that brings the window forward when the app is reactivated (Dock click / Cmd-Tab) while a `_window_hidden` flag is set. The flag is set in `_on_window_closing` and cleared in `_tray_show_window`, so a normal activation (window already visible) is a no-op — it won't fight you mid-use.
- **Startup mic-permission check works again in shipped builds.** `app.py`'s AVFoundation TCC check (`AVCaptureDevice.authorizationStatusForMediaType_`) imports `AVFoundation`, which is declared as a PyInstaller hidden import — but `pyobjc-framework-AVFoundation` was never in `requirements.txt` or `build_mac.sh`, so CI-built apps couldn't bundle it and logged `[mic-tcc] AVFoundation check failed: No module named 'AVFoundation'` on every launch, silently skipping the check (a denied mic would go undetected). Added the dependency to both. (It worked in older local builds only because the dev machine happened to have the package — a CI-build regression.)
- **AirPods / Bluetooth mic switch no longer loses the first few dictations.** After a mic hot-swap the stream is rebuilt, but the cold-start wait only blocked until the pre-roll was *non-empty* (0.5s) — which a still-negotiating Bluetooth input satisfies instantly with zero-filled (silent) buffers. So the first 2-3 presses after popping in AirPods recorded pure silence, got flagged as a dead stream, and were lost (the 10:20 AirPods burst in `app.log`). `audio.py` now waits for *live* audio (non-zero RMS) up to 2s on a cold start; a normal warm built-in mic still returns in ~100 ms, so there's no latency cost on the common path.

### Notes
- Verified the styling fallback chain is healthy: when Groq's free-tier daily token cap is hit, styling correctly falls back to Cerebras (`Groq FAILED … → styling (cerebras)` in the logs), then OpenAI, then `basic_clean`. No change needed.

## [3.14.62] - 2026-05-23

### Fixed
- **"It dropped about half of what I said" — partial dead audio stream.** Analysis of recent transcriptions found several long hands-free recordings with abnormally low word density (e.g. 204s → 166 words = 0.8 words/s vs a normal ~2.3). A controlled 5.5-minute test proved Whisper does NOT truncate long audio, and the transcripts otherwise matched their durations — so the loss is the mic stream going dead PART-WAY through a recording: CoreAudio keeps the stream `.active` but starts handing back zero-filled buffers, so the back half is digital silence and Whisper only returns the front half. The v3.14.59 dead-stream fix only caught *fully* silent recordings; this slips past it. Added partial-dead detection: the pipeline measures the fraction of each recording that is *exact* digital silence (RMS < 1 — a real mic always has a ~3-10 noise floor, so natural pauses never count), and if ≥30% of an otherwise-speaking take is dead, it rebuilds the stream for the next press and warns "Mic dropped out — please re-record". Also added always-on per-recording audio diagnostics (`[pipeline] audio diag: …s, N windows, digital-silence=X%, speech-windows=Y`) so any future occurrence is fully diagnosable from the log.

## [3.14.61] - 2026-05-22

### Fixed
- **DMG installer background rendered at 2x zoom (v3.14.60 regression).** The committed background was a plain 1320x880 @2x PNG, but Finder places a background image at its native point size — so only the top-left quarter showed (title cut off, arrow stranded in the corner). Replaced it with a HiDPI TIFF combining 1x (660x440) and 2x (1320x880) representations via `tiffutil -cathidpicheck`, which Finder reads as 660x440 points AND renders crisp on Retina. Verified in a locally-mounted DMG before shipping: full title, waffle icon left, gold arrow, Applications right.

## [3.14.60] - 2026-05-22

### Changed
- **Polished the macOS DMG installer window.** The drag-to-install screen now has a proper Granola-style background: warm cream gradient, a "To install, drag Waffler to Applications" title in Georgia, subtle line-art, and a gold curved arrow pointing from the app icon to the Applications shortcut. Generated by `installer/mac/make_dmg_background.py` (committed PNG at 2x for Retina), wired into the release workflow's Package DMG step. Larger icons (112px) and a centred 660x440 window to match.

## [3.14.59] - 2026-05-22

### Fixed
- **Dead mic stream after sleep/wake (the "had to force-quit" bug).** When the Mac sleeps overnight (or a mic is hot-swapped), CoreAudio can leave the InputStream `.active` but delivering zero-filled buffers — every recording comes back as pure silence (`overall RMS=0`) and the app keeps reusing the same dead stream, so the user force-quits. A real mic in a silent room always has a noise floor of ~3-10, so RMS≈0 is a reliable "stream is poisoned" signal. The pipeline now detects it (`overall_rms < 1.0`), hard-rebuilds the stream via the new `AudioRecorder.force_rebuild()` (full teardown + PortAudio reinit on the next press), and shows a "Mic reset — press and speak again" toast instead of a misleading "We couldn't hear you". One wasted press instead of a force-quit.
- **Pill flicker when swiping between Spaces in hands-free mode.** The Space-tracking heartbeat was calling `orderFrontRegardless()` every 250 ms while the pill was visible — forcing it to the front 4×/second, which read as glitchy/janky mid-swipe. Split the re-assert: the heartbeat now only refreshes Space-membership flags (no bring-to-front, invisible) and fires every ~1.5 s instead of 250 ms; the actual bring-to-front happens only on discrete events (hotkey show + the `SpaceChangeObserver`'s per-swipe notification), where it reads as intentional. Smoother swiping, same reliability.

## [3.14.58] - 2026-05-21

### Reverted
- **App icon reverted to the original golden waffle.** The dark + white 3D waffle introduced in v3.14.57 was only ever meant to be the **menu-bar widget glyph**, not the main Dock/Finder app icon — that change was a mistake in scope. Restored `icon_master_1024.png`, `icon.icns`, and `icon.ico` to the original golden waffle. The menu-bar widget keeps its monochrome waffle glyph (unchanged from v3.14.56), and the styling truncation guard (v3.14.56) and VPN save-audio safety net (v3.14.54) are unaffected.

## [3.14.57] - 2026-05-21

### Changed
- **New app icon — sleek dark + white 3D waffle (replaces the golden waffle).** User-directed redesign so the Dock/Finder icon matches the new monochrome menu-bar glyph: a charcoal vertical-gradient squircle with a soft drop shadow, and a white 4×4 waffle of beveled square pockets rendered with 3D depth (lit top-left, shadowed bottom-right). Regenerated `icon_master_1024.png` and, from it, `icon.icns` (macOS, via `iconutil`) and `icon.ico` (Windows, multi-size). Both build specs already point at these files, so the next build picks up the new icon automatically. (The in-app UI logo and website favicon still use the old mark — those can be refreshed separately for full brand consistency.)

## [3.14.56] - 2026-05-21

### Fixed
- **Styling no longer silently deletes most of a transcript.** User report: dictating *"testing testing testing testing one two three four five six seven bitches in heaven"* produced a styled output of just *"Bitches in heaven"* — 11 of 14 words gone. The model read the mic-test repetition as meaningless filler and dropped it; because only the styled text is shown (raw is behind "Show original"), that's silent content loss. `prompts/normal.txt` already forbids truncation with extensive rules, but nothing checked the *output*. Added `_guard_truncation()` in `src/style_openai.py`: after any provider (Groq/Cerebras/OpenAI) returns, if the styled text kept **< 50% of the words on a ≥ 8-word input**, the styler distrusts it and falls back to the lightly-cleaned raw transcript (`_basic_clean`), which preserves every word. It deliberately sets `provider` (not `fallback_reason`), so this raises **no error toast** — nothing failed, it just keeps your words. Short utterances (< 8 words, which legitimately compress hard, e.g. *"um, yeah, okay so, hi" → "Hi"*) are exempt. `_basic_clean`'s stutter-dedup still tidies the repetition, so the example now lands as *"testing one two three four five six seven bitches in heaven"* — all distinct content intact.

### Changed
- **Menu bar waffle glyph: thicker, more elegant.** User feedback on the v3.14.54 template: *"I still want it 4×4, but the current one is too thin — the lines just need to be thicker, then it'd be more elegant."* The thin gridlines read as a wireframe grid; bumped the white batter-line weight (1.4 → 2.8 px) so the 4×4 pockets read as a real waffle at menu-bar size, while staying a monochrome template (tints white on a dark bar, black on a light one).

## [3.14.55] - 2026-05-21

### Changed
- **VPN-block messaging now points to the fix that actually works: switch VPN servers.** Confirmed from the user's `app.log` that the failure is a Groq *edge* block — `403 "Access denied. Please check your network settings."` returned *before* authentication on the VPN's exit IP (not an API-key problem; a bad key returns `invalid_api_key`). The block is per-IP, verified empirically: same VPN provider flips between working and blocked as you change server/location, because each server sits on a different exit IP and only some ranges are on Groq's list. So the old "Add an OpenAI key in Settings as a fallback" advice was misleading — keys are irrelevant to a pre-auth IP block. Updated both the transcription-failure toast/journal note (`_handle_failed_transcription`) and the generic `403` error-branch toast to say *"Your VPN server's IP is blocked by Groq — try a different VPN server (or turn it off) and re-record."* Documented the two no-rebuild workarounds in `ROADMAP.md`: (1) switch VPN server, (2) split-tunnel / exclude Waffler from the VPN so its Groq requests use the unblocked real ISP IP. The proper auto-fallback (OpenAI/local Whisper) remains the roadmap'd durable fix.

## [3.14.54] - 2026-05-21

### Fixed
- **macOS menu bar widget is now a proper monochrome glyph instead of the full-colour app icon.** User report: *"I don't like the way it looks — I want it white like all my other widgets, and our actual logo/symbol, not a plain Waffler emoji."* The v3.14.52 status item resolved its image as `menubar_icon_template.png` → `icon.icns` → `🧇`, but the template PNG didn't exist, so it fell through to `icon.icns` with `setTemplate_(False)` — macOS drew the full-colour golden waffle (on its built-in cream rounded-square background) at 18×18, which looked out of place next to the system's monochrome menu-bar icons. Added a generated `menubar_icon_template.png`: an alpha-only 4×4-pocket waffle glyph (matching the logo) at 36 px for Retina, supersampled 4× and downscaled for clean edges. With the file present the existing code takes the `is_template = True` path → `setTemplate_(True)`, so macOS tints it white on a dark menu bar and black on a light one, exactly like Slack/Discord/system icons. Bundled via `Waffler_mac.spec` `datas` and resolved at runtime from `PROJECT_ROOT` (dev) or `_MEIPASS`/`_internal` (built app).

### Added
- **Recordings are no longer lost when a VPN blocks transcription.** User report: *"With a VPN on it doesn't work for me (but it does for my friend) and I lose all that text."* Root cause confirmed by inspection: the hosted `.env` had `GROQ_API_KEY` + `CEREBRAS_API_KEY` valid but **`OPENAI_API_KEY` empty**. Transcription runs on Groq Whisper, whose only fallback is OpenAI Whisper (`transcribe_sync` → `_transcribe_api`), and that client is `None` without an OpenAI key. So when the user's VPN exit IP is one Groq blocks at the network layer (`HTTP 403` before auth — exit-IP specific, which is why it works for the friend whose IP isn't blocked), `transcribe_sync` raises with no fallback, the whole pipeline 403s, and the recording is gone. Crucially there is **no "raw text" to paste in this case** — speech-to-text itself was blocked, not the AI styling (styling always degrades to a basic clean-up and never hard-fails; Cerebras also covers styling independently of Groq). As a safety net, `_process` now wraps the transcription call: on failure it writes the raw WAV to `~/.waffler-hosted/unsent/recording-<timestamp>.wav`, adds a journal entry (visible in History, with the saved-file path retained on the item) and shows an honest toast — *"Recording saved — not transcribed … turn the VPN off and try again."* The `403`/`Access denied` error branch also now salvages any already-obtained transcript to the clipboard. **The proper automatic fix (OpenAI-Whisper or on-device-Whisper fallback when Groq is blocked) is deferred to `ROADMAP.md`** per the maintainer's call — for now nothing is lost, but the user still re-records with the VPN off to get text.

## [3.14.53] - 2026-05-21

### Added
- **"Download Logs" button in Settings → Data.** User request: *"Implement a button into the settings page which says download logs. I want to use this for fixing bugs — that way, when my friend finds one, he can send his logs to me."* New `Api.download_logs()` method (`app.py`) bundles a self-contained diagnostic zip onto the user's Desktop, named `waffler-logs-YYYY-MM-DDTHH-MM-SS.zip`, then opens Finder/Explorer to reveal it. Contents: `logs/app.log` (tail-clipped to last 2 MB if huge), `logs/crash.log`, `config/{settings,config,setup_complete,vocab}.json`, the last five macOS `Waffler-*.ips` system crash reports from `~/Library/Logs/DiagnosticReports/`, a synthesised `sysinfo.txt` (version, OS, pipeline backends, hotkey config, audio devices, API-key presence-only booleans — never the keys themselves), and a `README.txt` for the recipient. **Deliberately excluded:** `.env` (API keys) and `history.json` (transcripts — PII). Tail-clipping uses a 2 MB cap and `seek(-2*1024*1024, 2)` so a multi-GB log doesn't blow up the zip or the user's chat upload. The UI button (`ui/index.html`, `ui/app.js::downloadLogs`) disables itself while bundling, shows a 6-second toast with the saved path on completion, and lives in the same "🗑️ Data" settings section as Factory Reset.

### Fixed
- **Overlay pill now centres correctly on multi-monitor rigs and follows the cursor across displays.** User report from a friend's machine: *"The Waffler's appearing like underneath the screen to the right, it's not centralized. We just need a bit of consistency going on here."* Root cause: `src/overlay_process.py::main()` snapshotted `NSScreen.mainScreen()` once at launch and computed `_waffle_x = (sw - WIN_W) / 2.0`, which implicitly assumes the primary screen's frame origin is `(0, 0)`. On the friend's setup — an external display rearranged off-origin and the dock on a non-default edge — that calculation put the waffle at global coordinates that fell off the visible area of his active screen. Fix: extracted the geometry calc into `_compute_overlay_position()`, which (1) picks the screen containing the mouse cursor via `NSEvent.mouseLocation()` so the overlay follows the user across displays, (2) falls back gracefully (cursor's screen → `mainScreen` → `screens()[0]` → `(0, 0)`), (3) centres on `visibleFrame` rather than `frame.size.width` so a dock on the left/right doesn't shove the pill off-centre on the active screen, and (4) uses `vf.origin.y + 16` directly (no longer assumes the screen sits at y=0). The dispatcher recomputes on every `show` command and calls `setFrameOrigin_` if the position changed, so plug-in / unplug / rearrangement all just work without a restart. Every position computation is logged to stderr (`[overlay_mac] _compute_overlay_position: via=cursor screen.frame=(...) visibleFrame=(...) → waffle=(...)`) so the new Download Logs bundle will tell us exactly which screen was picked the next time this is reported.

## [3.14.52] - 2026-05-21

### Added
- **macOS menu bar widget — Waffler now keeps running when you close the window.** User report: *"I need a widget on the top of Macs that shows Waffler is still running so it can run in the background even if you click off and click X on the app."* Implemented as a direct `NSStatusItem` attached to pywebview's existing NSApp (`src/`-level globals `_mac_menubar_status_item` / `_mac_menubar_target` / `_mac_menubar_menu` hold strong references so PyObjC doesn't GC them). The menu has *Show Waffler* / *Factory Reset…* / *Quit Waffler*. Clicking the window's red X-button now hides Waffler to the menu bar instead of quitting — the hotkey, recording pipeline, and overlay all keep working. The same `_on_window_closing` interceptor that Windows has used since v3.14.36 is now wired on Mac too, gated on the menu bar successfully installing (so the app can never become invisible-and-unrecoverable on a status-item failure).
- **Why this didn't ship before:** the previous `_create_mac_menubar_icon()` used `rumps`, which wraps `NSApplication.shared().run()` — colliding with pywebview's own NSApp event loop and producing `NSInternalInconsistencyException` that corrupted the app. The codebase comment had warned about this since v3.x. The fix is to drop down a layer and use `NSStatusBar.systemStatusBar()` + `NSStatusItem` + `NSMenu` from PyObjC directly, which attaches to *whichever NSApp is already running* without trying to own one. The new code path runs on the main thread before `webview.start()` blocks, so the status item is registered with NSRunLoop before pywebview takes it over — menu clicks then dispatch cooperatively via the existing event loop.

## [3.14.51] - 2026-05-21

### Fixed
- **Overlay pill now appears reliably on every Mac Space when swiping through multiple full-screen apps.** User report: *"on Mac, when you've got 10 full-screen windows and swipe across with the trackpad, the pill appears on the 2nd one but sometimes doesn't on subsequent ones — sometimes yes, sometimes no, but it needs to consistently work."* Root cause: macOS occasionally clears the `NSWindowCollectionBehaviorCanJoinAllSpaces` flag during Space transitions, especially in a swipe-storm of 3+ full-screen Spaces. The v3.14.15 mitigation re-asserted `orderFrontRegardless` every 250 ms while visible — but only *orderFront*, not the collection behavior itself. When the flag was the thing macOS dropped, re-ordering front did nothing because the window was no longer considered Space-resident. Two-layer fix in `src/overlay_process.py`:
  - **Heartbeat now re-sets collection behavior + window level + orderFront** every 250 ms (was just orderFront). All three paths (show, heartbeat, Space-change observer) now route through a single `_reassert_overlay_window()` helper so they can't drift.
  - **NSWorkspace `activeSpaceDidChangeNotification` observer** registered in `main()` — fires immediately on swipe (no 0–250 ms latency window). The observer is held on a module-level strong reference so PyObjC doesn't garbage-collect it after registration.

## [3.14.50] - 2026-05-21

### Fixed
- **Mic hot-swap actually works now.** v3.14.47 shipped an `AudioDeviceMonitor` that polled for default-input changes, but two layers below it kept reading PortAudio's *cached* default-device index: (a) `sd.query_devices(kind="input")` in the monitor itself, (b) `sd.InputStream(...)` with no explicit `device=` inside `AudioRecorder._create_stream`. So plugging in a wireless mic and switching the system default in Settings often produced no visible change — Waffler kept binding to the old built-in mic, and the user had to restart the app. Two-pronged fix: `_create_stream` now calls `sd._terminate(); sd._initialize()` before constructing the InputStream, forcing PortAudio to re-read the OS-level default. And `AudioRecorder.start()` now recycles the long-lived monitor stream whenever the previous press was >30 s ago — the exact moment a user is most likely to have switched mics since the last recording. The 50 ms one-off cost is invisible, but the user no longer has to close + reopen Waffler when they plug in headphones.
- **VPN slowness: transcription no longer wastes a round-trip on a hard-blocked Groq.** The styler has had a 1-hour skip on Groq 403/401 since v3.12.4, but the transcriber didn't. On NordVPN exit IPs that Groq hard-blocks (confirmed today: `194.156.225.17` → `HTTP 403 "Access denied. Please check your network settings."` in 180 ms before any auth), every recording wasted that 180 ms before falling back to OpenAI Whisper. The transcriber now mirrors the styler's `_groq_skip_until` circuit-breaker — 1-hour cooldown on 403/401/auth, 30 s on connection/timeout. Recovery is automatic on the next launch (or hour). Note: this fixes *waste*, not the underlying VPN block — when Groq is unreachable the OpenAI fallback is still slower than direct Groq would be, by ~5-8 s per recording. There's no client-side cure for an IP block.

## [3.14.49] - 2026-05-20

### Fixed
- **In-app update now reliably reopens Waffler after installing (Windows).** User report: *"I clicked install update, it closed the app and then it didn't reopen — I had to manually relaunch."* Cause: `_install_windows` spawned the Inno Setup installer with `/RESTARTAPPLICATIONS` and then called `os._exit(0)` 500 ms later. Windows Restart Manager only re-spawns a process if it's still alive when RM enumerates it for restart — our early exit killed Waffler first, so RM had nothing to relaunch. (The same `/RESTARTAPPLICATIONS` path was also implicated in the triple-instance multi-paste bug when RM *did* fire and over-restarted.) Replaced with an explicit detached batch script that (1) waits for the running Waffler.exe to fully exit — releasing the v3.14.45 single-instance lock and unlocking files, (2) runs the installer `/SILENT /NORESTART`, (3) launches the freshly installed Waffler.exe exactly once, (4) deletes itself. `ping`-based sleeps (not `timeout`, which needs a console we don't have under `CREATE_NO_WINDOW`); PID-liveness checked by the "Waffler" image name in the `tasklist` row so a digit collision in the memory column can't false-match. Combined with the single-instance lock, the relaunch is guaranteed to produce exactly one running instance.

## [3.14.48] - 2026-05-20

### Added
- **Startup VPN detection logged into `app.log`.** User report: *"Waffler doesn't work or is very slow with a VPN. Having this issue with NordVPN."* The underlying causes vary (VPN exit IPs blocked by Groq / Cerebras, added latency on every request, MTU fragmentation on Whisper audio uploads) and aren't always fixable from inside Waffler — but having a one-grep answer to *"was a VPN on when this happened?"* turns every future "why is Waffler slow today?" investigation into a one-line lookup. Added `src/vpn_detect.py` with a heuristic detector: on macOS we walk `ifconfig` blocks for an UP `utun*` interface with an IPv4 attached; on Windows we scan `ipconfig /all` for signatures of all major VPN clients (NordVPN, NordLynx, ExpressVPN, Surfshark, Mullvad, ProtonVPN, WireGuard, TAP-Windows, OpenVPN, PIA, Ivacy) gated on a same-block IPv4 line. Each detection has a 2 s subprocess timeout and any error returns False, so this never blocks startup. Result lands in `app.log` immediately after the version banner as `[vpn] on (VPN tunnel detected — providers may be slower or block requests)` or `[vpn] off`. No behaviour changes — Waffler runs identically whether VPN is detected or not; the detector exists only to enrich the log so future "is the user on VPN?" investigations are instant.

## [3.14.47] - 2026-05-20

### Fixed
- **Mac mic hot-swap (plug in / change default while Waffler is open).** User report: *"When Waffler is already open and then I add my wireless mic, I go to settings and I make sure the sound input is the mic. Settings can receive it but then Waffler can't — I have to close the app and then reopen it."* Cause: `sd.InputStream` binds to whatever PortAudio considered the *default input device* at the moment the monitoring stream was created (once, at pipeline init), and the stream never gets re-created — so a device change at the OS layer doesn't propagate. Added `src/audio_device_monitor.py::AudioDeviceMonitor` — a 2 s background poll on the default input's name. When it changes, the registered callback fires `AudioRecorder.stop_monitoring()` + `start_monitoring()`, going through the existing `_STREAM_LOCK`-serialised teardown + creation path. The next `_create_stream()` reads PortAudio's *current* default, which now reflects the new device. Skipped during active recording — restarting the stream mid-recording would lose the audio captured so far; the new default takes effect on the next press. Cross-platform (Windows users get the same fix as a free side-effect).

## [3.14.46] - 2026-05-20

### Added
- **Duplicate-launch now brings the existing Waffler window to the front.** v3.14.45's single-instance lock correctly blocked a second main-mode process from running, but it exited silently — so a user who double-clicked the Waffler icon while the app was already minimised / hidden / in the tray saw nothing happen. Followed the Slack / Discord / VS Code pattern instead: when a second instance fails to acquire the lock it touches `~/.waffler-hosted/focus.signal` (best-effort, swallowed on any error) and then exits. The first instance runs a daemon thread polling that file every 200 ms; on detection it calls `window.show()` + `window.restore()` (the latter only if pywebview exposes it on the running version) so the existing window comes forward. File-based signal because it's the simplest cross-platform IPC channel — works the same on Windows, macOS, Linux. The "silent exit" remains the graceful fallback if anything in the signal/watcher path fails: no extra processes spawn, no data lost, just back to the v3.14.45 behaviour.

## [3.14.45] - 2026-05-20

### Fixed
- **Multi-paste bug after in-app update.** After updating from v3.14.36 → v3.14.44 the user's `app.log` showed three simultaneous `=== Waffler starting === (v3.14.44, …)` banners at 08:31:54, three `WindowsHotkeyListener` instances each installing its own low-level keyboard hook, and every Win+Ctrl press fired three `on_release` callbacks → three `_process` threads → three transcriptions → three pastes per dictation (visible in the log as four-deep clusters of `Recording stopped, processing` / `audio captured: 329772 bytes` lines at the same timestamp). Root cause: Inno Setup's `/RESTARTAPPLICATIONS` flag (passed by the in-app updater) uses Windows Restart Manager to relaunch closed Waffler.exe instances after install, and with Waffler's multi-process layout (main + overlay subprocess) it ended up relaunching more main-mode processes than RM had originally killed. Fixed at the app layer with a defence-in-depth single-instance lock: on startup the main process acquires either a Windows named mutex (`Waffler-Single-Instance-Mutex-v1`) or a POSIX `fcntl.flock` on `~/.waffler-hosted/single-instance.lock`. Any subsequent main-mode process detects the lock and `sys.exit(0)`s before constructing a pipeline, hotkey listener, or audio recorder. Crash-safe — the kernel releases the lock on any process exit including SIGKILL, so it can never get stuck.

## [3.14.44] - 2026-05-16

### Fixed
- **Home page now shows the user's actual hotkey on Mac.** `updateHotkeyHint()` in `ui/app.js` had a Windows branch that called `loadHotkeyConfig()` to fetch the saved hotkey, but the Mac branch hardcoded `'Fn'` in the badge, sidebar pill, and empty-state hint. Mac users who'd customised their hotkey via the wizard or Settings (Cmd+Shift, Option+Shift — both valid presets) still saw `Press Fn to start recording` on the home page regardless of what they'd configured. Now always calls `loadHotkeyConfig()`; the hardcoded `'Fn'` text stays as an optimistic fallback for the few milliseconds before the API responds, then gets overwritten with the actual saved hotkey display.

### Changed
- **Vocabulary tab empty state now teaches users what to add.** Previously read just *"No words added yet — Add custom words to improve transcription accuracy."* Meanwhile wafflerai.com's CustomVocab section sells the feature with vivid before-after examples (Siobhan, JSON, Postgres, macOS) that the in-app page didn't show. New users opening Vocabulary now see the same four concrete examples (each with the misrecognition it catches) plus a one-line tip lifted from the website's CustomVocab footer: *"One word or short phrase per entry. No special syntax — just type it the way you want it written."* In-app and on-site messaging now match.

### Internal
- Removed a dead `keysJson` local variable in the wizard's hotkey-preset renderer. Aborted attribute-escaping that was never wired up — the final `onclick` re-stringified directly. Two-line comment now explains why the existing single-quoted-attribute + JSON.stringify pattern is safe (all keys in `MODIFIER_KEYS` are ASCII alphanumeric).

## [3.14.43] - 2026-05-16

### Fixed
- **Settings panel correctly states the styler fallback order.** The "⚡ API Keys" section's description said "Tried in order: Cerebras → Groq → OpenAI" — the OPPOSITE of the actual code execution in `src/style_openai.py::style()`, which is Groq → Cerebras → OpenAI (so Groq's free tier is used up before any paid Cerebras tokens are spent). v3.14.41's iteration through this codebase fixed the same lie in 6 other places (README, prompts/README.md, config.py, style_openai.py module + class docstrings) but missed the most-visible site — the Settings panel users actually open when they want to add or change keys. Now reads "Tried in order: Groq → Cerebras → OpenAI. Groq goes first so its free tier is used before any paid tokens; Cerebras and OpenAI catch the overflow."
- **"Active Backends" display now recognises Cerebras as a styling provider.** The friendly-name map in `ui/app.js::loadSettings()` only handled `groq`/`openai` for the LLM column — Cerebras users saw the raw `cerebras` token or "unknown" because the v3.14.0 multi-provider work never added the corresponding display case. Now shows "Cerebras Qwen-3 235B" / "Groq Llama 3.3 70B" / "OpenAI GPT-4.1-mini" so the same UI reflects whatever provider the pipeline actually chose. Also fixed a v3.14.0 model-name leftover ("GPT-4o-mini" → "GPT-4.1-mini" — the OpenAI default has been gpt-4.1-mini since v3.13.x) and added friendly names for the on-device `mlx` / `faster-whisper` transcription backends, which previously also fell through to the raw-token branch.

## [3.14.42] - 2026-05-16

### Fixed
- **"Rate limit reached" toast no longer hardcodes "Groq API limit hit".** The toast body said "Groq API limit hit. Wait a moment and try again." regardless of which provider actually rate-limited. A user who hit Cerebras's daily cap or OpenAI's RPM cap would see the wrong provider blamed and reach for the wrong fix (e.g. waiting for Groq's free-tier reset when their Cerebras credit had run out). The styler raises errors in the format `RATE_LIMIT|<limit>|<wait>|<details>`; the toast handler now parses out the actual wait time ("Try again in 16m12s" / "Try again in 3s") and surfaces it. When the format is unrecognised the body falls back to a generic "Wait a moment and try again". Either way the body now appends a concrete recovery hint: "Add another provider key in Settings → API Keys for instant fallback." Tested against five real error-msg shapes from the user's app.log (TPD, RPM, per-provider cooldowns, plain 429).
- **Settings panel's "Save Cerebras key" button now does client-side prefix check too.** v3.14.40 added the `csk-` check to the wizard's API-key entry but missed the post-onboarding Settings UI. Pasting a Groq or OpenAI key into the Cerebras field in Settings still bounced to a remote auth round-trip and came back with a confusing "Invalid Cerebras API key" instead of the obvious "wrong provider" diagnostic. Same fix as v3.14.40, applied to `ui/app.js::saveCerebrasKey`.

## [3.14.41] - 2026-05-16

### Fixed
- **Removed em-dashes from "correct output" worked examples in `prompts/normal.txt`.** The prompt has an explicit hard rule banning em-dashes in the output ("they scream 'AI'"; v3.14.0). The runtime `_strip_em_dashes` post-processor enforces it. But four worked-example outputs in the prompt itself contained em-dashes — directly contradicting the rule the model was being trained on. Mixed signal: the model would see "em-dashes forbidden" and "here are 4 correct outputs that have em-dashes" in the same prompt. Replaced each with the comma/full-stop substitution the rule itself prescribes. Locations: `Correct output:` on line 74 (Ctrl+Alt+S example, em-dash → full stop), line 78 (Redis example, em-dash → comma), line 111 (solo "number three", em-dash → comma), and the indented `output:` block at line 155 (Sam dashboard example, em-dash → comma). Em-dashes elsewhere in the file are fine — they're either inside *input* strings the model needs to recognise, inside *WRONG* labelled-as-bad examples, or inside prose rule descriptions. Audited every em-dash in the file before/after; only the four "correct output" sites changed.

## [3.14.40] - 2026-05-16

### Fixed
- **Setup wizard API-key validation parity across providers.** Groq and OpenAI key inputs in the setup wizard already had client-side prefix checks (`gsk_` / `sk-`) that surface "wrong provider — paste a different key here" before any network call. Cerebras input didn't — pasting a Groq key into the Cerebras field went all the way to a remote auth round-trip and came back with a confusing "Invalid Cerebras API key" instead of the obvious diagnostic. Added the `csk-` prefix check on both the client (`ui/app.js`) and the server (`app.py::validate_cerebras_key`) to match the other two providers.

### Changed
- **Wizard success messages now state only what was actually checked.** Previously the Groq success message said "Groq key is valid. **Free tier active.**" — but the validate endpoint just confirms the key works; tier info is never queried. Users on Groq's Developer tier (paid) would see a false claim. Changed to plain "Groq key is valid." Same fix for the Cerebras JS-side fallback message which claimed "Fastest tier enabled" — dead code in practice (server always returns its own message), but misleading if it ever fired.

## [3.14.39] - 2026-05-16

### Fixed
- **"and more" Whisper-layer hallucination stripped on short clips.** Complementary to v3.14.38, which addressed the same symptom at the styling-prompt layer (forbidding the LLM from generating filler-tails). This fix handles the upstream case: Whisper itself emits the literal phrase "and more." on near-silent <1 s audio, where local pass-through styling (no LLM call) never gets a chance to filter it. Evidence from one user's app.log on 14 May: 28 instances of `Done: and more.` with `styling (local): 0ms` — every one was a Whisper-layer hallucination, not a styler abridgement. Added "and more" + variants (`.`, `!`, `...`) to the exact-match `_WHISPER_HALLUCINATIONS` frozen set so bare outputs are discarded; added trailing-pattern regex `(?:and|with|plus)\s+(?:many|much|lots\s+)?more` so real content with a hallucinated `...and more` tail is stripped to just the content. Existing ≤2-word-remainder safeguard prevents discarding edge-case noise as real content. Also added a batch of short-filler hallucinations seen on noise-floor clips: `bye` / `goodbye` / `thank you` / `thanks` / `okay` / `yeah` / `uh` / `um` / `hmm` / `mhm`.

### Tests
- **5 regression cases added to `scripts/auto_test_corpus.py` (FT1–FT5)** guarding the v3.14.38 anti-filler-tail prompt rule. Each is a verbatim shape from the user's real-failure logs ("multi-feature request → 'and many more'" / "trailing-example file list → 'etc.'" / "long multi-clause sentence") or its negative-control complement (speaker LITERALLY said `and so on` / `etc` → rule must NOT strip it). Run with `python scripts/auto_test_corpus.py --filter FT`. Closes the regression-coverage gap: v3.14.38's prompt rule had no automated test.

### Docs
- **README + `.env.example` refresh.** Documentation had drifted 2+ months behind the v3.14 feature set. Now mentions: Cerebras (the primary styler since v3.14.0) and how to get a key for it; Esc as the universal cancel hotkey (v3.14.37); the in-app auto-updater; smart hallucination filtering; custom vocabulary. Fixed self-contradiction in Known Issues (doc said both "macOS signed and notarised" *and* "Gatekeeper will warn"). Fixed installer-filename pattern to match actual asset names (`Waffler-Setup-<version>.exe`, `Waffler-<version>-mac.dmg`). Added sign-up URLs for each provider so new users don't have to hunt.

## [3.14.38] - 2026-05-15

### Fixed
- **Styler no longer truncates long dictations with "and many more" filler.** User reported two transcripts where the model collapsed real content (a list of UI requests and a multi-feature suggestion) into the literal string "and many more" at the end — `I'd like it to and many more.` / `either that, and many more.`. Both outputs were grammatically broken because the filler was being spliced in where actual content should've gone. Root cause: the prompt's existing "NEVER drop whole sentences" rule didn't *explicitly* name the filler-tail phrases the model defaults to when it decides to abridge. Added an explicit hard-rule in `prompts/normal.txt` forbidding `"and many more"`, `"and so on"`, `"etc."`, `"etc etc"`, `"and other things"`, `"amongst others"`, `"to name a few"`, `"and the like"`, `"and similar"`, `"and others"` unless the speaker literally said those words. Mirrored the rule into the inline system message used by Groq, Cerebras, and OpenAI provider calls (higher-priority instruction-following slot). Two worked failure examples included so the model has a concrete pattern to recognise and avoid.

## [3.14.37] - 2026-05-15

### Added
- **Esc is now a global cancel hotkey for active recordings.** Previously the only way to discard a recording was clicking the small X on the floating overlay — fiddly and slow, especially in sticky mode. Now pressing **Esc** at any time during a push-to-talk hold or sticky recording immediately cancels: audio is dropped, no transcription fires (saves API quota on accidental hits), no paste, clipboard is cleared, sticky state is reset. Goes through the same handler as the overlay X-button click. Esc is a no-op when no recording is in flight, so it stays available for dialogs, vim, file pickers, etc.
- **Wizard step 2 now teaches Esc-to-cancel.** Added a fourth instruction tile alongside "Hold to record" / "Release to stop" / "Sticky mode" so users learn the shortcut up-front. Also updated the legacy sidebar hint string from `Hold to record • +Space = sticky mode` to `Hold to record • Space = sticky • Esc = cancel`.

### Wired through
- `SmartHotkeyListener` (macOS) and `WindowsHotkeyListener` both gain an optional `on_cancel` constructor parameter. The Mac event tap's existing Esc handler was repurposed from "sticky-mode-only escape hatch" to "universal cancel"; Windows' low-level keyboard hook + polling-fallback paths got new VK_ESCAPE branches that fire the cancel callback when state != IDLE. The pipeline wires `on_cancel=self._on_overlay_cancel` at both creation sites (initial start + hotkey-config restart). Wizard test-listeners (step 2 / step 4 / FnKeyMonitor) leave `on_cancel=None` so Esc behaves normally during onboarding.

## [3.14.36] - 2026-05-15

### Fixed
- **Fn-spam no longer stacks up "We couldn't hear you" toasts or burns transcription rate limits.** Pressing Fn 20 times in a row used to fire 20 transcription API calls (hitting Groq's 20-req/min cap almost instantly) and surface 20 toasts on screen. Root cause: the existing short-tap discard at `app.py:2403` was *dead code* — it checked `len(audio_bytes) < 44 + 9600` (i.e. < 0.3 s of bytes), but the recorder's 500 ms pre-roll buffer guarantees every recording has > 0.3 s of bytes regardless of how briefly Fn was pressed, so the check never fired. Replaced with a proper **time-based** check on `recording_duration` (press-to-release wall time): anything under 0.5 s is silently discarded — no transcription, no styling, no toast, no history entry. 0.5 s is comfortably below any deliberate dictation but well above typical Fn-mistap durations. The audio buffer is still drained so the next press starts clean.

## [3.14.35] - 2026-05-15

### Fixed
- **"Select mic" button on the "We couldn't hear you" toast actually does something now (macOS).** The toast button click was being silently swallowed: the handler called `subprocess.Popen(["start", "ms-settings:privacy-microphone"], shell=True)` which is *Windows-only* syntax — on macOS the `start` command doesn't exist, `Popen` failed, the broad `except` swallowed the exception, the toast hid, and the user saw nothing happen at all (then had to quit + relaunch). Replaced with a platform-agnostic action that brings the Waffler window to the front and navigates to the in-app Settings page where the device dropdown lives. Strictly more useful than the old Windows-only "Open OS Privacy panel" behaviour too: users can now see exactly which mic Waffler is using and switch to a different one without leaving the app.

## [3.14.34] - 2026-05-15

### Changed
- **Home-screen "Update available" banner now installs in-app instead of bouncing to the website.** The banner's `Download` button used to call `open_url('https://wafflerai.com/download/')`, sending the user out of the app to find the DMG / EXE themselves. It now opens the same in-app download-and-install modal that Settings → About already used: streams the installer with a progress bar, then runs `install_update_and_restart` on completion. The user never has to leave the app. The "Download in browser" fallback in the modal still works for cases where the in-app fetch fails.

## [3.14.33] - 2026-05-14

### Fixed
- **Fn hold-quiet timer — third attempt at fixing the OS-level Fn oscillation chatter (macOS).** v3.14.31's 40 ms leading-edge debounce caught hardware bounce but not the 60–250 ms OS-level oscillation seen on M-series Macs (Touch ID + Globe key both poke modifier state). v3.14.32 took the right approach — defer `on_release` via a `threading.Timer` that cancels if Fn re-asserts inside the hold window — but was reverted. The likely culprit there was a known `threading.Timer` race: `cancel()` only stops a timer that hasn't entered its callback, so an OS Fn=1 arriving while `_fire_delayed_release` is mid-execution would silently fire `on_release` anyway. v3.14.33 fixes that with a ticket-based invalidation pattern — every schedule/cancel bumps `_release_ticket`, and the timer callback captures its ticket at schedule time and aborts harmlessly if the ticket has moved on. `cancel()` is still called best-effort to skip the wait, but correctness no longer depends on it. Hold window dropped to 150 ms (audio post-roll already adds 150 ms of trailing capture, so a deliberate quick tap stays snappy). Tests in `tests/test_fn_handler_chatter.py` include a verbatim replay of the 18:07 chatter pattern (14 OS toggles → exactly 1 `on_press` + 1 `on_release`).

## [3.14.31] - 2026-05-14

### Fixed
- **Fn key debounce on macOS — kills phantom multi-recording fan-out.** Reproduction on v3.14.29 showed a single Fn tap producing three full `Recording started → Recording stopped` cycles in ~1 s (durations 0.17 s, 0.19 s, 1.36 s). Root cause: macOS / external keyboard occasionally emits multiple `flagsChanged` events for one physical press, with FN_FLAG flickering 1→0→1 in under 10 ms. The v3.14.13 single-CGEventTap consolidation correctly suppresses *intra-tap* duplicates but doesn't filter *OS-level* phantom events. Added a 40 ms debounce in `FnHandler` — state transitions inside that window are rejected with a `Fn press REJECTED (debounce: 3.2ms < 40ms)` log line. Real human Fn taps last 80–300 ms so the floor accepts every plausible input; phantom flicker is universally <5 ms so it gets caught.
- **Proper mic permission check via AVFoundation at startup (macOS).** The "bytes captured but RMS=0" signature in the 17:45 chaos log is *TCC-denied stream*: on macOS, an app without Microphone permission gets `sd.InputStream` opens that succeed silently and deliver zero-valued samples — no exception, no error, just permanent silence. The existing `PermissionsManager.check_microphone_permission()` was therefore lying (it returned GRANTED whenever the open succeeded). Added an `AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)` call at startup, logging the actual status (NotDetermined / Restricted / Denied / Authorized). When denied, a `[mic-tcc] WARNING: mic permission DENIED` line lands in `app.log` with the fix steps. Any future zero-RMS reproduction now self-explains via the log instead of needing a debug session to diagnose.

## [3.14.30] - 2026-05-14

### Added
- **Version stamp in the startup banner.** The `=== Waffler starting ===` line in `app.log` now includes the running `__version__` (e.g. `=== Waffler starting === (v3.14.30, PROJECT_ROOT=…)`). Was previously a recurring source of confusion when diagnosing Mac issues — figuring out which bundle a log was from required a separate `grep __version__` against `/Applications/Waffler.app/Contents/Frameworks/src/__init__.py`. Now it's one grep against the log itself.
- **Hotkey-path diagnostic logging (Mac).** `SmartHotkeyListener`, `MacEventTap`, and each handler (`FnHandler`, `GenericHotkeyHandler`) now route their lifecycle + key events through a new `src/log_util.py` helper into `app.log`, tagged with a unique per-instance id (`L01`, `tap01`, `Fn01`, `Gen02`…). The 16:22:17 dual-recording reproduction surfaced `Recording started` × 13 in 15s in `app.log`, but the `[HOTKEY] …` lines from `smart_hotkey.py` only went to stdout — so we couldn't tell whether it was one handler fanning out or multiple listeners alive at once. With the new logging the next repro will say e.g. `[HOTKEY/L01] _on_hotkey_press entered` 13 times (= one listener mis-firing) vs `[HOTKEY/L01] press fired` / `[HOTKEY/L02] press fired` interleaved (= two listeners alive). Pure observability — no behaviour change.

### Fixed
- **"Restart now" sometimes quit the app without relaunching it on macOS.** The `restart_app()` IPC called `/usr/bin/open -n /Applications/Waffler.app` while the old process was still running, then `os._exit(0)`'d 600 ms later. For signed/notarized `.app` bundles, Launch Services occasionally *collapsed* the `-n` request into a no-op "bring existing to front" when it saw an instance of the same bundle ID already alive — leaving the user with a dead app and no replacement. Switched to the Sparkle-updater pattern: spawn a detached `/bin/sh` that polls our PID, and only once we're gone calls `open -n` on the bundle. By the time the launch request is made, Launch Services has nothing to collapse, so it always honors it. Source-run path and the Windows re-exec path are unchanged.

### Changed
- **Restart prompt is now a centered modal popup instead of a top-of-page banner.** User reported the banner was easy to miss above the fold. New modal has a soft blurred backdrop, a centered card with the rotating 🔄 icon, primary "Restart now" + secondary "Later" buttons, full keyboard support (Enter triggers restart, Esc dismisses, primary button is autofocused), and click-outside-to-dismiss on the backdrop. Same `showRestartBanner()` callsite — only the rendering changed.

## [3.14.29] - 2026-05-14

### Changed
- **Wizard hotkey keycaps are now black-on-cream.** The big "Fn" / "Ctrl+Win" keycap in step 2 and the mini chips inside the "Hold to record" / "Sticky mode" instruction tiles were all white-on-cream — they faded into the wizard background. Switched both to a dark `#1A1612 → #2C2520` raised pill with `#FFFCF2` text so they read as real physical keys. Pressed state still flips to brand-gold so visual feedback during a real key press is unchanged.

## [3.14.28] - 2026-05-14

### Added
- **"Taking longer than usual" overlay toast.** If transcription takes >10 s or styling takes >15 s, a one-shot toast fires telling the user the provider may be slow and suggesting they add a fallback key in Settings → API Keys. Fires exactly once per dictation. The thresholds are tuned so they only trigger on genuine provider slowness — most dictations complete well under both.
- **"Restart now" banner after saving an API key.** When the user pastes a key into Settings (Groq / Cerebras / OpenAI) and hits Save, a sticky banner now appears at the top of the page with a clear "Restart now" CTA. The styler's client objects are constructed once at pipeline init and don't re-read keys live — so a fresh Cerebras key wasn't taking over fallback duties until the user manually quit and reopened. The new banner is loud, non-auto-dismissing, and clicking "Restart now" cleanly relaunches the app via a new `restart_app()` API endpoint (uses `open -n` on macOS to spawn a fresh `.app` instance; re-execs `sys.executable` on Windows).
- **Wizard reliability nudge in step 3.** Below the existing "Waffler tries them in order" intro, a soft amber-bordered tip card now reads: *"💡 Add more than one for reliability. If a provider hits a rate limit or has a slow day, Waffler will fall back to the next one automatically. You can always add more keys later from Settings."* Sets the right expectation without blocking advancement on a single key.

### Changed
- **Rate-limit toast wording shortened.** Was 3+ sentences (~140 chars body) which overflowed the toast and was hard to read in a flash. New version is one sentence: heading names the provider + when it resets ("Groq daily token limit hit · resets in about 3h"), body says "Pasted raw — Add a Cerebras key for fallback." Two ideas, fast to parse, still actionable.
- **In-app toasts now stay visible on hover.** Hovering over a toast pauses the auto-dismiss timer; the cursor leaving restarts a shorter 1.2 s timer. Clicking the toast dismisses it immediately. Stops users losing important messages mid-read while reaching for the mouse.

### Reverted
- **The v3.14.26 "Fn on Mac Mini" misfire**, dropped in v3.14.27.

## [3.14.27] - 2026-05-14

### Fixed
- **Wizard step 1 let users advance past macOS permissions without granting them.** The Next button on step 1 was `disabled = false` unconditionally in `wizUpdateNextButton()`. The polling enabled Next when both permissions were detected, but nothing kept it disabled in the meantime — and on initial render the button was already clickable. The visible symptom: user runs setup wizard, clicks Open System Settings, gets distracted before granting Input Monitoring, comes back, clicks Next anyway, lands on step 4 (Try It), holds the hotkey... and nothing fires. The hotkey listener can't function without Input Monitoring access. Tracked global state (`_wizardPermsAccessibility`, `_wizardPermsInputMon`), gated `case 1` on both being true, and added a belt-and-suspenders refusal inside `wizNext()` itself that shows a toast naming the missing permission(s) if the disabled-state was somehow bypassed.

### Reverted
- **v3.14.26 "Fn key on Mac Mini" changes.** That fix targeted what turned out to be a different bug — the user had simply not granted Input Monitoring (see Fixed above). Reverted the speculative keycode-63 (kVK_Function) detection in `FnHandler` and the "Fn not registering?" wizard help card so the codebase isn't carrying defensive logic for a problem that doesn't exist.

### Changed
- **Wizard buttons are now dark charcoal pills.** All cream-on-cream buttons (Open System Settings, Use a different hotkey, Back, Next) read as decorative on the cream wizard background; switched to solid `#1A1612` charcoal with `#FFFCF2` text so they pop as proper action buttons. The gold brand colour now appears as a hover glow ring instead of the base button colour. Finish-Setup button on the final step keeps its green for completion semantics.

## [3.14.26] - 2026-05-14

### Fixed
- **Fn key not detected on Mac Mini / Magic Keyboard setups.** Reported: "the Fn key won't work on my Mac Mini". Root cause is one of two things, both common on Mac Mini hardware:
  1. **macOS "Press 🌐 key to:" override.** System Settings → Keyboard → Modifier Keys lets you remap the Fn / Globe key to actions like "Change Input Source" or "Show Emoji & Symbols". When this is set to anything other than "Do Nothing", macOS intercepts the Fn key at a system level *before* our CGEventTap sees it — the `kCGEventFlagMaskSecondaryFn` flag never fires.
  2. **External Apple keyboards that send Fn as a raw keycode** (kVK_Function = 63) instead of a modifier flag. This used to surface as F13/F14/F15 (105/107/113) on older external keyboards, but on Magic Keyboard for Mac Mini it can be raw keycode 63 with no flag accompaniment.

  Two-part fix:
  - **Python `FnHandler` now catches keycode 63** in addition to the existing F13–F15 / F16 (`_EXT_FN_KEYCODES = {63, 105, 106, 107, 113}`). Fires the same press/release callbacks and suppresses the key event so the emoji picker / globe-key shortcut doesn't fire alongside.
  - **Wizard "Fn not registering?" help card** — fades in below the listening pill in step 2 after ~6 seconds if no key press has registered AND the configured hotkey is Fn AND the user is on macOS. Explains the Globe-key override issue plainly and offers two one-click actions: (a) "Open Keyboard settings →" which deep-links to `x-apple.systempreferences:com.apple.preference.keyboard`, or (b) "Choose a different hotkey →" which opens the existing alternative-hotkey picker. Hides automatically the moment a real Fn press is detected.

## [3.14.25] - 2026-05-14

### Changed
- **Wizard Step 3 — "Open the link" card is now a clickable target.** All three provider panels (Groq, Cerebras, OpenAI) had a card 1 illustration captioned "It opens straight to the API Keys page" — but the card was decorative; you had to find the small link in the help text below to actually open the page. The whole card 1 is now a tap target: cursor-pointer, hover lift, focus ring, and a "↗ Click to open" chip that fades in on hover. Clicking opens the provider's API-keys page via `pywebview.api.open_url` (or `window.open` as a fallback when previewing in a browser).
- **Wizard Step 3 — OpenAI now has the same animated three-step walkthrough as Groq and Cerebras.** Previously OpenAI was the only provider without the mock-browser walkthrough. Added the full structure with OpenAI's brand colours (dark theme, green `#10A37F` accent), `platform.openai.com/api-keys` URL bar, proper sidebar items (Playground / Assistants / Fine-tuning / **API Keys** / Usage), `+ Create new key` button matching their actual UI, and an `sk-proj-…` key prefix in the reveal modal.
- **Wizard Step 3 — added "Save this key somewhere safe" notice.** Every provider panel now has a gold-amber banner between the walkthrough and the paste input: 🔐 *"Save this key somewhere safe. Groq/Cerebras/OpenAI only shows it once — keep it in a password manager so you don't have to regenerate it later."*
- **Wizard Step 4 — "TRY SAYING" label is now a proper CTA chip.** Was a small 11px uppercase label; now it's a 14px gold-pill with a 🗣️ glyph, generous padding, soft drop-shadow, and a rounded-pill border. Stands out as an actionable prompt instead of decorative text.
- **Wizard Step 4 — send button now actually sends the dictated message.** Previously pressing the ▶ button just showed a "Pushed to app!" toast and cleared the input. Now the dictated text actually appears as a sent reply bubble (right-aligned, gold-gradient, mirror corner-radius — proper iMessage-style) inside the conversation thread with Alex. The user sees the whole loop: dictate → text in input → tap send → message lands in the chat. Reinforces what the app will do for real in their actual messaging apps once setup is done.

## [3.14.24] - 2026-05-14

### Fixed
- **Vocab "Add word" input still pre-filled for some users on upgrade.** v3.14.23 removed the legacy `loadVocab()` that was dumping every stored word into the input on startup, but some users still saw the pre-filled string after updating — turns out WebView's form-restoration cache was holding the value from the previous version's bad behaviour. Belt-and-suspenders fix:
  1. `loadVocabPage()` now unconditionally clears `#vocabInput.value` every time the page renders. Even if WebView, the browser cache, an extension, or future regression tries to put content there, it gets wiped the moment the user navigates to the Vocabulary tab.
  2. Added `autocomplete="off"`, `autocorrect="off"`, `autocapitalize="off"`, `spellcheck="false"`, and an explicit empty `value=""` on the input element so WebView's form-restoration can't repopulate it across launches.

## [3.14.23] - 2026-05-14

### Fixed
- **Vocabulary "Add word" input was pre-filled with every existing word jammed together.** A legacy `loadVocab()` function ran on every `pywebviewready` startup, joining all stored vocab words with `\n` and dumping the result into `#vocabInput`. The element used to be a multi-line `<textarea>` in an older UI, but the current UI uses a single-line `<input>` plus a separate list with delete buttons — so newlines collapsed and you'd see "WafflerAshkanGroqMLXkubernetesFargatepywebview…" stuffed into the box, forcing you to delete it before adding a new word. Removed the legacy `loadVocab()` / `onVocabSave()` functions and the startup call. Vocab list is loaded lazily by `loadVocabPage()` when you navigate to the tab; the input stays empty until you type in it.

## [3.14.22] - 2026-05-13

### Fixed
- **"Waffler" brand wordmark still didn't match the website.** v3.14.20 loaded Source Serif 4 from Google Fonts, but the actual mismatch was something subtler — the website's *header* logo uses `text-xl font-bold` with no font-family override, so it inherits Inter Variable from the body. I'd been setting the app's `.j-brand-text` to `var(--serif)` (Source Serif 4) thinking the website used serif there. Wrong font family entirely. Switched the app's brand wordmark to `Inter Variable` (bold 700, 20px, `letter-spacing: -0.01em`) so it now renders identically to the header. Journal entry body text + date dividers still use Source Serif 4 — that's where the editorial serif feel belongs.

### Changed
- **Status pill wording.** "Listening for [Fn]" was misleading — it sounded like the microphone was already hot. Changed to "Waiting for activation [Fn]" — calmer, matches the actual idle state. The Fn key chip stays so users still see exactly how to start.

## [3.14.21] - 2026-05-13

### Fixed
- **Top-nav Journal tab stayed highlighted after clicking another tab.** The HTML default put the active marker on `j-nav-active` (a sibling class to the showPage-toggled `.active`), so once the user clicked Vocabulary or Settings, BOTH "Journal" and the clicked tab looked active — confusing. Changed the default class to use `.active` (which `showPage()` actually toggles on/off), so the highlight tracks the current page correctly.

### Changed
- **Mode dropdown consolidated.** "Email — coming soon" + "Bullets — coming soon" replaced with a single disabled "More coming soon" entry. Cleaner, less repetitive — and when we ship Email / Bullets we can list them properly without retiring the placeholder line by line.

## [3.14.20] - 2026-05-13

### Fixed
- **"Waffler" brand wordmark and journal body text didn't match the website's typography.** The cream-theme CSS referenced Source Serif 4 (via `--serif`) for the brand wordmark, journal entry body text, and date dividers — but the font file wasn't actually loaded anywhere in the app, so the cascade silently fell through to Georgia. The result: subtle but visible "off-brand" feel compared to wafflerai.com. Added a Google Fonts `@import` at the top of `ui/style.css` that loads both **Source Serif 4** (serif) and **Inter** (UI sans) in the same weights the website uses. If the app is offline the cascade still falls back to Georgia / system fonts cleanly — but since the app needs internet for API calls anyway, the fonts will be there whenever the app is functional.

## [3.14.19] - 2026-05-13

### Fixed
- **Rate-limit toast was unhelpfully generic.** When Groq's daily token quota was exhausted and the user had no Cerebras / OpenAI fallback configured, the styler would fall through, try ``self.client.chat`` on a ``NoneType`` (because the OpenAI client wasn't set up), catch the resulting ``AttributeError``, and pass *that* meaningless error as the fallback reason. The toast then said: <em>"Cleanup skipped — Pasted raw. See the log for details."</em> — telling the user nothing about what actually happened or what to do.

  Two-part fix:
    1. **Styler now skips OpenAI entirely when ``self.client is None``** (instead of crashing on a NoneType call), tracks every provider's failure in an explicit ``failures`` list, and surfaces the most actionable reason — rate-limit messages beat auth/connection/other in priority. When a provider is in cooldown from a previous 429 but hasn't been called this dictation, a synthetic ``RATE_LIMIT|cooldown|<wait>s|<provider>`` reason is generated so the toast can still explain why.
    2. **Toast wording rewritten** to always be actionable. Every branch now tells the user (a) what happened, (b) what to do RIGHT NOW (add a specific fallback key in Settings → API Keys), and (c) the alternative (wait for the limit to reset, with the actual reset time). Example: <em>"Groq daily token limit hit. Pasted raw text — styling skipped because Groq's daily token limit resets in about 3h 24m. Add a Cerebras or OpenAI key in Settings → API Keys as a fallback, or wait for the limit to reset."</em>
  Headings also got more specific — ``"Cleanup skipped"`` is now ``"Groq daily token limit hit"`` / ``"Connection failed"`` / ``"Auth blocked"`` / ``"No styling provider"`` depending on what actually went wrong.

## [3.14.18] - 2026-05-13

### Fixed
- **Vocabulary / Settings pages had no way back.** The Journal top-nav was nested inside `<main id="mainArea">`. When the user clicked "Vocabulary" or "Settings", `showPage()` hid `mainArea` to reveal the relevant panel — which also hid the navigation, trapping the user on those pages with no way back to Journal. Moved the top-nav out of `mainArea` so it's a sibling of all three panels, persistently visible regardless of which page is active. Restructured `.app` as a column-flex container (top-nav on top, the active panel filling beneath) and overrode the old `height:100vh` on `.settings-panel` / `.vocabulary-panel` so they fill the remaining height instead of overflowing the viewport.

## [3.14.17] - 2026-05-13

### Fixed
- **Journal page entries didn't fill the window.** The Journal layout shipped in v3.14.16 had `max-width: 760px; margin: 0 auto` on the entry container, so the white cards looked orphaned in the middle of a wider window while the top-nav and stat strip stretched edge-to-edge. Removed the constraint on the page container — entries now stretch flush with the strip above. Body text inside each card is still capped at `75ch` (≈ classic reading width) so long paragraphs don't sprawl across an ultrawide monitor, but the card itself fills the available width.

## [3.14.16] - 2026-05-13

### Changed
- **Complete redesign — Journal layout.** Goodbye sidebar, hello editorial. The home view is now a writer's-notebook layout: top-nav across the window (Brand · Journal · Vocabulary · Settings · Listening-for-Fn pill on the right), a stat strip beneath it, then a serif "journal page" of white-card entries with italic-serif timestamps, gold-pill word-count chips, and Copy / Show original buttons.
- **🥞 Stack streak chip.** Right side of the stat strip — three little stacked waffles with a tiny syrup pat on top, "**N** stack streak" beside. Subtly wobbles every ~5s. Hidden entirely when the streak is 0 so first-time users don't see a "0 stack streak" eyesore. Powered by a new `streak_days` field in `get_stats()` that counts consecutive calendar days with at least one history entry; today's empty state preserves the streak so it doesn't snap to 0 at midnight — only breaks once a full day passes without dictating.
- **Mode selector** moved to a compact pill in the top nav (was bottom of sidebar). Mic selector moves to Settings (it's a one-time configuration, not a frequent toggle).
- **Listening indicator** is now a single amber pill with a "Listening for [Fn]" caption + animated dot; flips green when recording and amber-on-amber when processing.
- **Empty-state copy** updated: "Your journal is empty." + "Press your hotkey and speak — the first entry will appear here." 🥞

### Internal
- Existing `.transcript-card` / `.card-time` / `.card-words` / `.btn-copy` / `.text-toggle` class names preserved — restyled inside `.j-page` rather than renamed — so `app.js`'s `makeCard()` renderer keeps working without edits.
- Update-banner mount fallback added (looks for `.sidebar` first, then `.journal`) so the auto-update notification still appears in the new layout.

## [3.14.15] - 2026-05-13

### Fixed
- **Mac `SIGSEGV / EXC_BAD_ACCESS at 0x400` on wizard→pipeline handoff.** v3.14.14 fixed the CFFI closure lifetime *within* a single `AudioRecorder`, but the wizard runs its OWN `AudioRecorder` (`_wizard_recorder`) and the main `WafflerPipeline` then constructs a SECOND `AudioRecorder` immediately afterwards. The wizard cleanup path (`wizard_stop_hotkey_test`) was just doing `_wizard_recorder.stop()` (which only flips `is_recording=False` and snapshots the buffer — it does NOT close the stream) and then `_wizard_recorder = None` to drop the reference. CoreAudio's HAL I/O thread could fire one last callback into the freed wizard CFFI closure while the pipeline's brand-new `InputStream` was being constructed milliseconds later. Crash trace: `pythonify_c_value` → `method_stub` → `ffi_closure_SYSV` → `AdaptingInputOnlyProcess` → `HALC_ProxyIOContext::IOWorkLoop`, exactly as predicted.

  Two-part fix:
    1. **Process-wide `_STREAM_LOCK` in `audio.py`** serialises `sd.InputStream` creation and teardown across *all* `AudioRecorder` instances. `_create_stream()` and `_teardown_stream(stream)` both hold the lock for their full duration — so any concurrent stream creation transitively waits for the outgoing stream's HAL thread to fully drain. The lock window is ~100ms; invisible in normal use, exactly where it needs to be at instance-to-instance handoffs.
    2. **`wizard_stop_hotkey_test` now calls `_wizard_recorder.shutdown()`** (full stop → drain → close sequence, bounded by an internal 2s watchdog) BEFORE setting `_wizard_recorder = None`. Combined with the lock above, the pipeline's pre-warm `start_monitoring()` call cannot begin until the wizard's stream is fully closed.

  Added `tests/test_audio_stream_lifecycle.py` — a 50-iteration tight-loop stress test plus a 20-iteration overlapping-create/teardown loop that reproduces the exact wizard→pipeline race. Without the v3.14.15 fix this script reliably segfaults on Mac within a handful of iterations; with the fix it completes cleanly.

- **macOS overlay pill unreliable on Space changes.** Users reported "I activate it and switch to another window and the pill doesn't appear there — it eventually follows but it's flaky on some Macs." `NSWindowCollectionBehaviorCanJoinAllSpaces` is supposed to make the overlay show on whichever Space is currently active, but macOS silently "forgets" that flag intermittently, especially when the overlay's owning app isn't frontmost. Added a low-frequency re-assertion to the overlay's animation tick: every ~250ms (5 ticks at 50ms) we call `orderFrontRegardless()` while the pill is visible. CoreGraphics no-ops if the window is already topmost on the current Space, so the cost is negligible; the win is that the pill snaps onto the active Space the next tick after a switch.

## [3.14.14] - 2026-05-13

### Fixed
- **macOS `EXC_CRASH/SIGABRT` from PortAudio HAL callback (separate from the v3.14.13 segfault).** Different crash signature: `Py_FatalErrorFunc` → `abort()` on the `com.apple.audio.IOThread.client` thread, with the C trace going `convert_to_object` → `general_invoke_callback` → `ffi_closure_SYSV_inner` → `AdaptingInputOnlyProcess` → CoreAudio HAL. Trigger correlated with a Groq `RateLimitError` styling failure, but the rate-limit was incidental — the real bug was that sounddevice's CFFI closure could be invoked by CoreAudio's I/O thread for a brief window after `stream.stop()` had returned. If the `InputStream` Python object was GC'd (or the bound callback method was dropped) during that window, the next HAL callback fired into freed memory and CFFI hit a fatal Python state error.

  Rewrote `src/audio.py`'s stream lifecycle with a strict teardown sequence:
    1. `_callback_active = False` — Python-level guard so even a late HAL callback returns immediately.
    2. `stream.stop()` — stop dispatching new audio.
    3. `time.sleep(0.1)` — drain window for any in-flight HAL callback to complete.
    4. `stream.close()` — release C-level resources and the CFFI closure.
    5. Drop the Python reference last — bound method survives steps 2-4.

  Also cached the bound `_callback` method as a stable `self._callback_bound` attribute in `__init__` so PortAudio's CFFI closure always points at the same Python object for the recorder's lifetime — previously every `self._callback` access created a new bound-method object, which made GC behaviour around stream replacement non-deterministic. Switched `_stream_lock` to `RLock` so the new `_teardown_stream(stream)` helper can be called while the caller already holds the stream lock. `shutdown()` watchdog timeout bumped from 1.5s → 2.0s to cover the new drain window.

- **Cerebras "page does not exist" 404 on the wizard's "Get a key" link.** The link pointed at `cloud.cerebras.ai/keys` which returns a 404 from Cerebras's developer platform. Updated to the correct path `cloud.cerebras.ai/platform/api-keys` in the wizard, the settings panel description, and the dynamic settings hint — matching the URL already used in `wafflerai.com/api-key-guide`.

### Changed
- **Wizard Step 3 — Cerebras now has its own animated walkthrough.** Same three-card story as Groq (open → sign in → copy key) but with Cerebras's dark theme and orange-accent (`#FF6B00`) branding, the correct `cloud.cerebras.ai/platform/api-keys` URL in the mock browser titlebar, and a `csk-` key prefix in the reveal modal. Shares animation CSS with the Groq walkthrough so only one provider's cards animate visibly at a time (whichever tab is active).

## [3.14.13] - 2026-05-13

### Fixed
- **macOS segfault on recording end (multi-CGEventTap race).** Reported in `~/.waffler-hosted/crash.log` — `Fatal Python error: Segmentation fault` with `<no Python frame>` on the crashing C thread while two other threads were live inside `CFRunLoopRun()`. Root cause: `SmartHotkeyListener` was composing 2–3 independent HID-level `CGEventTap`s in one process (one for the hotkey via `FnKeyMonitor` or `MacHotkeyMonitor`, one for Space, one for Esc cancel). When the C callbacks for two taps fired near-simultaneously — for example "paste finishes + key release" at the end of a recording — PyObjC's bridge state raced between the two C threads and macOS sent SIGSEGV. The Esc-cancel monitor was added during the v3.14 series, which is exactly when the regression appeared (v3.12.3 only had one tap and didn't crash).

  Refactored to a **single** `CGEventTap` per process (`MacEventTap`) that dispatches incoming events to multiple lightweight handler objects on one `CFRunLoopRun()` thread:
    - `FnHandler` — Fn flag changes + external-keyboard Fn (F13/F14/F15), always suppresses Fn flag changes to prevent the emoji picker.
    - `SpaceHandler` — Space key, fires unconditionally so `SmartHotkeyListener` can decide whether it's a sticky toggle, suppresses Space when Fn is held (preserved Fn+Space sticky-toggle semantics).
    - `GenericHotkeyHandler` — arbitrary modifier+key combos with configurable suppression. Used for non-Fn hotkeys, the Space trigger for non-Fn hotkeys, and Esc cancel.

  All existing behaviour preserved: Fn push-to-talk, Fn+Space sticky toggle, Esc as universal sticky-cancel, Space as sticky trigger for non-Fn hotkeys, surgical modifier suppression so Cmd+C / Cmd+V keep working. `fn_key_cgevent.FnKeyMonitor` kept as a thin backward-compat shim (over the same single-tap architecture) so `app.py`'s startup permission probe and `tests/test_fn_key.py` still work without edits. Wizard polling surface preserved via property aliases (`_fn_pressed`, `_hotkey_active`, `is_combo_active`).

  Windows hotkey path untouched.

## [3.14.12] - 2026-05-13

### Changed
- **Wizard Step 3 — animated three-step Groq onboarding scenes.** Step 3 (API keys) used to be the hardest hurdle for users who'd never created an API key before. Added three illustrated story cards above the Groq input that walk through the actual flow with looping CSS animations:
  1. **Open the link** — mock Groq dashboard (dark theme, real "groq" wordmark in their orange `#F55036`, sidebar with "API Keys" highlighted) with an animated cursor that glides to the "+ Create API Key" button and clicks it; the button briefly depresses and a red ripple ring expands.
  2. **Sign in (free)** — modal with the Google G logo "Continue with Google" button, an email field that **types `you@example.com` character-by-character** with a blinking caret that moves with the text, a red "Sign in" button that briefly glows red, and a green check badge that pops in at the corner.
  3. **Copy + paste it here** — dark key-reveal modal with the "shown only once" warning, a `gsk_...` key with a **light shimmer sweep** across it, a green Copy button that briefly depresses, a "✓ Copied!" toast that slides up and fades, and a gold down-arrow that pulses toward the paste field below.

  Cards are staggered ~1s apart so the row reads left → right as one continuous demonstration, and a small gold dot travels along each connector arrow between cards to reinforce the direction. Cards stack vertically on narrow widths. Respects `prefers-reduced-motion`.

## [3.14.11] - 2026-05-13

### Fixed
- **macOS Accessibility pill never ticked green even after granting permission.** `check_accessibility_permission()` called PyObjC's `AXIsProcessTrusted()`, which has a long-standing issue under PyInstaller-bundled apps: once it returns `False` early in the process lifetime it tends to keep returning `False` for the rest of the run, even after the user toggles Accessibility ON in System Settings — the result appears to be cached inside the PyObjC interop layer. Input Monitoring was unaffected because it goes through `IOHIDCheckAccess` via raw ctypes. Rewrote the Accessibility check to use ctypes against `ApplicationServices.framework` directly: it now tries `AXIsProcessTrustedWithOptions(NULL)` first (the modern API explicitly designed for repeated polling), then falls back to `AXIsProcessTrusted()` via ctypes, and only uses the PyObjC binding as a last resort. Each call re-queries TCC live, so granting Accessibility in System Settings now ticks the wizard pill green within the next 1-second poll tick.

### Changed
- **Wizard Step 1 — proper Apple-style permission icons.** The Accessibility tile now shows the real macOS Accessibility glyph (blue gradient square with the white universal-access figure) and the Input Monitoring tile shows the dark slab with a detailed keyboard glyph, matching what users actually see in System Settings. Gold tint removed for these icons since they're now real system icons.
- **Wizard Step 1 — Accessibility now has its own animated walkthrough.** Previously only Input Monitoring had a mock-System-Settings animation showing how to add Waffler. Accessibility now has a parallel walkthrough labelled "Privacy & Security → Accessibility", offset ~3 seconds from the Input Monitoring one so the two windows feel alive independently.
- **Wizard animation apps swapped to recognisable real apps.** Replaced Karabiner-Elements / Terminal / AltTab with apps the average user actually has installed: Accessibility window shows Google Chrome, 1Password, then Waffler being added; Input Monitoring window shows Spotify, Discord, then Waffler being added. The application picker shows Zoom / Spotify / Waffler / WhatsApp / Discord with their proper brand-coloured SVG logos instead of emoji placeholders. The Waffler icon throughout uses the real bundled `logo-icon.png` instead of the 🧇 emoji.

## [3.14.10] - 2026-05-13

### Fixed
- **Post-setup crash, take 3 — different crash, same wizard.** v3.14.9 fixed the SSL access-violation by monkey-patching `httpx.create_ssl_context` and running `_initialize_pipeline()` synchronously on the IPC thread for defence-in-depth. The synchronous version turned out to introduce a DIFFERENT crash: the IPC call blocked for 2-3 seconds while OpenAI/Cerebras clients were constructed, and EdgeChromium WebView2's GUI thread crashed in C code during that block (confirmed via crash dump — no Python frame in the crashed thread, only `evaluate_js` threads stuck on `threading.acquire`). Reverted `complete_setup()` to spawn `_initialize_pipeline()` in a background thread again. The SSL monkey-patch from v3.14.9 keeps that path safe because every httpx client now reuses the same pre-built main-thread SSL context regardless of which thread constructs it. So: SSL crash gone (v3.14.9 fix), WebView2 crash gone (v3.14.10 fix), and the user gets a snappy wizard close instead of a 3-second freeze.
- **Wizard Step 1 hotkey detection on Windows.** `initFnKeyFeedback()` had `if (!document.getElementById('wizHotkeyBadge')) return;` at the top, left over from the pre-v3.14.5 wizard. After the wizard redesign, the Windows keycaps were renamed (Ctrl has no id, Win is `wizHotkeyBadgeWin`) so `wizHotkeyBadge` no longer existed → early return → `startFnKeyPolling()` never ran → `get_fn_key_state()` was never called even though the Python hook was happily firing PUSH_TO_TALK on every press (visible in `hotkey.log`). Removed the early return; polling now always starts when entering Step 2 of the wizard.
- **Wizard "Next" button hidden below the fold.** The wizard's Back/Next nav was a normal flow element and got pushed below the viewport on shorter windows. Made it `position: sticky; bottom: -4px;` with a cream gradient fade behind it so it's always pinned visible regardless of scroll position. Also tightened the hotkey-stage padding and instruction-tile heights to reduce total wizard height.

## [3.14.9] - 2026-05-13

### Fixed
- **Post-setup crash, take 2 — the real fix.** v3.14.5 added a main-thread `ssl.create_default_context()` pre-warm, on the theory that warming the SSL stack once would make later background-thread calls safe. That theory was wrong — `httpx` creates a fresh SSL context **per client**, so every `OpenAI(...)` constructor in a worker thread re-triggered the same crashing `ssl.create_default_context()` call. (Confirmed via the latest crash log: SSL context creation still showed up in the access-violation thread even though "SSL stack pre-warmed on main thread" appeared in `app.log`.) The real fix has two parts: (1) build ONE `ssl.SSLContext` on the main thread using `certifi.where()` as the cert source — this avoids the Windows cert-store call that's actually crashing — and (2) monkey-patch `httpx._config.create_ssl_context` so every httpx client reuses that same pre-built context regardless of which thread the client is constructed on. (3) As defence-in-depth, `complete_setup()` no longer spawns a fresh background thread for `_initialize_pipeline()`; it runs synchronously on the IPC thread instead, so we don't go three levels deep from main thread when constructing OpenAI clients.

## [3.14.8] - 2026-05-13

### Fixed
- **Waffle overlay finally appears during the wizard Try-It step.** The `wizard_start_hotkey_test()` path was explicitly setting `_wizard_overlay = None` with a comment about "preventing threading crashes" from an older codebase. The overlay actually runs as a separate subprocess (GIL-independent), so this defensive skip wasn't needed any more. Now it instantiates `RecordingOverlay()` and calls `prestart()` inside a try/except — if the spawn fails for any reason, recording still works but without the pill, so the wizard can't be bricked by an overlay glitch. This was the long-standing "overlay doesn't appear in Step 3" complaint.

### Changed
- **Step 1 hotkey instructions redesigned as three clean tiles.** Replaced the prose paragraph under the listening pill with three centred tiles: `Hold to record` (keys on top, title under, no description), `Release to stop` (no keycap chip at all, just the title centred and slightly larger), and `Sticky mode` (Space + hotkey on top, title, short caption: "Press Space to lock recording. Press the hotkey again to disable.").
- **Hotkey order swapped to `Ctrl + Win`** in the wizard (Windows) on Step 1, Step 4 try-it badge, and the mock chat placeholder — user preference.
- **Listening pill flips green** when keys are pressed (was previously only the body text changing). Keycaps also depress visually.
- **"TRY SAYING" label** in the Try-It step is now a bold gold-tinted pill with letter-spacing instead of small italic text.
- **The waffle in the Try-It explainer animates** with the same speech-wave cell-darkening as the homepage instead of being a static pattern.

## [3.14.7] - 2026-05-13

### Fixed
- **Auto-updater stuck at 0% on every platform (real root cause, finally).** Traced the bug by writing a standalone test script and running the updater module directly: `start_download()` was self-deadlocking on `_state_lock`. The function acquires the lock with `with _state_lock:` and then, while holding it, calls `_reset_state()` which *also* does `with _state_lock:`. `threading.Lock` is not reentrant, so the second acquire blocks forever on the same thread. The worker thread spawn at the end of `start_download()` was never reached, the request was never made, and `get_progress()` returned `total_bytes=0, downloaded=0` indefinitely — which the UI rendered as "0%". This has been the cause of every "stuck at 0%" report since v3.13.0. Both the `curl`-on-Mac path (v3.14.3) and the `requests`-on-Windows path (original) worked perfectly when invoked directly; they just never ran. Fixed by switching `_state_lock` to `threading.RLock` (reentrant). Verified end-to-end: 32 MB v3.14.6 installer downloads in ~2.5 seconds on Windows.

### Added
- **Usage section breakdown by provider.** Settings → Usage now shows four time buckets (Today / This Week / This Month / All Time) instead of just two, and a per-provider breakdown card listing Groq / Cerebras / OpenAI side-by-side with their accent colour, count of API calls, total cost, and a horizontal bar showing each one's share of overall spend. New `by_provider` field in `get_usage_stats()` aggregates the existing per-entry `provider` field that was already being recorded on every call but never surfaced anywhere.

## [3.14.6] - 2026-05-13

### Fixed
- **Email mode no longer selectable.** The sidebar Mode dropdown still listed Email as a clickable option even after we'd reframed it as "coming soon" on the website. Reverted to one active mode (Normal) plus two disabled "coming soon" placeholders (Email, Bullets). `get_modes()` IPC now returns only the active set, and `get_current_mode()` falls back to "normal" if a previously-persisted Email selection is found, so old installs migrate cleanly.
- **Fn preset shown on Windows.** The wizard's "Use a different hotkey" config panel rendered the Mac preset list (Fn / Cmd+Shift / Option+Shift) regardless of platform. Windows users could "select" a Fn key that doesn't exist on their hardware. Now `showWizardHotkeyConfig()` renders Mac-only presets on Mac and Windows-only presets (Win+Ctrl / Ctrl+Shift / Ctrl+Alt+Space) on Windows.

## [3.14.5] - 2026-05-13

### Fixed
- **Wizard hotkey step never auto-advanced on Windows.** `get_fn_key_state()` was looking for `_wizard_step2_monitor._monitor._fn_pressed`, but on Windows the wizard monitor *is* a `WindowsHotkeyListener` which exposes `is_combo_active` as a property directly — there's no inner `_monitor` wrapper. So on Windows every poll returned `pressed=False` no matter what the user pressed, and the "press your hotkey" step could only progress if the user noticed the Next button was already enabled (the manual `case 2: btn.disabled = false` override). Now uses `is_combo_active` on Windows, falls back to the inner-monitor lookup on macOS. The step now visibly registers the press and auto-advances after 1s.
- **App crashed on the very first dictation after finishing setup.** The OpenAI Python client (via httpx) calls `ssl.create_default_context()` the first time it constructs a client. When that first call happens on a background thread in a PyInstaller-bundled app on Windows, it can segfault the whole process — and the pipeline is initialized from `_initialize_pipeline()` running in a thread spawned by `complete_setup()`. Fixed by pre-warming the SSL stack with a no-op `ssl.create_default_context()` on the main thread at app startup, before any worker threads exist.

### Added
- **Waffle overlay explainer in the Try-It step.** The floating waffle pill that appears on screen during dictation was previously a mystery to first-time users — they'd press the hotkey, see a small gold square pop up somewhere, and not know what it was. The Try-It step now includes a labelled preview: a bobbing waffle with a pulse ring and a short caption ("A floating pill shows on screen while you're recording. The squares darken with the volume of your voice…"). Sits below the "Waiting for you to dictate" status so it's visible before they press the hotkey.

## [3.14.4] - 2026-05-13

### Fixed
- **Windows wizard was rendering the (Mac-only) permission step.** v3.14.3 introduced a CSS rule `#wizContent1 { display: grid !important }` to lay the Accessibility + Input Monitoring cards out side-by-side on wide screens. The `!important` overrode the JS-applied `style="display:none"`, so on Windows the permission cards leaked onto Step 2 of 3 (the hotkey step) and then onto Step 3 (API keys). Scoped the rule to `body[data-wiz-step="1"]` so it can only apply when Step 1 is genuinely active — and on Windows the wizard never reaches Step 1, so the rule never fires.
- **Wizard now fills the full app window.** Container max-width bumped from a fixed 1080px to `min(1400px, calc(100vw - 64px))` (1500px on the wide Try-It step). On a typical desktop window the wizard now occupies the whole canvas instead of floating as a small modal in a sea of cream.

### Added
- **SaaS-style polish on the wizard:** smooth fade + slide transition between steps (no more hard cuts), an idle "shine" gradient sweep across active progress segments, hover-lift on provider tabs, and a continuous ripple ring behind the "Listening for hotkey…" pill so it visibly listens instead of just sitting still. A subtle warm radial gradient at the bottom of each step adds visual weight without being noisy.
- **Inline "Need help?" guide link on the API-keys step** that opens `wafflerai.com/api-key-guide` in the user's default browser. Makes the recommended Groq → Cerebras → OpenAI ordering self-serve to learn about, without leaving the wizard hanging if a user has never used any of the three.

## [3.14.3] - 2026-05-13

### Added
- **Cream theme for the main app.** Replaces the previous black-on-grey dark UI with the same cream/gold/warm-dark palette as the new wizard and the website. New installs default to Cream so the wizard-to-app handoff feels seamless. **Settings → Appearance** now has a three-option picker: Cream (default) / Dark (the original look) / System (follows the OS via `prefers-color-scheme`). Choice persisted to `localStorage` so it survives restarts. The entire app uses CSS variables, so the theme is swapped instantly without a reload — sidebar, history list, settings, vocabulary, hotkey badges, even the toast all repaint correctly.
- **Bigger wizard window.** Wizard container max-width bumped from 800px to 1080px (and 1200px for the wide Try-It step) so the Accessibility and Input Monitoring permission cards (with the embedded animated walkthrough) fit on a single screen without scrolling. On screens ≥980px wide, Step 1 now lays the two permission cards out side-by-side as a 2-column grid instead of stacking them.

### Fixed
- **Mac auto-updater stuck at 0% — for real this time.** v3.14.1 redirected Mac users to the website as a workaround because the PyInstaller-bundled `requests`/SSL stack on macOS has a long-standing issue where the HTTPS connection establishes and `content-length` arrives but body chunks never reach `iter_content()`. The new `src/updater.py` shells out to `/usr/bin/curl` on Mac instead — curl is shipped with macOS, uses the system's SSL trust store, and works on signed-redirect URLs from GitHub Releases. Progress is reported by polling the partial file size every 400ms. The in-app **Download & Install** button is now back on Mac (no more "Open Download Page" workaround); the install + relaunch still uses the existing `hdiutil` + `cp -R` shell script.

## [3.14.2] - 2026-05-12

### Changed
- **Setup wizard fully redesigned** to match the website's branded look. The previous dark-grey/black wizard with stacked permission cards, scrollable triple-stacked API key fields and an under-stated mock chat has been replaced with a cream-background, gold-accented flow:
  - Header now has the real Waffler logo + brand mark on the left and a discrete step-counter pill on the right.
  - Progress bar uses a gold gradient on active segments instead of flat green.
  - Step 1 (Permissions) uses two compact white cards with status pills that auto-tick green via background polling. The Input Monitoring card embeds an always-visible animated mini-screencast showing exactly how to click `+` → pick Waffler → click Open → flip the toggle, with proper Karabiner-Elements + Terminal icons in the list.
  - Step 2 (Hotkey) shows a single floating plastic keycap (white/silver with black `fn` label on Mac, `Win + Ctrl` combo on Windows) that gently bobs up and down. A gold "Listening for hotkey press…" pill replaces the previous static grey hint.
  - Step 3 (API Keys) replaces three permanently-visible stacked input fields with three pill-style provider tabs (Groq · Cerebras · OpenAI). Only one input is on screen at a time, ticked green automatically on valid paste. Groq is the recommended default to match the website's chain order. Step height shrunk by ~60%.
  - Step 4 (Try It) gets a polished cream card with mini keycap hint, a gold dashed "Try saying" suggestion, and a fully redesigned mock Messages app with proper traffic-light dots and rounded message bubble. The Finish-Setup button is now green so the win at the end feels like a win.

### Fixed
- **Input Monitoring permission detection is now reliable.** The previous `CGEventTapCreate`-based check returned non-null even when Input Monitoring was denied (as long as Accessibility was granted), which meant the wizard always reported "granted" once Accessibility was on. Replaced with Apple's canonical `IOHIDCheckAccess(kIOHIDRequestTypeListenEvent)` via `ctypes`. Returns 0 (granted) / 1 (denied) / 2 (unknown), and we treat only 0 as granted. The wizard now polls this every second while step 1 is open and the status pills flip green the moment macOS reports the permission as granted — no need to click "Recheck".

## [3.14.1] - 2026-05-12

### Fixed
- **Mac auto-update stuck at 0%.** The in-app download worker on macOS would establish the HTTPS connection, read the `content-length` header, then never receive body chunks — leaving the progress bar frozen at 0% indefinitely. Root cause is a PyInstaller-bundled `requests`/`urllib3` quirk on macOS that mishandles streamed response chunks from GitHub's signed-redirect CDN. Rather than fight platform-specific bundling bugs, the Mac flow now opens the website's `/download/` page directly when an update is detected — the standard "drag DMG into Applications" workflow that's been working reliably since v3.13.0. Windows keeps the in-app silent install via Inno Setup, which has no such bug.
- **"Download in browser" now opens the website, not the GitHub release page.** Previously the fallback button (and the sidebar update banner) sent users to a raw `github.com/.../releases/tag/v3.X.Y` page with terse asset names. They now land on `https://wafflerai.com/download/` which has the proper download UI, system requirements, and OS detection — the surface we control.

## [3.14.0] - 2026-05-12

### Changed
- **Cerebras default model upgraded to Qwen-3 235B.** Was `llama-3.1-8b` (too small to follow nuanced styling rules — collapsed long exploratory speech and hallucinated greetings like "Hi, I have power toys installed" from rambling input). Probed the user's key and found Llama 3.3 70B is no longer available on Cerebras (404 across all name variants); Qwen-3 235B is the smartest accessible model (3.4× the parameter count of Groq's 70B Llama, comparable instruction-following quality). Sub-second styling on long inputs at paid tier. `CEREBRAS_MODEL` env var still overrides.
- **Provider chain re-ordered: Groq → Cerebras → OpenAI.** Was Cerebras → Groq → OpenAI. New order spends Groq's 100K-token-per-day free tier first before any paid Cerebras tokens, then falls through to Cerebras Qwen-3 235B (paid, dedicated queue), then OpenAI gpt-4.1-mini as last-resort safety net. Most light/moderate users now pay £0/month for styling; heavy users still pay a fraction of subscription voice-to-text apps.
- **Removed the "≥200 words → gpt-4.1 (full)" routing.** The original rationale (Cerebras `llama-3.1-8b` couldn't handle long inputs reliably) no longer applies now Cerebras runs Qwen-3 235B. Benchmarked Qwen-3 235B vs gpt-4.1 full on 5 real-world long transcripts (378-732 words): quality essentially equivalent, Cerebras 6.4× faster overall (11.8s total vs 74.8s total, 14.9× faster on the fastest test). OpenAI fallback now always uses gpt-4.1-mini, dramatically cutting the rare-OpenAI-fallback cost.

### Added
- **Em-dash and en-dash stripping** as the new top-of-pipeline post-processor in `style_openai.py`. Big LLMs (Qwen-235B especially) love em-dashes (—) and en-dashes (–); they're the loudest "AI wrote this" tell in styled output. Two-layer defence: (1) hard rule added to `prompts/normal.txt` and `prompts/email.txt` forbidding em/en-dashes with worked examples, (2) `_strip_em_dashes()` regex post-processor that replaces `\s*[—–]\s*` with `, ` regardless of what the model emits. Hyphens (-) inside compound words (`voice-to-text`, `gpt-4.1-mini`, `Ctrl+Alt+S`) are explicitly preserved.

### Fixed
- **"Exploratory speech collapses into a single conclusion" bug.** Cerebras Llama 3.1 8B on multi-clause thinking-out-loud transcripts ("let's say I do X… I just want to Y… is it already Z…") would drop 95% of content and add a hallucinated "Hi" greeting. Two fixes: (a) the model upgrade to Qwen-3 235B (bigger model holds the line), (b) a new HARD RULE in `prompts/normal.txt` explicitly forbidding the over-aggressive "abandoned restarts" interpretation, with the user's actual failing transcript captured verbatim as a NEGATIVE worked example. Tighter abandoned-restart definition: requires audible mid-word abort, not topic-meandering.
- **Cerebras `queue_exceeded` 429 distinguished from RPM 429.** Free-tier Cerebras users would see "high traffic" capacity-shed and the styler would mark Cerebras as failed for a full 5 minutes, breaking the speed advantage for half the day. Now both 429 shapes are recognised and given short 20-second cooldowns so the next dictation gets a fresh attempt.

### Added
- **Cerebras as primary styling provider.** New three-tier fallback chain: Cerebras Llama → Groq Llama → OpenAI gpt-4.1-mini. Cerebras hosts Llama models on wafer-scale chips at ~3000+ tokens/sec output — significantly faster than Groq. Free tier ~1M tokens/day. Defaults to `llama-3.1-8b` (free tier); `CEREBRAS_MODEL` env var lets users upgrade to `llama-3.3-70b` if they're on a paid tier. Each provider has its own monotonic-clock skip-until deadline that pauses further attempts after a 429 — Cerebras's "high traffic" 429 gets a short 20s cooldown so the next dictation retries, while genuine quota hits get 2 minutes. Wired into the setup wizard step 3 as the recommended primary, and into the Settings panel API Keys section as the top row. `validate_cerebras_key()` IPC method handles the awkward case where Cerebras rate-limits the validation itself (saves the key provisionally so setup isn't blocked).
- **Visible progress feedback on the recording pill.** During the slow post-recording stages (transcribing and styling), the pill now shows `"Transcribing… 1s"` and `"Styling… 5s 10s…"` with a ticking elapsed counter. Eliminates the "is it frozen?" feeling on long dictations. The label clears automatically when a new recording starts so VU bars take over again.
- **Auto-routing of OpenAI model by input length.** Inputs ≥ 200 words go to `gpt-4.1` (full), which generates output faster per token. Shorter inputs stay on `gpt-4.1-mini` because mini is already fast enough and meaningfully cheaper. `OPENAI_STYLE_MODEL` env var still pins exact models for power users.
- **500ms audio pre-roll buffer.** The audio stream is now pre-warmed at pipeline init and kept alive across recordings. A continuous ring buffer captures the last 500ms of audio, spliced into every new recording. Result: first 1-2 syllables of speech are no longer clipped on every dictation (was a 50-300ms stream-creation latency on Windows).
- **150ms audio post-roll buffer.** When the user releases the hotkey, recording keeps capturing for another 150ms so the final word / syllable isn't clipped sitting in the OS audio buffer.
- **Dual-key setup wizard.** Step 3 of the wizard now shows all three provider key fields together (Cerebras + Groq + OpenAI) instead of forcing a single choice. Users can fill in any combination during initial setup. Any one validated key unlocks the Next button.
- **`OPENAI_STYLE_MODEL` and `CEREBRAS_MODEL` env vars** for advanced users who want to override the auto-routed model selection.
- **Self-correction force-LLM markers in `_is_simple()`** — short transcripts that contain self-correction markers (`"sorry I mean"`, `", no <word>"`, `"actually"`, `"I meant"`, `"hmm no"`, `"scratch that"`, `"what I'm trying to say"`, etc.) now bypass the regex-only `_basic_clean` cleaner and go to the LLM, which can actually drop the wrong half. Previously short corrections like `"Tuesday, sorry I mean Monday"` would keep both versions in the output. The corpus now covers 24 self-correction scenarios across day-of-week, name, number, place, multi-step chains, and structural (numbered list / email body / question) contexts.
- **Profanity restoration safety net.** OpenAI's gpt-4o-mini and gpt-4.1-mini have safety training that sometimes drops swear words even when the prompt explicitly forbids censoring. New `_restore_censored_profanity()` runs after every styling call: if a swear word appears in the raw transcript but not in the styled output, it gets spliced back at the correct position by anchoring on the word immediately before the swear in raw (tracking which occurrence of that word, in case it appears multiple times). Covers the common UK/US swear lexicon.
- **Greeting auto-line-break for email-style input.** When the transcript starts with `Hi <Name>,` / `Hello team,` / `Hey Rohan,` / `Dear Sam,` / `Morning all,`, Normal mode now puts the greeting on its own line followed by a blank line before the body. Mirrors the existing sign-off split. False-positive guards in place for meta-language ("Hi guys would never work as an opener").
- **Sign-off auto-split.** `Cheers, James` / `Regards, James` / `Thanks again, James` etc at end of transcript split into two lines (sign-off line + name line) for proper email shape. False-positive guard for mid-body sign-off words.

### Fixed
- **OpenAI styler was using `gpt-4o-mini` despite the upgrade to `gpt-4.1-mini`.** The OpenAIStyler constructor default was changed correctly, but the caller in `app.py` was passing `model="gpt-4o-mini"` explicitly, silently overriding every model upgrade. Removed the explicit override so the constructor default takes effect.
- **`max_tokens=512` truncation on long dictations.** The styler was hardcoded to 512 output tokens (~380 words) regardless of input length, silently truncating long dictations mid-sentence. Output budget is now sized against input length (`max(1024, min(8192, input_words * 3))`).
- **Word-level stutters in short transcripts.** `_basic_clean` now collapses literal token-repeats like "I I", "the the", "we we" before returning. Punctuation between repeats blocks the collapse on purpose ("I, I think" might be a deliberate restart).
- **Solo `"number three"` was being converted to `"1."`** when no second item was dictated. The numbered-list rule now requires at least two explicitly counted items in the input before firing. Solo count phrases (references like "step five is wrong" or "Number one priority is X") stay as prose.
- **Vocab case-correction silently no-op'd.** When Whisper transcribed a vocab word in lowercase (e.g. `cobie` instead of `COBie`), the fuzzy-matcher recognised the exact case-insensitive match but emitted no correction — the lowercase form slipped through. Fixed to emit a `(token → canonical)` correction whenever the input case differs from the vocab entry's canonical case.
- **Numbers, times, dates, units, currency, versions and acronyms** are now strictly preserved (`3pm` stays `3pm`, not `3 PM`; `230ms` stays joined; `12th May 2026` doesn't get its month lowercased). The FORMATTING rule now spells out concrete preservation examples for each form.
- **Email mode dropdown choice persists** across app restarts (was in-memory only).

### Performance
- ~35% faster OpenAI styling on short clips via gpt-4o-mini → gpt-4.1-mini swap
- Order-of-magnitude faster styling on the Cerebras-served path when capacity is available

## [3.12.7] - 2026-05-10

### Fixed
- **Vocab case-correction was silently broken.** When Whisper transcribed a vocab word in non-canonical case (e.g. `cobie` instead of `COBie`, `ashkan` instead of `Ashkan`), the fuzzy-match function recognised it as an exact case-insensitive match but then did *nothing* — no correction was emitted, so the lowercase form was preserved in the styled output. Root cause: the `if word in vocab_lower: matched_tokens.add(word); continue` branch added the token to the matched set but never appended a `(word → canonical)` correction. Fix: when the case differs from canonical, emit a correction. Tokens already in canonical case (e.g. `Ashkan` exactly as stored) still skip without an unnecessary correction. This is what the user has been hitting when they said "the vocab doesn't really work" — Whisper biasing handled most of it, but any time it produced a lowercased form, the correction layer didn't fix the case.

### Added
- **Dedicated vocab regression harness** at `scripts/test_vocab_corpus.py` — 25 cases covering: exact match preservation, lowercase case-correction, single-char typos, bigram-collapse for Whisper splits (`Nash can` → `Ashkan`), unrelated common words that must NOT be corrupted (`cost`, `high`, `ash`, `cobblestone`, `cobalt`, real names like `Coby Persin`), bigram false-positive guards (`has can`, `task on`), multi-vocab in one sentence, and vocab inside lists / email greetings. Pure local execution — no API calls — so runs in milliseconds and locks the correction layer in for any future change.

## [3.12.6] - 2026-05-09

### Added
- **`OPENAI_STYLE_MODEL` env-var override** for the OpenAI styling model. Defaults to `gpt-4o-mini`; setting `OPENAI_STYLE_MODEL=gpt-4.1-mini` (or any other compatible chat-completions model) flips the styler without a code change. `gpt-4.1-mini` follows strict prompts noticeably better than `gpt-4o-mini` for the same speed bracket and is the recommended upgrade for users who want tighter rule-following.
- **53 new regression cases** in `scripts/auto_test_corpus.py`, taking the suite from 38 to 91 hand-built tests. New coverage:
  - Greeting variations: `Hey James,` / `Morning Sarah,` / `Afternoon team,` / `Dear Mr Thompson,` / `Hi everyone,` / `Hi all,` / `Hi James and Sarah,` / `Hi folks,` / `Hey both,` / `Evening Rohan,`
  - Sign-off variations: `Cheers` (no name), `Best regards, James Farrelly` (full name), `Sincerely,` `Speak soon,` `All the best,` `Thanks again,`
  - Combinations: email + numbered list in body, email + bullets in body, multi-paragraph email
  - Edge: currency (`£2,500` / `$3,300` / `€2,900`), dates and timezones (`12th May 2026 at 3pm UK time`), acronyms (`PR EOD API SIEM`), hyphenated names (`Mary-Jane`), apostrophes (`O'Brien`), URLs, email addresses, version numbers, file paths, mixed `twenty-five` vs `250`, profanity, British spelling
  - Real-world: stand-up update, bug report, code-review comment, Slack-casual, rambling thought
  - Hallucination guards: real `Thank you.` ending, dictated `subscribe to the newsletter` (not Whisper outro), digits/units/percentages, trailing `Amen.`
  - Self-correction: multi-correction, `I mean` as clarification, backtrack-and-restart
  - Negative guards: `Highest priority` (not `Hi-` greeting), `cheers` as celebration in body, `thanks card` in body

### Fixed
- **Sign-off list now includes `Thanks again`, `Thanks so much`, `Many thanks`, `All the best`, `Yours sincerely`, `Yours truly`** in the prompt's recognised sign-off triggers. Previously only `Thanks` (bare) was matched, so `Thanks again, James.` stayed inline at the end of the body instead of splitting into two lines.
- **Numbers, times, dates, units, currency, versions and acronyms are now strictly preserved.** Earlier: dictating `"meeting on 12th May 2026 at 3pm UK time, 230ms response time"` was being normalised by the styler to `"12th may 2026 at 3 PM UK time, 230 ms response time"` — month lowercased, AM/PM uppercased and space-injected, unit space-injected. The `FORMATTING` rule now spells out concrete preservation examples for times (`3pm` stays `3pm`), dates (`12th May 2026` stays exactly), units (`230ms` stays joined), currency (`£2,500`), versions (`v3.12.5`), acronyms (`EOD ASAP API` stay uppercase), URLs and email addresses.

## [3.12.5] - 2026-05-09

### Added
- **Sign-off auto-split in Normal mode.** Mirroring the v3.12.4 greeting-line-break rule, when a transcript ends with a sign-off + name (`Cheers, James` / `Regards, James` / `Thanks, James` / `Best, James` / `Best regards, James` / `Kind regards, James` / `Speak soon, James` / `Talk later, James`), the styler now splits them into two lines: the sign-off ending with a comma on one line, the name on its own line below, with a blank line above the sign-off block. Combined with the greeting rule, dictating a full email-shaped utterance now produces proper email layout in Normal mode without needing the dedicated Email mode. Trigger requires the sign-off pattern to be at the very END of the transcript and includes a guard against false positives where the sign-off word is being used mid-body to address someone (e.g. "Thanks for meeting today, James — it was really useful." stays as a single sentence). Four new corpus tests (3 positive variants + 1 negative guard) lock the behaviour in.

## [3.12.4] - 2026-05-09

### Added
- **Greeting auto-line-break in Normal mode.** When the transcript starts with a clear greeting addressed to a person or group (`Hi James,` / `Hello team,` / `Hey Rohan,` / `Dear Sam,` / `Morning all,`), Normal mode now puts the greeting on its own line with a blank line below it before the body — without needing the user to switch to the dedicated Email mode. Trigger requires (a) sentence-start greeting word, (b) a name or group address, AND (c) a punctuation mark already present in the input. The styler does not invent a comma where none exists. False-positive guards: the rule does NOT fire when the input is talking *about* a greeting (e.g. "Hi guys would never work as an opener") — the next word being a verb/auxiliary with no punctuation tells the styler this is meta-language, not a greeting. Six new corpus tests (4 positive + 2 negative) lock the behaviour in.

### Fixed
- **Email-mode dropdown choice was never persisted.** Selecting "Email" from the sidebar dropdown updated the running pipeline in memory, but `set_mode()` never wrote the choice to `~/.waffler-hosted/settings.json`. On every app restart the user was silently back on Normal. `set_mode` now persists `prompt_style`; `Config._load_env_vars` reads it back so the UI's choice survives restarts. Precedence: settings.json (UI choice) > `PROMPT_STYLE` env var (advanced override) > "normal" default.

## [3.12.3] - 2026-05-09

### Added
- **Email mode.** New `prompts/email.txt` mode that the user explicitly selects from the sidebar dropdown ("Normal" / "Email"). Email mode inherits every never-invent-content / never-paraphrase / never-censor rule from Normal mode and adds permissive paragraphing plus dedicated greeting / sign-off lines when the speaker actually dictated them. If the speaker did not say "Hi Sam," or "Cheers, James", email mode does not invent them — half-an-email stays as plain prose. This replaces the abandoned auto-detect approach (which misfired on prose that vaguely resembled an email).
- **Bigram-collapse vocab fuzzy-matching.** When Whisper splits a compound name into two words ("Ashkan" → "Nash can", "Ashcan", "Ash can"), the existing single-word fuzzy matcher couldn't find it. We now glue every adjacent word pair together and fuzzy-match the joined form against single-word vocab entries, with a slightly looser similarity threshold (0.70 vs 0.75 for unigrams) because gluing always inflates max-length by one. Verified against the actual "Nash can" → "Ashkan" failure from history.
- **Esc as universal cancel for sticky-mode recordings.** macOS swallows the Fn key at HID level on some Macs (M3 Max in particular), so Fn+Space cancel was unreliable. Esc is never typed during dictation, so it's a safe escape hatch in any state. The existing Fn+Space path still works.
- **Regression harness — `scripts/auto_test_corpus.py`** — 28 hand-built cases across six lengths (very-short / short / medium / long / very-long / extreme) and eight categories (prose, numbered-list, bulleted-list, email, double-words, self-correction, hallucination-bait, code/technical). Each case declares must-contain / must-not-contain / must-match / must-not-match assertions plus a word-retention range. CLI flags `--delay` (seconds between calls; default 0.5, bump to 3-5 to test on Groq without tripping per-minute limits) and `--filter` (substring-match a single label for targeted re-runs). Drives the styler directly with text inputs to skip audio capture during prompt iteration.

### Fixed
- **Windows rate-limit toast was never actually visible.** Three real bugs caused "Cleanup skipped" toasts to fire silently on Windows while showing reliably on macOS: (a) the toast `Toplevel` was created *after* the root pill was withdrawn, and Tkinter on Windows won't reliably paint a Toplevel whose master is currently in withdrawn state — Mac's AppKit overlay path doesn't share this Tk quirk; (b) no `update_idletasks()`/`lift()` to force the first paint of a borderless Toplevel; (c) the OS demoted the toast's `-topmost` attribute the moment the active app reclaimed focus, pushing it behind the user's window. All three are addressed: build the toast first then withdraw the pill, force paint after canvas build, re-assert `-topmost` at 100/400/1200 ms after creation. Auto-hide also bumped from 6 s to 9 s so users have time to actually read the rate-limit guidance.
- **Pill not appearing in fullscreen 90% of the time on macOS.** Re-asserts the `collectionBehavior` flags on every "show" so macOS re-evaluates which Space the pill belongs to; switched from `makeKeyAndOrderFront:` to `orderFrontRegardless:` so we no longer steal focus from the fullscreen app. The pill now appears reliably over fullscreen apps without yanking focus.
- **Hallucination stoplist** — when the entire styled output equals a known YouTube outro phrase ("Thanks for watching!", "Please subscribe", "[Music]" and a dozen related variants), we discard the output rather than pasting it. The vocab-echo filter alone didn't catch these because they don't overlap with vocab tokens. The end-of-text strip already caught them as suffixes, but a clip that transcribed *only* to "Thanks for watching!" was being preserved.
- **Word-level stutters in short transcripts.** Short clips (≤10 words, <15% hard-filler density) bypass the LLM via `_is_simple()` and run through `_basic_clean` only. `_basic_clean` previously stripped um/uh but left literal word-repeat stutters intact — "I I think we should ship it." came out unchanged. A new regex collapses any whitespace-separated repeat of the same alphabetic token: "I I" → "I", "the the" → "the". Punctuation between repeats blocks the collapse on purpose ("I, I think" might be a deliberate restart).
- **`Hi Sam.` greetings being dropped by the OpenAI styler.** The HARD RULE against removing spoken greetings was being interpreted inconsistently — Groq honoured it, OpenAI's gpt-4o-mini dropped greetings entirely. Added a worked example to the rule with the exact failing input shape; OpenAI now keeps "Hi Sam." reliably.
- **Sentence-leading `Yeah, …` not being stripped.** The previous filler rule listed "yeah (mid-sentence)", which the LLM read literally and left "Yeah, I do really like the job" untouched. New explicit entry plus worked example: "Yeah, I do really like the job." → "I do really like the job." Same logic for sentence-leading "Right," / "OK," when no clause-linking job is being done.
- **WKNO-MEMPHIS / station-attribution caption credits leaking through.** Real instance from history (`"CLOSED CAPTION PROVIDED BY WKNO-MEMPHIS."`) was leaking past the existing subtitles/translated/captioned-by patterns. Added explicit closed-caption pattern with provider-name match. Mid-text "closed captioning" usage in real speech is preserved.
- **Paragraph break heuristic** loosened. Was: only on explicit "new paragraph" cue. Now: also at concrete topic-shift discourse markers ("so anyway", "moving on", "another thing", "by the way", "ok so" preceded by a complete thought). Long monologues are easier to read.

### Reliability
- **Updater robustness.** `src/updater.py` gains a 45 s stall-timeout (download hangs no longer hold the app), a real-browser User-Agent header (some CDNs reject bare `python-requests` UAs), atomic `.partial → final` rename to prevent half-downloaded files being treated as complete on a crash, and download-progress logging. The "Update available" modal gains a "Download in browser" fallback button when the in-app updater can't fetch.

## [3.12.2] - 2026-04-30

### Fixed
- **Long dictations were silently truncated mid-sentence by the styler.** The two API call sites in `src/style_openai.py` had `max_tokens=512` hardcoded — about 380 words of output. A 732-word recording in the user's history was cut at ~421 styled words, dropping ~290 words including the entire closing section about Waffler itself. Worse, the constructor accepted a `max_tokens=1024` parameter and stored it on `self.max_tokens` but the API calls ignored it entirely, so even the configured default was being silently overridden. The styler now sizes the output token budget against the input length (`max(1024, min(8192, input_words * 3))`) so short utterances get the standard 1024-token headroom and a 30-minute monologue gets up to ~6000 words of room — well past any realistic single dictation. Verified with the actual truncated entry: same 732-word input now produces 716 cleaned words, ending correctly at the final phrase.

## [3.12.1] - 2026-04-24

### Changed
- **OpenAI transcription model upgraded** from `whisper-1` (original 2022 model) to `gpt-4o-mini-transcribe`. The new model is half the price ($0.003/min vs $0.006/min) *and* measurably better at filler words, punctuation, and accents — which is the main quality complaint users hit on the OpenAI fallback path when Groq is rate-limited. The model is configurable via a new `OPENAI_WHISPER_MODEL` environment variable; set it to `gpt-4o-transcribe` for maximum quality at the old whisper-1 price, or `whisper-1` to force the legacy baseline.
- **Custom vocabulary is no longer passed to the styler.** It was being injected into the LLM's system prompt as *"If any of these words were intended by the speaker, use these exact spellings: X, Y, Z."* — and the model treated that as *"use these words"*, substituting vocab entries into clean transcripts (reproducible: "the cost of the project" was at risk of becoming "the COBie of the project"). Whisper's `prompt=` parameter and the post-transcription fuzzy matcher already handle legitimate vocab biasing — the styler has no audio and can only hallucinate, so it never sees the vocab list now.

### Fixed
- **Smart list formatting restored and extended.** A prior prompt rewrite had replaced the original list-aware behaviour with *"do not convert to bullet points or numbered lists unless the speaker clearly dictates a short list"*, so even explicit dictation like *"Number one, X. Number two, Y."* came out as two paragraphs. The FORMATTING section is now rebuilt from scratch with MUST-language, concrete input/output transformations, and triggers for every count phrase the user actually says: `number one/two/…`, `first/second/third/…`, `first of all`, `firstly/secondly/thirdly`, and `next`. The count word itself is always stripped and replaced with `1. `, `2. `, `3. `; the lead-in ("Number one, ..." → "1. ...") is handled correctly.
- **Bullets for unnumbered sequences.** Grocery lists, *"the three things are X, Y, and Z"*, *"runs on Mac, Windows, and Linux"*, and similar parallel-enumeration patterns now auto-bullet with a clean lead-in and one item per line. Conversational prose with commas ("the meeting went well, but the team pushed back, so we revisit Tuesday") still stays prose — the bullet rule requires parallel, discrete items of similar shape, not every comma.
- **Prose lead-in before a list is preserved.** "Here's what we need to do this sprint. Number one, hire… Number two, onboard…" now outputs the lead-in as its own paragraph followed by the numbered list, instead of deleting the intro sentence to make a "pure list" (which violated the hard rule against dropping whole sentences).
- **Whisper hallucination strip extended** beyond the original `thank you` / `thanks for watching` / `please subscribe` trio to cover the full YouTube-outro family: *remember to subscribe*, *don't forget to subscribe*, *like and subscribe*, *subscribe to my channel*, *see you in the next one / next time / later*, *hit/smash the like button*, and tolerance for trailing punctuation variants. Added a short-remainder rule so fragments like "web outfits," left over after stripping a hallucinated tail are also discarded rather than pasted into the clipboard.
- **Vocab-echo guard tightened** so Whisper's prompt-regurgitation on silent audio is caught more reliably: output consisting entirely of vocab tokens is now discarded regardless of length, and short outputs (≤ 10 distinct words) are discarded at ≥ 50% vocab density. Verified live — today's smoke test produced `"Ashkan, COBieQC, COBie"` on silent audio from both `gpt-4o-mini-transcribe` and `gpt-4o-transcribe`, and both were suppressed end-to-end.

### Added
- `scripts/test_*.py` regression harnesses (6 files) — live probes against the real Whisper / styler APIs, 30s between calls, covering the numbered/bullet/prose prompt cases, the vocab injection guard, the hallucination strip, and the OpenAI model-switch contract. Useful anchors for the next prompt change.

## [3.12.0] - 2026-04-24

### Removed
- **Gemini styling backend.** Gemini was cleanup-only — it has no Whisper equivalent for transcription — so running it alongside Groq and OpenAI (which both cover STT *and* cleanup) just added surface area. Dropped the provider pill from the setup wizard, the settings row, the `validate_gemini_key` IPC endpoint, the Gemini path in `style_openai.py`, and the `google-genai` dependency from both requirements files and both PyInstaller specs. Users who had configured only a Gemini key will now be prompted to add a Groq or OpenAI key.
- **Dev-only launchers and duplicates.** `setup.sh`, `setup_windows.bat`, `run.sh`, `run_windows.bat`, `LaunchWaffler.command`, `install-run.bat`, and `install_local_whisper.bat` are gone — end users install from the GitHub release, and the README already shows the three-line `pip install && python app.py` sequence for running from source. `requirements-windows.txt` (hyphen) was an outdated shadow of `requirements_windows.txt` (underscore) and has been removed; the underscore file is what the Windows build actually uses.

### Fixed
- **"Cleanup skipped" toast leaked internals and looked unfinished.** The old toast read *"Groq \`org_01j44ka3s2fc0s81tyzhp399xn\` service tier \`on_demand\` on tokens per day (TPD) reached. Try again in 15m43.488s., or add an OpenAI key..."* — the rate-limit regex in `_style_groq` greedily matched the first `on ... :` in the Groq error, so the org ID and service tier leaked into the UI, and the raw millisecond-precision retry string was passed through untouched. The regex is now anchored on Groq's actual limit vocabulary (`tokens|requests|audio seconds` per `minute|hour|day`) and the pipeline maps that to a readable label ("daily token limit", "per-minute token limit", etc.) and rounds the retry duration up to whole minutes. Toast now reads like a sentence: *"Groq daily token limit hit. Try again in about 16 minutes, or add an OpenAI key in Settings as a fallback."*

### Performance
- **Skip Groq during its own 429 window.** When Groq returns a 429 it also tells us exactly when to retry (e.g. `15m43.488s`). The styler now parses that into an absolute deadline and bypasses Groq entirely until the window expires — every recording during the lockout goes straight to OpenAI instead of wasting a ~200–500ms round-trip hitting Groq just to be rejected again. The next request after the deadline automatically goes back to Groq with no user action; no API or UI change.

### Changed
- **README.** Tech-stack table corrected to match reality — Mac hotkey is Quartz / CoreGraphics, Windows hotkey is a low-level Win32 keyboard hook via `ctypes` (not `pynput`), and the menubar/tray row lists `rumps` (Mac) + `pystray` (Windows). Usage section split into push-to-talk, hands-free (hotkey + Space), and cancel (click × on the recording overlay) so the Space toggle is explicit on both platforms. Run-from-source snippet now points Windows users at `requirements_windows.txt`.

## [3.11.8] - 2026-04-23

### Changed
- **Pre-public-release polish.** Cleaned up unused imports across `src/`, removed unused locals (`latency`, `os`, `Optional`, `NSObject`, etc.), and added a lightweight CI workflow (`.github/workflows/ci.yml`) that runs on every push and PR: pyflakes static analysis (skipping known platform-gated false positives), an "import every module" smoke test, and the styling guardrail test against the real failure-mode corpus. No behaviour change — purely hygiene.

## [3.11.7] - 2026-04-23

### Fixed
- **Pipeline froze on long recordings ("stuck on processing for ages").** `sounddevice`'s `_stream.stop()` calls into CoreAudio (PortAudio on Windows). Both can wedge for tens of seconds — sometimes indefinitely — on long recordings or after a device hot-swap. While `_stream.stop()` was hung, the recording-stream lock was held forever, so every subsequent recording's `start()` and `stop()` blocked behind it. The pill stayed visible (looked like the app was "still processing"), but the pipeline was actually deadlocked on a kernel call before any LLM request was ever made. `audio.stop()` now snapshots the recorded buffer first, runs the blocking close in a daemon thread with a 1.5 s watchdog, and continues regardless — the captured audio is returned and processed even if the underlying stream object never finishes closing.

## [3.11.6] - 2026-04-23

### Fixed
- **"Cleanup skipped" toast gave unhelpful advice.** The old copy ("Groq limit hit — pasted raw. Try again in a minute.") was wrong in two ways: Groq's TPD (tokens-per-day) limit doesn't reset in a minute, and it didn't tell the user what to actually do. The Groq 429 response carries the exact retry time and which specific limit was tripped — `_style_groq` now parses both and passes them through. The toast reads the real thing: e.g. "Groq tokens per day reached. Try again in 8m52s, or add an OpenAI key in Settings as a fallback." Honest and actionable.

## [3.11.5] - 2026-04-23

### Fixed
- **"We couldn't hear you" toast fired on speech it should have accepted.** Silence detection windowed the recording into 1-second chunks and required RMS ≥ 30 in at least one. A short "hello" (~0.5 s) got diluted by the silence around it in the 1 s window to well under 30, so it was rejected despite being clearly audible. Shrank windows from 1 s → 0.25 s so brief utterances aren't diluted, and dropped the threshold from 30 → 12 (room tone is ~3-8 on a well-gained mic, so 12 still filters true silence without killing soft speech).
- **Toast and pill competed visually.** The toast has its own sad-waffle icon; the pill (which sits right below the toast) has the regular waffle icon. Users saw two waffles stacked right on top of each other, which looked cluttered and made the toast feel pasted-on. The pill now hides while any toast is visible and restores itself when the toast dismisses — same behaviour on both platforms.

## [3.11.4] - 2026-04-23

### Fixed
- **App opened twice on first launch (macOS).** The bundle's `Info.plist` had no `LSMultipleInstancesProhibited`, so when a race or re-invocation happened during startup (e.g. Dock click while the app was still coming up, or `open -a Waffler` firing during initialization), macOS's Launch Services would helpfully spin up a second full Waffler. Users ended up with two pills, two history watchers, and double the memory. Set `LSMultipleInstancesProhibited: True` so subsequent invocations reactivate the running instance instead of launching another copy.
- **`CFBundleShortVersionString` was hardcoded at `2.1.19` in every shipped `.app`.** CI's "Sync app version from tag" step rewrote `src/__init__.py` but never touched the PyInstaller spec's Info.plist. Result: the version the app self-reported in the UI (`__version__`) was correct, but every macOS-level surface — Finder's Get Info, the About menu, the bundle's plist — lied about the version. Spec now reads the version from `src/__init__.py` at build time so every surface agrees.

## [3.11.3] - 2026-04-23

### Fixed
- **Toast body text was cut off.** The body rect was 30 px tall — only enough for two lines — but several of the newer messages wrapped onto three. Third line silently dropped off the bottom of the toast. Raised toast height from 170 → 210 px on both platforms and the body rect from 30 → 60 px. All messages now render in full.
- **Dismiss button was unresponsive on macOS.** The toast lives in a borderless floating `NSWindow`, which can't become "key". Without `acceptsFirstMouse_` on the content view, the first click was swallowed as a window-activation attempt instead of hitting the Dismiss zone. Added the method so clicks fire on the first try, every time.
- **Toasts stayed on screen forever.** Neither platform had an auto-dismiss — a transient warning could sit there indefinitely if the user didn't click Dismiss. Now `warn` and `error` toasts auto-dismiss after 6 seconds on both platforms (macOS via `threading.Timer` + command queue; Windows via `tk.after`). `cancel` toasts still require explicit user action, since they ask a question.
- **Waffle icon had no sad face on macOS.** Windows has been drawing worried eyes, a curved frown, and a syrup tear since the feature landed; the macOS port only ever drew the 3×3 grid. Added the full face (two eyes, Bezier frown, tear from the left eye) so the icon matches.
- **Fallback toast copy was unclear.** "Styling rate-limited" / "Styling offline" / "Styling unavailable" made users ask "what does that mean?" All three now share the heading "Cleanup skipped" with a body that states what happened and what to do — e.g. "Groq limit hit — pasted raw. Try again in a minute."

## [3.11.2] - 2026-04-23

### Fixed
- **Toasts sometimes didn't fire on macOS.** If the overlay subprocess had died (e.g. after a pipeline error), `show_toast()` silently dropped the call instead of reviving the subprocess — so the toast that was supposed to explain *why* things broke never appeared. `show_toast()` now runs the same auto-restart path as `update_level()`: if the subprocess is dead, relaunch it, send a `show`, then fire the toast.
- **Non-mic errors showed a useless "Select mic" button.** Connection failures, rate-limit hits, access-denied responses, recording-too-long warnings, and the generic "Something went wrong" toast all offered Select mic as the first action — which obviously doesn't fix any of those problems. Those six scenarios now use the `warn` style (single centred Dismiss). Select mic only appears on the one toast that's actually about the mic: "We couldn't hear you".
- **Toast body text colour on macOS didn't match the theme.** Was flat grey `#888888` against a warm dark-brown fill + gold border — looked pasted-in. Now warm tan `#A89070`, matching Windows. Heading also tuned from cream to pale gold to match.

## [3.11.1] - 2026-04-23

### Fixed
- **macOS toast buttons styled inconsistently with Windows.** The "Select mic" button on macOS was rendered in Tailwind violet (`#7c3aed`) left over from an old revision; Windows was already on the waffle-gold theme (`#C8A256` / `#D4A843`). The Discard button was also flat red on Mac but a proper dark-red-with-red-outline on Windows. Mac now matches Windows exactly — one palette, one theme.
- **Silent quality degradation when all styling providers fail.** When Groq rate-limited (free tier: 30 rpm, 500k tokens/day on llama-3.3-70b) and no OpenAI / Gemini key was configured, the styler fell through to a regex-only filler-word stripper (`_basic_clean`) with no user-visible signal. Users would paste near-raw transcripts and not know why cleanup stopped working. Now: the fallback still runs (so you still get text), but a toast appears with a clear reason — "Styling rate-limited" / "Styling offline" / "Styling unavailable" — and the body explains what's happening.

### Added
- Third toast style, `warn`, with a single centred **Dismiss** button (applies to both platforms).

## [3.11.0] - 2026-04-23

### Removed
- **Private Mode.** Local Gemma 4 styling took minutes per long clip even on an M3 Max (LLMs max out around 50 tok/s on Apple Silicon vs. Groq's 500+ tok/s on LPU hardware), so the fully-offline pipeline was never going to match cloud latency for the cleanup step. The v3.10.x line shipped with a series of platform-packaging patches (mlx-whisper bundling, JIT entitlements, ffmpeg bundling) that are also rolled back here since they only existed to support the local stack. Revert scope: the v3.10.0 feature merge plus v3.10.1 → v3.10.4. Transcription / styling routes back through Groq + OpenAI only.

## [3.9.0] - 2026-04-21

### Security
- Fixed XSS vulnerability in update banner (replaced unsafe innerHTML with safe DOM construction)
- Fixed JavaScript injection in notify_js_status (added json.dumps escaping)
- Added URL scheme validation to prevent arbitrary protocol execution (open_url now only allows http/https)

### Changed
- Updated .gitignore to prevent build artifacts (*.dmg, build.log) from being committed
- Added code signing configuration placeholders to .env.example for macOS builds

This release hardens Waffler for open-source distribution with critical security fixes.

## [3.8.7] - 2026-04-18

### Fixed
- **Vocabulary pasted as output on empty/silent recordings.** When the user accidentally pressed the hotkey and released without speaking (or recorded pure silence), Whisper would regurgitate the `prompt` parameter (the custom vocabulary list) verbatim as the transcription — the user's vocab words got pasted into whatever app they were in. This is a documented Whisper failure mode under silence. Added `_is_vocab_echo()` post-filter that discards any transcription that is effectively a repeat of the vocab prompt (exact match, or ≥70% vocab-word overlap in a short output). Real speech containing vocab words (e.g. "I just spoke to Ashkan about Waffler") is unaffected.

### Changed
- Upgraded Groq Whisper model from `whisper-large-v3-turbo` to `whisper-large-v3`. ~15% better accuracy on rare words, proper nouns, and technical terms at the cost of ~400ms extra latency (still well under a second on Groq's LPU hardware).

## [3.8.6] - 2026-04-18

### Fixed
- "Check for Update" said "You're up to date, running vcurrent" even when a newer release existed. Two chained bugs:
  1. GitHub's REST endpoint `/releases/latest` returns **404 Not Found** on this repo because historic releases were never flagged `make_latest=true`. The backend silently treated the 404 as "no update available".
  2. On the 404 (or any API failure), the backend returned `{update_available: false}` with no `current_version`, so the UI fell back to literal text "vcurrent".
- **Fix:** rewrote `check_for_updates` to query `/releases` (list all) and pick the highest-semver non-draft, non-prerelease tag. Always returns `current_version`. Returns an `error` field on failure so the UI can show the actual reason instead of silently claiming "up to date".
- **UI:** distinguishes three states explicitly — update available / up to date / check failed with reason.
- **Workflows:** both release workflows now pass `make_latest: 'true'` so the REST `latest` endpoint works for future releases too (belt + suspenders).

## [3.8.5] - 2026-04-18

### Fixed
- macOS toast popups (e.g. "We couldn't hear you", cancel confirmation) rendered with the border, waffle icon, and buttons but **no heading or body text** — the rectangle looked empty. Cause: the toast drawing code called `drawInRect_withAttributes_` directly on Python strings, relying on PyObjC's implicit `str` → `NSString` bridge, which is no longer reliable in current PyObjC. Switched to constructing an `NSAttributedString` explicitly and calling `drawInRect_`, which works regardless of bridging behaviour. Applied to heading text, body text, and button labels.
- Toast button text also had a wrong attribute dictionary key (`NSMutableParagraphStyle` class object used as a key instead of the `NSParagraphStyleAttributeName` string constant), which meant button text alignment was never applied. Fixed.

## [3.8.4] - 2026-04-18

### Fixed
- macOS "This application can't be opened — error -10661" on launch. The release workflow was notarizing and stapling the DMG but not the .app inside it. When users dragged Waffler.app to /Applications, the stapled ticket stayed on the DMG and the .app had to fall back to online Gatekeeper verification, which fails intermittently on Sequoia 15.2+. The workflow now zips the .app, submits it to Apple for notarization, staples the ticket directly to the .app bundle, then packages the stapled .app into the DMG. The DMG continues to be signed + notarized + stapled for the initial-open check.

## [3.8.3] - 2026-04-18

### Added
- **In-app auto-update**: new "Check for Update" button in Settings → About. Click to check GitHub for a newer release. If one exists, a modal offers to download the installer with a live progress bar. On completion, Waffler closes, the installer applies the upgrade, and the app relaunches automatically.
- Windows path uses Inno Setup's `/SILENT /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS` silent-upgrade flags.
- macOS path mounts the DMG, swaps `/Applications/Waffler.app`, unmounts, and relaunches via a detached helper script (no Gatekeeper re-prompt since the new .app is already signed).

### Fixed
- Internal version string was not being bumped at build time — shipped installers reported a stale `__version__`, which made the auto-update banner show incorrectly even on the latest version. Both release workflows now substitute `src/__init__.py` `__version__` from the git tag at build time.
- v3.8.2 was tagged but its CI build failed (the version-sync step raised on no-op replacements). Reshipped as v3.8.3 with the regex replaced by a match-count check.

## [3.8.1] - 2026-04-17

### Fixed
- Phantom email wrapping: cleanup no longer injects `Dear Team,` / `Hi,` greetings or `Best regards,\n[Your Name]` sign-offs when the speaker never dictated them
- Meta-preamble leakage: `Here is the cleaned text:` / `Output:` no longer appears in output
- Hallucinated bullet/numbered lists: only applied when the speaker explicitly dictates structure
- Mid-output self-talk (`Wait, it seems there was a pause...`) no longer emitted
- Regex shortcut no longer strips meaning-bearing words (`like`, `basically`, `you know`) from short utterances; only hard fillers (um, uh, erm, ah, er) are stripped without the LLM

### Changed
- Styling model upgraded from `llama-4-scout-17b` to `llama-3.3-70b-versatile` on Groq — stronger instruction-following, preserves letter-spellings and technical terms more reliably
- `prompts/normal.txt` rewritten in Wispr-style: context-aware filler removal, light grammar smoothing allowed, synonym swaps forbidden, question marks preserved, contextual phrases (`to be honest`, `I can't lie to you`, etc.) kept
- Paragraph breaks now triggered by dictated enumeration (`number one...`, `number two...`)

### Added
- Deterministic post-processor (`_strip_hallucinations`) as a belt-and-suspenders guardrail over LLM output — strips leading preambles, injected greetings (only when raw didn't start with one), sign-offs with `[placeholder]` tokens, and collapses 3+ consecutive newlines
- Test harnesses: `tests/test_strip_hallucinations.py` (12 cases), `tests/test_e2e_real.py` (real-pipeline regression), `tests/test_model_bakeoff.py` (Groq model comparison)

## [2.1.19] - 2026-03-27

### Fixed
- Stripped phantom trailing words and prevented phrase relocation in cleanup
- Prevented duplicate prose+list output in cleanup prompt
- Lowered silence detection thresholds for better Whisper acceptance
- Lowered audio sensitivity thresholds for Whisper support
- Fixed permissions page and API key validation

### Changed
- Bumped `requests` dependency from 2.31.0 to 2.33.0

## [2.1.13] - 2026-03-22

### Fixed
- Fn key detection on macOS
- Windowed RMS so pauses don't trigger false silence detection
- Mac-only Fn key enforcement, disabled custom hotkeys on Mac

### Added
- API key guide link to setup wizard
- Developer ID code signing support for macOS builds

## [2.1.2] - 2026-03-20

### Fixed
- Sidebar logo uses waffle icon image with single-colour text
- Tray icon renders correctly on Windows (direct HICON loading)
- Double-paste bug resolved (hook now ignores injected keystrokes)
- Auto-paste restored after SendInput regression
- Brand icon restored, hidden waffle on error toast, fixed key release glitch

### Changed
- Replaced sound wave icon with waffle-with-syrup icon everywhere
- Regenerated icon.ico from brand icon

## [2.0.0] - 2026-03-15

### Added
- Full desktop GUI via pywebview (replacing CLI-only mode)
- Setup wizard for first-run onboarding
- Local transcription history (searchable, stays on device)
- Windows support with native hotkey handling
- macOS menu bar icon
- Recording overlay with VU meter animation
- Snippet/template system
- Audio device selection

### Changed
- Switched from Deepgram to OpenAI Whisper / Groq for transcription
- Switched from MiniMax to GPT-4o-mini for text cleanup
- Complete rewrite of hotkey system (platform-specific implementations)
