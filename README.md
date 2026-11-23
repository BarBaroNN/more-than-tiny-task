<div align="center">

# More Than Tiny Task

**Record your mouse & keyboard. Edit it like a video. Replay it as many times as you want.**

A free Windows macro recorder inspired by TinyTask, with a timeline editor, partial re-recording,
delays between runs and a one-key "centre the mouse".

[![Download](https://img.shields.io/badge/Download-Windows%20.exe-5b8cff?style=for-the-badge&logo=windows&logoColor=white)](https://github.com/BarBaroNN/more-than-tiny-task/releases/latest/download/MoreThanTinyTask.exe)
&nbsp;
[![Latest release](https://img.shields.io/github/v/release/BarBaroNN/more-than-tiny-task?style=for-the-badge&color=2c313d)](https://github.com/BarBaroNN/more-than-tiny-task/releases/latest)

<img src="docs/preview-playback.gif" alt="More Than Tiny Task playing a macro 10 times with a 1-minute delay" width="900">

</div>

## Why not just TinyTask?

TinyTask is great until you mess up one click in a 2-minute recording and have to redo the whole thing.
More Than Tiny Task keeps the same **record → play** simplicity and adds:

| | TinyTask | More Than Tiny Task |
|---|:---:|:---:|
| Record & replay mouse / keyboard | ✅ | ✅ |
| Repeat N times or forever | ✅ | ✅ |
| **Wait between runs** (e.g. 10 runs, 1 min apart) | — | ✅ |
| **Timeline editor** (cut, copy, paste, move, delete) | — | ✅ |
| **Re-record just one part** of a macro | — | ✅ |
| **Glide the mouse to the screen centre** with a hotkey | — | ✅ |
| **Play from wherever your cursor is now** | — | ✅ |
| **Works across resolutions** (record on 1440p, play on 1080p) | — | ✅ |
| Custom hotkeys, stay on top, compact mode, tray | — | ✅ |
| Undo / redo | — | ✅ |
| **Screen checkpoints**: stop and alert your phone if the screen looks wrong | — | ✅ |

## Download & run

1. Download **[MoreThanTinyTask.exe](https://github.com/BarBaroNN/more-than-tiny-task/releases/latest/download/MoreThanTinyTask.exe)** (Windows 10 / 11).
2. Double-click it. No installation needed.

> **"Windows protected your PC"?** The app isn't code-signed (certificates are expensive), so SmartScreen warns about it. Click **More info → Run anyway**. Some antivirus tools also flag macro recorders because they read keyboard input. The exe is built automatically from this source code by GitHub Actions, and every release lists its SHA-256 checksum.

## How to use it

| Key | What it does |
|-----|--------------|
| **F8**  | Start / stop recording (re-records just the selected part if you've selected one) |
| **F9**  | Start / stop playback (continues from the playhead, or plays only the selected part if you've selected one) |
| **Esc** | Stop playback |
| **F10** | Glide the mouse to the centre of the screen. Works while recording, and the glide is recorded |
| **F7**  | Add a [screen checkpoint](#screen-checkpoints--phone-alerts): saves a picture of the screen right now, at the current moment of the recording or playback (or at the playhead) |

All hotkeys work while other windows are focused and can be changed in **Settings → Hotkeys**.
F7, F8, F9 and F10 are never recorded and never reach the app you're using, so F10 won't open its menu bar.
**Esc only stops playback.** While recording it's just a normal key (and gets recorded), so the only way to stop a recording is F8.

1. Press **F8**, do your thing, press **F8** again.
2. Set **Repeat** (or tick *Unlimited*), an optional **Delay between runs**, and the **Speed**.
3. Press **F9**. Press **Esc** at any time to stop.
   If a part of the timeline is selected, **F9 plays just that part**, with the same repeat / delay / speed settings. Click an empty spot on the timeline to clear the selection.
   Otherwise **F9 continues from the playhead** (the red line): stop with Esc and press F9 again to pick up where it stopped, or click anywhere on the timeline to start from there. Keys and mouse buttons that were held down at that point are pressed again. Further repeats play the whole macro. Click *play from the start* under the progress bar to go back to the beginning.
4. **Ctrl+S** saves the macro as a `.mttk` file. Drag a file onto the window to open it.

### Mouse options

Two switches next to *Speed* control where the mouse goes during playback:

- **Start from current cursor:** the whole recording is shifted so it starts where your pointer is right now, instead of where it was when you recorded. Every repeat starts from that same spot. Useful for things that aren't always in the same place on screen.
- **Adapt to screen resolution** (on by default): each macro remembers the resolution it was recorded on. If you play it on a different one (for example recorded on 2560×1440, played on 1920×1080), every mouse position is rescaled so it lands on the same spot of the screen. The status bar shows both resolutions. Parts you re-record on another screen are converted automatically too.

**Screens.** Next to the mouse options, *Record on* picks the screen a recording belongs to (the one under the mouse when you start, a specific screen, or *All screens*) and *Play on* picks where it plays (the same screen(s) it was recorded on, or any screen you choose). Each macro remembers the monitor layout it was recorded with, so it doesn't matter how many screens are connected later: positions move to the matching screen (or the main one if that screen is unplugged), are rescaled to its resolution, and never end up off-screen. The status bar shows where it was recorded and where it will play.

> Rescaling works best for apps and games that fill the screen and scale their UI with the resolution. If the aspect ratio changes (16:9 → 16:10), positions are stretched to match.

### Fix one part without redoing everything

<img src="docs/preview-editor.png" alt="Timeline editor with a selected range" width="900">

- **Drag** across the timeline to select the part you want to change. Drag the blue edges to fine-tune it.
- Press **F8** to re-record it straight away: with a selection, F8 records over just that part instead of starting a new macro.
- Or press **● Re-record selection** (Ctrl+R) or the record button. Nothing is recorded yet: get ready in the app you're recording, then press **F8** to start (Esc or Stop cancels). Do that part again and press **F8** to finish. Only the selection is replaced and everything after it shifts to fit. When you press F8 the cursor is first moved to where it was at that moment, so the new take joins up with the rest.
- **● Insert recording** (Ctrl+I) adds a new take at the red playhead. It also waits for **F8** before it starts.
- **Cut / Copy / Paste / Delete** work on the selection. **More ▾** has *insert pause*, *change speed of a section*, *trim* and *remove / simplify mouse moves*.
- **Click** a key or click block to select it and **drag** it to move it in time. Double-click to edit exact times, delays and coordinates in the event table.
- **▶ Preview selection** plays only the selected part. **Ctrl+Z / Ctrl+Y** undo and redo everything.
- **Ctrl + mouse wheel** zooms, the mouse wheel scrolls, **Fit** shows the whole macro.

<img src="docs/preview-rerecord.png" alt="Re-recording a selected section" width="900">

### Screen checkpoints & phone alerts

A lag spike can make a macro turn a little too far or click a little too early, and from then on every click lands in the wrong place. Checkpoints catch that: at chosen moments the macro compares the screen with a picture of how it should look, and **stops before doing any damage** if it doesn't match.

1. **Add checkpoints.** While recording or playing, press **F7** at the moments that matter (a menu is open, you're standing at the right spot…). The checkpoint is added at that exact moment of the macro. One added during playback is checked from the next playback on; the run in progress plays on unchanged. When the macro isn't playing, **F7** or **◆ Checkpoint** adds one at the playhead. Checkpoints show up as green ◆ on the *Checks* track and move with cuts, inserts and re-recordings like any other event. When the macro plays on another screen (*Play on*) or resolution, each checkpoint's area moves and rescales with it, just like the clicks.
2. **Choose what to compare.** Double-click a ◆ and drag over the part of the screen that must look like this, such as the shop window or a HUD element. Leave out things that change on their own (chat, timers, animations). A tight area works best: a change inside it counts for more of the score.
3. **Decide how strict.** *Required match* (default 80%) is how much of the area must look the same, so a popup covering a corner doesn't stop the macro. *Wait up to* (default 5 s) gives a laggy screen time to catch up: playback pauses at the checkpoint and keeps checking, and everything after it waits too. *Allowed shift* lets the picture be a few pixels off. **Test against the screen now** shows the score you'd get.

When a checkpoint fails, the macro **stops, releases every held key and mouse button**, beeps and shows what it expected next to what was on screen, including a hint when the view looks shifted (e.g. *"looks shifted by 60px left"*). Press F9 to continue from the failed checkpoint once things are fixed. *Screen checkpoints* under **Safety** turns checking off for a run.

**Phone alerts** (Settings → Checkpoints & alerts) send you a notification with a screenshot when a checkpoint fails:

- **ntfy** (easiest, free, no account): install the [ntfy app](https://ntfy.sh), press *New random topic* in Settings and subscribe to that topic in the app.
- **Telegram:** create a bot with @BotFather, paste its token, send the bot a message and press *Find my chat ID*.
- **Discord:** paste a channel webhook URL.

Press *Send a test alert* to check it works. Tokens and webhooks are stored in the app's settings on this PC, never in `.mttk` files, so sharing a macro doesn't share them.

**Discord pings on errors.** *Ping on errors* (default `@everyone`) is mentioned in every checkpoint-failure alert, so errors stand out from the progress log and ring your phone even if the channel only notifies for mentions. Use `@here`, your user ID (Discord settings → Advanced → Developer Mode, then right-click your name → *Copy User ID*) to ping only you, `&` plus a role ID for a role, or leave it empty. Other messages never ping.

**Progress log.** Under *Progress log* you can also get a message when playback starts, at the start of every run (with how long the last run took) and when it finishes or is stopped. Messages arrive in order; if runs are only a few seconds long, some run messages are skipped so the log keeps up.

**Screenshot points: a picture at a set moment.** To get a picture of the screen at, say, 1:34 into the macro, right-click the timeline there and choose **📷 Send a screenshot here** (or use the arrow next to **◆ Checkpoint**). Type the exact time in the *at* box, drag over the part of the screen to send (or keep the whole screen) and press *Send a test picture now* to try it. Every time playback reaches that moment, the picture is sent with the run number. Screenshot points show up as blue dots on the *Checks* track and never pause or stop the macro. Any checkpoint can do both: tick *Send a picture of the screen* on a normal checkpoint and it sends the whole screen, with the checked area outlined, whenever it matches.

> Checkpoints compare pictures; they don't read text. The app's own window and the REC / PLAY indicator are left out of the screenshots. Some games in exclusive full-screen mode show up black in screenshots: use borderless / windowed mode if *Test against the screen now* shows a black picture.

### Small and always on top

The **pin** button keeps the window above your game or app, and **compact mode** hides the editor:

<img src="docs/preview-compact.png" alt="Compact mode" width="600">

Under **Settings** you'll also find window opacity, minimize to tray, auto-hide while recording or playing, a small click-through **REC / PLAY** indicator, a countdown before recording, mouse-move sampling and the centre-mouse options (which monitor, animation speed and style).

<img src="docs/preview-hotkeys.png" alt="Hotkey settings" width="560">

## Tips

- **Keys work like TinyTask's:** keys are played with the same Windows call TinyTask uses (`keybd_event` with the key code *and* the keyboard's scan code), so games and other programs that read scan codes see them too. New recordings also remember exactly which physical key was pressed (e.g. numpad Enter vs Enter, right vs left Ctrl/Alt). Re-record older macros if a key still isn't picked up.
- **Mouse movement in games:** mouse moves are played as real mouse input (like a real mouse by default), not just by placing the pointer, so things that react to the mouse while you hold a key (selection wheels, dragging, camera) respond. If a program reacts oddly, try *Settings → Playback → Mouse movement → Like TinyTask*.
- **Admin apps / some games:** Windows blocks input from normal programs into apps running as Administrator. Right-click the exe → **Run as administrator** if playback doesn't reach them.
- **Older macros (before 0.0.4)** only remember the main screen. When you open one, the app finds the screen it was recorded on: the screen its checkpoint pictures were taken on, one connected now, or a screen layout it has seen on this PC before. That layout is saved into the macro the next time you save it. If none of these works, it assumes the screen sat next to the main one, with the size of a screen this PC has had, and positions always stay on-screen.
- **Huge recordings:** *More ▾ → Simplify mouse moves* thins out mouse movement without touching clicks or keys.

## Run from source

Requires Python 3.10+.

```bat
git clone https://github.com/BarBaroNN/more-than-tiny-task.git
cd more-than-tiny-task
run.bat
```

`build.bat` builds `dist\MoreThanTinyTask.exe` locally. To publish a new version, see [RELEASING.md](RELEASING.md).

<details>
<summary>Development</summary>

```bash
pip install -r requirements-dev.txt
python -m pytest -q tests
```

- `mttk/model.py`: event model and pure editing operations (unit tested)
- `mttk/backend.py`: recorder, player, global hotkeys, mouse centring (pynput)
- `mttk/vision.py`: screen checkpoints: screenshot comparison and the checker used during playback
- `mttk/notify.py`: phone alerts (ntfy, Telegram, Discord) using only the standard library
- `mttk/ui/`: PySide6 interface (timeline, event table, settings, theme)

</details>
