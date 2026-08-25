# OIML R 106-1 Type Evaluation — Prep Notes (Indicator Module)

Scope: this covers only the electronics/software module we're responsible for —
**IND570 + `basic_interface_ind570.py`** — submitted to the lab as a *module*
under OIML R 106-1 §5.1.4, not the complete rail-weighbridge (load receptor,
rails, aprons, wagon recognition are handled elsewhere and tested separately).

The deployed field system does dynamic in-motion weighing; what we've built
here is the display/indicator layer, which is why it's evaluated as a module
rather than a complete instrument.

---

## 1. Why "module" evaluation (§5.1.4)

Testing a complete rail-weighbridge as one unit isn't practical here since
we don't own the mechanical/rail portion. §5.1.4 explicitly allows this:

> "the manufacturer may define and submit modules to be examined separately...
> where modules are manufactured and/or placed on the market as separate units
> to be incorporated in a complete instrument"

Typical modules listed in §0.2.6 include: *load cell, indicator, analogue or
digital data processing device, weighing module, terminal, primary display.*
Our module maps to **"indicator"** (§0.2.6.4) — displays the weighing result,
does not itself have the load receptor.

### 1.1 Error apportionment (§5.1.4.1)

Each module gets a fraction `pi` of the complete instrument's mpe. For an
"electronic indicator" specifically, Table 5 (§5.1.4.1) gives:

| Performance criteria | Electronic indicator `pi` |
|---|---|
| Combined effect (non-linearity, hysteresis, temp effect on span, repeatability) | 0.5 |
| Temperature effect on no-load indication | 0.5 |
| Power supply variation | 1 |
| Span stability | 1 |

These fractions apply once the accuracy class of the *complete* instrument is
known — confirm with whoever owns the mechanical/rail module what accuracy
class (0.2 / 0.5 / 1 / 2) the complete system targets, since our module's
error budget is a fraction of that, not a number we can pick independently.

**Action needed:** get the target accuracy class from the mechanical/rail
team before the lab can compute pass/fail thresholds for our module's tests.

---

## 2. Documentation to submit (§5.1.1)

Checklist, with status against what exists in this repo today:

| Item | Status | Notes |
|---|---|---|
| Metrological characteristics of the module | ⬜ Not written | Need scale interval (d) as configured/expected from IND570, resolution |
| Functional description of components | ✅ `SYSTEM_OVERVIEW.md` | Covers threading model, UI, data flow — usable as a base |
| Drawings/diagrams/photos | ⬜ Not prepared | A block diagram of IND570 → TCP → app → display would satisfy this |
| Interface documentation (§4.3.5.1) | ✅ `ETHERNET_DATA_TRANSMISSION.md` | Covers the TCP link; needs light editing into a formal "list of commands" format (see §5 below) |
| Software documentation (§3.8.1) | 🟡 Partial | See §3 below — several sub-items missing |
| Software identification (§0.2.8.5, §3.11.1) | ✅ Done this session | `SOFTWARE_ID = "1.0.0"` constant, shown in window title |
| Description of securing components/interlocks | ⬜ Not applicable / not implemented | See §4 — flagging as a gap, not a false "N/A" |
| Data storage device description (§3.5) | ⬜ N/A currently | App does not persist measurement records — flag to lab as "not implemented in this module" rather than omitting |
| Operating manual | 🟡 Partial | `SYSTEM_OVERVIEW.md` functions as one but isn't labeled/structured as such yet |

---

## 3. Software documentation gaps (§3.8.1)

§3.8.1 lists what manufacturer software documentation "may include":

a) **Description of legally relevant software** — ⬜ not done. Need to state
   plainly: the entire `basic_interface_ind570.py` is legally relevant per
   §0.2.8.6 ("if no software separation exists, the whole software is to be
   considered as legally relevant") — there is currently no separation
   between metrologically relevant code (parsing, averaging, calibration)
   and non-relevant code (UI chrome). Simplest honest answer for the lab:
   declare the whole module as legally relevant rather than attempt a
   separation that doesn't exist yet.

b) **Description of the accuracy of the measuring algorithms** — ⬜ not
   written. This should describe: the rolling-average method (`SharedState`,
   arithmetic mean over a configurable window), the unit-normalization
   conversions (kg/g/lb/t → kg), and explicitly flag that **"Tons" mode
   currently does not apply a ÷1000 conversion** (see `SYSTEM_OVERVIEW.md`
   §5) — this is exactly the kind of accuracy claim a type evaluator will
   check, so it needs to either be fixed or explicitly documented as
   intentional before submission.

c) **Description of the user interface, menus and dialogues** — ✅ covered
   in `SYSTEM_OVERVIEW.md` §4.

d) **Unambiguous software identification** — ✅ done this session.

e) **Description of the embedded software** — N/A (this runs on a general
   PC, not embedded firmware) — should be stated as such rather than left
   blank.

f) **Overview of system hardware** — 🟡 partial. `ETHERNET_DATA_TRANSMISSION.md`
   covers the network topology; a one-paragraph "runs on a standard Windows
   PC, no embedded hardware" statement would close this out.

g) **Means of securing software** — ⬜ real gap, see §4.

h) **Operating manual** — 🟡 as above.

---

## 3a. Descriptive markings owed by this module (§3.11)

§3.11 requires markings "at each location having a mass indicating device" —
our display screen is that location. §3.11.4 explicitly allows markings to
be shown on a software-controlled display instead of a physical plate,
**provided**:

- Max, Min, and d are displayed as long as the instrument is switched on
- other markings may be shown on manual command
- this is described in the type approval certificate
- the markings are treated as device-specific parameters (§0.2.8.4) — i.e.
  subject to the same securing requirements as §3.8/§3.9

