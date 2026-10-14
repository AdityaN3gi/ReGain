# ReGain

**Muscle-controlled rehab games.** A small EMG sensor reads one muscle, and you play a retro game with it: fly a bird through pipes, or drive a car up a hill. A therapist sets the plan (target zone, hold time, rest time, reps), the player just plays, and the game records how well the plan was followed.

> **Not a medical device.** ReGain is a hackathon prototype. Use it only under the plan of a physiotherapist or doctor, and stop if you feel sharp pain.

---

## Contents

1. [How it works](#how-it-works)
2. [What you need](#what-you-need)
3. [Hardware setup](#hardware-setup)
4. [Software setup](#software-setup) (macOS, Windows, Linux)
5. [Running the games](#running-the-games)
6. [Playing a session](#playing-a-session)
7. [The pain rating](#the-pain-rating)
8. [Training modes](#training-modes)
9. [Reflex tracking](#reflex-tracking)
10. [The research behind it](#the-research-behind-it)
11. [Troubleshooting](#troubleshooting)
12. [Files](#files)

---

## How it works

```
 muscle  ->  gel electrodes  ->  BioAmp EXG Pill  ->  Arduino Uno (A0)  ->  USB  ->  emg_core.py  ->  game
 (EMG)       (IN+, IN-, REF)     (amplifies signal)   (500 samples/s)                 (filter + envelope)
```

1. The **Pill** amplifies the tiny electrical signal your muscle makes when it contracts.
2. `emg_stream.ino` on the Arduino reads that signal 500 times a second and sends the numbers over USB.
3. `emg_core.py` removes drift (20 Hz high-pass) and mains hum (50 Hz notch; change `MAINS_HZ` to 60 in the US), then smooths it into an **envelope**: a single number for "how hard is the muscle working right now".
4. Each game calibrates that number to **you**: 0% is your relaxed level and 100% is your own maximum squeeze. Every target in the game is a percentage of that range, so the same plan works for any person or muscle.

---

## What you need

| Item | Notes |
|---|---|
| Arduino Uno R4 Minima (or Uno R3) | The project was developed on the R4 Minima |
| BioAmp EXG Pill (Upside Down Labs) | Or a Muscle BioAmp Shield, see [Hardware setup](#hardware-setup) |
| BioAmp cable + 3 gel electrodes | One of them is the reference electrode |
| 3 jumper wires (female to male) | For OUT, VCC and GND |
| USB cable **with data** | Charge-only cables will not work |
| A computer with Python 3 | Developed on Python 3.12 |

---

## Hardware setup

### 1. Wire the Pill to the Arduino

| Pill pin | Arduino pin |
|---|---|
| **OUT** | **A0** |
| **VCC** | **5V** |
| **GND** | **GND** |

> **Check twice before powering on.** Upside Down Labs warns that swapping VCC and GND can permanently destroy the Pill. Match the wires to the labels printed on the Pill, not just to their colours, and plug the USB cable in last.

Use the **power-header** GND next to 5V to keep the wires together (any GND works, they are all connected).

**Using the Muscle BioAmp Shield instead?** Stack it on the Arduino, plug the electrode cable into its ELECTRODES connector, and use no jumper wires. Check the shield's documentation for which analog pin carries the signal; if it is not A0, change `#define INPUT_PIN A0` in `emg_stream.ino`.

### 2. Place the electrodes

The cable carries three electrodes:

- **IN+ and IN-**: on the belly of the muscle you want to train, a few centimetres apart along its length (make a fist and feel where the forearm bulges).
- **REF**: on a **bony, non-muscular spot**, for example the back of the wrist or the elbow.

Tips:

- Wipe the skin with an alcohol swab and let it dry before sticking the pads on. Poor contact is the most common cause of a flat or noisy signal.
- Keep the electrode cable away from the jumper wires and tape loose wires down so they do not tug on the pads.
- If the signal is noisy, try running the laptop on battery rather than on its charger.
- Never put electrodes across the chest or on broken skin.

---

## Software setup

### 1. Get the code

Keep these files in **one folder**:

```
regain_arcade.py   regain_flappy_retro.py   regain_climb_retro.py   emg_core.py
requirements.txt   calibrate.py             live_plot.py            emg_stream/emg_stream.ino
```

(The Arduino IDE needs the sketch inside a folder with the same name, so the file lives at `emg_stream/emg_stream.ino`.)

### 2. Install Python packages

#### macOS

```bash
cd ReGain
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If `python3` is missing, install Python from [python.org](https://www.python.org/downloads/) or with Homebrew (`brew install python`).

#### Windows

Install Python from [python.org](https://www.python.org/downloads/) and tick **"Add python.exe to PATH"**. Then in PowerShell or Command Prompt:

```powershell
cd ReGain
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

If PowerShell refuses to run the activate script, run `Set-ExecutionPolicy -Scope Process RemoteSigned` first, or use Command Prompt instead.

#### Linux (Debian / Ubuntu / Mint)

```bash
sudo apt install python3-venv python3-tk
cd ReGain
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Serial ports need permission and, on some systems, ModemManager grabs the Arduino:

```bash
sudo usermod -aG dialout $USER     # then log out and back in
sudo systemctl stop ModemManager   # only if the port is "busy" (or: sudo systemctl disable ModemManager)
```

> **Every new terminal:** activate the virtual environment again (`source .venv/bin/activate` on macOS and Linux, `.venv\Scripts\activate` on Windows). Without it, Python will say `No module named 'pygame'`.

### 3. Upload the Arduino sketch

1. Install the [Arduino IDE 2](https://www.arduino.cc/en/software).
2. Open **Tools > Board > Boards Manager**, search for **Arduino UNO R4 Boards** and install it (skip this for an Uno R3).
3. Open `emg_stream/emg_stream.ino`, choose **Tools > Board > Arduino UNO R4 Minima** and your port, and click **Upload**.

If the upload fails with `dfu-util: No DFU capable USB device available` (Uno R4):

- **Double-tap the RESET button** so the small orange `L` LED pulses, choose the port that appears, and upload again.
- **Linux:** the IDE prints a command after installing the board package, similar to `sudo "~/.arduino15/packages/arduino/hardware/renesas_uno/<version>/post_install.sh"`. Run it, unplug and replug the board, and try again.
- Try a different USB cable or port.

The R4 does not reset when a program opens the serial port, so you only need to upload once. **Close the Arduino Serial Monitor before running any Python program**: only one program can use the port at a time.

### 4. Check the signal

```bash
python3 calibrate.py        # Windows: py calibrate.py
```

It records a relaxed and a maximum squeeze and prints two numbers. They should be clearly different (for example `rest = 1.5`, `max = 35`). If they are almost the same, see [Troubleshooting](#troubleshooting). `live_plot.py` draws the filtered signal and its envelope live, which is handy for checking electrode contact.

(The games do their own calibration inside the game, so `calibration.json` from `calibrate.py` is only a hardware test.)

---

## Running the games

The **Arcade** opens one retro window, lets you pick a game, and keeps the sensor connected between games.

| System | Demo mode (no hardware) | Real sensor, auto-detected port | Real sensor, port given |
|---|---|---|---|
| macOS | `python3 regain_arcade.py demo` | `python3 regain_arcade.py auto` | `python3 regain_arcade.py /dev/cu.usbmodem1101` |
| Windows | `py regain_arcade.py demo` | `py regain_arcade.py auto` | `py regain_arcade.py COM3` |
| Linux | `python3 regain_arcade.py demo` | `python3 regain_arcade.py auto` | `python3 regain_arcade.py /dev/ttyACM0` |

Find your port in the Arduino IDE under **Tools > Port**. A game can also be launched on its own, for example `python3 regain_climb_retro.py auto`.

**Demo mode** needs no hardware: hold **Space** to squeeze, or press **0-9** for a steady 0-90% effort level. It is perfect for testing the games and for presenting.

Menu options: **Options > Pixel Mode** switches the game scene between a chunky low-resolution look and full resolution (dialogs and text are unaffected). **Help** shows how each game works.

---

## Playing a session

Every session follows the same flow:

1. **Session Setup**: pick **Recovery** or **Training** and set the plan (see [Training modes](#training-modes)). Recovery is the default every time a game opens.
2. **Pain Check (before)**: rate your pain from 0 to 10.
3. **Calibration**: press **B** while the muscle is fully relaxed (3 seconds), then **X** while squeezing as hard as is *comfortable* (3 seconds, no pain). The game checks that the two are different enough. If it says the squeeze is too close to relaxed, check the electrodes and record again.
4. **Play**: the game shows a Go/Relax cue, your effort, and live feedback.
5. **Pain Check (after)**: rate your pain again.
6. **Results**: a summary table, and your reflex times.

---

## The pain rating

Before and after every session the game asks one question: *"How much does it hurt right now?"* on a **0-10 scale** (0 = no pain, 10 = worst pain imaginable). Rate the injured area. Use the slider, the arrow keys, or click.

What the game does with the answers:

| Situation | What happens |
|---|---|
| Pain **before** is **6 or more** | A warning appears: check with your physiotherapist before loading this muscle |
| Pain **after** is **2 or more points higher** than before | The results screen shows a red warning to tell your physiotherapist before the next session |
| Always | "Pain before to after" appears in the results table |

The ratings are advisory: they never block a session, and they are shown on screen but **not saved to a file**, so write them down if you need a record. The thresholds (6 and +2) are this project's own conservative choices, so a clinician should set the real limits for each patient.

**Why pain is tracked at all.** In tendon and muscle rehabilitation, "no pain" is not always the goal. A well-known approach, the *pain-monitoring model* used in Achilles tendinopathy research, lets patients load the tendon with some pain, up to roughly 5/10 during exercise, as long as it settles soon after and is gone by the next morning, and it should not get worse from week to week ([Silbernagel et al., 2007](https://search.pedro.org.au/search-results/record-detail/18389)). In that randomised trial of 38 patients, continuing pain-monitored loading showed no negative effects compared with complete rest. ReGain applies the same idea at session level: it asks for a rating before and after, so a rise in pain can be spotted.

A change of about **2 points** on the 0-10 numeric rating scale is commonly treated as the minimal important difference, and the scale itself has good reliability in people with limb musculoskeletal problems ([systematic review in Zdravstveni Vestnik](https://vestnik.szd.si/index.php/ZdravVest/article/view/3108)). That is where the "+2" flag comes from.

---

## Training modes

Both games have two modes, chosen on the Session Setup screen.

### Recovery mode (default)

A repeated **hold - rest** pattern, like a prescribed set of repetitions. The therapist sets a **target zone** (for example 40-60% of max) and the player has to hold their effort inside it.

| Setting | ReGain Flap | ReGain Climb |
|---|---|---|
| Target zone: low / high | 40% / 60% | 40% / 60% ("sweet spot") |
| Hold time | 5 s (2-15) | 6 s per hill (3-15) |
| Rest time | 3 s (1-10) | 5 s per hill (2-15) |
| Repetitions | 5 (1-12) | 4 hills (1-8) |
| Gradual load added per rep | - | 5% per hill (0, 5 or 10) |
| Ceiling warning | 80% | 80% |

- **ReGain Flap**: squeeze to lift the bird, relax to let it sink. The gap in each pipe sits at the target zone. A rep counts when you stay inside the gap for at least 80% of the pipe. Between pipes you relax fully. Stars: 3 for 80% of time in the zone, 2 for 60%, 1 for 40%.
- **ReGain Climb**: your muscle is the gas pedal. Power is highest in the sweet spot and fades if you squeeze too little **or too much**, so it rewards *controlled* effort rather than maximum effort. Each hill takes the set climb time at best, with fuel for about 1.35 times that. At the **rest stop** the fuel pump only runs while your muscle is relaxed (15% or less), so the rest is genuinely taken. If *gradual load* is on, the sweet spot rises on every hill and the hills get steeper.

### Training mode

A **long single hold** at or above an effort threshold. There are no rest cues; the question is "how long can you keep it up?".

| Setting | ReGain Flap | ReGain Climb |
|---|---|---|
| Effort threshold | 70% (30-95) | 70% (10-95) |
| Ceiling warning | 80% | 80% |

- **ReGain Flap**: hold the bird above the threshold line for as long as you can. The session ends when you drop below the threshold for more than 1.5 seconds. Your longest hold is saved as the best on this device (`regain_best.txt`).
- **ReGain Climb**: one very long incline with a milestone sign every 10 seconds of full-power climbing. Stay at or above the threshold to climb; below it the car slows and rolls back. If you stay below the threshold for more than 2 seconds the climb ends. The results show your longest hold, time above threshold and the distance climbed.

In both modes, going **above the ceiling** triggers a warning (a red flash) and, in Climb, the engine overheats and loses power until you ease off. This protects against over-exertion: the goal is controlled effort.

**Defaults are for demonstration.** For example, the default hold times are a few seconds so a demo is quick. A therapist would set the values for a real plan.

---

## Reflex tracking

Every rep starts with a **GO!** cue and ends with a **RELAX!** cue. The game measures how long it takes for your effort to reach the target zone's lower edge (squeeze reaction), and to drop to 15% or less (relax reaction). A cue with no response within 4 seconds counts as missed. Training mode measures only the squeeze reaction.

Times are shown during play, summarised in the results, and appended to `reflex_log.csv` with these columns:

`session, clock, hill, event, latency_s, threshold_pct, effort_at_cue_pct`

so progress can be compared between sessions. The times include roughly 0.2 s of smoothing, so compare sessions that use the same setup.

---

## The research behind it

ReGain combines three ideas that each have some research behind them: **EMG biofeedback**, **monitored isometric / progressive loading**, and **games for adherence**. The evidence is **mixed**, and ReGain's own settings and thresholds have not been clinically validated.

| Design choice | What the research says | Caveat |
|---|---|---|
| Show the muscle signal live (EMG biofeedback) | A systematic review of sEMG feedback in sports and orthopaedic rehabilitation suggests it can improve strength, muscle control, pain, function and range of motion ([Haab, 2024](https://explorationpub.com/Journals/emd/Article/100765)). In stroke, EMG biofeedback beat conventional therapy alone for ankle dorsiflexion strength, but not clearly for gait speed ([Moreland et al., 1998](https://search.pedro.org.au/search-results/record-detail/397)). | Reviews disagree with each other, and a review of quadriceps biofeedback trials found low average study quality ([PEDro score 4.6/10](https://lida.sport-iat.de/ta/Record/4023517)). For neck pain, biofeedback is suggested as an **adjunct, not the main treatment** ([Campo et al.](https://mercy.edu/news-events/news/professor-marc-campo-published-musculoskeletal-science-and-practice-academic-0)). |
| Hold a steady effort at a set % of max (isometric holds); Training threshold default 70% | In a small study of 6 athletes with patellar tendon pain, a single bout of heavy isometrics (5 holds of 45 s at 70% of max, with 2 minutes' rest) reduced pain immediately, for at least 45 minutes ([Rio et al., summarised by IASP](https://www.iasp-pain.org/publications/relief-news/article/isometrics-tendon-pain/)). A later in-season trial found isometrics more analgesic than isotonic exercise ([summary](https://medbridge.com/blog/2016/09/the-true-benefit-of-isometric-exercises)). | Small samples, and at least one later study reported **no** pain-relieving effect ([summary](https://www.physiotutors.com/nl/isometrisch-spierwerk-als-wondermiddel-om-pijn-te-stillen/)). ReGain's default holds (5-6 s) are far shorter than 45 s, so they are for demos. Use Training mode or longer hold times for real long holds. |
| Controlled effort with a **ceiling**, plus a **pain check before and after** | The [pain-monitoring model](https://search.pedro.org.au/search-results/record-detail/18389): pain up to about 5/10 is acceptable during loading if it settles quickly and does not carry into the next day. A 2-point change is the usual minimal important difference on the 0-10 scale ([review](https://vestnik.szd.si/index.php/ZdravVest/article/view/3108)). | Studied mainly in Achilles tendinopathy. The ceiling % and the 6 / +2 pain flags in ReGain are design choices, not taken from a trial. |
| **Gradual load** per hill (Climb) | Progressive loading is the basis of tendon rehabilitation protocols such as the one in [Silbernagel & Crossley (JOSPT 2015)](https://www.jospt.org/doi/full/10.2519/jospt.2015.5885). | Climb's 5% step is an arbitrary default. |
| **GO / RELAX reflex times** and **rest only counts when relaxed** | After a stroke, finger muscles often stay active after a grip, so people are slow to let go ([case report with background](https://pmc.ncbi.nlm.nih.gov/articles/PMC4066970)). A small student project suggested relaxation time may improve with feedback, but it involved one patient and the result was not statistically significant ([abstract](https://uen.pressbooks.pub/range24i2/chapter/murali/)). | Preliminary. Reflex time here is a simple practice measure, not a clinical test. |
| Make it a **game** | Reviews report better adherence and compliance with serious games in rehabilitation, and custom-built games tended to give better medical results than off-the-shelf ones ([JMIR Serious Games, 2021](https://preprints.jmir.org/preprint/20066)). | Another review found long-term effects and adherence **inconclusive** because study quality varies ([Taut et al.](https://ehps.net/ehp/index.php/contents/article/view/1029)). |

**Bottom line:** biofeedback and monitored loading are reasonable, evidence-informed ways to support rehab, and games may help people keep doing it. ReGain is a prototype to show the idea. It does not diagnose, treat or replace professional guidance.

---

## Troubleshooting

| Problem | What to try |
|---|---|
| `No module named 'pygame'` | Activate the virtual environment first, then `pip install -r requirements.txt` |
| `python` not found (Linux/macOS) | Use `python3`. On Windows use `py` |
| `Serial problem: ... resource busy` | Close the Arduino Serial Monitor, `live_plot.py`, `calibrate.py` and any other copy of the game. Only one program can open the port |
| `Permission denied` on the port (Linux) | Add yourself to the `dialout` group and log out and in. Stop ModemManager if needed |
| `No Arduino port found` | Check the USB cable carries data, then try giving the port yourself (see [Running the games](#running-the-games)) |
| `dfu-util: No DFU capable USB device available` | Double-tap RESET on the Uno R4, pick the new port, upload again (see [step 3](#3-upload-the-arduino-sketch)) |
| Serial Monitor shows the same number all the time | The Pill is not seeing your muscle: check the electrode plug is fully seated, pads are on bare skin, and **REF** is on a bony spot |
| Number stuck at about 0 or about 1023 | Check yellow = OUT -> **A0**, red = VCC -> **5V**, black = GND -> **GND**, and that every plug is pushed in all the way |
| Calibration says the squeeze is "too close to relaxed" | Re-seat electrodes and squeeze harder. If the signal clearly moves but is small, raise `GAIN` (in `class Signal` in `regain_climb_retro.py` when you use the Arcade; `GAIN` near the top of `regain_flappy_retro.py` when you run Flap on its own) |
| The bar moves by itself | You are probably in **demo mode** or calibration was recorded with a flat signal. Start the game with `auto` and calibrate again |
| Signal is noisy | Re-do skin prep, keep wires still, run the laptop on battery, and check `MAINS_HZ` in `emg_core.py` matches your country (50 or 60) |

---

## Files

| File | What it is |
|---|---|
| `regain_arcade.py` | Retro window that lets you pick a game and shares one sensor connection |
| `regain_flappy_retro.py` | **ReGain Flap** (bird and pipes), Recovery and Training |
| `regain_climb_retro.py` | **ReGain Climb** (car and hills), Recovery and Training, plus the shared retro UI |
| `emg_core.py` | Reads the serial stream, filters it and computes the muscle envelope |
| `emg_stream/emg_stream.ino` | Arduino sketch: streams analog samples at 500 Hz, 115200 baud |
| `calibrate.py` | Command-line hardware check: records relaxed and max, shows a live % bar |
| `live_plot.py` | Live plot of the filtered signal and envelope |
| `requirements.txt` | Python packages |
| `reflex_log.csv` | Created by the games: reaction times for every cue |
| `regain_best.txt` | Created by Flap Training: your longest hold |

---

*ReGain is an educational prototype and is not a medical device. It does not diagnose, treat or prevent any condition. Always follow the advice of your physiotherapist or doctor.*
