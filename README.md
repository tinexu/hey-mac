# hey-mac 🎙️

A voice assistant for your Mac, a bit like Siri, ChatGPT and Claude combined. It runs locally and controls your computer.

- **Hears you correctly.** Speech recognition runs on your Mac with Whisper large-v3-turbo, which is much more accurate than Siri. You can add names it should expect (app names, project names) to a vocabulary list.
- **Finds files.** "find my resume" gives you a ranked list of matches using Spotlight's index plus fuzzy matching. Then say "open the second one" or click a result.
- **Opens and quits apps.** "open messages", "quit chrome" and "lock the computer" run instantly and don't use the AI.
- **Handles more complex requests with AI.** "open a new cursor window with my portfolio folder" goes to Claude Haiku, which picks the right actions.
- **Stays out of the way.** It shows a Spotlight-style bar only when you call it. Start it by saying **"hey jarvis"** or pressing **⌥ Space**, or type instead of talking.

```
   ┌──────────────────────────────────────────────┐
   │ ◉  find my tax return pdf                    │
   │    Found 3 matches for “tax return”          │
   │ 1  2025_Tax_Return.pdf  —  ~/Documents/Taxes │
   │ 2  tax-return-draft.pdf —  ~/Downloads       │
   └──────────────────────────────────────────────┘
```

## How it works

```
 mic ─► wake word (openWakeWord, local) ─┐
 ⌥Space hotkey ──────────────────────────┴─► record until you stop talking
                                             │
                                  Whisper (local, mlx) → text
                                             │
                 ┌───────────────────────────┴────────────────────────┐
     simple command? (open/quit/lock/volume/…)             anything else
          │  instant, free                                      │
          ▼                                          Claude Haiku + tools
     Mac actions  ◄──────────────────────────────────────────────┘
          │
   Spotlight-style bar shows the result
```

The AI can only use a fixed set of safe actions: open or quit apps, find and open files, open projects in an editor, lock, sleep, volume, media, and web search. It **can't** run arbitrary shell commands.

## Setup (about 5 minutes)

