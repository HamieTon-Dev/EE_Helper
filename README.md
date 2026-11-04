# Multisim Copilot

A Windows desktop app that turns a design brief into simulated, reviewed and documented
circuits:

1. **You provide** a brief (`.docx`, `.txt`, `.md`, or typed instructions), schematic
   images, and optionally a SPICE netlist (`.cir`) or a Multisim circuit (`.ms14`).
2. **An AI model** (Anthropic Claude, OpenAI ChatGPT, or any OpenAI-compatible endpoint)
   reads everything, including the schematic images and the images embedded in the Word
   file, and writes a **simulation plan**: a SPICE netlist plus the analyses to run, each
   tied to a virtual instrument (multimeter, Bode plotter, oscilloscope, DC sweep) and to
   pass/fail checks taken from your requirements.
3. You can **review and edit the plan** (it is plain JSON) before anything runs.
4. **Simulation** runs on ngspice, the open SPICE engine that is compatible with Multisim's
   XSPICE core. Every analysis produces CSV data, an instrument-style figure and
   measured values.
5. **The AI reviews the results.** If a requirement fails, it revises component values
   and the design is simulated again (configurable, default 2 iterations).
6. **Multisim:** the final circuit is opened in Multisim, the interactive simulation runs,
   and the instrument and Grapher windows are captured as screenshots. A `.cir` deck is
   converted into a `.ms14` file you can open and edit. When you supply `.ms14` files, they
   are also opened through Multisim's Automation API (COM).
7. **Report:** `report.html` (self-contained, with images embedded) and an editable
   `report.docx`, with links to every generated file: `.cir`, `.ms14`, CSV, plan JSON and
   logs.

## Lab reports in the course format

Give it the lab handout (`.docx`, with its circuit figures) and it produces the full
report as an editable Word file, in the same structure as a hand-written lab report:

1. **Cover page:** course, instructor, title, student name(s), date. These come only from
   what you enter in Settings; the fields start empty.
2. **Contents:** a table of contents that Word fills in when the file is opened.
3. **Abstract, Introduction, Procedures:** first-person narrative written by the AI.
4. **The handout's parts:** "Part I: …", with every step numbered continuously and the
   action verbs in bold. Blanks such as "fr = ____ Hz" are filled in, and circuit
   figures are placed with "Figure N:" captions.
5. **Data Presentation & Analysis:** numbered tables ("Table N:", including merged
   "Calculated" group headers) and plots ("Plot N:"). Plots are spreadsheet-style
   frequency-response graphs or impedance/current phasor diagrams.
6. **Calculations:** each step written out as
   `Part II step 12: ZT = √(R² + XC²) = √(6,800² + 31,831²) = 32.549 kΩ`.
7. **Required Screenshots:** Multisim captures when Multisim is installed, otherwise
   oscilloscope figures with T1/T2 cursors and a Time/Channel A/Channel B readout,
   Bode plots with a cursor at the resonant peak, and multimeter faces.
8. **Conclusion:** includes percent differences.
9. **References:** in APA format.

**Every number is computed, not typed by the AI.** Table cells, calculations and in-text
values are expressions such as `=mag('sweep','v(n2)',300) | V | .4f` or
`{{meas('scope','theta') | ° | .2f}}`. The program evaluates them against the
simulation data, so measured, calculated and percent-difference values always agree.
If an expression can't be evaluated, the AI is asked to fix it (up to two rounds) before
the report is written.

The simulation side covers what these labs measure:
* readings at listed frequencies: DMM-style RMS values or scope peak values with phase
* circuit variants, such as swapping a capacitor or changing a winding resistance
* oscilloscope period, Δt and phase angle/lag measured from the waveforms
* Bode resonant frequency from the peak or dip of the response

**Setup, once:** in Settings > Lab report, enter the cover details you want: course,
instructor, student name(s), and any references to add to every report. All of them are
optional and start empty. You can also choose:
* a **style template**: any `.docx` whose fonts, heading styles and margins you want. Only
  the styles are kept. Its text, headers, footers, logos, pictures, links, footnotes and
  document properties (author, company …) are all removed.
* a **writing-style example**: a report whose voice and depth the AI should imitate. The
  AI is told never to copy names, schools, courses or pledges from it.

**Each lab:** add the handout and click **Run everything**. To write only the lab report,
set *Report output* to "Lab report (.docx) only" or use
`mscopilot-cli run handout.docx --report lab`. The run folder also contains
`lab_report.json`. You can edit its text and re-render without the AI:
`mscopilot-cli simulate <run>/simulation/plan.json --lab-json <run>/lab_report.json`.

`examples/series_rc_lab/` has a worked series RC lab: handout, plan and report JSON. It
renders with no API key:

```bat
mscopilot-cli simulate examples\series_rc_lab\plan.json --lab-json examples\series_rc_lab\lab_report.json --no-multisim
```

## Install

Download the `MultisimCopilot-windows` artifact from the latest run of the
**Multisim Copilot (Windows .exe)** GitHub Actions workflow, unzip it anywhere, and run
`MultisimCopilot.exe`. The folder contains:

| File | Purpose |
|---|---|
| `MultisimCopilot.exe` | Desktop app |
| `mscopilot-cli.exe` | Command-line version, for scripting and batch runs |
| `MultisimWorker.exe` | 32-bit bridge to Multisim's COM Automation API |
| `ngspice\` | Bundled SPICE engine |
| `examples\` | Sample brief and plan |

The first time you run it, open **Settings**:

* **AI provider + API key.** Keys are stored in Windows Credential Manager, never in a
  file. The environment variables `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or
  `MSCOPILOT_API_KEY` override the stored key.
  * Anthropic: default model `claude-opus-5-5`, with adaptive thinking and an "effort"
    setting. A server-side refusal fallback is enabled.
  * OpenAI: default model `gpt-5`. You can enter any model name.
  * Other: any OpenAI-compatible URL, for example Azure OpenAI, OpenRouter, LM Studio or
    Ollama (`http://localhost:11434/v1`). Images are only used if the model accepts them.
* **multisim.exe**: found automatically under
  `C:\Program Files (x86)\National Instruments\Circuit Design Suite *\`. If yours is
  elsewhere, browse to it.
* **ngspice**: blank uses the bundled copy.

`mscopilot-cli.exe doctor` shows what was detected.

## Use

**Desktop app:** add files, type requirements, then click **Run everything**. To check the
plan before running it, click **Plan with AI**, edit the JSON on the right, then click
**Run the plan shown on the right**. The report opens when the run finishes. Each run gets
its own folder under `Documents\MultisimCopilot\`:

```
20261103_141500_Sallen_Key_low_pass/
  report.html, report.docx
  inputs/                      copies of what you supplied
  iteration_1/, iteration_2/   (or simulation/ for a single pass)
    plan.json                  the plan that was run
    circuit.cir                SPICE deck - opens in Multisim (File > Open)
    circuit_ngspice.cir/.log   engine deck and log
    data/*.csv                 raw data per analysis
    plots/*.png                Bode / scope / multimeter figures
    multisim/circuit.ms14      Multisim circuit (when Multisim is installed)
    multisim_screenshots/      captured instrument and Grapher windows
```

**CLI:**

```bat
mscopilot-cli run brief.docx schematic.png -t "fc must be 1 kHz +/-5%%"
mscopilot-cli plan brief.docx -o plan.json        & rem AI plan only
mscopilot-cli simulate plan.json --review          & rem run an edited plan
mscopilot-cli simulate examples\sallen_key_plan.json --no-multisim   & rem no API key needed
mscopilot-cli doctor --connect --circuit my.ms14   & rem list Multisim's Automation API
```

## How the Multisim integration works (and its limits)

Multisim offers two automation surfaces, and the app uses both:

* **Automation API (COM, `MultisimInterface.MultisimApp`).** This is a 32-bit COM server,
  so it runs in the separate 32-bit `MultisimWorker.exe` process. The worker connects,
  opens `.ms14` files, reads and writes R/L/C values, and lists every method your Multisim
  version exposes in `multisim_api_members.txt`. The API is only available in Multisim
  editions that include automation; the COM steps are skipped, and noted in the report, if
  connecting fails. NI's API reference describes analysis methods per version. You can
  call them by adding entries to `extra.com_calls` in
  `%APPDATA%\MultisimCopilot\config.json`, for example
  `{"com_calls": [{"target": "circuit", "method": "SomeMethod", "args": []}]}`.
* **UI automation (pywinauto)** is what produces the screenshots. It works with any
  edition. It opens the circuit, runs **Simulate > Run**, pauses, captures the main window
  plus every open instrument window (oscilloscope, Bode plotter, multimeter, …), then
  opens **View > Grapher** and captures that too. Tips:
  * Instrument faces that are open when you save a `.ms14` re-open with it. Leave the
    scope, Bode and multimeter windows open in your circuit so they are captured.
  * Raise **Settings > Simulation settle time** for slow circuits.
  * Menu names are matched in English. Localised Multisim installs may skip some steps;
    those steps are logged in the report.

The numbers, figures and pass/fail checks in the report always come from the ngspice run
of the same netlist, so the report is complete even when Multisim automation is
unavailable.

**Engineering caveat:** the AI turns pictures and prose into a netlist, and it records
every interpretation and part substitution (for example, an op-amp macro-model in place
of a vendor model) under *Assumptions* in the report. Read that section, and the netlist,
before relying on the results.

## Build from source

```bat
cd <your clone of this repository>
py -3.12 -m pip install -r requirements-dev.txt
py -3.12 -m pytest
py -3.12 -m mscopilot            & rem run the app from source
powershell packaging\build_windows.ps1
```

The GitHub Actions workflow `.github/workflows/build-windows-exe.yml` does the same
thing on `windows-latest`: it runs the tests, builds the 64-bit app and the 32-bit worker
with PyInstaller, bundles ngspice, smoke-tests the CLI and the worker, and uploads the zip.

## Layout

```
mscopilot/
  main.py            CLI entry point (no args opens the GUI)
  gui_app.py         tkinter desktop app
  config.py          settings, API keys (Credential Manager), tool discovery
  inputs.py          .docx/.txt/.md/images/.cir/.ms14 loading
  plan.py            plan dataclasses, JSON schema, validation
  ai/                provider adapters (Anthropic, OpenAI, compatible), prompts, plan/review/revise
  engines/           netlist generation, ngspice runner, data parsing, measurements
  plots.py           Bode / oscilloscope / multimeter / sweep figures
  multisim/          COM worker + client, UI automation + screenshots
  report.py          HTML and Word engineering reports
  labreport.py       course-format lab report (.docx): schema, template handling, tables, plots
  labexpr.py         safe expression evaluation + number formatting for lab reports
  pipeline.py        end-to-end orchestration
```