Current UI (`basic_interface_ind570.py`) does **not** display any of these
persistently. Concretely missing from the always-visible screen:

| Marking | Required by | Currently shown? |
|---|---|---|
| Max (maximum capacity) | §3.11.4 (always-on) | ❌ no |
| Min (minimum capacity) | §3.11.4 (always-on) | ❌ no |
| d (scale interval) | §3.11.4 (always-on) | ❌ no |
| Software identification | §3.11.1 | ✅ now in window title (`SOFTWARE_ID`) |
| Manufacturer name/ID | §3.11.4 (on the plate even when display-controlled) | ❌ no |
| Supply voltage | §3.11.4 | N/A — not applicable to PC software; state explicitly |
| Type approval sign | §3.11.4 | Not yet issued — placeholder until approval granted |
| Accuracy class | §3.11.2.1 (code, on manual command acceptable) | ❌ no |
| Weighing method | §3.11.1 | ❌ no — should state "static display of IND570-sourced value" |

**This is a real, fixable UI gap** — if the plan is to rely on §3.11.4's
allowance for a software-controlled display rather than a physical plate,
the app needs a persistent status area (or footer) showing at minimum
Max/Min/d, and a way to bring up the rest "on manual command" (e.g. an
About/Info dialog). Worth deciding whether to build this before submission
or handle descriptive markings via a physical plate on the IND570 instead,
which may already satisfy this if the IND570 itself carries these markings.

---

## 4. Securing requirements (§3.8.2, §3.9) — genuine gaps

This is the area with the most actual work, not just documentation:

- **§3.8.2(a):** "legally relevant software shall be adequately protected
  against accidental or intentional changes." Currently: the `.py` source
  and the packaged `.exe` have no integrity protection, no code signing, no
  checksum verification at runtime. Worth discussing with the lab what's
  acceptable for a module submission — a common minimum is documenting the
  build/release process (this repo's GitHub Actions workflow) as the
  control, plus the software-identification string added this session as
  the means of detecting drift.
- **§3.9.2(a):** access control by code/key for anything that changes
  legally-relevant parameters. Currently: **no such control exists** —
  `IND570_HOST`/`IND570_PORT` and the averaging window are either hardcoded
  or freely editable via the UI (the "Average by N measurements" spinner,
  calibration offset fields) with no authorization step. If the calibration
  offset (`_cal_offset`, per-channel) is considered a legally-relevant
  parameter, this is a real compliance gap, not just a documentation one —
  flag this specifically to whoever is coordinating the submission, since it
  may need a code change (e.g., a password-gated calibration mode) before
  the lab will accept it.
- **§3.9.2(b):** intervention records (who changed what, when) — not
  implemented. No audit trail exists in the app.

**These three are the items most likely to actually block type approval as
currently built** — worth raising early with the lab/certifying body rather
than discovering it during evaluation.

---

## 5. Interface documentation format (§4.3.5.1)

The lab wants, formally: (a) list of all commands, (b) description of the
software interface, (c) list of all commands together [sic — likely
duplicate wording in the standard], (d) effect of each on functions/data.

`ETHERNET_DATA_TRANSMISSION.md` already has the substance (TCP client,
port 1702, ASCII line protocol, regex-based parsing) but not in a
command-list format, because the IND570 link isn't a command/response
protocol — it's a continuous one-way data stream. Recommend explicitly
stating this asymmetry to the lab: "the interface accepts no commands from
this module; it is a read-only continuous data stream from the IND570,"
which satisfies (a)–(d) by stating there are no commands rather than
leaving the section blank.

---

## 6. What the lab will test on our module specifically (Annex A, as a module)

Per §5.1.2, "Instruments may be tested on the premises of the metrological
authority" — for a module, expect at minimum:

- **A.6.2** Agreement between indicating and printing devices — N/A, no
  printing device exists in this module; state explicitly.
- **A.6.4** Functionality at voltages below minimum operating voltage — N/A
  for software running on a general PC; the PC's own power behavior isn't
  ours to certify. Flag this boundary explicitly to the lab.
- **A.7.2 / A.7.3** Influence factor and disturbance tests (temperature,
  humidity, AC voltage, ESD, bursts, surges, EMC) — these typically apply to
  the **indicator hardware** (the IND570 itself, which Mettler-Toledo has
  presumably already type-approved separately) rather than to display
  software running on a PC. Worth clarifying with the lab whether our
  module even needs Annex A.7 testing, or whether that burden sits with the
  IND570's own existing OIML approval and only the *software's calculation
  accuracy* (A.5, static weighing performance) needs re-verification here.
- **A.8** Span stability — depends on whether "span" means anything at the
  software layer (it doesn't add/remove span, only displays/averages what
  IND570 sends) — likely N/A, but confirm rather than assume.

**Recommend getting this scoping confirmed by the lab in writing before
investing further prep time** — a large fraction of Annex A is written for
instruments with their own load cell/ADC/environmental exposure, which this
software module does not have.

---

## 7. Immediate action items

1. Confirm with the lab/certifying body: does OIML R 106-1 module evaluation
   even apply the way we're assuming, or is there a more specific pathway
   for "display software only, IND570 already certified separately"?
2. Get the target accuracy class from the mechanical/rail team (needed for
   §5.1.4.1 error apportionment).
3. Decide whether to fix the "Tons mode doesn't actually convert" issue
   (`SYSTEM_OVERVIEW.md` §5) before submission, or document it as
   intentional/known — a type evaluator will find this either way.
4. Decide how to handle §3.9.2(a) access control on calibration/config
   parameters — likely needs a code change, not just documentation.
5. Confirm with the lab whether Annex A.7 influence/disturbance testing
   applies to this software module or sits entirely with the IND570's own
   prior approval.