You need an **Apple Silicon Mac**, **Homebrew** and an **[Anthropic API key](https://console.anthropic.com/)**.

```bash
git clone <your repo> hey-mac && cd hey-mac
./scripts/setup.sh              # creates .venv, installs everything
open -e .env                    # paste your ANTHROPIC_API_KEY
source .venv/bin/activate
heymac
```

On the first run it downloads the Whisper model (~1.5 GB). The menu bar icon (〰) shows **Ready** when it's done.

### macOS permissions

Give these to the app you launch `heymac` from (Terminal, iTerm, or Cursor's terminal):

| Permission | Why | Where |
|---|---|---|
| Microphone | hearing you | macOS asks automatically |
| Accessibility | ⌥Space hotkey, lock screen | System Settings → Privacy & Security → Accessibility |
| Automation | quitting apps | macOS asks the first time |

Restart `heymac` after you grant Accessibility.

### Test without the mic

```bash
heymac --text "quit chrome"
heymac --text "find my resume"
heymac --text "open a new cursor window with my portfolio folder"
python -m pytest tests          # unit tests, no Mac needed
```

## Things to say

| You say | What happens | AI used? |
|---|---|---|
| open messages / launch spotify | opens the app (fuzzy, "spotfy" works) | no |
| quit chrome / close slack | quits it | no |
| lock the computer / go to sleep | locks / sleeps | no |
| volume 30 / mute / louder | sound | no |
| pause / next song | Spotify or Music | no |
| what time is it | time + battery | no |
| find my resume | ranked file list | yes |
| open the second one / show 2 in finder | opens from the list | no |
| open cursor with my portfolio project | new Cursor window on that folder | yes |
| where's that pdf about taxes from last year | Spotlight + content search | yes |
| search the web for best ramen near me | Google in the browser | yes |
| ask Claude how to reverse a linked list | opens Claude in Chrome and sends it | no |
| open Gemini and ask it … / search … on Perplexity | same for Gemini, ChatGPT, Perplexity | no |
| ask ChatGPT to summarize my cs373 hw2 instructions | finds the file, sends its text along | yes |
| text me a note: buy milk / send me this page / send me what I copied | iMessage to yourself | no |
| send my cs373 hw2 instructions to my phone | finds the file, texts it to you | yes |
| airdrop this page to my phone | opens the AirDrop picker | no |
| take a note: call the dentist tomorrow | new note in Apple Notes | no |
| add eggs to my grocery list | adds a line to that note (makes it if needed) | no |
| what's on my grocery list? | reads the note and answers | yes |
| send my resume to ChatGPT and ask for feedback | attaches the real file, types the prompt, sends | yes |
| ask Gemini what's in my latest screenshot | finds your newest screenshot and attaches it | yes |

## Aesthetics (presets)

Say **"coffee mode"**, **"change my aesthetic to matcha"** or pick one from the menu bar, and one preset switches
your wallpaper, light/dark mode, Spotify playlist, the lily's colors, your Cursor/VS Code theme, the Dock,
accent color, a Focus (via an Apple Shortcut) and which apps are open, all at once. **"Undo my aesthetic"** puts
everything back; **"save my setup as a preset called rainy day"** captures what you have now.

Presets live in `~/.config/heymac/config.toml` under `[presets.<name>]`; see `config.example.toml` for every option.
Lily palettes: `lily`, `coffee`, `matcha`, `ocean`, `sakura`, `midnight`, `sunset`, or your own three hex colors.

### Mascots

Instead of the lily, a little animal drawn in code sits in the corner: **cat, bunny, frog, bear, fox, otter or owl**.
It blinks and breathes while idle, perks its ears and moves its mouth with your voice, tilts its head while
thinking, does happy ^^ eyes with sparkles when it's done, and gets a sweat drop when something fails. Each
aesthetic brings its own animal (coffee → bear, matcha → frog, ocean → otter, sakura → bunny, midnight → owl,
sunset → fox), or set `mascot = "fox"` in a preset. Say **"be a fox"**, **"change your mascot to an owl"** or
**"go back to the lily"** any time; `[ui] mascot` sets the default (`auto`, `lily`, or one animal).

Limits: wallpaper changes the current desktop Space on each display; accent color reaches some apps only after
they're reopened; Spotify needs the desktop app; app icons, the menu bar and Chrome's theme can't be changed.

## Customising

Edit `~/.config/heymac/config.toml` (menu bar → *Open config file*):

- **`[files] project_roots`**: where your code lives, e.g. `~/code`. This is how "open *X* in Cursor" finds projects.
- **`[aliases]`**: nicknames, e.g. `"ide" = "Cursor"`.
- **`[speech] vocabulary`**: words Whisper keeps mishearing, e.g. your project names.
- **`[activation] hotkey`**: e.g. `"cmd+shift+j"`.
- **`[assistant] speak_replies = true`**: have it talk back.
- **On an 8 GB Mac**: set `[speech] model = "mlx-community/whisper-small.en-mlx"`.

### Using your own wake word ("hey <computer name>")

The built-in wake words are `hey_jarvis`, `alexa` and `hey_mycroft`. For a custom one:

1. Open the [openWakeWord training notebook](https://github.com/dscripka/openWakeWord#training-new-models) in Google Colab. It generates synthetic voices, so you don't need to record anything, and takes about an hour.
2. Download the `.onnx` file and set `wake_word = "/path/to/hey_mymac.onnx"`.

## Cost

Simple commands never call the AI. An AI command costs about 2–4k tokens with Haiku 4.5 ($1/M input, $5/M output), roughly **$0.003–0.01 each**. 20 a day comes to about $2–5 a month.

## Ideas for later

- A launchd agent so it starts at login
- Package as a `.app` with `py2app` so permissions attach to the app instead of Terminal
- More tools: calendar, reminders, Messages ("text mom I'm late"), window tiling
- Streaming transcription so text appears while you talk