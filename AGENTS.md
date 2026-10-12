# Agent instructions — shani-chronoa

This file applies to any AI coding assistant working in this repository
(Claude Code, opencode, Kilo Code, Cursor, Aider, or similar). Read this
## laya's evidence states: `honest()` collapsed "unanswerable" into "no" (2026-10-12)

`harness-study/laya-decision`'s `laya/evidence.py` states the doctrine in its
opening lines:

> Every state it assigns is either derived directly from an upstream artifact or
> marked UNKNOWN. **It never manufactures a healthy-looking state from absent
> input.**

and it keeps `INSUFFICIENT` apart from `MISSING` for the reason this needed:
*there and too thin to support a claim* is not *not there*.

Chronoa's `learning.Report.honest()` collapsed the same distinction into a bare
`False`. Measured: a model whose minority verdict has **3 examples** (under
`_MIN_CLASS`'s 20) and a model that **genuinely failed** to learn that verdict
returned the **same answer** - and the two have opposite remedies, one being *use
the assistant more* and the other *stop quoting the model*. This file already
records the principle from the other side: *"a truthful refusal demoted as a
failure"* is a defect, and `train_and_save()` declining with *"the training split
contains no example of verified"* is the correct output for that data. A refusal
that reads as a result is the same defect the other way up.

`Report.honest_reason()` returns `("reason", "sentence")` - `""` when honest,
`insufficient` when the question cannot be answered from this log, and
`not-detected` when it was answered and the answer was no. **`honest()` is
unchanged** and every callsite reads it; the reason is additive, and a test
asserts the two never disagree.

**Measuring found a dead branch in my own first version.** `best_detection()` only
returns verdicts for which `detected()` is true, and any of those makes `honest()`
true - so a `near-miss` branch placed after the early return could never run. A
four-case probe never reached it. It is recorded as unreachable rather than left
looking reachable.

`tests/test_honest_reason.py` (7), `tools/mutations/honest_reason.py` (**8 caught,
0 survivors**).

**Three of the eight were defects in my set, and one of them is the recurring
shape.** The class-count guard `len(counts) <= 2` was untested because my fixture
built exactly two verdicts, so it was always true - a wide report with one thin
verdict among three is the witness that was missing. One mutant altered no
behaviour because `return ("", "text")` is a **tuple**, which is truthy, so the
bool-disagreement test could not see it. And one mutated a `# pragma: no cover`
comment, which is not a mutant at all - deleted rather than kept.

53 passed across the honest-reason, learning-calibration, outcome-model,
measure-not-asserted and surface-learning suites.


## laya's hooks: `EventSink` refuses a field that is not a callable (2026-10-12)

`harness-study/laya-decision`'s `laya/hooks.py` opens with the rule:

> a plain callable is only accepted on the single-event `on_predict_start=`
> parameters, because `hooks=` reads for method names and a bare function has
> none.

An API that introspects must refuse the shape it cannot introspect, rather than
accept it and never call it. Chronoa learned the same lesson in
`skills/__init__.py`, where `run=lambda a: ...` passed `callable()` and then
failed on **every** call in a subprocess - and fixed it at the loader, not at the
caller.

`events.EventSink` had the same shape unchecked. Measured, `EventSink(on_text="not a
function")` built **cleanly** and the TypeError arrived at the call site - mid-turn,
after the model had run and the tool had been called. `__post_init__` now refuses
any field that is neither a callable nor None, naming the field and the type and
saying why the refusal is at construction.

Every construction site in the tree passes a real callable
(`app/conversation.py`, `events.fan_in`, the streaming tests), so this is defence
at the seam and not a behaviour change - asserted rather than assumed, by
`test_a_callable_object_builds`, because the obvious implementation of this guard
(`isinstance(value, type(lambda: None))`) refuses a bound method and a callable
object, and the GUI sink in another process is exactly that.

Three ways to write the guard wrongly, each of which lets a real defect through:
`if value:` (0 and "" are falsy, so they pass as None), one field checked out of
six (the unchecked one fails), and `return` instead of `raise` (a bad sink is built
and the turn runs with a dead stream).

`tests/test_sink_shape.py` (16), `tools/mutations/sink_shape.py` (**8 caught, 0
survivors**). 80 passed across the sink, streaming, harvest-tranche and task-eval
suites.

**Two of the eight mutations were malformed the first time** - both replaced one
line inside a parenthesised `raise` and left the continuation dangling, which is a
SyntaxError. The runner's `MutationDidNotParse` (added earlier this session)
reported it as an error rather than counting it as a survivor, which is the only
reason both were noticed before they were committed. A mutation that cannot parse
is not a mutation.

**And the third iteration of one of them was a survivor for a real reason:** my
test asserted the refusal names the *field* and the *type*, and the mutation kept
the type and dropped the *reason*. A refusal that says what is wrong and nothing
about why it is being caught here is a refusal nobody can act on - the assertion
that would have caught it says `"before the turn runs" in said`.


## laya's confidence floor, and the reward its one caller fed it (2026-10-12)

`harness-study/laya-decision`'s `laya/confidence.py` names the hazard precisely:

> reporting the entropy number under the `answer_confidence` name would be worse
> than reporting nothing, because **the name is what a caller filters on.**

Chronoa had it twice over, and the second instance is the one worth reading.

### The floor inflated every k above 2

`decision.confidence` floored at 0.5, on the reasoning that "a binary outcome
cannot be less than even" - true at k=2, false above, and this repository's own
outcome model has **three** verdicts. Measured:

| distribution | max(p) | was reported as |
|---|---|---|
| 0.33/0.33/0.34 | 0.34 | **0.5** |
| 0.40/0.30/0.30 | 0.40 | **0.5** |

A genuinely-below-even 3-way answer read as *even*, and **two different
distributions read as the same number** so a caller could not order them. The floor
is `1/k` now, which at k=2 is 0.5 and so leaves the case the old reason described
untouched.

### The caller was passing a reward

`ActionSelector.select` read `confidence({c: rewards.value[c]}) < threshold` - a
**reward-space** scalar through a **probability-space** clamp. An arm worth 0.05
and one worth 0.49 both reached the comparison as 0.5. Measured, that was **inert
at the default threshold of 0.7**: every value below 0.7 is unconfident whether or
not it was clamped first. It was **one constant away from live** - at 0.3 an arm
worth 0.05 climbs to 0.5 and reads as *confident*, so the explore path stops asking
about the worst tool on the machine. The threshold is on the value now.

### Two things the fix got wrong first, both caught by the same mutation run

- **A `1/k` floor reports a single value as certainty.** The old caller passed
  `{c: reward}` - one key - so `1/1 = 1.0` told a caller a 0.05 arm was certain,
  which is *worse* than the 0.5 it used to be told. `len(< 2)` now falls through
  to the value itself.
- **Three of my witnesses never called `select()`.** They rebuilt the `unconfident`
  list by hand with the same expression, or asserted on `rewards.value` directly,
  so all three mutations of the caller came back as survivors. A witness has to
  drive the thing it names.

`tests/test_confidence_floor.py` (9), `tools/mutations/confidence_floor.py`
(**6 caught, 0 survivors, 2 equivalent, both provably**).

**And the two equivalents are the interesting part.** Neither is a shrug; each
states something that is not obvious until it is measured:

1. **The `1/k` floor never binds on a valid distribution.** For any distribution
   over k outcomes the maximum is at least the mean, which is 1/k - so
   `max(1/k, top)` is `top`, always. The floor is pure defence against a caller
   passing something that is not a distribution, which is what the `len < 2` guard
   covers. Keeping it is a bet on future callers, not a current fix.
2. **The caller change is behaviour-preserving for a second, independent reason.**
   With the `len < 2` guard in place, `confidence({c: value})` returns the value
   unchanged, so putting the old call back is the same expression. The conflation is
   fixed twice over - by removing it from the caller *and* by making the function
   refuse to apply a probability floor to a scalar - and either half alone would
   have been enough, which is why both mutations are inert.

103 passed across the confidence-floor, learning-chain, learning-calibration,
outcome-model, surface-learning and learning-says-what-a-fit-is-worth suites.

### And the router's out-of-distribution finding, measured here and **not** a defect

Laya's `laya/router.py` documents the reason it routes at all:

> The English checkpoint does not gently degrade off English, it collapses: on
> 20-option MASSIVE intent it scores 0.100 on Hindi ... and it reports high
> confidence while doing so (ECE 0.855 on Hindi). Script detection is therefore
> the primary routing signal.

The analogue to check was `tool_select.select_tools`, whose noise floor is
`scored[0][0] / 3` - **which collapses to 0 when nothing matches, disabling the
very filter meant to reject noise.** That was the hypothesis. Measured, it is
wrong, and the measurement is worth writing down rather than the hypothesis:

| request | top score | tools sent | beyond CORE |
|---|---|---|---|
| `zzzz qqqq vwxk plfm tkjb` | **no ranked tools at all** | 3 | **0** |
| `set the volume to 40 percent` | 18.09 | 9 | 6 |
| `thanks!` | conversation path | 1 | **0** |

`ranked()` returns only positive scores, so a zero top score means **no entries**
rather than a zero-ranked list - `floor` falls to 0 and there is nothing for
`score >= floor` to admit. Pure conversation goes down `wants_a_conversation`. So
the unmatchable case already returns the smallest honest set, and the fix would
have been for a case that cannot occur.

**One observation recorded, not fixed:** for "set the volume to 40 percent" the
top scorer is `calculate` (18.09) with `set_volume` sixth. It is a scoring
weakness rather than a functional defect - `set_volume` is still in the candidate
list, and AGENTS.md's own eval measures 14/15 on what the *model* picks from that
list - so it is left alone rather than tuned against a number nobody is failing.


## laya, second repo: the temperature the reader divides by (2026-10-12)

The first clone was the wrong Laya - the notification command center, which gave
`prompt_pieces`. The one meant was **the decision model**
(`harness-study/laya-decision`, `NandhaKishorM/laya`): a 421M-parameter
non-autoregressive System-1 model on a ModernBERT-large backbone, Apache-2.0,
which returns typed decisions in one forward pass - `choice` (a distribution over
options), `score` (a distribution over an ordered rubric) and a calibrated
P(true) - in about 33 ms on CPU.

Its own `AGENTS.md` reads like this file's: *"Do NOT introduce a dependency on a
hosted service"*, *"If a change moves numbers, report the before and after"*,
*"Leave one runnable check behind for non-trivial logic."*

### The transfer: one pair of bounds, and the read path had none

`laya/calibrate.py` states the rule:

> Fitted temperatures go through `clamp_temperature` and therefore `TEMP_MIN,
> TEMP_MAX` from `laya.common`, **the same guard checkpoint load uses.** There is
> no second pair of bounds in this module.

Chronoa **already had temperature-scaling calibration** (`learning.fit_calibration`,
`cross_fit_calibration`, `calibration_metrics`; measured ECE 0.430 -> 0.107, NLL
0.938 -> 0.547). It also already bounded the *fit*: the golden-section search runs
`log(_T_RANGE[0])` to `log(_T_RANGE[1])`, so a fitted temperature is legal by
construction. What it had **none of** was a bound on the *read* path - and a
temperature out of a model file is the only way one outside the range reaches
`calibrated()`.

**Measured, feeding the reader what a file could hold:**

| T | top probability before | after |
|---|---|---|
| 0.05 (`_T_RANGE`'s floor) | 0.9975 | 0.9975 |
| **1e-06** | **1.000000** | 0.997482 |
| 20.0 (the ceiling) | 0.3378 | 0.3378 |
| `"0.5"` | **`TypeError`** | the identity |
| 0 / `True` | 0.4260, by luck of `or` | 0.4260, by rule |

Two failures, both the shape this file records: **below the floor the calibrated
probability saturated to 1.0**, so `Report.honest()`'s `detected` gate and
`recommend()` act on a certainty nothing earned - laya's own issue #394 (*"publishes
that same 0.24 as 0.4993, so a threshold applied to these numbers still selects
below model accuracy"*); and **a non-numeric temperature raised `TypeError`** out of
the read path, where every other failure in this module falls back to identity.

The precedent for the file-being-garbage is this file's own: unbalanced weights
once made `to_dict`'s `round(v, 6)` write **zeros**, and the file - which verified
its own digest - predicted 0.3333 for every verdict on every input.

### The design call the naive fix gets wrong, and the mutation that pins it

Clamping 0 to `_T_RANGE[0]` would make the **sharpest legal temperature** out of a
value that means "rounded away": the zeros-file would go from predicting 0.3333 on
everything to predicting ~0.997, which is a certainty instead of a flat model. So
**0 is the identity**, not the floor. `test_zero_and_true_still_read_as_the_identity`
is the witness and the mutation that removes the rule fails it.

`True` and `False` keep returning the identity too, now by an explicit
`_is_number` rather than by the accident of `temperature or 1.0` - laya records the
same trap in `laya/common.py` ("`True`/`False` used to float to 1.0/0.0 here and
read as fitted/sharpening temperatures").

**And what the clamp does not do:** `_SHIFT_MAX` is 8.0 in logits, and clamping to
it still saturates on a small spread. Laya's own wording for the asymmetry is
recorded here: *"The clamp bounds the damage; it does not calibrate anything."*

`tests/test_temperature_bounds.py` (13), `tools/mutations/temperature_bounds.py`
(**7 caught, 0 survivors, 1 equivalent documented**). 190 passed across the
temperature-bounds, outcome-model, learning-calibration, learning-chain,
learning-says-what-a-fit-is-worth, reply-style, prompt-pieces, calibration,
distill, surface-learning and tool-outcome suites; 35 sets / 296 anchors all match.

**Four of the eight mutations were once survivors of my own making**, and each is
a different way a control that cannot fail disguises itself: a bool mutation
aimed at the *call site* (inert - `float(True)` is 1.0 either way, so the witness
had to be on `_is_number` itself); a shift guard whose `try/except` below it
already catches the same case (kept, documented equivalent); a "second pair of
bounds" mutation that added a constant nothing read (deleted rather than counted);
and a floor witness using one example with one label, which `len(set(labels))`
already refuses, so removing the floor changed nothing it measured.


## laya: the system prompt is now tunable, and the safety sentence has no door (2026-10-12)

`harness-study/laya` cloned and studied (a local-first AI notification centre;
Python engine, 285 files - the same language as Chronoa). Its README states its
headline feature as:

> Override 20+ pipeline stages individually - change one, keep the rest as
> default. Tune persona voices, classification rules, or briefing style to your
> team. **Delete the file to revert - defaults always come back.**

Chronoa's `SYSTEM_PROMPT` was one hard-coded string, so a person who wanted
"answer in British English" had to edit the source and lose the default, or put
it in `rules.md` - one file, landing *after* everything, for a job it was not
shaped for. `prompt_pieces.py` is the lever.

**Four of Laya's decisions are the whole transfer, and each is a way a simpler
version lies:**

1. **The default lives in code, and nothing here writes the directory** - so
   "revert" is deleting a file, and an update can never be undone by a user edit.
2. **An unrecognised file name is a warning that lists the recognised ones.**
   `tools/mutations`'s first version of this test asserted `"recognised" in
   caplog.text`, which the word *unrecognised* satisfies - a warning that named
   the problem and not the answer still passed. It asserts each recognised
   filename now.
3. **An empty file is a warning and not an override.** Replacing a prompt with
   nothing is worse than not replacing it.
4. **`override_status()` answers "what am I overriding"** - the default alongside
   the current, because there is nothing to compare against without it.

**And the property that matters more than any of them:** the provenance boundary
sentence - the one telling the model what the untrusted-content fence means - is
**not in the registry at all**. There is no key for it, so no file can name it.
Laya's construction is the same shape, for identifier fields ("absent by
construction because capabilities don't list them as content"). A security
statement is not a preference; the way to keep it unoverridable is to leave no
door, not to check the door harder. The mutation that puts the lookup back is
**inert**, and that is the proof - a key no file maps to cannot be reached.

**Chronoa's version is stronger than Laya's in one measured way.** Laya's loader
runs at engine start, so its "delete to revert" needs a restart. `compose()` here
re-reads the directory, because it is called once per assistant built and not per
turn - so the scan is free and deletion reverts immediately. A test asserts the
immediate revert; the mutation that restores Laya's caching fails it.

`tests/test_prompt_pieces.py` (11), `tools/mutations/prompt_pieces.py` (**10
caught, 0 survivors, 1 equivalent documented**). 91 passed across the prompt,
reply-style, provenance, user-prompts and matrix-name suites; 30 sets / 265
anchors all match.

### Not taken, and why

Laya's deeper answer to a defect Chronoa records four times is **not** ported
here, because it is a refactor and not a fix. `engine/laya/egress/registry.py`
says:

> adding a platform means writing one file, not editing a dozen tables here.

Chronoa's `capabilities._GROUPS`, `GATED` and `GATE_NAMES` are three hand-kept
tables, and `test_skill_gates_are_enforced._IMPL` is a fourth; AGENTS.md records
that a duplicate key silently killed a `browse` label and that a skill landing
leaves four lists stale. Laya's answer - each skill owns its metadata, and the
prompt and the tables are **generated** from the registry, so there is no second
list to drift - is the right one and it is a change across every skill. Recorded
as the next pass, not half-done in this one.

**And it was measured before being recorded, which changes what the entry says.**
All four lists agree *today*:

| check | result |
|---|---|
| tools in the registry (`discover_skills()`) | 257 |
| with a `_GROUPS` row | **257 of 257** |
| with a `GATED` entry | 78 |
| consent keys with a `GATE_NAMES` row | **57 of 57** |
| `_IMPL` keys absent from `GATED` / `GATED` keys `_IMPL` lacks | 15 harmless extras / **none** |
| `test_capabilities`, `_doc`, `_skill_gates_are_enforced`, `_help_gate_labels` | **50 passed** |

So there is **no live defect to fix**, and doing the refactor here would have been
churn dressed as improvement: it touches every skill in the registry to prevent a
problem that is not present and is already guarded by three tests. The argument
for it is that *nothing structural keeps them that way* - a weaker claim, and it
belongs in the same sentence as the measurement rather than instead of it. The
trigger to actually do it is the first time one of those tests goes red, at which
point the refactor is the fix rather than the prophylaxis.


## The wizard's void got a mark, and the icon was a gear the first time (2026-10-11)

Reported as *"minor UI glitches during model/voice installation"*, then, looking at
the rendered pages: *"if void it should have some icon no?"*

**Measured against the 620px default window, four of the sixteen pages have far
less content than it has room for** - `ears` 270px, `brain` 84px, `mode` 316px,
`model-picker` 470px - so between the last row and the pinned action there was 150
to 536px of nothing. Holding the action at the bottom is right, and this file's
`setup_wizard` comment explains why at length; half a screen of nothing above it
is what reads as broken.

The void now carries Chronoa's orb. **Three things the first version got wrong,
all found by running it rather than by reading the diff:**

1. **The icon was a gear.** `Gtk.Image.new_from_icon_name("shani-chronoa")` is a
   *theme lookup*: it needs `usr/share/icons/hicolor/` on an `XDG_DATA_DIRS` that
   an installed system has and a checkout does not. The render came back as GTK's
   missing-image placeholder - **a gear** - which is what "some page had a gear
   icon" was reporting. The mark is embedded SVG bytes now, drawn through
   `Gtk.Svg` + `Gtk.Picture`, so it is identical in the app, in a render and in a
   test, with no file, no theme and no compiled `GResource` (this repo has none).
2. **The mark appeared on all sixteen pages, including the one that overflows.**
   `upper <= page_size` is the no-scrollbar case, and it is *also* what an
   unallocated scroller reports - `upper` and `page_size` are both 0 before the
   widget has been laid out, and `0 <= 0 + 1` is true. `review`, whose content
   demands 1029px of a 620px window, got a mark drawn over it. The rule is a
   named function now, `_void_has_room`, and a page that has never been measured
   is not a page with room in it.
3. **The panel's first name collided.** `gui/surfaces/background.py` shadowed
   `shani_chronoa.background` - the module that owns the inventory the surface
   draws - so `from shani_chronoa.gui.surfaces import background` inside it
   resolved to *itself*, `background.inventory` did not exist, and the whole
   layout suite came back as errors. Renamed to `gui/surfaces/processes.py`. Same
   shape as `calendar.py` shadowing the stdlib: a name is not just a label.

**And on the Brain page, a jump that read as dead text.** "Use a model on this
computer" is a wired `Gtk.Button` that goes to the model picker - styled `flat`,
which in a dark theme renders as unstyled words. So it sat underneath the Next
button looking exactly like a label nobody connected. The control was never
broken; the affordance was missing, and the chevron is what makes one - the same
device this file's welcome rows use, whose own comment says a chevron is not
decoration.

**And it was then lost, which is the part worth recording.** The chevron was
added, the render was read, `Use a model on this computer >` was on screen - and
a concurrent session committed `setup_wizard.py` from an earlier state. Nothing
failed, because nothing asserted the affordance; the next render showed the bare
label again and the report came back as *"re add setup wizard changes i think
they are not added"*. **A change verified only by looking at a picture is a
change one commit away from not existing.**
`test_a_flat_jump_button_still_says_it_goes_somewhere` and its mutation are the
fix, and both are in the set now.


`tools/mutations/wizard_mark.py` (9). **7 caught, 1 survivor that was my witness
being wrong, 1 equivalent kept and documented.**

### The mutation runner reported errors as survivors, which is the same bug twice

Two of those nine were reported as survivors for a reason that has nothing to do
with coverage:

- A mutation that produced an `IndentationError` aborted pytest with *"1 error
  during collection"* and exit 2. `_failures` looked only for the words
  *" passed"* or *" failed"*, neither of which is in that sentence - so it printed
  `(no summary)` and counted the mutation as a survivor.
- A mutation that broke a module's import gave `27 passed, 101 errors` and no
  FAILED lines at all, which is again not a survivor: it is a run that could not
  measure anything.

`_failures` collects `ERROR` lines as well as `FAILED` now, and raises
`MutationDidNotParse` for a run with no summary. Both are OmO's #8850 in the
other direction - **a check that could not run is not a check that found
nothing** - which is the same statement `verification.py`'s exit-code protocol
made earlier the same day.

The survivor was mine: I had named `test_no_wizard_page_needs_a_sideways_scrollbar`
as the witness, which only ever builds the *wizard* and so passes. What breaks is
every test that walks the surfaces.


## The force stop, the reaper, and a model nobody could start (2026-10-11)

Reported in a person's words: *"it needs a force stop button - manually blocking
those processes for beginners could be a headache, otherwise they face heavy
resource usage"*, and *"when shifting to that Kilo APIs, needs button to run the
model etc"*. Both were **measured gaps, not feature requests**, and the second
one is the same defect the first is.

### Six background watchers were started and could not be stopped

`app/application.py` starts the incoming-call watcher, the message watcher, the
file-sharing receiver, the watch companion, the phone-keyboard service and the
Bluetooth music remote **unconditionally**, at startup. Each is written with a
`stop()`. Two scans:

| scan | result |
|---|---|
| the tree, for calls to those `stop()`s | **none** |
| `gui/` and `app/tray.py`, for the six by name | **no hit** |

So a machine could hold a Bluetooth link, a watch connection or a microphone open
for as long as the app ran, with quitting as the only lever - on a machine
somebody is gaming or streaming on, that is not a lever. This is the dead-code
class this file records four times, in the shape that costs.

`background.py` is the inventory, read **off the live application object** rather
than a hand-kept list, so adding a component cannot silently drop it. The surface
is "What's running", with a Stop per row and a **Stop everything** that asks
first and names what it takes. Two rules it is held to: a row with a dead Stop
button says *why there is no stop* instead of rendering one, and a row only reads
"stopped" when the component itself confirmed - `presence.py`'s Settings control
already does that, and this does the same.

### The child supervisor could see a hung child and had nothing to do about it

`check_heartbeats` reports `DISCONNECTED` - "alive but silent past the budget:
hung" - and its own docstring says *"waiting longer for a hung one is
pointless"*. **Every caller in the tree only reads that status.** A detector with
no reaper is the control-nothing-reaches class again.

Measured on this box, the cost of the whole shape: a `llama-server` up
**7h36m holding 1.09 GB**, and two `whisper-cli` runs up **44 and 2 minutes on
3-4 second WAVs holding 4.7 GB each**. Killing four PIDs took available memory
from **9 GB to 18 GB**. Those two were *not* Chronoa's children - they were this
agent's own measurement runs - and no supervisor could have reached them, because
a process may only signal its own descendants. But a hung child of Chronoa's own
was already visible to the supervisor and nothing acted on it.

`reap_hung()` closes that loop: terminate, a bounded grace, then SIGKILL. Guarded
two ways, and **both guards are load-bearing** - it re-probes immediately before
the signal (a pid that already belongs to a stranger must not be signalled), and
only a *tracked* child with a pid is ever signalled (a stand-in must not take
down whatever happens to be at that number).

**The re-probe guard is asserted on the log, not on a kill.** Its removal is
otherwise unobservable: `os.kill` on a dead pid raises OSError, which the reaper
already caught, so `reap_hung()` returns the same empty list either way. The
guard's value is that it declines *before* trying, and the only place that shows
is the sentence it logs.

### The model could be released but not started

`presence.py` and `settings_window` had a working cycle - Ready, "Letting go of
the model", Asleep - which calls `local_llm.start_service/stop_service`. What it
could not do is **start** one: the button said "Free the model" and "Asleep" and
nothing else, and the state that matters here ("the model is off because the
cloud fallback answered instead") is not shown on the row. A person who wanted
their local model back had to press a button labelled with the opposite of what
they wanted, three times.

**"Run the model" is added, and the cycle was not removed.** `Asleep` means "stop
it *and* do not start it again to answer the next question", which no pair of
Run/Stop buttons can express; dropping it to fit two buttons would have removed a
capability to fix a different one. It runs off the main loop like the cycle, and
**re-reads** the state afterwards - "Started" and "is answering" are different
claims and only the second is worth showing.

`tests/test_what_is_running.py` (19) and `tests/test_settings_text_fields.py`
(3 more, 7 in the file). **Seven of the first twelve mutations in the set were
defects in the set, not in the code**, and four of them were mutations aimed at
nothing: replacing a whole guard with a bare `continue` made the reaper a no-op,
which the real-kill test already caught, so they said nothing about the pid or
the state filter they claimed to test. Two more were *equivalent* - the failed
stop's `self.running = True` is redundant on the first call, and `stop is None`
rows already land in `refused`, so both witnesses had to be built before the
mutation could break anything. That is the recorded "a control that cannot fail"
trap, hit five times in one file.

### And the mutation runner reported a broken mutation as a **survivor**

Three of those seven were mutations that produced code which **could not parse** -
one had its replacement indented one level too far, giving `IndentationError:
unexpected indent`. pytest aborted with *"1 error during collection"* and exit 2,
and `_failures`' parser looked only for the words *" passed"* or *" failed"* -
**neither of which appears in that sentence.** So the run reported
`(no summary)` and counted the mutation as a survivor: it read as *"the tests did
not catch this"*, when the truth is *"nothing was measured at all"*.

That is OmO's #8850 from the other side, and it is the same error the exit-code
protocol fixes in `verification.py` - **a check that could not run is not a check
that found nothing.** `_failures` now raises `MutationDidNotParse` for a run with
no summary, so it is reported as an error rather than as a survivor, and "error
during collection" is named separately from "pytest produced no summary".

The fourth was worse in a way only running it showed: the re-probe guard's witness
passed `pid=os.getpid()`, on the reasoning that the guard declines before any
signal - which is exactly what it does *while the guard is present*. Remove the
guard and the reaper SIGTERMs the pytest process, so the run dies with no output
and the mutation is unmeasurable. **A fixture that kills the runner it is being
measured by cannot measure anything.** It tracks a throwaway `sleep` now, and
asserts it is still alive afterwards, so the guard's absence is observable from
outside: alive means it worked, dead means it did not.


before editing, and follow the verification steps before calling any change
done.

## Start here (fast path)

**Always read these first:**
- `What this repo is`
- `Empirical verification (mandatory)` and
  `Rule: verify by actually running it, not by reading it`
- `Required verification for a change`
- `Boundaries`
- `Commit discipline`
- `Cross-repo impact`
- `Package layout and renames` - older sections use pre-2026-10-02 file names
- `ARCHITECTURE-TARGET.md` - the architecture Chronoa is aiming for, every box
  mapped to today's code, and the ordered gap list. Update its row when you
  fill a box; read its Part 4 before building anything it marks 🚫
- `Third-party studies` - the `../harness-study/HARVEST-*.md` index below.
  **A harvest is a dated claim about this tree**, not a finding about the repo
  that was cloned: the named symbol may have moved, and one recorded claim
  (`HARVEST-2/E-memory-mcp-special.md`'s "threshold gate") was already found
  false. Check a claim against the code before acting on it.

### Third-party studies - `../harness-study/HARVEST-*.md`

Findings from reading outside harnesses, and **what this repository did about
each**. Four of these were unlinked from this index when it was added
(2026-10-12), including a 286-line deep pass, so nothing here depended on having
read them. `harness-study/` is **not a git repository**, so a harvest nothing
points at exists on one disk until that disk is reused.

| file | subject | what came of it |
|---|---|---|
| `HARVEST-OIU.md` | open-intelligent-ui | five transfers, 7 passes: goal edits, `safeUrl` links, the bounded cache, `clip` |
| `HARVEST-HERETIC.md` | heretic | one transfer: a measurement reported beside its baseline |
| `HARVEST-MIMIC.md` | mimic | none taken; **already refused** by `ARCHITECTURE-TARGET.md` |
| `HARVEST-ECC.md` | everything-claude-code | skill coverage, the durable history read, the sense budget |
| `HARVEST-LAYA.md` | laya-decision | `EventSink` shape, the confidence floor, `prompt_pieces`, temperature bounds |
| `HARVEST-AIRLLM.md` | airllm | residency vs runnability; a 297x sparse-vector win |
| `HARVEST-EDGE0.md` | edge0 | KV width cross-check; bytes-per-token |
| `HARVEST-EDGE0-DEEP.md` | edge0, deep pass | a hygiene suite that imports nothing; presets as measurements |
| `HARVEST-PERSONAPLEX.md` | PersonaPlex | the audio-codecode quantizer; the loudness target, measured then built |
| `HARVEST-DTA.md` | digital-travel-agent | the rules-file refusal; the approval-time replay guard |
| `HARVEST-BWRAP.md` | bubblewrap | the three sandbox protections that belong to the machine |
| `HARVEST-MAZE.md` | Maze-AI | nothing; a short negative result |

**Read when your change touches them:**
- `Rendering the UI to a PNG` — GTK4/Adw init ordering and the capture path
- `Machine-state senses` / `Senses layer` — the sense contract
- `Settings window`, `Model choice`, `Packaging` — the two naming
  conventions must not be mixed

**Current known issues — read this before you start:**
- `Audit-verified known issues` and `Garuda Cross-Reference Findings` —
  dated retrospectives. **Grep for the subsystem you are changing.** Full
  methodology in `AUDIT-HISTORY.md`.

  This section mixes fixed history with issues that are **still open**,
  including Critical security ones. Grep it for `not fixed`,
  `still open`, and your subsystem name before you touch anything.

**Background reference — skippable, pure survey material:**
- `Deliberately not done` — read before proposing a feature, so you don't
  rebuild something intentionally excluded.

**Never skip:** running it. Reading the diff has never been sufficient here.

## What this repo is

A local-first GTK4 voice/text AI assistant for Shanios: `whisper.cpp` for
STT, Ollama for LLM inference and tool-calling, Piper for TTS, real mic
capture and audio playback via `pw-record`/`pw-play` (falling back to
`arecord`/`aplay`), plus optional hands-free wake-phrase activation
(`wakeword.py`: vad.py segments utterances, whisper.cpp transcribes each one
biased toward the phrase, matched at the start; openWakeWord was dropped
2026-10-01 - neither it nor onnxruntime/tflite is in Arch's official repos). Listening auto-stops on silence
(`AudioRecorder.start_auto_stop`, `vad.py`) rather than requiring a second
button press - pressing the orb again while listening just ends the turn
early (`cancel_auto_stop`). Barge-in has two layers: talking again always
interrupts TTS mid-reply (`AudioPlayer.stop()`, unconditional), plus an
opt-in continuous-VAD layer (`BargeInMonitor`, gated behind
`barge-in-vad-enabled`, off by default - no acoustic echo cancellation, so
it can self-interrupt on speaker output without headphones).
`tools.py` + `assistant.py` are what make it an actual task-doing assistant
rather than a chatbot: a fixed whitelist of local system actions (open an
app, volume, mute, datetime, timers, battery status, web search) wired
through Ollama's `/api/chat` `tools` parameter — deliberately not a generic
shell-exec tool. When Ollama isn't available, `cloud_llm.py` provides an explicit, opt-in
fallback to free, no-API-key cloud LLM gateways (LLM7, Kilo Gateway,
BlockRun) - gated behind BOTH privacy mode being off AND a separate
`cloud-fallback-enabled` gsetting, so it never activates from a single
switch (see `app.py`'s `_maybe_enable_cloud_fallback`). Each free provider
also supports an optional user-supplied API key (BYOK, `*-api-key`
gsettings) to raise that provider's anonymous rate limits - Kilo's own
429 error during live testing explicitly suggested this. Four more
providers - Anthropic, OpenAI, Google Gemini, Groq - are BYOK-*required*
(confirmed live: all four reject an unauthenticated request outright) and
are tried ahead of the free chain when a key is configured; Anthropic uses
its own native-protocol adapter (`AnthropicLLM`) since it isn't
OpenAI-compatible, the other three reuse `OpenAICompatibleLLM`. Each action is a "skill" module under `shani_chronoa/skills/`
(see that package's docstring for the plugin contract); a user can add their
own by dropping a same-shaped module into `~/.config/shani-chronoa/skills/`
without touching this codebase. Those same skills are also exposed as a real
MCP (Model Context Protocol) server (`mcp.py`, run standalone via the
`shani-chronoa-mcp` binary) so any MCP-speaking tool - Claude Desktop, Claude
Code, Cursor - can call them too; this replaced an earlier `mcp.py` that
only looked like MCP support (see the bug list below). Packaged via both an
Arch `PKGBUILD` and a `DEBIAN/control` (two different package managers, keep
dependency names in the right convention for each — see below).

## Package layout and renames (2026-10-02) - read before grepping

The package was restructured for long-term maintenance on 2026-10-02. **Older
sections of this file keep the names that existed when they were written**;
use this map to translate them. Moving code changed no behaviour; the bugs
that linting turned up were fixed separately and are listed below.

| Older sections say | Now |
|---|---|
| `app.py` | `app/` - `application.py` (`ChronoaApplication`), with `voice`, `brain`, `conversation` and `desktop_integration` mixins, plus `common` |
| `gui.py` | `gui/` - `window.py` (`ChronoaWindow`), `style`, `asking`, `attaching`, `conversations_menu`, `widgets`, `questions` |
| `settings_window.py` | `settings_window/` - `window`, `senses`, `privacy`, `voice`, `activity` |
| `triggers.py` | `triggers/` - `common`, `sources`, `desktop_sources`, `rules`, `event_rules`, `events` |
| `office.py` | `office/` - `common`, `read`, `write`, `edit` |
| `sandbox/executor.py` (the whole thing) | `executor.py` keeps `SandboxExecutor` and the bwrap/Landlock probes; `child.py` does the in-child hardening (`_harden_child`, `_die_with_parent`, core dumps); `commands.py` works out which program an argv really runs (`_blocked_binary`); `limits.py` holds the profile ceilings and the seccomp request (`ProfileLimitError`) |
| `llm.py` | `ollama_llm.py`. The default local brain is now llama.cpp (`local_llm.py`, user unit `shani-chronoa-llm.service`); Ollama stays as an option |
| `models.py` | `model_choice.py` |
| `sessions.py` | `conversation_store.py` (many conversations, plus FTS5 search) |
| `calendar.py` | `eds_calendar.py` (it shadowed the stdlib module) |
| `desktop.py` | `desktop_session.py` |
| `secrets_manager.py`, `vault.json` | **removed** - the vault was XOR plus base64, not encryption. Keys live in GSettings; `redaction.py` (`Redactor`) is what keeps them out of logs, tool output and memory |
| `gateway_supervisor.py` | **removed** (it was dead code). `child_supervisor.py` is the part that was live |

Rules the split follows, so the next change keeps them:

- **A package's `__init__` re-exports its old public names**, so
  `from shani_chronoa.triggers import RuleStore` still works. Code inside the
  package imports from the submodule that defines a name, never from
  `__init__`, which keeps the import graph acyclic.
- **Patch a name where it is read, not where it is re-exported.** After a
  move, `monkeypatch.setattr(executor_mod, "_PR_SET_PDEATHSIG", -1)` sets a
  copy nobody reads, and the test still passes. Two tests did exactly that
  and were repointed to `sandbox.child`. Test the patch: make it break the
  code and confirm the test fails.
- **Data paths are resolved per call, never at import.** `triggers_dir()`,
  `rules_file()` and `screenshot.output_dir()` are functions because, as
  import-time constants, they pointed the test suite at the real
  `~/.local/share/shani-chronoa` (178 fake screenshots and a `triggers/` tree
  ended up there). `tests/conftest.py`'s
  `_trigger_and_capture_paths_are_inside_the_test` guard fails any test that
  writes there.
- **Tests that read source as text** use `tests/_source.py`'s
  `package_source("shani_chronoa.app")`, which concatenates a package's
  modules, so a split does not break them.
- **Lint with pyflakes.** It found a real crash (`DEFAULT_COOLDOWN_SECONDS`
  never imported in `skills/manage_triggers.py`) and a synonym table in
  `tool_select.py` whose duplicated keys silently dropped `edit_image` for
  "fix up my photo". The only expected warnings are the re-exports in
  package `__init__.py` files.

## What counts as done

The governing principle, and the test every change is judged against:

> **कर्मण्येवाधिकारस्ते मा फलेषु कदाचन** — we have the right to our
> *actions*, never to the *fruits* of them. (Gita 2.47, and its chiasm in
> Shanti Parva, Udyoga 153.10-11: *"You have a right to action, but the right
> to its fruits is not yours."*)

In engineering terms: **the work is the deliverable; the metric is an
instrument, never the goal.**

What this rules out, concretely:

- **A metric as the objective.** Accuracy, `beats_chance`, a coverage count.
  These are read, never chased. A change that moves a number without making
  anything more true is not an improvement.
- **A truthful refusal demoted as a failure.** `train_and_save()` declining with
  *"the training split contains no example of verified"* is the correct output
  for that data. Making it return something confident instead would be the
  actual defect.
- **A learned component that authorises anything.** It advises. Authority stays
  with the consent keys - the same division as right-to-action, not
  right-to-outcome.
- **Discarding knowledge for being not-yet-useful.** Retire it with the reason
  kept, and keep learning even when the result is not yet actionable.
- **Machinery whose only purpose is to report success.** If a number cannot
  honestly rise yet, the honest refusal beats the flattering figure.

What survives being wrong: **the log, the post-conditions, and the machine's own
evidence.** No model outlives its own justification, and none is allowed to
have one that isn't measured.

## Empirical verification (mandatory)

**Reading code is analysis; running code is verification.** A change is not
verified by reading the diff, running `bash -n`, or confirming it "looks
correct." It is verified by observing the actual behavior of the real
thing in the real environment — built, served, deployed, signed, running.
If you haven't seen it work (or fail) for real, it isn't verified.

## Test harness: shani-testbed (use it - and improve it, never invent around it)

The ecosystem's real test harness is the sibling repo **`../shani-testbed`**
(read its `README.md` and `AGENTS.md`). It installs a real ShaniOS image with
the real installer, boots its slots (`systemd-nspawn`, and UEFI + TPM VMs),
runs real deploys and rollbacks, drives GUI apps through their accessibility
tree, and checks web pages in a real headless browser. Every command runs from
`../shani-install-media`, which provides the builder container:

```bash
cd ../shani-install-media
./run_in_container.sh build.sh test <command> ...   # `... test help` lists them all
```

**If the check you need does not exist, add it to shani-testbed - do not invent
around it.** A one-off script in this repo, a scratchpad, or a heredoc piped
into a container is lost when the session ends, and the next agent re-derives
it. Extend the harness instead (see "Extend the harness" in its AGENTS.md):

- an in-slot check -> `shani-testbed/slot-tests/<name>.sh` (`# slot-test-mode: boot`,
  prints `RESULT <name> PASS|FAIL|SKIP` lines), run by `slot-test <slot> <name>`;
- a GUI interaction or assertion -> an `app` action in `lib/app.sh`, or a walk
  through a real app as `app-scripts/<app>.actions`;
- a web check -> `lib/web_client.py`;
- a new way to boot, drive or observe -> a command or option in `lib/`;

each with a negative control (a check that cannot fail is not a check), its
self-test (`tests/run-app-actions.sh`, `tests/run-web-client.sh`, ...), and the
`usage` + README updated. One harness run at a time: disk-touching commands
take `disk/.testbed.lock` and a second run is refused. Plain nspawn boots see
the image's whole `/var`; real boots have an empty tmpfs `/var`
(`systemd.volatile=state`) - use `slot-test --volatile`, or a real UEFI boot
with `iso-install --boot-only --console-exec=CMD`, for anything touching `/var`.

### What to run for this repo

- `slot-test <slot> chronoa-senses chronoa-machine-state --local-src-chronoa=/opt/shani-chronoa`
  checks the senses against the real Arch image (run_in_container.sh mounts
  this checkout at `/opt/shani-chronoa`).
- `slot-test <slot> repo-pytest` runs this repo's suite on the image's
  Python/GTK; Landlock and seccomp tests need the real kernel:
  `iso-install --boot-only --console-exec=...`.
- `app <slot> --run=shani-chronoa --strict ...` drives the real window.

## Rule: verify by actually running it, not by reading it

**A command that is not on this box is a slot run, not a workaround and never
a guess.** This dev machine is Ubuntu (see `test_package_names_match_arch.py`),
so an image binary like `arping` or `smartctl` may be absent or behave
differently than on ShaniOS. When a CLI command is the thing being wired, the
honest order is: run it on the image first (`cd ../shani-install-media;
./run_in_container.sh build.sh test ...`, or `slot-test <slot> <name>` after
`bootstrap`), and only then write the parser against its real output.

Measured cost of skipping that step (2026-10-10): `check_internet` needed a
duplicate-address check. `arping -D` is the usual probe, `iputils` ships it on
both images, and it was *not installed here* - so the code was written against
the kernel's ARP cache instead (`ip neigh show`), which needs no extra binary,
sends nothing, and was verified for real on this box. The swap was defensible
on its own merits, but the reason it was chosen was that the tool was missing,
not that the tool was wrong - and its exit-status contract went unmeasured. If
a slot had been used, `arping`'s real status codes would have settled it and
the choice could have been made on the merits instead of the environment.

Two rules that follow, both already stated above in other words:

- **A contract you cannot measure is a contract you do not have.** Exit codes,
  empty-output-on-success, and permission-denied-on-rc-0 are all documented
  for real binaries elsewhere in this file; do not reason one out from a man
  page summary or from another distribution's version.
- **Prefer a mechanism whose behaviour is verifiable here** when the two are
  otherwise equal - but record *why* the preference happened, so a later
  reader can tell a measured decision from an environment's accident.

This repo has shipped multiple bugs that read as completely correct and
only broke when actually executed — this is not hypothetical caution, it's
what happened on the first two passes here (2026-09-16):
- A GTK4-removed API (`Gtk.Button.set_relief`/`Gtk.Relic`, GTK3-only
  concepts) that crashed the app on first window creation.
- `async def` handlers (`_process_input`, `_start_listening`) invoked via
  `GLib.idle_add`/plain calls with no asyncio loop anywhere — the
  coroutines were created and silently garbage-collected, so the entire
  chat pipeline was dead code. `bash -n`/`python -m py_compile` catches
  none of this; only actually running it and seeing the LLM never
  respond does.
- A bad type *annotation* on a function parameter
  (`Gio.CommandLine` instead of `Gio.ApplicationCommandLine`) that crashed
  the whole module on import — but the equivalent-looking
  `self.x: Optional[Foo] = None` attribute annotation does NOT get
  evaluated at runtime and is harmless. These look identical on the page
  and behave completely differently; verify, don't assume from the shape
  of the code.
- `super().do_startup()`'s vfunc chain-up throws a `TypeError` on at least
  one real PyGObject build (3.48.2) and caused an actual segfault
  (GLib-GIO-CRITICAL "failed to chain up on ::startup", then SIGSEGV).
  Fixed with the explicit class-qualified call, `Gtk.Application.do_startup(self)`.
- `org.shani.chronoa.gschema.xml` used an invalid `schema="..."` attribute
  on `<key>` and was missing required `type="s"/"b"` attributes —
  `glib-compile-schemas` silently discarded the *entire file* (exit 0, no
  visible error), so every setting silently fell back to hardcoded
  Python defaults. Always run `glib-compile-schemas` on any schema
  change and check it actually produced `gschemas.compiled` — a clean
  exit code alone proves nothing here.
- The orb button (`ChronoaOrbWidget`, the mic icon — Chronoa's primary
  voice-input control) was a `Gtk.Button` never connected to anything:
  no `set_action_name`, no `clicked` handler. Clicking it did nothing;
  voice input was only reachable by calling the `app.toggle-listening`
  GAction directly (e.g. over D-Bus), which nothing in the GUI did. Reading
  `gui.py` alongside `app.py`'s action definitions looked complete — the
  missing wire only showed up by constructing the real window and checking
  `orb.get_action_name()` / firing a real `clicked` signal.
- `wakeword.py`'s first draft called `openwakeword.model.Model(wakeword_models=["hey_jarvis"])`
  and read scores back with `scores.get("hey_jarvis", 0.0)`. Both looked
  reasonable and neither raised on inspection. Actually loading the
  installed package: `Model.__init__` takes `wakeword_model_paths` (file
  paths, not bare names — `TypeError` otherwise), and a loaded model's score
  key is the resolved filename stem (`"hey_jarvis_v0.1"`), not the short
  name — so `scores.get("hey_jarvis", 0.0)` would have silently returned
  `0.0` forever, never once firing. Since exactly one model is ever loaded,
  the fix reads `max(scores.values())` instead of indexing by a guessed key.
  Also: it assumed `pw-record ... -` writes a 44-byte WAV header before raw
  PCM (matching the file-recording path in `audio.py`) and skipped it — a
  real capture + hex dump of the first 200 bytes on this machine's mic
  showed no RIFF header at all when streaming to stdout; the skip was
  silently discarding the first 80ms of every wake-word check. Both bugs
  were confirmed **and fixed** by actually loading the real
  `openwakeword` package and capturing real mic audio, not by reasoning
  about the API from its usage elsewhere.
- Both `usr/bin/shani-chronoa` and (when it was added) `usr/bin/shani-chronoa-mcp`
  computed the package path as `dirname(dirname(__file__))` — for a script
  at `usr/bin/foo` that's `usr/`, but the actual package lives at
  `usr/lib/shani-chronoa/shani_chronoa/`. Every previous verification pass
  in this file ran code via `sys.path.insert(0, '.')` from inside
  `usr/lib/shani-chronoa` directly and never actually invoked the installed
  launcher script, so this shipped for the entire life of the project
  without anyone noticing: running `usr/bin/shani-chronoa` directly has
  always raised `ModuleNotFoundError: No module named 'shani_chronoa'`.
  Found only by actually running the launcher script itself, not the
  package it imports. Fixed by joining `lib/shani-chronoa` onto that
  directory in both scripts.
- `mcp.py`'s real implementation needed the `mcp` package's actual
  `add_tool()` behavior, not an assumption: it infers a tool's JSON schema
  by inspecting the Python function's signature, with **no path to hand it
  a pre-built JSON schema directly**. The obvious-looking shortcut - a
  generic `def _wrapper(**kwargs)` - produces a broken schema (one literal
  field named `"kwargs"` of type string) and fails validation on every real
  call. Setting `__signature__` to a real `inspect.Signature` built from the
  skill's existing schema is what actually works; confirmed end-to-end
  (zero-arg, required, optional-with-default, int/string/bool params) by
  driving the real installed `shani-chronoa-mcp` binary as a subprocess
  over genuine stdio JSON-RPC with the real `mcp` client SDK, not just by
  calling functions in-process.
- `vad.py`'s first draft used a hardcoded RMS silence threshold (400). A
  real 2-second capture from this dev machine's actual mic - nobody
  speaking, just ambient/fan noise - measured a peak of ~2046 and a mean of
  ~592, five times the hardcoded value; a fixed threshold would have
  registered continuous "speech" and never auto-stopped on any real
  machine with normal ambient noise. Fixed by calibrating against a short
  real sample of ambient audio at the start of each session
  (`calibrate_noise_floor()`) instead of a literal. Separately, this dev
  sandbox's mic does not pick up its own speaker output at all (played a
  generated tone through `pw-play` while recording; RMS stayed at ambient
  levels the whole time) - almost certainly a virtual/cloud audio backend
  with no real acoustic path between output and input. That means
  `BargeInMonitor`'s actual real-world false-positive rate from speaker
  bleed (the reason it's off by default) could NOT be verified acoustically
  here - only the detection logic itself was verified, via synthetic PCM
  fed directly into the same code path. Verify the acoustic behavior for
  real on a machine with physical speakers/mic before enabling it by
  default anywhere.
- `app.py`'s first draft of `_llm_status_text()` checked `self.llm is
  None` to decide whether to show "LLM unavailable" - but `self.llm` is
  *never* actually `None` when Ollama is unreachable, it's still a live
  `OllamaLLM` instance that just fails `.is_available()` (the same
  distinction `_submit()`'s existing guard already made correctly). Running
  it for real against this Ollama-less dev machine caught it immediately:
  the status bar claimed "Ready (Ollama: qwen3:4b)" while Ollama was
  completely unreachable. Fixed to check `.is_available()` explicitly.

None of these were caught by type-checking, linting, or reading the diff
carefully — only by constructing the actual GTK objects / running
`glib-compile-schemas` / driving the real GLib main loop and watching what
happens.

## Rendering the UI to a PNG, and what it cost to work out (2026-09-28)

The UI defects in this app's history - `hwmon` and `thermalgrid` shown to users
as module names, reply markdown displayed as `**38G**`, near-white text on a
light theme at a measured 2.80:1 - were all invisible to a suite that
constructed the widgets and walked the tree. Constructing a widget is not the
same as it being legible. `Gsk.CairoRenderer` renders a widget tree offscreen
with no compositor and no window manager, which is the only way to *look*.

**Three requirements, all of them silent on failure. Each returned a plausible
wrong answer rather than an error, which is what makes them worth writing down:**

- **`Adw.init()` must come before any Adw widget is constructed, and before the
  `Gtk.Application` exists.** Initialise it at module scope. The settings window
  is entirely Adw widgets and renders *nothing* without it - no node, no
  exception, just an empty PNG. Calling it inside `activate`, after the window
  was built, does not help.
- **`present()` must happen a main-loop turn before the snapshot.** Inside
  `GApplication::activate` the tree is unallocated (`0x0`, `get_mapped()` False),
  and pumping the context does not help: with nothing pending,
  `iteration(False)` returns immediately. Worse, calling `present()` *again*
  from inside the capture function re-maps the window and the snapshot catches
  a tree with no node.
- **`Gdk.Texture.save_to_png_bytes()` fails on the texture a
  `Gsk.CairoRenderer` produces; `save_to_png(path)` works on the same texture.**
  The bytes form returns nothing with no error, which is indistinguishable from
  a window that rendered blank.

Also: `Gsk.CairoRenderer.new_for_surface` is inherited from `Gsk.Renderer` and
refuses to construct the subclass (`TypeError`); use `new()` then `realize(None)`,
because `render_texture` asserts on being realized. And an `Adw.Application`
id is a D-Bus well-formed name - one containing `Adwaita` is invalid, registers
badly enough that the window never maps, and produces the same empty PNG.

**Measure the pixels; do not trust a description of them.** A written
description of the pre-fix screenshot called the header "black text, clearly
readable" when it measured 2.80:1 and the glyphs were the page's own lightness.
Current measurements: light theme 11.61:1, HighContrast 13.88:1 main window and
17.58:1 settings. A skip is silent, so a display probe that is wrong is worse
than no probe - `Gdk.Display.get_default()` returns None until GTK is
initialised, and the first version of that probe skipped every test on a
machine with a live X session.

`render_ui.py` in the repo root is that renderer, working and committed - it
prints a PNG for `main`, `empty`, `settings` and `help`, and takes an optional
theme argument. It must be run as a script, not imported into a test.

**`render_ui.py panel:<name>` returns `NO NODE` on this box for *every* panel,
measured 2026-10-11 on unmodified source - including `conversations` and
`memory`, which have shipped for months.** So a panel that does not render here
is not a broken panel. Two causes, both found by bisection and both silent:

- **`Gtk.WidgetPaintable` over an `ApplicationWindow` whose child is a
  `NavigationView` returns a node of `None`.** The window is mapped and fully
  allocated (measured `1000x724`) throughout, and the *same* paintable over
  that same `NavigationView` returns a real node. So it is a wrong argument,
  not a rendering fault - and `NO NODE` points at the display in both
  directions.
- **An editable widget has no surface to draw into when snapshotted detached.**
  Measured side by side in one mapped window: `Gtk.SearchEntry`,
  `Adw.EntryRow` and `Adw.StatusPage` all return `None`, while a plain
  `Gtk.Entry` and a `Gtk.Label` both return real nodes. So `my_files` - which
  uses `Adw.EntryRow` and `Gtk.SearchEntry` because those are the right
  controls - cannot be photographed this way at any level, and `meetings` can.

The recipe that does work: build the page inside an `Adw.NavigationView`
(`add()`, **not** `add_content` - libadwaita 1.9 has no such method), present the
window, and shoot the **deepest level that yields a node** - which is the
`NavigationView` for some panels and the page's own content box for others, so a
renderer has to walk down rather than assume one. GTK's pump is
`MainContext.iteration(False)` on a main context, **not** `Gtk.events_pending`
(a GTK 3 name that does not exist) and **not**
`Gdk.Display.get_default_context` (not a method either). Two further measured
traps: the broadway daemon must be started with `setsid` or it dies with its
parent shell, `gtk4-broadwayd :N` listens on `broadway(N+1).socket`, and
`DISPLAY` must be unset or a live X session wins over `GDK_BACKEND=broadway` -
that last one produced `GDK_IS_DISPLAY (display) failed` on every Adw call while
the process ran to completion.

**Render, then read the pixels - both of these still bit after the two new
panels were behaviourally verified.** The shelf's ordering key was the file's
`mtime`, which is when the file was last *written*, so a `cp` reorders it and
`tar` preserves every mtime in a backup to the second - a restored shelf is
arbitrary while it still says "newest first". The capture time is in the file
name by construction (`meeting-%Y%m%d-%H%M%S`), so that is the key. And a red
`destructive-action` **Stop** was the loudest thing on a page holding two saved
recordings, on a control that ends nothing destructively.


**A visual regression suite was written, could not be made to fail, and was
deleted rather than shipped.** The measurements, so nobody re-derives them:

- A pytest wrapper returned empty PNGs when built in-process; shelling out to
  `render_ui.py` fixed that, and 15 tests passed.
- Restoring the pre-theming `gui.py` (hardcoded `#14141f` background) left all
  15 **passing**, and the header measured 9.89:1 - not the 2.80:1 that the
  original investigation recorded. The regression could not be reproduced.
- The "not a blank page" assertion (`> 50 distinct colours`) was aimed at the
  `Adw.init()` failure above. Rendering the settings window with `Adw.init()`
  omitted still produced **345** distinct colours against **350** for the
  working build - the two are indistinguishable by that measure.

So every assertion in it passed regardless of the state it was meant to
catch. A test that cannot fail is the same failure as a test that always
passes, and is worse than no test because it reads as coverage. The renderer
is the durable artefact; the assertions were not salvageable.

## Commit discipline

Before composing a commit message, run `git log --oneline -20` (and `git
log -5 -- <touched paths>` for the files you changed) and match the
existing style — subject shape, scope prefixes, body detail level —
rather than writing in a generic format.

## Boundaries

- ✅ **Always**: grep for real callers before trusting a `feat:` commit
  message or a passing module-level unit test as proof something is live
  — this repo has shipped multiple modules that were fully built,
  unit-tested, and never actually wired into anything that runs (see
  "Audit-verified known issues" below).
- ⚠️ **Ask first**: adding an MCP *client* (consuming external servers) —
  deliberately not done; it conflicts with the fixed-whitelist skill
  design's whole safety rationale and needs its own trust story first, not
  a quick addition.
- 🚫 **Never**: turn `tools.py`'s skill whitelist into a generic
  shell-exec tool, even as a convenience shortcut — that's the specific
  design boundary that makes this an "actual task-doing assistant" safe to
  run LLM-issued commands through, not an accident to "simplify" away.
- 🚫 **Never**: delete or skip a failing test to make a build/CI pass — fix the underlying code, not the test. A red test is signal; silencing it destroys the signal, not the bug.
- 🚫 **Never** run `git checkout <dir>`, `git checkout .`, `git stash`, `git stash pop`, `git reset --hard` or `git clean` **anywhere in this repository**. Another session is committing here continuously — it landed `polkit_policy`, `thread_activity`, `graphics_stack` and `autostart_entries` in a single afternoon — and every one of those commands reverts *their* uncommitted work along with yours. Measured cost (2026-10-11): `git checkout -- usr/lib/shani-chronoa/shani_chronoa/` was run to test "is this failure pre-existing", and it reverted the whole package, including `pipewire.py`'s uncommitted `list_streams` and `skills/recording.py`'s `streams` action. Both had to be re-created. **A path operation in a shared tree is a destructive operation.**
  - To ask "is this failure mine or pre-existing": copy **only your own files** to a temp directory, restore **only those** afterwards (`cp`, never `git`), and restore them from your own copy. Never scope the restore wider than the files you changed.
  - To measure at HEAD: `git show HEAD:<path> > /tmp/x.py` and swap that **one file**, then swap your copy back. A test run that needs a wider revert than one file is a test run whose conclusion does not need those files.
  - Back up your own files outside the tree *before* any swap, and keep the backup until the work is committed — this file already records a record lost to a rollback that way, and this is the destructive version.


*Maintenance note: this file is well past 300 lines. Keep new entries
terse and current-state; consider a dated `AUDIT-HISTORY.md` split (the
pattern already used in shani-docs/shani-install-media/shani-deploy/
shani-builder) if it keeps growing.*

## Required verification for a change

```bash
python3 -m py_compile shani_chronoa/*.py shani_chronoa/skills/*.py   # syntax only, not sufficient alone

# Actually construct the GTK objects — catches API-removed/renamed bugs
# that py_compile can't see:
python3 -c "
import gi; gi.require_version('Gtk','4.0')
from shani_chronoa.gui import CajitaWindow, ChronoaOrbWidget
from gi.repository import Gtk
app = Gtk.Application(application_id='test.chronoa')
app.connect('activate', lambda a: (CajitaWindow(a), a.quit()))
app.run([])
"

# If you touch gschema: verify it actually compiles, don't just eyeball the XML
mkdir -p /tmp/schema-test && cp usr/share/glib-2.0/schemas/*.xml /tmp/schema-test/
glib-compile-schemas /tmp/schema-test/   # must produce gschemas.compiled with no errors

# If you touch app.py's async wiring: prove a coroutine actually executes
# through AsyncBridge inside a real GLib.MainLoop, not just that it compiles
# (see asyncbridge.py's own module docstring for why this matters).
```

### Measured on a real Shanios slot (2026-10-01), not inferred

`shani-testbed/slot-tests/chronoa-speech.sh`, on `@blue` (`shanios-20260925-gnome`),
with the checkout overlaid via `--local-src-chronoa`: **11 pass, 0 fail.**
The same slot with the overlay removed: **3 pass, 8 fail**, including
`ModuleNotFoundError: No module named 'shani_chronoa'`. Both runs are the
evidence; either alone proves nothing.

**Speech output works on Shanios today, and `espeak-ng` is load-bearing.**
`PiperTTS().engine()` resolves to `espeak-ng` and `synthesize()` wrote a real
81,698-byte RIFF/WAVE of 1.85s (confirmed with `ffprobe`). That is why
`espeak-ng` is a hard `depends` and the other tiers are `optdepends` — drop it
and every image loses speech output entirely, because RHVoice is packaged
separately and the neural engines (Kokoro, Piper) are opt-in downloads that
are not there on a fresh install.
**Correction (2026-10-02):** this said "piper is not installable on Arch (no
`onnxruntime`)". The runtime is in `extra` now, and Kokoro is the neural voice
that unblocked; `engine()` is a four-way chain, not a three-way one, and the
measured cost of its first entry is in `Kokoro TTS` above. The conclusion — that
`espeak-ng` must stay the only hard `depends` — is unchanged.

**Speech input is absent by design, not by breakage.** `is_available()` is
`False` and `app.py`'s `"Whisper.cpp not available - STT disabled"` is the
branch actually taken. `whisper-cpp` is an `optdepend` that no image profile
installs, and no `ggml-*.bin` exists on any install. Do not "fix" this by
hard-depending `whisper-cpp`: a user without it must get a working assistant
that says so, not a half-configured one whose microphone silently cannot work.
`test_the_piper_voice_dir_moves_with_the_data_home` guards exactly that, and
also that no hard `depends` entry ever starts with `piper` — on Arch `piper`
is a gaming-mouse configurator, not Piper TTS.

An absent dependency must never be a confident wrong answer. The distinction the
slot-test enforces: STT reporting `False` is a PASS, and STT claiming ready with
no binary and no model would be a FAIL.

`whisper.cpp`, `piper`, and `ollama` are unlikely to be installed on a
generic dev machine — STT/LLM/TTS will report unavailable and the app
should degrade gracefully (it's designed to: check `is_available()` on each
component before use). That's a legitimate environment limitation, not a
reason to skip the checks above. If you change the tool-calling loop in
`assistant.py`, verify it with a mocked Ollama endpoint
(`httpx.MockTransport`) simulating a `tool_calls` response — you don't need
a real Ollama server to prove the round-trip logic (parse tool call →
execute → feed result back → final answer) actually works.

`httpx`, `numpy`, and `openwakeword` are not guaranteed to be on a bare
system Python either (`pip install --break-system-packages` is refused by
design on Debian-family systems) — use a `venv --system-site-packages` if
you need to exercise `llm.py`/`assistant.py` (needs `httpx`) or
`wakeword.py` (needs `numpy` + `openwakeword`) for real; `--system-site-packages`
keeps `gi`/GTK reachable since those aren't pip-installable. If you touch
`wakeword.py`, don't trust the `openwakeword` API from memory or from other
projects' usage of it — versions differ (this repo's dev pass found
`Model(wakeword_models=[...])` doesn't exist on the installed version; it's
`Model(wakeword_model_paths=[...])`, taking file paths under
`<package>/resources/models/`, not short names) - actually import it and
call it. If you touch the skill-loading path (`skills/__init__.py`,
`tools.py`), verify with a throwaway module dropped in a temp dir pointed
at by `skills._USER_SKILLS_DIR` — both a well-formed one and a deliberately
malformed one (e.g. `SKILLS` not a list — an earlier draft here iterated a
plain string character-by-character instead of rejecting it).

If you touch `cloud_llm.py`: these are free, keyless, third-party gateways
with volatile availability - live-tested on 2026-09-16 from this dev
machine, two of three configured providers (Kilo, BlockRun) were already
rate-limited/exhausted, and the third (LLM7) rate-limits concurrent
requests, occasionally returns raw non-JSON error bodies (real HTTP 502),
and re-called a tool a second time instead of concluding when driven
through `assistant.py`'s actual loop. None of this is hypothetical - it's
what happened testing it. Two non-obvious protocol quirks, both confirmed
live and NOT something `raise_for_status()` alone catches: (1) Kilo and
BlockRun both return **HTTP 200 with an `"error"` key in the body** on a
rate limit - you must check for that key regardless of status code: (2) a
successful-looking 200 can still have an empty `choices` list. If you
change the provider list or the error-detection logic, re-verify against
the real endpoints (they're reachable with no key at all - `curl
https://api.llm7.io/v1/chat/completions ...`), not against reasoning about
what an OpenAI-compatible API "should" return. Re-test before trusting any
existing "this provider works" note in that module's docstring - free-tier
rosters and quotas move fast (see shani-docs' own caveat that Zen/Kilo's
model list "changes without much notice").

If you touch `mcp.py` or either `usr/bin/` launcher: run the actual
launcher script directly (not just `sys.path.insert(0, '.')` from inside
`usr/lib/shani-chronoa`) — that's the only way the `dirname(dirname(...))`
path bug above was ever going to surface. For `mcp.py` specifically, drive
it as a real subprocess with the `mcp` package's own client SDK
(`mcp.client.stdio.stdio_client` + `mcp.ClientSession`), not just by
calling `build_server()` in-process — the schema-inference behavior only
shows up over a real `tools/call` round trip.

## Model choice (as of 2026-09-16)

Default model is Qwen3 (`qwen3:4b` on capable hardware, `qwen3:1.7b` on the
lowest tier — see `config.py`'s `HardwareProfile.get_model()`), not
`llama3`. Qwen3's dense checkpoints are tool-call-trained at every size,
unlike most small model families where function calling is only reliable
at 7B+. This has NOT been verified live against a real Ollama server in
this environment — if tool calls come out malformed on real hardware,
step up via `--model=qwen3:8b` (that flag is wired to actually take effect;
verify it still is if you touch `_parse_args`/`_init_components` in
`app.py` — it silently didn't for months before this pass, and `--voice=`
had the identical bug).

## Packaging: two different naming conventions, don't mix them

`PKGBUILD` (Arch) and `DEBIAN/control` (Debian) need different dependency
name conventions for the *same* underlying package — e.g. GTK4 is `gtk4` in
Arch, `gir1.2-gtk-4.0` in Debian; `python-gobject`/`python-httpx` in Arch,
`python3-gi`/`python3-httpx` in Debian. The PKGBUILD here previously had
Debian-style names copied straight in, which would have failed
`makepkg`/pacman dependency resolution outright. Cross-check against a
sibling Arch PKGBUILD doing the same kind of GTK4 + httpx app (e.g.
`shani-pkgbuilds/waydroid-helper`) before trusting a dependency name in
either file.

## Cross-repo impact

No `AGENTS.md` existed for this repo before 2026-09-16 — it's newer than
the other ~15 `shani-*` repos. This repo is currently standalone (no
sibling depends on it, and it doesn't yet talk to `shani-platform` or
`shani-fleet`). `mcp.py` now *is* a real MCP server (see "What this repo
is" above) — that placeholder-REST-client note is stale as of the pass that
replaced it; don't trust old descriptions of `mcp.py` in memory or prior
notes without re-reading the file.

## Settings window (added 2026-09-16)

`settings_window.py`'s `SettingsWindow` now exists - plain GTK4, no
libadwaita, opened via a gear button in `gui.py`'s header row or
`Ctrl+,`/`app.open-settings`. Switch rows call the app's own GActions
(`app.activate_action(...)`) rather than writing gsettings directly, so a
toggle's side effects run through the same code path as the keyboard
shortcuts - see the module's own docstring. Building it surfaced two more
real bugs, both fixed: `config.model` and `config.whisper_model` had
non-empty gschema defaults (`'qwen3:1.7b'`, `'base'`) that were **never
actually read** by `_init_components` (it always used
`HardwareProfile.get_model()`/`get_whisper_model()`) - exposing them as
editable settings would have silently done nothing. Fixed by changing both
defaults to `''` (meaning "no override, auto-detect") and wiring
`_init_components` to check the persisted gsetting between the CLI
`--model=` override and hardware auto-selection; verified live with a
patched `ChronoaConfig.get()` that both an override and the empty default
correctly reach `OllamaLLM.model`/`WhisperSTT.model`.

Testing gotcha hit while verifying this: `Gtk.ScrolledWindow.set_child()`
auto-wraps a plain (non-`Gtk.Scrollable`) child in a `Gtk.Viewport` - a
test script doing `window.get_child().get_child()` to reach the content box
silently gets the Viewport instead and finds only one "row" (the box itself,
misread as a single child). Confirmed by tracing every real `Gtk.Box.append()`
call during construction (all 49 fired correctly) before finding the
missing `.get_child()` hop was in the test, not `settings_window.py`. If a
future check on a `Gtk.ScrolledWindow`'s contents finds implausibly few
children, check for this before suspecting the widget-building code itself.

## Senses layer: wired into the live chat path (2026-09-27)

The senses layer (`senses/`) was built, consent-gated per sense, unit-tested
and exposed through the headless CLI - and reached **no LLM turn at all**.
`app.py`, `assistant.py`, `tools.py` and `mcp.py` imported none of it, so
`shani_chronoa-sense` was its only consumer and every percept it produced died
in the one-shot process that made it. This is the same dead-code class as
`tool_tracking.py`/`gateway_supervisor.py`/`sandbox/profiles.py`
below, and `test_sense_manifest.py` only passed because the CLI counted.

Fixed: `Assistant` takes an optional `percept_store` + `context_builder`, and
`Assistant.build_messages()` re-reads live percepts and inserts them as one
transient system message on **every** request (`app.py:_init_components`
constructs a real `PerceptStore()` + `ContextBuilder()` and passes them in).
`build_messages()` is called fresh on each round of the tool loop, not once
per turn, so a percept added or expired mid-turn is reflected immediately.

Three things this deliberately does **not** do, all of which look like
oversights and are not:

- **Percepts never enter `_history`.** `MAX_HISTORY_MESSAGES` is 40 and
  `_trim_history()` preserves only `_history[0]` - a percept parked at
  `_history[1]` would be silently deleted by the next trim, would eat the
  conversation's context budget (Ollama *drops* rather than errors on
  overflow), and would outlive its own TTL. Asserted directly, including
  after enough turns to force repeated trimming.
- **No sense is exposed to the LLM as a callable tool.** That is a separate
  design decision touching the fixed-whitelist boundary; this change only
  makes perception the assistant already has *visible* to the turn. A
  percept recorded by the CLI (or by any future in-GUI producer) is now live
  on the next turn, but the GUI still cannot itself *ask* a sense anything.
- **Consent is now re-checked when a percept is *sent*, not only when it was
  recorded** (decided 2026-09-29). This was previously recorded here as an
  open decision, and it was a real gap: `ContextBuilder` had no consent check
  at all, so a fact captured while `memory-sense-enabled` was true kept being
  injected into every prompt after the user set it to false. Proven by running
  it, not by reading it. `ContextBuilder` now takes a `consent` predicate and
  drops anything whose sense is no longer permitted, on every turn.

  This **withholds rather than deletes**: the percept stays in the local store
  and reappears if consent is granted again, so withdrawing a permission costs
  the user nothing they did not ask to lose. Two traps worth knowing:

  - The `consent=` argument in `app.py` is load-bearing. Omitted, the
    behaviour reverts silently and nothing else notices — which is how
    `privacy.py` came to gate on a sense name that no longer existed. A test
    constructs the real application and asserts the argument is present.
  - `consent=None` keeps `ContextBuilder` a pure renderer, because the CLI and
    the unit tests construct a bare one. Only the path that actually sends to a
    model supplies a predicate.

  Every sense name stamped on a percept was checked against
  `_SENSE_CONSENT_KEYS` before the filter was added, so it withholds nothing
  legitimate.

**A skill cannot see the app's transient percepts, and the durable file is the
wrong instrument for the question.** `list_percepts` shipped claiming to show
"every fact held right now" and how many "would be sent with the next reply".
Both were false. A skill runs in a subprocess, so it built its own
`PerceptStore` and read `memory.jsonl` — which only ever holds the `memory`
sense's facts. Every transient percept, i.e. what a running app is mostly
holding, was structurally invisible, so it would have under-reported the answer
while stating it as complete. `PerceptStore` now publishes `live.json` beside
the durable file on every `add()` and `clear_transient()`, renamed into place
so a concurrent reader never sees half a file.

  - `clear_transient()` must publish **after** clearing. Publishing before it
    restates exactly the facts the caller just discarded, so a reader landing
    between the two lines is told the opposite of the truth.
  - `_live_path` is a **per-instance** attribute, not a module global read at
    write time. With a global, a store constructed with a custom
    `durable_path` — the parameter tests already use — still publishes into the
    shared location; that is how a test run wrote a fabricated snapshot
    (`pid 424242`) into a real user's `~/.local/share/shani-chronoa/percepts/`.
  - `list_percepts` must name its provenance. A view older than 5 minutes is
    reported as a leftover from a process that is gone, not as the present, and
    an absent view says no app is publishing. A smaller *honest* answer is the
    requirement; a confident wrong one is the failure.

**Verified by running, not by reading:** the real `ChronoaApplication()` (a
genuine `Gtk.Application`, not `__new__`) through the real `_init_components()`,
with a real `Percept` added to the app's own store, produced a prompt
containing `- [memory/fact] the meeting is at 4pm in the annex` at index 1,
`percept in _history: False`, `_history length: 1`. The cross-process case is
proven through the real `usr/bin/shani-chronoa-sense` launcher as a
subprocess, and both the revert-the-wiring and the park-it-in-`_history`
mutations were run to confirm the tests fail without the fix.
Suite: 190 passed, 1 failed - the failure is
`test_tool_tracking.py::test_export_csv` (`NameError: name 'record' is not
defined` at `tool_tracking.py:128`, where the loop variable is `call`), which
is in concurrent, unrelated work in `tool_tracking.py` and was left alone.

**Two real defects found by running, deliberately NOT fixed here** (both are
in the senses CLI, both need a `Sense.run` contract or ownership decision
rather than a mechanical patch):

- `argfile.py`'s module docstring wrote a bare `\uXXXX` in a non-raw
  docstring, which is a `SyntaxError` - so `tools.py`, `assistant.py` and
  `app.py` could not be imported at all, i.e. **the whole assistant was
  unlaunchable**, and every module-level test passed anyway because none of
  them import it. Fixed (one backslash), because it blocked all verification
  here; it is the clearest argument yet for this file's own rule that a
  passing test suite proves nothing about the app starting.
- `shani-chronoa-sense --durable-file PATH run memory operation=remember`
  ignores `PATH` for the *durable* write: the fact lands in the default
  `~/.local/share/shani-chronoa/percepts/memory.jsonl` (the memory sense
  holds its own store, which the CLI cannot reach through `run(arguments)`),
  and a following `percepts --durable-file PATH` then reports zero. The
  option's own help text promises otherwise.

Also worth knowing: a fresh `python3 -c` with a overridden `HOME` loses
`~/.local/lib/python3.*/site-packages` from `sys.path`, so `httpx` disappears
and `app.py` will not import. Pass `PYTHONPATH=$HOME/.local/lib/python3.12/
site-packages` (or use a `venv --system-site-packages`) when probing by hand.
The repo's own `test_sense_scheduler.py` handles this for child processes.

## Machine-state senses (added 2026-09-28, merged 2026-09-29)

Thirty-two senses read the machine rather than the user's world. The
machine-state set added that day was `privilege` (who holds a dangerous
capability), `display`, `network`, `bluetooth`, `rfsense` (WiFi RSSI spread),
`thermalgrid` (MLX90640/AMG8833 over I2C), `hwmon` (fan/temperature/voltage/
current from `/sys/class/hwmon`) and `modelfit` (available RAM against the
models Ollama reports), on top of the pre-existing `memory`, `power`, `storage`,
`cpu`, `gpu`, `security`, `devices` and `audio` — plus `senses/latch.py` and
scheduler de-duplication, so a polled sense that keeps saying the same thing is
not deposited 1440 times a day. `hwmon` and `modelfit` have no external binary:
they read the kernel directly, so there is nothing that can be missing for them
to degrade into.

Five more were added after the merges below took the registry to 23:
`filesystems` (what is mounted, and real room per filesystem — `storage` walks
`/sys/block` and reports *disks*, which is not the same question), `services`
(`systemctl` units and whether they are enabled), `timebase` (NTP sync and
timezone), `usb` (the USB bus — `devices` walks PCI, which is the *internal*
bus, so nothing covered what was actually plugged in) and `resources` (zombie
processes, swap in use, file-descriptor pressure: the ways a machine runs out
of something without `cpu` or `memory` looking busy). On the dev box `usb`
found the integrated camera and the Intel wireless device, which no other sense
reported, and `resources` found a `zypak-sandbox` zombie and 810 MiB of swap in
use — all invisible to every pre-existing sense.

Four more arrived 2026-09-29: `updates` (pending package updates, and the age
of the database the count came from - a stale database makes the number a floor,
not an answer), `faults` (recent journal errors, grouped by the unit named in
the message rather than by the emitting pid, because every unit systemd starts
logs as `systemd[1]` and grouping by pid reported a count of pids as a count of
things that broke), `snapshots` (btrfs subvolumes and which one is booted - the
rollback safety net, which nothing could previously report existed) and
`sessions`. `sessions` defaults **off** and is the only machine-state sense that
does: it reports who *else* is on the machine, which is other people's presence
rather than this machine's own hardware.

**`filesystem` vs `filesystems` are not duplicates and must not be merged.**
`filesystem` reads one text file the user names, confined to their home
directory; `filesystems` reports mounts and room. The names differ by one
letter, so the settings labels are deliberately distinct — "Files and folders"
against "Filesystems and room". Do not rename either to tidy this up: the
consent key is `<sense-name>-sense-enabled` and is derived from the name, so a
rename silently makes a sense permanently ungrantable (see the alias rule
below).

**Seven senses were merged away on 2026-09-29, taking the registry from 28
modules to 23** (28 − 7 retired + 2 new, `capture` and `printing`), and this
file was corrected rather than left to mislead the next reader. `contention`,
`camera`, `thermal` and `cooling` no longer exist as modules, so the lesson
below refers to code that moved:

| Gone | Now |
|---|---|
| `contention` + `camera` | `capture` |
| `thermal` + `cooling` | `hwmon` |
| `monitors` | `display` |
| `smart` | `storage` |
| `link` | `network` |

Each retired consent key **still grants its successor** via
`config._SENSE_CONSENT_ALIASES`, because a rename that silently ungrants a
capability is a permission revocation wearing a refactor's clothes. Every
retired key now defaults `false` in the schema, and that is a fix rather than an
inconsistency: three of them (`monitors`, `smart`, `link`) used to default
*true*, which let the alias override the live key and made the settings switch
appear to do nothing. A test asserts every retired key defaults false.

**Three of the original set were green on the dev box while confidently wrong on
the distribution this actually ships to, and the lesson is not optional to
read:** `contention` (now `capture`) reported every microphone and camera as
*free* when `fuser` (psmisc) was missing, because the OSError was swallowed into
"no holders"; `privilege` used Debian's `dpkg-query`, so on Arch — which is what
ShaniOS is — it found no package owner for anything and reported **every process
as unmanaged third-party software**, burying the one real finding; `bluetooth`
reported "0 devices" when `bluetoothctl` was simply not installed. 584 passing
unit tests caught none of it, because the tests ran on the wrong distro.

**Rule: a sense whose failure mode is a plausible-looking wrong answer is
worse than a sense that fails.** "I could not determine X" must be a state the
code can represent, distinct from "X is false" — and it must fail toward
UNKNOWN, never toward a clean result. Ownership lookups span pacman/dpkg/rpm/
zypper and report *undetermined* when none answers.

Also load-bearing and invisible:

- A malformed sense schema is **skipped at load with only a log line**, so a
  registered-but-unusable sense looks merely absent. The contract is Ollama's
  `{"type": "function", "function": {...}}` wrapper, not raw JSON Schema.
- `glib-compile-schemas` rejects `_` in key names and on hitting one discards
  the **entire file** — all keys at once. A sense name must be legal as a key
  fragment; there is a test asserting this across the whole registry.
- ALSA nodes live in `/dev/snd/`, not `/dev/`; `/proc/net/wireless` writes
  `70.`/`-40.` with trailing points; `i2cdetect` prints bare hex `33` and
  prints "Permission denied" on stderr **while exiting 0**.
- Consent key is `<sense-name>-sense-enabled`, derived from the name, so a
  rename silently makes a sense permanently ungrantable.

`rfsense` deliberately states its own ceiling: the research it sits beside
(RF-Pose, RF-Avatar, arXiv 2401.17417) is real but runs on Channel State
Information, and this machine's Intel AX201/`iwlwifi` exposes no CSI. It
answers "is something moving", not "what" — and says so rather than implying
the research result it cannot produce.

Full methodology, the eight running-only bugs, the research correction, and
the live security finding are in **`AUDIT-HISTORY.md`**.

**Verification status 2026-10-06: 5364 passed, 27 failed, 39 skipped** (run in
six chunks, because a single 44-minute run kept being lost to a killed process
group). **All 27 failures are in test files this work never touched** - verified
by intersecting the failure list with the modified-file list, which is empty.
Every one is environmental on this box, and each is a real capability that is
genuinely absent rather than a defect:

| file | why |
|---|---|
| ~~`test_browser_window_probe.py` (7)~~ **no longer environmental — the row was stale (corrected 2026-10-11)** | it said "WebKitGTK is not installed (`require_version` fails for both 6.0 and 4.1)". Measured: **`WebKit 6.0` resolves** (`WebKit-6.0.typelib` present; 4.1 is only refused because 6.0 is already loaded, which is not the same as missing), and the file now runs **7 passed, 2 skipped**. `xdotool`, `grim` and `tesseract` are likewise all present. This row is the shape of a list that stops asking why — it is corrected rather than deleted so the next reader sees that "environmental" is a claim to re-measure |
| `test_setup_extras.py` (6) | `ConsentRequired` - model downloads are gated shut in this environment. Verified pre-existing on the **unmodified** tree |
| `test_sandbox_seccomp.py` (2), `test_sandbox_profiles_live.py` (1) | Landlock and seccomp need the real kernel; AGENTS.md already says so |
| `test_sense_idle.py` (3) | real clock and real X resources, headless |
| `test_everyday_skills.py` (2), `test_input_skills.py` (1) | xdotool / X11 not present |
| ~~`test_permission_decisions.py` (2)~~ **FIXED 2026-10-09 — this entry named the wrong cause** | it was never the missing `grim`/`gnome-screenshot`. The fixture already writes a fake `grim` to a temp dir and puts it on `PATH`, because the skill runs in a sandboxed child that inherits `PATH` and not the test process's patches. What it did **not** supply was a *display*: `screengrab.capture_screen` calls `display_environment()` first and raises *"there is no display to capture"* when neither `WAYLAND_DISPLAY` nor `DISPLAY` is set — so on a headless runner the fake `grim` was never executed. **Supplying the tool does not supply the display.** The fixture now sets `WAYLAND_DISPLAY` too (preferred over `DISPLAY`, because `display_environment()` prefers it and setting only `DISPLAY` on a Wayland desktop would capture the XWayland root — the exact mistake that function's docstring warns against, committed in this test). 19 pass headless **and** with `DISPLAY` set; removing the display fails 2, removing the tool fails 5, so both halves are load-bearing. **A list that says "environmental" stops asking why, and this one was wrong for as long as it was there** — the same lesson as the seccomp entry above. |
| `test_portal_input.py` (1) | no `org.freedesktop.impl.portal.RemoteDesktop` backend |
| `test_recordings.py` (1) | the consent switch is off here, so the honest refusal is returned instead of the granted path |
| `test_vision_sense.py` (1) | a real screenshot of this desktop, which needs `grim` |

**Two failures were mine to fix and are fixed**: `test_packaging.py`'s two
bytecode checks (690 stray `.pyc` left by ad-hoc probes - AGENTS.md's documented
trap, and the only reason to prefer `PYTHONDONTWRITEBYTECODE=1` on *every* probe)
and `test_skills.py`'s two lambda handlers (above). Cleaning the bytecode also
silenced four ordering-dependent failures elsewhere, which is worth knowing:
**stray `.pyc` changes what a full run exercises.**

Earlier in the same session: unit suite green on Ubuntu (1878 passed, 6 skipped)

**Run the suite with `XDG_STATE_HOME` set, or it writes to the real home
directory.** `skills/timer.py` resolves its store from `XDG_STATE_HOME`,
falling back to `~/.local/state` — and it is *not* the timer tests that leak:
run in isolation they leave nothing, but a full suite run without the
variable set leaves a real `~/.local/state/shani-chronoa/timers.json`
containing fixture data (`pasta`, plus a `'; touch /tmp/pytest-of-...`
shell-injection probe label). Pointing `XDG_STATE_HOME` at a temp dir
contained it completely. `XDG_CONFIG_HOME` and `XDG_DATA_HOME` are not
enough — only `XDG_STATE_HOME` reaches this store. Note the same variable
gates the durable percept store documented above, which has already
contaminated a real user's `percepts/` directory once.

Related trap from this pass: an ad-hoc `python3 -c` probe run **without**
`PYTHONDONTWRITEBYTECODE=1` leaves `.pyc` files inside the package, which
makes `test_no_pycache_in_packaged_payload` and
`test_no_bytecode_files_in_packaged_payload` fail on the *next* full run. The
suite itself leaks nothing. Clean the tree before trusting a red packaging
test — and check whether the bytecode is yours before assuming the repo is
broken.

**Two tests in `test_skills.py` could not pass as written - FIXED 2026-10-06.**
Both registered `Skill(run=lambda a: ...)`, and `skills/__init__.py` **refuses a
handler whose `__name__` is not an identifier** (the 2026-10-05 fix: a non-local
skill is dispatched by interpolating `handler.__name__` into an import, so
`from module import <lambda>` is a SyntaxError and the skill was advertised to
the model and failed on every call). So both lambdas were skipped with a warning,
`tools` stayed empty, and `test_duplicate_override_keeps_single_schema` read
`assert 0 == 1`. They were testing the **lambda rejection**, not the
duplicate-name behaviour they are named for. Now they use named module-level
functions.

Worth recording because of how it looked: the failure was `assert 0 == 1`, which
points at "the dedupe did not happen", and the dedupe was fine. A green-looking
assertion about a *count* is ambiguous between "the thing was removed" and "the
thing was never added", and only the warning line above the traceback said which.

**A module-level `lambda` is still a lambda.** The first fix here moved them to
module scope to "keep it inline", which reproduced the same skip.

**Kill a stale suite in one command and start the new one in another.**
`pgrep -f` has the same self-match as the `pkill -f` trap the workspace
AGENTS.md records, and the `[p]` trick does not save you when the pattern is a
fragment of your own command line. Measured 2026-10-06: a single command that
both killed a stale run and launched a fresh one matched *its own shell* — the
`/bin/bash -c` line contained the literal text `pytest tests/` — so `kill -9`
killed itself and the new suite died **before writing a byte**. Two consecutive
"SIGKILL, empty log" results read exactly like a suite that crashes on
collection, and sent me looking for a segfault in a GTK teardown that had never
been reached. Confirm the log has grown before believing any result about a run
that "crashed".

**⚠️ And the `[p]` trick fixes *self-match only* — it is not a safety guard.**
This repo's D-Bus work is where that became expensive on 2026-10-06. Cleaning up
the private `dbus-daemon`s from the gateway round-trip harness, I ran
`pkill -f '[d]bus-daem'`. The brackets stopped the pattern matching pkill's own
command line, and it **still matched every real `dbus-daemon`** — so it killed
the user's session bus at `/run/user/1001/bus`, GNOME Shell lost it and logged
out, and logind tore down four sessions in a row (`40`, `41`, `c3`, `42`,
journaled at 16:04:26–16:04:59). It had been run in several commands, hence four
teardowns.

Two things to take from it:

- **Never `pkill -f` a daemon the desktop depends on** — `dbus-daemon`,
  `systemd`, `pipewire`, `gdm`, `gnome-shell`, `at-spi*`, `ibus*`. Bracketing
  changes nothing about that; it only hides the pattern from `ps`.
- **Clean up by PID, not by name.** The private daemons are created with
  `--print-address --fork`, so the address (and therefore the PID, from
  `pgrep -f "dbus-daemon --config-file=$CFG"` where `$CFG` is *your* mktemp
  file) is knowable. Kill that PID. If you cannot name the PID, you cannot
  safely clean up, so leave it and say so.

And a heuristic that would have caught it in one second: **`pkill: killing pid N
failed: Operation not permitted` is not a partial success.** It means the pattern
matched a root-owned process — i.e. it matched more than you meant — and
everything else it matched was killed. Stop and re-read the pattern there
instead of continuing.

> **MCP stdio verified 2026-09-29** against a real JSON-RPC client:
> `initialize` (protocol 2024-11-05), `tools/list` (70 tools), and `tools/call`
> for a normal call, a malformed call, a shut consent gate, an unknown tool, and
> five identical calls in a row. All 70 tools carry a title and annotations.
>
> Running it needs `mcp` installed *alongside* system PyGObject, which is the
> non-obvious part. A plain venv does not work: `gi` is a system package pip
> cannot supply, so the server dies with `ModuleNotFoundError: No module named
> 'gi'`. And `pip install --user` is refused by PEP 668 on this box. What works
> is a venv *with* system site packages:
> `python3 -m venv --system-site-packages` then `pip install mcp`. The
> repository itself needs no change for this.

⚠️ **The slot run predates the 2026-09-29 sense merges and does not cover
them.** It was 2026-09-28, at testbed `9be7139`, against the pre-merge
packaging. Ten senses were merged after it, and five more (`filesystems`,
`services`, `timebase`, `usb`, `resources`) were added after that, so no
real-hardware run has yet exercised `capture`, `hwmon`, `storage` or `network`
as the single senses they now are, nor any of the five newest. The unit suite
covers the merges; the honest gap is real-hardware coverage of the merged and
newly-added ones, and it needs a slot run to close.

`usb` and `resources` are the two whose value is least provable from a unit
test, because both exist to surface *this* machine's actual condition — the
integrated camera and Intel wireless device on the USB bus, the
`zypak-sandbox` zombie and 810 MiB of swap. Those readings came off the dev
box, not a slot. A slot run should confirm both degrade honestly when the
sysfs tree or `ps` output looks unfamiliar, rather than reporting clean.

`modelfit` earns one note here rather than in AUDIT-HISTORY, because the slot
test's handling of it is the lesson. It reports UNKNOWN in its *model list*
when Ollama does not answer, while the hardware half reads fine — so the
generic step of the slot test no longer guesses whether that UNKNOWN was
honest and reports SKIP, and a dedicated check asserts both halves: that the
sense says UNKNOWN rather than claiming a model count, and that the
`/proc/meminfo` half is still real. An earlier version of that check asserted
a *reason* for the UNKNOWN that turned out to be false, and the version before
it failed senses whose legitimate readings are all one or two digits. A green
line with an invented explanation is worth less than an admitted SKIP.

**A `ups` sense, and the reason the power sense could never have grown one (2026-10-10).**
Shanios moved its UPS daemon from apcupsd to NUT, and nothing on this side knew: the
`power` sense reads `/sys/class/power_supply/*` and keeps only `type == "Battery"`, so a
UPS - which the kernel reports as `type == "UPS"` - was filtered out **by design**. A machine
with a UPS was indistinguishable from a desktop, and the one question a UPS exists to answer
("how long do I have?") had no answer anywhere in this app. Registry **51 → 52**.

  Two sources, answering different questions: `/sys/class/power_supply` (`type == "UPS"`)
  needs no binary and no daemon and reports what the kernel's driver sees; NUT's `upsc`
  reports charge, runtime, load and voltages but only when NUT is actually monitoring
  something - which on a fresh install it deliberately is not (shani-settings ships
  `ups.conf` with no device section, and `nut-scanner -U` is what fills one in).

  **The failure this sense exists not to have.** "No UPS is connected" and "I could not find
  out" are opposite claims, and on a UPS the second is the dangerous one - an unreachable UPS
  is indistinguishable from a dead one, which is exactly why NUT's own `DEADTIME` treats
  silence as on-battery. So every unreadable path reports UNKNOWN with its reason named. My
  first version got this wrong twice and the tests caught both: it treated a readable-but-empty
  `power_supply` as "could not ask" (an absence dressed as a failure), and it silently preferred
  one source when sysfs and `upsc` disagreed - which is how a machine on mains comes to be
  reported as on battery. The disagreement is named now, not resolved quietly.

  **What is deliberately not read:** `/etc/nut/upsd.users` holds the credential that can force
  a UPS power-down, and `ups.conf`'s device section names the port and driver. The sense reads
  `upsmon.conf` for one thing only - whether a MONITOR line exists - and a test asserts the
  credential file is never opened.

  **One invariant `test_sense_manifest` already owned caught this sense too:** the TTL was 15s
  against a 30s poll, so the fact would have expired for 15 seconds of every cycle. On this
  sense that is the difference between "the power is fine" and "nobody has looked lately".

  `ups-sense-enabled` is registered, so the Settings → Senses switch appears automatically -
  that panel derives its rows from `discover_senses()` plus the consent table, which is why a
  new sense cannot fall into the `heard-sound` / `calendar_edit` trap of having a key no user
  can reach. Verified: 52 senses in the registry, 52 with a consent key, none unreachable.

  **The trigger engine was blind to the same hardware, and fixing it found two bugs in code
  that already shipped.** `powerstate` is one of the event types and its reader handled
  `Mains`, `USB` and `Battery` — so a machine with a UPS could not arm "when the power goes
  out" at all, which is the one rule a UPS exists for. The sense could therefore report the
  power was out while nothing could act on it. A UPS answers both halves (`online` is a mains
  source, `capacity` is a battery source), so it needed its own branch; two bugs followed,
  both found by writing the tests rather than by reading:

  * **The shipped status inference could not read a UPS at all.** It was
    `status != "Discharging"`, and a UPS's word is `OB DISCHRG` — which is not equal to it,
    so a UPS on battery would have been read as **on AC**. That silences the exact rule this
    was meant to enable, on the machine that has a UPS. `_ac_from_status` handles both
    vocabularies now (NUT's own `upsc` spells it `OL` / `OB` / `OB LB`).
  * **"First status wins" let one device's word discard every other's.** `status` was
    assigned with `or`, so a UPS that had lost mains read as on AC because some other entry
    said `OL`. Each source now casts a vote and **"on battery" wins** — a discharging source
    is the more urgent fact than a charging one, so OR-ing them would let a charging laptop
    mask a dead UPS.

  The `scope` guard still holds for the new branch: a mouse at 5% must not make a UPS at 90%
  read as low, which is what averaging levels across a `Device`-scoped battery would do.

## Surfaces added 2026-10-01: six event types, post-conditions, "Open with"

Found by `tools/cli_matrix.py` (see below), which reads Chronoa's own registries
beside the OS's commands and interfaces and showed three gaps.

- **Trigger event types 6 -> 12**: `screenlock` (logind LockedHint/IdleHint),
  `powerstate` (`/sys/class/power_supply`), `netstate` (nmcli), `usbplug`
  (`/sys/bus/usb/devices`), `btconnect` (`bluetoothctl devices Connected`),
  `schedule` (local clock). The `source` names the state to be told about and an
  event exists only while it holds, so the engine's baseline-then-transition
  logic fires on the transition *into* it (arming "locked" on a locked screen
  fires nothing). Each has its own default-off `<type>-sense-enabled` key - the
  names deliberately differ from the `power`/`network`/`bluetooth`/`sessions`
  senses so no switch is shared. A peripheral's battery (`scope=Device`) is not
  the machine's; a schedule missed while asleep (beyond `grace_minutes`, 15) is
  skipped, not replayed. In an unbooted nspawn slot screenlock/netstate/btconnect
  read UNAVAILABLE (no session, no NetworkManager, no bluetoothd) - correct, and
  the real-desktop readings were checked on a laptop.
- **Post-conditions 1 -> 14 of 60 actuators** (volume, set_mic_mute,
  power_profile, set_timezone, control_service, kill_process, create_directory,
  delete_file, trash_file, write_text_file, move_or_copy_file, do_not_disturb,
  clipboard). `verify()` now passes the tool name to a two-argument check, and a
  check returning None is UNVERIFIED. That fixed a live bug: `clipboard`'s check
  could not tell get from set, so **reading** a non-empty clipboard reported
  FAILED. delete/trash checks use `files.expand`, not `resolve` - resolving
  follows a dangling symlink and reads it as removed.
- **"Open with Chronoa"**: the desktop entry passes `%U` with a MimeType list;
  `do_command_line` attaches file arguments/`file://` URIs (relative to the
  *invoking* cwd - a second launch is forwarded to the running instance) and
  `--ask=TEXT` sends a question through the window's own send path. Verified in
  the `@blue` slot: the transcript showed `user said: <text>\n📎 os-release`.

`tools/cli_matrix.py` (not packaged) writes a JSON/Markdown/HTML map of every
command (owner, man NAME line, JSON output, intent, safety, which Chronoa
surface it could feed, which skill/sense/trigger already calls it) plus D-Bus,
typelibs, GSettings, Polkit, sysfs, portals and entry points. Run it in a slot:
`SHANIOS_TEST_EXTRA_BINDS=/opt/shani-chronoa:/mnt/chronoa,<out>:/mnt/out
build.sh test enter blue --local-src-chronoa=/opt/shani-chronoa python3
/mnt/chronoa/tools/cli_matrix.py --out=/mnt/out/shanios-matrix`. Its
classifications are heuristics from man summaries - a starting list, not a verdict,
and the output says how far to trust them: `calibration` scores safety and the
sense fit against Chronoa's own modules (2026-10-01: safety 89%, sense recall
83% with 22% of commands classed as senses, up from 61%/19% after the fixes the
disagreements pointed at - a syscall page chosen over the command's, "Format"
read as a verb, `\bpack` matching "packet", no `mixed` class for managers like
systemctl).

**The matrix is also the only authority in the tree on Arch package names, and
three package names this project states to users were wrong (fixed 2026-10-03;
guarded by `tests/test_package_names_match_arch.py`).** `commands[].package` in
`chronoa-matrix.json` is read out of pacman's own file database on a real image,
so it settles arguments about what ships a binary. Auditing every package name
this tree claims against it found three that name a package which **does not
contain the binary the user needs** — each of which sends someone to
`pacman -S <name>` and gets nothing:

- **`wpctl` is in `wireplumber`, not the `pipewire` package.** It left
  `pipewire` when wireplumber split off the PipeWire project, so this was a
  *stale truth* rather than a guess — the kind that survives review forever
  because it was once true. In `skills/privacy.py` and twice in `senses/audio.py`.
- **`bluetoothctl` is in `bluez-utils`.** `bluez` is the daemon and does not
  ship the client. In `skills/toggle_bluetooth.py`.
- **`udisksctl` is in `udisks2`.** `udisks` is the pre-rename name. In
  `skills/manage_mount.py`.

`files._PACKAGE_HINTS` was four entries, so seven of the eleven skills calling
`tool_missing()` fell back to "the package that provides it" — a sentence that
helps nobody install anything. It now carries 45 entries, all read out of the
matrix.

**Two method traps, both hit during this audit and both worth remembering:**

1. **A substring test is not a check.** The first pass matched package names as
   substrings and reported `bluez` and `udisks` *correct*, because `bluez` is a
   prefix of `bluez-utils` and `udisks` of `udisks2` — it found two of the
   three real bugs as clean. Exact equality only.
2. **The matrix's `gsettings` entries carry 5 *example* keys per schema, not all
   of them**, so it cannot confirm an individual key name. To check
   `desktop_setting.py`'s allowlist, use `gsettings list-keys` against the
   installed schemas instead — which is what was done, and all 8 keys it
   references (`enable-animations`, `cursor-size`, `clock-format`,
   `clock-show-seconds`, `show-battery-percentage`, `enable-hot-corners`,
   `natural-scroll`, `tap-to-click`) exist and are correct. A clean result, worth
   recording so nobody re-audits it blind.

**`open_surfaces` is a lead list, not a gap list - read this before building a
skill out of it (measured 2026-10-03).** The matrix flags a command as a skill
candidate when no Chronoa code *calls that binary*, and this repo deliberately
answers questions without the binary. Checking the top candidates against the
244 skills that exist (`discover_skills()` → 244 tools and 244 handlers,
**re-measured 2026-10-11** — this figure moves as skills land, so treat the number
as a measurement and not a constant) — **not** the 128 that
`READ_ONLY_TOOLS | MUTATING_TOOLS` sums to, which is a *classification* used for
explore mode, and a subset rather than the whitelist): `fastfetch`, `lscpu`,
`lsblk`, `lsmod`, `ps` and `uptime`
are all flagged, and all six are already answered by `system_info` /
`list_processes`, which read `/proc` on purpose because those binaries can be
absent on a minimal image. `rg`, `fd`, `tree` and `locate` are flagged and are
`search_file_contents`, `find_files` and `directory_tree`. So does `pactl` -
which turns out to be covered too, by `volume.py`'s numeric device-node
handling. **`open_surfaces` measures calls, not answers.** Also note `ideas[].score`
is a category-size number, not a value one: every entry in a big category ties
at 25, so ranking by it surfaces `lur-command` and `gendict`.

> **Both of those column names were fixed 2026-10-10, and the fix was measured
> rather than argued.** `ideas[].score` is now `ideas[].category_weight`, counted
> against the matrix in this tree: of **382 ideas, 8 tie at exactly 25** - every
> one an `inspect` intent in one of the two biggest categories - and **94 score
> 0**. A number with a tie at the top and a floor at zero is ranking how *big* a
> category is, so both renderers now say "category weight" and the markdown says
> in as many words that it is **not a priority**. `service_exposure` became
> `service_sandboxing` for the same reason: the number is systemd's count of its
> own sandboxing options (`systemd-analyze security --offline`), which says
> nothing about what reaches the service over the network - and "exposure" is the
> word a reader would use to decide what to harden. The field keeps systemd's own
> vocabulary for the *value*, because that is genuinely what the tool prints;
> the rename is of what this tree calls it. `tests/test_cli_matrix_names.py`
> drives the parser against a real `systemd-analyze` line and asserts the tie
> from the matrix on disk.

Two skills were built from a matrix pass on 2026-10-03 after that check, both on
`/proc`/stdlib rather than the flagged binary, both verified against the real
thing: `json_query` (`jq` was the flagged command; `analyze_table` already reads
JSON but flattens it, so it cannot address a nested field) and `port_owner`
(`ss`/`lsof` were flagged; verified to report the same 10 listening sockets as
`ss -tuln` on this machine, and it attributes sockets that `ss -p` cannot
without privileges).
podman, git, nmcli, flatpak, pactl...), from COMMANDS sections and from
`<cmd>-<sub>` pages; classification is by whole-word name, then the full
description, and defaults to "changes" - `checkout` is not `check`, and
`log-level [LEVEL]` is a setter. `--scaffold` of a mixed tool exposes only
its reading subcommands as an allowlisted enum (systemctl: 20 of 74).

Distro-level modes, all run on the host from the JSONs a slot run wrote (one per
image; the GNOME and Plasma images are separate harness data dirs):
`--audit-packaging A.json B.json [--strict]` checks the matrices against
shani-pkgbuilds and image_profiles (listed-but-not-installed, meta deps, drift
between the profiles' lists and between the images, Chronoa's undeclared runtime
deps, image lag against `pkgver-pkgrel`, setuid and password-less Polkit
surface); `--strict` exits 1 on the hard ones and does not blame a stale image on
its lists (an image older than its profile's last list commit is reported as
stale). `--suggest A.json B.json [--suggest-json F]` ranks fix / rebuild / add /
review / consider: "add" is measured (an installed package's unmet optional
dependency, wanted by two or more; a command or Python module Chronoa uses),
"consider" is a labelled catalog (opinion), X11 tools are notes, not Wayland gaps. The JSON also carries
each package's size, build date, depends and optional deps, enabled-unit links,
broken launchers/units and Chronoa's unresolved imports, so the audit reports
each explicit package's *exclusive* footprint (what removing it alone frees),
services enabled on one image only, and stale builds. `--resolve-files` asks
pacman's files database (run `pacman -Fy` first - the builder can) instead of
the hand-kept command->package map. `--enrich M.json --security-json all.json --tldr-zip tldr.zip
--pkgstats-cache F` adds web data (run in the builder - it has the network and
`vercmp`): Arch security tracker AVGs (only a recorded fixed version newer than
the installed one is a "fix"; an open AVG with no fix is "review", because the
tracker leaves old ones open - 2026-10-01: 25 open, 0 fixable on either image),
tldr coverage, and pkgstats popularity from ONE paged list down to 0.5% (24 s
for 11,873 packages; a request per package took minutes). pkgstats candidates
are filtered to things a person picks: leaves in the sync db, not explained by
a popular dependent (libodfgen <- libreoffice), not the base-devel closure, not
the other desktop's image, not Arch-maintenance tools, and not apps the profile
ships as a Flatpak. Rare-on-Arch packages on the image come with the chain that
pulled them in (arpwatch 0.52% <- shani-tools-network).

## Kokoro TTS (2026-10-02): run by sherpa-onnx, the way Piper is run by its own program

Kokoro (82M, Apache-2.0) is a voice, and like Piper it needs a program to speak
it. It first shipped as `kokoro.py`, which phonemised with espeak-ng and ran the
ONNX graph through `python-onnxruntime-cpu`. **That module is gone (2026-10-02).**
`tts.py` now runs sherpa-onnx's `sherpa-onnx-offline-tts` (`sherpa.py`: one
27 MB release in the user's home, onnxruntime bundled, found through the
binaries' `$ORIGIN/../lib` rpath) with sherpa-onnx's packaging of the model
(`kokoro-int8-en-v0_19`, 98 MB, eleven speakers). Nothing system-wide, no
numpy or onnxruntime for Python, and no PKGBUILD/DEBIAN dependency on either.

- **Voices are speaker ids.** `voices.KOKORO_VOICES` maps six female voices to
  their `--sid`; each id was checked byte for byte - row 30 of the speaker's
  slice of `voices.bin` equals row 30 of that voice's own style bank in
  onnx-community/Kokoro-82M-ONNX (the first 21 rows of every bank are
  identical, so row 0 would prove nothing).
- **Opt-in, by measurement.** RTF about 1.2 on a CPU (7.7 s to make 6.4 s of
  speech in a slot; 2 and 4 threads the same; the old Python path measured
  1.18-1.32 too), so as a default it put seconds of silence before each reply.
  `kokoro-tts-enabled` defaults false; setup's Voice page turns it on when a
  Kokoro voice is chosen.
- **One list of voices.** Setup shows Piper and Kokoro voices together; picking
  one installs the engine it needs (`setup_wizard.setup_voice`), and picking a
  Piper voice turns Kokoro off, so the voice chosen is the voice heard. The
  "Listen" button tries a voice without saving it (`PiperTTS.kokoro_trial_voice`
  / `piper_trial`, never a monkeypatch on a live instance).
- **English only.** A reply in another script (`languages.script_of`) goes to
  that language's Piper voice instead.
- Intelligibility was checked earlier by transcribing each engine's output back
  with whisper: espeak-ng 0.920 and Kokoro 0.926 mean similarity - both clear;
  the gain is naturalness, not intelligibility.

## The goal (2026-10-02): a harness that makes the smallest model beat bigger ones - measured

The user's stated goal: Chronoa's harness should be good enough that the
smallest local model (Qwen3 0.6B/1.7B on a CPU) outperforms much bigger models
on what people actually ask. Judge changes by that, and **measure it**:

- `tools/task_eval.py` + `tools/eval_cases.json` (not packaged): ~60 requests
  with the call a correct assistant makes, scored on tool and arguments,
  **never executed**. Configs: `bare` (every schema - 131 tools are ~19,700
  tokens, past the 8k local context, so it cannot even be sent), `select`
  (tool_select), `select+recover` (the default: calls written as text
  recovered), `+compact`; and `--cloud=kilo|blockrun|llm7` (the free keyless
  providers, user's choice of baseline - "some free are good") as `cloud`
  (every schema, no harness) and `cloud+select`.
- A provider that is busy, rate-limited or over quota is retried and then
  counted as **not measured**, never as the model being wrong (LLM7 was over
  its daily quota during the first run).
- shani-testbed `slot-tests/chronoa-eval.sh` runs it against the real model on
  a slot. `tests/test_task_eval.py` checks the scorer from both sides and that
  every case names a real tool and real arguments.

### Measured, both local models (2026-10-02)

Raw results: `shani-install-media/test-env/eval-out/eval-qwen3-{0.6b,1.7b}.md`.
62 cases, right tool **and** right arguments, scored only - never executed.

| config | 1.7B | 0.6B | mean seconds (1.7B) |
|---|---|---|---|
| `bare` (every schema) | **0/62** | 5/62 | 0.08 |
| `select` (tool_select) | **56/62 (90%)** | 44/62 | 7.7 |
| `select+recover` (the default) | 56/62 | 42/62 | 37.4 |
| `+compact` | 52/62 | 38/62 | 8.7 |

Four things this settles, all now the defaults in `local_llm.py`:

- **The harness is what makes the small model work.** `bare` scores zero on the
  1.7B, and not because the model is bad: 131 tool schemas are ~19,700 tokens
  against an 8k context, so the request is rejected before the model reads a
  word (`request (20724 tokens) exceeds the available context size (8192
  tokens)`). Narrowing to the relevant tools takes the same weights from 0/62 to
  56/62.
- **`bare` scoring 5/62 on the 0.6B but 0/62 on the 1.7B is not the 0.6B being
  better.** It is that the smaller model emits a shorter tool call that
  occasionally fits under the limit. Both are unusable; neither is a result.
- **`COMPACT_TOOLS = False`.** Shrinking the schemas further made it *worse*
  (52/62 and 38/62), not better. Measured, not assumed - this reverses the
  obvious guess.
- **`select` beats `select+recover` on speed** - equal accuracy on the 1.7B
  (56/62 either way) at 7.7 s against 37.4 s, and better on the 0.6B (44 vs 42),
  so recover stays a fallback rather than the default path.

The 1.7B run is the evidence for the whole `tool_select` design; read it before
changing how tools are offered.

## Distillation, and the student that now routes (2026-10-04)

`distill.py` asks the **configured** model which skill answers a request, keeps
only the answers a human already agreed with, and trains a small Bernoulli router
on those. `tools/route_cases.json` is the labelled corpus (54 requests, 6 per
skill, every label read off the live schema); `tools/distill_run.py` runs it and
prints what was learned. Measured on a real booted `@blue` slot, teacher
**llama.cpp / qwen3-0.6b** (`slot-tests/chronoa-distill.sh`, 9 pass 0 fail):

| what | measured |
|------|----------|
| teacher agrees with the human label | **47/54 (87%)** |
| student, held out by request | **62% vs a 12% majority baseline** (8 held out) |
| student right across all 54 cases | 32/54 - it can never be right on the 7 the teacher got wrong |
| `tool_select` sees the written student | yes, and picks `get_datetime` |
| `tools/eval_cases.json` shape (one request per skill) | **refuses**, with the reason |

**The teacher was unreachable for Claude, ChatGPT, Gemini, Groq, OpenRouter,
opencode-zen and the keyless gateways**, because the first version called
`.chat()` and only `OllamaLLM` defines that; llama.cpp and the whole cloud chain
raised `AttributeError`, the loop moved on, and a machine with a configured
Anthropic key reported "no language model is configured". The uniform interface is
`chat_message()`, and `AnthropicLLM` reaches through the adapter already shipped.
Both privacy gates (privacy off **and** cloud-fallback-enabled) gate anything off
this machine, and keys are registered with `redactor` - without that the
sanitisation of the routing cases is a silent no-op.

**Four bugs in the student, all found by running it, not reading it:**

- `parse_choice` returned the first `[a-z][a-z0-9_]*` in a reply, so "The skill
  is `edit_image`" parsed as the skill **`he`**. It now segments whole
  identifiers and accepts a token only if it is a skill that exists.
- The per-class feature map was seeded from the **class** indices, so only the
  handful of features hashing below the class count were counted and every score
  came out equal for all but one class.
- Bernoulli's absent-feature term summed `log(1 - p)` over all 16,384 columns,
  which handed the most-used skill a ~450-point head **before a word was read**;
  every request was answered with it. Counted over the fitted vocabulary only,
  as scikit-learn does.
- The split was a contiguous tail, so the 54-request corpus (grouped by skill)
  put whole skills in the held-out half: a 91%-correct teacher "distilled" to a
  student scoring **0% against a 100% baseline**. It is now stratified per skill
  and never splits one request.

**The student is a prior, never an authority.** `tool_select.distilled()` may only
return names already in the candidate list, and only when the router separates
first from second by a score margin - there is no probability to quote, so there
is no confidence percentage anywhere. `distill.fallback()` carries a turn when
**no model answers at all**, and only for a skill that `safe_to_run_unattended()`
clears: nothing required (a router cannot fill in arguments) and nothing
destructive or outward-facing - `delete_file` declares no required parameter, so
the argument gate alone waves it through and the second gate is what stops a
classifier from choosing to delete something.

`distill.harvest_rows()` mines this machine's own sessions for
(request, skill) pairs, filtered by the **recorded** verdicts rather than a
prediction. On this machine it finds 11 rows, all of one repeated question, which
is why no student is shipped here and the fixture was used instead.

## The outcome model learns now, and this is the measured shape of it (2026-10-04)

Before today it could not be trained at all, and the reason was four defects
rather than weak features. All measured on this machine's own log - **11,636
labelled calls: 10,936 `unverified`, 351 `verified`, 349 `failed`**:

- `OutcomeModel.__init__` defaulted `self.w` to `None` while `fit()` read
  `self.w.get(...)`, so **every fit from scratch raised `AttributeError`** and
  `train_and_report()` crashed instead of reporting. A crash is not a refusal: the
  report that would have said "no signal" never appeared either.
- Unweighted, the model predicted `unverified` for **all 11,636 calls** -
  precision and recall exactly 0.000 on both minority verdicts. A 3%-positive
  class does not move a 30-step gradient through 1e-4 of L2. Inverse class
  frequency fixes it; 150 epochs instead of 30 changed nothing measurable.
- The balanced weights were **not normalised**, so every step was ~1e-7, the
  fitted weights landed near 1e-4, and `to_dict`'s `round(v, 6)` stored them as
  **zeros**: a file that verified its own digest and predicted 0.3333 for all
  three verdicts on every input.
- The split was ordered by time, so the training half held 8,462 `unverified`,
  264 `failed` and **zero** `verified` and nothing could be written. Folding over
  **feature vectors** fixed the starvation - all 1,979 `verified` calls live in
  just **3** distinct vectors, so no split that keeps a vector whole can balance
  them. `cross_validate()` is what `train_and_save` now judges on, and the model
  written is fitted on everything.

`Report.honest()` no longer asks only "does top-1 beat 94%?". That question
cannot be answered by any model on this data, and the gate had never once been
able to say yes. It now accepts a verdict that is genuinely **detected**: at
**2x its own base rate on both recall and precision**, with recall >= 20%.
Both halves are required because either alone is gameable - `verified` is 3% of
the data, so a model that flags *everything* as verified scores **~33x recall
lift** while being useless. `tests/test_outcome_model_learns.py` asserts that
degenerate report is refused.

**What it can and cannot do, on 5-fold grouped CV over all 11,636 calls:**

| verdict | recall | precision | recall lift | precision lift | verdict |
|---------|-------:|----------:|------------:|---------------:|---------|
| `unverified` | 85.5% | 96.8% | 0.91x | 1.03x | not detected |
| `verified` | 99.7% | 17.7% | **33.1x** | **5.86x** | **detected** |
| `failed` | 0.0% | 0.0% | 0.00x | 0.00x | not detected |

So: it can flag a call that looks like it will verify, 5.9x better than chance on
precision; it **cannot** spot failures at all; and as a top-1 classifier it scores
83% against a 94% constant, so it is a flag and not a predictor. `failed` is 7
vectors and 2 of them are 1,831 examples each - there is nothing to generalise
from. `tests/test_outcome_model_learns.py` holds all of this shut, and
`learning.render_outcome()` prints both lifts so a 94% score cannot read as
success.

## Installing on immutable ShaniOS: verified, not assumed (2026-10-04)

`shani-testbed/slot-tests/chronoa-state.sh` boots a slot and asks where
Chronoa's state actually lands, because on this layout "it works on my machine"
and "it works here" are different questions. The image's own
`image_profiles/shared/overlay/rootfs/etc/fstab` is the spec:

| path | what it is | consequence |
|------|-----------|-------------|
| `/` | read-only root slot (@blue/@green) | nothing may be written here |
| `/etc` | overlay, upper in `/data` | writable, survives a switch |
| `/var` | tmpfs (`systemd.volatile=state`) | **gone on reboot** |
| `/home`, `/root` | `@home`, `@root` btrfs | persists |
| `/data` | `@data` btrfs | persists |
| `/var/cache` | `@cache` btrfs | persists, **shared between slots** |
| `/var/log` | `@log` btrfs | persists |

Measured in a booted slot: **every** Chronoa path is writable, none is on
volatile storage, and a write-read-back round trip passes through all of them.

**The download cache is on `@cache`, and that is not luck.** `stt_provision`
defaults to `/var/cache/shani-chronoa/models`, which the fstab documents as the
directory that exists "to avoid re-downloading packages after slot switches" - so
a 639 MB model is fetched once per machine, not once per reboot *and not once per
blue/green switch*. `SHANI_DOWNLOAD_CACHE` overrides it.

**The write side was already right, and it is worth knowing why.**
`stt_provision._install()` always writes to `user_dir` and treats `system_dir`
(`/usr/share/piper`, `/usr/share/shani-chronoa/llm`, ...) as *read-only* - those
paths are only where a reader may look. So a read-only root needs no special
case: nothing tries to install into `/usr` in the first place.

**Two failures this test reported that were the test's own:**

- It exported `HOME=$T/home` under `/tmp`, then asked whether the paths were
  volatile - so it audited its own scratch directory and reported, in all
  seriousness, that Chronoa's models were "on tmpfs, so lost on reboot". The
  verdict now runs under `env -u HOME -u XDG_DATA_HOME ...`, because the question
  is where Chronoa puts things on a real machine.
- It failed every path on "not in my list of persistent filesystems", which under
  nspawn is `overlay` rather than `btrfs`. **Only tmpfs is a finding**; a
  filesystem this test does not recognise is reported as unrecognised, not as a
  defect - the same rule the diagnostics panel applies to `systemctl is-*`.

Both are the shape of bug this repository documents: a check that fails for its
own reasons and is then believed.

## The UI features worth taking from assistd, sayri and Alpaca (2026-10-04)

**Taken: the wait is now a state.** `assistd` carries
`VoiceCaptureState::Queued` — "waiting for the GPU to free up before
transcribing" — beside `Idle`, `Recording` and `Transcribing`. Chronoa had built
the gate that produces that wait (`speech_gate.py`) without producing anything
anyone could *see*: the orb simply paused between listening and thinking, which is
indistinguishable from the microphone having died. There is now a real
`AssistantState.QUEUED`, wired in `app/voice.py:_transcribe`, so the orb moves
and holds there for as long as the wait lasts.

Adding a state is not one line, and the tests that say so are the point:

- `test_window_ux.py` refuses **any colour reuse**, because colour alone excludes
  colourblind users. A first version deliberately reused listening's green - the
  reading that felt right - and the existing invariant overruled it. Queued is
  `#14b8a6`, teal, between listening (green) and thinking (amber), which is where
  the state actually sits.
- `test_window_theming.py` keeps an explicit `STATE_PALETTE` so a colour cannot
  "quietly slip through", and requires a matching `halo-<state>` ring so the two
  rings cannot disagree about what is happening.
- `tests/test_queued_state.py` adds the forward guard: every state needs a
  colour, an icon, a label, a CSS rule, and - **only if it animates** - a
  reduced-motion rule. That last qualifier matters: a first version demanded one
  of every state, which produced a test that could only fail.

**And then taken, because it turned out to be a missing feature rather than a
UI change: `assistd`'s presence cycle.** Measured on a real slot with a real
model, `slot-tests/chronoa-presence.sh`:

| step | measured |
|------|----------|
| while Active | **2,659 MB** resident in `llama-server` |
| after "Free the model" | **no process remains** — the memory went back |
| after a question | answering again, cold-started |
| what the UI shows | `ACTIVE | Free the model` |

**Chronoa could not release the model at all.** `local_llm` had `start_service()`
and no `stop_service()`, and setup ran `systemctl --user enable --now` — so from
the first login the unit came up on its own and stayed up, holding a 1.1-4 GB
model resident whether or not anyone was talking to it. There was no lever.

**Drowsy does not claim to unload weights**, because nothing here can. It is:
listening, model not resident, next question loads it cold. Sleeping is the same
but waits to be asked — which is the distinction a person actually wants between
"let go of the memory for now" and "leave the machine alone". A state called
"drowsy" that quietly meant "still fully loaded" would be the exact failure this
repository documents. `tests/test_presence.py` pins the *wording*, because the
wording is the claim.

Two things release depends on, and both were missing:

- `_use_local_server(wake=True)` now **starts the service** when it is not
  answering. With a bare `is_up()` check, a released model was simply gone — a
  question would have fallen through to the cloud fallback or nowhere. Releasing
  a model nothing brings back is not a feature, it is a brick. `wake=False`
  remains for callers that must not start anything: *checking* whether a model
  exists is not a reason to load one.
- The guard reads a **presence**, not the `_can_answer` bool. A bool cannot tell
  "no model installed" from "asleep on purpose", and a first version compared the
  bool to an enum — never true, so it silently disabled the whole guard.

`stop_service()` deliberately does **not** `disable` the unit: it stays enabled
so a question can start it again, which is what makes Drowsy mean *released, not
gone*. Disabling would leave a machine whose assistant silently never answers.

**Two measurement notes, both the same lesson.** The slot has no user session
bus, so `systemctl --user` cannot be exercised there — which is also why every
GUI run in that slot logs "Ollama not available" and starts with no model. The
test therefore measures the *memory* claim directly, because that claim does not
need systemd. And a first version started the binary straight from an `llm.env`
that had never been written, so the server took its own default port — **8080**
in llama.cpp 0.5.0, against the 8765 Chronoa configures — and the health check
polled the wrong one for four minutes. Reproduce the product's *sequence*, not
just its commands.

**And then taken as well, on the user's instruction to do both.**

### Alpaca's model manager — `gui/surfaces/models.py`

An `Adw.NavigationPage` with a view stack: **On this machine** against **Available
to add**, a search bar filtering both, and a `Gtk.FlowBox` per list whose
`min/max-children-per-line` are bound to `Adw.Breakpoint`s at 560px and 1000px.
Registered in `SURFACE_IDS` under "This machine", so it is in the sidebar.

25 cards across all four engines, read from `local_llm.TIERS`,
`stt_provision.MODELS`, `voices.VOICES`/`KOKORO_VOICES` and `local_vision.MODELS`
rather than a list kept in step by hand. Kokoro's card says **one download covers
every Kokoro voice**, because six cards without that sentence read as six
purchases of the same 310 MB.

**A card states a fact and offers one action; none of them loads a model.** A pin
writes a setting and stops - the model comes back through a question, via
`start_service()`, so a pin can never leave the assistant claiming to be ready
while a server it never started stays down.

Three things this cost, each found by building rather than by reading:

- `Adw.SearchBar` **does not exist** on libadwaita 1.5; `Gtk.SearchBar` does. Same
  shape as `Adw.BackButton` earlier - check the installed library.
- `KOKORO_VOICES` holds `KokoroVoice` objects, not strings, so `.split` on one
  raised on the first render.
- **The Eyes list was silently empty.** The vision check called
  `local_vision.is_provisioned`, which that engine does not have - the function is
  `verify` - inside a bare `except: pass`. So the panel shipped with no vision
  cards at all, which are exactly the models the "what do I have" view exists to
  show. `except: pass` around a probe is how a panel ends up quietly incomplete;
  the import guard is now around the *import* only, and a test asserts the kinds.

`Adw.Breakpoint.new()` takes a condition rather than a width and the condition is
built by `Adw.BreakpointCondition.parse()` - neither of which the first attempt
used. `gui/sidebar.py` already documents both by calling the installed library
and recording what raised, which is why this was copied rather than rediscovered.
And `Adw.Breakpoint` exposes no way to read its setters back, so the binding is
asserted where it is observable: the flowboxes' own column properties.

### sayri's tray indicator — `app/tray.py`

One click is one action: the indicator's `activate` calls the same
`app.toggle-listening` the window's button calls, and `popup-menu` is swallowed, so
there is no menu in the way. **There is one implementation of "start listening",
not two that can disagree.** The icon's label follows `AssistantState`, so it never
claims more than the assistant is.

GTK4 removed `Gtk.StatusIcon`, so this needs `libappindicator`, which Chronoa does
**not** depend on - and that is the point rather than a problem: `available()`
returns `(usable, why)`, `build()` returns `None`, and the Desktop panel has a
**"Tray icon"** row that names the package which would fix it. A tray that silently
does not appear reads as a bug in the assistant; one that reports "not installed -
the libappindicator package provides one" reads as a missing package.
- **assistd's *Drowsy* in its original sense** - llama-server still running with
  the weights unloaded, which needs llama-server's model-load API. What is built
  stops the server instead, which frees the same memory from a person's point of
  view at the cost of a cold start. The weights-unloaded variant is recorded here
  rather than claimed.

## What harness-study's assistd and sayri were worth (2026-10-04)

`harness-study/` holds 20 third-party harnesses. Reading `assistd` (Rust,
llama.cpp, Unix-socket IPC) and `sayri` (Python desktop assistant) against
Chronoa's own code, **one of the two headline ideas was already here**:

- **assistd streams TTS sentence by sentence** so playback starts before the
  model has finished. Chronoa already has this - `speech.SpeechQueue`, built per
  turn at `app/conversation.py:213` - and it is wired. Worth recording, because
  the plausible assumption is that a streaming reply needs streaming speech, and
  the sentence queue turns out to be the whole of it.

- **assistd's `QueuedTranscriber` asks "is the GPU busy" before every
  transcription**, and logs which way it went. Chronoa did not, so a transcript
  starting mid-reply competed with llama.cpp for the same accelerator. Now
  `speech_gate.py`, wired at `app/voice.py:_transcribe`: a bounded wait for the
  model to finish generating, and **transcribes anyway at the timeout** rather
  than losing what somebody said.

  Two details worth keeping: the wait is on the executor, not the GTK thread (the
  transcription is off-thread for the same reason), and `wait()` takes its clock
  as a parameter, so the tests prove the *timeout* instead of measuring how long
  four seconds is.

  `waited` means **it actually slept**, not "it was busy on the first look" -
  because assistd's `gpu_busy_timeout_ms = 0` means "ask, do not queue behind a
  stream that may never end", and counting that as a deferral would put a number
  on the log that never happened.

## Cloud voice, and the wizard's cloud branch that never asked (2026-10-06)

**There was no cloud voice at all, and "In the cloud" produced an assistant that
could not hear.** Three defects, all found by walking the real wizard and reading
what it said.

### 1. "Nothing to download" was true because the wizard had stopped asking

The Mode page's "In the cloud" row reads *"nothing to download"*. The flow it
produced was `welcome -> mode -> cloud-keys -> done`, and `on_finish` writes
`setup-complete=true` unconditionally - after which `needs_setup()` returns False
forever. So somebody who deliberately chose the cloud got a Chronoa that could
think and **could not hear**, and was never asked about it. The cloud branch now
goes `cloud-keys -> ears -> voice`, and Ears and Voice both say what they cost.

### 2. The Mode page's radio did nothing, and the Ears page charged for nothing

**The question was asked and the answer discarded.** `navigate()` took the
destination as a string and bound it into the button's handler when the page was
built - before anyone could toggle a radio on it. Measured: choose "In the cloud",
press Next, land on `brain`, the page offering a 1.1 GB local download. That is
the specific failure the page's own comment says it exists to prevent, three lines
above the wiring that caused it. `navigate()` now takes a callable, and the Mode
page re-titles the button on `toggled` so the label cannot disagree with the
destination (`tests/test_mode_choice_routes_the_flow.py`; both halves are asserted,
because a button that *goes* to Cloud keys while still reading "Next: Brain" is
the same defect wearing a different hat).

**In cloud mode the Ears page showed nothing and still charged 60 MB.** The model
list was built as `[]`, so the choice group rendered no rows - and then the same
`else` branch ran anyway and queued the recommended model's download. Measured with
`setup-mode=cloud`: Ears listed **zero rows** and the Review page read
**"Download 3 thing(s) ... 1.3 GB across 3 download(s)"**, one of which had no row
in front of it to see, choose or decline. A charge with no row in front of it is
not a choice, and that page's own subtitle promises *"Everything you picked, and
nothing you did not."* Fixed in `tests/test_cloud_branch_is_honest.py`.

**A gap that test file had, found by mutation rather than by reading it.** Three of
its four mutations were caught; `cloud-keys -> done` was **not**, because the tests
reached `ears` and `review` with `push_by_tag` - a test that navigates *around* a
route is not a test of the route. It now presses Next on the page the branch
actually lands on. This is the "negative control that cannot fail" trap in a new
place, and the fourth time in this repo that a source-level check has agreed with
code that does nothing.

### 3. `cloud_voice.py`: speech recognition and synthesis over the API

`stt.py`, `stt_parakeet.py`, `tts.py`, `sherpa.py` and `voices.py` contained
**five URLs between them and every one is a download** - a sherpa-onnx release, two
Piper releases, a Piper-voice archive. Speech itself was whisper-cli, Parakeet,
espeak-ng, Piper and sherpa-onnx, all local subprocesses; "cloud" meant
`cloud_llm.py`'s text and nothing else.

`cloud_voice.py` adds `CloudSTT` (`is_available`/`transcribe`/`transcribe_stream`,
`WhisperSTT`'s exact surface) and `CloudTTS` (`synthesize_to_bytes`/`synthesize`,
`PiperTTS`'s), so each is a *selection* rather than a branch at every call site.

**Four reasons it is not just another provider in `cloud_llm.py`:**

- **Audio is a different disclosure from text.** `redactor` keeps API keys out of
  prompts; nothing makes "the last thirty seconds of your microphone" safe. So it
  gets **two** switches, `cloud-stt-enabled` and `cloud-tts-enabled`, both default
  **false** - deliberately *not* `cloud-fallback-enabled`. A person who allowed
  prompts to leave as text has not agreed to upload a recording, and one switch
  cannot express that difference.
- **"Fallback" is the wrong word** for the primary listener on a machine with no
  local model. Hence separate names.
- **The privacy gate is per request here, and that is the structural difference.**
  `cloud_llm` enforces privacy at chain *selection*
  (`_maybe_enable_cloud_fallback`) and on toggle (`_toggle_privacy` drops
  `self.llm`); `cloud_llm` itself only *records* `egress.privacy_mode_enabled()`.
  Sound for a chat turn, which is request-scoped. **A microphone stays open for
  minutes** - dictation's ceiling is 300 s against an ordinary turn's 20 - so
  privacy mode can be turned on halfway through and the next chunk would be sent
  by a chain chosen legally before the person changed their mind.
  `_privacy_refusal()` is therefore called *inside* `transcribe()` and
  `synthesize_to_bytes()`, failing closed.
  `test_privacy_turned_on_mid_dictation_stops_the_next_chunk` builds that exact
  sequence, because a gate only ever tested in one state cannot tell "reads the
  flag every time" from "read it once at construction".
- **`egress.MODEL_HOSTS` is a model-*download* allowlist** and `cloud_llm` never
  calls `check_destination`, only `egress.record()`. Reusing it would be wrong in
  both directions, so `cloud_voice` records its own events with `purpose` set to
  `speech-recognition`/`speech-synthesis` and `bytes_out` set to the **real audio
  size** - `payload_size` is meaningless here.

**It displaces nothing local.** Cloud TTS is the *last* link of
`PiperTTS.engine()`'s cascade, after Kokoro, Piper, RHVoice and espeak-ng -
`espeak-ng` is a hard package dependency, so on any working install that branch is
never reached and turning the switch on cannot displace a local voice.
`app/voice.py:_build_stt` only reaches for the cloud when `local.is_available()`
is False, and re-decides per utterance so installing a local model later takes
precedence without anybody turning a switch back off.

**Every refusal case in `tests/test_cloud_voice.py` installs a transport that
raises if reached.** Checking `is_available() is False` proves a flag was read; it
does not prove a request was not made. The three refusals are kept apart because
they are three sentences and only one is actionable: the switch is off, privacy
mode is on *right now*, or nothing configured has the route.

### The capability table was wrong three times before it was right

**The first table was built from an empty POST, reading `400` as "the route
exists".** That is wrong, and each of the three failures looks exactly like a
working provider:

| provider | a real body says | the 400-based guess claimed |
|---|---|---|
| `kilo` | `"This endpoint only accepts the path /chat/completions"` | has a speech route |
| `blockrun` | `"Unknown speech model: tts-1. Available models: elevenlabs/..."` | right answer, wrong reason |
| `openrouter` | `ZodError ... response_format ... values: ["mp3","pcm"]` | returns WAV |

**A status code is not a capability; the server's own sentence about what is wrong
is.** `probe_capabilities()` now sends a real body and a real WAV and returns four
verdicts - `yes` / `no` / `needs-key` / `unreachable` - because "I could not ask"
and "it does not have one" are different answers.

Four findings that shape the design, all from the corrected probe:

- **No cloud speech is anonymous anywhere.** Every provider with a route answers
  401 without a key, Kilo with `"PAID_MODEL_AUTH_REQUIRED": "You need to sign in
  to use speech-to-text."` This matters because the *text* chain is deliberately
  keyless, so "speech follows suit" is a natural and wrong assumption: enabling
  `cloud-stt-enabled` with no key configured enables **nothing**.
- **The free keyless chain (`llm7, kilo, blockrun`) has exactly one speech
  provider** - BlockRun, and it names its models after ElevenLabs. So somebody with
  no API key gets a cloud *brain* and a local *voice*, and a provider whose free
  tier has run out gets silence.
- **OpenRouter cannot return WAV**, so `_to_wav` converts with the `ffmpeg` already
  used by `recordings.py` and **raises** if ffmpeg is absent - never handing back
  MP3 under a name every caller here believes means WAV.
- **BlockRun's speech endpoint bills crypto.** Measured: HTTP 402 with an x402
  challenge - `{"x402Version":2,"accepts":[{"scheme":"exact","network":
  "eip155:8453","amount":"2000"}],"error":"Payment Required","message":"This
  endpoint requires x402 payment","price":{"amount":"0.002000","currency":"USD"}}`.
  0.002 USD in USDC on Base, **per request**. Its *chat* tier is free, which is
  exactly what makes this easy to get wrong, so BlockRun is deliberately **not**
  offered for speech - a switch labelled "Cloud speech synthesis" that quietly
  bills a wallet is worse than not offering the provider. Its ElevenLabs model
  name stays in `TTS_MODELS` so the finding is recorded in the module, and
  `probe_capabilities()` reports `needs-paid` rather than losing it.

**And the corrected classifier caught its own author.** `verdict()` needed a
`needs-paid` state because its first version read BlockRun's 402 as `no` - the
same "a status code is not a capability" mistake this module documents, made one
function above where it was being described. Found by running the live probe
against the table and diffing all nine providers: it disagreed on exactly one,
and the disagreement was in the direction that would have shipped a crypto bill.
The probe and the table now agree on all nine.

**Also fixed while in there:** `_stt_backend_label()` answered from the
*configured* backend, so once `_build_stt` could return a cloud engine the startup
line named `Whisper.cpp` on a machine that never ran it. It now reads the object
(`CloudSTT.label = "Cloud"`) - the same "two log lines a millisecond apart,
disagreeing" failure this file has recorded before, reached from a new direction.

### Tests and mutations

`tests/test_cloud_voice.py` (23), `tests/test_cloud_branch_is_honest.py` (5),
`tests/test_mode_choice_routes_the_flow.py` (2),
`tests/test_welcome_rows_are_targets.py` (14). Mutations run and confirmed to
fail: privacy read once at construction (1 test), switch ignored (2), keyless
providers allowed (2), Kilo put back in the routes (2), MP3 returned as WAV (1),
empty cloud model list restored (1), cloud branch skipping Ears (1, after the gap
above was closed), ears queued anyway when cloud is on (1), honest paragraph
removed (1), welcome rows inert (11), a wrong row tag (1), `Start` renamed (1).

**The probe method was wrong before the code was.** An empty POST to Kilo returns
`400`, and so does a *valid* body to a provider that merely names its models
differently - so the first table agreed with three providers that cannot do what
it said. `tests/test_cloud_voice.py` pins the four-verdict classification
specifically so the mistake cannot come back quietly.

### The egress panel answered "what left?" with a URL and a byte count

`gui/surfaces/privacy.py`'s **Recent network activity** group rendered

    {method} {url} - {bytes_out} bytes

— accurate about bytes and **silent about the only part that matters**. An upload
of a recording therefore read as

    POST https://api.openai.com/v1/audio/transcriptions - 53312 bytes

which looks like a data POST and not like the voice of the person sitting at the
machine. The person opening that panel is asking one question, *what left?*, and
for the newest kind of egress the answer was a URL and a size.

**The byte count is the kind of number that reads as complete.** 53,312 bytes of
anything looks like a request; 53,312 bytes of 16 kHz mono PCM is fourteen
seconds of somebody speaking. The panel cannot tell the difference, because
`payload_size` is a *length* and a length does not say what was measured — only
the `purpose` does. `egress.record` has carried that field since model
downloads, and `cloud_voice` writes `speech-recognition` /
`speech-synthesis`; none of it reached the screen. Now it does, and events with
no purpose render **exactly** as before, because an addition that made old rows
unreadable would be the worse regression.

Deliberately **not** done: deriving seconds from the byte count. That needs a
sample rate and a channel count, and guessing either is the confident wrong
answer this panel exists to avoid. `tests/test_egress_panel_says_why.py`
(3 tests, mutations: purpose dropped 2, prefix applied unconditionally 1,
hardcoded prefix 3).

`gui/browser.py`'s `_record_navigation` had the same gap and now records
`purpose="page-navigation"`, so a page the person opened is legible in the same
panel as everything else. Its two existing properties are pinned alongside, both
because they are easy to break while adding a field: `privacy_mode` is still
**read at the call site** (omitted it computes `violation=False` structurally,
which is how `webtext.retrieve` was uninstrumented while looking instrumented),
and a `file://` page is still **not recorded at all** (an alarm that fires for
local files is an alarm nobody reads).

**Two test-shape mistakes here, both worth recording.** The file's first version
*skipped* when the panel build raised — a skip reads as coverage and is not, so it
now builds through the real `surface.build(app)` with a config stub and has no
skip path. And its "old rows are unchanged" assertion was a **substring** check
(`"GET ... - 12 bytes" in texts`), which a mutation that prefixed every row with
`request: ` still satisfied, because the prefix goes *before* the string being
looked for. That mutation ran green. It now asserts **equality on the egress
row**, selected by host — the panel also renders privacy mode, the switches and
every consent row, so "the rendered text contains X" was never the right shape.

### The Voice panel did not learn about its own fifth engine

Adding the link to `tts.PiperTTS._engine` is not adding it to the panel.
`gui/surfaces/voice.py`'s `_chain()` had its own four engines, so a machine
speaking through a provider would have found no row at or above its own and been
told it was using nothing — the panel-silently-incomplete defect the file's own
docstring records for the Eyes list.

The instructive part is what the tests said while it was broken. Two existing
assertions **passed anyway**:

- `test_a_probe_that_raises_is_unknown_not_absent` proves a failed probe names no
  engine, by asserting the absence of `ENGINE_NAMES` — and `ENGINE_NAMES` listed
  four, so omitting the fifth was invisible to the guard. **An omission in a
  negative-space guard cannot fail.**
- `ENGINE_NAMES` was the same four-tuple in a second place, kept in step by hand.

Both now derive from `surface.CHAIN`, and the state list is asserted as a
*property* ("exactly one engine is present, and it is the floor") rather than as
a literal — because `cloud` sits **after** `espeak-ng` and is False, so
"all False then a True" was never going to be the right shape and I wrote it that
way first.

**And the panel is read-only, which `cloud_voice` had to learn.** Asking the cloud
engine's availability by constructing a `ChronoaConfig()` instantiates a
`Gio.Settings`, which on first use creates `~/.config/glib-2.0` —
`test_building_writes_nothing_to_the_data_home` caught it, correctly. `CloudTTS`
and `CloudSTT` now take an optional `config` and `_read_switch`/`_provider_key`
use it when given. Better dependency direction anyway; the injectable half exists
because a read-only page that creates a settings directory is not read-only.

### The root-side plan validator was a non-executable file

Found by asking "which functions in this tree look like enforcement but have no
caller?" — an AST scan for `may_`/`can_`/`enforce`/`require`/`check_`/`deny`/
`refuse`/`consent`/`validate` names with no usage outside their own definition.

Three came back at zero. **Two were false positives and one was a live
shipped defect**, and the false positives taught the scan its own lesson:

- `sandbox/landlock.py:apply_filesystem_allowlist` — called from line 594, which
  is *inside the source string returned by `get_landlock_wrapper()`*. The
  executor writes that generated program into the workspace and runs it, so the
  caller is generated code, not the tree.
- `netprovision.revalidate` — called by **`usr/bin/shani-chronoa-lab-network`**,
  which my scan had not looked at because it only walked
  `usr/lib/shani-chronoa/`. *Any scan of "who calls this" in this repo has to
  include `usr/bin/`; the scripts there import the package and are the
  privileged half.*
- `cloud_llm.check_health` — genuinely uncalled, and consistently so: the Model
  panel already says *"would answer through … never asked here; availability is
  per-request"*, so nothing anywhere claims a health probe runs. A dead
  convenience method, not a defect.

**The live one.** `usr/bin/shani-chronoa-lab-network` was mode **`100644`** in git
— the only non-executable file in `usr/bin/`, where all five siblings are
`100755`. Measured on the repo copy:

| mode | result |
|---|---|
| `644` | `Permission denied`, exit 126 |
| `755` | runs, and refuses to do anything without root (exit 77) |

`skills/lab_network.py` documents the root path as
`pkexec /usr/bin/shani-chronoa-lab-network apply /path/to/plan.json`, so on any
install built from this commit the lab-network skill's root path **could not be
executed at all**.

That matters more than a dead launcher usually does, because this file is the
**only** enforcement point for a security claim.
`netprovision.revalidate()`'s docstring says a plan file *"can only ever contain
commands this module would have produced from a request it accepts — there is no
path by which a plan file becomes an arbitrary root command"*, and this helper
is what calls `revalidate()`. An unexecutable validator is the same defect as an
uncalled one, pointed the other way: code that reads as a control nothing can
reach.

The existing `test_pkgbuild_installs_every_binary_not_a_hand_kept_list` could
never have caught it — it checks that the **manifest mentions** each launcher,
never its mode. The new test asserts **git's recorded mode** (`git ls-files -s`),
not the working tree's, because the working tree's mode is what a local `chmod`
papers over while a fresh checkout still produces a 644 file. Mutation run: mode
back to `100644` in git → the test fails. It also caught the mode silently
reverting when a `git stash`/`git stash pop` round-tripped the file, which is the
second reason to read the index rather than `stat`.

### The gateway, driven from a second process — and a grant that is only a label

I had verified the gateway in-process. This drove it across **two processes on a
private `dbus-daemon`**, the app exporting and an unrelated client calling:

```
CLIENT Submit(phone,'what is the weather') -> "reply to 'what is the weather'"
CLIENT Submit(laptop,'ping')               -> "reply to 'ping'"
CLIENT Submit(nosuch,'ping')  -> <Refused: no gateway called 'nosuch'; registered: laptop, phone>
CLIENT Submit(phone,'')       -> <Refused: empty message>
CLIENT Submit(phone,'x'*99999)-> <Refused: message is 99999 characters; the limit is 4000>
CLIENT bogus method           -> <UnknownMethod>
```

and the server's submit callable reported the text it actually received —
`'what is the weather', 'ping'` — so the string crossed the bus rather than being
echoed by a stub. Introspection advertises
`dev.shani.chronoa.Gateways.Submit(ss) → (s)` correctly.

**And it exposed a claim that is false.** The second line is a channel registered
with the **default `ask` grant**, and it executed. An AST search confirms why:
`self.grant` is written once in `__init__` and read once, in `may_execute()`,
which **nothing in the tree calls**. So `ask` and `execute` behave identically
today; the grant is what the Settings row displays.

The security argument does not rest on the grant, and that is worth being precise
about: the submitted turn meets the same per-sense consent keys as anything typed
into the window, which is why an inbound message cannot delete a file by itself.
What does not exist is any *behavioural* difference between the two grants.

`Gateway.may_execute`'s docstring said the grant is "the *outer* limit". There is
no outer limit. It now says plainly that it has no caller, that the enforced
limits are `admit()`'s three plus the consent keys, and that a method named
`may_execute` with no caller reads like a security control it is not — which is
the same shape as `Registry.register()` having zero callers, the defect that
kept the whole inbound channel dead. Four tests pin the behaviour (AST search for
callers, both grants submitting, `admit`'s three limits, `describe` still showing
the grant); four mutations confirmed to fail.

### A logic-pattern scan across all 377 files: one live finding, three clean results

Structural scans were done, so this one went after **logic patterns** instead.

**`argfile._make_argfile_dir()`'s docstring said "A private 0700 directory" and
delivered the umask default.** Measured on this machine with the usual
`umask 002`: `~/.local/share/shani-chronoa/argfiles/` is **0775**, sitting beside
`egress/`, `logs/`, `sessions/`, `models/` and `percepts/`, all 0700.

Not a disclosure hole — the per-call directories come from `mkdtemp`, so they are
0700 whatever the umask, and the envelopes inside are written 0600. An
**integrity** one: a 0775 directory lets another local account unlink and replace
an entry. And the function's own docstring claimed a mode it was not delivering.

**`mkdir` then `chmod`, not `mkdir(mode=0o700)` alone**, which is the part worth
remembering and is already documented in `egress.py`: with `parents=True` the
mode argument applies to the **leaf only** — intermediate directories are created
at the umask default — and `exist_ok=True` leaves an already-created directory
exactly as it was. So a root that predates the mode argument stays 0775 forever.
The `chmod` makes the docstring true on every machine. Two mutations confirmed
to fail (chmod removed, chmod to a wrong mode).

**Three clean results worth having, so nobody re-derives them:**

- **Zero `subprocess.run` / `check_output` / `call` without a timeout.** My first
  scan said "28 subprocess calls with no timeout" and that was **wrong** — it
  matched `Thread.run` and `asyncio.run` by attribute name. The 12 real `Popen`
  calls are long-lived children (audio capture, playback, the STT server,
  wakeword, the sandbox executor) where a timeout would *kill the thing it
  started*, so none is a defect.
- **Zero `shell=True`**, so no string-built command lines to inject into. The
  sandbox executor's comment records that it used to.
- **Zero bare `except:`.**

One artifact looked like a finding and is not: `sandboxes/t/landlock_wrapper.py`
is mode **0664**, world-readable, beside 600 files. It is dated **2026-09-27** —
nine days *before* `executor.py` gained its `chmod 0o700`. Stale state from
before the fix, not a live bug.

### A Rebuild that built a model nothing could load

Wired earlier today and **measured useless on inspection**. Three separate
defects, found by asking "what happens to the file it produces?":

**1. The result was unreachable.** `current.gguf` is a **symlink**, so
`server_args` would load any file on disk - but the only function that could
point it there, `use(key)`, resolves its target through
`SPECS[key].filename`, a fixed catalogue of downloads. A re-quantization of a
model already on disk has no `SPECS` entry *by construction*: it did not come
from the catalogue. Settings -> Models -> **Rebuild** reported `Done: Q4_K_M`,
named a path in `$TMPDIR`, and discarded it. So the button built a measurably
better model (42.571 against 43.611 on the same corpus, same bytes) and left it
where the system clears it. Fixed with `adopt_path()`, which promotes **any**
file behind the same perplexity gate `use()` applies, and by making Rebuild
report the path *and* adopt it.

**2. Every run leaked ~200 MB.** `calibrated_quantize()` built its scratch with
`mkdtemp()` and **never removed it, on any of the four exit paths** - including
the two refusals that had built nothing worth keeping. Each directory held a
corpus, an `imatrix.dat` and **two** ~100 MB GGUFs. The scratch now lands under
`model_dir()` rather than `$TMPDIR`, and the two paths that built nothing reclaim
it. The one path that hands back the naive file keeps it - and names the
directory, because a caller holding a file must be able to find its home.

**3. Nothing could ever reclaim it.** `_QUANT_SCRATCH` is in memory, so it is
empty in every process except the one that made the directory - which is to say
the Settings window could never reclaim last week's leftovers. **Measured: the
first version found the directory and removed nothing.** The guard is now the
name prefix *and* the parent directory, checked inside `_remove_scratch_dir()`,
which is the only function here that deletes anything on a pattern.

Two bugs of my own, both caught by measurement rather than reading, both recorded
because the shape recurs:

- **`directory.name not in _QUANT_SCRATCH_PREFIXES` asks whether the whole name
  *equals* the prefix.** `mkdtemp` appends random characters, so every real
  scratch directory was refused: two present, zero removed, logged as "not a
  quantization scratch name". It has to be `startswith`.
- **The reclaim guard compared a file against a directory.** It protected
  `link.resolve()` - the model *file* - then compared it to each scratch
  *directory*, so it could never match and never fired. Measured: after
  adopting `chronoa-quant-a/m.gguf`, reclaim deleted `chronoa-quant-a` and left
  `current.gguf` dangling. Both the file and **its parent** must be protected.

`f"{freed / 1e6:.0f} MB"` also reported **"0 MB"** for a 4 kB reclaim - the exact
size the button exists to explain - so sizes go through `_human_bytes()`.

**32 tests, 12 mutations confirmed to fail.** Two of those mutations were
**survivors on the first run** and are worth remembering:

- The self-adopt test passed with or without the guard, because with no
  `llama-perplexity` installed `quality_verdict()` returns `measured: False`
  for *everything*. A test that passes for an unrelated reason is not a test;
  stubbing a number was what made it real.
- Only the **first** failure path had a reclaim test. Removing the reclaim from
  the second one - the expensive path, where the matrix has already been fitted
  and both ~100 MB builds exist - left all 19 green.

And the reclaim row originally had **no test at all**, proved by a mutation that
replaced its call with a hardcoded "nothing to do" and stayed green. Its handler
was a closure inside `_build_models`, unreachable without a display, so it is now
a staticmethod - the same reason `_rebuild_calibrated` already is.

**Regression caught by existing tests, not by new ones:** an edit to those two
lines dropped `directory.mkdir()`, so every `work=` run failed with "llama-quantize
could not produce". `test_the_happy_path_names_all_three_artifacts` failed before
the new tests were even run.

### The 76% failure signal about a tool that no longer exists

`unknown_tool_examples()` has been *reporting* this since before the Learning
page existed, with a docstring that says the answer is the person's:

> It is recorded rather than filtered. **Filtering would be the more useful
> behaviour and the wrong one here**... Whether to train on it is their decision,
> made with the figure in front of them.

That argument is right for a **default** and wrong as a **dead end**. So the
default is untouched - `only_tools=None` trains on everything, byte-identical to
before - and the filter now exists for when a person wants it, **next to the
figure on the same page**.

Measured on this machine's log (14,395 records), via the code's own reader:

| | |
|---|---|
| records naming tools this build lacks | **712** (4.9%), `liar` and `unver` |
| of those, failures | **`liar`: 323 of the 423 total failures - 76%** |
| examples dropped when filtered | 650 of 14,102 |

So the outcome model's most confident, most heavily-repeated lesson is about a
tool that cannot run, and its "detects failure" score is substantially a memory
of `liar`. That is not a bug in the model; it is the model being faithful to a
log full of a fixture.

**`_EXAMPLE_CACHE` was the whole ballgame.** Keyed by path and revision alone,
so the first unfiltered call populated it and a filtered caller was served the
**unfiltered** list: 4 examples either way, from the same file, with the button
appearing to work and changing nothing. A feature that does exactly what it says
and does not do it is worse than a missing one. The key is now
`(path, sorted-filter)` - sorted, so `{a, b}` and `{b, a}` share an entry rather
than parsing the whole log twice.

Three of my own test bugs, all the same shape - **asserting the implementation
instead of the behaviour**:

- Spied on `train_and_report` to prove the filter reaches the fit. It never gets
  called: `train_and_save` splits and cross-validates itself. `load` is the one
  boundary every route crosses.
- Asserted `provenance` on a return that legitimately had none, because the real
  model refused to be written on four examples. A test that needs a save to
  succeed in order to check a filter breaks for the right reason at the wrong
  time.
- Asserted the filter reached `cross_validate` as a **kwarg**. It should not -
  `load()` already filtered the list. What matters is that CV gets a *shorter
  list*, which is what is asserted now.

And `example_count` lives **inside `provenance`**, not at the top level of
`train_and_save`'s return - reading it from the wrong level reports "Trained on
None of your records".

12 tests, 3 mutations confirmed to fail.

### `refuse_catalogue` refused `/` and `~`, and nothing around them

The guard for `delete_file`, `extract_archive`, `trash_file`, `edit_file` and
`undo_last_change` tested `path == root`. Measured by relocating `$HOME` into a
temp tree and recording any `shutil.rmtree` call instead of performing it:
`delete_file` with `recursive=True` and `path=<the parent of $HOME>` called
`shutil.rmtree` on a directory **holding the user's entire home directory**, and
`refuse_catalogue` said nothing.

The check is now `root.is_relative_to(path)` - equality is the narrowest case of
"this path contains a protected root"; the parents are the rest, and deleting a
parent deletes the root inside it just as surely. Subdirectories of home, deep
descendants, and siblings of home are still allowed, and nine tests pin the
difference.

**Severity, honestly.** On a conventional multi-user Linux box, the parent of
`$HOME` is `/home`, root-owned, so the `rmtree` fails with `EACCES` and the
filesystem stops what the guard missed. The trade fails open in the guard and
closed in the permissions, so the guard is the second line. It was the *first*
line on any layout where the parent is user-writable - a single-user system
with `$HOME` directly under a user-owned directory - and it is a hole in the
function whose own docstring said its job was to close it.

Also: both concurrency candidates this scan turned up are already covered -
`planmode` reads `_enabled` and `_reason` under a lock, `ask_bridge` touches
`_presenter` only once at startup. No fix was the honest outcome there, and no
`open()` call outside a `with` block is an actual leak (each is closed by a
later `with handle:`). The HTTP layer sets a timeout on every client; zero were
missing. Positive results recorded so nobody re-derives them.

### Not wired on purpose: a quality gate for the vision and embedding models

`local_llm` gained a perplexity gate; `local_vision` and `local_embed` have
none, and **copying it across would be theatre**:

- **The embedding model cannot be measured by perplexity at all.** It runs with
  `--embedding --pooling mean`, producing vectors rather than tokens, and
  `llama-perplexity` has **zero** `--embedding` or pooling flags (measured:
  `llama-perplexity --help | grep -c` returns 0). There is nothing to gate on, so
  a "quality gate" here would be a check that cannot fail.
- **The vision model's language backbone could be measured** - `llama-perplexity`
  does accept `--mmproj` - but that scores how well it predicts *text*, which is
  not the thing the vision model is for. It would be reported as if it were.

Both stay digest-verified and unmeasured, which is what they honestly are. If
this is revisited the measurement to reach for is retrieval quality against a
labelled set, not perplexity.

### The full inventory: what is dead, what is deliberate, and what is a lie

An AST scan of **all 1,025 public functions and classes** in the package found
**57 never called anywhere**. The 379 "called only inside their own module" are
ordinary internal helpers and were not treated as findings. Triaged in full so
nobody re-derives this:

**Wired this session (15).** `merge_models`, `lessons`, `render_lessons`,
`experience_summary`, `evaluate_bandit`, `organ_status`, `adopt_retired`,
`import_knowledge`, `probe_capabilities`, `perplexity`, `quality_verdict`,
`calibrated_quantize`, `audio_status`, `seccomp.is_active`, and
`permissions.cancel_requested` (which had a caller added rather than a caller
found).

**Deliberate, and the reason is recorded in the code.**
`retire_model` is a no-op on purpose — a rename is the only genuinely fragile
operation in that layer and the space check on load already refuses a stale
model. `summarize_tool_results` / `summarize_history` take an opt-in
`summarizer` that `assistant.py:185` passes none of, so compression is
elision-only by choice, not by omission. `open_settings_button` /
`wire_page_button` are **speculative API, not duplication**: the four call sites
that navigate to Settings use `banner()`'s own button, not a suffix button, so
the helper has no user. `bayes`, `timeseries`, `clustering`, `classification`,
`anomaly` are a statistics toolbox. `Point`, `Cert`, `Busy` are dataclasses.
`prctl_set_seccomp_attempt` is the legacy path kept on purpose.

**False positives a `usr/lib`-only scan produces, and the two that bit me.**
`apply_filesystem_allowlist` is called from *generated code* inside
`get_landlock_wrapper()`. `revalidate` and `read_envelope` are called by
`usr/bin/shani-chronoa-lab-network` — **any scan of "who calls this" in this
repo has to include `usr/bin/`**, because that is where the privileged half
lives. `check_destination` appeared in the discarded-result scan and is
correct: it returns `None` and raises on refusal, so `_guard()` fails closed.

**Still unwired, genuinely candidate.** `run_from_file`, `webkit_version`,
`known_organ`, `page_titles`, `pending_count`, `price`,
`collapse`, `capability_for`, `summarize_*` siblings, `from_url`,
`save_record`, `lab_network_namespace`,
`describe_captured`, `tempo_map` / `apply_contour` /
`within`.

**Re-measured 2026-10-11, and two entries had gone stale in the dangerous
direction.** `install_hint` (`routes.py`) and `remember_from_turn`
(`senses/memory.py`) now each have a non-test caller; the other sixteen still
have none. A stale "dead" label is the direction that destroys work, so the
list is corrected rather than left to be trusted — but note it still rots, and
the scan has to be re-run rather than believed.

**`encode_rejection` is unwired on purpose, and that was worth proving rather
than assuming.** It has no production caller, which reads like the dead-code
class above. It is not: `permissions.parse_reply` splits the reason off a
denial, `decide()` files it in `_reasons`, and the *next* attempt is answered
by `permits()` with it attached. Measured through a real `delete_file` dispatch
with a presenter built the way a real one is (`ask_bridge.make_event()`):

    1st refusal : 'The user did not allow delete_file. Nothing was done.'
    2nd refusal : '... says deny_session. The user said: never in /home,
                   use the trash Do something else instead, or ask ...'

The terse first refusal is the design — the question is not put twice — and the
reason reaches the model where it can act on it. The shipped `notify-send`
presenter returns `"allow"`/`"deny"` action keys and structurally cannot collect
a reason, so the encoder has no caller by construction. **My first probe drove
the presenter with a bare string, `ask_bridge` rejected it ("returned str, not
an event"), and the reason came back absent — a confident wrong answer produced
entirely by the probe.** `tests/test_approval_ux.py::test_the_reason_reaches_the_model_on_the_refusal`
already covered this; the lesson is that I should have run the existing test
before building a probe of my own.


**All 154 schema keys are read** — there is no unwired setting in this
repository. Worth recording as a clean result.

### Re-run of the same scan: eight names wired, and five were live defects

The scan above re-run (a token-count pass over `usr/lib`, `usr/bin`, `tests/`
and the root scripts, excluding each definition's own line) found 49. Eleven
were wired; **five of those eleven were not missing features but shipped
defects**, which is the argument for running the scan again rather than
trusting the list above. Each was found by running the real thing, not by
reading the caller.

**`--show-page=` did not work, for five of six destinations.** `pages.py`'s
own docstring gives `--show-page=settings:privacy` as the example. Measured on a
real app: `main:conversation`, `settings:senses`, `settings:privacy` all
returned **False**, and `setup:review` logged *"setup cannot show a page"*. Four
separate causes, none of them visible in the registry:

- **The registry was only populated by building the window.** `register()` ran
  inside `SettingsWindow.__init__` and inside `build_window`, so `show()`'s
  `window_id not in _PAGES` check fired *before* the factory that could have
  built the window was ever consulted. The factory was unreachable code. Both
  now register at import time, and `pages._DECLARERS` imports the declaring
  module on demand so an id is answerable before anything has been built.
- **`senses` and `privacy` matched no group at all.** The seven ids the registry
  promises are **sections**; the window's groups are 25 and are titled after
  their own content ("Getting started", "Privacy and network"). Measured on a
  real window: declared-but-not-built was exactly `['privacy', 'senses']`. So
  the search-based `show_section` could never land on them — while a window with
  fourteen sense rows sat there. `group._section_family` is now set by each
  builder and `show_section` reveals a family when the id names one.
- **The wizard's `show_page` was a closure held only by the local scope that
  built it.** `pages.show()` looks for `window.show_page`; there was no
  attribute to find, and the wizard logged "cannot show a page" on a wizard with
  the Review page fully built. It is attached now.
- **`forget_window` had no caller**, so `_WINDOWS` grew forever and a
  destroyed window stayed addressable. Now dropped on `destroy` — not on
  `close`, because closing a `Gtk.Window` can merely hide it and forgetting it
  then would make `show()` build a second copy of a live window.
- **Clearing the search box did not restore the window.** `_on_search` with an
  empty needle fell through to "no needle, so everything matches" only by
  accident of the `not needle or` clause; after `show_section` had set
  visibility directly, clearing the box left the person with a fraction of the
  window. Measured: `25 → 14 (senses) → 3 ("camera") → 25` after the fix, and it
  stopped at 14 before it.

The three lookup functions (`page_ids`, `resolve`, `page_titles`, `windows`)
also load their declarer first, so "does this id exist" cannot depend on which
module happened to be imported already. `_DECLARERS` maps to a **callable**, not
a module: importing once is not enough, because `tests/test_pages.py` resets the
registry and would otherwise leave every window permanently undeclared.

**Four more, each measured before and after:**

| helper | what it was doing | what was wrong |
|---|---|---|
| `ChronoaWindow.clear_input` | unused | Ctrl+N emptied the transcript and left a half-typed question in the composer, so the next Enter asked about the *previous* conversation |
| `capabilities.help_prompt` | unused | `/help what can you do?` opened the window and **threw the question away**; the command now fills the composer, and `set_input_text` is a separate method from `submit_text` because "show this" and "send this" are different acts |
| `permissions.allows_prompting` | unused | The Approvals page read only `can_ask()`, so `DONT_ASK` **with a listener present** said "Chronoa will ask before running a gated action" — a policy `decide()` refuses on the spot, on the one page whose subject is what the app enforces |
| `is_parakeet_provisioned` | unused | The Models panel listed only `stt_provision.MODELS`, so a machine with a Parakeet model installed showed **every speech card** as "not downloaded" — the confident-wrong-answer shape this file already records for the Eyes list, reached from the other direction |

Plus `provenance.describe_boundary()` into `SYSTEM_PROMPT`: every tool result
has been fenced as untrusted data since provenance landed, and the model was
never told what the markers meant. **A fence the model cannot interpret is
decoration.** And `organism.Organ.is_live` into the Inventory panel's per-row
tooltip, so "a light means something is happening" and "this row has no light"
are reconciled on the same screen rather than only in the summary group.

`voices_for("en")` now answers the wizard's question instead of the wizard
filtering `VOICES` itself — which voices may be offered is a fact about the
catalogue, and one list re-deriving it is how a catalogue change would show up
in one place and not the other.

**Mutation-verified, all failing when reverted:** wizard `show_page` detached
(1), `senses` family removed (1), search-clear restore removed (1), family
filter widened to everything (1), composer clear removed (1), `/help` argument
discarded (1), approvals mode check falling back to `can_ask()` (1), Parakeet
cards removed (1), boundary line removed from the prompt (1).

**`tests/test_pages.py::test_all_three_windows_answer_to_show_page` was
green while two of the three windows were unaddressable**, because it grepped
the source for `def show_page`. Rewritten against the real registry and a real
wizard, plus `test_every_declared_settings_id_shows_something`, which builds the
window and asserts each declared id reveals *some but not all* groups — an id
that hides nothing is as wrong as one that shows nothing. A negative-space guard
that cannot fail is the same failure as a test that always passes.

### Two more that existed, answered nothing, and were exactly what a panel is for

A second scan — not "never called" this time, but **query-shaped functions whose
return value is discarded at a call site** — turned up two, and both are the
Diagnostics panel's job:

- **`audio.audio_status()`** — zero callers, and its own docstring says why that
  is a loss: it returns the statuses *"rather than a boolean for the reason
  `check_heartbeats` documents — a caller has to be able to tell **'not started
  yet' from 'hung' from 'gone', because only one of those three is worth waiting
  for**."* A boolean cannot carry that, and Diagnostics is where somebody looks
  when audio is not working.
- **`sandbox.seccomp.is_active()`** — zero callers, and its docstring is careful
  about scope: the filter is installed in the **sandboxed child**, so this
  long-lived process reads `False` while every skill call is still filtered. The
  row keeps that honesty: `False` reads **"could not determine"**, never
  "working", because a row that said *"no seccomp"* would be a lie.

**`STATUS_WORDS` is a closed vocabulary of three and a row may not use a fourth**,
so a gone audio child is `STATUS_NOT_WORKING` with the three states named in the
detail — my first draft invented a fourth word, which would have broken the
rule the module states in its own constants.

**Two mutations, and one of them was my fault twice.** Collapsing the three audio
states into one boolean left the suite green on the first attempt, because I put
the mutation **before** the loop that classifies them — a no-op dressed up as a
test. Redone in the right place: caught. That is the second time this session a
`str.replace` "mutation" silently did nothing; every mutation now asserts
`s.count(old) == 1` **and** is placed where it actually changes behaviour.

`check_destination` also showed up in the discarded-result scan and is a **false
positive**: it returns `None` and raises on refusal, so discarding the value is
correct and `_guard()` fails closed.

### The model actions were reachable only as a side effect of changing models

`local_llm.perplexity()` and `quality_verdict()` run `llama-perplexity` — which
is installed on this machine and was **never invoked anywhere in this
repository** — but were reachable **only through `use()`**, i.e. only as a side
effect of switching which model is loaded. There was no way to ask "is this model
any good?" without changing something. `calibrated_quantize()` had no caller at
all.

Two buttons on the Models page, both off the main thread (perplexity takes
seconds, calibration takes minutes), both re-enabling themselves so one run does
not end the feature for the session:

- **Measure** — perplexity of the model in use against another installed here,
  through the same gate `use()` applies. An unmeasurable result reads
  *"Not measurable"*, never a number.
- **Rebuild** — re-quantizes an **F16/BF16** source with a calibrated
  importance matrix. It refuses when there is no high-precision source, because
  **re-quantizing an already-quantized file is meaningless and the error is not
  obvious** — the output is simply worse than the input while claiming to be an
  improvement.

Measured on this box, both report honestly rather than inventing: *"No model is
installed yet"*, *"No F16/BF16 model to re-quantize"*.

One test passed for the wrong reason first: it created a model file but no
`current.gguf`, so the handler answered *"No model is installed yet"* and never
reached the verdict under test.

### Which providers can do speech — the table was real and unreachable

`cloud_voice.probe_capabilities()` was written and measured live on 2026-10-06,
then left with **zero callers**. So the capability table lived inside a
maintenance function no person could reach, while the two switches above it in
Settings said only *"needs an API key"* and stopped.

It is a **button and not a row**, because it makes real requests to up to five
providers with a 30 s timeout each, and a probe that can take two minutes cannot
run on the thread that draws the window.

**My first renderer invented keys of its own** — `stt_yes`, `tts_yes`,
`needs_key` — and would have printed *"none"* for every provider while looking
like a measurement. The real shape is `{pid: {"base_url", "stt": {...},
"tts": {...}}}` with `verdict` inside each, and the verdicts are **hyphenated**:
`yes`, `no`, `needs-key`, `needs-paid`, `unreachable`. Read correctly it
reproduces the measurement exactly: speech in from *groq, kilo, llm7, openai,
openrouter*; speech out from *groq, openai, openrouter* only; **blockrun paid**;
**anthropic with no route at all**.

All five verdicts are kept apart, and two of them are the ones that matter:
a route answering **402 for money** is not one that works, and **"could not be
asked"** is not **"has no route"** — the first is a network problem, the second a
fact about the provider. Collapsing them is what made the table wrong three times
before it was right.

Nine tests, four mutations confirmed to fail (hyphenated verdicts unrecognised,
the paid route folded into "works", unreachable folded into "no route", and the
probe moved onto the main loop).

### The last of the orphaned fifteen, wired — and one that misdescribed itself

The AST scan found **15 public names in `learning.py` with no caller anywhere in
`usr/` or `tests/`**. Most are a statistics toolbox (`bayes`, `timeseries`,
`clustering`) that is legitimately library surface. Nine have now been wired;
these are the last three, and each was **fully written** rather than stubbed.

| function | what the panel now says |
|---|---|
| `evaluate_bandit` | **"3 tool(s) scored; top-5 precision 33%; but it does NOT rank them better than chance yet (rank correlation -0.5)"** |
| `experience_summary` | *"1244 scored call(s) across 4 tool(s) on this machine"* |
| `organ_status` | *"none - no tool has been scored enough to carry a learned weight"* |
| `adopt_retired` | a **Restore a retired model** button |

**`evaluate_bandit` is the one that mattered.** It measures whether the bandit is
*learning*, and it had never been called — so "the bandit works" was an
assumption, and three thin estimates were one panel away from reading as a
ranking. On this machine the honest answer is negative, and the row says so
rather than showing the arms.

**`organ_status`'s docstring claimed the Inventory panel showed its numbers. It
does not** — `inventory.py` renders `organism.INVENTORY`, a static table of which
organs are *built*, and never mentions tools, trust or doubt (verified: none of
the four tokens appear in that file). So the function was orphaned *and* its claim
was wrong, in both directions. Its docstring is corrected and the row is here.

**`adopt_retired` reports its own absence.** Retirement was removed on purpose — a
rename is the only genuinely fragile operation in this layer, and the space check
on load already refuses a stale model without anyone moving anything — so this
fires only on a machine that already has a set-aside model from an older version.
Saying "restored", or staying silent, would both be wrong; it says nothing was
set aside.

Five mutations, all confirmed to fail, including the bandit's negative verdict
being dropped and the absence reported as success.

Suites: **174 passed**.

### Calibrated quantization — and a flag-order bug that failed silently

The "modify an open model with its own weights" path. It **does not train** — no
parameter is updated from a gradient — but it re-quantizes at a chosen precision
guided by an importance matrix, so the precision budget goes where activations
say it matters. That is the whole of what is available without CUDA or torch.

`calibration_corpus()` builds the imatrix corpus from **Chronoa's own source**,
not filler: llama.cpp's guidance is calibration data "derived by running a model
over a representative text corpus", and the representative corpus for this system
is the tool schemas, skill docstrings and settings copy it is asked to reason
about.

**The bug, found by running it rather than reading it.** My first `quantize()`
appended `--imatrix` after the model paths. The installed tool's usage is

```
llama-quantize [--imatrix file] model-f32.gguf [model-quant.gguf] type [nthreads]
```

and that trailing `[nthreads]` is **positional**, so the flag is parsed as a
thread count:

```
main: invalid nthread '--imatrix' (stoi)
```

That is the verbatim output on this box. It costs the entire calibration while
reporting only that — which is why the flag now precedes the positionals, and why
there is a test asserting the order rather than a comment.

`calibrated_quantize()` **refuses rather than pretending**: with no
`llama-imatrix` it returns `calibrated: False` and names the uncalibrated file as
`uncalibrated_fallback`. Handing that back under `calibrated: True` would be worse
than failing, because the caller could not tell them apart — and the entire point
of an importance matrix is that it is not the same file.

Nine tests, three mutations confirmed to fail (flag order moved back, the naive
file relabelled as calibrated, a timeout turned into a pass).

Measured on the real model, base `SmolLM2-135M-Instruct-F16` (259 MB) from
`unsloth/SmolLM2-135M-Instruct-GGUF`: naive `Q4_K_M` in **751 ms**, calibrated
`Q4_K_M` in **10.9 s** with the 631 KB matrix.

### A model was promoted on a matching digest and nothing else

`provision()` verifies a **digest**. Nothing verified the *artifact*: a
correctly-hashed bad quant passed every check there was. Meanwhile
`llama-perplexity` is installed on this machine and was **never invoked anywhere in
this repository** — `local_llm.py`, `local_vision.py` and `local_embed.py` all
download *pre-quantized* GGUFs and point `current.gguf` at one.

Perplexity is the right measure because that is how llama.cpp documents
quantization loss: the upstream `tools/quantize/README.md` measures it in
"ppl and/or KLD", and `llama-imatrix` exists because the same calibration data
improves the quant. So `local_llm.perplexity()` runs the installed tool over a
**fixed** corpus — fixed so two models are scored on identical bytes, and
written into the module so it cannot go missing from a package and silently turn
the gate into a no-op.

Measured on the two models already on this box:

| model | perplexity |
|---|---|
| `SmolLM2-135M-Instruct-Q4_K_M` | **1.805** |
| `Qwen3-0.6B-Q8_0` | **1.373** |

and promoting the first for the second is **refused**: *31.5% worse, past the 2%
limit*. Five seconds per model.

**`None` means unknown, never "fine"** — no binary, a timeout, unparseable output
and a missing file all read as `None`, because a probe that could not read
anything must not report a pass. And an **unmeasured** model is still promoted,
with the reason recorded: a minimal install without the tool should not be unable
to pick a model at all. What is refused is the *claim*, not the choice.
`use(key, gate=False)` is the explicit escape hatch.

**My first parser matched nothing.** The installed tool prints
`Final estimate: PPL = 1.0128 +/- 0.00145`; my patterns were `perplexity = ...`
and a case-sensitive `\bppl`, so **every** measurement came back `None` on a box
where the tool runs in four seconds. Verified against the real binary before
writing the test, so the test asserts the real format.

Thirteen tests. Five mutations; **one equivalent** (breaking the first of three
parsing patterns leaves the two fallbacks matching, which is defence in depth by
design) and the real break — all patterns removed — is caught by three tests. One
assertion was too weak: the refusal quotes a percentage *derived* from the two
numbers, so dropping the raw figures still left "31.5% worse" in the sentence. It
asserts **both** absolute numbers now.

`local_llm` / `modelfit` / `surface_model` / `vision` suites: **153 passed, 2
skipped**, and the single failure is the recorded `grim` one — "no Wayland screen
capture tool is installed".

### What the log taught, once something showed it — and the "cannot liar" sentence

`learning.lessons()` and `render_lessons()` were fully written with **zero
callers**, so a machine that had logged 14,000 tool calls could not be told what
any of it meant. Wired, and the first thing it showed was nonsense:

```
cannot  liar  failed 323/323 here and has never succeeded on this machine
```

`liar` is not a skill. Neither is `unver`. **712 records** name those two — four
and five characters, always `args: {}`, always in pairs ~80 ms apart, all
`origin: user` — and the count matches `provenance.examples_for_unknown_tools`
exactly, so the training path already knew.

**This is not a new finding about the log.** `unknown_tool_examples()` has
documented those two by name for a while, including that they are "**313 of the
378 failures**" — so a model fitted on this log learns its entire failure signal
from something no longer installed. What was missing is anywhere a *person* would
see it, and the lessons row is the first such place. Worth being explicit,
because I nearly wrote it up as a discovery.

The renderer now names those apart from real findings, so `get_clipboard failed
5/5` keeps its authority and `liar` stops borrowing it.

**My first fix was worse than the bug.** I resolved names with
`skills.discover_skills()`, which returns 152 names that do **not** correspond to
the log's `tool_name` values — so it immediately reclassified `delete_file
succeeded 820 of 915` and `get_clipboard failed 5/5` as "tools this build does
not have". There is one registry and it is `tools.TOOLS`, the same one
`unknown_tool_examples()` uses. There is also a test for exactly that, because
the second attempt is the one that would have stuck.

### Merging, and the two features beside it that nothing could reach

`merge_models` now has a **Merge models** button, and the group explains that the
result is scored before it is written. `_merge_verdict()` reports the numbers
including the argmax, so a merge that detects `failed` at 25× while losing the
argmax says both.

Also removed a **dead duplicate** `outcome_button` assignment that passed its
handler as `_run_async(b, _train_outcome, status)` while every live button used
`_handler(b, status)` — it was overwritten immediately, and had it ever run it
would have raised on the arity.

And the Train group's subtitle repeated the false claim this commit removes: it
said an outcome model that does not beat the majority baseline "is not written".
The test is not that, and the group now says which test it is.

**Four mutations, and the first attempt at one of them was not caught** — the
lessons test called `_lessons_sentence()` directly, so deleting the *row* from
`build()` left it green. A helper can be correct and still be unreachable, which
is the entire subject of this batch, so the row's title is now asserted through
the built widget. Redone: caught.

Learning/distill/surface suites: **149 passed**.

### A merge was marked trustworthy by construction — the worst thing I found today

`merge_models()` wrote `"honest": True` into the payload it signed and **never
evaluated the weights it had just combined**. Every writeup on TIES/DARE/SLERP
says the same thing about merging: the result is a *hypothesis* and you evaluate
it like one. Sign election makes conflicts cancel; nothing guarantees the outcome
beats the models that went into it, and on independently fitted models — which is
what these are — there is no shared base to make it likely. That is precisely why
the module's own docstring rejects addition and plain averaging.

It was not a stylistic shortcut. **`tools._outcome_model()` refuses any model
whose `provenance.honest` is false**, so a merge that was pure noise was stamped
trustworthy *by construction* and then loaded.

Now the merged weights are scored against this machine's own log, the provenance
carries the measured `accuracy`/`baseline`/`beats_baseline`/`detected`/both
lifts, and **a merge that fails the same bar a fitted model must pass is not
written at all**:

```
three noise models -> merged=False
  reason: the merged model is not worth quoting (0.0% against a 91.2% constant,
          and no minority verdict is detected), so it was not written
```

Two genuine merges, measured on the real 16 MB log:

| | accuracy | vs constant | detected |
|---|---|---|---|
| model A (half the log) | 0.857 | 0.913 | `verified` 17.5× / 5.6× |
| model B (the other half) | 0.858 | 0.910 | `verified` 16.8× / 5.6× |
| **TIES merge of A+B** | **0.880** | 0.912 | **`failed` 25.4×** |

So the merge **beat both parents** (0.880 > 0.858) and found a class neither
parent did — and now that is a measurement rather than an assumption. It also
**fails closed**: with nothing to measure against, it refuses rather than writing
an unmeasured model.

**One equivalent mutant, kept and documented.** Replacing
`"honest": bool(report.honest())` with `"honest": True` leaves the file green —
necessarily, because the gate above it only lets a file be written when
`honest()` is already true, so the expression is redundant *given the gate*.
Deleting the gate (mutation 2) **is** caught, which is what proves the gate is
load-bearing rather than decorative.

**A fixture that made a test unfalsifiable, twice.** The first version of the
"the refusal quotes its numbers" test passed against a mutation that hardcoded
`"0.0%"`, because the synthetic held-out data had **no `failed` examples at
all** — so a noise merge predicted the absent class and scored exactly 0.0%,
which is the string the mutation hardcoded. Right answer, unfalsifiable test. Two
fixes: all three verdicts are now present, and the **baseline** is the
load-bearing assertion (0.0% accuracy against a **50.0%** constant), because
accuracy alone on this fixture can coincide with a plausible constant.

### The learning layer: the export dropped the one thing it exists to carry

Found by AST-scanning `learning.py` for public names with **no caller anywhere in
`usr/` or `tests/`** — 15 of 111. Most are a statistics toolbox (`bayes`,
`timeseries`, `clustering`) that is legitimately library surface. Three were not,
and all three are the portability feature.

**`bundle["bandit"]` was always empty, on every machine, ever.**
`export_knowledge()` read the arms with `Bandit().arms()`, and `Bandit.__init__`
does not read the file — a fresh instance has an empty `_arms`. `load_bandit()`
is what loads. Measured, with a bandit holding real history written to disk
(`say` 5/5, `espeak` 5/0), no fitted model and no conversation log:

```
on disk, load_bandit() reads: {'say': (5, 5.0), 'espeak': (5, 0.0)}
a fresh Bandit().arms()    : {}
export_knowledge(...)      -> None
```

So the Export button said *"there is no trained model on this machine yet"* while
the thing it wanted sat on disk. Both halves of the feature's docstring describe
the arms travelling — "the knowledge a fresh machine cannot get any other way" —
and `import_knowledge` promises to adopt them "even when the model is not".
Neither had ever happened. Now `load_bandit()`, and the bundle carries
`{'say': {'pulls': 5, 'wins': 5.0}, 'espeak': {'pulls': 5, 'wins': 0.0}}` with
`import_knowledge` reporting `"arms": 2`.

### …and "Written to `<path>`" on a model that lost to a constant

Training this machine's real 16 MB log (14,094 examples, **24 s** on CPU) gives
`accuracy 0.857` against `baseline 0.912` — *worse than always answering
"unverified"* — and `_train_outcome` reported **"Written to …"** and stopped. A
person reading that concludes the model is good, and then `recommend()` acts on
it.

The trap here is that **two flags answer two different questions** and
conflating them hides the finding either way:

- `beats_baseline` — "does it win the argmax a constant already wins 91% of the
  time". Here **no**.
- `Report.honest()` — "is it worth quoting". It accepts *either* a top-1 win
  *or* a minority verdict genuinely `detected()` at ≥2× its base rate on **both**
  recall and precision. Here **yes**: `verified` at **17.1× recall, 5.6×
  precision**.

So the model is legitimately loaded — `tools._outcome_model()` refuses only on
`honest` being false — and the sentence has to say **both**. `_verdict_sentence()`
does, and `learning.py`'s "saved whether or not it beat the baseline" stays: its
docstring is explicit that the gate exists "to stop the *caller* from relying on
a useless model", and **the caller was the thing failing**.

The surface's own docstring claimed "writes a file only when the fit beat the
majority baseline" — false, and contradicted by `learning.py`'s deliberate
design. `_nothing_to_export_sentence()` and `_verdict_sentence()` now carry the
truth; `_import_sentence()` reports a refusal as a refusal (arms adopted, model
refused, *which was which*).

**The Import button, which the module docstring described and did not have.**
"Export and import are here because the models are portable" — beside an Export
button. `import_knowledge` was fully implemented, digest-checking and refusing,
with **zero callers**. It has a button now, and `_import_sentence()` says what
actually happened rather than "imported".

Six tests, six mutations confirmed to fail. **One of those six needed a second
attempt**: my first mutation used 8 spaces of indentation against a 4-space line,
so `str.replace` was a silent no-op and the suite stayed green — which is the
"a negative control that cannot fail is not a control" trap, hit from a new
direction. Every mutation after that asserts `s.count(old) == 1` before writing.

Related suites: **151 passed, 2 skipped**.

### The grant is now enforced, and the whole thing hinged on a thread

I called wiring it "a feature question, not an audit finding" and left it alone.
Asked for a decision that is secure *and* useful long-term, I took the third
option rather than the second: `ask` now goes through `permissions.decide()`,
the layer that already exists and already fails closed.

- **Reused, not reinvented.** `permissions.decide()` returns `None` for a
  refusal, a dismissal, a timeout and a headless run alike, records the refusal
  so the same question is not put again on a retry, and gives the user "yes,
  this once" and "yes, for this session" for free. The session grant is keyed on
  the **channel**, so answering once for `phone` does not answer for `laptop`.
- **The action is its own** - `submit_from_gateway`, not one of the existing
  actions - so a rule written for an inbound gateway cannot accidentally widen
  something else.
- **Fails closed on every path.** Verified across two processes on a private bus:

  | what happened | `phone` (`ask`) | `desk` (`execute`) | server received |
  |---|---|---|---|
  | approved | submitted | submitted | both |
  | refused | **refused**, switch named | submitted | desk only |
  | dismissed | **refused**, switch named | submitted | desk only |
  | nobody there | **refused**, *"nothing is on screen"* | submitted | desk only |

### …and then the thing I built it on turned out to have a no-op in it

Wiring the grant to `permissions.decide()` means the gateway now inherits
whatever `decide()` actually guarantees — so I measured that rather than reading
it, and **"Allow for this session" asked again on the very next call.**

`decide()` had a short-circuit for an answer already on record, and the set it
checked was only the three **refusals**. A session *grant* was written to
`_grants` and then never read. Measured, with a presenter that always picks the
session option: **three calls, three prompts**, two of them on the same channel:

```
1st='allow_session' 2nd='allow_session' other-channel='allow_session'
times the person was asked: 3   (3 calls, 2 on 'phone')
rules recorded: [('submit_from_gateway', 'phone', 'allow_session'),
                 ('submit_from_gateway', 'phone', 'allow_session'),
                 ('submit_from_gateway', 'laptop', 'allow_session')]
```

So the option was offered, recorded, and did nothing — a label that lies. Same
shape as the grant itself being a label, one level down, and it is **not the
gateway's bug**: `decide()` is shared by every tool, so the fix belongs there.

`ALLOW_SESSION` is now honoured. `ALLOW_ONCE` is deliberately still asked every
time, because "this once" means once — and that contrast is the test that stops
this being over-fixed into "the prompt never appears". Measured after:

| the person picks | prompts for 3 calls | returns |
|---|---|---|
| Allow for this session | **1** | `allow_session` ×3 |
| Allow this once | **3** | `allow_once` ×3 |
| No, don't allow | **1** | `None` ×3 |

**The second defect was in the same three lines of output above**: `_grants` held
the *same rule twice* for two calls on one channel. `add_rule` appended
unconditionally, so a channel with a session grant grew `_grants` by one entry
per approved message for the whole session — and `evaluate()` walks the whole
list on *every* permission check, so the cost is paid by checks that did not
record it. Identical rules are now not re-appended; replacing rather than
appending is a no-op for "last match wins", since an identical triple contributes
the same answer wherever it sits.

Six tests, five mutations confirmed to fail (including the over-fix, and one
where the dedupe reorders instead of skipping). Blast radius: **250 passed**
across every consent, permission, tool-activity, approval and gateway suite; the
only 2 failures are the recorded environmental `grim` ones.

**A harness note, because I made this mistake twice in one afternoon:** calling
`permissions.decide()` from a GLib timeout callback — i.e. on the main loop —
hangs for `DECISION_TIMEOUT_SECONDS`, which is **120**, not the 60 one assumes. My
first probe did exactly that and looked like a permissions bug. Ask from a
worker, which is what the gateway now does.

**Why I rejected "refuse `ask` at the bus"**, which was the smaller change: it is
secure and useless. The default grant would do nothing until somebody edited a
setting, so the feature would ship dark. This way the default works - one click,
then not again for the rest of the session.

**And why that needed a worker thread, which is the part worth remembering.**
A GDBus method handler runs *on* the main loop, and `decide()` blocks on a
`threading.Event` that only the main loop can set. So an approval asked from the
handler **cannot be answered** - it waits out its full timeout every time, which
would have looked exactly like the gate working. Measured with a presenter that
resolves from an idle callback: **3.0 s and no answer on the main loop thread,
0.0 s on a worker.** `export()` now dispatches through `off_the_main_loop()`,
which is a named function so the property can be asserted directly rather than
only observed through a live bus. The rate limit and the submit callable no
longer run on the main loop either, which was blocking the whole UI on a slow
call.

**Four pre-existing tests failed when the gate landed, and all four were right
to fail.** `test_submitted_text_reaches_the_window_entry`, the length limit, the
rate limit and the bus round-trip all submitted through an `ask` channel with
nobody present. They are tests about the bus and the limits, not about approval,
so they now use `execute` - with a note saying why. A gate that changes what
other tests can be about is worth noticing; the fix was not to weaken the gate.

Mutations run and confirmed to fail: the gate removed (5), `None` treated as yes
(2), the headless check removed (1), `execute` channels asked as well (2),
dispatch put back on the calling thread (1), the question no longer showing the
message (1).

**Three harness traps, each of which cost a wrong intermediate conclusion:**

- `Gio.bus_get_sync` resolves `DBUS_SESSION_BUS_ADDRESS` on the **first**
  session-bus connection and caches that connection for the process. Setting it
  after `gi` is imported does nothing — my first server exported on the *real*
  session bus (unique name `:1.10577`) while the client sat on the private one,
  and the client got `ServiceUnknown`.
- `Gio.TestDBus` is constructed as `Gio.TestDBus.new(flags)`, not
  `Gio.TestDBus(flags)` — the latter is `TypeError: GObject.__init__() takes
  exactly 0 arguments`.
- **A bus server with no GLib main loop answers nothing.** My first probe
  `time.sleep`ed instead of running a `MainLoop`, so every `Submit` timed out and
  the handler never ran — which read exactly like a broken export. It was the
  probe. (`Gio.bus_get_sync` was called before the loop existed, which is fine;
  dispatch needs the loop, not the connection.)

### The cloud-speech privacy story, verified by running it end to end

`cloud_voice` has a `_transport` hook, so the whole path can be driven for real
with `httpx.MockTransport` — real `_post`, real `_record`, real httpx, only the
wire faked. Measured with a hand-built 16 kHz mono WAV (32044 B) and a real
provider-shaped reply:

| | privacy off | privacy on |
|---|---|---|
| `CloudTTS.synthesize_to_bytes` | 32044 B of real RIFF WAV back | `CloudVoiceRefused` |
| `CloudSTT.transcribe` | the provider's text back | `CloudVoiceRefused` |
| ledger rows added | 2, both with a `purpose` | **0** |
| wire hits | 2 | **0** |

The two directions record **different** things, correctly:

```
purpose='speech-synthesis'   component='cloud_tts:openai'  bytes_out=11
purpose='speech-recognition' component='cloud_stt:openai'  bytes_out=32044
```

Synthesis records the 11 bytes of **reply text**, because that is what left;
recognition records the 32044 bytes of **audio**, because that is what left.
Answering "how much of my voice left this machine" means reading `bytes_out` on
a `speech-recognition` row — which is exactly what the egress panel's `purpose`
column now makes possible.

**A harness trap worth knowing, because it nearly had me reading a real user's
data.** `egress.EGRESS_LOG` is a module-level constant built from `~/.local/share`
and is **not** where a redirected run writes. `record()` resolves the path per
call through `egress._egress_log()`, which honours `XDG_DATA_HOME` and gives a
pinned `EGRESS_LOG` priority. So a probe that sets `XDG_DATA_HOME` and then reads
`egress.EGRESS_LOG` silently reads the *real* `~/.local/share/shani-chronoa/…`
ledger — 1256 lines of this machine's actual history, which is what it printed
before I noticed. Read `egress._egress_log()` in a probe, and set
`XDG_DATA_HOME` as well as `XDG_STATE_HOME`; `egress` does not look at
`XDG_STATE_HOME` at all.

### A hardcoded cascade count that went stale the same day I made it go stale

`_speech_out()` resolved through the **real** `PiperTTS.engine()` — correct — and
then described it in a hand-typed sentence:

```
speech out would use cloud, resolved from PiperTTS.engine()'s own four-way chain
```

`gui/surfaces/voice.py` gained a **fifth** link (cloud) and this row went on
saying "four-way" while resolving it through the five-link chain. A count that
can be wrong on the same day a feature lands is not worth writing twice, so the
count and the list are now **read from `voice.CHAIN`** — the one place the order
is written down.

Two more false claims in the same row, both measured:

- **When cloud wins, nothing said the text left the machine.** `tts._announce`
  puts it in the log ("the reply text was sent off this machine") and the
  Speech-in row puts it on the panel; this row said "would use cloud" and stopped
  — the least useful sentence in a panel whose job is *what is wrong here*.
- **When nothing worked, the message listed only the four it knew about**, so on
  a machine relying on the cloud, the reason it was not working — no provider
  with a speech route — was invisible.

Mutations run and confirmed to fail: "four-way" hardcoded again (1), the cloud
disclosure removed (1), the nothing-works list back to four (1), and
`_tts_chain` returning a hardcoded tuple instead of reading `CHAIN` (3 — the one
that proves the number is derived rather than retyped).

### A third instance, with the loudest alarm: Diagnostics said speech input was broken

`gui/surfaces/diagnostics.py`'s `_speech_in()` constructed a `WhisperSTT`
**directly**, so it could not see a `CloudSTT` even when that was the engine
transcribing every utterance. On a machine with cloud recognition on, a key
configured and no local model it returned

> neither the whisper.cpp binary (…) nor a model file (…) is on this machine, so
> speech input is off whatever the settings say

— a **warning**, on a machine transcribing perfectly well. The row is titled
"Speech in (whisper.cpp)", so the alarm was at least honestly titled; it was still
an alarm about nothing.

The row is now "what can transcribe here", and the local half is reported
**alongside** rather than suppressed — a person who wants whisper.cpp locally is
still told exactly what is missing. `STATUS_WORKING` with the cloud named first,
then `"Locally: …"`.

**Zero-argument probes, so this asks the engines directly rather than reading
`app.stt`.** `Probe = Callable[[], Tuple[str, str]]` — no app, by design, so the
panel stays usable from a script. That is the same availability question either
way. `_cloud_speech_in()` is total: any exception is `''` — "not cloud" — so a
broken probe can only ever *under*-report, never invent a working engine.

Mutations run and confirmed to fail: the cloud half ignored (the original
defect), a raising probe claiming `working`, the local truth dropped along with
the alarm (1 each).

### Two engine rows could both claim to be the one answering

`gui/surfaces/model.py`'s llama.cpp row rendered
`f"{HOST}:{PORT} is answering with {active}"` off `local_llm.is_up()` — a claim
about the **app** made from a probe of the **server**.

Those genuinely diverge, because the LLM is chosen **once, at startup**:
`_maybe_enable_cloud_fallback` returns early on
`isinstance(self.llm, CloudLLMChain)` and never reconsiders. So a machine that
started without llama-server and started it afterwards has an *available* local
engine and a *selected* cloud one. Measured in exactly that state: the local row
said **"127.0.0.1:8765 is answering with qwen3-1.7b"** while `app.llm` held a
`CloudLLMChain`, and the cloud row said it "would answer". Two rows, one
asserting something false.

The rows stay availability-scoped ("is up and X is ready to load"), and
`_selected_engine(app)` names the choice **from `app.llm` itself** — because
re-deriving the condition is exactly how the two came to disagree. It also says
*why* they can differ: "chosen at startup, and not reconsidered since".

**This is the opposite of the Voice panel, deliberately.** There,
`PiperTTS.engine()` is asked per reply, so the running object and the selected
engine coincide and resolving through `app.tts` is right. Here the LLM is
selected once. Copying either approach to the other panel would be wrong, and
that is the note worth leaving.

Mutations run and confirmed to fail: "is answering" restored (1), selection
derived from config rather than the object (2), `isinstance` check dropped (1,
**only after the assertion was tightened**), the "chosen at startup" caveat
removed (1).

That last one is the interesting one. `assert "cloud" in chosen.lower()` **also
matches "Answers through CloudLLMChain"** — the class name contains the word — so
a mutation that deleted the `isinstance` check entirely ran green. The assertion
is on the phrase *"cloud provider"* and on the class name *not* appearing.

### And the *input* side had the same defect, which fixing the output side did not

I fixed the Voice panel's engine rows and stopped. The speech-**input** half of
the same panel was wrong in the same way, and by the same mechanism:

- **`_stt_for`'s guard was whisper-shaped.** It read
  `hasattr(live, "model_path") and callable(live.is_available)` — which
  identifies a speech engine by a field only the two *local* backends happen to
  have. `CloudSTT` has `is_available` and `transcribe` and no `model_path`, so
  the guard rejected the **running** object and the panel built a local
  `WhisperSTT` instead. Measured: the row read **"Whisper.cpp (whisper-cli)"**
  and *"the whisper.cpp model ggml-base.bin is not downloaded"* on a machine
  that was transcribing in the cloud and had no whisper-cpp at all. The guard's
  *intent* — "the panel must not describe a different object" — is right; the
  implementation identified the object by the wrong thing.
  `is_available` plus `transcribe` is the contract `stt.build_stt` says its own
  two backends share, so that is what it asks for now.
- **`senses/hearing.stt_problem` raised on it.** It read
  `engine_stt.whisper_path` and `.model_path` directly, so any engine without
  those attributes raised `AttributeError`; the caller caught it and reported
  `unknown - AttributeError: 'CloudSTT' object has no attribute 'whisper_path'`.
  Honest, and useless — it points at whisper-cpp on a machine that does not use
  it. An engine with neither attribute is now asked **itself**, which is what
  `CloudSTT.refusal()` is for: an engine that can explain its own absence beats
  any explanation assembled from attributes it happens not to have.

`CloudSTT.label` is **"Cloud provider"**, read verbatim by both
`app/voice.py:_stt_backend_label()` and the panel, so the log line and the row
say the same thing.

Mutations run and confirmed to fail: the guard back to `hasattr(model_path)` (3),
the label back to the configured backend (1), `stt_problem` reading
`whisper_path` again (3), `stt_problem` refusing to ask the engine (2).

**Every other `hasattr` guard in the tree was checked and is deliberate**, so
nobody re-audits them: 19 name a specific attribute, and the only one that
identified a *pluggable implementation* by an implementation detail was the guard
above. The rest are legitimate — `settings_window/voice.py`'s
`hasattr(live, "use_config")` guards a *call* on the single class that has one;
`gui/surfaces/models.py`'s `hasattr(voice, "label")` exists precisely because
`KOKORO_VOICES` holds `KokoroVoice` objects on one path and strings on another;
`midi.py`'s `hasattr(..., "read_bytes")` is the documented fix for `_Bytes`;
the `SIGALRM`/`setitimer`/`flatten`/`get_children` ones are GTK- and
platform-version probes.

**One test-hygiene note, because I wrote it badly first.** The test that
exercises `stt_problem`'s refusal assigns `cloud_voice._read_switch` directly and
restores it in a `finally`. If the assertion fails before the restore, the
module stays globally patched for every later test in the file — a failure
corrupting the rest of the run. It uses `monkeypatch` now.

`COST["cloud"]` claims **no real-time factor**, and says why: the cost is not
time but that the reply text leaves the machine, and quoting a latency would make
the row read as a neutral alternative to Kokoro.

Mutations run and confirmed to fail: `cloud` dropped from the panel chain (2), the
disclosure removed from `COST` (1), the panel building its own config again (1).

## The inbound gateway: two faults in one function, and no test file at all (2026-10-06)

`gateway.py` is the channel AGENTS.md's "Channels and the jail" section cites as
its security argument - one D-Bus method, `Submit(gateway, text)`, whose text
goes through the window's own `_submit` so it meets the same whitelist, consent
keys and post-conditions. That argument was written about a module **with no
`tests/test_gateway*.py` anywhere in the tree**, and whose one activation
function had never once succeeded.

### The three findings

**Nothing could turn it on.** `_export_gateways()` runs from `do_activate` and
returns early because `Registry.names()` is empty - because, measured with an
AST search over every call whose receiver mentions a gateway, **nothing in the
tree ever called `Registry.register()`**. No config key, no CLI flag, no Settings
row. A complete, tested-by-nobody inbound channel with no switch.

**`export()` threw on every call.** It passed the `_Service` object to
`register_object`, which needs a *closure*; a plain object with methods is not
one, and the type check rejects it. Measured:

    could not export the gateway interface: Must be callable, not _Service

The `except Exception` around it turned a line that had never once worked into a
log message reading "could not", which reads as transient.

**`BUS_NAME` was the application's own name.** It was `dev.shani.chronoa`, which
is `ChronoaApplication`'s `application_id`, so `export()` requested a name the
`GApplication` already held, with `REPLACE | ALLOW_REPLACEMENT`. Two owners on
one well-known name: the bus arbitrates, ownership ping-pongs, and a client gets
`ServiceUnknown`. Now `dev.shani.chronoa.Gateways`, matching the interface name
the introspection XML already declared.

**The second fault was invisible *because* of the first** - the name was never
requested, because `register_object` threw first. "Fixed the error" and "the
feature works" are different claims and only running it settles which one you
have. Verified end to end afterwards: export, own the name, `Submit` over a real
private bus, and the text arrives at the window's entry point, with every refusal
specific (`no gateway called 'signal'; registered: telegram, whatsapp`,
`message is 5000 characters; the limit is 4000`, `empty message`).

### Three ways this took longer than it should have, all recorded because all three will recur

- **`Gio.bus_get_sync` caches one session-bus connection per process**, resolved
  from `DBUS_SESSION_BUS_ADDRESS` at first call. Spawning a private
  `dbus-daemon` and setting that variable *inside* a script that has already
  imported `gi` does nothing. Measured: the service owned `:1.7503` on the real
  bus and the client sat on `:1.0`, and every call returned `ServiceUnknown`
  while the log said `gateway bus name acquired`. `export(..., connection=)` now
  exists so a test can hand it a bus, and `tests/test_gateway.py` uses
  `Gio.TestDBus` - which also needs `up()` before `get_bus_address()` returns
  anything but `None`.
- **`register_object` refuses a second export of the same interface at the same
  path on one connection**, and `bus_unown_name` releases the name without
  unregistering the object. So the bus fixture is function-scoped: a shared
  connection would let the first export decide every later one's outcome.
- **A registry captures `self._submit_gateway_text` when it is built**, so
  replacing that attribute on the instance afterwards is never read - and the
  resulting "the text did not arrive" looks exactly like a product bug.

### And a third fault, which only exists *because* the first two are fixed

Editing a channel name reloads rather than restarting, so `export()` runs
repeatedly on one connection. **`register_object` refuses a second export of the
same interface at the same path** (`g-io-error-quark: An object is already
exported for the interface dev.shani.chronoa.Gateways at
/dev/shani/chronoa/Gateways`), and `bus_unown_name` frees the *name* while
leaving the *object* registered.

Measured on the app's own reload path before the fix: **reload 1 owned the name;
reloads 2, 3 and 4 all reported `owner=False, on_bus=False`** while the registry
still listed the channel - so the Settings row would have read "on the bus as
whatsapp" while nothing was listening. A feature that works once and stops
working the first time you edit it.

The fix needed the **registration id**, and that is the trap: GLib's C function
is `g_dbus_connection_unregister_object(connection, object_path)`, but PyGObject
introspects it as `unregister_object(registration_id: int)` - measured by passing
the path and getting `TypeError: Must be number, not str`. Reading the C header
and writing the Python is how that happened, and it is the same class of mistake
as assuming a 400 means a route exists: **the signature you remember is not the
one you are calling.** `test_reloading_keeps_the_channel_reachable` asserts the
reload works rather than that a particular unregister call is spelled correctly,
so it survives the next re-spelling.

**This is also why the round-trip test's bus fixture is function-scoped** - one
root cause seen from two directions. And it was found by running the reload path
I had just written, immediately after fixing a test fixture that failed for the
identical reason: I had witnessed the constraint and then written code that
violated it.

### One equivalent mutant, kept rather than hidden

Deleting the `if handler is None` guard inside `dispatch` leaves the file **fully
green**: GDBus refuses any method absent from the interface info before the
closure runs, so that branch is unreachable over D-Bus. The guard is kept (it is
the right thing to have if the XML ever grows a method with no handler) and the
test now says so, including that GDBus's own wording is `No such method
"RunAnything"` - accurate, and it does not point at the one method that exists.

### Now reachable

`gateways` in the schema, parsed by `gateway.parse_config` into `(entries,
errors)` - **errors are returned, never raised and never dropped**, because a
channel name containing `/` or `.` cannot work (it becomes part of a D-Bus method
name) and a setting that silently ignores what it cannot parse is a switch that
appears set and does nothing. Format: `name` or `name:execute`, comma-separated.
An invalid entry is logged per-entry, the good ones still register, and the
Settings row reports both.

Settings → Privacy gained **"Inbound channels"** and a live **"Listening for"**
row that names what is on the bus and what was ignored. Changes reload live
(`_reload_gateways`, which releases the previous owner before taking a new one,
so a reload does not leave two owners of one name).

`tests/test_gateway.py`, 32 tests, mutation-checked: object-instead-of-closure (2
fail), `BUS_NAME` back to the application's (1), rate limit removed (1), length
limit removed (2), bad entry silently dropped (3).

**Also corrected here:** AGENTS.md said `register_agent`/`broadcast_message`
"remain dead". They no longer exist - removed in the 2026-10-02 structure review,
per `child_supervisor.py`'s own docstring. What is left of that module *is* live,
imported by `audio.py:42` to supervise the `pw-record`/`pw-play` children. And a
naming collision worth knowing: **"Kilo Gateway API key" in Settings is an LLM
provider**, unrelated to this module; "gateway" means two unrelated things in
this codebase.

## Channels and the jail: the two review points, audited rather than asserted (2026-10-05)

From the same thread: *"you can connect other channels like whatsapp etc to give
commands straight to your pc... so best to sandbox/jail the app fully."*

**Channels are not a WhatsApp integration.** `gateway.py` is the part that has to
exist before any of them can be written safely, and its shape is the argument:

- **The session bus, not a socket.** No port, no listener, no network. Whatever
  carries a message to it later is a separate component with its own credentials,
  and Chronoa never sees a token.
- **One method: `Submit(gateway, text)`.** A gateway cannot ask Chronoa to run a
  skill, name a tool, read a file, or reach the model server. It hands over words
  and gets words back.
- **The text goes through the window's own `_submit`**, so it meets the same
  whitelist, the same consent keys, the same post-conditions and the same log. A
  gateway has no way to lower a gate because it never touches one - which is the
  property that matters, since a second path into the assistant makes every gate
  the window respects optional for whoever finds it.
- **Ask-only by default.** Executing anything needs an explicit per-gateway
  grant, *and* still goes through `tools.execute_tool_outcome`.
- **Bounded**: 4,000 characters, 20 messages a minute, no attachments. An
  unbounded pipe into an assistant that can run tools is a denial-of-service with
  extra steps.
- **Off unless asked for.** No registered gateway, nothing exported - so a
  machine that has not opted in has no inbound interface at all.

### The jail, measured

`tests/test_exposure.py` audits the doors rather than asserting a slogan, because
"we are local-first" is a claim about configuration and configuration drifts:

| door | measured |
|------|----------|
| model servers | `local_llm`, `stt_server`, `model_service` all bind `127.0.0.1`; ports 8765/8766/8767-8769 |
| the MCP server | stdio only - no `HTTPServer`, no socket, no `AF_INET` |
| exported bus names | exactly one before this change: `dev.shani.chronoa.SearchProvider` |
| the gateway | one method, and it reaches no tool - checked from the **AST**, not by grepping |

Two failures of my own checks while writing it, both the same shape as the
animation one: a grep that hit the module's own **docstring** (which explains, in
prose, that a submission still goes through `tools.execute_tool_outcome` - the
sentence *is* the argument), and a regex for `^HOST = "127.0.0.1"` that silently
matched nothing because the line is `HOST, PORT = "127.0.0.1", 8766`.

## The channel bridge: Telegram and WhatsApp, thin clients of `Submit` (2026-10-06)

`channel_bridge.py` (`shani-chronoa-bridge`) is the other half of the inbound
channel: adapters that *carry* a message to `gateway.Submit` and print the
reply. Telegram (Bot API `getUpdates` long poll - dials out, no public URL)
and WhatsApp (Cloud API webhook - the one adapter with a port, loopback by
default, every POST checked against `X-Hub-Signature-256` before it is
believed). Tokens come from the bridge process's environment
(`CHRONOA_TELEGRAM_TOKEN`, `CHRONOA_WHATSAPP_*`); Chronoa never sees one, and
a reply that mentions one is redacted before it is logged.

**Verified by running, not by reading:** 16 tests in
`tests/test_channel_bridge.py` - `httpx.MockTransport` for both wires, a real
loopback HTTP server for the webhook (challenge GET + signed POST + a 403 on
a forged signature), and the bus call itself driven **across two processes on
a real `dbus-daemon`**: the app's own `gw.export` on one side, `cb.submit` on
the other, asserting the reply round-trips and an unconfigured channel is
refused by name. An in-process `Gio.TestDBus` **cannot** drive `cb.submit` -
its `call_sync` needs the connection's main context iterating and deadlocks
against the gateway's worker-thread reply (measured: the test hung until
killed). The launcher itself was run directly: `--help` works, and both
channels exit 2 naming the missing env var rather than starting token-less.

`shani-pkgbuilds/shani-chronoa/PKGBUILD` gained the `install -Dm755` line for
the new binary (the packaging test reads git's recorded mode, so the file is
staged; a checkout that is not committed still fails that test by design).

## Dictation: what the review asked for, and what actually blocked it (2026-10-05)

From the GitHub issue: *"add features like transcription of Google Meet etc
could be very useful as it will be able to listen locally on dev/snd directly."*

**Chronoa had every part of it and could not do it.** `audio.py` already shells to
`pw-record`, whisper.cpp was already there, and the turn pipeline was already
there. The blocker was one number: **an ordinary spoken turn is capped at twenty
seconds**, which is right for "what time is it" and wrong for talking *at* the
machine - a paragraph is not twenty seconds, and 1.2 seconds of silence cuts at
every pause *inside* a paragraph rather than at its end.

So it is two ceilings chosen when the turn starts, not one compromise:

| | ordinary turn | dictation |
|---|---|---|
| ceiling | 20s | **300s** |
| silence to finish | 1.2s | **2.5s** |
| started by | button or wake word | **<kbd>Ctrl</kbd>+<kbd>Shift</kbd>+D** |

`app.dictate()` reuses the same capture, transcription and turn, so there is no
second audio path to keep honest, and it is stopped the same ways - the stop
button, Esc, or the silence.

**And it says when it was cut.** `audio.py` now records *why* it stopped
(`silence` / `limit` / `cancelled`) because a recording that ended because the
person finished and one that ended because the clock ran out are different facts,
and only the second is a transcript that stops mid-sentence. A transcript that
silently stops reads as "that is everything I said", which is the one thing a
transcript must never imply. The first version of this had a
`_dictation_note()` that claimed to report a cut and could never report one,
because nothing set the flag.

Two bugs of mine, both caught by writing the test before the run:

- **`self._stop_listening()` does not exist.** The second press on dictation
  would have raised `AttributeError` - at exactly the moment dictation most needs
  to work. It now goes through `_toggle_listening`, the microphone button's own
  handler, so there is one implementation of "stop listening".
- **Restoring `1.2` overrode the person's setting.** `_start_listening` reads
  `_listen_silence_seconds` *or the configured `end-of-speech-pause`*, so putting
  the constant back meant one dictation silently overrode the user's own
  microphone timing for every later turn. It restores `None`, which means "use
  the setting".

`tests/test_dictation.py`, 9 tests, drives the product's own methods rather than
stand-ins.

## What the assistant was actually learning from: two fixtures (2026-10-05)

Built a per-skill verdict summary in the Tool activity panel - worst failure rate
first, every rate carrying the count it is a proportion of - and it found the
most important thing in the log on its first run.

**`liar` failed every one of its 313 calls and never verified once.** It returns
`"Created the file."` 343 times while its post-condition records *"the file was
never created"*. There is no `skills/liar.py` in this tree: it is a fixture that
did exactly what it was built to do.

**`liar` and `unver` account for 692 of 12,856 logged calls and 313 of the 378
failures.** So this machine's entire failure signal comes from tools that are no
longer installed, and a model fitted on that log reports a number about a machine
that does not exist any more - unless it says so. Two changes, both about
*showing* rather than deciding:

- The panel's row ends **"not installed any more, so this is history, not a
  fault"**, because "failed 313 of 313" reads as *broken* and only the registry
  can tell you that it was a fixture.
- `train_and_save`'s provenance records **`examples_for_unknown_tools`** and
  which tools. **Recorded, not filtered**: filtering would be the more helpful
  behaviour and the wrong one, because it changes what the model says about the
  past without saying it did. Whether to train on it is a decision for a person
  holding the figure.

This also explains the outcome model's failure blind spot, which I had put down
to a weak feature space. Measured again on the same log: `liar` fails 313/344 and
`delete_file` 60/2,176. The signal was never weak - it was *concentrated in a
fixture*, and the feature view collapsed it into seven vectors.

`unverified` calls are excluded from the per-skill rates on purpose: they are
most calls, and counting them as successes would turn every rate into a statement
about post-condition coverage rather than about the skill.

## Auditing every surface for scroll, wrap, overflow and motion (2026-10-05)

`tests/test_ui_layout_contract.py` is an audit turned into a contract, over all
twenty surfaces. Measuring found two real defects that reading had not.

**One surface could not scroll.** `inventory` passed its column straight into
the toolbar's content slot, so a window shorter than its twenty-odd rows could
not reach the bottom of them - and it is a list of every function and its state,
so the last few are exactly the ones nobody can get to. Nineteen surfaces had a
`Gtk.ScrolledWindow`; that one had none, and nobody had noticed, because a panel
that is *mostly* visible looks fine in a screenshot taken at full height.

**Every wizard page was too wide, and there was a horizontal scrollbar on all of
them.** The bar in the bottom of every wizard screenshot - visible since the
first slot run and unexplained until now. It belongs to
`Adw.NavigationPage`'s *own* scroller, whose horizontal policy is the default, and
it appears when the page content is wider than the page. The cause:

> **`Gtk.Label.set_wrap(True)` does not bound a label.** It wraps the text *at
> whatever width it is given* and reports its natural width as the full
> unwrapped run. Measured: the wizard's welcome description asked for **947px in
> a 560px window**; `set_max_width_chars(56)` brings the same label to **437px**.

So `common.wrap_label()` and the wizard's `wrapping()` cap every wrapping label
they create, and `tests/test_ui_layout_contract.py` fails if any is left
uncapped. Thirteen of sixteen wizard pages now have no node wider than the window
at all.

**And the wizard is now clean too — all sixteen pages.** After the label caps,
three pages still asked for more than the window (774, 956, 983px). The cause was
**libadwaita 1.5 does not wrap its own text**, measured on the installed library:

| widget | a 130-character string measures |
|--------|----------------------------------|
| `Adw.ActionRow` subtitle | **1,227px** |
| `Adw.PreferencesGroup` description | **1,428px** |
| a capped `wrapping()` label | 437px |

So every piece of prose in the wizard now lives in a capped `wrapping()` label or
a tooltip, and the libadwaita slots carry at most a few words. **Worst demand
across all sixteen pages: 538px, in a 560px window.**

Three measurement mistakes of my own, all recorded because each produced a
confident wrong answer:

- **Reading allocation as demand.** A widget allocated 900px reports 900px. The
  number that decides whether a page needs a sideways scrollbar is what its
  *content asks for*, measured on the child of the page's own scroller - not on
  the page. This was chased in circles for a while because the page-level numbers
  never moved when the text got shorter.
- **`propagate_natural_width(False)` does nothing here.** Measured: a scrolled
  window with a 900px child still reports 900. It is left out of the code rather
  than left in under a comment claiming it works.
- **The block regex counted prose as selectors**, so the first animation test
  reported eleven uncovered animations and every one was a sentence.

**Two things that looked like the fix and were not, both measured rather than
assumed:**

- `Gtk.ScrolledWindow.set_propagate_natural_width(False)` **does not reduce a
  scrolled window's natural width** on this GTK: a 900px child still measures
  900 with it off. It is left out of the code rather than left in under a comment
  claiming it works.
- My first version of the animation test reported eleven uncovered animations
  and **every one was a sentence** - the block regex was capturing the sheet's
  prose comments as selectors. Strip `/* ... */` before parsing CSS.

**What the contract asserts**, each with a failure mode behind it: every surface
has a scroller; none needs more than **480px** (the widest measured came to 256px
before the fix, and the 560px wizard looked clipped because the harness has no
window manager - a clipping that was the environment, not the layout); a label
over 30 characters either wraps or ellipsises; and **every selector with an
`animation` has a `.reduce-motion` rule**, because the desktop's accessibility
setting is only honoured if something reads it. The sheet has three animations -
the orb's listening and queued pulses, the suggestion-bar reveal, the transcript
turn-in - and all three are stoppable.

## Every page has an id, and the things that had no UI at all (2026-10-04)

`pages.py` is the registry; `shani-chronoa --show-page=settings:privacy` and
`app.show-page('setup:review')` reach any page of any window, and a retired id
resolves to whatever absorbed it. This is shani-cassini's shape (`notebook.py`'s
`PAGES`/`page_ids()`/`select(pid)`, `--section=`, a `show-section` action),
generalised to three windows.

It exists because **every page was reachable only by holding the mouse.** The
settings sections by typing in a search box; the wizard steps by pressing Next;
the main window by whatever happened to be open. So a notification could not say
"open Settings on Privacy", a keybinding could not, and a test could not - it had
to guess at pixel selectors, and the guessing is expensive:

- `entry:Search settings` never matched, because a `Gtk.SearchEntry`'s role is
  **`search-box`**, not `entry`. The typing went to no widget.
- Four screenshots of four "different" settings sections came out
  **byte-identical** (1024000 px, 0 different) while every step reported green.
- Every wizard page's forward button was called "Next", so with a stack of
  visited-but-alive pages behind it the tree lists several identical "Next" and a
  script clicking "the first Next" lands on the wrong page's. They now read
  **`Next: Ears`**, which is also the answer to "what am I agreeing to".
- A fixed `sleep=25` after Download meant the click on "Start using Chronoa"
  landed before the page was drawn, the wizard stayed up, and the "main window"
  screenshot was the wizard.

**Three bugs in that class were only findable by running it:**

- `setup_wizard.build_window` **raised** on `Gtk.Box.connect("shown", ...)` -
  only an `Adw.NavigationPage` emits `shown`. So `_open_setup` died, the app
  carried on, and "the setup wizard is not shown" was the whole symptom. Three
  runs of screenshots showed the main window every time.
- The review page's Download handler took no argument, so `clicked`'s button
  raised `TypeError` on the only press that matters - the one that would have
  started the download.
- **My own measurement was the broken thing once.** `get_accessible_property` does
  not exist in this PyGObject, so a local probe read every accessible name as
  `""` and I nearly reported that the settings window was unnamed. The harness's
  AT-SPI tree says otherwise. A probe that cannot fail is not a probe.

### What had no UI at all, and now does

`gui/surfaces/learning.py` - **"What it has learned"** - is the surface for four
pieces of machinery that had no entry point in the app at all:
`learning.train_and_save()`, `distill.harvest_rows()`,
`learning.export_knowledge()` and `distill.train_router()`. Five read rows (facts,
bandit arms, routing pairs, a distilled router, the outcome model) and three
actions (train outcome, train router, export), each off the main loop because
every fit reads the whole log.

It **leads with the detection, not the accuracy**, because on a 94%-majority log
83% is what not learning scores too; and it checks a model's `honest` flag before
quoting any number from that model's own report.

### The extras got their own pages, and the hub went

`generate_image` and `photos` were filed under **Files**, and `describe_screen`,
`identify_sound`, `diarize_speakers`, `search_conversations` and `remember_fact`
were **not mapped to any page at all** - so a 2.0 GB picture model was reachable
only as "Files > make a picture", and three engines had nowhere to live. They now
have Eyes, Imagine, Photos and video, Sounds and recordings, and Languages, in
the same words the setup wizard uses.

**Memory was nearly a false claim.** A first pass wrote that `local_embed` was
"installed by the wizard and read by nothing at all", from grepping for the
module name and not finding a caller. It is read - `conversation_store.search()`
embeds the query and the stored turns through it, and the `conversations` skill
exposes it as `action: search`. **"grep found no caller" is not "nothing calls
it"**: the caller was two indirection away, in a module named after the *store*
rather than the *embedding*, behind a skill whose tool name shares no word with
either. This is the same shape as the `_outcome_model` mis-addressing above, and
it is why the capability pages here were assigned from what each engine is
actually imported by.

The setup wizard has no "Optional extras" hub either: it existed only to list the
other pages, and the review page now carries one row per extra.

And the **welcome page lists all nine things individually, each with its real
size** - Eyes 546 MB, Imagine 2.0 GB, Memory 146 MB, Photos 88 MB, Sounds 29 MB,
Who said what 37 MB, Languages 432 MB, plus what Brain/Ears/Voice are doing. It
used to say Chronoa "needs three things", list those three, and collapse the six
extras into one row reading "eyes, pictures, memory and languages - after the
voice" - so the first screen a new person saw named three of nine, and the only
mention of a 2.0 GB picture model was inside a comma-separated string with three
100 MB ones. Nine rows is four more lines, and each one can be clicked into,
which the lumped row could not be because it was not a target.

**Those nine rows were not clickable, and this file said they were - FIXED
2026-10-06.** Walking the real welcome page for anything pressable gave exactly
one answer: `Start`. All nine were bare `Adw.ActionRow(title=..., subtitle=...)`
with no `activatable`, no suffix control, and nothing on `activated`, two lines
under a comment claiming they were targets - and that claim is also the *reason*
given for splitting the lumped row, so the rationale rested on a capability the
rows did not have. The module already knew the device (`choice_group` passes
`activatable_widget` to every row it builds); it had been left off here. Each row
is now activatable, carries a chevron and a tooltip naming its page, and is
verified by **pressing it** - one fresh wizard per row, because `goto()`
truncates its own history and a reused window made the first version of that
test report `Brain -> voice` and `Ears -> imagine`, which looks like a routing
bug and is a probe sharing state (`tests/test_welcome_rows_are_targets.py`).

### And three log lines that were confident and wrong

- `Whisper.cpp not available - STT disabled` - logged on a machine that had just
  had whisper-cpp installed, because `is_available()` needs the binary *and* the
  model and the model is a download. It now names what is missing.
- `Initialized: ... stt=Whisper.cpp (medium)` on the same run, one line after
  saying STT was disabled - it interpolated the backend's *label* rather than
  whether it could listen.
- `LLM unavailable (no Ollama, ...)` - on a machine where Ollama was never the
  default and llama.cpp is. It now says which of the three backends is missing
  what, which is the only reason that line exists.

The same shape was in the UI: the state line read **"Ready"** on a machine with
no model, while the line under it read "LLM unavailable". `set_can_answer()` now
overrides it, because "Ready" is the idle label and so is true by construction on
every machine.

## Six functions that were alive in one copy of the tree and dead in the other (2026-10-04)

`weights_from_records`, `weights_from_tracker`, `reorder`, `organ_status`,
`reliability`, `weight` existed only under `pkg/shani-chronoa/...`, while
`usr/lib/shani-chronoa/...` - the canonical package the tests import and the
overlay installs - did not have them. `tool_select` still called them, inside
`try/except`, so `learned_weights()` returned `{}` on **every** request and
`reorder()` was never reached: the learned half of selection was dead and silent.
16 tests in `tests/test_sleep_and_learning.py` were red about it.

Restored, with the boundary named. **A guarded import of a function that does not
exist is the absence-shaped green this workspace keeps being bitten by**, and the
only test that could have caught it asserted the *effect*
(`tool_selection_applies_what_was_learned`), not the import.

`test_it_runs_nothing_on_its_own` was itself a false positive: it scanned the
module text for `threading`, `asyncio`, `while True` and `sleep(`, which cannot
tell an import from a paragraph - and `learning.py` is 4,000 lines of prose about
why threading is the wrong shape here, so it went red on its own docstring. It
parses the AST now, and carries a control module that really does import
`threading` so the check is known to be able to fail. Same class as pinning an
exact command line: assert on the shape of an interface, not its text.

## Optional extras (setup's More page, 2026-10-02)

Each is opt-in, pinned by size and sha256, installed into the user's home, and
runs on this machine. What needs no model uses the system's own tools.

| Extra | What | Runs as | Download |
|---|---|---|---|
| Eyes | describe a screen or photo | llama.cpp `--mmproj`, `shani-chronoa-model@vision` (port 8767, `--sleep-idle-seconds`) | 0.5 / 1.6 / 3.0 GB by RAM and GPU |
| Imagine | pictures from a description, and changing a photo by description (img2img) | stable-diffusion.cpp release, `sd-server` `@imagine` (8769) | 2.0 GB + 26/35 MB engine |
| Memory | conversation search by meaning (hybrid with FTS5, reciprocal rank) | llama.cpp `--embedding`, `@embed` (8768) | 146 MB |
| Languages | OCR data, a Piper voice, whisper listening | user tessdata + Piper | 1-12 MB each + ~64 MB voice |
| Photos and videos | identify faces/objects, blur faces, blur/remove background, page scans | OpenCV wheel + numpy + 3 OpenCV Zoo models | ~88 MB |
| Sounds | what a sound is (AudioSet 527 classes) | sherpa-onnx + CED-tiny | 28.5 MB |
| Who said what | speaker turns in a recording | sherpa-onnx + pyannote seg 3.0 + CAM++ | 37 MB |

No model needed: video keyframes and "describe this video" (`video_frames.py`,
ffmpeg scene detection), plain photo looks and 2-4x upscale (`edit_image`,
ImageMagick), subtitles and transcripts (`recordings.py`, ffmpeg + whisper),
recording clean-up (ffmpeg `anlmdn,afftdn`: measured 11.8 -> 13.4 dB on real
speech - modest; `loudnorm` made it worse by lifting the noise in the gaps and
resampling to 192 kHz).

Lessons from building these, each found by running:

- **OpenCV 5 orders NanoDet's outputs differently from the Zoo's 4.x
  reference** (all scores, then all boxes; three levels, not four). The
  reference's `outs[::2]` pairing would pair scores with scores; `detect._levels`
  pairs by shape. The Zoo's 0.35 threshold found two people in a one-person
  portrait; 0.5 is right on both sample photos.
- **A page finder must not find the picture's own border** - random noise came
  back as "a page" until candidates spanning ~the whole frame were refused.
- **Downloads resume** (HTTP Range) and are retried; a server that ignores
  Range restarts the file cleanly; the digest still covers the whole file
  (`tests/test_stt_provision.py`, with a wrong-bytes control). A 2 GB model on a
  slow link timed out three times in a slot before this.
- **A truncated download can still list its archive** - a partial `.tar.bz2`
  lists its first members. Pin a file only after its size matches the
  publisher's.
- **Address-space limits shape the design**: a skill child runs under the
  profile's 512 MB RLIMIT_AS, so every model runs as a loopback service and
  skills are HTTP clients; slow skills get a named longer timeout
  (`tools._SLOW_TOOLS`), still capped by the profile.

## The right rail, and what it may say (2026-10-07)

The window had a left sidebar and a transcript, and nothing else. Three
questions a person has while talking to an assistant with its hands on their
machine had nowhere to live, because the conversation is a *transcript*: it
grows downward and the useful part scrolls off the top. `gui/rail.py` is a
right-hand column answering them, in that order:

1. **Now** - the live state and the tool currently running, read from
   `_state_label` and `_detail_label` rather than tracked separately. The
   first version kept its own copy and rendered "Idle" while the window said
   "Ready"; reading the widgets is the fix, and it is also why the rail is
   built at the *end* of `_setup_ui` - built earlier it reads labels that do
   not exist yet.
2. **Timers** - pending countdowns with the time left, so a countdown is a
   countdown rather than a number that never moves.
3. **Changed** - files Chronoa wrote that the undo ring can still put back,
   each row a button that opens the Diff panel. Same store the Diff panel
   reads, deliberately: a second change log would drift from the first.
4. **Tasks** - outstanding items from the task list, **only when its consent
   key is on**. With the key off it says so, because silence would read as
   "no tasks" when the truth is "you are not looking at them".
5. **Posture** - permission mode, memory consent, sandbox qualification. Three
   lines that each change what a reply is allowed to mean.

**Not in it, on purpose.** Not the senses and not health - those panels own
them, with a per-row reason. Not goals or a plan — **corrected 2026-10-11: this
used to say "`goals.py` has no producer yet, and a card for a queue nothing can
enqueue is a dead control", and it was stale.** `skills/manage_goals.py` is the
producer and the Goals panel is the consumer; both are wired and tested. What
Goals has no surface for in the *rail* is deliberate for this reason instead: a
run that can park mid-plan is a queue nobody can act on from a side column, and
its panel owns it. Every
section above reads a store that already exists and renders an honest
sentence when it is empty, because a rail of decoration is worse than no rail:
it looks like it knows something.

**Two bugs found by rendering, not by reading.** The rail's scroller had
`set_propagate_natural_width(True)`, so a long posture line pushed the column
past its own width request and off the right edge of the window - the one
thing a fixed-width column exists to prevent. And `RailBreakpoint` was first
handed the *row* holding the conversation, so at 600px it hid the conversation
too: an empty right half with no orb, no transcript, no composer.

**`SidebarBreakpoint` was a dead control too, and the narrow layout was
impossible for a reason nobody had looked for.** The breakpoint was constructed
and returned; nobody ever called `window.add_breakpoint()`, and an
`Adw.Breakpoint` no window knows about is never evaluated. Registering both is
what makes the rail responsive - but at 600px the sidebar still did not
collapse, and the cause was upstream of it: the split view reported it needed
**762px**, because **the organ strip asked for 470px of its own**. Eight little
status lights were the single reason the conversation could not fit a narrow
window.

`OrganStrip` is now a `Gtk.FlowBox`, so it wraps instead of demanding a row
(measured: 470px minimum -> 90px). Two follow-ups the render forced: the cells
must not be *homogeneous* (equal-width cells broke "thinking" onto a second row
at 1280px with room to spare), and `max_children_per_line` is set explicitly
because the default still wrapped early. The collapse itself is applied on
width change rather than by the breakpoint alone - the breakpoint's setter runs
before the split view finishes setting itself up and is then overwritten - and
it goes through `_set_panels_visible`, because `collapsed` alone leaves the
drawer *open* over the conversation, which is the exact bug that method's own
docstring records for F9.

Two declarations were also doing nothing but printing a warning on every window
build: `width`/`height` on `.sidebar-dot` and `cursor: pointer` in the diff
stylesheet. GTK4's CSS parser has neither property; the dot was being sized by
its `min-*` pair all along, and the pointer is now set through `set_cursor`
where GTK is new enough for it.



`../harness-study/HARVEST-2/` mined 42 agent harnesses; every item it proposed
is now in the code or rejected with a reason in its own `00-MASTER-BACKLOG.md`
implementation log. Three rounds: Tranche 1 patches, then the feasible Tranche 2
items, then the T3 structural core. What landed, and the reasoning that decided
the shape:

- **`ToolFailure` (`toolfailure.py`)** — a skill that ran but whose effect did
  not hold says so in-band; the child carries it as a stdout marker, and both
  the local and the sandboxed path report `ran=True, verdict=FAILED`. This is
  the first honest fourth state; everything else in `DispatchResult` is
  "ran/not ran" crossed with "verified/not".
- **Per-origin round budgets (`assistant.py`)** — `handle(origin=...)` is the
  parameter the T2.17 blocker analysis kept asking for. A person gets 4 rounds,
  a trigger-driven turn gets 1, an origin nobody has named gets 1 (fails closed
  like `profile_for_origin`). The same parameter now reaches `execute_tool`, so
  the audit log can finally tell a typed turn from a bridge turn.
- **`permissions.Mode` — `DEFAULT` / `DONT_ASK` / `EXPLORE`, plus
  `bypass_immune`** — `DONT_ASK` refuses every ask and suspends session grants
  (the unattended-trigger default); `EXPLORE` refuses anything outside
  `capabilities.READ_ONLY_TOOLS`; destructive tools never answer from a
  standing grant and no longer *offer* one. **This is the item most likely to
  be argued with**, and the argument is worth having: `delete_file` asking again
  on the third call in a session is friction that buys a property. It buys it
  because the alternative is a session grant written by one "yes" standing in
  for every later delete, and the harvest is explicit that the answer to
  "should this be delegated" is per-tool.
- **Requery/validation budget** — three never-run calls in a row ends the turn
  honestly. A round where the only tool output is refusal, repeated, is a circle
  more rounds do not break.
- **`goals.py`** — a plain-data goal run that can `park` and `resume`, with
  `PlannedStep` variable dependencies that refuse to run a step whose inputs do
  not exist yet. **Wired, and this line said the opposite until 2026-10-11:**
  it claimed the module was "deliberately un-wired into the UI: a task card over
  a queue nothing can enqueue is a dead control". `skills/manage_goals.py` is
  the producer, the Goals panel is the consumer, and `tests/test_goals_wired.py`
  covers all three. A person can now also take one step out of a plan and
  reorder it, and the model is told what was changed — see
  `harness-study/HARVEST-OIU.md`.
- **`restart.py` / `sandbox/qualify.py`** — the bounded restart table and a
  fail-closed qualification that reports `missing`/`unknown` rather than
  blending "off" into "yes". On this unprivileged machine it correctly reports
  `proven is False`.

**What the wiring audit caught, which the unit tests had not:** `ToolFailure`
was only ever proved in-process — a live dispatch through the real subprocess
was the first thing that proved the marker crosses; the permanent test includes
a control that must succeed, so the assertion can fail. The event sink had no
live subscriber until `ConversationMixin` started handing it to `handle()` in
place of three callback slots. And two surfaces (`diff`, `artifact_store`)
returned pages with no `status()` at all, so every surface walker raised — the
new `diff-symbolic` icon was a glyph this theme does not ship.

**The permission-mode row is session-scoped on purpose.** Every subtitle says
"Back to Normal when Chronoa restarts". Persisting `EXPLORE` would leave a
restarted assistant unable to change anything with no visible cause; that is the
"an option whose label promises something the program does not do" failure the
module already has a scar for.

## Six defects the first full run after the harvest work found (2026-10-07)

The first complete run since that work landed: **53 failed, 5739 passed, 39
skipped** in 45:44. Eleven of the failure *files* were already on the
environmental list below and are unchanged; these are the rest.

**`office/` was mid-rewrite when the run started, and was reverted while it ran.**
At the start of the run `office/write.py` was an uncommitted rewrite that had
dropped `make_docx`, `make_pptx`, `rows_from_text`, `_Strings` and `_xlsx_cell`
— `skills/analyze_table.py:312` and three tests call the missing `make_xlsx`, so
`save_as='~/x.xlsx'` raised `AttributeError` on every call, and `office/edit.py:105`
still called a `_Strings` that no longer existed. Nine `test_office_document.py`
failures came from that. **Partway through this pass `office/` was reverted
outside the session** and now matches HEAD, which carries its own `make_xlsx`
(shared-string table, `fullCalcOnLoad`, `[Content_Types].xml` first).
`test_office_document.py` (11) and `test_analyze_table.py` (13) both pass
against it. Nothing here is claimed as the fix: **a suite someone else's
`git checkout` turned green is not a repair**, and the one genuine finding from
that thread is worth keeping — the rewrite's xlsx writer emitted `<w:row>` into a
document binding only `x:` and `r:id` into a workbook that never declared `r:`,
so the file it produced failed `read_xlsx_rows` with `OfficeError: workbook.xml
inside the file is not well-formed XML (unbound prefix)`. No test caught that
because nothing ever read a workbook this module had written.

**Zeroing the organ strip's cell padding removed the gap between the lights.**
The `OrganStrip` is a `Gtk.FlowBox` so it wraps on a narrow window, and the
`flowboxchild` padding was costing ~230px of width — `padding: 0; margin: 0`
fixed that measurement and broke the reading: the eight labels rendered as
**"listening looking speaking network sensing remembering acting thinking"**,
one run-on string rather than eight named lights. No test caught it, because
constructing the strip is not looking at it and the width test *passed* the
whole time.

The first repair put a 4px CSS **margin** back, which is what the render wanted
— and left the gap in two places (that stylesheet plus
`OrganStrip.set_column_spacing`'s 2px), so no test could measure it and either
half could be removed without failing anything. It is now
`set_column_spacing(6)` in `organs.py` and nothing else, with
`test_the_organ_lights_are_separated_by_a_gap` on that property. **The lesson
is the width measurement's blind spot**: it measured the right number and the
render was still broken.

**The organ strip: what was asked, what is actually wrong, and the lead.**
Asked whether the strip is misaligned and whether it drops anything. Measured,
not read:

| question | measurement | verdict |
|---|---|---|
| does every organ appear? | `ORGANS` 8, `ORGAN_ICONS` 8, no key either way, **8 of 8 cells mapped** at 1280 and 640 | nothing is missing |
| is any label truncated? | every word whole in the render at both widths | no |
| is each icon over its own label? | pixel-measured: worst offset **1.0px** across the eight | yes, centred |
| is the *row* aligned? | labels span x=296..791 (centre 543) while the mode chips below span 477..782 (centre 629) | **86px off** |

So the misalignment is the row, not the icons: it is hard against the left of
the chat column while the chips and composer beneath it are centred. **Two fixes
were tried and both are measurably no-ops, which is why neither is in the tree:**

- `set_halign(Gtk.Align.CENTER)` on the strip: the property is set and no pixel
  moves. A vertical `Gtk.Box` hands its child the full cross-axis width whatever
  the child's alignment is — the strip was allocated **680px of a 680px box**
  with `halign=CENTER`.
- Wrapping it in a horizontal row between two expanding spacers, the usual
  trick: also nothing, because **there is no slack to distribute** — the strip
  fills its container.

The numbers nobody had looked at are why. Measured in the real window at 1280:

```
strip.measure(HORIZONTAL, -1)  ->  min 90,  natural 834,  allocated 680
cells allocated                 ->  58, 50, 59, 56, 50, 90, 41, 54   (= 458)
7 gaps x 6px                                                (=  42)
                                                            total  500
```

So the strip is handed the whole 680px column, its cells fill only **500px** of
it, and the ~180px left over is the lopsided right edge. Its *reported* natural
width (834) is larger than the sum of its children's own naturals (500) by
almost exactly 8 x the default flow-box child padding - so the measure counts
padding the allocation does not apply. That is why `halign` and the spacer trick
both had nothing to work with: the strip is measured as full and allocated as
full while its contents sit at the left.

Two more measurements, both contradicting the story I wrote down first, and
recorded for that reason:

- **`.organ-strip > flowboxchild { padding: 0 }` does match.** Loaded in
  isolation against eight synthetic cells: 444px natural without it, **402px
  with it** - worth 42px, not the ~350px the 834 figure suggested. The
  stylesheet does what it says; it is not the missing 334px.
- `ORGAN_IDLE_CSS` in `organs.py` is a **second** stylesheet declaring
  `.organ { padding: 2px 6px }` and `.organ-label { font-size: 10px }`. Neither
  reaches the widget either: the cells allocate exactly their labels' natural
  widths (58px for "listening", whose text is 57px). Two stylesheets for one
  strip and one of them does nothing - worth knowing before editing either.

`Gtk.Center` and `Gtk.Alignment` are both gone in GTK4 and `Gtk.FlowBox` has no
`justify`, so this is not a one-liner.

**CLOSED 2026-10-09 - and the numbers above are why it was never closed.** The
two no-op attempts were real, and so is the fix: `OrganStrip.do_measure`
overrides `Gtk.FlowBox`'s widest-child-x-count measure with the sum of the
cells' own naturals, so the strip is allocated **exactly** what its lights need
and `halign=CENTER` finally has slack to work with. The `set_halign(CENTER)`
line is in the tree, it was written *before* `do_measure` existed, and it was a
no-op until `do_measure` landed. Nothing was left out; the second one was simply
built in the wrong order.

Measured on a real window (`tests/test_organ_strip_is_centred.py`, broadway):

| | `reported_natural` | content left | content right | verdict |
|---|---|---|---|---|
| as shipped, 1024px | 479 | 130 | 131 | **centred**, 0.5px off |
| `halign` -> `FILL` | 479 | 0 | 258 | 129px off |
| `do_measure` removed | **815** | 1 | 260 | 130px off |

815 is the exact figure this section recorded for the un-overridden flow box, so
the historical root cause is reproduced rather than approximated, and 129px here
against 86px at 1280px is the same defect at a different column width.

**Measure the cells, not the strip's own box.** Under `halign=FILL` the box is
trivially centred - left 0, right 0 - while the lights sit hard against the left
with 258px of nothing beside them. A centring check written against the box
reports "centred" for the broken layout and passes; the first version of that
test did exactly that and stayed green under mutation until the FILL control
was written.

**Three defects the second full run found, all of them mine.**

- **`show()` replaced `present()` on the activation path.** The start-hidden
  work (`_start_hidden` + `toggle-start-hidden` + `show-window`) swapped
  `self.window.present()` for `self.window.show()` in two places. `show` only
  maps a window; `present` maps it **and** asks the desktop to raise and focus
  it. The assistant therefore opened behind whatever was already in front, with
  nothing wrong-looking to explain it — and
  `tests/test_sense_scheduler_is_live.py` failed on a stub window that only ever
  implemented the older call, which is how it surfaced. Both sites are `present()`
  again, `show-window` included: "show the window" from a tray icon or a
  notification means raise and focus.
- **The Diff preview tests asserted on process-wide state they did not own.**
  `recent_changes()` reads the undo ring, and *any* earlier test that wrote a
  file through `write_text_file` or `edit_file` added a pre-image to it — so
  `assert len(pairs) == 1` was really asserting that no other test in the
  process had ever edited a file. Measured: 3 failures in a chunked run, 0 in
  isolation. The helper now empties the ring first, through the module's own
  `_save` so the on-disk format stays the one the module writes.
- **The slash-command menu had no test at all**, and it is the one part of that
  UI whose reachability nobody had ever checked: the registry tests assert each
  command runs, and nothing asserted that typing `/` opens the menu. New
  `tests/test_slash_menu_opens.py` drives a real window in a real main loop,
  inserts a real `/` through `insert_text()` (not `set_text()`, which emits no
  `changed`), and presses a row. Three mutations confirm it can fail: the menu
  never pops up (caught), it never pops down (caught), and `/diagnostics` is
  neutered (caught, 2/2 — after one false start recorded below).

  **The first version of that last assertion was vacuous and measured green
  against a dead command.** It asserted `"diagnostics" in window._surface_pages`
  — but the window **pre-builds every surface in an idle callback**, so that key
  is present from startup whatever the command does. Asserting on the click at
  all was also the wrong shape: a row *completes* the text (`/diagnostics `) and
  hands focus back, which is cline's behaviour and the reason the menu is a
  discovery aid rather than a one-click macro. It now asserts the completion and
  then that submitting it brings the Diagnostics page to the front.

**And two test bugs of my own that a green suite was hiding.**

- `_CONSENT_NAMES` in `test_skill_gates_are_enforced.py` was missing
  `vision_sense_enabled`, so a correct skill (`screenshot.py`, which gates by
  reading `config.vision_sense_enabled` — a property that returns
  `sense_allowed("vision")`) was reported as ungated. The control test then made
  it worse by demanding the *implementation detail* (`"sense_allowed" in names`)
  rather than that a gate was consulted at all. Fixed to
  `set(_CONSENT_NAMES) & gated`, mutation-verified: deleting the gate from
  `screenshot.py` fails three tests including this one.
- `test_paths_read_as_paths.py` asserted a path appeared unbroken in a label
  that **wraps mid-path at the hyphen** ("shani-\nchronoa"), so it read as "the
  panel stopped showing the path" when the panel was showing it perfectly.
  Pre-existing — it fails identically with `gui/style.py` unmodified from HEAD.

**A test kept reading a declaration out of a class that no longer holds it.**
`test_dead_ends_have_buttons.py::test_every_settings_target_is_a_page_that_exists`
parsed `inspect.getsource(SettingsWindow)` for a `register([...])` call and
checked five settings page ids against it. Two things moved under it, and it was
reading neither: the declaration became `_declare_families()` at module level
(still called at the bottom of the module, so still at **import** time — nothing
changed about *when* registration happens, which is why `pages.py` can now also
call it on demand for a lazy `--show-page`), and the call passes
`list(_SECTION_FAMILIES)` rather than an inline list literal. The class source
then contained no `register` call at all, so the scan read an **empty set** and
reported five working buttons as dead ends.

Measured both directions: it fails against the current module and passes against
the pre-change one, which is how a *reader* defect gets told apart from a
*behaviour* defect. It now resolves the module-level table and additionally
asserts the registry call passes that table — the stronger claim, because a
literal-list scan could not tell a complete table from one nothing registers.
Two mutations confirm it can fail: dropping a family id, and replacing
`list(_SECTION_FAMILIES)` with `[]`.

**`RESERVE_DISTINCT_KEYS` was discarding facts.** The reserve loop kept *one*
fact per key and `continue`d past every other, so two true facts about the same
subject lost one — and it broke a pre-existing ordering test. The reserve's
stated purpose is that a bulk of one key cannot starve another subject; two
passes (one per distinct key, then fill) gives that without dropping facts. This
is the failure this file keeps describing under another name: a loop written to
*reorder* silently *filtered*.

**`execute_tool(name, args, origin=…)` broke a two-argument test stub.** The
stub was out of date, not the call.

**`_apply_narrow_layout` re-opened the panels the person had just closed.** Its
"widening" branch ran on every width notification rather than on a transition,
so a late notification undid the toggle. Measured 2 of 5 runs failing with it,
0 of 5 without; now keyed on a `_narrow_applied` bucket.

**Two `test_approval_ux.py` tests proved grant scoping through
`bypass_immune`'s back door.** Both used `delete_file`, whose session grant is
now ignored on purpose (T2.6), so the narrow-grant property they were written
for had no witness left. They now run on `edit_file`, which is not immune, and a
new one asserts the immunity itself — through dispatch, because an assertion on
`evaluate()` alone reads the ignored grant as "granted".

**`list_services` called a healthy machine broken.** `failed_only=True` with
nothing failed returned "systemctl returned no service units, which is not
expected on a running system" — the anomaly sentence, on the one input where
an empty answer is the good one. The test that caught it had the mirror-image
fault: it required this machine to *have* a failed unit, so it failed on a
healthy box and would have passed on a sick one.

**One thing deliberately left alone, with the numbers.** `tests/test_sidebar_toggle.py`
is flaky for an environmental reason: it measures on-screen layout, this runner
has no compositor, so the sidebar's slide animation is advanced only when a
frame happens to arrive (4 of 6 runs pass with the harness fixes; 4 of 6 with
`window.py` **unmodified from HEAD**, which is how you show the residue is not
yours). Two harness fixes there are real and measured: the width is now *stated*
(`ChronoaWindow` defaults to 460x640, below the sidebar's collapse threshold, so
a harness that takes what it is handed measures the drawer contract while
asserting the column one), and a control asserting the header toggle is
unreachable in the drawer state was removed because today's measurement
contradicts it (at 1100px the overlay is not allocated over the header: header
y=0..46, sidebar 0x0, toggle mapped) — it also failed against `window.py`
unmodified.

**`test_at_1280_both_columns_are_back` was not flaky — it tested a path no
headless display can reach (2026-10-09).** It failed 5 of 5, and also with the
whole session's work `git stash`ed, so it was pre-existing and unexplained.
Measured rather than diagnosed:

| display | allocated | rail | result |
|---|---|---|---|
| `broadway :96` | **1024x768** | hidden | fails |
| the real X11 (`:0`) | 1280 | visible | **18 passed** |

**Broadway caps windows at 1024x768 and the rail appears above 1040px**, so the
wide-rail path sits sixteen pixels above what any broadway display can be — and
at the 1024px actually achieved the rail *correctly* hides. The product was
right on both; only the harness was wrong. `tests/test_now_rail.py` now picks the
backend **before** `gi.repository.Gtk` is imported, because GDK chooses one at
GTK init and no fixture can change it afterwards. With no wide display it
**fails and names the number** — never a skip, since a green skip would leave
the rail's entire wide path unasserted on every headless machine, which is where
it is least exercised. It is `pytest.fail`, the same "a skip reads as coverage
and is not" rule the sidebar test above turned on itself.

**The rail's threshold was not lowered to 900 to make broadway satisfy it.**
Measured: with `RailBreakpoint.apply`'s `max_width` at 900 the test *still*
fails on broadway, because the allocation cap is what binds — so the wrong fix
would have bought a failing test *and* a worse product constant.

**My own probe was wrong first, and the failure looked like the product's.** The
first version read `xdpyinfo`'s `dimensions: 1920x1080 pixels` as
`int(line.split()[1])` — the whole token `1920x1080` — so `int()` raised, the
`except` swallowed it, and the probe answered *"no display on this machine"*
about a 1920px screen while taking **15.1 s**. Fixed to split on the `x`; the
same probe returns `:0` in **0.1 s**. A probe that is always False is the same
defect as a test that always skips, and here it read as a confident verdict
about the product.

Its settling step is a **fixed pump, deliberately not a convergence loop**:
with animations on and no compositor the state after "show the panels" is
correct while the column measures 0px, and that state is *stable* — so "wait
until two readings agree" is satisfied by the broken frame, and the test then
reports a layout that has not happened. `gtk-enable-animations` off is what makes
it reliable (3 of 3 recovered, against 0 of 3 with it on); `queue_draw`,
`queue_resize`, `present()` and `set_default_size` were each tried as a nudge
first and none moves it, because the widget waits on the frame clock.

**And the run itself nearly produced nothing.** It was launched as
`pytest … | tail -60`; after 45 minutes the shell was still alive with an empty
log, because the pytest process had exited normally while a **forked child still
held the write end of the pipe**, so `tail` never saw EOF. `kill <that pid>`
recovered the entire summary. This is what the "44-minute run keeps getting
lost" note above actually was: **a suite whose output is piped is at the mercy
of any process that inherits the pipe.** Write to a file, then read the file.

## Two UI gaps verified by looking at them, and what looking found (2026-10-07)

The harvest work was verified by behaviour throughout — tests drove the real
paths and the mutations were confirmed to fail. That is not the same as having
*looked* at it, and this repository's own history is that a widget can behave and
still be broken: the organ strip passed its width test while rendering as one
run-on string. So both new surfaces were rendered and read.

**The slash-command menu** (`tests/test_slash_menu_opens.py` added first, because
it had no coverage at all). Rendered with the menu open: six named commands,
each with a plain-language summary, anchored under the composer — and one defect
the tests could not see. **A summary ran straight into the popover's rounded
corner** with nothing between the last letter and the border, so "Show what
Chronoa changed, file by file" read as a sentence cut in half. Ten pixels of
right margin; the menu went 398px -> 408px and now has visible padding.

Three harness facts worth keeping, each found by the attempt before the real one:

- **A `Gtk.Popover` is its own surface.** The first capture was a 1280x900 image
  of the *window* with the menu reporting `visible: True` and six rows, and no
  menu in it. Capturing the popover itself is what shows the menu.
- **A widget captured on its own looks invisible.** `WidgetPaintable` of the task
  card produced near-white text on transparency: the theme's foreground with
  nothing painted behind it. Indistinguishable from a legibility defect, and not
  one — the window has to be captured and the card cropped out of it.
- **Pump the loop before capturing.** Without a compositor nothing guarantees an
  allocation, and `snapshot.to_node()` on an unallocated widget returns `None` —
  "NO NODE", which reads like a rendering bug.
- Also: `ChronoaApplication` hardcodes the single-instance name
  `dev.shani.chronoa`, so a render collides with a running app and fails with
  "Failed to register: Timeout was reached". The harnesses use a minimal
  `Gtk.Application` with their own id, as `_render_one.py` already does.

**The task card.** Rendered with three seeded tasks: all three states read
correctly and are distinguishable **without colour** (ellipsis / circle / warning
triangle, plus the words "doing" and "blocked"). One real gap: the blocked row
said "blocked check the backup" and the reason was **only in a tooltip** — which
needs a hover or a keyboard focus, never appears on touch, and is in no
screenshot. `blocked_by` exists to say what is in the way, so "waiting on disk
full" is now on the row, dimmed, wrapping, with the tooltip kept.

**And the change immediately regressed something the render caught and the tests
did not:** the first version returned a fresh vertical box holding the two
labels and left the icon behind, so the next render showed "blocked" with no
warning triangle — the one marker distinguishing it from "doing" without colour.
The head box goes *inside* the row rather than being replaced by it. Both halves
are now covered: the test asserts the reason is in the rendered text **and** that
the blocked row still carries `dialog-warning-symbolic`, and each was confirmed
to fail when its half is removed.

## open-intelligent-ui, seventh pass: two transfers, from four files nobody had opened (2026-10-11)

The sixth pass recorded a negative over `page.tsx`, `layout.tsx`, `map.tsx`,
`vector-basemap.ts` and the spec — and had not read `src/lib/route/store.ts`,
`src/lib/travel/map-utils.ts`, `src/lib/prompt-options.ts` or
`src/lib/library.ts` at all. Going into those found two real defects. Write-up in
**`../harness-study/HARVEST-OIU.md`** §10; the argument for this file is that the
first six passes read the files a summary named, and the gap was in the four it
did not.

**1. `senses/location.py` checked nothing about the coordinates it reads.** OIU's
`coordinate()` validates a pin a *model* supplied — a real number, finite,
`|lat| <= 90`, `|lng| <= 180`. Chronoa reads coordinates from *devices*, and
gpsd's own man page says *"lat, lon: numeric, may be null"*, so its allowed
shapes include a null. Measured on the real parser before the change:

| gpsd sends | before |
|---|---|
| `"lat": 999.0` | ACCEPTED |
| `"lat": NaN` | ACCEPTED (renders as `nan` in every sentence) |
| `"lat": "18.5"` | ACCEPTED, coerced |
| `"lat": true` | ACCEPTED as 1.0 |
| `"lat": null` | **`TypeError` out of the sense** |

`locate()` and its three consumers (`get_location`, `weather._here`,
`maps._here`) had no guard, so a TPV with `mode >= 2` and a null lat answered
"where am I" with a traceback. `mode >= 2` is gpsd's own claim of a fix, and a
fix is not a position — the "take the service's word for it" failure this file
records for `privilege` trusting `dpkg-query`, reached from a new direction.
`_coordinate()` / `_on_the_planet()` refuse all five shapes and name the values
the device sent; returning `None` rather than raising is what lets `locate()`'s
loop hand the question to GeoClue. The GeoClue branch has the same check,
because its regex captures whatever the text says.

**2. The Diff panel's file list was reachable by a pointer only.** OIU's
`createPinElement` is three lines — `tabIndex = 0`, `role = "button"`, and *both*
click and keydown (Enter/Space) calling one `onSelect`. Chronoa builds the file
list as a `Gtk.Box` of `Gtk.Box`es with a `Gtk.GestureClick` each. Measured on
the built row: `get_focusable()` **False**, `grab_focus()` left `has_focus()`
**False**, one controller, the GestureClick — while the Accept and Reject buttons
*inside the same row* were focusable. A keyboard user could act on a file they
could not select, and selecting is what decides whose diff you are reading.

The scan that found it (`GestureClick` across the tree) returned **one** hit, so
this is one dead control and not a pattern: every `Gtk.Button` and every
`Adw.ActionRow`-plus-button in this repo already handles both because GTK does it
natively. The row is focusable now, claims `GTK_ACCESSIBLE_ROLE_BUTTON`, carries
an accessible label, and Enter/keypad-Enter/Space call **`_on_click`'s own
action** rather than a second copy — two copies is how the two drift. The handler
returns `False` for anything that does not mean "activate", or it would swallow
every key press in the window.

Two API facts measured on the installed library, not remembered:
`Gtk.AccessibleProperty.ROLE` does not exist (it is `set_accessible_role`), and
`get_accessible_property` does not either — the second is this file's own
recorded probe defect, so the label is asserted by recording `update_property`
at the class level before construction, the only place the call can be caught.

**Not taken, with reasons in §10.3:** `wikiCache`'s in-flight `Map<string,
Promise>` (every skill call is its own child process, so a module-level Map is
empty in the next one), `addStop`'s plain-field allowlist (Python's `to_dict()`
enforces the same thing structurally), `getStopKey` (`variable_name` is required
by the schema, so the empty case cannot arise), the *"do not present weather as
verified"* prompt rule (Chronoa's answer is a control: `NO_TRAFFIC`, provenance
fencing, the egress panel), and `localPromptOptions` (`tool_select` is already
per-request).

`tests/test_location_coordinates.py` — 35 tests, **14 of 14 mutations confirmed
to fail**, including the range check, the type check, the finite check, the
refusal naming the device's own values, the GeoClue branch, the row's focus, the
label, the role, the key controller, the over-fix that swallows every key, and
the mouse path being dropped while the keyboard one was added. The one partial
is an equivalent mutant by construction: dropping the int check leaves every
float fix passing, which is what that mutation can reach.

Verified with the other three suites (`weather_location`, `inline_diff_in_chat`,
`ui_surfaces`) and the established sweep. **7 failures in the sweep are
pre-existing** and were checked with the sanctioned one-file swap — the same 7
fail with both of this pass's source files at HEAD. All 7 name the concurrent
session's new skills (`lan_phone`, `screen_to_phone`), and one of them says so in
its own message: *"if it is not, it is another session's work in progress and
this failure is not about your change."*

## oh-my-openagent's expect-sabotage: the mutation harness, in the repository (2026-10-11)

Third pass over `harness-study/oh-my-openagent`, into
`script/qa/desktop/linux/expect-sabotage.ts`. It verifies a *single*
deliberately-failing scenario, and its own comment states the rule:

> A failing run is not a successful negative control unless the intended opt-in
> check failed **and** both the process and fixture directory were cleaned up.

Chronoa's `AGENTS.md` carries the same rule from the other side - *"a negative
control that cannot fail is not a control"* - and the enforcement has been a
harness written into `/tmp`, run by hand, and lost. **Eight existed in one session
on 2026-10-11 and two had already been wiped by a restart when this was written.**
Every commit this session made that says "N of N mutations confirmed to fail" was
citing evidence that no longer existed - including five of its own.

`tools/mutate.py` is that rule as a tool, and `tools/mutations/` holds the sets.

    python3 tools/mutate.py            # every set (minutes)
    python3 tools/mutate.py edit_file  # one by name
    python3 tools/mutate.py --list     # the honest inventory
    python3 tools/mutate.py --check-anchors   # seconds, no tests run

**Eleven sets, 129 anchors** - `activity`, `clip`, `edit_file`, `finite`,
`fm_radio`, `goal_edits`, `ir_remote`, `location`, `maps`, `stale`, and
`file_locks` from a concurrent session. Every anchor still matches the code.

**The five sets that were lost to a restart have been rebuilt — from the
committed tests and code, not from memory.** `goal_edits`, `clip`, `maps`,
`ir_remote` and `fm_radio` were `/tmp` files whose evidence this file said was
gone, and the honest remedy was to re-derive them where every claim is checked
against something real: the runner asserts each anchor occurs exactly once in
the *current* source and that a named witness fails, so a set that drifts from
the code is an error rather than a silent survivor. Writing them found
**eleven witnesses that could not fail**, which is the real return on the work:

- `test_the_approximation_is_stated` asserted only `"_joins" in dir(M)`. It
  now asserts the docstring actually says `approximation` and `stdlib`, read
  off `_joins.__doc__` so a comment in the test cannot satisfy it — **a claim
  about the code's own limits was the exact shape of the `fmdsp` comment that
  caused a real bug.**
- `test_the_trailer_space_is_doubled` asserted against `I.RC6_TRAILER_SPACE`,
  the module's **own constant**, so the constant could be halved and the test
  stayed green. It now pins the published literal. Writing that assertion
  **failed**: it said `889` is not `2 × 444 = 888`, which is right — RC-6's
  trailer is 889 µs, not the arithmetic of two half-periods, and a number that
  looks derivable and is not is this file's own `privilege`/`mokutil` lesson.
- `test_the_carrier_is_the_rc6_one_and_not_nec` pinned the constants but never
  touched the code that *chooses* one, so an RC-6 frame written at the NEC
  carrier was green. Now the skill's own `_run` is driven and its output
  checked.
- Nothing tested `_run`'s carrier choice at all, and nothing fed a capture
  **longer** than a frame or a leader of the wrong width — so two refusals had
  no witness. Both now do.

Seven rules the runner enforces, each of which cost a green run against
unmutated code before it was written down:

- **Every anchor must occur exactly once**, asserted *before* the write. A
  `str.replace` that silently matched nothing has twice produced a mutation
  reported as a survivor. A *pair* of anchors is applied in order with the same
  check, for the two loops that are not adjacent and must change together.
- **The file is restored in a `finally`**, so a crash or a timeout leaves the
  tree as it was found.
- **The verdict is read from the summary line, never the return code** - this box
  segfaults at GTK teardown *after* the summary, and a `-11` read as a test result
  would fail every GTK file.
- **A mutation is "caught" only when a named witness fails**, and a whole-file
  red for another reason is reported as a survivor *and* names what failed. The
  first run of this found exactly that: a witness named a base id while the
  suite's ids were parametrized, so five tests were failing for the right reason
  and the run said "survivor". Witnesses now match as a prefix of a parametrized
  id, with a `[` guard so `test_foo` does not match `test_foo_bar`.
- **A set that raises on a rotated anchor still prints the tally** - a set whose
  anchor rots is a result to fix, not an abort that loses the other sets' answers.
- **Nothing here is packaged.** `tools/` is the maintenance surface, as
  `cli_matrix.py` and `task_eval.py` already are.
- **One run at a time, and a killed run says so.** See below; this is the rule
  the harness broke itself.

**And the harness is itself subject to the rule**, in
`tests/test_mutation_harness_can_fail.py`: it reports caught for a mutation a
test really covers and survivor for one nothing covers, it **refuses a mutation
whose anchor does not exist**, it reports a mutation that broke the code under a
witness name that matches nothing, and it leaves a fixture tree byte-identical
afterwards - which is `expect-sabotage.ts`'s cleanup half, for a harness whose
fixture is a source file.

**Two of my own harness defects, both caught by running it.** The runner resolved
its mutation sets from its own location rather than the working directory, so the
self-test's temp tree was ignored and it silently tested production data; and a
bare `exec` of the runner raised `NameError: __file__`, which made the harness
look unimportable. Both fixed, both recorded in the test.

## The harness corrupted the repository, and the two rules that were missing (2026-10-11)

**A `finally` restores a file. It does not cover `SIGKILL`, and it does not cover
two runs at once** — and the harness had neither a lock nor any record of what
it had changed.

Measured, in this session, on this repository's own source: a mutation run
started before a session boundary **was still alive** when the next one began.
Each run held its own copy of the original, so restoring one clobbered the other.
Killing both left **two mutations stuck applied**:

| file | stuck mutation | read as |
|---|---|---|
| `skills/fm_radio.py` | the discriminator's phase wrap deleted | stereo never worked |
| `fmdsp.py` | the lowpass's zero at Nyquist dropped | 34 837x too high at 0.99 fs |

Both restored by targeted reverse replace — **never a path operation** — and
both verified byte-identical to HEAD afterwards. A concurrent session's
uncommitted work in five other files was untouched, which is the whole reason
for the rule.

**Two rules, both tested, and the second is the half a `finally` cannot do:**

- **`_Lock`** — `O_EXCL` on a file holding the owner's pid. A second run is
  **refused and the refusal names the pid**, so a person can tell which run to
  wait for or stop (`--wait` if they would rather block). A lock whose owner is
  gone is taken over **and says so**, because taking it silently is the same
  class as silently deleting a rotated anchor.
- **`_JOURNAL`** — each file's pre-run text is written *before* the first
  mutation and removed in the same `finally` that restores. So a killed run
  leaves a **claim that the tree is not in its committed state**, and the next
  run puts it back and prints which files it recovered. Clearing the journal
  before restoring would leave a window in which the tree is restored and the
  journal still says otherwise — and a run killed in that window recovers
  nothing, which is the entire failure the journal exists to catch.

Both live in `build/mutate/`, which `build/` already ignores.

**Both were then verified on the real thing, not a fixture, within the hour.**
A concurrent session started its own `mutate.py history` run while this one was
mid-flight and **was refused, naming the holder** — which is the lock doing its
job against a real invocation rather than a synthetic one. This run was then
killed, and **the other run recovered its stuck mutation automatically and
printed what it had restored.** Nothing noticed either event; that is what a
mechanism that works looks like, and it is the opposite of the two stuck
mutations an hour earlier.

**`RUN_TIMEOUT` was 900 s, and that was a bug in the other direction.** A
mutation that turns a bounded wait into an unbounded one does not fail — it
*hangs* — and 900 s per mutation turns an eight-mutation set into two hours of
nothing. The first version of the lock mutation did exactly that (`if False:`
instead of the deadline test, so the loop spins on `FileExistsError` forever),
and it was found by killing the run rather than by any check. Now 300 s, four
times the slowest real set.

**Three things this found about the checks themselves, all of which were
assertions that could not fail:**

- **The cheap rot check read files a run was mutating.** `test_no_anchor_has_rot`
  reported four anchors as rotted in sets that had run green minutes earlier —
  because it read `fm_radio.py` and `fmdsp.py` *while a run had them
  rewritten*. A check that reads the files a run is mutating cannot tell a
  mutation from an edit. The check now lives in the runner as
  `--check-anchors`, under the same lock, reusing `_apply`'s own rule instead of
  restating it.
- **`test_the_runner_lists_its_sets` named four sets in a tuple**, so the five
  added later sat in the repository with the test claiming to be the inventory
  and not mentioning them. A hand-kept list of what a directory already knows is
  a list that rots. It reads the directory now, and also checks the reverse
  direction: every listed set names a test file that exists.
- **`test_a_stale_lock_is_taken_over_and_says_so` passed while proving nothing.**
  `_Lock.__init__` resolves `STATE_DIR` when the object is built, and the test
  never pointed it at the fixture — so the lock was taken in the repository's
  `build/` and the stale file it wrote was never read. The assertion that caught
  it is now `lock.path == state / "run.lock"`.

**The expensive half can no longer run every time, and pretending otherwise is
how it went missing.** The inventory was under two minutes with four sets and is
over ten with eleven, and `fm_radio` alone synthesises 200 kS/s of signal per
mutation. `TestNoSetIsLeftBehind.test_every_set_runs_clean` ran it in the
default suite, where it quietly outgrew `_run`'s 600 s cap — so the guarantee
was gone and the test reported a **timeout**, which is a failure about something
else entirely. It is now marked `slow` and excluded from the default run
(`pytest -m slow` to include it); the anchor check is the half that runs always,
and it costs under a second.

`tools/mutations/harness.py` aims the harness at itself, so "the harness cannot
lie" is a claim with witnesses. **That is safe to run** because `run_set`
restores the file it mutated and the journal covers `tools/mutate.py` too.

**What is not claimed.** That the *full* inventory is green right now: the last
clean end-to-end figure was **48 mutations, 48 caught** across four sets, and the
five rebuilt sets have each been run individually and clean. `--list` and
`--check-anchors` are the two things a fresh clone can verify in seconds; the
full run is the slow, deliberate half.

`build/` is gitignored for the runner's redirected `XDG_*` directories - a
mutation run must not write into the developer's real home, which is the same
rule `XDG_STATE_HOME` already carries in the test suite.



Second pass over `harness-study/oh-my-openagent`, into
`packages/hashline-core/src/autocorrect-replacement-lines.ts`. Its
`restoreOldWrappedLines` answers a question Chronoa answers badly: a model
re-emitting a block it means to leave alone strips trailing spaces, re-wraps, or
drops a continuation token, so its transcription is not the bytes on disk.

**Chronoa's version of the problem is a refusal, not a spurious diff.**
`edit_file` requires `old_string` to match exactly - the right rule, because a
whitespace-tolerant *write* would silently change lines nobody asked to change.
Measured before this change, a three-line function with trailing whitespace on
its second line:

    the model's old_string -> exact matches: 0   (the whole edit is REFUSED)

Safe, and a friction the model cannot see past, because the difference is
invisible in the text it wrote.

**Taken, in two halves, and the second is what makes it honest:**

- **The match.** `_match_ignoring_whitespace` finds the run of *whole lines*
  whose tight form (whitespace removed) equals the needle's, and returns **the
  file's own bytes** - trailing whitespace included - so the substitution is
  still exact. More than one match comes back as `count > 1`, so the existing
  refusal with its line numbers is unchanged.
- **The write.** `_restore_kept_lines` puts back, *inside the region being
  replaced*, the runs the model was quoting. OmO searches the whole original
  file; narrowing it to the region is what makes it Chronoa's - a correction can
  only put back what was in the text it was asked to change.

**A line-aligned match, and the bug that proved it.** Matching on *characters*
stops wherever the tight characters run out - measured, a needle whose last line
ends in `)` matched a span one character short of the newline, and
`text.replace(old, new)` left the original newline behind, so a real edit came
out `...items)\n\n`. The match is a run of whole lines now, and the trailing
newline the run consumed is kept in the span.

**Two equivalent-mutant traps, both the recorded `str.replace`-that-mutated-
nothing shape in a new place.** The "a single line is treated as a quote" mutation
first targeted the region scan alone, then the out scan alone - each is a no-op,
because a one-line span is only restorable if *both* loops allow it. They are not
adjacent, so that mutation is now a pair of anchors. And the blank-line witness
used `"alpha\n\nbeta\n"` for both region and replacement, which leaves the region
with no usable key at all - the function returns early and the test passes with
or without the check. The witness is a region holding a real run plus a
replacement with a blank inside it.

**Four of my own witnesses could not fail, and the mutation run found all four** -
the short-needle list used a file that contained none of them; the ambiguity
witness used two runs that differed in *letters*, so there was no ambiguity to
find; the restorable-span witness used a nine-character run that passes the floor
anyway; and the boundary case used `return 1` (seven tight characters) instead of
`return` (exactly six). Each is fixed, and each reason is in the test.

`tests/test_edit_what_the_model_meant.py` — 30 tests, **13 of 13 mutations
confirmed to fail, 0 survivors**, covering the retry, the ambiguity refusal, the
line alignment, the trailing newline, the write-side restore, both floors, the
blank-line rule, the confinement to the region, and the skill end to end
(a preview writes nothing; an absent string is still refused; `replace_all` still
means all; an unparseable result is still refused; and a model that copies the
bytes exactly gets byte-identical behaviour to before). 235 passed across the
edit, goal, office, capability, senses and file-write suites; 0 stray `.pyc`;
0 real-state-dir writes.



`harness-study/oh-my-openagent` (OmO — an OpenCode plugin harness, cloned this
session; `packages/boulder-state`). 7,491 TypeScript files, and one mechanism in it
names a defect Chronoa had with no name for it. From its own notes:

> `completeBoulder` is the only completion transition, so a work whose session
> died abnormally would stay `active` forever (#8413).

**Chronoa had the identical hole.** A run mid-`advance` when the process died keeps
`phase=RUNNING` with a frozen `updated_at`, and the panel rendered
`_PHASE_WORD[RUNNING]` — "in progress" — forever. Measured before the change,
through the real store and the real panel: **a run last touched 30 days ago still
said "in progress - 1 of 2 steps"**. Nothing in the tree aged it out; the word
"stale" appears once in the panel and is about a stale *card*, not a stale run.

**Taken, with the four rules that make it safe.** Boulder demotes an `active` work
to `paused` and stamps `stale_since`; its readers are unchanged; `selectActiveWork`
returns a stamped work to `active` and drops the stamp; and "a work with no activity
evidence at all is stale". All four are in `goals.reconcile_stale_runs`:

- only RUNNING runs are considered — `AWAITING` may legitimately be weeks old, and
  `PENDING` has not started;
- a run already stamped is left exactly as it is, so re-stamping cannot move a
  run's stopped-time forward on every refresh;
- nothing stale means nothing written — the same objects come back, so a store
  write happens only when something changed;
- a threshold of zero or less is ignored, because it would mark every live run as
  stopped the instant it was created.

**The part that is Chronoa's own, and the reason `Phase` was not reused.**
`Phase.AWAITING` means "parked, somebody outside has to answer", which is not what a
dead run is, and a new `Phase` would ripple through `_PHASE_WORD`, `LIVE`,
`manage_goals` and every reader. So the stamp rides on a new `stale_since` field,
`phase` stays RUNNING, and only the sentence changes. Boulder draws the same line
between its `paused` status and its `stale_since` stamp.

**And the trap the write itself sets, which is the reason for `save(stamp=False)`.**
`GoalStore.save` stamps `updated_at` — which is the *activity* signal the demotion
reads — so writing a demotion would reset it and the card would read "last moving in
the last hour" about a run dead for a month. Measured without the switch. `advance`
is the opposite: it *is* activity, so it stamps, and it clears the stamp because
resuming a run is the same operation as starting one.

**A corrupt file took the whole store down, and the new tests found it.**
`from_json` passed `created_at`/`updated_at` straight through, so
`"created_at": "yesterday"` made `_last_activity` raise `TypeError` — and
`GoalStore.list` raises on the first bad row, so the panel reported *"the goal
store could not be read"* for **every run in it**. `_stored_number` now coerces to
0.0, which is "no activity evidence", which the reconciliation already treats as
stale — so a broken timestamp demotes one run instead of blanking the panel.

**Two of my own tests could not fail, and the mutation run is what said so.** The
"only `updated_at` counts" survivor was witnessed by a run with *both* timestamps
zero, which the mutation leaves unchanged — the real witness is a run whose
`updated_at` field was lost but whose `created_at` is recent, which `from_json`
actually produces. And the zero-threshold witness was a 30-day-dead run, which a
zero threshold also stamps; the live case is the one that can tell them apart. Both
were mine, both are fixed, and both are recorded in the harness.

**One consolidation, caught in the same session.** `_stored_number` was written with
its own copy of the finite test — a **fifth** copy, in the same commit that
consolidated the other four — and `test_no_third_copy_exists_anywhere` noticed on the
first sweep. It uses `sysfs.finite` now. The grep is the reason this is caught within
an hour rather than in six months.

`tests/test_stale_goal_runs.py` — 34 tests, **12 of 12 mutations confirmed to fail,
0 survivors**, covering the predicate, the four safety rules, the persisted stamp,
the `stamp=False` write, `advance`'s two halves, and the corrupt-file path. 295
passed across the goal, finite-check, location, diff, capability, senses, cpu, media
and surface suites; 0 stray `.pyc`; 0 real-state-dir writes.



The pass above added `senses/location.py`'s finite-number test by reading
`skills/media_control.py`, **which already had the identical three lines**. So the
consolidation-worthy duplication this repo keeps recording (`files.py`: *"the
earlier `wpctl` triplication... three copies, three timeouts, and a divergence
nobody notices until one of them is wrong"*) was **made worse by the fix that
should have prevented it** — a second copy added while the first existed, by an
author who had just read it.

`sysfs.finite()` is now the one implementation, and `sysfs.read_float(path,
field=0)` is the float sibling `read_int` never had — which is what
`cpu.read_uptime` was hand-rolling (`try: float(raw.split()[0]) except
(ValueError, IndexError): return None`) until it went through `sysfs` too. The
extraction has a real caller, so a new helper is not born dead, which is the
dead-code class this file records most often.

**A grep for the duplicated pair found a third and a fourth copy, and the control
that did it is the interesting part.** `triggers/sources.py` carried the exact
same test on a deadline read from a hand-editable JSON file — a `json.loads` reads
the bare tokens `NaN` and `Infinity` (Python's own `json.dumps` writes them), and
`expires_at - moment` then yields `nan`, which compares False against every
threshold, so the signal reads OK forever and **a deadline is never reported**.
Consolidated. `triggers/common.py:_clamped_seconds` also tests for NaN and is
deliberately **not** folded in: its contract is different (NaN returns the rule's
`default`, not a refusal, and it lets the `min`/`max` pair pin an infinity to
`high`), so sharing it would change trigger behaviour from a de-duplication pass.
Recorded rather than tidied.

**Three of my own tests could not fail, and the mutation run is what said so:**

- `test_no_third_copy_exists_anywhere` was **too broad** on its first version —
  its pattern matched `triggers/common.py`, which is a different contract, so it
  failed on correct code and the "fix" would have been widening it into nothing.
  Narrowed to the exact duplicated pair, with the third module's difference
  written into the test's own docstring.
- `test_an_empty_file_is_none_not_zero` was an **equivalent witness** for the
  "empty file is no longer None" mutation: `"".split()` raises `IndexError`,
  which the `except` already covers, so the test passed either way. The real
  witness is the unreadable path, where `read_text` returns `None` and
  `None.split()` is an `AttributeError` — named in the harness instead.
- `test_uptime_against_the_real_file` **passed with the local copy restored**,
  because it asserts the value and both implementations return it. Only a
  source-level assertion can tell a shared helper from a fourth duplicate, so
  `test_uptime_reads_through_the_shared_reader` reads `inspect.getsource` — and
  the mutation that restores the local copy fails it.

**Two claims checked and NOT made.** `float("nan")` reaching
`skills/read_video.py`'s `divmod(int(seconds), 60)` does raise (`ValueError:
cannot convert float NaN to integer`, and `OverflowError` for inf, both outside
the `except (TypeError, ValueError)`), but **ffprobe could not be made to emit
one** — six input shapes tried here (broken mp4, empty file, valid mp4, audio-only
m4a, bad ts, and no file at all) all produce `N/A` or nothing, so the path is
unproven rather than live, and it is not claimed. Second: `local_llm`'s `tps >
1000` guard does not exclude a NaN (`nan > 1000` is False), so `weights * tps`
would be `nan` — but llama.cpp's timing fields were not observed to carry one
either, so that is recorded as the same shape and not fixed.

`tests/test_one_finite_check.py` — 35 tests, **10 of 10 mutations confirmed to
fail**, covering the shared predicate, both call sites growing their own copy
back, the fourth copy reappearing, the `field` argument (`read_uptime` taking the
idle column would double the machine's age), and the extracted caller. 546 passed
across the location, diff, capability, triggers, scheduler, cpu, media, senses and
surface suites; 0 stray `.pyc`; 0 real-state-dir writes.


A single full run is not a reliable way to get a number out of this suite on
this box: over the course of one afternoon it lost its output three ways, and
only the third is obvious.

| what happened | why | what it cost |
|---|---|---|
| `pytest ... | tail -60`, log stayed empty after 45 min | the pytest process had exited **normally** and a forked child still held the write end of the pipe, so `tail` never saw EOF | the whole summary, sitting in a buffer |
| SIGTERM twice | the process group was killed | 45 minutes each |
| `Fatal Python error: Aborted` at ~60% | `g_application_run` asserts inside `tests/test_setup_button_is_reachable.py:123` | everything after that test |

**Write the output to a file, not down a pipe**, and **run it in chunks**. Eight
chunks of ~36 files, one summary file each: **36 failed, 5845 passed, 39
skipped**, no abort. A crash then costs one chunk instead of the run.

Of those 36, **27 were the environmental list above**, unchanged. Ten were real
and are fixed; the residue after the fixes was re-measured over every file that
had failed, and came to **26 failed / 451 passed / 2 skipped — every one of the 26
on the documented environmental list** (browser_window_probe 7, setup_extras 6,
sense_idle 3, sandbox_seccomp 2, permission_decisions 2, everyday_skills 2,
sandbox_profiles_live 1, input_skills 1, portal_input 1, vision_sense 1).

The abort is **pre-existing and still unexplained**: that test passes alone
(12/12, three runs) and no file pair I tried reproduced it. A dead session bus
was my first hypothesis and it is **ruled out** — pointing
`DBUS_SESSION_BUS_ADDRESS` at a nonexistent socket and caching the connection
with `Gio.bus_get_sync` first still lets `app.run()` return normally. Recorded
as an open item rather than a fix.

### What the chunked run also showed: two order-dependent failures of mine

- **`test_settings_targets_resolve.py` had the same defect as
  `test_dead_ends_have_buttons.py`**: a generator that read the page ids out of
  the `SettingsWindow` class body yielded **nothing** once the declaration moved
  to `_declare_families()`, so every assertion built on it passed for the wrong
  reason. Fixed the same way, mutation-verified.
- **`test_skills.py::test_execute_tool_non_dict_arguments_are_ignored` picked its
  handler by insertion order**, and the first candidate is `airplane_mode` —
  which is consent-gated (`radio-control-enabled`, off by default). So the test
  passed or failed depending on whether an earlier test in the process had left
  that switch on: intermittent in a full run, never in isolation. It now picks
  the first **sorted**, **ungated**, non-read-only handler with no required
  arguments (`audio_output` today) — the point being that the claim is about
  argument handling, and a gated tool never gets far enough to exercise it.


## oh-my-openagent's comment-checker: the checker that could not answer, and its exit code (2026-10-11)

Eighth pass over `harness-study/oh-my-openagent`, into
`packages/comment-checker-core`. It is a hook on every `write`, `edit` and
`multiedit` that runs an **external comment checker** as a subprocess, and its
`runner.ts` is the part worth reading:

> exit 0 = clean. exit 2 = **the check ran and has findings**. Anything else is
> returned as a `failure` carrying the exit code and the checker's stderr, "so
> callers that ignore it keep their behavior; a caller can use it to stop
> re-running a broken checker" (#8850).

The last sentence is the whole content of the module. A checker that **could not
start** and a checker that **ran and found something** are not the same event,
and collapsing them is what leaves a dashboard reporting green across every
skill - the #2463 failure this file already has a scar for.

### Chronoa's argv post-condition had no exit-code contract at all

`verification.verify()` runs a post-condition after a skill call. Its subprocess
branch treated **every non-zero exit as "the effect is absent"**. Measured through
`verification.verify()` before the change, not by reading it:

| the checker prints | exits | was reported as |
|---|---|---|
| "I could not determine whether it happened" | 3 | **FAILED**, quoting that sentence as the evidence |
| "wrapper: checker not installed" | 127 | **FAILED**, quoting it |
| - | 1 | **FAILED** |
| (binary absent entirely) | - | UNVERIFIED, correctly |

**The last row is the tell.** The distinction existed one branch away, for the
binary that is *absent*: `OSError` at spawn gives UNVERIFIED. So the tree could
distinguish "I could not run it" from "it ran and failed" at spawn, and lost it
immediately after. A person reading *"VERIFICATION FAILED: I could not determine
whether it happened"* is being told the action failed by a check that said it
could not tell.

The contract is now 0 = verified, **2 = the check ran and the effect is absent**,
and anything else = the checker failing to answer, reported as UNVERIFIED naming
the code and whatever it printed. **`skills/__init__.py`'s docstring is where a
drop-in author reads it**, because a contract that lives only in
`verification.py` is one nobody dropping a module into
`~/.config/shani-chronoa/skills/` will find - and the trap it has to name is
that **exit 1 is what every unhappy program returns**, so a drop-in checker would
naturally use it for "I could not tell".

**Nothing in this repository declares the argv form** - 60 post-conditions,
every one a callable - so this defines a contract for a documented extension
point rather than changing shipped behaviour. Two tests did change, both recorded
in place: `test_verification.py`'s `['false']` fixture, and
`test_surface_workbench.py`'s `(["false"], "verifies: failed")`. The latter was a
**second copy of the contract** encoded in a surface test by the argv a shell
returns for "unhappy" rather than by the code that means something - an author
reading only that row would have learned that 1 means failed.

### Two more defects fell out of the same change

**`suffix` rendered one fixed sentence for every UNVERIFIED.** "no post-condition
declared", "the post-condition raised KeyError" and "the checker binary is not
installed" reached a person as the same fourteen words, in the module whose whole
purpose is that *I could not determine* is not *false* - so it could not say
*which* it meant. The no-evidence case keeps the sentence verbatim, because
`capabilities.py` and `browse.py` both quote it when explaining why a plain
answer must not carry it.

**And then the change's own hazard, closed in the same commit.** `verdict_from_text`
recovers a verdict from a plain result string, and it looked for the markers as
bare substrings. Now that a checker's own words are quoted back to the person, a
checker printing "route impossible: no gateway answered" would turn its own
**unverified** verdict into an IMPOSSIBLE one on the way back through - a
checker's prose deciding the verdict of the check that read it. The markers are
matched in the shape `suffix` produces them, `(` included.

**The two markers needed two witnesses, not one witness and a hope.** Reverting
the `FAILED_MARKER` match to a bare substring is caught by the first test;
reverting `IMPOSSIBLE_MARKER` the same way **passed**, because every IMPOSSIBLE
string this module produces contains the marker with its parenthesis already.
They are not equivalent - and the mutation set is what said so.

`CHRONOA_VERIFY_ARGS` - the `repr()` of the skill's arguments, passed to a
checker in the environment - was **written in two places and read by nothing,
in this repository or any drop-in**. It is documented now, with the honest
instruction to read it defensively and exit outside {0, 2} if it cannot be
parsed: a checker that was not told what to check has not been told what to
check, and the protocol gives it a way to say so.

`tools/mutations/post_condition.py` - **14 mutations, 14 caught, 0 survivors**,
including the shipped behaviour restored verbatim (a checker that could not
answer reported as the action failing), which is the strongest kind of mutation:
it is not a hypothetical, it is the bug.

### And the same package's slash-command detector: already correct, measured rather than assumed

`detector.ts` strips fenced code blocks before reading a leading `/`, on the
premise that a person pasting a shell snippet should not have its first line read
as a command. Chronoa's answer is `commands.lookup()`, which requires the slash at
the **very start of the trimmed buffer** and is called once, at submit - there is
no per-keystroke detection to be fooled. **Measured, all six cases:**

| the composer holds | lookup | the menu |
|---|---|---|
| `/undo` | runs `undo` | opens |
| `/etc/passwd is on disk` | text | closed |
| `#!/bin/sh` then `echo hi` | text | closed |
| `run /undo please` | text | closed |
| `/1 2 3` | text | closed |

So the defect `removeCodeBlocks` exists for **does not occur here**, and copying
it would have added a second parser of the same text for a case the one parser
already answers. Recorded so the next pass does not re-derive it - the sixth pass
over open-intelligent-ui found the same shape and recorded it the same way:
**a transfer invented to justify another pass is the confident wrong answer this
file keeps recording.**

## Six harness-UI gaps closed (2026-10-07)

Comparing Chronoa's UI against what opencode, cline, Roo-Code, OpenHands and
sayri ship surfaced six things we did not have. All six are now in, each
verified by execution rather than by the feature existing:

1. **Context meter** (`context_meter.py`) - opencode's
   `session-context-usage.tsx` + `session-context-breakdown.ts`. A headline
   (`781 of 8,192 tokens (9.5%)`), a per-segment breakdown (instructions /
   percepts / user / assistant / tool results / tool schemas), and a
   *usable* percent that reserves reply room. Two rules the comparison
   forced: when the window size is unknown the meter says `None` rather than
   inventing a percentage against a made-up denominator, and its divisor is
   *read from* `local_llm.CHARS_PER_TOKEN` - my first guess (4) was wrong;
   the shipped one is 3, and the test that caught it is permanent.
2. **Compaction says what it cut.** `compression.compress()` now reports into
   `last_elision()` (messages shortened, characters removed), the assistant
   fires an `on_compaction` sink event once per turn, and the transcript
   shows a dim row. cline's `CompactionRow`, OpenHands' `CondensationEvent`.
   A local assistant that elides silently looks like it forgot.
3. **A cloud-turn is announced.** `CloudLLMChain` tracks `last_provider`; the
   assistant fires `on_cloud_turn` once per turn; the transcript shows "This
   turn was answered by X, not on this machine." The privacy strip and the
   egress audit existed for this, but neither told a person their sentence had
   left - they had to have known they enabled a fallback at all.
4. **Slash commands.** `gui/commands.py` - kimi-cli's registry, cline's
   `SlashCommandMenu`. Six commands, each with a real action behind it:
   `/new /diff /undo /tasks /diagnostics /help`. `/undo` runs the real skill,
   so the permission layers are not bypassed. A command with no wired action
   is the dead-control class this repo keeps meeting; the test refuses it.
5. **The plan sits in the chat.** `gui/task_card.py` - OpenHands' 3-state
   `TaskItem`, placed above the composer where it cannot scroll away, hidden
   entirely when there is nothing outstanding, and *absent* (not blank) when
   its consent key is off or the store is unreadable - "you have no tasks"
   and "you cannot read them" are different facts.
6. **Tool output is collapsible.** This turned out to already be there
   (`ToolCallCard` has a toggle + revealer); a test pins it so it is not
   "fixed" twice.

## A full visual pass over every page, and what looking found (2026-10-07)

Every page of all three windows (chat, 21 panels, 7 settings sections, 17 wizard
pages, Help) was rendered at 1280x860 and read. `render_ui.py` gained
`page:<window>:<id>` (any registered page, through `pages.show()`) and is now
`NON_UNIQUE` - before that, a parallel batch registered one bus name and every
render but the first exited as a remote having written nothing. `panel:` also
never sized its window, so panel captures were a small window stretched up.
Batch: `xargs -P4` over `render_ui.py`, ~35s for all 49. For the light scheme
set `ADW_DEBUG_COLOR_SCHEME=prefer-light`: the `[theme]` argument sets
`gtk-theme-name`, which libadwaita's style manager overrides, so a "light"
render with it comes back dark.

Defects found only by looking, all fixed and each pinned by a test that was run
against the old code and failed:

- **Red for a choice.** The health vocabulary had no word for "off on purpose",
  so a shut consent key was "Needs attention": five sidebar rows red on a default
  install, and Devices printed "This is a setting on this machine, not a fault."
  under its own red word. `common.STATUS_OFF` ("Turned off", grey) now covers
  consent-off (calendar, devices, privacy mode, background mode, no senses
  granted); an empty calendar/memory/artifact list is `OK`; Machine counts only
  senses that *failed* (refused/absent are by design) and banners only those.
  `tests/test_status_off_is_not_attention.py`.
- **Wizard extras had four buttons, two primary**, and the halves routed
  differently: "Skip the rest" and Languages' "Next: Done" went past Review - the
  only page that downloads - so an extra picked on the way was never fetched;
  "Finish" went to Review. Each extra now has one primary "Back to the list" and
  a flat "Next: <extra>". Cloud keys had two primaries; Next now also saves typed
  keys. Pages are clamped to 640px. `tests/test_wizard_footer_one_primary.py`.
- **Settings had no way to a section but typing.** Section chips call the
  existing `_show_only_family`. `Gtk.SearchEntry` emits `search-changed` at once
  for empty text and *delayed* for typed text, so type-and-clear can deliver only
  the empty event; the chosen section is forgotten on `changed` instead.
  `tests/test_settings_section_chips.py`.
- **Help** drew a second title and close button under the title bar's; that
  space is now a search (`HelpWindow.filter`). Its "switch on *Speak answers
  aloud* to send a notification" was correct and read as wrong: the one key does
  both, so the label is now "Spoken replies and notifications".
  `tests/test_help_search.py`.
- Smaller: the user's bubble filled the row with text pushed right (now hugs
  right, accent-tinted); Setup's `system-run` icon is a gear in Yaru beside the
  Settings gear (now `system-software-install`; Help/Quick Ask icons swapped to
  the conventional ones; sidebar toggles use `sidebar-show[-right]`); panel
  subtitles were a centred narrow block over left-aligned rows; a banner's button
  sat flush on the window edge; Senses rows showed module ids (`cgroup`,
  `rfsense`) - now `common.SENSE_TITLES`, id kept in the tooltip; "Cloud fallback
  when Ollama is unavailable" now names the real condition (no local model
  answers - llama.cpp by default).

**Every tool card vanished from the chat when the reply arrived.**
`TranscriptView._replace_blocks()` removed every child of the turn, and the turn
also holds the tool cards and notice rows, which are added *before* the reply
text because skills run first. Unit tests built cards alone and never followed
one with a reply; a render of the real order showed the reply and no card while
the rail listed the changed file. It now keeps `ToolCallCard`s and `.turn-notice`
rows. On top of that, a single-file write (`edit_file`, `write_text_file`,
`find_and_replace` on a file) shows its changed lines in the card
(`blocks.InlineDiff`, from the undo ring) with "Show full diff" into the panel.
The Diff panel itself, rendered with real content for the first time: it loaded
only at build (panels are cached, so it went stale - now reloads on `showing`),
its count toolbar was a third column over the file list, its diff scroller was
one line tall, every hunk header was wrong (old side read from a removed line's
`-1`), and its fills were light-theme hex colours (white on near-white on a dark
desktop). All in `tests/test_inline_diff_in_chat.py`, driven through the real
`edit_file` skill.

**Layout, conversations and sidebar (same day, third pass).** Renders now run on
a headless `gtk4-broadwayd :5` (`GDK_BACKEND=broadway BROADWAY_DISPLAY=:5`, unset
`DISPLAY`/`WAYLAND_DISPLAY`): on the real display every render window takes focus
and the person's typing lands in it (a filter field rendered holding "ho").
Broadway caps windows at 1024x768 and scales anything larger, so measure widths
with a probe, not a picture. Found and fixed:

- **The header bar needed 632px** - wider than the 600px narrow layout - so every
  narrow window clipped the chat's right side. Quick ask / Browse / Help fold into
  a "More" button below the breakpoint (Setup and Settings stay visible, which
  `test_setup_button_is_reachable` requires); the title ellipsises; the split
  view's automatic back arrow (a duplicate of the sidebar toggle) is off. 443px
  narrow, 483px wide.
- **The organ strip could not centre** because `Gtk.FlowBox` reports natural
  width as widest-child x count (815px for 482px of lights). `OrganStrip.do_measure`
  reports the real sum; it now centres and still wraps.
- Conversation and composer are clamped to 860px (`widgets.reading_width`).
- **Conversations panel** rows were a centred bold `• title  (02 Oct 00:42)`;
  now a title plus `Today 23:35 · 2 messages · Current`, a warning mark for an
  unfinished last turn, and inset margins.
- **Sidebar sections** follow the organ lights: Remembering, Acting, Sensing,
  Thinking, Health and trust (the old four had Models under "This machine" and
  Skills under "What Chronoa did"). Rows ~40px instead of 50. "Model" is now
  "Answering now", so it no longer sits beside "Models" with no difference.
- **Five icon names Adwaita does not have** - `chat-symbolic`,
  `office-calendar-symbolic`, `emblem-documents-symbolic` (the "remembering"
  light), `emblem-ok-symbolic` (the tool card's tick),
  `audio-input-microphone-muted-symbolic`. The existing icon checks ask
  `has_icon` on the *installed* theme, and this machine is Yaru, so all five
  passed while drawing blank boxes on GNOME. `tests/test_sidebar_icons_exist_in_adwaita.py`
  reads Adwaita's files. Also de-duplicated: Artifacts/Export (one arrow),
  Background mode/Desktop/header Settings (three gears), Memory/Artifacts.
- `test_sidebar_toggle.py::test_two_presses_return_exactly_where_it_started` is
  **flaky on untouched HEAD** (4 of 12 runs), the narrow-layout race recorded
  above - not caused by this pass (1 of 12 with it).

- **Three capture paths left the organ strip dark.** `screengrab` lights
  "looking" and `AudioRecorder` lights "listening"; `capture_video` (ffmpeg on
  /dev/video directly), `scan_document`'s scanner, and the wake word's own
  always-open mic stream lit nothing - the camera could record, or the mic stay
  open all day, with the strip idle. `body.lit()` is a context manager that
  lights an organ for a `with` block and always puts it out; all three use it.
  `tests/test_capture_lights_the_strip.py` (fails on the old files, measured).
- Also failing and **not from this pass**: `test_sidebar_toggle.py::test_the_panels_open_beside_the_chat_and_come_back`
  fails 4/4 on untouched HEAD; `test_capabilities.py::...other_group` fails on
  `skills/browse.py`, an untracked file from a concurrent session.

**Fourth pass (2026-10-08): rail, panels, mode strip.**

- Rail sections are quiet cards with real small-caps headings (the CSS comment
  promised letterspacing and uppercase; neither was set), and Context has a
  `Gtk.LevelBar` when the window size is known.
- "Nothing here yet" is `STATUS_OK`, not `STATUS_UNKNOWN`: Activity, Diff,
  Triggers, What it has learned and Conversations all showed amber "Could not
  determine" for an empty store read without error. Diff's empty page said it
  three times; it is now one empty state.
- Desktop headlined red "not answering" for being off the bus - the normal state
  between searches, as its own row says. Red now means the service file is
  missing; off-the-bus with the file present is OK.
- Privacy's sense-consent rows and Skills' rows used raw ids; now
  `common.sense_title()` / `capabilities.tool_title()`, id kept in the subtitle.
- Models' view-switcher tabs had no icons (`add_titled`), drawn as blank cards.
- Artifacts: status row first, count beside the search, a real empty state - and
  **its "open folder" button never worked**: `subprocess` was never imported and
  a broad `except` hid the `NameError`. Now `Gio.AppInfo.launch_default_for_uri`.
- Mode strip: **Talk over** (`toggle-barge-in-vad`) and a **reply-style** menu
  chip on a new stateful `app.reply-style` action (Ordinary / Brief /
  Explanatory; applies from the next reply, verified through the registered
  app). Below 560px the chips go icon-only (`set_compact`) - with words the strip
  needs 512px and the window can be 380px. Permission modes were left as they
  are: Explore duplicates Plan mode by design, and Don't ask is for unattended
  turns.
- `OrganStrip.do_measure` must return -1 baselines for the horizontal axis, or
  GTK warns "reported a horizontal baseline" on every layout.

**What the backend can do that the UI did not show (audit, 2026-10-08).**
Four layers checked by script, not by reading: backend modules with no GUI
reference (33 of 101, mostly plumbing or reached through skills), schema keys no
UI code names (55 of 161, nearly all sense keys the Senses panel builds by
f-string), app actions with no control (none - `show-page` is for D-Bus), and
binaries. Fixed:

- **A custom model server had no field.** `custom-llm-base-url` / `-model` /
  `-api-key` are read by `app/brain.py` (first in the cloud chain) and shown by
  "Answering now", and nothing could set them. Settings -> Privacy now has
  "Your own model server"; the key goes through `set_api_key` (keyring).
- **Every text field in Settings was two fields.** `_entry` packed a `Gtk.Entry`
  inside an `Adw.EntryRow`, which is itself a field (rendered: two boxes per
  row), and the secret reveal toggle called `set_visible` - press 1 revealed
  nothing, press 2 hid the field (run, not inferred). Now one `EntryRow` /
  `PasswordEntryRow`. `tests/test_settings_text_fields.py`.
- **Your own `/commands`** (`~/.config/shani-chronoa/commands/*.md`) worked when
  typed but were missing from the `/` menu; they are listed now, marked "yours",
  summarised by their first line. A **rules file** that is active now shows in
  the rail's Posture card.

Then wired, same day:

- **Connections** panel (`gui/surfaces/connections.py`, section Acting, gear to
  Settings -> Privacy): whether the MCP server can run here (it needs the `mcp`
  package, absent on this machine - the panel says so), the exact `claude mcp
  add` line and `mcpServers` JSON with Copy buttons; for Telegram/WhatsApp,
  whether the `gateways` setting accepts each, which bridges are running (read
  from /proc), and the command that starts one. It never asks for a token: the
  bridge reads its own environment by design, so Chronoa never holds one.
- **What it has learned -> Background passes**: Dream (the daemon's pass, run
  now, with when it last wrote one), Reflexes (all five listed, "check now"),
  Compare predictors (`model_zoo.compare/render/recommend`, previously imported
  by nothing). Off the main loop; the whole report is shown.
- `tests/test_surfaced_backends.py`: builds both panels and drives the real
  backends, including a real process named like the bridge (killed by PID).

Further gap layers checked the same day: every skill is in Help (182/182); no
GUI button is wired to nothing (AST scan; the five flagged are wired by their
callers); every app action has a control. Found and fixed:

- **Replies never streamed.** `ConversationMixin` passes only `sink=` and puts
  `_on_reply_text` (live text *and* sentence-by-sentence speech) on the sink;
  `handle()` read only its own `on_text` parameter, so with a streaming backend
  the reply arrived whole. Measured with one fake model: 0 pieces via `sink=`,
  2 via `on_text=`. `handle` now takes the sink's. `on_state` is declared on
  `EventSink` and neither fired nor subscribed - unused, not broken.
  `tests/test_reply_streams_through_the_sink.py` (fails on HEAD's assistant).
- **Conversations could not be renamed** although `conversation_store.rename`
  exists (and protects the title from the model). Pencil button per row.
- **Trigger rules could be deleted, not made**, outside chat. The Triggers panel
  has an "Arm a rule" form that calls `tools.execute_tool("manage_triggers")` -
  so consent, argument checks and the destructive refusal are the chat path's.
  Probing it needs a settings backend a child process can read: skills run in a
  sandboxed child, and `GSETTINGS_BACKEND=memory` is per-process, so a grant
  made in the probe was invisible to the skill and read as "turned off".

Third gap round (function level and reverse direction, same day):

- The arm form also arms **event rules** (all 19 `EVENT_TYPES`: screen lock,
  USB plug, schedule, ...) with a source field; a bad source gets the skill's
  own list of what that type accepts.
- **Branch a conversation** (`conversation_store.fork`, chat-only before): a
  button per row that copies and opens the copy, leaving the original intact.
- **Gears where a panel names a switch it could not reach**: Desktop ->
  Privacy (global shortcut, document search), Machine -> Senses (its refused
  rows), Activity -> Tool activity. `NO_GEAR` in
  `tests/test_settings_targets_resolve.py` listed Desktop and Machine on
  reasons the code contradicts; the note there now records the evidence.
- Test-isolation trap found on the way: `ChronoaApplication` registers a
  spoken permission presenter in `ask_bridge` at startup and nothing clears it,
  so any later in-process call that needs consent *asks* and blocks. Tests that
  expect a skill's own refusal must clear `ask_bridge._presenter` (see
  `tests/test_surfaced_backends.py::_nobody_to_ask`).
- Checked clean: no setting in the UI that nothing reads (the three hits are
  read by the wizard and the organ strip); session grants are listed and
  revocable in Settings > Approvals; the remaining store functions without UI
  are plumbing. **`goals.py` was listed here as "unwired on purpose (no producer
  yet)" until 2026-10-11 and was false** — `skills/manage_goals.py` produces
  runs, the Goals panel consumes them, and a person can edit a saved plan's
  steps with the model told what changed.
Pre-existing failures seen in this pass, all failing on untouched HEAD too:
`test_config_gsettings.py::test_api_key_defaults_are_empty_not_quoted`,
`test_slash_menu_opens.py::test_typing_a_slash_opens_the_menu...` (flaky, 1/2).

Not changed, noted: Machine's readings are monospace blocks outside the row
cards; the 8-word organ strip shows no state at rest; the Models switcher shows
a red "blocked" glyph on both tabs.

## Twenty skills from the matrix shortlist (2026-10-07)

Picked from `chronoa-matrix.json`'s `ideas` after checking each against the
existing skills (`open_surfaces` measures calls, not answers - see above).
Read-only: `snapshot_status` (shani-deploy `--status --json` + the snapshots
sense), `security_status`, `list_containers` (adds `distrobox list`),
`list_vms` (`virsh`, session and system separately), `boot_report`
(`systemd-analyze time`/`blame`), `disk_health` (LUKS found by walking `lsblk`
ancestors), `temperatures`, `usb_devices` (adds `boltctl`), `driver_info`
(`/proc/modules`, `/sys/module`, `device/driver` links), `list_fonts`
(`fc-list` + `fc-match`, substitutions called out), `photo_metadata` (own stdlib
EXIF reader - no exiv2 on the image, Pillow not a dependency), `crash_report`,
`login_history` (`last`, else wtmp parsed directly). Changing: `audio_output`
(`wpctl set-default`), `default_apps` (`xdg-mime`/`xdg-settings`), `pdf_pages`
(poppler merge/split/extract; no rotate - `qpdf` is not on the image),
`print_queue` (cancel), `set_hostname`, `set_locale`, `speed_test`.

Rules they follow, so the next one does too:

- **A skill that reports a sense's facts shares that sense's switch**, checked in
  the skill's own source (`sense_reading.py` explains why; the precedent is
  `git_inspect`). Only the sense half of a mixed skill is withheld - turning the
  snapshots sense off hides the snapshot list, not the deploy state. Found by
  running them for real: five of the eight senses gate inside `_run` and three do
  not, so the first version's "consent is not consulted" docstrings were false
  for five of them.
- `audio_output` and `pdf_pages` are ungated, by precedent: `set_volume` for
  one, `convert_document` (writes only new files) for the other. The other five
  changers each have their own key, default off; `speed_test` also refuses in
  privacy mode, which defaults on.
- `set_locale` passes back every existing assignment: `localectl set-locale`
  replaces all of them with exactly the list given, so changing LANG alone
  would drop LC_TIME. Mutation-tested.
- `pdf_pages`' post-condition requires the output to be fresh: a refused call
  leaves an older file of the same name, and matching its page count would
  verify work that never happened. The first control could not catch this (a
  non-PDF fails the page count for another reason); it is now a real 4-page PDF
  dated in the past.

**`mcp.py` builds a Python signature in schema property order**, so a required
property listed after an optional one raises `ValueError: non-default argument
follows default argument` and takes the **whole MCP server** down, not just that
tool. `default_apps` hit it; its properties are reordered. The fix belongs in
`mcp.py` (sort required first) and is not made here.

Verified: `tests/test_matrix_skills.py` (51, every skill through
`execute_tool_outcome`; 16 mutations, all caught), `tool_select` picks the right
skill for 28 of 28 natural phrasings, and shani-testbed
`slot-tests/chronoa-matrix-skills.sh` on `@blue` (`shanios-20261006-plasma`):
**29 PASS, 0 FAIL** - every binary present, shani-deploy's status read, a real
poppler merge VERIFIED, all five gated paths refuse, and the control names
`fontconfig` for a hidden `fc-list`.

## Approve from a notification, and four more event types (2026-10-01)

An event rule armed with `ask_first` (manage_triggers' `ask_first`) does not act:
`approvals.py` shows `notify-send --action` Allow once / Deny on its own thread
(both GNOME Shell and Plasma implement org.freedesktop.Notifications; Gio's
GNOME path would need a `dev.shani.chronoa.desktop` that does not exist), and
only Allow runs it, after consent is read again, with the audit origin
`approved`. That is what lets such a rule reach a destructive actuator;
expiry stays notify-only. Event types 12 -> 16: `sleepwake` (BOOTTIME minus
MONOTONIC only grows while suspended), `audiodevice` (pw-dump), `journalmatch`
(cursor of the newest `journalctl --grep` match, pattern as one argv element),
`dbusprop` (`busctl get-property`, every token validated; read-only by D-Bus's
rules). `set_theme` on Plasma 6 uses `plasma-apply-colorscheme` and its
"(current color scheme)" mark as the read-back - the Plasma 5 tool it called does
not exist on the Plasma image. Also: `--scaffold CMD --from-json M.json` writes a
user drop-in skill (flags from the man page's OPTIONS, a `which` guard,
operands that may not start with '-'; anything not read-only is opt-in and
generated disabled), `--diff OLD NEW` compares two runs, and `--check` is what
shani-testbed's `slot-tests/chronoa-matrix.sh` asserts. The dependency audit
follows guards into imported helpers - its first version reported
close_window/focus_window/press_key as unguarded when all three check via
`list_windows.session_problem()`.

## Desktop surfaces added 2026-10-02 (GNOME and Plasma backends, each run on both images)

| Surface | GNOME | Plasma | Gate (default off) | Verified on the images |
|---|---|---|---|---|
| Search provider (`search_provider.py`, `shani-chronoa-search`) | Shell SearchProvider2 | KRunner `org.kde.krunner1` | - (no network, no model, nothing logged) | both: GetInitialResultSet / Match over D-Bus |
| `search_documents` | LocalSearch (`tracker3` fallback) | `baloosearch6` + `balooctl6 status` | `document-search-enabled` | GNOME: honest "index not available"; Plasma: empty output with no index is NOT "nothing found" |
| `calendar_events`, trigger `calendar` | EDS via ECal/ICalGLib 4.0 | none on the image - says so | `calendar-read-enabled`, `calendar-sense-enabled` | GNOME: event created in EDS, read back, trigger fired |
| `phone`, trigger `phone` | GSConnect (ObjectManager + daemon.js CLI) | `kdeconnect-cli` + battery over D-Bus | `phone-control-enabled`, `phone-sense-enabled` | both: link-not-running reported as itself. No SMS, no notification reading, no remote input |
| Wayland input (`portal.py` RemoteDesktop) | gnome portal | kde portal | `input-control-enabled` (existing) | fake portal on a private bus only - the first real use shows the desktop's dialog, which needs a person |
| Global shortcut (GlobalShortcuts) | gnome portal | kde portal | `global-shortcut-enabled` | fake portal; the app thread is stoppable (a thread parked in MainLoop.run deadlocked interpreter exit) |
| `airplane_mode`, `charger_info`, `firmware_updates` | /sys/class/rfkill, power_supply, typec; fwupdmgr --json | same | `radio-control-enabled` (switching only) | real laptop hardware (read paths); rtc wake left out - needs CAP_WAKE_ALARM |
| `desktop_setting` (allowlist) | gsettings | kreadconfig6/kwriteconfig6 --notify | `appearance-control-enabled` (existing) | both: change + read-back verified; the other desktop's setting is refused |
| Background mode (`daemon.py`, user unit) | systemd --user | same | `background-mode-enabled` | both: unit verifies, daemon starts/stops; `runner_lock` = one engine at a time; never the microphone |
| Keyring (`secret_store.py`) | gnome-keyring | KWallet secret service | - (automatic, additive) | GNOME: migrate + read back + clear; Plasma with no keyring: nothing moved, key kept |

**Test hygiene learned the hard way here:** Gio caches ONE session-bus connection per
process. A test that only sets `DBUS_SESSION_BUS_ADDRESS` can still reach the user's
real bus if an earlier test in the run opened it - the global-shortcut test did, once,
sending BindShortcuts to the real desktop portal. Anything that talks D-Bus in a test
takes an explicit private connection (`Gio.DBusConnection.new_for_address_sync`) or
runs in a subprocess. `dbus-launch` is not installed on the dev box; use
`dbus-daemon --session --print-address`.

## A real task end to end in the real app, and what it took (2026-10-08)

Driven by recording demos: first a scripted one, then the **real
`ChronoaApplication`** with a free cloud model (Kilo Gateway via the custom
model-server fields, `kilo-auto/free`). The final run: one typed request ("book
the cheapest Boston -> London flight on blazedemo.com, weather, rupees,
itinerary, remind me tomorrow 9am") -> 27 tool calls chosen by the model ->
booked (demo site, no real payment), weather, INR, HTML itinerary, reminder.
Every gap below was found by a run failing, fixed, and re-run.

**Harness gaps (each one ended the task):**
- `MAX_TOOL_ROUNDS` 4 -> 40 and `MAX_TURN_SECONDS` 300 -> 600. Four rounds cut
  every multi-step task off; the loop detector, per-tool budget, wall clock and
  Stop remain the bounds. `test_multi_step_turns.py`.
- The out-of-rounds answer (sent without tools) was the model's next call as
  raw markup; now `assistant._out_of_rounds` says the steps ran out instead.
- `tool_select.wants_a_browser`: a request naming a site or a page action
  (book, fill in, sign in, order...) is offered `browse`. Before, the model
  tried to book with `web_search`.
- Text tool calls: `recover_tool_calls` now also reads Qwen3-Coder XML
  (`<function=x><parameter=k>v</parameter>`) and a reply that is only a JSON
  call, and the **cloud** client now runs it (its `postprocess` returned the
  message unchanged). Only offered tools; quoted JSON is left alone.
  `test_text_tool_calls_recovered.py`.
- `reaction.py`'s repeat check ("needs a person") never reached a person:
  `tools._reaction_refuses` turned the question into "Not run". It now asks
  through `ask_bridge` (person's own turns only, never from the GTK main
  thread), and an approval restarts that tool's count. `test_reaction.py`.

**browse in the in-app browser (fork-built, then verified here):** runs
in-process while a GUI browser window can serve it (`tools._runs_locally`),
egress-checks every model navigation incl. clicks and redirects, refuses
`evaluate` and `file://`. Added here: a visible cursor/ring/ripple and an
activity strip in the window; typing in visible chunks that carries on into the
focused field when a framework re-creates the input (Wikipedia did); smooth
scroll + `direction`; a click on a hidden element is an error naming the
form's real button (it used to report success); CAPTCHA pages are handed to
the person, never solved; **typing into a card field or pressing a
Pay/Purchase/Place-order button asks the person** (once per site for 5 min;
nobody to ask = not done). `test_browse_in_app.py` (real window on Broadway).

**Skill bugs the recordings showed on screen:** `reminders` refused its own
schema example "tomorrow 9am" and built "tomorrow" on today's date;
`create_document` HTML dumped markdown as one paragraph (now rendered, escaped
first). `test_reminder_times_and_html_documents.py`.

**Window control (`shani_chronoa/windows/`):** one API (list/find/focus/close/
minimize/maximize/fullscreen/move/resize/set_workspace) over GNOME (the shipped
`usr/share/gnome-shell/extensions/chronoa-windows@shani.dev` - enabling it is
the permission), KWin scripting, X11 (xdotool, wmctrl fallback), and the
AT-SPI bus. Measured on GNOME 50: GTK4 frames expose `window.close/minimize/
toggle-maximized` and those work; nothing focuses or moves through AT-SPI;
GTK3/Chromium expose no window actions. New skill `arrange_window`.
`test_windows_gnome_live.py` runs a **private headless gnome-shell** (own
bus/HOME/runtime dir, `--no-x11`) with real windows and drives the skills
through `tools.execute_tool`. **KWin and X11 backends are written but not yet
run against a real KWin/X server.** sway/Hyprland deliberately not done.

**Recording:** GNOME's Screencast in the headless shell produced one frame
for a minute of activity - not usable. Windows record themselves instead
(`Gtk.WidgetPaintable` -> `render_texture` -> PNG at ~6 fps, joined by
ffmpeg at the frames' real timestamps). A fully covered GTK window on Wayland
stops being drawn, so windows are laid out side by side (`_Shell(monitor=)`).

## Voice, measured end to end through real audio (2026-10-08)

`tests/test_voice_loop_live.py` and `demos/record/record.py voice` run a private
PipeWire graph: `mic_feed -> demo_mic` (a virtual microphone) and
`demo_speaker -> speaker_tap` (a virtual speaker). Spoken WAVs go in, Chronoa's
voice is recorded out, and both directions are scored by word error rate. A
transcript that merely exists is not evidence: "two hundred dollars" came back
as "to $100".

**Bugs this found and fixed:**
- **No named voice style was ever applied.** `voice_style.selected_style`
  called `config.get_string`, which ChronoaConfig does not have. The
  AttributeError was swallowed as "unstyled". The same call made the Settings
  "Voice" style row fail to build, so it never appeared. The unit tests' fake
  configs had `get_string`, so they passed. Now `config.get`, with a
  real-config test and a control.
- **The window froze for about 8 seconds after start.**
  `_seed_status_dots` built every sidebar panel in one idle callback,
  Diagnostics' real probes (a screen capture) included. The mic press waited
  behind it, and a spoken request was lost before recording began. Now one
  panel per main-loop turn, and Diagnostics has `PREBUILD = False`.
  `test_startup_does_not_freeze.py`, with a control.
- **Every spoken request waited 4 seconds at the transcription gate.** Its
  busy probe read the window state, and the voice path sets THINKING itself
  just before transcribing. It now asks `VoiceMixin._model_turn_in_flight`
  (`_turn_future`). `test_speech_gate.py`.

**Measured with whisper `base`:**
- **Hearing:** WER 0.00–0.29 for the synthetic "person" voice. Wording
  matters: "at nine" is heard as "time", "at nine o'clock" exactly.
- **Speaking:** espeak-ng (the fallback; no Piper voice in the test profile)
  scores WER 0.35–0.47 with numbers lost, so the word-for-word speaking test
  skips under espeak-ng and runs once a neural voice exists.
- **espeak variants:** `+m3` at 160 wpm 0.27, against the default `+f3` at
  0.39. Changing the default voice's gender is a product call, not made here.
- **Styles** (with SoX) shape timbre: 0.33–0.42, inside the noise.
- **SoX is only an optdepend.** Without it, every style, plus pitch, tempo
  and rate, is a silent no-op.

**Voice engines compared** (`test_voice_engines_measured`, 8-thread laptop CPU,
no GPU; voices installed once into the git-ignored `cache/data-home/`):

| engine | WER | first sentence ready | RTF |
|---|---|---|---|
| espeak-ng `+f3` | 0.39 | 0.02 s | 0.00 |
| **Piper Lessac** | **0.18** (0.00 on the reply sentence) | **0.47 s** | **0.11** |
| Kokoro (int8, 4 threads) | 0.41 (dropped words) | 9.9 s | 2.0-2.4 |

Kokoro reloads its model in each per-sentence `sherpa-onnx-offline-tts` call
(about 3.4 s of load). Without that, synthesis alone is still RTF ≈ 1.3, so a
persistent worker would not make it keep up on this class of CPU and was not
built. It stays opt-in; Piper, which setup's Voice page installs on every path,
is the voice to use. The Kokoro thread count is now `min(4, cpu_count)`, not
a fixed 2: 4 threads were fastest, and 8 were slower again.

## Audit-verified known issues (confirmed present)

- **Four surfaces disagreed about which consent key opens the microphone. FIXED
  2026-10-05.** The `heard-sound` sense gated on `heard-sound-sense-enabled`;
  its own schema description told the model it needed `sound-sense-enabled`; the
  `listen` skill in `skills/recording.py` gated on `sound-sense-enabled`; and
  Settings → Privacy had a row for `sound-sense-enabled` and none for
  `heard-sound-sense-enabled`. The sense was therefore **unreachable by a user** —
  the only switch that granted it was not on screen. Verified by execution after
  the fix (with the recorder stubbed to raise, so no audio is ever captured): one
  switch, both paths.

  Two keys for two things is correct and worth keeping: `heard-sound-sense-enabled`
  is *ask and it listens once*, `sound-sense-enabled` is *an automatic rule may
  listen for a doorbell*. A user who allows the second has not agreed to the
  first, and one key cannot express that difference.

- **Arm-time source validation matched English, so malformed sources armed.**
  `manage_triggers` decided a source was unreadable by regex-matching the
  openings of the readers' refusal messages, which only covered the three
  readers whose wording happened to match. Five verified cases (`sound:12345`,
  `sound:doorbell:1.5`, `powerstate:battery-below:0`, `…:abc`,
  `journalmatch:unit:bad unit:x`) armed cleanly and then reported
  `SIGNAL_UNAVAILABLE` on every poll forever, with `list` still showing them
  enabled. Now the reader's **status** is consulted — a contract rather than
  English — behind a small fixed list of phrases that distinguishes "your source
  string is malformed" from "the thing it names does not exist yet", because
  arming `git` on a repository you have not cloned yet is legitimate and must
  keep working. All five now refuse; all legitimate cases still arm.


- **Three skills were advertised to the model and to every MCP client, and
  failed on every call. FIXED 2026-10-05.** `calendar_month`, `date_math` and
  `stopwatch` were declared `run=lambda a: _run(a)`. A lambda passes
  `callable()`, so the loader accepted it — but a non-local skill is executed in
  a sandboxed child process whose program is built by interpolating the
  handler's name:

      from shani_chronoa.skills.stopwatch import <lambda>; import sys; ...

  which is a `SyntaxError`. The tool's *result*, as the model saw it, was a
  Python traceback. `tests/test_everyday_batch2.py`/`batch3.py` call the private
  `_run()` directly, which works perfectly, so **the suite was green while three
  skills were dead**. `tests/test_every_skill_handler_is_importable.py` now
  asserts the transport-level property (every handler's `__name__` is a real
  identifier and resolves in its own module), and `skills/__init__.py` refuses
  the registration with a warning rather than shipping a broken tool.

  The general lesson, and it is the same one as the `midi.py` entry below:
  **a test that calls the function is not a test that the skill is reachable.**
  Always exercise the registered handler through `execute_tool_outcome`.

- **`guardrail.py` was fully built, unit-tested and never called. FIXED
  2026-10-05.** It answers a different question from every other layer in the
  dispatch path — not "is this call permitted" but "is it even well-formed" —
  and `tools._dispatch` now consults it next to the reaction layer, where it can
  only stop a call that was already allowed. It turns `speak(text=42)` and
  `set_volume()` with no arguments into one readable sentence instead of a
  traceback three frames down in a subprocess. Verified by execution: both
  return `ran=False` and the well-formed call still runs.

- **`dream.py` was built and had no caller. FIXED 2026-10-05.** It now runs on
  the daemon's existing sleep tick (`senses` and `consolidation` already lived
  there), separately guarded so a dream that cannot run cannot also cost the
  consolidation pass above it. A separate timer unit would have meant a second
  thing to start, stop and misfire.

- **`memory` disclosed and deleted with consent off. FIXED 2026-10-05 — the
  most serious of this batch.** The gate lived only on the write paths
  (`remember_fact`, `link_entities`), so with `memory-sense-enabled=false` the
  `recall`, `about`, `history` and `forget` operations still returned the full
  contents of the durable store, and `forget` still erased from disk. Reproduced
  by execution before fixing; the gate is now at the top of `_run`, so it covers
  every operation. The per-write checks stay — they are reached directly by other
  callers, and an entry-point gate is not a gate on the function.

- **`labnetworks` raised `NameError` on every call. FIXED 2026-10-05.**
  `_record_state()` called `record_path()`, which was never imported. The sense
  was 100% broken — it could produce no reading at all, granted or not — and it
  had **no test file whatsoever**, which is why it survived.
  `tests/test_sense_labnetworks.py` now covers it, including an AST check that no
  module uses a name it never binds (the general form of this bug).

- **`midi.py` is the clearest illustration of this whole section — and it could
  not even be imported.** Added 2026-10-03 as a third member of this class
  (alongside `singing.py`/`prosody.py`): it reads a Standard MIDI File and fits
  its melody to a line of syllables, it is 357 lines of careful code, and
  `grep -rn "\bmidi\b" --include=*.py usr/` finds **no importer at all** — the
  one hit is `senses/capture.py`'s regex for ALSA `pcm` nodes. There is no
  `tests/test_midi.py` either. **Now reachable: `skills/sing.py` takes an
  optional `midi_file` and fits that tune instead of a named contour, which is
  the "a caller who wants a specific tune has to bring it" line its own
  docstring always pointed at.**

  **It was also broken in the most basic way available, and 4603 passing tests
  said nothing.** A module-level line `_ = (os, struct)` referenced a name
  `os` that was never imported, so `from shani_chronoa import midi` raised
  `NameError: name 'os' is not defined` on line 357 of the file — a module
  that is 100% unreachable and 100% unimportable. Fixed by deleting the line
  (pyflakes 4.0.1 flags it; the suite does not, because nothing imports the
  module).

  **Then `tests/test_midi.py` was added (same day) and two more real bugs
  surfaced within minutes, both of which the empty test suite had been
  concealing.** Writing the first MIDI bytes by hand is what found them:

  - **Every note was half its real length.** `seconds_per_tick` was
    `(60.0 / bpm) / ticks_per_second`, but `ticks_per_second` was itself
    derived from `TICKS_PER_BEAT`, so the length of a beat was applied twice.
    A quarter note at 120bpm in a 480-division file parsed as **0.25s instead
    of 0.5s** — a melody sung from a parsed file ran at double speed. Any file
    whose division was not 480 was wrong by a different factor again (division 96
    is common), because the only correct denominator is the file's own
    `division`. Now `(60.0 / bpm) / division`, with the SMPTE branch kept
    separate since its clock is the recording equipment's, not the tempo's.
    Verified by running: 0.5s / 0.5s / 0.5s at 120bpm for divisions 480 and 96,
    1.0s at 60bpm, onsets 0.0 / 0.5 / 1.0.
  - **`from_url()` could never have worked.** It passed `_Bytes` to
    `read_notes`, which called `Path(path).read_bytes()`; `Path()` rejects an
    object that is not `os.PathLike`, so every call raised
    `TypeError: argument should be a str or an os.PathLike ... not '_Bytes'`.
    `read_notes` now takes either a path or anything with `read_bytes()`.

  Recorded here because the lesson is sharper than any of the three bugs: **the
  absence of an importer is what hid a syntax-level defect, a factor-of-two
  timing error and an unusable public function from the entire suite.** Adding
  the test file is done; **wiring the module to something real is still open**,
  and this entry is the reason to distrust any future "green, so it works"
  claim about a module with no callers.

- **`singing.py` + `prosody.py` were dead code; that is FIXED, and this entry is
  kept because the lesson is the point.** They were fully built, unit-tested
  (`tests/test_singing.py`, `tests/test_prosody.py`) and verified on a real
  image, and as of 2026-10-03 nothing in the product imported either module -
  `grep -rn "singing\|prosody" --include=*.py usr/` returned nothing, and their
  only consumer was `../shani-testbed/slot-tests/chronoa-singing.sh`. **As of
  2026-10-05 they are reachable**: `skills/sing.py` exposes
  `SKILLS = [Skill(name="sing", ...)]`, verified by execution as both
  *discovered* and *dispatchable* (`skills.discover_skills()` → 152 schemas,
  `"sing" in handlers`). `midi.py` also imports `prosody` directly.

  This was exactly the dead-code class at the end of this section - a module
  that is green in isolation and unreachable at runtime. **Grep for a consumer
  before believing "done", and do not leave a green-but-unwired module described
  as shipped.**

  What *is* real, measured on a booted `@blue` (`shanios-20260925-gnome`,
  Chronoa overlaid, kokoro + soundstretch + sox present): per-note pitch is
  exact — a four-note rising plan of +0/+2/+4/+6 semitones read back
  +0.0/+2.0/+4.2/+6.1 by zero-crossing count — and the `soothing` voice style
  measurably moves the audio (1.28x at 180 Hz, 0.87x at 3500 Hz, with a `bright`
  control moving 3500 Hz to 1.40x the other way). So the capability works on real
  hardware; what is missing is a **surface**: which entry point should reach it
  (a `sing` skill? a reply mode? a trigger?), and that is a design decision
  rather than a mechanical wire-up — the same reason `ipc.py` was deleted rather
  than wired.

  Two dependencies to know before that surface exists, both measured on the same
  run: `soundstretch` (or `rubberband`) is what shifts a note — `sox` alone
  cannot — and `singing.singing_support()` reports `can_track_pitch: False`,
  which is the honest ceiling and is stated in the module itself: nothing in the
  codebase measures the pitch of the audio it is shifting, so the contour cannot
  be closed against what is actually sung.

- **`SandboxExecutor`: the four policy guards scanned a shell *string*, so shell
  expansion defeated every one of them — FIXED (2026-09-30) by taking `argv`
  instead of a string.** The four checks below are the *pre-fix* shape, kept
  because the reasoning is what explains the fix and because a future change
  that reintroduces string matching should be recognisable as the same mistake:

  | # | Guard | Method |
  |---|---|---|
  | 2 | privilege escalation (`sudo`/`pkexec`/`su`) | `"sudo " in raw_cmd` |
  | 3 | internal binaries, isolated levels | `set(raw_cmd.split())` |
  | 4 | `config.blocked_binaries` | `_first_blocked_binary` |
  | 5 | `DANGEROUS_BINARIES` | `_first_blocked_binary` |

  Verified by running, against the real `SandboxExecutor` at
  `LEVEL_3_HOST_USER` (the default for every skill call): plain `dd` is refused
  with `126`, while `$(echo dd) status=...` and `d\d status=...` both reach the
  **real system `dd`**, which then errors on its arguments — proof it ran, not
  proof the filter declined. Check #2's predicate was tested the same way and
  `$(echo sudo) reboot`, `` `echo pkexec` id ``, `su${IFS}-c id` and
  `sud\o reboot` all pass it.

  **This was latent, not a live hole, and the distinction mattered.** Both
  production call sites `shlex.quote` everything untrusted:
  `tools.py:370` builds `python3 -c {shlex.quote(program)}` and
  `argfile.py:380` builds `python3 -c {shlex.quote(_REFERENCE_PROGRAM)}
  {shlex.quote(envelope_path)}`, writing untrusted *values* into an `O_EXCL`
  0600 JSON envelope rather than into the command string. No skill module uses
  `shell=True` with interpolated values. So on the production path the guards
  inspected `python3 -c '<quoted program>'` and were very nearly a no-op — which
  is exactly why this survived so long unnoticed, and why "the guards are on" was
  never the same claim as "the guards work".

  It was recorded rather than quietly patched because the correct fix is not a
  filter. Two traps, both measured rather than assumed, and both of which decided
  the shape of the fix:

  - The obvious repair — refuse any command containing `$`, a backtick, `\` or
    `${` — false-positives on valid calls, because `shlex.quote` does not strip
    those characters, it only wraps the program in single quotes. Argument
    *data* carrying `$(...)`, a backtick, a regex backslash or `$5` lands
    verbatim inside the quoted program. This is why the fix inspects `argv` and
    never the text, and why `test_a_python_program_carrying_metacharacters_is_not_refused`
    exists: a python program whose *payload* contains a metacharacter is a
    legitimate call and must still run.
  - The fix was not mechanical, because `tests/test_sandbox_parent_death.py`
    calls `_run_host("sleep N; touch MARKER &")` and depends on `;` sequencing
    and `&` backgrounding, neither of which argv can express. **That decision
    was resolved as: an explicit `sh -c` stays reachable as a visible opt-in**,
    which is honest — a blocklist never could police a shell string — and is why
    `_shell_script()` exists and why it is tested to fire only for a real shell.

  Do not "fix" one guard in isolation. They share a root cause and a fix that
  patches only #5 leaves #2 — the privilege-escalation one — just as bypassable.
  That is exactly what happened: all four moved to `argv` together.

  **What the fix actually is.** `execute()` now takes `argv: list[str]` and
  returns `(126, ...)` for a `str`, so the old shape cannot be used by accident.
  The program is resolved with `_program(argv)`, which skips leading
  `VAR=value` assignments and one leading `env` — `env FOO=bar python3 ...` and
  `FOO=bar python3 ...` both run python3, so a guard reading only `argv[0]`
  would see `env`, match nothing, and wave the real program through. Matching is
  still basename-based with dotted variants treated as the binary they are named
  after (`mkfs.ext4`), and still never substring, so `add` and `ddrescue` are not
  `dd`. An explicit shell script is accepted, but `_unresolvable_script()` refuses
  one containing `$(`, a backtick or `${` rather than guessing — the cost is real
  and deliberate (`sh -c "echo $(date)"` is refused too), and the alternative is a
  check that silently fails on the input it exists to catch.

  Verified by running, not by reading: 46 cases in `test_sandbox_argv_policy.py`,
  and three mutations were run to confirm the suite can actually fail — reverting
  `_program()` to a naive `argv[0]` fails 5, re-allowing a `str` fails 2, and
  emptying the `_EXPANSION` tuple fails 3.

- **`SandboxExecutor._run_host()`: `timeout_seconds` was never actually
  enforced for LEVEL_3_HOST_USER (the default level for every skill call)
  — FIXED (2026-09-18).** A foreground command that ran past its configured
  timeout was never killed: the poll loop just gave up waiting and the
  function returned `(0, "Command started and continues running in the
  background (PID: ...)", ...)` — reporting success while the real process
  kept running on the host, completely untracked, for as long as it liked.
  **Verified live**: a command with `timeout_seconds=2` running `sleep 10 &&
  touch marker` returned exit 0 after 2s while the `sleep` kept running and
  the marker file was created 8 seconds later, proving the process was never
  terminated. This defeats the entire stated purpose of the sandbox executor
  (bounding LLM-issued commands) for the common case — every skill call
  goes through `tools.py:_get_sandbox_config()`, which defaults to
  `LEVEL_3_HOST_USER`. Fixed by launching with `start_new_session=True` and,
  when a genuinely-foreground command (not `&`/`gtk-launch`/`xdg-open`)
  exceeds its timeout, `os.killpg()`-ing the whole process group and
  returning `(124, "...timed out...", ...)` instead of `(0, "...continues
  running...")`. **Verified live after the fix**: the same repro now kills
  the sleep within the 2s window (marker file never created) and returns
  124; real backgrounded commands (trailing `&`) and normal quick commands
  were re-verified unaffected.
- **`SecretsManager`'s vault was never populated with the actual secrets it
  exists to protect — FIXED (2026-09-18).** `set_secret()`/`get_secret()`
  were never called anywhere in the app; the real cloud LLM API keys
  (Anthropic/OpenAI/Google/Groq/etc.) are stored via `ChronoaConfig`/
  GSettings instead (a deliberate, documented choice — see
  `cloud_llm_api_keys()`'s own threat-model docstring in `config.py`). This
  meant `sanitize_text_for_llm()` — the P0 "prevent API keys leaking to LLM
  providers" fix — always checked against an empty `_cache` and silently
  redacted nothing. **Verified live**: a fake key embedded in a prompt
  string passed through `sanitize_text_for_llm()` completely unredacted
  before the fix. Fixed by adding `register_runtime_secret()` (registers a
  value in the in-memory cache for sanitization/env-injection purposes only,
  without persisting a second copy to `vault.json` — GSettings stays the
  single source of truth for these keys, matching the existing design) and
  calling it from `app.py:_maybe_enable_cloud_fallback()` for every
  non-empty configured provider key before building the `CloudLLMChain`.
  **Verified live after the fix**: the same fake-key repro is now redacted
  to `$SECRET:CLOUD_LLM_ANTHROPIC`, and the vault file is confirmed to stay
  absent from disk (no second on-disk copy created).
- **The 2026-09-18 security/architecture modules that were built, unit-tested
  in isolation, and never imported by anything that actually runs: two were
  deleted (2026-09-30), and the other two are mid-wiring — grep before you
  trust this list.** Confirmed by `grep` for real callers across the whole
  package, not by a `feat:` message and not by a passing module-level test.

  - **`ipc.py` — DELETED (2026-09-30).** `PeerValidator.sign_message()` was a
    bare `hashlib.sha256(payload)` with **no key**, so signing and verifying
    were the same operation and anyone could forge a "signed" message.
    Wiring it in would have made Chronoa *appear* to have authenticated IPC
    while providing none, which is worse than having none. There was also
    nothing for it to protect: no D-Bus or socket IPC surface exists, the MCP
    server is explicitly stdio-only/same-user-trusted (see `mcp.py`'s own
    "Trust model" docstring), and the package's only near-match was the
    unrelated string `--unshare-ipc` in `sandbox/executor.py` — a bwrap flag,
    not an import. This was Implementation Roadmap item 4's own explicit
    choice: "decide whether there's a real integration point before wiring it
    in, or remove it."
  - **`skills/scan_archive.py` — DELETED (2026-09-30).** A superseded
    duplicate, not an orphan. `extract_archive._check_members()` already
    rejects `..` components, absolute paths and paths resolving outside the
    destination, refuses symlink/hardlink/device/FIFO members, and caps member
    count — strictly better on every count than the deleted copy, which had
    only the first two checks. Two overlapping guards on one path is the
    failure mode this file keeps warning about, where one gets hardened and the
    other silently does not. `tests/test_scan_archive.py` went with the module,
    which briefly left the zip-slip guard itself exercised by no test —
    `tests/test_extract_archive.py` now covers it directly (20 tests), including
    the symlink-through-destination escape the stdlib does not save you from.
  - **`gateway_supervisor.py` and `sandbox/profiles.py` (`AgentProfile`) —
    BOTH WIRED (2026-09-30); do not add either back to the dead-code list.**
    They were dead when first measured; both now have real callers:
    `sandbox/executor.py:23` for `AgentProfile`, `audio.py:42`/`:105` for
    `GatewaySupervisor`. Re-measure with
    `grep -rn 'gateway_supervisor\|AgentProfile' usr/` and trust that output
    over any list here. This entry has already contradicted itself twice, in
    both directions — see `tool_tracking.py` below, which was listed as dead
    while live and would have been deleted by a reader who trusted the list as
    this file instructs. That is the direction that actually destroys work, so
    a stale "dead" label is worse than a missing "live" one.

  **`tool_tracking.py` is not in this list and must not be added back.**
  It was wired in later the same day: `tools.py:37` imports `ToolTracker,
  ORIGIN_USER` and `triggers.py:48` imports `ORIGIN_UNATTENDED`, so it is live
  on both the user-tool and unattended-trigger paths. This entry previously
  listed it as dead while the Implementation Roadmap below said the same work
  was DONE — the file contradicted itself, and a reader who trusts the
  dead-code list (as this file instructs) would delete a live module. Two other
  modules an audit pass reported as dead are also live and must not be
  "cleaned up": `markdown_lite.py` (`gui.py` calls
  `markdown_lite.to_pango`, which is what renders `**bold**` in the
  transcript) and `verification.py` (`tools.py:166` calls
  `verification.verify(...)` on **every** skill invocation — security-critical,
  and its own comment says it "must never kill the action").

  The `NOT_A_SKILL` opt-out marker in `skills/__init__.py` is **kept**, and
  `tests/test_skill_discovery_noise.py` still covers it, including the rule
  that only a built-in may use it — a user module that declares itself a
  non-skill is dropped loudly, never silently. No built-in carries the marker
  now that `scan_archive` is gone; the built-in half of the behaviour is
  covered from the other side, by asserting that no built-in logs a skip
  warning at all. (An earlier version of this file claimed the deleted module
  logged a `Skipping 'builtin:scan_archive'` warning on every startup. That
  was already stale before the deletion — the marker was suppressing it.)

  Do not treat a `feat: add X` commit or a passing module-level unit test as
  proof `X` is live in the running app — grep for real callers first, per this
  file's own verify-by-running rule above.
- **`tests/`: two more pre-existing bugs found and fixed the same pass.**
  Fourteen `.pyc` files under `usr/lib/shani-chronoa/shani_chronoa/**/__pycache__/`
  were committed to git (`git ls-files | grep __pycache__` — the
  `.gitignore` rule existed but never retroactively untracked them),
  directly contradicting this repo's own `test_no_pycache_in_packaged_payload`/
  `test_no_bytecode_files_in_packaged_payload` tests, which were failing
  before this pass — `git rm --cached` fixed it. Separately,
  `tests/test_skills.py::test_execute_tool_non_dict_arguments_are_ignored`
  referenced a `tools_mod._HANDLERS` attribute that doesn't exist (renamed
  to `_HANDLER_FNS` at some point, or the test predates `execute_tool`'s
  current subprocess-based dispatch, which makes monkeypatching a handler
  directly impossible anyway) — rewritten to mock `_SANDBOX.execute` and
  assert on the built command string instead, which actually matches how
  `execute_tool` dispatches today. Full suite: 122 passed / 3 failed before
  this pass, 125 passed / 0 failed after.

## Garuda Cross-Reference Findings (added 2026-09-17)

Based on a full scan of 29 garuda-linux repos mapped against shani (see `../garuda-catalog.md` — 29 repos, not 34; several user-listed names don't exist). See `../garuda-mapping-analysis.md` and `../deep-analysis.md` for full details. Sayri (a Pulsar OS AI assistant, present in garuda-clones/ — not a garuda repo itself) is the most directly comparable repo — both are local-first GTK4 AI assistants.

### 🔴 CRITICAL: Security gaps vs sayri

Sayri has security features Chronoa lacks. These are high-priority:

1. **Add sandbox execution for shell commands** (estimated 2-3 days).
   - Sayri's `adapters/sandbox/executor.py` implements 5-level sandboxing: LEVEL_0 (no exec), LEVEL_1 (bwrap read-only), LEVEL_2 (bwrap isolated dev), LEVEL_3 (host user), LEVEL_4 (host root via pkexec).
   - Blocks privilege escalation (`sudo`, `pkexec`, `su`) for all levels except HOST_ROOT.
   - Blocks dangerous binaries (`mkfs`, `dd`, `shutdown`, `reboot`) via configurable blocklist.
   - Uses `subprocess.run(timeout=...)` for command-level timeouts.
   - Detects GUI access attempts in sandboxed execution.
   - **Where**: `shani_chronoa/tools.py` where `execute_tool()` (defined at `tools.py:25`, called from both `assistant.py:98` and `mcp.py:121`) runs bash commands. **Model**: `sayri/adapters/sandbox/executor.py`.
   - Impact: Prevents the LLM from running `mkfs`, `dd`, `sudo` etc. accidentally.

2. **Add secrets vault** (estimated 1-2 days).
   - Sayri's `domain/secrets_manager.py` stores secrets XOR-obfuscated using a key derived from `/etc/machine-id` + UID, with `os.chmod(file, 0o600)`.
   - `sanitize_text_for_llm()` replaces all secret values with `$SECRET:<key>` BEFORE sending to any LLM provider.
   - `inject_environment()` injects secrets ONLY into child process env at execution time, never stored in plaintext.
   - **Where**: new file `shani-chronoa/usr/lib/shani-chronoa/secrets_manager.py`. **Model**: `sayri/domain/secrets_manager.py`.
   - Impact: Prevents API keys (stored under the `org.shani.chronoa` GSettings/dconf schema — audit-verified 2026-09-17: there is **no** `~/.config/shani-chronoa/config.json`) from leaking to LLM providers.

3. **Add LLM prompt sanitization** (estimated 2-4 hours).
   - Add `sanitize_for_llm()` that redacts secrets from any text before sending to LLM API.
   - **Model**: `sayri/domain/secrets_manager.py:143-152`.

4. **Add peer-validated IPC** (estimated 1 day).
   - Sayri's UNIX socket at `~/.local/share/sayri/sayri.sock` rejects connections from other users (peer UID validation). Chronoa uses D-Bus with no peer validation.
   - **Where**: Chronoa's D-Bus interface. **Model**: `sayri`'s UNIX socket peer check.

### 🟡 HIGH: Architecture gaps

5. **Gateway architecture for external channels** (estimated 2-3 days).
   - Sayri has a Gateway Supervisor managing external channels (Discord, Telegram, Matrix) with per-instance sandbox binding, PID files, auth files, and inactivity timeouts.
   - Chronoa has no gateway concept — add one for any future external integrations. **Model**: `sayri/gateway_supervisor.py`.

### ✅ What Chronoa already does better than Sayri

- Wake word with VAD calibration (Sayri: basic)
- Barge-in with TTS interrupt + VAD layer (Sayri: no barge-in)
- 7+ LLM providers vs Sayri's OpenAI-compatible only
- Explicit cloud fallback (BYOK) — Sayri requires API key setup
- Real MCP server (Sayri: no MCP)
- Calibrated noise floor for voice quality

### 🔍 Re-Scan Findings (2026-09-17)

Re-scan against `../garuda-catalog.md` (29 repos, not 34). **Confirmed mapping: sayri** ✅ — it is an ACTUAL repo at `../garuda-clones/sayri/` (the catalog's discrepancy table confirms `garuda-sayri` → `sayri`). Its source is directly readable, and the file paths cited in the findings above (`adapters/sandbox/executor.py`, `domain/secrets_manager.py`, `gateway_supervisor.py`) are all verified present under `usr/share/sayri/lib/sayri/`.

**Read sayri's source directly for implementation patterns** — `../garuda-clones/sayri/` has its own 210-line README and a full package layout with more patterns than the earlier analysis captured:

- `domain/skills_scanner.py` — skill/plugin risk scoring that can block an installation outright
- `downloads.py` — zip-slip protection on skill/plugin downloads
- `ipc.py` — single-instance UNIX socket (`$SAYRI_STATE_DIR/sayri.sock`, chmod 600, peer UID validation)
- `domain/cron_scheduler.py`, `domain/triggers.py` — scheduled/triggered agent runs
- `wizard.py` — first-run provider/API-key setup screen
- `overlay.py`/`orb.py`/`cajita.py` — transparent layer-shell overlay UI (libgtk4-layer-shell)
- `webkit.py` + `web/` — WebKit-based web UI (App.js, metro.config.js)
- `settings_window.py` — GTK settings window (can download whisper-cli/Piper models on first use)

**New gaps** (sayri has, chronoa lacks):

1. **Skill/plugin download with zip-slip protection + risk scoring** — sayri installs skills from store-os.inled.es/ClawHub with a scanner that assigns risk scores and can block; chronoa only loads local user-dropped skill modules.
2. **Single-instance enforcement via UNIX socket** — sayri is single-instance via `sayri.sock` with peer UID validation; chronoa uses D-Bus with no peer validation (already noted above).
3. **Scheduled/triggered agent runs** — sayri has `domain/cron_scheduler.py` + `domain/triggers.py`; chronoa has no scheduling.
4. **First-run setup wizard** — sayri has `wizard.py` for provider/API-key setup; chronoa relies on gsettings defaults.
5. **Transparent layer-shell overlay UI** — sayri pins a transparent always-on-top overlay (orb + cajita) via libgtk4-layer-shell; chronoa uses a conventional window.

**Shani advantages** (chronoa has, sayri lacks) — the list above is confirmed against sayri's own README: wake word with VAD calibration, barge-in with TTS interrupt, 7+ LLM providers vs sayri's OpenAI-compatible-only, explicit BYOK cloud fallback, real MCP server (sayri: none), calibrated noise floor.

**Qt GUI gap note**: sayri is Python/GTK4 — the same stack as chronoa — so garuda's 12 Qt6 GUI apps are not the relevant comparison for this repo; the GTK4/Python stack is shared with sayri, not with garuda's Qt fleet.

## Deliberately not done (as of 2026-09-16)

> **Superseded (2026-10-08).** The live list is `ARCHITECTURE-TARGET.md` Part 4,
> mirrored as data in `organism.STANDING_DECISIONS` and shown in the app on the
> Inventory panel under "Deliberately not built";
> `tests/test_standing_decisions_are_shown.py` fails when the two drift. Of the
> items below, the tray and background mode have since been built (`app/tray.py`,
> `daemon.py`) and sherpa-onnx is in use (Kokoro); the MCP client stays refused.

Found during a competitor-comparison pass but not implemented, because each
needs a real design decision rather than just typing code - don't assume
these were missed:

- **System tray / background mode.** Wake-word listening makes an
  always-visible foreground window increasingly pointless, but this is a
  real GTK4 application-lifecycle change, not a small one.
- **An MCP client** (consuming external servers like Context7/filesystem/
  GitHub) - conflicts with the fixed-whitelist skill design's whole safety
  rationale and needs its own trust story first.
- **Wyoming protocol support** (the live, maintained successor to
  Rhasspy's dead Hermes/MQTT approach, used by Home Assistant's Assist
  pipeline) - a real, scoped integration opportunity if "join the
  smart-home voice ecosystem" ever becomes a goal, but a new subsystem, not
  a bugfix.
- **sherpa-onnx** as an alternate/backup wake-word engine to openWakeWord -
  filed away, not urgent unless openWakeWord's real-world accuracy
  disappoints.

### 📋 Implementation Roadmap (2026-09-17)

Implementation priorities are per `../IMPLEMENTATION-ROADMAP.md` (master roadmap for the whole shani ecosystem).

**Status correction (2026-09-18, audit-verified — read this before trusting
any item below):** items 1-3 and 7 were implemented as code (commits
`fb5fd67`, `9fde500`, `b3dd356`, `d85df5c`) but were NOT actually functional
as shipped — see the "Audit-verified known issues" section above for the
live-execution proof and the fixes applied this pass (items 1-3, 7 are now
genuinely done). Items 4, 6, 8 remain implemented as standalone modules but
confirmed (via `grep` for real callers) **never imported by anything that
runs** — dead code, not done, and deliberately left that way (each needs a
real design decision - see the entries below and the "Deliberately not
done" section - not a mechanical wire-up like item 7 got). Don't take a `feat:`
commit message or this list's prose as proof of "done" — grep for real
callers first, and check what the default arguments actually do when
nothing overrides them (item 7's `/var/log` default was the second
"passes its own unit test, crashes for real" bug found this pass).

**Later update (2026-09-30):** items 4 and 5 are no longer modules at all —
both were deleted, after deciding not to wire them in. See "Audit-verified
known issues" above for what replaced each and why. Items 6 and 8 are being
wired in a concurrent pass, so this paragraph's claim about them is a
statement about the 2026-09-18 measurement, not about the tree today.

1. ~~**Sandbox Executor** (P0, 2-3 days)~~ **Module exists and IS wired into
   `tools.py`'s `execute_tool()` path, but its timeout enforcement for
   `LEVEL_3_HOST_USER` (the default) was a no-op until fixed 2026-09-18** —
   see "Audit-verified known issues" above.

2. ~~**Secrets Vault** (P0, 1-2 days)~~ **Module exists but its
   `set_secret()`/`get_secret()` were never called anywhere — the vault's
   cache was always empty until `register_runtime_secret()` was added and
   wired into `app.py` 2026-09-18** — see "Audit-verified known issues"
   above. Real API keys still live in GSettings/dconf by design (roadmap
   #2's `config.json` premise was already wrong per the 2026-09-17 note
   below — no such file ever existed).

3. ~~**LLM Prompt Sanitization** (P0, 2-4 hours)~~ **`sanitize_text_for_llm()`
   exists and IS wired into every LLM call path (`llm.py`, `cloud_llm.py`
   x3), but was a silent no-op against real keys until item 2's fix above
   made the vault aware of them — DONE as of 2026-09-18.**

   **Same class, still open as of 2026-10-05** — three more modules that pass
   their own tests and that **nothing that runs imports**. Measured, not read:
   `grep -rn` for a real importer across `usr/lib/shani-chronoa/`, then each
   candidate opened by hand:

   - `midi.py` — reads a `.mid` into a `prosody.Melody`. `skills/sing.py` exists
     and is dispatchable, but it **does not import it**: it takes notes the model
     supplies. So the "sing me a real tune" path is the one thing still missing
     from a feature that otherwise works.
   - `guardrail.py` — a well-formedness check between model and tool. Only
     referenced in `organism.py`'s metaphor text, which lists it as `BUILT`.
   - `dream.py` — the offline pass over the day's tool calls.

   **Two false positives worth remembering**, because a grep-only audit gets
   these exactly backwards: `mcp.py` and `search_provider.py` *also* have no
   importer inside the package, and both are genuinely shipped — they are entry
   points reached by `usr/bin/shani-chronoa-mcp` and `usr/bin/shani-chronoa-search`.
   **A module with no in-package importer may be a binary's `main`.** Check
   `usr/bin/` and the systemd units before calling anything dead.

   Not fixed here: each needs a design decision (what surfaces singing a real
   tune, where a pre-tool check belongs in the dispatch order, what schedules
   the dream pass), which is the same reason `ipc.py` was removed rather than
   wired.

4. **Peer-Validated IPC** (P3, 1 day) — **DECIDED AND CLOSED (2026-09-30):
   removed rather than wired in** (roadmap #28). `ipc.py`'s `PeerValidator` was
   never imported anywhere and was not a real peer-credential check — an unkeyed
   SHA256 hash, so signing and verifying were the same operation. Chronoa also
   has no D-Bus/socket IPC surface for it to protect: the MCP server is
   explicitly stdio-only, same-user-trusted (see `mcp.py`'s "Trust model"
   docstring). See "Audit-verified known issues" for the full reasoning. The
   *capability* gap versus sayri's single-instance socket remains real; the
   fix for that is a real socket with peer-credential checks, which is a new
   subsystem and not this module.

5. **Zip-Slip Protection + Skill Download Validation** (P3, 1 day) —
   **PARTIALLY CLOSED (2026-09-30)** (roadmap #29). The protection half is done
   and stronger than the roadmap assumed: `extract_archive` checks every member
   before writing anything, and refuses `..` components, absolute paths,
   paths resolving outside the destination, link/device/FIFO members and
   oversized listings. `skills/scan_archive.py` was deleted as a superseded
   duplicate of exactly that. The *validation* half — a store to download
   skills from — is still not done, and still is not wanted without its own
   design decision: Chronoa only supports local
   `~/.config/shani-chronoa/skills/` drop-in.

6. **Gateway Supervisor Architecture** (P2, 2-3 days, only if external
   channels are planned) — **the heartbeat half is now WIRED (2026-09-30).**
   `audio.py:42` imports `GatewaySupervisor` and `:105` holds a module-level
   `_SUPERVISOR`, supervising the real `pw-record`/`pw-play` children — a hung
   `pw-record` means the assistant silently stops listening with nothing
   reporting it. `register_agent`/`broadcast_message` remain dead: they imply a
   multi-agent surface Chronoa does not have, and none was invented for them.
   Discord/Telegram/Matrix integration is still not a goal (roadmap #22).

7. ~~**Tool Call Tracking** (P2, 1 day)~~ **DONE (2026-09-18).** Wired
   `ToolTracker` into `tools.py:execute_tool()` — every call (success,
   non-zero exit, or exception) is now recorded to an in-memory ring
   buffer (`_TRACKER.get_calls()`, last 100) and appended to
   `~/.local/share/shani-chronoa/logs/tool_calls.log`. Its own default
   `LOG_DIR` was `/var/log/shani-chronoa` — not writable by the normal
   desktop user Chronoa actually runs as; **confirmed live**:
   `ToolTracker()` with defaults raised `PermissionError` before this fix
   (the module's own unit test never caught this because it always passes
   an explicit `tmp_path` override). Changed the default to
   `~/.local/share/shani-chronoa/logs`, matching the sandbox executor's own
   per-user state directory, and capped the in-memory list to a
   `deque(maxlen=100)` (was an unbounded list) per the roadmap's own "last
   100" spec. **Verified live**: a real `execute_tool('get_datetime', {})`
   call produced a matching entry in both `_TRACKER.get_calls()` and the
   on-disk log file (roadmap #20).

8. **Per-Agent Sandbox Profiles** (P2, 2 days) — **WIRED (2026-09-30).**
   `sandbox/executor.py:23` imports `AgentProfile`/`ResourceCeiling`/
   `profile_for_origin` and enforces a per-**origin** profile, so an unattended
   trigger action gets a stricter sandbox than a user-initiated one. No profile
   field can *loosen* a guard: `ceiling()` only tightens the timeout, no
   allowlist widens, and the blocklist is not runtime-extensible. `memory_limit`
   /`cpu_limit` are enforced with `setrlimit` rather than left decorative —
   wiring the profile in without enforcing them would have created a new piece
   of dead config that merely looks configured. (roadmap #21)

9. **LICENSE file** (P3, 5 min) — **DONE.** A `LICENSE` file is present
   (audit-verified 2026-09-18); the master-roadmap #31 "4 repos missing a
   LICENSE" list is stale for this repo.

10. **Shared CI templates, Renovate, conventional commits** (P1, cross-repo)
    — Adopt `shani-ci-commons` (roadmap #7), add `renovate.json` (roadmap
    #8 — Python repo: pip + GitHub Actions), enforce conventional commits
    (roadmap #9). Still open. **Correction (2026-09-18):** the "this repo is
    not a git repo" note here was stale even at the time it was written —
    verified this pass: real git history, 9 commits, `git status`/`git log`
    work normally. Whatever blocked git detection during the 2026-09-17
    pass was environmental, not a property of this repo.

## The Siri/Google parity batch shipped a skill that could never run (2026-10-08)

Six builders were launched in parallel to close the gap between what Siri and
Google Assistant can do and what Chronoa could. The session hit a spend limit
mid-build; the files landed, the wiring did not. Four of the new skills
(`alarm`, `calendar_edit`, `maps`, `news`) had **no test file at all**, and
checking what they actually did turned up three defects that only running them
showed.

**`calendar_edit` refused every call, and the switch it named did not exist.**
Measured, through the real dispatch path:

```
create -> Refusing: changing your calendar is turned off
          (enable 'calendar-write-enabled' in Settings). Nothing was changed.
```

`calendar-write-enabled` was **not in the schema at all**, so `get_bool` returned
its Python default `False` for every value, and there was no row in Settings to
turn it on either. A permanent refusal wearing the shape of a permission - the
same class as the `heard-sound` sense above, where the only switch that granted
it was not on screen. This is worse than a missing feature because the skill
looks configurable: the model advertised it and the refusal explained itself.
Fixed by adding the key (default `false`), the Privacy row beside the read
switch, and the `GATED` entry. Verified by granting it: the skill then reaches
the calendar service and reports the honest environmental reason (no Evolution
Data Server bindings on this box) - a *different* sentence, which is what shows
the gate opened. `tests/test_parity_skill_gates_are_reachable.py`.

**`eds_calendar._escape` never escaped a semicolon.** Written as a backslash in
front of `;` in a non-raw string literal, which Python reads as just `;`. A
`SyntaxWarning` on every import (visible) and a malformed property value for any
title containing one (not visible). The other four escapes in the same call were
correct, which is why only this one was wrong.

**Eight of the twelve new tools fell through to the `Other` help group**, failing
`test_capabilities.py::test_no_builtin_skill_lands_in_the_other_group` - a test
that was already red on HEAD for an unrelated untracked file, so the failure was
easy to mistake for the known one. The full name list is the tell: the assertion
prints all eight.

**A documented invariant was nearly broken by the obvious fix.** `calendar_edit`
was in neither `MUTATING_TOOLS` nor `READ_ONLY_TOOLS`, so `cli_matrix`
classified it as neither actuator nor reader and no post-condition column was
computed for it. Adding it to `MUTATING_TOOLS` is the intuitive repair and is
**wrong**: the module states that a consent-gated tool must not be in that set,
because `tool_annotations` has a separate gated branch that already answers
`read_only_hint: False` and the two branches disagree about `idempotent_hint`.
`toggle_wifi` and `take_photo` were gated too, so all three had to stay out.
`alarm` and `routines` are ungated and are in it - `alarm` has a post-condition,
and `routines` gained one that re-reads the saved store, because a writer that
reports success for a store it never wrote is its own failure mode.

**A duplicate dict key had been silently killing a help label.** `browse`
appeared twice in `capabilities._GROUPS` with two different menu labels ("Use a
web page: click, type and read it" and "Drive a web browser"). Python resolves a
duplicate key to the last one, so the better label was dead with no error in
either direction - the table is correct as far as any reader can tell, and the
Help window showed the worse text. `python3 -m pyflakes` names it; nothing in the
suite did. Worth remembering that pyflakes is already this repo's declared lint
(AGENTS.md, "Lint with pyflakes") and a duplicate key in a data table is exactly
the class it is for.

**Two tests that could not fail, found by mutation rather than by reading:**

- `test_a_refused_confirmation_is_reported_as_a_refusal_not_as_a_change` asserted
  that the skill *asked*, so a mutation that skipped the confirmation while still
  calling `_confirm()` stayed green - the question was still asked, the answer
  ignored. It now asserts on `cal.remove_event` never being called.
- `maps`' location-gate test asserted the answer contained the word "location",
  which the *environmental* refusal on this box ("This computer's location is not
  available") also contains. With the gate deleted the test passed. The
  `locate` probe is now stubbed to **succeed**, so consent is the only thing that
  can stop the lookup.

**Three test bugs of my own, each recorded because the failure pointed at the
code instead of at the test:** a stub written with `netjson.get_bytes`'s
signature where `news._fetch`'s was needed (recording a params dict where a URL
belonged, so the section-feed assertion failed as if routing were broken); a
haversine band tightened to a distance remembered as ~115 km that is really
120.2 km, and a second pair asserted at ~1380 km when it is 735; and a store
reader that raised on an absent file, so "nothing was stored" assertions failed
with a `FileNotFoundError` that looked nothing like the bug.

Run: 224 passed across the parity, capability, consent, annotation and
post-condition suites. Each skill's gate, refusal and post-condition confirmed by
reverting it and watching the test fail.

## The wearable read twice; `listen` had to be rebuilt on bluez's own API (2026-10-08)

Asked how Da Fit changes smartwatch settings over Bluetooth, then asked to find
the paired device. Doing the second thing is what found four bugs in the first.

**The device is MoYoung.** Read through the real path, live:

| | |
|---|---|
| `FB BGS002` | `B3:69:73:62:B2:B6`, connected, RSSI -67, 9 services / 21 characteristics |
| Manufacturer Name | **`MOYOUNG-V2`** |
| Firmware Revision | `JLQFNJFD1.0` · Software `MOY-7OT4-2.0.1` · Sensor location `chest` |
| services | 0x1800, 0x1801, **0x180D**, 0x180F, 0x180A, **0x190E**, 0xFEEA, 0xFEE7, **0xAE00** |
| vendor command channel | **0xAE01 `write-without-response` / 0xAE02 `notify`** |

So the earlier research was right about the vendor and this is that vendor. **And
0xAE01/0xAE02 is the closed MoYoung protocol**, the one whose framing lives in a
closed-source AAR — see the Dart plugin's `MethodChannel.invokeMethod` chain with
zero UUIDs in its 93 KB Java layer, and `McuPlatform`'s six MCU dialects. Its
*shape* is now known and still not safe to drive: an unknown write to a wearable
is how firmware is bricked, so it is not implemented and that is a decision, not
an oversight. 0x190E (Phone Alert Status) is the GATT half of what
`bluetooth_call` does over HFP.

**`read` was truncating every multi-byte value.** `_READ_VALUE` was
`([0-9a-fA-F]+)`, which stops at the **first space** in gatttool's
`Characteristic value/descriptor: 06 4a 00`. Battery Level (0x2A19) is one byte,
so it read correctly and hid it completely — while a firmware string came back
as one character. Before/after on real hardware: `4a` → **`JLQFNJFD1.0`**,
`4d4f594f554e472d5632` → **`MOYOUNG-V2`** instead of "M".

Formats are taken from the binary's own strings, not from what a BLE tool is
assumed to print — `strings $(which gatttool)` gives both: a read is
`Characteristic value/descriptor: <bytes>`, a notification is
`handle: 0x0022 <tab> value: <bytes>`. The first version of the notification
pattern matched a *different* tool's format entirely and captured one byte, so
every live reading arrived too short to decode.

**`decode_heart_rate` had flags bit 0 backwards** (bit 0 is uint16, not uint8),
so **every 8-bit measurement** — nearly all of them — was refused with "the
measurement did not say whether the rate is 8 or 16 bit", the sentence you write
for a device that failed to set a bit rather than one that correctly cleared it.

### `listen` could not have worked, because gatttool cannot see a device bluez holds

The whole `listen` action was built on `gatttool --listen`, and on a real desktop
it fails **always**: gatttool opens a connection of its own, and bluez holds the
link to any in-range paired device by policy. Measured with the device connected
at RSSI -67, every call answers `Device or resource busy (16)`. Disconnecting
first works, but bluez reclaims the device and the next call fails — so it would
have worked in the seconds after a manual disconnect and nowhere else.

It now goes through bluez's own `org.bluez.GattCharacteristic1` first, with
gatttool as the fallback, and **says which route it used**. Four traps, all
measured, all silent:

- **`busctl get-property` takes the service name** —
  `get-property SERVICE OBJECT INTERFACE PROPERTY`. Omitted, every property
  returns empty, so **all 21 characteristics of a connected device read as
  absent** — a lookup that silently finds nothing.
- **`busctl tree` takes a service name, never an object path.** Given a path it
  prints `Invalid bus service name: <the path>`. The whole tree is fetched and
  filtered.
- **bluez is on the *system* bus.** `busctl --user tree org.bluez` answers "not
  provided by any .service files"; `--system` lists it owned by `bluetoothd`.
- **The method name moved between bluez versions**: the installed daemon has
  `StartNotify`, and `StartNotifications` is refused with `doesn't exist`.

`busctl monitor` is **polkit-denied** (`Access denied` on `BecomeMonitor`), so
the route polls the cached `Value` property at 200 ms rather than watching
`PropertiesChanged` — unprivileged, and a heart rate notifies at ~1 Hz.

### Two bugs that fix created, and the one that mattered most

- **`ay 0` is an *empty* array, not a zero reading.** The first version read it as
  the byte `0` and reported **"Battery Level: 0%"** for a device that had never
  sent anything — a believable, confidently wrong number about a battery, from a
  change made to *fix* a failure. Introduced by the bluez work.
- **`ay 1 00` parsed as `100`**, because the array *length* was taken for a byte,
  so every real value would have been off by one leading byte.
- A notify session left subscribed holds the connection, so `StopNotify` is in a
  `finally` and asserted to be called.

**And the device confirms why `read` can never answer heart rate**: bluez reports
`0x2A37` with `Flags: "notify"` — no `read` property at all. `read` on it is not
slow or flaky, it is impossible, which is why the refusal now names `listen`.

`0x180A` and `0x190E` were both advertised by that device and came back as
`UUID 0x180a` / `UUID 0x190e`. **Both were then checked against
`NordicSemiconductor/bluetooth-numbers-database` `v1/service_uuids.json`, the
SIG's adopted-number list, and one of the two names this file had recorded was
wrong:**

- **`0x190E` is not Phone Alert Status Service — `0x180E` is.** The name was
  right and the UUID was wrong, written from memory earlier the same day. The
  wearable advertises `0x190E`, which is **not in the SIG's adopted list at
  all**, so it is now reported by its number. That is the exact failure this
  module documents — a name attached to the wrong UUID reads as fact and nobody
  can tell it was invented. It also *shadowed* the real gap: `0x180E` was
  missing from the table the whole time.
- `0x180A` = Device Information: confirmed correct.
- Three added from the same source while checking: `0x1802` Immediate Alert,
  `0x1811` Alert Notification Service, `0x1812` Human Interface Device.

**`0x1811` is Alert Notification Service, not the Health Device Profile.** Android's
`BluetoothHealth` class is HDP, and HDP is **not a GATT service** — it is a
classic Bluetooth profile discovered over SDP. So Android's health-device support
maps onto a protocol `bluetooth_gatt` does not speak at all, which is a real
boundary and not a gap in the UUID table.

`0x0003`/`0x0004` remain named from the HID *usage* specification rather than
the SIG list, because they are HID usage descriptors and the SIG list assigns
those numbers differently (`0x2A4B` is "Report Map" there). Two numbering spaces,
deliberately distinguished in a comment.

**`bluetoothctl` on this box exposes no SDP at all** — measured: its menu has
`scan`, `devices`, `connect`, `disconnect` and nothing else; `search-services`
and `register-service` are absent. `rfcomm` *is* installed, so classic-Bluetooth
serial is available as a binary while SDP is not available through the menu.

### The manifest could not be built, and the suite could not see it

An apostrophe inside a single-quoted `optdepends` description —
`'pulseaudio-utils: pactl, for bridging a Bluetooth call's audio ...'` — closes
the quote early. `bash -n` reports `syntax error near unexpected token '('`, so
**the whole PKGBUILD stops parsing and makepkg cannot build the package at all**.
It was introduced in this session and caught only because a packaging test
failed with an unrelated-looking `ValueError: No closing quotation` from
`shlex.split`.

Every existing check in `test_packaging.py` reads the manifest as **data** — a
regex for the array, `shlex` for its entries — and both are blind to this.
Worse, `bash` itself then evaluated every entry *after* the apostrophe as
commands, so `sox` and the RHVoice voices were no longer declared at all and
`tts.py apply_timbre` would have been a permanent no-op on any real install. The
guard added is `bash -n`, the only one of the three that names the real problem,
plus a named check for the specific mistake.

Run: **301 passed** across the bluetooth, capability, consent, annotation,
post-condition and packaging suites. **20 mutations** (14 for the skill, 6 for
the manifest and the new bluez route) confirmed to fail.

## A Raspberry Pi HFP write-up, checked rather than believed (2026-10-08)

[Automating Calls on Real Mobile Devices with Bluetooth HFP on a Raspberry
Pi](https://sipfront.com/blog/2025/03/automating-calls-on-real-mobile-devices-with-bluetooth-hfp-on-a-raspberry-pi/)
(March 2025) is the closest public description of what `bluetooth_call` does, so
it is worth reading closely. **Its architecture is right and its tooling is
Debian's** — which is the useful distinction, because the interesting parts are
the claims that survived contact with this box.

**oFono is not on Arch, so every command in that post is unavailable here.** No
`ofono` package, no `/usr/share/ofono`, nothing named `org.ofono` on either bus
(measured). So `list-modems`, `dial-number` and `hangup-active-calls` do not
exist on ShaniOS. That is not a gap to close — `org.pipewire.Telephony` **is**
the equivalent, and it is what this skill talks to. Introspecting it live:

    org.pipewire.Telephony   (session bus, owned by wireplaster)
      ObjectManager          GetManagedObjects / InterfacesAdded / InterfacesRemoved
      org.ofono.Manager      GetModems / ModemAdded / ModemRemoved

PipeWire **re-exports the ofono interface names** rather than inventing its own,
which is why searching for `org.ofono` is the right instinct even on Arch — and
why `bluez_cards()` having to walk `/proc/asound` for the HFP card is the only
part of this path that is not already a D-Bus call.

**Their `pw-dump` caveat is stale, and it matters.** The post says
`pw-dump | jq` "does not work due to PipeWire producing broken JSON" and uses
`pw-cli ls` + scraping `object.serial` instead. Measured here: `pw-dump` exits 0
and yields **264,627 bytes that `json.loads` parses into 87 objects**. So the
text-scraping workaround is not needed on this PipeWire, and anything that wants
call-audio node ids should read the JSON.

**The one finding to take seriously is the mixing.** "When playing to and
recording from the phone at the same time, regardless of the sources and sinks
used, we always got both streams mixed into the recording", worked around by
tapping the PipeWire bluetooth *device node* rather than a sink. That is a real
limitation of routing through a sink, and it is the kind of thing that otherwise
shows up as mysteriously two-sided audio in a recording.

Also true and worth stating so nobody expects more: the HFP codec ceiling is
**CVSD 8 kHz or mSBC 16 kHz** even when the cellular leg is EVS/wideband, and
there is Bluetooth latency in both directions.

### What is actually missing, and one dead alias removed

The post treats **call events** as first class — "receive phone events (incoming
call, call closed)". `bluetooth_call` has none: `status/dial/answer/hangup/audio/
profile` all *act or report on request*, and an incoming call is never
signalled. That is the real gap, and it is the one worth building, because
"your phone is ringing" is exactly what a voice assistant should say out loud
and there is currently no way for it to know.

It maps onto the existing trigger engine — `read_btconnect` in
`triggers/desktop_sources.py` is the exact shape — and the data is there:
`InterfacesAdded` on the ObjectManager carries `org.ofono.Voice`/`org.ofono.Call`
for a modem, so an incoming call is a state transition on an object already
being polled. **Not built, and the reason is the one this file keeps recording:
there are zero gateways connected on any machine here, so a reader for it could
only be verified returning UNAVAILABLE.** The honest reading — "no phone is
connected" — is the easy half; detecting a real ringing phone is not something
a unit test substitutes for.

Second, smaller: `_audio_report` **reports** where call audio is going and what
is missing, and moves nothing. The post's actual subject is injecting and
capturing call audio by node id, which `pw-dump` now makes reachable here.

**Removed a dead alias found on the way.** `if action in ("audio", "route")`
accepted `route`, but the schema enum is
`["status", "dial", "answer", "hangup", "audio", "profile"]` — so `route` was
unreachable from the model and from every MCP client, and existed only as a
second spelling of a live action. Same shape as the slash commands "that are
advertised without a check": a name that reads as capability and can never be
invoked.

## NFC: the library is on every install and nothing reads it (2026-10-08)

Asked about [Android's NFC
overview](https://developer.android.com/develop/connectivity/nfc), which
describes three modes - **reader/writer**, **card emulation**, and **HCE** - and
noted that ShaniOS has libnfc. Both are right, and checking the second one
against the first is worth writing down.

**It is genuinely on every install.** `shani-pkgbuilds/shani-peripherals`
`depends` on `libnfc` (with `ccid`, `pcsclite`, `acsccid`, `opensc`,
`pcsc-tools`), and `shani-peripherals` is in **all four** image profiles -
`gnome` and `cosmic` in `Packages-Desktop`, `plasma` in `Packages-Desktop`,
`gamescope` in `Packages-Base`. Verified by reading the profile lists, not
assumed.

**And it ships the command-line tools**, which is the part that decides whether
a skill is possible at all. Arch's `libnfc 1.8.0-3` runs cmake without
`BUILD_UTILS=OFF`; upstream `libnfc-1.8.0/CMakeLists.txt` has
`option (BUILD_UTILS "build utils ON/OFF" ON)` and an unconditional
`add_subdirectory (utils)`. So the binaries are:

    nfc-list  nfc-scan-device  nfc-mfclassic  nfc-mfultralight
    nfc-jewel  nfc-emulate-forum-tag4  nfc-relay-picc  nfc-barcode
    nfc-read-forum-tag3  nfc-utils

Mapping Android's modes onto those: reader/writer and NDEF are `nfc-list` plus
`nfc-mfultralight`; raw tag tech is `nfc-mfclassic`/`nfc-mfultralight`; and
**HCE has a Linux equivalent after all** - `nfc-emulate-forum-tag4` presents an
ISO-DEP Type 4 tag and `nfc-jewel` a Type 1 one, so "tap the laptop with your
phone" is library-supported. It is **hardware-gated**: emulation needs a
PN532-class reader, and x86 laptops essentially never have an NFC controller
built in. Arch builds with `LIBNFC_DRIVER_PCSC`, `ACR122_PCSC` and
`PN53X_USB` on, so an ACR122U or PN532 USB dongle is the whole hardware
requirement - a few dollars - and `pcsclite`/`ccid`/`acsccid` are already deps.

**Arch's package is `arch=('x86_64')`**, worth knowing before anyone tries this
on a Pi - which is the platform the HFP write-up above is about.

**Nothing here can verify any of it.** This dev box is **Ubuntu 26.04, not
Arch** (the repo's own "the unit tests ran on the wrong distro" trap in a new
place): no `pacman`, no reader on the USB bus, no `/sys/class/nfc`, no
`/dev/nfc*`, no `ccid`/`pcsc` modules, and not one of the ten binaries. So the
read and write paths can only be written, not exercised.

Chronoa references NFC in exactly **one** line today - `airplane_mode.py`'s
rfkill label list includes `"nfc"` next to wlan/bluetooth/wwan/uwb - so the
radio is already acknowledged and the data path is entirely unwired.

## The `nfc` skill, and the one bit that killed it (2026-10-08)

Built on the finding above: libnfc is on every install, ten tools come with it,
and nothing read them. The skill is `scan` (is there a reader), `read` (tap a
tag, be told what is on it), `write` (a link onto a sticker) and `emulate`
(present a tag to a phone - Android's HCE, which libnfc can do via
`nfc-emulate-forum-tag4`).

**The NDEF decoder is written here rather than delegated to a binary**, because
NDEF is a published format and `nfc-list`'s output is a human dump. So the
verifiable part is the format itself, and the tests run against real bytes with
no reader present.

**One bit made the whole thing decode to nothing, silently.** The short-record
bit is `0x20`. A first version emitted `0xD1` - MB | ME | TNF=1, SR *clear* -
then wrote a one-byte payload length where the decoder, reading the same bits,
expected the high byte of a four-byte one. Every record produced decoded to an
empty list, with no error and no warning anywhere. The correct bytes are

    E1 01 0C 55 04 65 78 61 6D 70 6C 65 2E 63 6F 6D

which is what an NTAG213 actually holds, and the test asserts
`ndef_bytes("https://example.org") == that literal` **and** decodes the literal
separately - so a broken encoder cannot make the test pass by being decoded by a
matching broken decoder. The URI abbreviation table is also ordered longest
first, because `0x02` "https://www." has to beat `0x04` "https://" or the
result is `https://https://www...`.

**The destructive-write refusal runs before the tool check, deliberately.** With
the checks the other way round, the answer to "write to my bank card" was
"nfc-mfultralight is not installed" - so the refusal existed only on machines
that happened to have the tool. A refusal about destroying somebody's card has
to be the same answer everywhere. Eight different names (bank card, transit pass,
hotel key, credit card, fob, mifare classic, door card, smartcard) are all
refused, and the tests assert the tool is never *invoked*, not merely that the
answer sounds right.

**Three of my own test bugs, all the same shape - asserting the wrong byte.**
The record layout is `[0]` header, `[1]` type *length*, `[2]` payload length,
`[3]` the type itself, `[4:]` the payload. I indexed `[5]`, then `[4]` as a
length, before reading `ndef_bytes` again. The record was right every time.
Separately, four hand-written fixtures had length fields disagreeing with their
payloads and the decoder was right about all of them - so `record()` in the test
file **computes** its lengths, and the MIME/long-record cases are correct now.

**One equivalent mutant, kept and documented.** Making the *text* branch also
accept `"U"` changes nothing: the URI branch precedes it and returns for every
input. Verified rather than assumed - the mutation stays in the run and is
reported with the reason, alongside the real risk it stands for (the text branch
removed entirely), which is caught.

**Wiring:** `nfc-enabled` (default `false`) in the schema, a Privacy row, a
`GATED` entry and a Devices help-group entry, plus seven `files._PACKAGE_HINTS`
entries - without which `tool_missing` printed the literal
*"it comes from the 'the package that provides it' package"*, since libnfc ships
all ten binaries as one package. Two suite tables also needed it
(`test_skill_gates_are_enforced._IMPL`, and `libnfc` in
`test_package_names_match_arch`'s list of real Arch packages, verified against the
Arch package API: `libnfc 1.8.0-3`, `extra`, `arch=(x86_64)`).

63 tests in `tests/test_nfc_skill.py`, **23 mutations, 22 caught** and the one
survivor documented above. 366 passing across the bluetooth, nfc, capability,
consent, annotation, post-condition, packaging and package-name suites.

**Still unexercised:** every read and write path. This box is Ubuntu with no
`pacman`, no reader on the USB bus, no `/sys/class/nfc` and none of the ten
binaries. The refusals, the gate and the NDEF format are all verified; the I/O
is written and marked so.

## BLE: two hard boundaries, measured rather than assumed (2026-10-08)

From [Android's BLE overview](https://developer.android.com/develop/connectivity/bluetooth/ble/ble-overview).
It is conceptual - GATT/ATT, central-vs-peripheral, client-vs-server, and what a
descriptor is - and two of its points turn out to be limits of **bluez**, not
of the protocol.

> **WRONG - corrected 2026-10-09.** The paragraphs below introspected `/org/bluez`;
> the advertising and GATT-server interfaces live on the **adapter**,
> `/org/bluez/hci0`. There: `Adapter1.Roles = ["central", "peripheral"]`,
> `GattManager1.RegisterApplication` and `LEAdvertisingManager1` (12 instances).
> Proved by registering a `LEAdvertisement1` named "Chronoa": `ActiveInstances`
> went 0 -> 1 -> 0. So this laptop **can** be a BLE peripheral, a beacon, and a
> GATT server (man org.bluez.GattManager(5), org.bluez.LEAdvertisingManager(5)).
> Kept below as the record of how a wrong object path produced a confident "no".

**bluez is central-only. There is no peripheral role, so no beacon.** Android's
own page says it "provides built-in platform support for BLE in the central role",
which reads like an Android limitation. Measured on this box, it is also a bluez
one: `busctl --system introspect org.bluez /org/bluez` exports exactly three
interfaces - `AgentManager1`, `HealthManager1`, `ProfileManager1` - and
**`LEAdvertisingManager1` is not among them**. No `RegisterAdvertisement`, no
peripheral, no Eddystone, no iBeacon.

So the first use case on that page - *"interacting with proximity sensors to
give users a customized experience based on their current location"* - **cannot
be built on this stack at all**, and no amount of skill work changes it. Reading
a device that advertises is everything Chronoa can do; being one it can see is
not.

**`HealthManager1` being present corrects what this file said an hour ago.** The
entry above records that Android's `BluetoothHealth` "is not a GATT service - it
is a classic profile found over SDP, which is a different protocol from
everything above", which is true of `bluetooth_gatt` and misleading about the
machine: **bluez does expose `org.bluez.HealthManager1`**, in the same object
tree the GATT reader walks. HDP devices - blood-pressure cuffs, glucose meters,
the SIG-profiled ones - therefore have their own D-Bus home, reached through the
proxy bluez registers rather than through a characteristic read.

That is a better answer than reading a raw characteristic would have been, and it
is a different one: HDP carries RACP (remote access control), so the protocol
handles the session rather than this module interpreting bytes. **Not built.**
There is no HDP device connected to anything here, so it could only be verified
returning "nothing registered" - the same wall as `bluetooth_call`'s call
events.

**Descriptors are the third door, and this device has nothing behind it.** The
page notes a characteristic carries "0-n descriptors that describe the
characteristic's value ... a human-readable description, an acceptable range, or
a unit of measure". That is `0x2901` User Description, and it would beat any
UUID table this module maintains - the *device* would say what a characteristic
is. Measured: the wearable has **8 descriptors and all 8 are CCCD `0x2902`**,
which is exactly the set you need for `listen` to work, and is confirmation that
every one of its notify/indicate characteristics is subscribable. But there is
not one `0x2901` here, so the improvement is real and unexercised.

Worth noting as the reason to want it: `0x2901` is **authoritative where this
module's own table is not** - and this session's table had one wrong entry
(`0x190E` named "Phone Alert Status", which is `0x180E`). A device's own
descriptor cannot have my UUID transcription wrong.

## The wearable, driven live: find, listen, and what the suite was hiding (2026-10-08, later)

All of this was run against the real FB BGS002 (MoYoung) through
`tools.execute_tool`, with consent granted in a scratch keyfile GSettings
(`GSETTINGS_BACKEND=keyfile`). Use the group `[org/shani/chronoa]` — the schema
*path* — which is what the app itself writes; the `[org.shani.chronoa]` spelling
below records the older behaviour and a keyfile written in it is migrated on
read.

**`find_device` (new tool, same module, same `bluetooth-gatt-enabled` switch).**
Two fixed writes only: the SIG Immediate Alert (0x2A06 <- 0x02) and MoYoung's
`CMD_FIND_MY_WATCH`, taken from Gadgetbridge (`MoyoungConstants` 97,
`MoyoungPacketOut.buildPacket(mtu=20)`): `FE EA 10 05 61` to 0xFEE2, only when
the 0xFEEA service is present too. **The watch vibrated**, confirmed by the
person wearing it, first by hand and then through the tool. Measured traps:
bluez's `WriteValue` answers `Not connected` (this watch drops bluez's LE link
within half a second of "Connection successful"), and `gatttool --char-write`
only ever ends at its timeout; gatttool's interactive mode is the route that
works. A classic device (the JBL) is refused at once instead of waiting 25 s.
`bluetooth_gatt` is now in `READ_ONLY_TOOLS` (the one write is the separate
tool), so its reads no longer end with "unverified - nothing observed it".

**`listen` over gatttool never worked.** `gatttool --listen -a H` with no
command prints the usage text and exits 1, which read as "sent nothing in 15s"
after 4 s. It now writes 0x0100 to the characteristic's own CCCD, found with
`cccd_for` from `--char-desc` (never handle+1), and an early stop is reported
as one. Live after the fix: two real Heart Rate notifications, correctly
flagged "sensor contact LOST" for a watch not being worn.

**`list` sent people to connect a device a read reaches anyway**: a read on
the "not connected" watch succeeded and left it connected. The line now says so.

**Wiring the parity batch missed:** `fm_radio` named `fm-radio-enabled`, which
was not in the schema - a permanent refusal (same as `calendar_edit` above).
Added the key, a Privacy row, `GATED`, a help group, `_IMPL`, and
`rtl_fm -> rtl-sdr` (verified: `rtl-sdr 2.0.3-1`, `extra`, ships
`usr/bin/rtl_fm`). `nfc-enabled` had a Privacy row but was missing from the
settings-coverage test's `CONTROLLED`; the window-building test confirms both rows.

**Other suite fixes:** pyflakes found a dead duplicate `captions` key in
`tool_select` (the later entry always won); the cloud-keys wizard page was
590px of 560 because a sentence was appended to a `PreferencesGroup`
description (now its own wrapping label); the memory-ceiling test's
`'ALLOCATED-OK' not in out` matched the traceback echoing its own source line;
`timer` now accepts `60.0` by design, so the test moved it to the accepted list.

**Not fixable on this dev box (Ubuntu), not regressions:** two seccomp tests
need unprivileged user namespaces (`kernel.apparmor_restrict_unprivileged_userns
= 1`: bwrap "Failed to make / slave"); the vision real-path test times out in
`gnome-screenshot` on GNOME Wayland. And `bluetooth_call` could only be checked
saying "no phone connected": no phone is paired here as a hands-free gateway.

**Running the suite:** about 960 tests per sixth of the files; a serial run
was ~10% after 15 minutes. Running six file shards in parallel makes GUI and
live-audio tests flaky (`test_question_presenter` and others failed only
there), and one shard aborted inside GTK; re-run failures serially before
believing them. Running the package outside pytest leaves `__pycache__` in
`usr/`, which fails two packaging tests until removed.

**The phone (acer ZX), connected over HFP, same session.** `bluetooth_call`
answered "no phone is connected" with the phone connected, for two reasons
found only by running it: PipeWire 1.4's `GetModems` returns the gateway with
**no fields** (`{"/org/pipewire/Telephony/ag1":{}}`) while the skill kept only
rows with `Name`/`Address`; and the text parser expected a count before every
key, which busctl never prints - its fixtures had been hand-written in that same
wrong shape. Now parsed from `busctl --json=short`, the address read from
`GetManagedObjects` (`AudioGateway1.Address`), the name from bluez. Third:
`calls()` asked `GetCalls` of `org.ofono.Manager`, where it does not exist
(measured), so a ringing phone always read as "no calls"; it is on
`org.ofono.VoiceCallManager`. Live after: `acer ZX (74:6B:AB:67:7F:91), no call
in progress, audio: card 94`. Fixtures now carry the captured JSON; 3 mutants
caught. Nothing was dialled. **GATT on the phone is a dead end**: bluez shows no
GATT services for it (Android serves none to a classic-connected laptop), and a
separate gatttool LE link times out. What it does offer is AVRCP
(`.../avrcp/player0`, `org.bluez.MediaPlayer1`: Play/Pause/Next/Previous,
Position, Shuffle, Repeat), plus PBAP, MAP, OBEX push and PAN NAP in its UUIDs -
none of which Chronoa uses yet.

## The phone with no app: MAP, PBAP, HFP, OPP (2026-10-08, later still)

`phone_bluez.py` is a third `phone` backend, used only when neither GSConnect
nor KDE Connect is installed and bluez has a paired phone (`Icon: phone`).
Measured live on the acer ZX through the real `phone` skill: `status` (battery
20% from `org.bluez.Battery1`) and `messages` (225 messages, 67 conversations,
names from the phone's 813 contacts). Text and names were redacted in every
run; nothing was sent, dialled or shared.

- **obexd drops a session when the D-Bus client that made it disconnects.**
  Driven from `busctl`, one process per call, every PBAP/MAP session vanished
  before its first method, and the error ("Method ... doesn't exist") reads
  like the phone refusing. `_Session` holds one Gio connection for the lot.
  `Target` is lowercase (`pbap`, `map`, `opp`).
- **Android gates both per device**: Settings > Bluetooth > this computer >
  "Contacts and call history" / "Text messages". Off, PBAP answered
  `OBEX Connect failed with 0x46` and MAP timed out; `_why` names the switch.
- **MAP `ConversationId` is useless on this phone**: all 225 messages had the
  same one. Threads are keyed by correspondent (`thread_key`, last 9 digits).
  The text is in `Subject`; RCS/MMS arrive with an empty one.
- **Calls are placed, not typed into a dialer**: over HFP `dial` calls
  `AudioGateway1.Dial`, so the confirmation asks "Call X now from <phone>?"
  (`ph.places_calls()`) instead of "Open the dialer".
- Sending a text is `PushMessage` of a bMessage whose LENGTH counts bytes from
  BEGIN:MSG to END:MSG inclusive. Written and unit-tested; **not yet sent
  live**. Sharing is OBEX Object Push, files only. Ring and ping are refused
  over Bluetooth: no profile does it; a watch does it through its own app.
- A messages listing takes ~24 s (813 contacts plus 200 messages per call).
- **Music needs no code**: bluez's own `mpris-proxy` user unit (shipped in
  bluez-utils on Arch and Ubuntu) exposes the phone's AVRCP player as MPRIS,
  and `media_control` then reports "acer ZX is paused". It was enabled but had
  exited; keeping it running belongs to the image (`shani-settings`), not here.

**The watch as an input device for this laptop is blocked by pairing, not code.**
Connecting its HID profile logged `hidp_add_connection() Rejected connection
from !bonded device`: the watch dials in over classic HID, and bluez only
accepts classic HID from a bonded device; this laptop holds an LE bond only.
bluez's encrypted LE link is also dropped within a second while gatttool's
unencrypted one holds, which fits stale LE keys. Re-pairing is the fix, and it
is the owner's call because it touches the watch's pairings.

**Phone panel, incoming calls, call audio (same day, last).** `gui/surfaces/phone.py`
(Calls with dialer / answer / hang up / recent calls / audio route, Messages with
thread and reply, Contacts, Music, Watch) runs everything in threads through
`phone.py`, `bluetooth_call`, `media_control` and `bluetooth_gatt`/`find_device`.
Rendered headless on Broadway against the real phone: 30 calls, 40
conversations, 60 contacts, the watch row, status `ok`. Found by running it:
- **A phone serves one PBAP session at a time.** Loading contacts and call
  history together gave `OBEX Connect failed with 0x53` (busy); `phone_bluez`
  now holds one lock around every obexd session. After that burst the phone
  **withdrew** the contacts permission (PBAP refused, MAP still fine), so the
  person had to re-allow it on the phone.
- **`bluetooth_call` could never dial, hang up, or answer properly**: `Dial`
  was passed without its `s` signature; hang-up called `SendReleaseAndHangup`,
  which the gateway does not have (`HangupAll` it does); answer used
  `ReleaseAndAnswer`, which ends the active call to take a waiting one. Answer
  is now `Call1.Answer` on the ringing call. 3 mutants caught.
- **Call audio route** is `AudioGatewayTransport1` on the gateway: `Activate()`
  brings it to this PC, writable `RejectSCO` keeps it on the phone, `State`
  says which (flipped and restored live with no call). `bluetooth_call audio
  to=pc|phone` and the panel's This PC / Phone toggle use it.
- **`incoming_call.py`** posts an incoming call (named from cached contacts)
  with Answer/Decline via org.freedesktop.Notifications, critical urgency,
  `category=call.incoming`, and closes it when the call is answered anywhere or
  ends. Verified on a private dbus-daemon with stand-in services (6 tests, 3
  mutants caught); **not yet seen with a real ringing phone.**

## The watch's own protocol (MoYoung / Da Fit), live (2026-10-08, night)

`moyoung.py` + skill `watch` + the Phone panel's Watch tab. Protocol from
Gadgetbridge (`service/devices/moyoung/`, read that day), verified on the FB
BGS002 through `tools.execute_tool`: settings read (goal 10000, 12h, metric,
raise-to-wake on), clock set, messages sent, step goal 10000 -> 10001 -> 10000
read back each time, **SpO2 97 %** and **heart rate 100 bpm** measured on the
wrist. Steps, sleep, stress all answered with zeros (watch not worn / Da Fit
had synced them off); training and HR history did not answer on this firmware;
blood pressure did not answer in 70 s.

Measured, not from Gadgetbridge:
- **The watch takes one LE connection.** With Da Fit connected on a phone it
  stops advertising entirely; turning the phone's Bluetooth off frees it.
- **A set is unanswered and takes ~2 s**: read back after 0.5 s showed
  nothing; after 2 s the new value (`SET_SETTLE`).
- **Heart rate from command 109 streams on 0x2A37**, not as a 0xFEE3 reply.
- **Stress is 48 half-hour slots** (51-byte reply), not the 26 Gadgetbridge reads.
- **An empty night is one all-zero sleep triple**, not "awake at 00:00".
- Interactive gatttool prints `char value handle: 0x..`, one-shot prints `=`.
- **The sandbox's 30 s default killed every measurement** (and `bluetooth_gatt
  listen` above ~25 s, which accepts up to 60): `_SLOW_TOOLS` now has watch 150,
  bluetooth_gatt 100, find_device 60.
- Each skill call is its own process, so the one-connection rule is an flock
  per watch in `$XDG_RUNTIME_DIR`, not a thread lock.
- Weight is a profile value (`profile`), never measured.

From Maze Connect (github.com/berk-kucuk/Maze-Connect, read as a reference; it
is a LAN + own-Android-app link, so its transport does not apply): text from a
phone is screened for bidi overrides/isolates and control characters before it
is shown (`phone.clean_text`, applied to every backend's messages and contacts).

## Files both ways, the watch companion, AI voice (2026-10-08, late)

All verified live on the acer ZX and the FB BGS002 unless marked.

- **Files to the phone (OBEX Push)**: received on the phone, confirmed by its
  owner. `_Session` now subscribes to Transfer1 status *before* sending; the
  first version treated "the transfer object vanished" as success, which a
  refusal also does.
- **Files from the phone** (`obex_receive.py`, an obexd Agent1): Accept/Decline
  notification, then **staged in ~/.cache/obexd and moved to ~/Downloads**.
  Returning a Downloads path failed live: obexd refuses any path outside its
  root (`open(...): Operation not permitted`, OBEX Forbidden to the phone).
  Names are stripped of paths and direction characters, never overwrite.
- **Phone music without mpris-proxy**: it was measured exiting 1 at boot and
  **segfaulting** 40 s after a start. `media_control` now falls back to bluez's
  `MediaPlayer1` directly and **reads the state back** - Play was accepted with
  the player still paused when no music app was open.
- **Phone battery triggers work over Bluetooth** (`read_phone` via phone.py).
- **`watch_companion.py`** (switch `watch-companion-enabled`, off: holding the
  watch locks Da Fit out) - live: find-my-phone (98, `00` start / `ff` stop),
  camera (102), music next/play (103 `02`/`06`), volume (103 `04`/`05`, 0.40 ->
  0.55, echoed back as 103 [12, level/16]), and the `watch` skill served
  **through the companion's socket** while it held the watch. Reconnects by
  itself after a drop.
- **Found running it**: polling now-playing through `tools.execute_tool` every
  5 s tripped the repeat guard after 35 calls and then refused `media_control`
  to everyone - it reads the player directly now. A SIGTERM'd caller left
  gatttool holding the watch; gatttool now gets PR_SET_PDEATHSIG.
- **AI voice is 0xF9** (not in Gadgetbridge): `01 01 00`/`01 01 01` on press,
  `02 00` when it ends. Wired to Chronoa's listening (`_watch_voice`). The
  start was captured once and the stop twice; **not yet seen starting a real
  listening turn in the app**.
- **Weather to the watch** (requested by 100 on connect): Gadgetbridge's four
  packets, WMO codes mapped to the watch's icons, through the weather skill's
  web and location gates. Refused live by privacy mode (correct), then by **no
  location source on the dev box** - so `home-place` (Settings > Privacy) is
  now the fallback for the weather skill too. Not yet seen on the watch screen.
- **take_photo timed out at 30 s** when the camera button fired it; webcam
  capture itself, not the watch path. Not investigated yet.

## Calibration, and the log the learning layer trains on (2026-10-09)

- **The outcome model's probabilities are now calibrated** (`learning.fit_calibration`,
  `cross_fit_calibration`, `calibration_metrics`; stdlib, after the standard
  temperature-scaling method, plus a per-class shift for the prior the class-balanced
  fit removes on purpose). Fitted on `out_of_fold_logits` - the same folds
  `cross_validate` scores, which now shares that code - and scored by a 2-way
  cross-fit over feature vectors so its own before/after is held out too. Measured
  on this machine's 17,891 labelled calls: ECE **0.430 -> 0.107**, NLL 0.938 -> 0.547,
  Brier 0.561 -> 0.272, argmax accuracy 85.1% -> 85.8% (majority baseline 89.0%).
  T = 0.28: the balanced fit is *under*-confident, not over. Stored in the model
  file as `calibration`; a file without it loads as identity, and out-of-range
  values are ignored. A fit that does not lower held-out NLL returns identity.
- **`score_predictions` paired every prediction with the first call of its tool
  ever logged** (sorted with a constant key, never consumed a call). Now: the
  next same-tool call at or after the prediction's epoch (2 s slack, 600 s
  window), each call answering one prediction, plus NLL/Brier/ECE of the recorded
  probabilities. Negative control: widening the slack to admit earlier calls
  makes `test_predictions_pair_with_their_own_call_not_the_first_one` fail.
- **Tests were writing into the real user's `tool_calls.log`.**
  `tool_tracking.LOG_DIR` was computed at import and `tools._TRACKER` is built at
  import, before conftest redirects `XDG_DATA_HOME`, so every test that dispatched
  a tool appended to `~/.local/share/shani-chronoa/logs/tool_calls.log`. The
  development machine's 18,277-line log holds 374 `liar`, 378 `unver`, 369
  `add_reminder`, 40 `harvest2_tf_ok` fixture calls (and the heavy
  `get_datetime`/`screenshot`/`delete_file` counts look synthetic too) - which the
  outcome model trained on. Fixed: the path is resolved per write (module
  `__getattr__` keeps `tool_tracking.LOG_FILE` / `dream.LOG_FILE` working).
  Measured: four test files that added 2 lines to the real log now add 0.
  **The existing log was not cleaned** - it is append-only and the user's to
  decide; until it is, read the outcome model's numbers as being about a mix of
  real use and fixtures.
- **The distilled router has nothing to learn from on this machine.**
  `harvest_rows()` reads (request, tool_calls) pairs from saved sessions, and the
  only session file is test residue (128 user turns, no tool calls). The router
  trained in `shani-install-media/test-env/eval-out/distill-qwen3-0.6b.json`
  (62% vs 12% baseline on 8 held-out) was written into a throwaway HOME. Its
  `DISTILLED_MARGIN` stays uncalibrated until there is a router to calibrate.
- **Tool selection, Qwen3-1.7B, before/after on the same server** (15 cases,
  `select+recover` and `+greedy`): rewriting the `web_search`/`news`/
  `get_world_time`/`open_file`/`find_files`/`calculate` descriptions boundary-first
  changed nothing measurable (14/15 both). The one miss, `past-chat`, was the model
  putting the search words in `which`; giving `query` its own parameter description
  fixed it (3/3 re-run, both configs), and `search` also accepts `which` now.
  `search-web` ("search the web for the latest news about ISRO") accepts `news` as
  well, since it is a correct answer; `search-web-fact` is the case that requires
  `web_search`.
- **"what is two plus two" sent only `ask_user`**: `CONVERSATIONAL` treated every
  "what is X" as chat. Arithmetic (digits, number words, plus/minus/percent...) is
  excluded now; "explain gravity" / "thanks!" still are chat.
- **The OBEX receiver held obexd's only agent slot while switched off**, rejecting
  every incoming file and keeping the desktop's own receiver out. It now registers
  only while `phone-control-enabled` is on (re-read every 5 s), on its own thread's
  context: `GLib.timeout_add_seconds` attaches to the *global* default context, so
  the poll never ran (a test caught it) and the per-file decline timeout ran on
  the GTK thread.

## A pre-existing highpass that passed 5,000 Hz, and a rules file nobody could fix (2026-10-09)

Three failures that were **red on unmodified source**, found by running the whole
suite file-by-file (318 files, each in its own process — the per-file log is what
made "which ones are mine" answerable at all). Two were real, one was a missing
declaration, and **in every case the first explanation was wrong.**

### `fmdsp.butter_sos(..., "high")` built a filter with no zero at DC

`tests/test_fmdsp.py` was 5 red and stayed red through a previous pass, which is
how a real defect sits for months in a suite nobody reads to the end. Measured: a
**300 Hz highpass at 250 kS/s attenuated 5 kHz by 116 dB** — it passed nothing.
`highpass(4, 300)` reported 1.6e-06 where the test asked for > 0.95, and the
order-8 impulse response peaked at **8529** against a bound of 1000.

**How live this was: not at all, and that is worth stating before the severity.**
`grep` for callers first, as the Boundaries section says: `fmdsp.highpass()` and
`butter_sos(..., "high")` have **no caller in `usr/`**. The only product consumer
of this module is `skills/fm_radio.py`, and it uses exactly four of it —
`dc_block`, `lowpass`, `measure_tone`, `resample` — all of which were already
correct and still are. So nothing on a user's machine was receiving a silent
audio output; what was broken was a **latent** filter, correct in shape but wrong
in every number, sitting one call away from being used.

The exception is the point. `dc_block`'s own docstring says it **replaced** a
highpass Butterworth at the same cutoff for numerical reasons, so this was not
shipped audio that happened to sound bad — it was a filter that had already been
demoted, and the test kept pinning the mathematics. Had the caller count been
checked first, the honest write-up is "fixed and unreachable", not "fixed a live
defect". Recorded here because the repo's rule is the reverse of what I did: this
pass found a 5-failure test file and reached for the loudest true statement about
it before establishing whether anything depended on it.

The cause was a comment:

> *"The first version marked extra zeros with a (1 - z^-1)^2 numerator. That is
> not derived from this prototype - **a Butterworth highpass does not have its
> zeros at DC** - and it made the numerator near-zero across the whole of the
> passband."*

**That sentence is backwards.** A highpass blocks DC, which *means* a zero at
DC; a lowpass blocks Nyquist. So the "correction" removed the zeros on the
strength of a false premise, and left behind `H_hp(z) = (-1)^N * H_lp(-z)`
implemented as *keep the constant numerator, flip `a1`'s sign* — which is wrong
independently, because `z -> -z` flips the sign of **every odd power**, not one
coefficient. A comment that reads as settled mathematics is worse than no
comment: it is a reason not to check.

Fixed by `_highpass_poles()` — the analog prototype inverted with `s -> w/s`,
dividing by the same complex exponential rather than negating, because negation
reflects through the origin and puts some poles in the *right* half-plane, which
the bilinear transform then puts outside the unit circle — and by a `(1 - z^-1)^2`
numerator on every section. Measured after: **-48 dB at ¼ cutoff, -3.1 dB at
cutoff, ~0 dB in the passband, all poles inside the unit circle**, for orders 4
and 8 at 300 Hz and 5 kHz. 5 failures → 0.

**4 mutations, all caught:** zeros moved back to Nyquist (2), zeros removed
entirely (2), poles negated instead of inverted (2), the pole angle offset dropped
(2). The first two are the two wrong designs above, so the suite now fails on
each of them rather than on the consequence.

### Two test expectations that were measuring the harness, not the filter

Both were fixed **in the test**, and both are recorded because "fix the code, not
the test" is not the rule — *make the test measure the thing* is, and these two
were not measuring the filter at all.

- **`test_rolloff_is_twice_the_order` failed on its own clamp.** Order 8 read a
  slope of 14.0 dB/octave against 16. The true amplitude at 20 kHz is
  **2.8e-10**, and `max(amp, 1e-9)` reports -180.0 dB for it — so the test was
  dividing by its own measurement floor and the slope came out of a *clamped*
  number. Single-pass slopes, which never reach the floor, were 1.94 / 3.97 /
  7.94 against 2 / 4 / 8. The clamp is now 1e-15, chosen from this signal
  length and not picked to pass: Goertzel over 30000 float64 samples of an
  all-zero signal reads exactly 0.0, and order 8 at 40 kHz is 7.1e-15, i.e. at
  the noise. A control asserts the response is above the floor, and asserts the
  all-zero signal reads 0.0, so the floor stays honest as orders grow.
- **`test_it_removes_offset_and_keeps_audio` measured inside the transient.**
  `dc_block` is a one-pole `y = x - x[n-1] + r*y[n-1]`, `r = 0.999246` at 30 Hz
  and 250 kS/s, so its time constant is **1326 samples** and the offset decays as
  `r**n`. Measured means of the same signal: 33.16 whole, 0.0351 over
  `[10000:30000]` — the window the test used, still inside the decay — 1.9e-5 over
  `[20000:40000]`, 0.0 over the tail, with the 1 kHz audio at **1.002** amplitude
  throughout. The filter was never wrong. It now measures a settled window, **and
  a new test asserts the transient decays at exactly the rate the one-pole model
  predicts**, so the settling is checked rather than skipped past.
- **`test_lengths_match_the_ratio` asked for a length the input cannot produce.**
  `resample(sig_199999, 32000, 128000)` is a quarter of 199999 = 49999.75, and the
  code returns **50000**. The expected `(31999, 32000)` is the answer for a
  128000-sample input. Corrected, plus a property test over five rate pairs so a
  future pair cannot pass by luck. **Two equivalent mutants kept and documented**
  (reducing the pair list, weakening a bound) — both leave the file green because
  both are true, which is what an equivalent mutant is.

### `soundtouch` and `rubberband` were declared in Debian and not in Arch

`tests/test_prosody.py` was 4 red: `prosody.apply_song()` raises
`SingingUnsupported` because neither `soundstretch` nor `rubberband` is
installed. **`DEBIAN/control` has carried both since it was written; the Arch
`PKGBUILD` — the one that is actually built — declared neither.** So on every Arch
install `sing` refused for a reason nobody could act on, and sox cannot substitute:
`sox` shifts a whole file in one go, which is precisely the intonation path the
per-syllable design replaced.

Added to `shani-pkgbuilds/shani-chronoa/PKGBUILD`, with the package names read
out of pacman's own file database via `chronoa-matrix.json`
(`/usr/bin/soundstretch` → **`soundtouch`**, not a package named
`soundstretch`) rather than guessed, and offered rather than required for the same
reason as sox. `tests/test_packaging.py` now derives the requirement from the
binary names `singing.py` actually looks for, so a differently-named shifter is
caught at packaging time instead of at runtime; 2 mutations (each entry removed)
confirmed to fail, and `bash -n` passes.

The four measurement tests now carry `@needs_transposer`, whose condition is
probed through `singing._best_transposer()` — **the module's own chooser, not a
second `shutil.which`**, because two probes answering different questions is how a
suite skips where the feature works. And the branch a machine without a shifter
always takes is now **asserted rather than skipped**: the refusal names both
packages, writes no output file, and `singing_support()` reports
`can_transpose: False` instead of claiming the capability. Verified the other way
too, with a stand-in `soundstretch` on `PATH`: the four measurement tests run and
fail on pitch (the stub copies the file without shifting it), which is the
behaviour of a machine that *can* call the shifter.

### One lesson, repeated a fourth time in this repository

The stray-bytecode pair (`test_no_pycache_in_packaged_payload`,
`test_no_bytecode_files_in_packaged_payload`) went red twice during this pass and
both times it was **my own probe** leaving `.pyc` in `usr/`, not a regression —
`PYTHONDONTWRITEBYTECODE=1` on every invocation, and check whether the bytecode is
yours before believing the repo is broken. Verified by bisecting: no single test
file in the group writes bytecode, and cleaning between runs makes both green.

## Two gaps from `digital-travel-agent`, one of them a control (2026-10-09)

`harness-study/digital-travel-agent` (LangGraph, 15.4k LOC, pointed at a live
airline) studied for the first time; the write-up with every `path:line` is
`../harness-study/HARVEST-DTA.md`. Its headline mechanism turned out to be one
Chronoa already has and is further along with — the approval gate is
`permissions.decide()` inside `tools.py:_dispatch_inner`, central for all 190
skills, where the gate there is one `frozenset` beside the tools. Seven such
"already here" rows are in that document; **do not re-propose them.** Two things
were genuinely open, and the harness worked the same way it always has: the
first version of each was wrong, and running it is what said so.

### `rules.md` was a standing system-message injection with no content check

`user_prompts.rules_message()` put `~/.config/shani-chronoa/rules.md` in the
**system** role on every request, under the preamble *"follow them unless they
conflict with safety"* — and the safety half of that was the model's. On the
tool side Chronoa has a control; on the prompt side it had a prompt, which is
`digital-travel-agent`'s `graph.py:12-18` arriving from the other direction.

`provenance.py` fences content by **where its text came from**, and a rules file
is the user's own words, so it is unwrapped by design — correct, and it left
this uncovered. `commands/*.md` are safe for a different reason: the person typed
`/name` this turn, and `forced_tool()` refuses to fire on a user command of the
same name.

A rules file that reaches for tool behaviour or a gate is now **refused** — not
loaded, and the refusal replaces the file rather than sitting under it, because
two voices in the system role is the shape that loses. That is not theory
either: that project's recorded test is that appending a contradicting note
*below* an instruction left both present and the model still took the wrong one.

**Four false positives, all found by running the first regex, not reading it.**
A bare `do not ask` refused *"Do not ask me for the time; use my timezone"*, and
a bare `bypass` refused *"Bypass the corporate proxy for local addresses"* —
ordinary standing rules. Both now require their **object**; the word alone is
never the signal. Then the derived term list turned out to hold **nine gated
tool names that are simply English words** — `phone`, `watch`, `news`, `maps`,
`browse`, `notify`, `screenshot`, `temperatures`, `conversations` — and refused
*"My phone is called edit."* A term now qualifies only if it carries a `_` or a
`-`, which every consent key does and which 11 of the 12 destructive tools do.
The twelfth is `conversations`, carried by the phrase patterns, and that ceiling
is written down rather than patched with a hand-kept exception list.

**The term list is derived, never typed**, from `capabilities.GATED` and
`DESTRUCTIVE_CONSENT_KEYS`, so a newly registered destructive tool is covered
the day it is registered. `tests/test_user_prompts.py` asserts that as a
property of the module's **AST** — collection literals only — because a raw
substring search refuses its own test: the first version of it failed because a
*comment* explaining the derivation named `delete_file`, which is prose about
the list rather than the list.

**The refusal is visible in two places that cannot disagree.** `rules_verdict()`
is the single answer; `assistant.py` sends what it returns and `gui/rail.py`
describes what it returns. The rail says *which phrase* tripped it, because a
refusal nobody can locate is a refusal nobody will fix — and a refused file
showing **no** row would be indistinguishable from "you have no rules".

**Its ceiling is a soft check and cannot be a jail**, stated in the module
docstring for the reason `persona.py` states it: the refusal text and the system
prompt are plain text read by the same model. It catches the obvious case and
refuses it loudly. It is **not** applied to `commands/*.md`, because refusing
those is `provenance.py`'s documented mistake run backwards.

### A duplicate action is born at the approval

Two of the three ways a person can say yes are reachable **without the window
focused** — `approvals.py`'s `notify-send --action`, and the gateway's
worker-thread call — so a notification pressed twice, or a client reconnecting
and replaying after a timeout that actually succeeded, ran the same call twice.
`grep -rn idempot usr/ tests/` found nothing: only `idempotent_hint` on the MCP
descriptions, which is a claim to a client and not a control.

`tools.py` now keeps a bounded, TTL'd receipt for a completed gated call
(`REPLAY_TTL_SECONDS = 60`, `REPLAY_CAPACITY = 64`, `time.monotonic()` like every
other TTL here). The check sits **after** `permissions.decide`, so the question
is asked again every time and only the second *action* is skipped — hoisting it
would quietly undo both "allow once means once" and `is_bypass_immune`.

**The first version cached `ran=True, UNVERIFIED` and was wrong, and running the
real skill is what showed it.** In Chronoa that cell is where a skill's *own*
consent refusal lands — `DispatchResult`'s docstring says so — so a refused call
was stored as a completed one, and a retry after the permission was granted came
back with the stale refusal and could never run. **`VERIFIED` is now the whole
rule**, because it is the only verdict that means the effect held.

**What that costs, measured: 12 of the 62 gated tools** have a post-condition and
can reach `VERIFIED` — `airplane_mode`, `control_service`, `default_apps`,
`delete_file`, `desktop_setting`, `kill_process`, `office_document`,
`print_queue`, `set_hostname`, `set_locale`, `take_photo`, `toggle_wifi` — of
which four are also classified destructive. The other 50 are **not**
replay-protected, and `install_app`, `power_action`, `move_pointer`,
`click_pointer`, `type_text` and `connect_wifi` are among them. Widening it needs
a refusal marker in the child, as a sibling to `ToolFailure.MARKER` — a change
across the skill set, so it is noted rather than faked, which is the same trade
`DispatchResult` made. `tests/test_tool_replay.py::test_the_tools_named_as_covered_still_have_post_conditions`
fails if that list rots.

**Driving these for real needs a keyfile gsettings backend, not a stub.**
`GSETTINGS_BACKEND=memory` is per-process, so a grant made in the probe is
invisible to the sandboxed child that runs the skill and the refusal reads as
"turned off" however the parent was configured. Write the group
`[org/shani/chronoa]` — the schema *path*, which is what the app writes; a
keyfile using the older `[org.shani.chronoa]` spelling is migrated on read. With
that, `delete_file` really deletes and really verifies.

**Three of my own mistakes, all caught by running, all recorded because the shape
recurs.** The replay's own note was stored instead of the clean receipt, so a
*third* call came back with the note twice — the note now leads the text and
`_dispatch` skips re-recording anything that starts with it. The ordering test
scanned with `ast.walk`, which is **breadth-first**: it reported `replay` before
`decide` for a function where the opposite is true, until the calls were sorted
by line number. And `test_tool_replay.py` left `_REACTIONS` tripped and
`ask_bridge._presenter` clobbered, so `test_tool_outcome_structure.py` failed
four files later with *"has now touched 12 different targets"* — which read as
that file's bug and was this one's. Both globals are now restored, and the
fixture says why.

### Not done, and why

- **Capture-once-then-reuse for an approved argument.** The right shape is
  real — `digital-travel-agent`'s `payment_mandate.py:47-50` captures the exact
  passenger data shown on the checkout page so the later booking reuses it
  *verbatim instead of trusting the model to retype it several turns later* —
  and Chronoa already has both halves of the store (`undo_last_change.py`'s
  pre-image ring, and the Diff panel and rail that read it). What is missing is
  that `_resource_for()` builds the scoped resource from **the model's argument**,
  so a drifted path means the gate asks about the wrong file. **Not built:** which
  file a person *meant* is a design question about the selection UX, not a
  mechanical wiring, and shipping the wrong answer would make the gate describe
  the wrong resource with total confidence.
- **"Everything a tool says must have a spoken sentence."** `TurnEnvelope`'s rule
  that `text` is always complete is the most transferable idea in that repo, and
  Chronoa has three renderers of one turn — transcript, MCP result, and
  `speech.SpeechQueue`, which has no card to fall back on, so a result that
  exists only as a card is a turn that did not happen for a voice user. **Not
  asserted:** no claim is made that the gap exists. Checking it needs a
  `shani-testbed` `slot-test`/`app` action, because constructing a widget is not
  the same as looking at it, and this repo has two recorded instances of a width
  test passing against a row nobody could read.


## `audio.py` mistook a dead microphone for a silent room — and only under load (2026-10-09)

`test_child_supervisor_live.py` passed in isolation and **failed 4 of 5 runs**
during the 330-file sweep. The instinctive reading is "a flaky timing test, make
the timeout longer". It was not: it was a real defect, and the flake was the only
thing that ever showed it.

**The invariant.** A capture that ended the way it was meant to — silence, the
deadline, the user pressing stop — terminates its own child, so it is not a fault
and the child is forgotten. A child that **died on its own** is the fault and
stays tracked, because forgetting it erases the exit status at the moment it is
the only evidence there is: `pw-record` dying mid-turn is otherwise
indistinguishable from a quiet room (`on_done(None)` either way).

**The defect.** The `finally` decided this with `if proc.poll() is None`. Two
things are wrong with that:

- **The question is the wrong one.** `poll()` asks "has the child exited *by
  now*", not "did it die, or did we stop it". What is needed is "did the pipe
  close on us", which is only knowable at the instant the pipe closes.
- **It is racy in the main loop.** The `while` condition is re-checked *before*
  the blocking `stdout.read()`, so a read that overshoots the deadline — which a
  loaded machine makes ordinary — exits on the deadline and the closed pipe is
  never observed at all. The child is then classified as "ended the way it was
  meant to", forgotten, and its exit status goes with it.

`pipe_closed` now records it at the only moment the question can be answered, at
**both** EOF sites, and the `finally` reads that. It is declared **before** the
`try`, because the calibration loop can also see EOF — a child that dies before
producing four frames never reaches the main loop — and initialising it inside the
`try` (as the first attempt did) raised `NameError` inside a `finally`, replacing
the real fault with a confusing one and still reporting the wrong verdict.

**Four defects of my own here, and the two that mattered were both "fix the wrong
thing":**

- **My first fix moved the release to just after the callback**, reasoning that a
  caller asking `capture_status()` from its callback must find the child tracked.
  That is backwards for the clean-stop test, which asserts the opposite, and it
  *also* could not work: `_release()` tests `proc.poll() is None` and the
  `finally`'s `proc.terminate()` runs first, so it could never fire. Reverted; the
  real fix was in the **reason**, not the **position**.
- **That "fix" broke a second previously-passing test**, and the honest reading of
  the conflict is that the two tests were describing a genuine ambiguity — not
  that one of them should be edited to agree with me.
- **A new test I wrote failed**, and the fault was the fixture: 40 silent frames
  at 0.01 s is 0.4 s, more than the 0.16 s `silence_seconds`, so the *detector*
  fired first and my "child dies mid-turn" test was the clean-stop test wearing a
  different name. It now feeds loud frames only, so there is no silence to detect.
- **The mutation that proved the fix was ambiguous.** Both EOF sites are the same
  two lines of text, so the first attempt hit `anchor=2` and reported nothing —
  the recorded "`str.replace` was a silent no-op" trap. Mutating by line number
  found that **the mid-turn site is load-bearing and the calibration site is
  not**, which turned out to be the real finding: printing `proc.poll()` at
  calibration EOF shows **`None`**, because the child has closed its pipe but has
  not been reaped, so the old check reached the same verdict *by coincidence of
  scheduling*. One green test was covering a correct mechanism and hiding a
  defective one in the other path — the same shape as the other four cases in this
  file. Both sites are now pinned by their own test, and the calibration one is
  documented as an **equivalent mutant** rather than left looking load-bearing.

**Wall-clock budgets scale with load** (`_contention_scale()`, from
`/proc/loadavg`, 1.0 idle and capped at 2.5×). This is about **false negatives
only**: every use is a deadline on a wait that must *reach* a state before the
assertions run, so a longer budget lets a slow machine finish and cannot make a
real fault pass. Verified under **26× load on 8 cores**: 20 passed, where the
file failed 4 of 5 before.

224 passed, 14 skipped across the audio, capture, singing, DSP and packaging
suites.

### Three "environmental" failures that were two wrong guards and one honest refusal

The sweep's last failures are on this file's environmental list. Two of them were
**test guards answering the wrong question**, and each cost real time to
diagnose — the environment list says "environmental" and stops there, which is
exactly the "a skip reads as coverage" shape in reverse.

- **`test_sense_idle.py` (3): the guard asked whether a variable was set.**
  `skipif(not os.environ.get("DISPLAY"))` — and on this machine `DISPLAY=:0`
  **is** set while the X server has no `MIT-SCREEN-SAVER` at all, so the guard did
  not apply and three tests failed with the sense's own honest refusal,
  `MIT-SCREEN-SAVER missing on display ":0"`. The product was right and the test
  called the refusal a defect. It now asks `idle.read_idle_seconds()` — **the
  sense**, not the environment — for exactly the reason every other availability
  check here is asked of the component that does the work. The `os` import went
  with it; pyflakes was the thing that noticed it was now unused.
- **`test_vision_sense.py` (1): the guard read `display_environment()`, which is
  a fact about variables, and the test then burned the full 90-second capture
  timeout.** Measured here: `gnome-screenshot -f FILE` prints *"Unable to use
  GNOME Shell's builtin screenshot interface, resorting to fallback X11"*, then
  `Gdk-CRITICAL: gdk_pixbuf_get_from_surface: assertion 'width > 0 && height > 0'
  failed`, then **exits 0 having written no file** — the confident-wrong-answer
  shape this file keeps recording, in a real binary. The guard now attempts one
  real capture at a short timeout and asks whether it produced a sized image.
  That is 94 s → **11.5 s**, and the skip reason names the actual cause.
  **Checked that the new guard can answer True**, since a probe that is always
  False is the same defect as a skip: on a real `Capture` the predicate returns
  True for 1920x1080-with-bytes and False for the zero-sized and empty-data
  cases, so it discriminates.
- **`test_sandbox_seccomp.py` (2): left alone deliberately, and it is the
  example of the rule.** AppArmor restricts unprivileged user namespaces here
  (`kernel.apparmor_restrict_unprivileged_userns = 1`), so seccomp cannot be
  reached at all. The tests do not fail by asserting the filter works — they fail
  in their **own control**: *"the control did not escape, so the refusal below
  would prove nothing"* (`PermissionError: [Errno 13] Permission denied`).
  A check that refuses to certify itself when its premise is absent is the
  correct behaviour, and "make it green" here would mean deleting the control.
  It needs a kernel with Landlock reachable — `slot-test ... repo-pytest` on a
  real image, which is what AGENTS.md already says.

150 passed, 4 skipped across the vision, idle, sense-manifest and sandbox suites.

### The seccomp filter silently broke bubblewrap — and the last two "environmental" failures (2026-10-09)

`test_sandbox_seccomp.py` was 2 red and had been left alone twice with the
reason "Landlock and seccomp need the real kernel; AGENTS.md already says so."
**One of the two was a real product defect**, and it was invisible because the
environment table said it was expected. Checking the recorded reason rather than
the recorded conclusion is the whole lesson.

**Measured, directly.** Installing the real filter and then running the real
bwrap, in one process, in that order:

    filter ON  -> bwrap rc=1  bwrap: Failed to make / slave: Operation not permitted
    filter OFF -> bwrap rc=0

`_harden_child` runs as `preexec_fn` on the **bwrap process itself**, so the
filter is installed *before* bwrap builds the namespace it exists to build — and
the filter denies `mount(2)`, correctly, because Landlock has no mount right in
any ABI. So **every `LEVEL_1`/`LEVEL_2` call failed whenever the gate was on**:
a confinement mechanism refusing to confine anything, reported as an exit code
rather than as a security decision.

Fixed by withholding the filter **from a bwrap child only**, at the point where
bwrap is actually chosen rather than where `_PENDING_SECCOMP` is set — that
assignment happens earlier, and whether bwrap will wrap the command is only
known inside `_run_landlock`. Narrowing the deny list was rejected: `mount` is
the one syscall in that list a sandbox exists to deny, and bwrap's namespace is
already kernel-enforced confinement for this level. One layer lost, named in a
`logger.warning` once per level, rather than a hole left open quietly.

Verified after, through the real `SandboxExecutor` with the gate on:

    gate=false -> code=0 out='WRAPPER ok'
    gate=true  -> code=0 out='WRAPPER ok'

and confinement is intact — reading a file outside the allowlist still returns
`PermissionError: [Errno 13]` with no leak. One mutation (keep the filter on)
fails. 320 passed across the sandbox, actuator and matrix-skill suites.

**The second failure: the premise is absent, and my first two probes were wrong.**
The headline test's control needs `memfd_create` + `execve` to actually escape
Landlock. Here it does not, and I got the reason wrong twice:

1. First I concluded "the kernel returns `EACCES` (13), a memfd execution policy."
   Wrong: my probe used `['/nonexistent']` as the interpreter, so `execve` failed
   with **`ENOENT` (8)** — an absent interpreter, not a policy at all. The real
   payload, with `/bin/echo` copied in and executed, **works on the host**.
2. Then I wrote a probe that looked for errno 13 or `EPERM`. It found neither and
   returned False for the wrong reason — which would have skipped the test on
   *any* machine, for *any* cause. That is the "a check that cannot fail" trap in
   a new place: a probe that is always False is a test that always skips.

**The actual cause is an LSM, and it kills the process before Python can catch
anything:**

    Security violation: Requested utility `3` does not match executable name:
      /memfd:chronoa-probe (deleted)

AppArmor's `exec` profile, so there is no errno to read — the process is gone.
The probe now runs the **real payload** and asks the only question that matters,
"did it print `PREMISE-OK`?", which is the control's own assertion, so guard and
test cannot disagree. Verified it discriminates: `False` here, and `True` for a
control payload that does print — so it is not a test that always skips. 56
passed, 1 skipped with the reason naming AppArmor.

**What I got wrong by not measuring first, in one line:** I read "the environment
list says these are expected" as "these are environmental", and the first of the
two was a defect that had been sitting behind that sentence.

### `lab_network_destroy` refused every call, and its *plan* path could not
### work at all (2026-10-09)

Found while closing the gap left by `c2408de` (pyflakes: undefined names in
shipped code). That commit **fixed** the first of these two bugs. Neither had a
test, because `skills/lab_network.py` had no test file at all — and
`test_netprovision_routes.py` covers the *plan the builder emits*, which is a
different question from *whether the skill's gate opens and its teardown runs*.

**Bug 1 (fixed by `c2408de`):** one line refused every call.

    blocked = (reason if not allowed else _refuse("")) or _root_paths("")

With consent **granted**, `reason` is `""`, so the `or` moved on to
`_refuse("")` — a *truthy* refusal sentence — and never reached the name check,
the record lookup or the teardown. The undefined `_root_paths` behind it was
reported by pyflakes and simultaneously unreachable on every call, so linter and
runtime agreed on a fact that could never happen.

**Bug 2 (found here, still present after that fix):** the read-only half of the
same call rebuilt the network request **by hand and dropped `uplink`** — a field
the record has carried since the helper started writing it
(`usr/bin/shani-chronoa-lab-network:186`). `parse_request` rejects a NAT network
without an uplink, and NAT is the default, so:

    destroy WITHOUT apply, pre-fix:
      Not removed: the record for 'lab1' no longer validates (nat is on but no
      uplink was given, so a public subnet's default route would have no next hop...)

**Every plan preview was refused**, while `apply` worked — because that path
hands the name to the helper, which does its own correct rebuild. The two halves
of one call disagreed about what the record means, and only the read-only half
was broken, which is why it survived: the destructive path is the one a person
notices. Fixed with `_request_from_record()`, named so both halves share one
definition.

Verified through the real record store and real keyfile gsettings, against a
record with a real interface:

    plan shown?  True      (was: refused)
    apply ->     'destroy lab1 done'

`tests/test_lab_network_skill.py` (7 tests): the teardown is asserted on
`helper_calls`, **not on the return string**, because bug 1 returned a plausible
refusal sentence and asserting on text would only prove some sentence came back.
Five controls, each aimed at a mutation that would otherwise survive: consent
refused (catches removing the gate), root precondition consulted (catches
dropping the call — the granted path stubs it to `None` and would never notice),
an unrecorded name refused, a missing name asked about rather than guessed.

**Mutations: 6 confirmed to fail, and the original bug restored verbatim fails
6 of 7.** One survivor worth recording, because it was my mutation that was
wrong, not the test: inserting `blocked = _refuse("") or None` *above* the real
`blocked = _root_precondition()` left all 7 green, because the next line
overwrote it. That is the recorded "a `str.replace` that silently does nothing"
trap — asserted `s.count(old) == 1`, which passed, and still mutated nothing
behaviourally. The check that caught it was noticing that a *whole-file* fault
should not leave the file green.

**Two of my own test bugs, the second one worse than a failing assertion:**

- The first `RECORD` fixture had `subnets: []`, which `parse_request` **rejects**
  — so the plan test was measuring that rejection and calling it a plan. Then
  it had no `uplink` at all, the same trap one level down. Both fixed by
  building the fixture from the shape `usr/bin` actually writes, with the
  interface looked up from `/sys/class/net` rather than typed, since
  `parse_request` checks that the named interface exists.
- A `try`/`finally` restore that put `_invoke` back to itself — so an assertion
  failure would have left the module **globally patched for every later test**,
  a failure that reads as the next file's bug. `monkeypatch` throughout. This
  is the `NameError`-inside-a-`finally` lesson in a different guise: both are a
  failure corrupting the run instead of reporting itself.

75 passed across the lab-network, netprovision, capabilities, gate,
handler-importability, tool-outcome and labnetworks-sense suites; 35 more
across packaging and package names.

**`cli_matrix_v2.py` — deleted rather than merged (2026-10-09).** Asked to merge the
two matrix scripts. `tools/cli_matrix_v2.py` (891 lines, added 2026-10-07 in the
harvest2 commit and never referenced since) turned out not to be a version of
`cli_matrix.py` but an abandoned fork with its own incompatible CLI, so there was
nothing to merge *into* v1. Measured, not read:

| question | answer |
|---|---|
| callers anywhere in the repo | **zero** — no test, no doc, no harness, no `shani-testbed` slot-test |
| can it run today | **no** — its `main` requires `chronoa-matrix/chronoa-matrix.json` to already exist and prints *"No matrix JSON found"*; that directory only exists on a slot |
| does it read Chronoa's registries | **no** — `SKILLS_DIR` is defined and never used; `discover_skills`/`ast.parse` appear 0 times against v1's 2 |
| its own headline feature | `enhanced_intent_detection` is called **nowhere**, not even by its own `main` |
| what v1 has that it lacks | `--check`, `--diff`, `--audit-packaging`, `--suggest`, `--scaffold`, `--enrich`, `--resolve-files`, `--package-report`; and it reads the real skill schemas, so its `open_surfaces` measures *calls* |

**Five claims in its own docstring, and the two most interesting are false.** It
advertises "cross-profile analysis to identify consistent missing functionality" —
implemented nowhere in the file, while v1 has a real `audit_packaging` across the
image profiles. And "more comprehensive intent classification" names a function with
no caller.

**Its regexes are corrupted, which is how this was found.** `INTENTS` and
`CATEGORIES` have every alternative repeated two or three times — one 33-way
alternation contains 22 duplicates (11 unique terms), e.g.
`\bsearch\b|\blookfor\b|\bfind\b|...` with each word three times over. **`search`
appears twice in `INTENTS`**, so the first entry can never match anything. That is
not a stylistic complaint; a classifier whose first rule is unreachable and whose
rules are triple-counted produces a confident coverage number derived from nothing,
which is the exact failure this file's `--suggest` section warns about when it says
`open_surfaces` measures calls, not answers.

Removed with `git rm`. A fork nobody runs, that cannot start, that reads no
registry, and whose only distinctive function is unreachable, is not a merge
candidate — merging it would have put a second CLI and a second classifier into
`tools/` for a reader to choose between, with no way to tell from the code which
one produced a given number.

**The two matrix scripts were one live tool and one abandoned fork (2026-10-09).**
Asked to merge `tools/cli_matrix.py` and `tools/cli_matrix_v2.py`. Measured first,
because "merge" is the wrong verb if one of the two is not a version of the other:

| question | v2 |
|---|---|
| callers anywhere in the repo | **zero** - no test, no doc, no harness, no slot-test |
| can it run today | **no** - its `main` needs `chronoa-matrix/chronoa-matrix.json` to exist and prints *"No matrix JSON found"*; that path only exists on a slot |
| does it read Chronoa's registries | **no** - `SKILLS_DIR` defined and never used; `discover_skills`/`ast.parse` appear 0 times against v1's 2 |
| its own headline feature | `enhanced_intent_detection` called **nowhere**, not even by its own `main` |
| what v1 has that it lacks | `--check`, `--diff`, `--audit-packaging`, `--suggest`, `--scaffold`, `--enrich`, `--resolve-files`, `--package-report`; and it reads the real schemas, so `open_surfaces` measures *calls* |

So v2 is a fork with its own incompatible CLI, added 2026-10-07 with the harvest2
work and never referenced since. **Deleted with `git rm`**, not merged: merging
would have left two CLIs and two classifiers in `tools/` for a reader to choose
between, with nothing in the code to say which produced a given number - the same
defect as `scan_archive.py`, which was deleted as a superseded duplicate of a
strictly better guard.

**Two things worth knowing about v2 before anyone restores it.** Its docstring
makes five claims and the two most interesting are false: "cross-profile analysis
to identify consistent missing functionality" is implemented nowhere in the file,
while v1 has a real `audit_packaging` over the image profiles. And its regexes
are corrupted - `INTENTS` and `CATEGORIES` repeat every alternative two or three
times (one 33-way alternation holds 22 duplicates, 11 unique terms), and
**`search` appears twice in `INTENTS`**, so the first entry can never match. A
classifier whose first rule is unreachable and whose rules are triple-counted
produces a confident coverage number derived from nothing, which is the failure
`--suggest`'s own caveat already warns about.

**`--check` on a real image found a live defect, which is the argument for the
slot-test over any host test.** `set_theme` shelled to three KDE helpers it never
checked for. Nothing crashed - `_run_cmd` returns `None` and every caller handled
it - so on a GNOME desktop a person asking "am I in dark mode?" got a bare "no"
with no reason. Fixed both halves: `_missing_kde_tools()` checks first, and
`files._PACKAGE_HINTS` gained `kreadconfig6 -> kconfig` and
`plasma-apply-colorscheme -> plasma-workspace`, read out of pacman's file database
via `chronoa-matrix.json`'s `commands[].package` - **the only machine-readable
authority in the tree for what package ships a binary**. `plasma-lookandfeeltool`
and `kreadconfig5` get no hint on purpose: they are Plasma 5 names, absent from a
Plasma 6 image, so naming a package for them would be the invented answer that
table exists to avoid. Measured before/after on `@blue`: FAIL, then
`chronoa-deps-explained PASS (26 tool(s)/sense(s) need a command this image lacks,
and every one checks for it first)`, and tree-wide `unguarded_missing == []`.

Running the matrix outside a slot is a silent refusal: `--check` prints
*"No pacman database here: run this on a Shanios/Arch system or in a testbed
slot."* and **exits 0**, which reads as a pass. The run needs a bootstrapped slot
(`build.sh test ca`, then `bootstrap -p gnome -d latest`, ~40 min) and an output
directory that exists **inside the container's mount namespace** - a host
`/tmp/opencode/...` path makes nspawn die with *"Failed to clone ...: No such file
or directory"* while the overlay still reports success, so the failure looks like
a boot problem. Use `test-env/mout:/mnt/out`.

**Forty failures that were a missing display, not a broken product (2026-10-09).**
A chunked run came back with 59 failures where the last verified state had 6.
Nine were `test_window_input_and_copy.py`, **twenty-eight** were
`test_tool_activity_panel.py`, three were errors of
`test_help_gate_labels_match_settings.py` — and all of them **pre-existing**,
checked by restoring the senses directory from `9817575` where the same counts
fail. Not one was a product fault.

Each file runs a real `ChronoaWindow` in a subprocess. With no display backend
the subprocess died with `RuntimeError: Gtk couldn't be initialized` and
`Gtk-CRITICAL: ... 'GDK_IS_DISPLAY (display)' failed`, printed **no `RESULT`
line**, and the module fixture read the missing line as `{}`. Every assertion
then failed on **a key that had never been written** — `KeyError:
'send_visible'` for a send button that works on every display-capable machine,
where that file is **9 of 9**.

**An absence presented as a failure is the same defect as an absence presented
as a pass**, and this file has been bitten by both. The failure *text* is what
costs: it names the product, not the machine.

**`Gtk.init_check()` is not the question.** It returns `True` with no display and
every widget built afterwards is unallocated — `Adw.init()` warns `invalid
(NULL) pointer instance`, icon-theme lookups critically fail, and
`Gtk.ApplicationWindow.__init__` raises. A guard written against `init_check`
passes and then the window raises anyway, which is what the first version of this
fix did. The guard asks **`Gdk.Display.get_default()`**: whether a window can
exist, not whether the library loaded.

**Two halves, because one was not enough.** A function-scoped autouse fixture
covers the files whose harness fixture is function-scoped; a
**`pytest_ignore_collect` hook** covers the module-scoped ones, because a module
fixture is set up *before* any function fixture and raised into
`ERROR at setup` — three errors where a skip belongs, the same absence in a
different word. Refusing to collect is the honest shape for a file that cannot
run: no test to skip, because no test exists on this machine. Note the hook does
**not** fire for a file named directly on the pytest command line — verified,
and it is why the two halves are both needed.

**Neither half can hide a product fault**: it fires only when no display backend
exists at all, and `test_window_input_and_copy.py` is 9 of 9 on X11. That is the
control, and `tests/test_a_harness_that_cannot_run_says_so.py` holds it — with a
probe that proves `init_check() == True` **and** `display == None`, which is the
claim the entire fix rests on.

**A new sense, found by the matrix's own calibration and then checked against the
tree: `kernel_log`** (49 senses → 50). `chronoa-matrix.json` lists `dmesg` with
`used by: []` while `faults` reads **journalctl** and `containers` asks the
runtime — so nothing read the kernel ring buffer at all. That is the one record
no service has touched: a disk controller that reset, a USB denial, an OOM kill
before journald was running. Route order is `dmesg`, then `/dev/kmsg` drained
**non-blocking** (it is a stream; a blocking read waits for a message that may
never come and burns the whole skill budget), and a permission failure is
**UNKNOWN**, never an empty list — "no kernel messages" and "the kernel will not
tell me" are opposite claims.

**Two loader conventions that make a finished sense silently dead**, both caught
by running it rather than by reading: the loader reads **`SENSES`, not
`SKILLS`** (`senses/__init__.py:_register`), and a module carrying `SKILLS` is
treated as a library module and returns **without a warning** — the same defect
class as `SKILLS` itself had in the sense layer. And `sensitivity` must be one
of `personal`/`private`/`public`; `"machine"` is not in the vocabulary and the
loader refuses the whole sense over it. The kernel ring is `public`, beside
`faults` and `firewall`.

**`kernellog`, not `kernel_log` — and the reason is worth keeping.** The new
kernel-ring sense was written as `kernel_log`, and two rules collide on that
name. The consent key is **`<sense-name>-sense-enabled` derived from the name**
(so `kernel_log-sense-enabled`), and **`glib-compile-schemas` rejects `_` in key
names and discards the entire file when it hits one** — all keys at once, not
just the bad one. A single snake_case sense name would therefore have silently
undeclared every other key in the schema. Renamed to `kernellog`, which is legal
as a key fragment.

Measured, both halves: with `kernel_log-sense-enabled` the schema compiles to
*"Invalid name ... only lowercase letters, numbers and hyphen are permitted. This
entire file has been ignored"* and `test_sense_manifest` fails with *"these senses
are registered but have no consent key ... so `sense_allowed()` denies them
forever"* — the `calendar_write` / `fm_radio` dead-switch class, reached a third
way. The rule this file already records (*"a sense name must be legal as a key
fragment; there is a test asserting this across the whole registry"*) catches it
too late to be useful here, so the fix is the rename, and the schema now compiles
clean with `gschemas.compiled` produced.

Its Settings row sits **beside `faults` deliberately** — the same question
("what went wrong") asked of two different records, and a reader comparing them
is doing exactly the right thing. Off by default, unlike `faults`: the ring
usually needs root or a group membership, and it carries hardware detail rather
than a service's own account of itself.

## Eight skills enriched from the CLI matrices, and the three lies the work found (2026-10-10)

Both image matrices exist in the tree now — GNOME (`shani-install-media/test-env/mout/matrix-gnome.json`, 3,982 commands) and Plasma (`test-env/mout-plasma/shanios-matrix.json`, 4,116) — and this pass enriched **eight existing skills** from them. Every one was verified by running the real binary; the two that could not be were measured on a real slot afterwards.

| skill | what it answers now | verified |
|---|---|---|
| `read_document` | whether the PDF embeds its fonts (`pdffonts`), and pages/encryption/page size (`pdfinfo`) | real binary, this box |
| `firmware_updates` | which firmware the machine is *running*, not only what could update (`fwupdmgr get-devices`) | real, a live ThinkPad's 27 devices |
| `check_internet` | an IP conflict — one address at two MACs in the kernel's ARP cache, the classic cause of "works sometimes" | real `ip neigh` |
| `scan_document` | "no scanner" told apart from "SANE cannot open it" (`sane-find-scanner`) | real, no-scanner path |
| `system_info` | vendor, model, board, BIOS version, from `/sys/class/dmi/id` | real; `dmidecode` needs root and **exits 0** while refusing |
| `discover_hosts` | devices that announce themselves *by name* over mDNS (`avahi-browse`) | real slot, `@blue` 14 pass 0 fail |
| `check_units` | `systemd-analyze verify` — the command that answers "why won't it start" | real output, this box |
| `usb_devices` | driver and USB class per device (`usb-devices`) | real binary, this box |

**Three things the work found, each a false claim rather than a missing feature:**

1. **`pdffonts`' exit code had been measured through a pipe.** The docstring recorded *"exits 0 even on a file that is not a PDF"* — that code belonged to `head`. Measured properly: a non-PDF is **rc=1**, and a **fontless** PDF is the rc=0-empty-table case. Two different contracts, both handled, one of them cited wrongly. `tests/test_read_document_fonts.py` now covers each on its own terms.
2. **`check_units`' consent gate was a row in a table.** `capabilities.GATED` declared `service-check-enabled` and the skill never consulted it, and the key existed nowhere else — honouring the table would have refused every call forever. Removed: it is a read-only verifier and belongs with `list_services` and `read_logs`. This is the `calendar_edit`/`fm_radio` dead-switch class caught by the suite rather than by a user.
3. **The `pdffonts` parser misread real output on its first day.** The table is fixed-width, `Type 1` is two words and font names hold spaces, so field-index parsing counted a non-embedded font as embedded. Boundaries now come from the dashed rule under the header, and the fixture generator is asserted byte-for-byte against captured real rows — a hand-typed fixture had been one space narrower and was misread by the parser it existed to test.

**Five of my own test controls could not fail** and mutation testing is what caught them: a router that sorted first anyway, a zero-MAC listed once per different IP, a banner test that replaced a fixture line with itself, a composed test whose PDF had no text layer (so the OCR branch kept the mutation green), and a slot-test seam assertion counting stderr prose as table rows. Each is fixed and each is why the mutations below are recorded per skill.

**Where this left the harness.** `shani-testbed` gained `slot-tests/chronoa-cli-formats.sh` and its self-test `tests/chronoa-cli-formats-results.sh`, which settle on Arch the four output shapes the Ubuntu dev box could not run. **Run for real on `@blue`: 14 pass, 0 fail, rc=0**, and it measured the two facts this repo had been guessing — `arping -D` on an unanswered address **exits 0** (so rc=0 means *no duplicate*, the opposite of the guess), and `avahi-browse`'s service-type lines with both binaries at `/usr/sbin`. The rule above about a missing command being a slot run rather than a workaround is the lesson that produced the file.

**Still unmeasured on Arch:** `compsize` (btrfs compression ratio) and `bootctl` (EFI boot entries) are both open on the matrix and absent on the dev box, and both are in `chronoa-cli-formats.sh`'s presence list waiting for a run that exercises them.

## The ninth enrichment, and the marker that would have named the wrong slot (2026-10-10, later)

`boot_report` now reports **which EFI entry the firmware will boot by default**,
because on a blue-green machine "what will my machine boot into if I do nothing?"
has two answers that can disagree — what `shani-deploy` intends (which
`snapshot_status` reports) and what the firmware is pointed at. Nothing read the
second, and `bootctl list` is the only place it exists.

**The measurement corrected the implementation, which is the whole argument for
doing it this way round.** `shani-testbed`'s `chronoa-cli-formats.sh` ran it on a
real `@blue` slot, and this systemd's `bootctl` **has no JSON output** at all
(`--json` is refused with *"Unknown argument to --json= switch"* — three
invocations were tried and all three were wrong before the help text was
captured instead of guessing a fourth). The prose list carries **three different
markers that are not synonyms**:

```
title: shanios-green (Candidate) (not reported/new)
title: shanios-blue (Active) (default) (not reported/new)
 tries: 3 left; 0 done
```

`(Active)` is what is running now, `(default)` is what boots next, and
`(Candidate)` is the *other side of an auto-tries pair* — which here is green
while the default is blue. **The first version of the parser keyed on
`(Candidate)` and would have named the wrong slot on the very machine the format
was measured on.** They are read apart now, the `tries` line is reported
(auto-tries is the safety net someone asks about after a failed boot), and an
unmarked entry set says UNKNOWN rather than defaulting to the first row, which
is ordered by sort-key and not by preference. 9 tests, and the mutation that
restores the original bug fails four of them.

`compsize` (btrfs compression) was measured in the same run and **exits 1 with
"Not btrfs"** — nspawn's `/` is an overlay, so the honest result is a SKIP naming
the environmental cause. A real boot or `--volatile` would measure it; until
then nothing here answers "how much is compression saving me", and the entry in
`chronoa-cli-formats.sh` is what stands in its place.

**Four harness bugs the same runs found in the slot-test itself**, each recorded
because each is the shape that produces a confident wrong verdict: a FAIL with
no cause in its message (now prints stderr, and environmental is a SKIP), an
`else` merged onto a `res` line by an edit so an assertion silently never ran
while the summary still counted its results, two commands missing from the
presence list so a run could not say whether they were on the image, and a
truncated capture that left the parser writer completing a fixture by hand.

## Driver development: what this platform can and cannot do (2026-10-10)

Five skills, in two modules, built after reading both image matrices
(`chronoa-matrix.json` for GNOME 20260925 and Plasma 20260922, 3,982 and 4,116
commands) rather than guessing at what a Linux desktop can do.

**The headline is a negative one, and it is the reason this is worth recording.**
A kernel module **cannot be built on a stock ShaniOS image**: no compiler, no
`make`, no `dkms`, and no `linux-headers` on any desktop profile - only
`linux-api-headers`, which is the UAPI half and cannot build a module.
`linux-headers` is in exactly one profile (`server/Packages-Base`) and *no*
profile installs `base-devel`. `kmod` does ship (insmod/rmmod/modprobe/modinfo/
lsmod/depmod) and `binutils` does, so the load and inspect sides exist.

Two more facts make a module that *builds* still unusable, and both are why
`install` is deliberately not an action: `/` is a read-only blue/green slot and
`/var` is tmpfs, so a module has nowhere to persist across a reboot; and
`sbctl`/`sbsigntools`/`mokutil` are **server-only**, so a desktop has Secure Boot
with no tool to sign with.

**The user-space half is the feasible half**, and that is why it got the bigger
share of the design. Already on both images, no compiler involved:
`fusermount3` (fuse3), `v4l2-ctl`/`edid-decode` (v4l-utils),
`i2cdetect`/`i2cget`/`i2cset` (i2c-tools), `socat`, `udevadm`. Reading an I2C
register *is* a user-space driver, so `device_i2c` is the part that works here.

| skill | module | gate |
|---|---|---|
| `driver_status` | `driver_dev.py` | — (read-only) |
| `driver_scaffold` | `driver_dev.py` | — (writes new files under home, like `write_text_file`) |
| `driver_build` | `driver_dev.py` | `driver-build-enabled` |
| `device_probe` | `user_driver.py` | — |
| `device_i2c` | `user_driver.py` | `i2c-write-enabled`, **destructive** |

**Reads are ungated and writes are not, and that asymmetry is the design.** A
register write to the wrong address can erase an EEPROM's stored calibration or
drive a power device into a state nothing here can undo. The denylist runs
*before* the tool check, deliberately: a refusal about destroying a device has
to be the same answer on every machine, or it exists only where a binary
happens to be missing (the `nfc` bank-card rule, and the same reasoning that
kept the MoYoung wearable protocol unimplemented).

### Four bugs in the first version, all found by running it

1. **`obj-m := mymod` does not build a module.** I "fixed" this twice by
   reasoning and was wrong both times. Sweeping all five spellings against real
   headers (7.0.0-38-generic) settles it: **`obj-m := mymod.o`** builds, the
   bare form fails with *"No rule to make target 'mymod', needed by
   modules.order"* and the compile never happens - so the failure names a target
   nobody wanted. The template now carries the whole measured sweep as a
   comment, and `tests/test_driver_dev.py` asserts the active line.
2. **`/sys/kernel/security/lockdown` read whole instead of by its bracketed
   active mode.** The real file says `[none] integrity confidentiality`, so the
   code called an unlocked machine **"locked down"** and claimed unsigned
   modules would be refused when they are not - the dangerous direction to be
   wrong in. This is the one fact the matrix cannot supply, which is what
   `slot-tests/chronoa-driver-tools.sh` in `../shani-testbed` now measures.
3. **The EEPROM denylist could not fire on any input.** It was a dict keyed
   `(address, register)` holding what are plainly register *ranges*, so every
   lookup missed - a refusal that existed only in the source. Re-keyed on the
   register, and the message now says it is a **convention** and the datasheet
   is the authority, because a rule presenting itself as certain about the
   device in front of you is the confident wrong answer this file keeps
   recording.
4. **Two inverted verdicts**: dkms was in the "blocks a build" set, so a
   machine with a compiler and headers was told it could not build a module
   because the one optional piece was missing - contradicting the four lines
   above it - and I2C buses were sorted as strings, so `10` came before `2`.
   `driver_status`'s rows now carry their own `blocks_a_build` flag instead of
   two string-matching comprehensions disagreeing with each other.

The build is verified **for real**: a scaffolded module compiles through the
real `driver_build`, and `modinfo` reads the resulting 182,776-byte `.ko` as a
loadable module. The post-condition requires the `.ko` to be **newer than the
`.c`**, because a refused build leaves the previous one sitting there and
matching mere existence verifies work that never happened (the `pdf_pages`
defect). 34 tests, **7 mutations all caught** - two of which first reported
"passed" because `str.replace` silently mutated nothing (the anchor appeared
twice), which is why every mutation asserts `count == 1` first.

**The Arch claims are measured, not inferred.** `../shani-testbed`'s
`slot-tests/chronoa-driver-tools.sh` asserts them on a real image, with
`tests/chronoa-driver-tools-results.sh` (30 host checks) proving those
assertions can go red. Writing that file found three more bugs of mine: a
`present()` helper that returned true when **nothing had been measured** (11
PASSes from an emptied capture), and two FAILs for the same reason.

### Run on a real slot (2026-10-10): 13 pass, 0 fail, rc=0

`slot-test blue chronoa-driver-tools` on a bootstrapped `@blue`, and it settled
the claims above **from the image's own pacman database** rather than from a
matrix:

- **no `linux-headers` package installed** - the load-bearing claim, confirmed
  directly and independently of which kernel is booted;
- no `gcc`, no `make`, no `dkms`; `kmod` at `/usr/sbin` (modprobe, insmod,
  modinfo), so the load side exists;
- the image's own kernel is **`7.2.6.arch2-1`**;
- `fusermount3` + `/dev/fuse` present, and one real `fusectl` fuse mount in
  `mountinfo`, which exercised the `' - '` splitting `device_probe` parses;
- `v4l2-ctl` exits 0 with **0** video devices - the tool works, the slot has no
  camera, and the check reports both.

**And it found two more defects, both in what I had written:**

- **`uname -r` in a slot is the HOST's kernel.** Measured: `7.0.0-38-generic`
  while the image's own is `7.2.6.arch2-1`, because `systemd-nspawn` shares the
  host kernel (this file already says so about `audit-rules-loaded`). So the
  build-tree check was reporting *the dev machine's* headers while claiming to
  report the image's - a fact about the wrong machine wearing the right
  clothes. It now reads the image's kernel from pacman, and a `kernel_source()`
  line says which kernel any `uname`-derived fact describes. **A caveat of this
  shape has to be in the result, not only in the commit:** the slot-test now
  states that settling the real layout needs `vmspawn` or `iso-install`.
- **The separators differ, and reading the printed output is what showed it.**
  pacman says `7.2.6.arch2-1`; a booted kernel says `7.2.6-arch2-1` (Arch's
  pkgver `.` becomes `-` in `KERNELRELEASE`). Compared literally they do not
  match, so a boot running the image's own kernel would have been reported as a
  host kernel - attaching the wrong caveat to a real boot and hiding a real
  build tree. Both sides are now compared with separators folded away, and the
  printed pair is a test case.
- **A bus can exist with no device node.** `i2cdetect -l` lists `i2c-0` and
  `i2c-1` (Synopsys DesignWare) while `/dev/i2c-*` is **empty** in the slot. So
  `device_i2c`'s "there is no such bus on this machine" was wrong on the machine
  it was measured on: the adapter is there and the *node* is not, which is a
  udev question rather than a wrong bus number. It now says which of the two it
  is.

Two honest SKIPs, both environmental and both named: no
`/sys/kernel/security/lockdown` and no SecureBoot efivar under nspawn. **The
SecureBoot measurement is the one worth having** - it is the fact a desktop user
cannot read (efivarfs is root-only 0400) and that `driver_status` therefore
reports as UNKNOWN rather than guessing, and nspawn cannot settle it either. A
`vmspawn`/`iso-install` boot of the image's own kernel can, and is what would.

`/usr/lib/modules` is **writable in the slot**, which is the slot's overlay and
not the real blue/green layout - so that line reports both rather than
contradicting the read-only claim `driver_build` makes on a real machine.

### And running it end to end on this dev box: a 182 KB `.ko`, and a post-condition that judged the wrong tool (2026-10-10)

The whole loop was driven for real with `driver-build-enabled` granted in a
scratch keyfile GSettings: `driver_scaffold` wrote its five files, `driver_build`
compiled one, and `modinfo` read the result back off disk:

    filename:    .../probe.ko
    description: An out-of-tree module scaffolded by Chronoa
    license:     GPL
    srcversion:  3BD5FBA16EB91DABEECD91A
    name:        probe

**182,840 bytes** — so on a machine with a toolchain the five skills really do
produce a loadable module, which is the claim the whole driver set rests on and
which had only ever been asserted against a scaffold.

**The run also found a real defect, in the second skill rather than the first.**
`driver_scaffold` replied

    Scaffolded the module 'probe' in ...:
      probe.c  Makefile  dkms.conf  99-probe.rules  README.md
    ...
    This is source, not a built module. Ask driver_status whether this machine
    can compile it, and driver_build to try. (VERIFICATION FAILED: no .ko file is
    in ...)

`verification.post_condition_for` reads **one `POST_CONDITION` per module**, so
all three skills in `driver_dev.py` inherited the build's `.ko` freshness check —
including the one that writes source and the one that only reads. A
post-condition describing another tool's output calls a correct action a
failure, which is the confident-wrong-answer shape this file keeps recording, and
it is the same defect `verification._call`'s own docstring describes fixing for
`clipboard` (*"reading the clipboard was verified as a write of the empty
string"*).

**The fix was free, which is the part worth remembering.** `_call` already passes
the tool name to any check that takes two arguments, specifically *for* the module
holding several skills; `driver_dev` had simply never used it. `_post_condition`
now takes `(arguments, tool)` and answers `None` (UNVERIFIED) for `driver_status`,
the source-plus-`obj-m` claim for `driver_scaffold`, and the `.ko` freshness claim
for `driver_build`.

**Checked the other three multi-skill modules for the same shape:** `clipboard`
and `volume` already dispatch on the tool name, and `git_write` discriminates on
its own `action` argument, so it is correct. `driver_dev` was the only one.

**6 tests in `TestPostConditionKnowsWhichToolRan`, 41 in the file, and the
mutation (drop the dispatch, fall back to one shared check) fails 5 of them** —
restored and re-run green after. `shani-testbed`'s
`chronoa-driver-skills` gained `driver-scaffold-is-not-verified-as-a-build`, so
the dispatch path is covered on a real slot too: **19 pass, 0 fail** on `@blue`,
and `tests/chronoa-driver-skills-results.sh` is at **24 host checks** with that
mutation among them.

Two of my own test bugs while writing it, both recorded because both looked like
product failures: one assertion was pointed at the `project` fixture, which is
scaffolded *by definition*, so it asserted the opposite of what it claimed; and
the first version of the slot-test's new check drove a `driver_scaffold` call
with no `path`, which a post-condition cannot resolve and therefore answers
`None` for — a check that could never fail.

### The consent-write trap: `gsettings get` did not read what the app writes — FIXED (2026-10-10)

Driving the five skills on a real slot to close the last SKIP found this, and it
is worth knowing **before** anyone debugs a stuck consent key from a shell.

`ChronoaConfig._new_settings` built its explicit keyfile backend with
`root_group=SCHEMA_ID`, so the app wrote `[org.shani.chronoa]` while the
**default** backend — what the `gsettings` CLI uses, and what any other GLib
process on the machine uses — derives the group from the schema's **path** and
writes `[org/shani/chronoa]`. Both landed in the same file:

    $ cat ~/.config/glib-2.0/settings/keyfile
    [org/shani/chronoa]          <- what `gsettings set` wrote
    i2c-write-enabled=true

    [org.shani.chronoa]          <- what ChronoaConfig.set() wrote
    i2c-write-enabled=true

So **`gsettings get org.shani.chronoa i2c-write-enabled` answered `false` for a
value the app's Settings window turned on.** The switch says on, every gated skill
still refuses, and the CLI insists the key is off — a debugging path that
contradicts the thing it is meant to check. It was invisible because the app
reads back through `ChronoaConfig` too, so every round-trip test passed on both
spellings; **only a second reader can tell them apart.**

**Measured, not reasoned out** — GLib's own behaviour settled which of the two
spellings is "correct", because it is not a free choice:

    root_path='/org/shani/chronoa/'  root_group='org.shani.chronoa'  -> [org.shani.chronoa]
    root_path='/org/shani/chronoa/'  root_group='org/shani/chronoa'  -> [org/shani/chronoa]
    root_group=''                    -> refused: g_key_file_is_group_name assertion

`root_group=""` cannot be used to mean "derive it", so the group is now
`_ROOT_GROUP = SCHEMA_PATH.strip("/")` — **derived from the path rather than
written as a second literal**, so a path change cannot leave a stale spelling
behind (the same reasoning that derives `<sense-name>-sense-enabled` from a
sense's name).

**And the old spelling is adopted, not orphaned.** A keyfile written by an older
build still holds `[org.shani.chronoa]`, and a user who had already granted a
consent key would find every gated skill refusing while the switch reads on —
the dead-switch class this file keeps recording (`calendar_write`, `fm_radio`),
reached by a rename. `_adopt_legacy_group` reads it through `GLib.KeyFile`,
copies any key the correct group lacks, and removes the old group. Three things
running it settled:

- **The newer group wins.** Both spellings holding `model` keeps the correct
  group's value; an old copy must not overwrite a newer one.
- **The old group must go even when nothing was adopted.** The first version
  removed it only under `if adopted:`, so a keyfile whose keys were all already
  correct kept the stale spelling **forever** — measured as two groups after any
  number of runs. The control that caught it is a keyfile where *both* spellings
  hold the same key: the plain case adopts, so it converges either way and the
  mutation survived a green suite.
- **It is written atomically.** `GLib.KeyFile.save_to_file` rewrites in place, so
  a crash or a full disk between the truncate and the flush truncates a user's
  settings file — in the function whose whole job is rewriting it. `mkstemp` +
  `os.replace` now.

Verified: app writes, `gsettings get` reads `true`; an old keyfile keeps all
three keys and both readers agree; one group, no stray temp file.
`tests/test_config_gsettings.py::TestTheKeyfileGroupSpelling` — 6 tests, and
**both mutations confirmed to fail** (the group spelling reverted: 6 red; the
group never removed: 1 red, caught only by the control added above).

**Swept the whole suite rather than trusting the file:** 7568 passed. The 11
failures are all pre-existing or order-dependent — `test_capabilities` on the
untracked `log_files.py` (since fixed upstream), `test_routines_live` (the live
1.7B model's behaviour; now a SKIP naming the file — see its own section),
`test_task_eval` on a stale skill count (since fixed upstream),
`test_sidebar_toggle` (documented as failing 4/4 on untouched HEAD — it is green
on its own, so it is order-dependent rather than broken), and a trigger cluster
that passes **1201 tests** in its own chunk both with the fix and at HEAD. Two
earlier flakes (`multi_step_turns`, `question_presenter`) likewise pass in
isolation and in their own chunk with the fix applied — re-run failures serially
before believing
them.

The second half of the original measurement, for anyone writing a probe: **the
two writers are not equally culpable in how they fail.** Under the private session
bus `dbus-run-session` creates (no activatable `ca.desrt.dconf`),
`ChronoaConfig.set()` and `gsettings set` *both* return success, the writing
process reads its own value back as `true`, and nothing reaches disk — so a child
reads `false`. Set `GSETTINGS_BACKEND=keyfile` to test consent crossing for real;
`sandbox/executor.py:741` carries it to the child for exactly that reason.

### Booted on the image's own kernel (2026-10-10): every unanswerable fact, answered

`iso-install -p gnome --boot-only --console-exec` firmware-boots the image, so
`uname -r` is **the image's kernel** rather than the host's, which is the only
way to measure any of this (`../shani-testbed`'s `tests/driver-boot-facts.sh`).
Measured on the installed disk, as root, in one boot:

| fact | measured |
|---|---|
| kernel | **`7.2.6-arch2-1`** (pacman calls the same package `7.2.6.arch2-1`) |
| **`/sys/kernel/security/lockdown`** | **present, reads `[none] integrity confidentiality`** |
| enforced mode | **`[none]` — not locked down** |
| `/var` | **tmpfs** |
| `/usr/lib/modules` | **read-only** |
| `/lib/modules/<kernel>/build` | absent |
| SecureBoot efivar | absent (no UEFI Secure Boot variable on this boot) |
| `/dev/i2c-*` | **one node**, `crw-rw----+ root i2c` |
| `/dev/fuse`, fuse mounts | present, 4 |
| `v4l2-ctl` | rc=0, 0 devices |
| toolchain | no `gcc`, no `make`, no `dkms`; `kmod`, `i2c-tools`, `v4l-utils`, `fuse3`, `socat`, `udevadm`, `dracut` all present |

**Two of those confirm claims that only the matrix had suggested, and one
corrects a claim this section made.** `/var` really is tmpfs and
`/usr/lib/modules` really is read-only on a real blue/green boot — so
`driver_build`'s "cannot be installed from here" and `driver_status`'s
"nowhere to persist" are measured, not inferred. And `/dev/i2c-0` **does**
exist on a real boot, group-owned by `i2c`, which is the opposite of the nspawn
slot where the bus was listed and no node was created; both facts now have a
test, and the message distinguishes them.

**The correction: lockdown is `none`, so an unsigned module would load here.**
This section previously said a desktop "has Secure Boot with no tool to sign
with", on the strength of `sbctl`/`sbsigntools`/`mokutil` being server-only in
the profile. The signing tooling's absence is real; **Secure Boot being *on* was
never measured and this boot shows no SecureBoot variable at all** — so the
honest statement is that the profile installs no signing tools and that the
firmware state was not observable on either boot, which is exactly what
`driver_status` reports (UNKNOWN) rather than asserting either way. The real
barriers to loading a module here are the missing toolchain and having nowhere
to keep the result — **not** a signing wall.

**And the format bug would have shipped wrong on the target platform.** The
lockdown file on the image's own kernel reads `[none] integrity confidentiality`
— byte for byte the same as the Ubuntu dev box that caught the parser reading
the whole line. So the first version's "locked down" would have been reported on
the real ShaniOS kernel, i.e. a confident wrong answer about whether an unsigned
module loads, on the distribution the app ships to. The bracketed-mode reading is
now measured on the image itself.

**One measurement came back wrong before it came back honest.** The first boot
script called `pacman -Q` naively and reported `linux_pkg=` and `headers_pkg=`
as **empty strings** — which reads exactly like "this image has no kernel". It
has one: on a real boot `/var` is an empty tmpfs, so `/var/lib/pacman/local`
does not exist at runtime. `slot-tests/_pacdb.sh` documents precisely this and
mounts the read-only subvolume underneath it; the script now does that, and says
`unknown (no readable package database at runtime)` instead of an empty list. **An
empty string is not an answer**, and it is the same class as every `0/0` this
file keeps recording. Its `set -u` also died on `$_src: unbound variable` where
the label *existed* — this repo's documented command-substitution trap, in a new
place.

### Where this landed, for anyone reading `git log`

**All of the above is in commit `ec6b295`**, whose message is *"gui, tests:
SENSE_TITLES was a fifth hand-kept sense-name list..."* - a concurrent session
committed the tree with my work in it, and the commit message does not mention
it. `capabilities.py`'s half (the `GATED` entries, the `_GROUPS` rows, the
destructive key) went the same way inside `f5ece62`. Neither commit is on any
remote yet, so the split is still possible - **but another session is committing
in this repository right now, so rewriting history underneath one is a decision
for a person and not a mechanical fix.** This section is the record until
someone decides otherwise.

## FM stereo never worked, and infrared got a codec (2026-10-10)

Four defects made `skills/fm_radio.py`'s stereo decoder unable to separate a
channel at all, and none was visible by reading. Full methodology, the
measurement tables and the three mutations that first survived are in
**AUDIT-HISTORY.md**; this is the current state.

| defect | measured |
|---|---|
| **the discriminator had no phase wrap** — `atan2`'s branch cut injects a spurious `±2π` step whenever the accumulated phase crosses `±π` | true advance **1.41 rad**, shipped difference **5.79 rad**, wrong 24 000 times in 200 000 samples |
| **the de-matrix compounded two 0.5s into 0.25** | `L = 0.75L + 0.25R`; a hard-panned tone leaked a third of itself across |
| **`_deemphasis` had DC gain `a`, not 1** — `a*(state + (1-a)*value)` carries an extra `a` in the second coefficient | **-3.6 dB** on every signal |
| **the pole scaled with the rate** — `1/(1+2πτf)` is valid only while `2πτf << 1`, and at 32 kS/s that product is 15.08 | corner **14 146 Hz** at 32 kS/s, 145 037 at 200 kS/s, for a nominal 2 122 |
| the pilot was measured through a bandpass costing 30% of its amplitude | lock threshold margin **1.24×** |
| pre-emphasis used the audio-rate pole on IQ-rate samples | its shelf at 822 Hz, not 2 122; the pair did not cancel |

`exp(-1/(τ·fs))` gives the 2 122 Hz corner at every rate because
`-log(a)·fs/(2π)` cancels the rate exactly, and `_deemphasis_pole()` is a named
function so a test reads the module's own value rather than re-deriving the
algebra. Measured after, 1 s at 200 kS/s: left-only rejection **+32.4 dB**,
right-only **+35.1 dB**, hard-panned **+31.3 / +38.2 dB**, mono residual
**-23.1 dB**.

`tests/test_fm_radio_stereo.py` (29 tests) pins all of it; **10 of 10 mutations
confirmed to fail**. Two lessons in it worth keeping:

- **A single-tone level test through `demodulate` proves nothing.** It normalises
  every frame to a peak of 1.0, so every signal comes back at 1.0 whatever was
  done to it — a 6 kHz tone sent alone reads -1.77 dB against 300 Hz *with the
  transmitter given a lowpass instead of the pre-emphasis inverse*. The flatness
  test sends both tones in one signal.
- **The passband half of the anti-alias contract was untested.**
  `test_fmdsp`'s stopband test uses 40 kHz for `resample(x, 1, 2)` at 250 kS/s,
  which is *not above* the 62 500 Hz output Nyquist, so it cannot catch a filter
  that is too narrow. Halving the sinc's argument left it green.
  `test_the_passband_is_flat_up_to_the_output_nyquist` is the missing half.

Both FM fixes in `fmdsp.py` are now measured by DTFT of the filter the code
actually convolves with, rather than inferred from output amplitudes: the
lowpass's sinc argument was missing a **factor of two** (`sin(πfc·m)/(πm)` is a
lowpass with cutoff **fc/2**), which left it a mild shelf that never crossed
-6 dB at all, and the interpolator's kernel was never normalised.

### The `ir_remote` skill

`usr/lib/shani-chronoa/shani_chronoa/skills/ir_remote.py` — NEC and RC-6
pulse-space codecs in the standard library, plus a device probe, a send path and
a receive path. There is no IR hardware on the dev box, so **the protocol is the
verifiable half and that is what is verified**: a codec that can only be tested
on hardware nobody owns is a codec nobody has tested.

What ShaniOS really has, read out of the image's own pacman database:
`lirc` is a `shani-peripherals` dependency and is in **all four image profiles**,
shipping **29 commands**; `ir-ctl` and `ir-keytable` are **v4l-utils**, not lirc.
The send route is `ir-ctl --send=<file>` and its file format is quoted from the
man page (`carrier <hz>` then alternating `pulse`/`space` µs, **pulse first**),
because that is the one contract here a wrong guess would make useless.

**Every NEC frame is exactly 67 420 µs, and that is a theorem**: each byte
travels with its one's complement, so of any 16 bits exactly 8 are ones however
the byte was chosen. The decoder checks the total exactly, and a band would be
the weaker check rather than the safer one.

Wired the way `nfc` is: `ir-remote-enabled` (default false), a `GATED` entry, a
Devices help-group row, six `files._PACKAGE_HINTS` entries, a Settings row, and
`_IMPL` in the gate test. Reachability was proven through the real dispatch path
— the loader sees it, the handler's `__name__` is a real identifier, the gate
refuses by name with the key false and answers the device question with it true,
and the Settings row is confirmed present in the **built** `SettingsWindow`
rather than only in the table.

`tests/test_ir_remote_skill.py` (43), `tests/test_ir_settings_row_exists.py` (4).
**14 of 14 mutations confirmed to fail**, including the three that first survived:
an index of `2 + 2*25` flipped the *pulse* instead of the *space* at `3 + 2*25`
(which the burst-width check refuses for an unrelated reason, so the complement
check could be deleted silently), a carrier-line mutation placed in the inner
token loop where `break` is equivalent to `index += 2`, and a package-hint claim
untested by the codec's own file.

**Not claimed:** that a real remote recognises a frame this module builds, or
that `send`/`receive` drive `ir-ctl`. Both need hardware nobody here owns, and
the module that runs them says so.

## Containers and VMs: the two actuators that run a whole computer (2026-10-10)

`browse` drives a browser by putting a **window** beside a **protocol** - the in-app
WebKit the person watches, plus CDP underneath. The same shape now exists for the
two other things a person asks an assistant to *drive*: `container` (podman, plus
nspawn through `machinectl`) and `vm` (QEMU with a visible window, QMP on a socket
beside it). `container` is the *lifecycle* of a container and `vm` the lifecycle of a
virtual machine; **what either one draws on this desktop is driven by `ui_elements`**
(see the section below) - one way in, so a container request does not pay schema for a
driving vocabulary it will never use. Both are gated, default off:
`container-control-enabled` and `vm-control-enabled`, each with a Settings row, a `GATE_NAMES` label, a help-group
entry and a `_SLOW_TOOLS` budget (300s and 120s). "Which containers / VMs exist"
stays the read-only `list_containers` / `list_vms`; these two *run* them.

**Every podman and QEMU invocation goes through a transient `systemd-run --user`
unit, and that is measured rather than defensive.** A skill child runs under
`PROFILE_DEFAULT`'s 512 MB `RLIMIT_AS`, soft equal to hard, inherited by everything
it spawns and unraisable. Measured on this box with the real podman 5.7.0:

| ceiling | `podman run --rm alpine echo hi` |
|---|---|
| 512 MB | Go runtime traceback in `runtime.schedinit` - the client will not start |
| 1 GB | `runc: pthread_create failed: Resource temporarily unavailable` |
| none | works |

QEMU is the same wall: `ulimit -v 524288; qemu-system-x86_64 -m 1024` fails with
*"allocate 1073741824 bytes for jit buffer: Cannot allocate memory"*. A transient
user unit is spawned by the user manager, outside the skill child, and measured to
escape both problems - a unit started from a capped shell reads back
`Max address space unlimited unlimited`, a detached container outlives its unit
(`probe-nginx Up 2 seconds` after the client exited), and the invocation contract
through `--pipe` carries **stdout, stderr and the real exit status** (measured:
exit 7 in the unit, rc=7 in the caller).

**Four defects the first working versions shipped, all caught by running the real
thing, none by reading it:**

1. **The unit ran the wrong program.** The invocation was built as
   `systemd-run ... run --rm alpine`, so the unit tried to exec the bare word
   `run` and podman was never involved - `Failed to find executable run`. While it
   lasted, `rm`, `stop`, `ps` and `logs` were executed as *system* binaries. The
   regression test pins that the program the unit execs is `podman` and the
   subcommand follows it.
2. **A detached run died when its client exited.** With `--pipe` the invoking
   process holds the read end, and the container's `conmon` inherits the write end,
   so when `systemd-run` returns conmon takes a SIGPIPE and dies - leaving a
   container that `podman ps` still shows **Up** while `stop` reports
   *"conmon exited prematurely"*. A 2-second existence check cannot tell those
   apart; the fix (file-captured unit output for detached runs) is asserted
   structurally because the failure is invisible to a quick read.
3. **A hyphenated combo was sent as one key.** `ctrl-alt-f2` was split on `+` only,
   so the model's combo arrived as a single QMP key name and QEMU answered
   *"Parameter 'data' does not accept value 'ctrl-alt-f2'"* - while the schema's own
   example said `ctrl-alt-f2`. Both separators split now, in `_do_key`.
4. **The pointer could not move.** The launch attached `virtio-mouse-pci`, which is
   *relative*, and QMP's `input-send-event` with an `abs` axis is refused with
   *"Input handler not found for event type abs"* on anything without an absolute
   device. The launch attaches `virtio-tablet-pci` now.

**Two more, in the launch path, both about retries:** a unit that failed on a
previous attempt stays *loaded*, so systemd refuses a second transient unit under
the same name ("was already loaded or has a fragment file") - `reset-failed` before
the launch is what makes a retry possible; and the duplicate-name check read
`LoadState`, so a **failed** unit was reported as "already running" rather than as
dead.

**What is verified, and what is not.** On this box, for real: a one-shot
`alpine echo` through `execute_tool_outcome`, a detached nginx (Up 13 seconds
later - the SIGPIPE control), `exec`, `logs`, `stop`, `rm`, `images`, `machines`
(empty); a QEMU launch with a visible GTK window, `query-status`, a real 720x400
PPM screendump, `send-key`, typing, a click at 50%, and a QMP `quit` with the
unit going inactive (the post-condition reads that back, so `stop` reports
VERIFIED). UEFI is real: `firmware: "uefi"` produced a **640x480** screendump,
which is OVMF's own resolution and therefore the firmware that ran, against
SeaBIOS's 720x400 on the bios path.

**Three honest walls, each recorded where it is hit rather than papered over:**

- **A virtual TPM cannot be verified here.** swtpm starts and its socket appears,
  but the QEMU it is attached to creates its QMP socket, keeps running and never
  answers it - libtpms does not come up on this Ubuntu build. The launch names this
  specifically rather than saying "may have exited at once", and the path needs a
  ShaniOS slot run before it can be claimed. Two more measurements on the way
  there: swtpm refuses a socket under `/run/user/1000` (*"Could not open UnixIO
  socket: Permission denied"* - Ubuntu's `usr.bin.swtpm` AppArmor profile allows
  `owner @{HOME}/**` and libvirt's own paths, and the session runtime dir is
  neither), which is why the socket and state live under `~/.cache`; and swtpm does
  not create its own state directory, which is a fatal error *after* the socket
  exists.
- **nspawn's boot path is unverified.** Rootless `systemd-nspawn -D <rootfs>` fails
  on this box with *"Failed to allocate user namespace with 64K users: No such file
  or directory"* (AppArmor restricts unprivileged user namespaces), and a
  privileged `machinectl start` asks machine1 for a polkit authentication this dev
  machine cannot complete. `machinectl list` and the refusal sentence are verified
  (rc=1, stderr, no pipe); `machine-start`/`machine-stop` report whatever machine1
  says.
- **`ir-remote-enabled` and `broken_symlinks` were in-flight work in a concurrent
  session** and were red on this tree while it was being built; both have since
  landed (`ir_remote.py` with its Settings row, `broken_symlinks.py` grouped),
  and the gated keys that had no `GATE_NAMES` label - so their refusals named a
  raw key instead of the row's label - were completed here: 15 keys, each taking
  the label its own settings row already showed. That is the
  switch-nobody-can-find failure in the other direction, and
  `test_help_gate_labels_match_settings.py` only checks the labels that exist,
  which is why it never noticed.

`tests/test_container_skill.py` (17) and `tests/test_vm_skill.py` (26) include a
live test each - a real container and a real VM through the real dispatch path -
and 9 of the VM skill's 9 mutations and 5 of the container skill's 5 are confirmed
to fail when their defect is restored. Two of my first-round mutations were
*equivalent* (they changed nothing the test measured) and were rewritten; that is
the same "a control that cannot fail" trap, and it is why every mutation asserts
its anchor appears exactly once.

### Three things the full-suite sweep on that work found (2026-10-10, later)

A chunked re-run of all 318 files (8 chunks, one log each, per the lesson above)
came back **green except two things, neither of them a product defect**:

- **195 failures and 24 errors in one chunk were a full `/tmp`.** The messages
  were `OSError: [Errno 122] Disk quota exceeded` on a six-byte `write_text`, in
  the dispatch path, across a dozen unrelated files. `df` showed 13 G of a 16 G
  tmpfs in use, `ulimit -f` was unlimited, no quota tooling existed, and every
  write succeeded again after the box was restarted and the tmpfs cleared. **A
  full tmpfs on this box reports EDQUOT, not ENOSPC** - so a wall of
  "Disk quota exceeded" means "clean `/tmp`", not "fix the code". Re-running the
  same chunk afterwards: 842 passed, 1 failed. Anyone reading a sweep log should
  check `/tmp` before believing 195 failures.
- **`test_routes_live.py`... `test_routines_live.py::test_good_morning_runs_its_tools_on_the_real_model`
  fails, and it is not this work's doing.** The loaded model is
  `Qwen3-1.7B-Q4_K_M`, and for "weather in Pune **and** what time it is" it calls
  only `get_datetime`, then *narrates* "Let me check the weather in Pune" with no
  tool call behind it — the assistant's loop correctly ends a turn whose reply
  carries none, so the user is left holding a promise. Measured both ways: it
  fails identically with the two new skills hidden from discovery, so the failure
  is a property of the live 1.7B model rather than of the two added tools.

  **Fixed 2026-10-11 as a SKIP that names its cause, because a live-model test is
  the one kind whose result changes with what happens to be loaded.** Three ways
  of identifying the loaded model were tried and two are wrong, which is worth
  recording before someone re-derives it: `local_llm.active()` reads the symlink
  through `model_dir()` → `XDG_DATA_HOME`, which this file's `gsettings_env`
  fixture redirects, so it answers `""` under test; `/v1/models` reports the
  generic `current.gguf` for every model on the machine, so it identifies nothing;
  and `/props`' `model_path` is the **symlink**, so it must be `realpath`d before
  it names the file behind it. `SINGLE_TOOL_MODELS` holds the models measured not
  to manage two calls in one turn, and the skip prints the resolved path.
  Control run both ways: a tag that matches nothing makes the test **run** and
  fail on the 1.7B, so the skip is conditional on the model rather than permanent.

**And the duplicate-key defect has now happened three times, so it has a test.**
`browse` dead-labelled itself once, then `space_by_type` arrived with *three*
entries in one table (two from the session that added it, one from a
well-meaning pass adding a second label) while the table looked correct and
pyflakes was the only thing that noticed - AGENTS.md's own note says "nothing in
the suite did". `test_capabilities.py::...test_a_label_table_has_no_duplicate_key`
now parses the module source with `ast` and counts the keys the way the
interpreter will, for `_GROUPS`, `GATED` and `GATE_NAMES`. Mutation-verified: a
duplicate key reinserted fails it with the key named. The concurrent session's
own duplicate was resolved by keeping their label and folding both comments'
information into one entry - **the later entry wins at runtime, so a duplicate is
only ever a dead earlier label, never a wrong one.**

## Containers with a display, and the cue layer that makes driving them visible (2026-10-10)

`browse` is what "driving something in front of a person" looks like in this
codebase: a window the person can see, and a protocol underneath it, with a
**"Chronoa"-labelled cursor that glides to each target, a ring around it, and a
ripple on the click** (`gui/browser_driver.py`). Nothing else here owned a window,
so nothing else could be *watched* being driven. Two things closed that, and
both are measured on this box rather than reasoned about.

**`automation_overlay.py` - the cue layer for a display Chronoa does not own.**
A transparent full-screen layer whose widget has `set_can_target(False)`, driven
over a unix socket by newline-delimited JSON. It is a separate process on
purpose: the thing doing the driving is a sandboxed skill child with no GTK main
loop of its own, so the layer owns one - the same window-beside-protocol shape
`browse` has. It draws nothing and clicks nothing; it is a picture of an action.

**`container run display=x11|wayland` - the mechanism is `shani-testbed`'s and
`shani-desktop-plasma`'s, copied rather than invented.** The display's socket is
bound in, the variable that names it is handed over, and the toolkits are pinned
to their X11 backends with software rendering - because a container has no GPU,
and because a Wayland-native client cannot be driven by an X pointer
(`lib/nspawn.sh`'s `X11_BIND`, `tests/plasma-session.sh`'s Xvfb + `xdotool`
`mousemove`/`click`/`search --name` sequence, and the testbed's own comment that
a private Xvfb is what keeps clicks off the person's real screen).

**Verified for real, end to end, on this box:** `xeyes` from a container, with
`/tmp/.X11-unix` bound in and `DISPLAY` passed through, opened a **real window on
the host display** (measured: 200x187 at 893,463, found by title with `xdotool
search --name`), the cue layer was driven over its socket to glide the cursor to
the window's centre, `wait_until_pointing_at` confirmed the cursor had *visibly
arrived* before the real pointer moved and clicked there, and the window was
still alive afterwards. `xhost` was granted for the run and revoked at the end.

**Five defects the running of it found, none of them visible in the code:**

1. **The cue layer could not draw at all.** This PyGObject cannot marshal
   `cairo.Context` (a `DrawingArea`'s draw function never fires), cannot marshal
   `cairo.Region` (`set_input_region` fails with "could not find foreign type
   Region"), and **segfaults in `Gsk.FillNode` and `Gsk.BorderNode` when they are
   constructed directly**. The drawing therefore uses only `Gtk.Snapshot`'s
   `append_color`, `append_border` (whose widths *and* colours are sequences, one
   per edge) and `append_layout`, with the cursor glyph rasterised in plain
   Python into a `Gdk.MemoryTexture` - the one thing a vector fill would have
   been used for.
2. **A cue layer that refused the first ping.** A unix socket file appears at
   `bind()` and only *listens* a moment later, so the first connect of a freshly
   started layer was refused - and a driver reading that as "no cue layer" drove
   the machine with **no visible cursor at all**, silently. `cue()` retries the
   connect until its timeout now; the other half is asserted too, so a genuinely
   absent layer is still `None` rather than a five-second wait.
3. **The reply was queued on the main loop, so it was lost.** The client shuts
   its write end, the server's read loop sees EOF and closed the connection
   *before* the main loop ran the queued reply - measured as `connect OK` then an
   empty read. On Wayland the main loop can sit inside a composited frame for
   seconds, so a reply queued behind it also arrives after the driver has given
   up. Replies are sent on the socket thread now (`overlay.apply` only touches
   plain tuples), and the next frame is driven by the 16 ms tick.
4. **The ring faded *in*.** It was invisible at the instant it mattered - the
   click is about to happen *now*. It is on at once and fades over its whole
   life, and the test measures the alpha dropping rather than asserting
   present-or-absent, because a ring that never fades is still "present" at both
   moments.
5. **`Gtk.Snapshot.translate()` takes a `Graphene.Point`**, not two floats, in
   two places - and the resulting exception in `do_snapshot` killed the main
   loop, which is what made every ping fail while the process ran.

**What this box could and could not settle.** A TPM-equipped QEMU still never
answers QMP here (libtpms does not come up on this Ubuntu build), so
`firmware: "uefi"` is verified - the screendump comes back 640x480, OVMF's own
resolution - while `tpm: true` is written and honestly marked unproven. swtpm
refuses a socket under `/run/user/1000` (*Permission denied* - Ubuntu's
`usr.bin.swtpm` AppArmor profile allows `owner @{HOME}/**` and libvirt's own
paths), which is why the socket and state live under `~/.cache`.

## Driving any app on this desktop: one tool, one cue, one call per turn (2026-10-10)

`browse` drives a page it owns a window for. This is the same shape for **apps
Chronoa did not start**: `ui_elements` gained `drive`, `act` and `state`, over a new
app-agnostic driver, `usr/lib/shani-chronoa/shani_chronoa/ui_driver.py`. An app on
the accessibility bus is reachable by what its controls *are* (button, field, menu
item, with a name and a role); the input that reaches it is an X pointer on
XWayland or XTEST, or the RemoteDesktop portal for a native Wayland window - the
TeamViewer/AnyDesk mechanism. Every click glides a labelled cursor to the target
first and rings it, through `automation_overlay.py` (the section above), so the
person watching sees it happen.

**The shape is one call per turn, and that is the whole design.** A model that must
call four tools per step - read, click, read again, decide - spends its context on
round trips. So `drive` takes an action and a control *number* and answers with the
app's new state in the same short numbered form, plus the text on screen. Measured
on a real GTK4 app in a real container, through the real dispatch path: turn 1
returns 12 numbered controls and the OCR'd screen text in **795 bytes**; turn 2 is
`click 5` and comes back with the click's coordinates *and* the next state. The
driving loop is a model reading a numbered list and answering with a digit.

**The tool's own schema was the inefficiency, and it was measured rather than
argued.** The container skill had grown its own driving vocabulary (`windows`,
`click`, `type`, `key`, `read`, `screen`, `drive`) beside `ui_elements`' - two ways
in, with *different parameter names for the same thing* (`target` vs `number`), and
routing offered both. Measured: `container` was the **largest schema in the registry
at 3481 chars, ahead of `browse` at 3462**, carrying both halves, so a container
request paid ~1.3k chars for driving it would never do. The driving vocabulary is
now removed from `container` (3481 → **2146** chars, rank #1 → #5, ~335 tokens saved
per offering) and lives in `ui_elements` alone. Nothing was lost: the end-to-end
loop above still runs through `ui_elements`, and `container action=click` now
refuses by name with the lifecycle list.

**Two composition facts, both from a refusal rather than from reasoning.** `ui_elements
drive start` *nests* a `container` call, and the sandbox's ceiling can only ever
tighten: a nested profile asking for more than its parent already has is refused
with *"the 'default' sandbox profile allows 300 of CPU time but this process is
already limited to 185"*. So `ui_elements` and `container` are **equal at 300s** -
equal fits, anything less refuses. And the two consent keys compose rather than
merge: `input-control-enabled` gates the driving (it is input), and
`container-control-enabled` gates starting the container, each enforced by the tool
that does it, both named in the `drive` description.

**Reachability was measured, not assumed.** A skill nothing offers is the failure this
repo records most often, and the plainest phrasing - "click Fishbowl in the
container" - was answered by `browse`, `ask_user` and `get_datetime`. Three changes,
in the order the measurements forced them: synonyms (people say "container" and
"click"; the schemas say "image", "run", "drive"); then a **cue of the kind
`browse` has**, because "window" belongs to `close_window`/`list_windows` and page
words to `browse`, so no word score could join the request to this tool; then
**protection from the token cap**, because `_cap_to_window` dropped it anyway - its
schema ranks below `ask_user`, which is the same zero-sum the `browse` lines already
describe. Measured after: driving requests offer `ui_elements`, container lifecycle
requests offer `container`/`list_containers`, and "what time is it", "volume up" and
"save the file to my downloads" are unchanged. The cue is deliberately narrow:
"press the" was in the first version and displaced `press_key` for "press the enter
key" - two tests named it, and it is a keystroke, not a control.

**Verified for real, end to end, on this box:** the loop above; a click by name on a
control that was **off-screen** (scrolled into view, focused, wheeled over its own
column, re-measured between each - the testbed's `a11y_client` sequence); typing
into a field; `ctrl-alt-f2`; the cue layer's ping, glide and ripple; and a standing
window border whose pixels were measured (`(53,132,228)` at 255 alpha on the edge,
`(0,0,0,0)` inside). **Still open:** the RemoteDesktop portal accepts a session and
then stalls on this box - the route is wired, fails fast at a 5s client timeout, and
never claims a delivered effect it did not observe. `tools/route_cases.json` has no
`ui_elements` entry either: the corpus's own note requires every skill in it to
declare no required parameter, and `action` is required, which is a schema decision
rather than a typing job.

### And then it was run against this desktop's own app, which found two defects (2026-10-10)

Everything above was verified against an app *inside a container*, which nests its
controls at depth 5. Driving this desktop's own `gnome-text-editor` - a GTK4
libadwaita app, the second half of the original ask - found two real defects, both
invisible to the container test and both in the driver rather than in the skill:

1. **`_screen_box` raised instead of returning `None`.** It caught
   `(AttributeError, TypeError, KeyError)`, and the a11y bus raises **`GLib.GError`**
   for an object with no Component interface - measured, on an object at
   `/org/a11y/atspi/accessible/root`, so `state` came back as a **traceback** on a
   real desktop app. The sibling reader in `ui_elements` has caught broadly here all
   along, which is why only the driver's copy crashed. A position that cannot be
   read is `None`, never an exception.
2. **The tree walk's depth cap was silently throwing away every control.**
   `_walk_all` capped at **8**; gnome-text-editor publishes its buttons at depth
   **17-18** (467 nodes, deepest 26). Measured: **55 actionable named controls, 0 of
   them under the old cap** - so the observation answered *"none this window can act
   on by name"* about a window full of them. The cap is `ui_elements`' own 30 now,
   with a node bound of 800 so a deep tree is not an unbounded walk. Measured after:
   **12 numbered controls in 547 bytes in 0.6s**, on the desktop's own app.

`tests/test_ui_driver.py` (7) pins both, and **both mutations are confirmed to
fail** - the depth cap back to 8 fails 2, the narrow exception tuple back fails 1.

**What driving a desktop app can and cannot do here, measured rather than argued:**
reading works (above). Clicking *by position* refuses, correctly - no client may
learn where a native Wayland window is, and the refusal says so and names the
portal route. Typing and keys go through the RemoteDesktop portal, which does not
deliver on this box. Pressing a control **does** go through AT-SPI and returns
success - but a `New Tab` press reported success while the tab count stayed at
**3 before and 3 after**, so the effect was not confirmed. That is recorded as
unverified rather than as a working press: `_press` claims what `do_action`
returned, and nothing here observed it, which is the post-condition gap this repo
insists on naming.

One clean result from the same run, recorded so nobody adds it by reflex: **0
hidden** actionable controls - the observation lists only what is on screen, so no
"showing" filter was needed and none was written.

### The guard that only worked where it was not needed (2026-10-10)

Taking two ideas from an anti-surveillance script (`~/try/privacy-shield.js`) - an
eased pointer path with overshoot instead of a jump cut, and *refusing to type
until it has confirmed where the keys will land* - added a guard before the first
keystroke. It was written against an X window id, and **"we are on Wayland" is what
showed that was backwards**: `xdotool` hands out a window id only for an XWayland
window, so on this desktop the app being typed into is a native client that goes
through the **portal** instead - and the guard sat *after* the window lookup, i.e.
after the route it was meant to check. Measured: typing into a chat surface on the
Wayland route would have gone straight through.

`_comms_app` reads the app's own frame title from the accessibility tree, which every
toolkit publishes on either display server, so the refusal fires before the portal
is reached at all. Verified on the real desktop: `gnome-text-editor` is **not**
refused (it goes to the portal and fails honestly), and `slack` **is** refused on the
Wayland route. `key()` has the same hole and the same fix, because a chord can send
a message too. **4 of the new tests fail when the guard is neutered**, including one
that asserts the portal was never called.

Not taken from that file, and the reason is not squeamishness: the rest of it is
active *deception* - ghost-occupying the webcam so a capture process gets EBUSY,
muting the mic while "it believes a live channel is being recorded", nulling and
swapping frames inside another program's sqlite store, killing the capture process,
iptables-redirecting its telemetry, and manufacturing synthetic keystrokes to defeat
inactivity timers. **The agents it names are the same class as this workspace's own
`shani-insights`** - an enrolled, consented product whose own AGENTS.md says
retention and disclosure need HR-system scrutiny. Building that into the assistant
would be covert sabotage of a consented sibling product, on the same machines, hidden
from its operator; nothing in Chronoa's consent-key architecture was designed to
authorise it, and it would need its own trust story first. What *is* worth building
from the same source is the opposite: a **read-only detector** as a consent-gated
sense, which reports to the person rather than fooling anyone. **What was built
covers four channels: camera device holders, live audio streams, raw-input readers
with the compositor excluded, and monitoring agents by name.** Two probes from the
offer are **not** built, and the reasons are kept rather than dropped: `CAP_NET_RAW`
holders (a process's capabilities are readable only for this user's own on this box,
so it would report the compositor and the network stack and nothing else - the same
privilege wall the agent's connections hit), and the monitoring agent's media growth
(a sqlite row count in another program's store is a path into that program's data,
which this sense has no business taking).

### Built: the `monitoring` sense, and what it found on its first run (2026-10-10)

`usr/lib/shani-chronoa/shani_chronoa/senses/monitoring.py` - the detector above,
wired the way every sense is: consent key `monitoring-sense-enabled` (default
**false**, sensitivity `private`), the `_SENSE_CONSENT_KEYS` entry, the
`GATE_NAMES` label, and a Settings row under **Security and privacy** beside
`capture`. Registry **52 -> 53**, TTL 600 = poll 600, schema compiles to a real
`gschemas.compiled`. Eight tests in `tests/test_sense_monitoring.py`.

It reads: which process holds each `/dev/video*`, whether an audio stream is
capturing (`pactl`, `wpctl` fallback), who reads `/dev/input/event*` with the
compositor excluded, and whether a monitoring agent is running by name. **It
changes nothing** - no blocking, muting, occupying, killing or redirecting, and the
docstring says so because the two look alike from the outside.

**Run for real on this box, it found something immediately:**

    A monitoring agent is running on this machine.
    no camera device is held open
    no audio stream is capturing
    monitoring agent running: 1541 WorkpulsService /usr/lib/Workpuls/WorkpulsService
    unknown: the agent's connections could not be attributed to it (ss names a
    socket's owner only for this user's own), so whether it is uploading is unknown

So the anti-surveillance script it was read beside was not written about a
hypothetical: a Workpuls agent is running here. The sense says so and stops, which
is the whole difference between it and that script.

**Four defects of my own, each caught by something other than reading:**

1. **`_run` was defined twice** - the subprocess helper and the sense's entry point
   shared the name, so the second definition replaced the first and the sense
   **never registered at all** (52 senses, no `monitoring`). Renamed to `_probe`,
   with the reason in its docstring.
2. **Two generator expressions in one list literal** - a `SyntaxError` the loader
   reported and a read-through would have accepted.
3. **The Settings row did not exist**, which is the key-nobody-can-reach trap in
   its newest form: three tests, covering the schema key, the help label and the
   switch reaching config.
4. **UNKNOWN outranked a real finding.** The headline put "could not ask" above "an
   agent is running", so the one true answer read as no answer. Precedence is
   found-first now, and UNKNOWN is only for when *nothing* could be determined.

One limit recorded in the sense rather than papered over: `ss -tunp` names a
socket's owner only for this user's own processes, so **whether the agent is
uploading is UNKNOWN here**, not "it isn't". A second user's copy of the sense
cannot answer that without privileges, and saying so beats an empty list.

## Typing into a real desktop app: what actually delivers, measured (2026-10-10)

The delivery problem above ("XTEST exits 0 and nothing arrives") had **two**
independent causes, and each was found only by running against the live desktop.
The portal was cleared of a false accusation in the process.

**1. `xdotool search` returns Mutter's decoration frame, not the app.** Slack's
window is `10485898`, whose `WM_CLASS` is `"mutter-x11-frames"`; the real client
is its child `6291460`, carrying `WM_CLASS "slack","Slack"`. `windowfocus` on the
frame never moved focus; `windowfocus` on the client did, and the text landed on
the next attempt. `xdotool windowactivate` fails on both with
`XGetWindowProperty[_NET_WM_DESKTOP] failed`. `_client_window()` resolves the two,
keyed on `xprop -id ... WM_CLASS`.

  **Two bugs the unit test could not see, both found by running it:**
  - **`xdotool getwindowclassname` does not exist in this build** ("Unknown
    command", rc=1) and returns *empty* rather than failing loudly, so the first
    version decided every window was its own client - the resolution was dead
    code, and the test passed because its fixture supplied a value a real run
    could never produce. The test now mocks `subprocess.run` and asserts the
    `xprop` argv, and reverting to the nonexistent verb fails it.
  - **`xwininfo -tree` prints hex** while every `xdotool` call here speaks
    decimal, so returning its child verbatim (`0x600004`) broke every downstream
    lookup in a way that reads as "the window has gone".

**2. X focus and compositor focus are different things, and only one is enough.**
`getwindowfocus` reported the right window while `getactivewindow` reported
Mutter's guard window (4194307) - and every keystroke went to whatever window the
compositor actually held (a terminal). The existing test asserted the opposite,
on the reasoning that the compositor "lies"; it does not lie, it answers a
different question, and it is the only signal that says whether keys land.
`_focus_the_window` now checks both, and the refusal names the two ways out
instead of saying "focus failed".

**Why focus cannot be forced from the X side, and what does work.** Measured on
GNOME Shell 50.1: `_NET_CLIENT_LIST_STACKING` showed Slack's client frontmost
*of the X clients*, while `getmouselocation` at the point being clicked reported
window `1024` (a Mutter internal window) - a native Wayland surface was covering
it. Two stacking domains; only gnome-shell moves a window between them. And
GNOME 50.1's `org.gnome.Shell` exposes **zero methods** - no
`Application.Activate`, no `FocusApp` - so the only programmatic route is this
repo's own extension (`chronoa-windows@shani.dev`), whose header already says it
exists for this reason. `_focus_via_shell()` calls it first when the title
matches; enabling it remains the person's decision.

**The portal was wrongfully accused, twice.** It was recorded as "accepts a
session and then stalls". Measured: the backend is **version 2**, and
`persist_mode=2` plus a stored restore token meant the session was **granted in
0.0 s with no dialog at all**. What it lacks is *targeting* - like uinput it is
session-wide, so it delivers to whatever the compositor has focused. Both routes
therefore need the same focus fix, and neither removes the guard.

**Which injector, per the ArchWiki automation list, measured here:**

| tool | verdict |
|---|---|
| **ydotool** | **works** - uinput, so events enter as real hardware input |
| `wtype` | **cannot** - needs the virtual-keyboard protocol; `wayland-info` counts **0** on Mutter |
| `wlrctl` | cannot - wlroots-only (foreign-toplevel/virtual-pointer), Mutter implements neither |
| `python-keyboard`/`mouse` | not installed; same `/dev/uinput` requirement, plus global hooks needing root |

`ydotool type` also has a spelling trap: this build accepts **only** the short
options (`-d`, `-H`), and `--delay` is refused outright - the whole invocation
fails, so it types nothing while looking like a success.

**Two deployment facts, because the mechanism does not work without them.**
`ydotool.service` is `UnitFileState=enabled` but **`ActiveState=inactive`** -
enabled is not running, and the daemon present here was started by hand from a
shell. And `/dev/uinput` is `root:input`, so this route needs **membership of the
`input` group**, which a stock desktop user does not have. The driver therefore
falls back to XTEST when `ydotool` is absent, which is also the only route inside
a container's X display.

**Read-back needed a second reader.** Slack publishes **zero** editable AT-SPI
nodes, with or without a dialog open (Chromium builds its tree only for an
announced assistive technology), so `_read_focused_text` returns `""` and
delivery could never be confirmed. `type_text` now falls back to OCR of the
window - which is what actually read `test message` back out of the composer.

**Also worth knowing:** the ArchWiki records that **Electron has used Wayland by
default since 38.2**, so a newer Slack would be a native client with no X window
at all - which is the case the portal path exists for, and the one this driver
takes when `find_window` returns nothing.

**Which injector, researched rather than guessed (2026-10-10, later).** The
ArchWiki's automation list is the authority, and each entry was checked against
this box rather than assumed applicable:

| tool | verdict, measured here |
|---|---|
| **ydotool** | works - uinput, so events enter as real hardware input |
| **RemoteDesktop portal** | works - backend **v2**, granted in **0.0 s**, no privilege |
| `wtype` | **cannot** - needs `zwp-virtual-keyboard`; `wayland-info` counts **0** |
| `wlrctl` | cannot - wlroots-only; Mutter implements neither virtual-input nor foreign-toplevel |
| `python-keyboard`/`mouse` | not installed; same `/dev/uinput` need, plus global hooks |

**The portal is the better default on GNOME, and the reason is structural.** Every
input protocol that could *target* a window (`zwp-virtual-keyboard`,
`wlr-virtual-pointer`) is wlroots-only, so on Mutter there is no such protocol at
all - which is why `wtype` cannot work here and why Chromium ships its own
`remoting/tools/wdotool.py` built on "Gnome Remote Desktop and libEI APIs" for
this exact job. `libei`/`EIS` is the successor (`RemoteDesktop.ConnectToEIS`,
added in RemoteDesktop **v2**, which this portal reports); no `eis-0` socket
exists here yet, so the portal's fd handover is the route, and `liboeffis` is
the wrapper for it. Two limits recorded rather than discovered later:
`NotifyKeyboardKeysym` does not type a character directly - it hands the
compositor a keysym that is looked up **in the active keymap**, so characters
absent from the layout are silently dropped (ASCII is fine) - and the portal is
still **session-wide**, so it inherits uinput's one real limitation: it delivers
to whatever the compositor has focused.

So `type_text` now tries **portal → ydotool → XTEST**, **verifying after each**
and naming the one that worked, rather than trying them blind and claiming the
last. That ordering is by what the platform sanctions, not by what happened to
work on one box: uinput delivers, but it arrives as hardware input *without* the
compositor's access control ever seeing it, which is the thing Wayland's model
exists to police.

**And the window-stacking question is settled the same way.** `wdotool`'s own
compatibility table marks `search`, `getactivewindow`, `windowactivate` and
`windowclose` as **absent on every input backend** (libei, wlr-protocols, KDE,
GNOME, uinput) - no input protocol can raise a window, because that is the
compositor's authority and only the compositor grants it. KDE's door is kwin
scripting over D-Bus; GNOME 50.1's `org.gnome.Shell` exposes **zero methods**,
so this repo's existing extension is the only one. Which is why the focus guard
exists at all, and why its refusal names the extension rather than saying
"focus failed".

Two things this research corrected in this file's own earlier claims: the portal
was recorded as stalling (it does not - `persist_mode=2` plus a restore token
means no dialog after the first consent), and uinput was treated as *the* route
(it is the fallback; it needs the `input` group, which a stock desktop user does
not have, and its `ydotool.service` here is `enabled` but **inactive**, started
by hand from a shell).

## The portal works, and it could never type a space (2026-10-10, correction)

**This corrects two claims made earlier in this same session, both of which were
wrong and both of which I believed for several hours.**

**1. "The portal does not deliver" is false.** It delivers, and it is the route
that works. Proven by read-back on this box: `ZZPORTALMARK` appeared in Slack's
composer (a before/after OCR diff), `zzdirect` appeared as `zadirect`, `ab cd`
appeared as `ab cq`, and `test message` was read back **exactly**.

**2. Why it looked broken: `keysym_for()` could not type a space.**
```python
t = token.strip()          # " " becomes ""
if t.lower() in KEYSYMS:   # so "space": 0x20 is never reached
    ...
if len(t) == 1:            # and "" is not length 1
    ...
raise ValueError(...)
```
`type_text("test message")` raised `ValueError: ' ' is not a key name`
**before sending a single keystroke**, and `portal_type` turned that into a
`GuiUnavailable` which the route loop reported as a route that could not run.
Every single-word probe typed fine, which is exactly why it read as "the portal
is broken here" rather than "the portal cannot type a space". The fix is to test
length on the **unstripped** token first. **`test message` now types.**

**3. A third false signal: OCR mangles single characters.** `zzdirect` reads as
`zadirect`, `ab cd` as `ab cq`. Since the read-back is the only evidence of
delivery, an exact substring test rejects text that demonstrably arrived - and
the driver then blamed every route in turn. `_matches()` allows **one**
differing character and no more: a reader that accepted more would confirm text
that never arrived, which is the rc=0 lie wearing the costume of proof.

**4. Re-focusing a window that is already focused breaks what follows.**
`_focus_the_window()` returned success on an already-active window and the click
and keystrokes after it went nowhere - the app's internal caret is disturbed and
not restored by the click that follows. It now asks "is this already the focused
window?" **first** and touches nothing if so.

**The recipe that works, and it is not a privileged one:** `flatpak run
com.slack.Slack` (a second launch request makes the running instance raise its
own window - no extension, no D-Bus, no X-side trickery) -> confirm
`getactivewindow` is the *client* 6291460, not the guard -> click the composer
-> type through the portal. `type_text()` still does not confirm as a single
call although each of its steps does when run separately; the click point, the
re-focus and the cue layer have each been ruled out by measurement, and what
remains is **not yet identified**. Recorded as open rather than guessed at.

## The cue layer's cursor, found by rendering it and looking (2026-10-10)

Reported as *"the cursor isn't good"* and *"the blue box is at a different
location"*. Both are invisible by reading the code; both are obvious in one
render. The sprite was rasterised to a PNG at 12x and looked at.

**1. The glyph had no antialiasing at all.** One sample at each pixel's centre,
written as fully on or fully off, so both diagonals of the arrow were a hard
staircase - at 1:1 that reads as *broken*, not as *plain*. Now
`_POINTER_SAMPLES` squared subsamples per pixel with the colour averaged and
alpha as coverage: **81 of 263 edge pixels are partially transparent**, and the
edge blends with what is behind the layer.

**2. The quad was not an arrow.** Rendered, the old
`((3, 2), (10.5, 21), (13.1, 13.4), (21, 11))` is a lopsided blob - the fourth
corner sat almost level with the third, so the head was barely wider than the
tail, and at 22px both the head and the tail were **flush against the sprite
edge** (`right: 0`, `bottom: 0`), so the shape was being cut off square. Redrawn
at 24px with standard arrow proportions.

**3. The label was in two places at once, which is the whole of the second
report.** `snapshot.append_layout()` takes **no position** - it draws wherever
the snapshot has been translated to. The old `draw_tag` drew the blue box at a
fixed `+16, +16` and the text at `0, 0`, so the word landed on the cursor's tip
and the box floated off beside it: the label and its own background were never
in the same place. Moving the offset could never have fixed it, because the two
were never related. Box and text now share one origin, translated *into* the
box, and the offset is derived from `POINTER_SIZE` so redrawing the glyph cannot
strand it again.

**What the tests hold, and what they honestly do not.** Three properties are
pinned: antialiasing (a real count of partially transparent pixels), the glyph
not being clipped by its own sprite, and the label starting beyond the sprite's
width. All three fail when their defect is restored - measured, 2/1/1 failures.

**The silhouette itself is not pinned, because no cheap number distinguishes
it.** The first attempt compared head-width to tail-width and set the threshold
at 2x; measuring both shapes showed the ratio is **exactly 2.0 for both**, so
that test passed the blob and failed the arrow - "a check that cannot tell the
two apart is not a check". The clipping test also cannot distinguish them now
that the sprite is 24px and the old quad fits inside it. So the outline is
held by the render, and the guards are antialiasing, clipping and placement.
The tip is deliberately **exempt** from the clipping margin: a pointer's tip is
its bounding-box corner because it is the hot spot, and nudging it inward would
make the drawn arrow point somewhere other than where the click lands.

## Clicking a real desktop app: full-screen OCR, not the window's (2026-10-11)

**The window capture is the wrong coordinate space, and it cost an afternoon.**
`import -window <id>` gives pixels in the *window's* space; turning those into a
screen coordinate means adding a window origin, and on GNOME that origin is the
**decoration frame's**, which sits above the app's own client and **moves**.
Measured on Slack's window: frame at (67,32), client at (67,106) in one run and
(67,69) in another - the same window, 37px and 74px of disagreement. A click
computed from a window capture was landing **37px off**, which is most of a
sidebar row.

**A full-screen capture has no such arithmetic.** The Screenshot portal
(`org.freedesktop.portal.Screenshot.Screenshot`, signature `(sa{sv})` - the
**parent window argument is required**, and omitting it is an `InvalidArgs`, not
a default) writes a real **1920x1080 PNG** to `~/Pictures/Screenshot-N.png`.
OCR of that yields **absolute screen coordinates** with nothing to add.

Measured, same target, same moment:

| source | point for Abdul Bijur | result |
|---|---|---|
| window capture + client origin | (213, 819) | 37px low, **wrong row** |
| full-screen OCR | **(213, 782)** | **`title` became `Abdul Bijur (DM)`** |

**Three other full-screen routes are dead here, each measured rather than
assumed:** `import -window root` fails ("missing an image filename"), `grim` says
"compositor doesn't support wlr-screencopy-unstable-1", `xwd` is not installed,
`gnome-screenshot` exits having written nothing, and the private
`org.gnome.Shell.Screenshot.Screenshot` answers **"Screenshot is not allowed"**
- it is locked like `org.gnome.Shell.Eval`. The **portal** is the one that works,
which is the same answer the typing route gave.

**And the portal's file must be found by mtime, not by "a new file appeared"** -
it reuses `Screenshot-N.png`, so a set-difference against the previous listing
finds nothing and the first version of this raised `TypeError: expected str ...
not NoneType` from the copy.

**The click itself is XTEST and it works** - measured with the pointer check
that matters: `getmouselocation` must report the app's **client** window, not
`1024` (a Mutter internal window, meaning the point is not over the app at all).
The driver now resolves the frame to the client before any geometry is read.

Also fixed in the same pass, all mutation-checked or measured: `x_percent` is a
percentage and `0.079` put the click **0.3 pixels** from the corner while still
reporting a click (out-of-range values are now refused with the reason);
`click(button=)` for right/middle; a `scroll()` that glides visibly and then
wheels **without pressing the left button** first; and `find_window` surviving a
window that **renames itself** - a chat client titles its window after whatever
is open in it, so after one click the old title matched nothing and the driver
reported "no window appeared within 30s" for a window that was on screen. It now
matches on the workspace+app tail, and **re-queries by the app's class**, because
`xdotool search --name` takes the title as a regex and so filters the old title
away before any comparison can happen.

### Why Slack is hard to drive, researched rather than guessed (2026-10-11)

Every desktop-driving failure on this box has one root: **Chromium/Electron
publishes no accessibility tree unless it believes assistive technology is
present.** Measured on Slack: **2 named nodes** (`application`, `frame`) - the
tree exists but holds nothing clickable, which is why `element_box` finds nothing,
`screen_box` is the only way to locate a row, and every click is a guess about
coordinates.

The documented ways to change that, from Chromium's and Electron's own docs:

- **`--force-renderer-accessibility`** - *"Force accessibility to be enabled ...
  for the entire execution."* Confirmed as the blocker by VS Code's issue:
  Electron apps on Linux are *"not accessible under Orca ... this requires
  passing a --force-renderer-accessibility command line parameter"*. It is a
  launch flag, so it means restarting the app with different arguments.
- **`app.setAccessibilitySupportEnabled(true)`** - Electron's own API, but only
  callable from inside the app.
- **`org.a11y.atspi.Registry.RegisterEvent(event, properties, app_bus_name)`** -
  the registry's own docs: *"This method is to be called **by an AT** to indicate
  that it is interested in receiving a particular event from applications."*
  **Measured traps:** the registry is **not on the session bus** -
  `ServiceUnknown: The name org.a11y.atspi.Registry was not provided` - it lives
  on the **a11y bus**, whose address comes from `org.a11y.Bus.GetAddress`, and
  `gdbus` has **no `--address`** (it must be reached through
  `Gio.DBusConnection.new_for_address_sync`). `toolkit-accessibility` is `false`
  here.

**So the honest position: this has not been made to work.** The best remaining
route is `--force-renderer-accessibility`, which needs the app restarted with a
flag and is therefore a decision about somebody else's app rather than a change
to this driver. Until an Electron app publishes its tree, driving it is
necessarily **coordinate-based**, and coordinates must come from a **full-screen**
capture - see the entry above - with every click read back before it is trusted.

This is recorded as an open problem rather than a solved one. A driver that
"drives Electron apps" by pixel-guessing is a weaker claim than one that says it
cannot yet, and only the second is true today.

## Two cursors, and why they were two (2026-10-11)

Reported as *"why two cursors?"* The cue layer **draws** its own arrow and the
system pointer is the **real** input. The module's own docstring says the two
"read as one cursor", and that is true only while they are in the same place and
only while one of them is the glide. At rest with both drawn, a person watching
sees **two arrows**, which reports as two agents driving the mouse or as the
machine driving itself twice. Neither is true.

Two separate defects, both fixed and both measured:

**1. The real pointer did not have to land.** `_move_like_a_hand` stopped at the
first step it could not send — *"a step that cannot be sent stops the path rather
than jumping the rest of the way"* — which sounds careful and is right about the
**eased path**, and wrong about **arriving**. Measured before the fix: asked for
`(213,754)`, the pointer finished at `(202,810)` — **56px** away — and the click
reported success. It now ends with an exact `mousemove`, retried once, and
**reads the pointer back** (`getmouselocation`) and returns whether it landed.
The read-back is what makes the hand-over honest rather than assumed.

**2. The drawn cursor was never retired.** New `hide` command on the cue
protocol. Once the real pointer has landed, the drawn arrow has nothing left to
add, so `_hand_over_to_the_real_cursor()` retires it — **but only if it landed**.
A cursor that failed to arrive stays visible, because hiding the only cursor that
got there would leave a click looking like it happened somewhere it did not.

Verified on the live desktop through the driver's own `_glide_and_click`:

```
REAL pointer        : (187,806)  delta from request = (0,0)
cue layer says drawn: False        <- one cursor on screen
```

**A ping could not tell "drawn" from "retired", and that was the trap.** After a
hide the remembered position is exactly where the arrow was retired *at*, so
`{"ping": True}` reported `(187,806)` either way — and "the second cursor is
gone" would have been a claim no measurement supported. The reply now carries
**`drawn`** alongside `pointer`, and a test asserts the two answers differ.

**8 of 8 mutations confirmed to fail** (hide keeps drawing / ping says (0,0) /
protocol drops `hide` / second glide starts at the corner / driver never retires
/ a failed arrival is hidden anyway / arrival reported without a read-back / the
final exact move never sent). **65 tests pass.**

### `WINDOW=1024` — what it means, and why clicks "land and do nothing"

`getmouselocation`'s `WINDOW` is the check this driver has been using to decide
*"did the point actually land on the app?"*, and **`1024` means it did not**:
6291460 is Slack's client. Measured this session, **the same coordinate answered
`6291460` in one run and `1024` in the next**, with nothing in the code
different — so what is raised over that part of the screen is what decides it.

Window 1024 is **not** the cue layer: it is present with the layer down and up
alike. It is a permanent **1920x1080 window at (0,0) with no `WM_CLASS` and no
name** — Xwayland's own host window, which is what an X client sees when no
other X-mapped window is under the pointer. So the honest reading is the
negative one: at those moments **a native Wayland surface is covering the
point**, and no amount of coordinate correctness helps.

**This is the strongest candidate yet for the open "clicks land correctly and
nothing happens"**, which has survived every other explanation (click point,
re-focus, cue layer, frame/client arithmetic — each ruled out by measurement).
The pattern fits exactly: the click arrives, at the right pixel, at a moment
when the pixel belongs to something else. **Not yet proven** — it needs the
person to say whether their terminal is on top of Slack at the time, which is
one observation and settles it. Recorded as the lead, not as the answer.

## Why the clicks did nothing: XTEST never leaves the X server (2026-10-11)

The research answer to the longest open question in this file - *"clicks land on
the right coordinates and nothing happens"*. It was **never a coordinate
problem**. OpenJDK's own bug report on the subject (JDK-8280990) states it:

> *"the emulated input event reaches the X11 clients, but not the Wayland
> compositor which is the actual display server ... **these click events do not
> leave the XWayland server** and are not reported to the Wayland compositor."*

So `xdotool click` can dispatch, can exit 0, can move the X pointer - and the
application never sees it. **This desktop is the documented worst case for it.**
Measured: `/usr/bin/Xwayland :0 -rootless -noreset -accessx -core ... -enable-ei-portal`,
Xwayland 24.1.10. That flag is the **libei** bridge (GNOME 45 / Xwayland 23.2+),
so XTEST events are *additionally* forwarded to the compositor through the
**Remote Desktop portal** - which means they inherit the portal's consent gate.
The field report of that exact configuration (mutter 45.2, Fedora) is
`xdotool key` opening a **"Remote Display"** consent window instead of pressing
the key, and after allowing access once the popup stops while the keys still
never arrive.

**Fixed: `_pointer_routes()` sends every press through the portal or uinput,
and XTEST is not in the ladder on a Wayland session.** Same route order typing
already uses, which is why typing worked and clicking did not. `portal.py`'s
`RemoteInput` gained the half it never had - `NotifyPointerMotion` (`(oa{sv}dd)`,
two **doubles**; an `i` is an `InvalidArgs`) and `NotifyPointerScroll`
(`(oa{sv}idd)`), so `click()` is no longer a click wherever the pointer
happened to be.

**XTEST is not removed everywhere, and that distinction is the point.** It is
refused when `WAYLAND_DISPLAY` is set and kept when it is not - because
`container ... display=x11` hands the inner process `DISPLAY` and **no**
`WAYLAND_DISPLAY`, and there the X server genuinely is the display server, so
XTEST is the only thing that can deliver. Reading `XDG_SESSION_TYPE` instead
would refuse it there too: the over-fix that breaks the one case that works.

### What was actually on screen at the coordinates

The other half, found by OCR rather than reasoning. Full-screen OCR of the
"sidebar region" returned `deepu`, `Jisha`, `Nevin`, `Abdul`, `github` at
x~125 - and the surrounding text was **this agent's own terminal**:

```
Now the real sidebar found a test row,
/home/shrinivaskumbhar/Documents/shani/shani-chronoa
$ cd ... find usr -name __pycache__ ...
```

**The terminal was on top of Slack.** The clicks were landing on the terminal:
correct coordinates, wrong window. Every time a command runs, the terminal
raises itself - so *any* click attempted after running a command goes to the
terminal, which is exactly the pattern observed all session. `WINDOW=1024` in
`getmouselocation` was the same fact from the other side (1024 is a permanent
1920x1080 unnamed window at 0,0 - Xwayland's own host window, which is what an
X client sees when nothing else X-mapped is under the pointer).

**Not a code fix - an observation rule.** A driver that drives a desktop must
ask what is actually on top, not what should be. Capturing full-screen and
reading it is the only way; the window's own state is a claim about what *it*
thinks is above it.

### One capture answers many questions (20 seconds each, measured)

`screen_box` took a fresh `org.freedesktop.portal.Screenshot` **and** a fresh
full-screen `tesseract` run **per query** - ~20s each, so looking for four
sidebar rows cost 80s and four chances to catch the screen mid-repaint. That is
why the session kept appearing to do nothing.

`screen_words(region=None, attempts=2)` now takes **one** capture and returns
every word as `(x, y, w, h, text)`; `screen_box` is a lookup over them and takes
`words=` so several lookups share one capture.

Three defects found by hitting it, not by reading it:

- **Tesseract repeats its header row** (`left top width height` a second time),
  and `int("left")` raises a `ValueError` that is not an OCR failure and was not
  caught as one - so a perfectly good capture became a traceback that looked like
  the capture was at fault.
- **A failed OCR returned `None`**, which is the same answer as "that word is not
  on screen". A **0-byte `.tsv`** with the image still 1080 rows of real pixels
  was reported as four confident absences. It now retries and logs tesseract's
  own stderr, which `capture_output=True` had been swallowing.
- **`if not found: "the screen was not read at all"`** - found by *reading* the
  finished code, after several live runs. With a `region` supplied, an empty
  result is the ordinary correct answer (nothing in that rectangle), and the
  warning claimed something stronger than anything measured. `read` counts rows
  *before* the filter, which is what separates "OCR failed" from "nothing there".

**15 tests, 10 mutations, 8 caught on the first pass and the 2 survivors fixed**
rather than documented - one of them survived because the region test called
`screen_words` directly and never reached the mutated line inside `screen_box`,
which is a test that cannot fail wearing a passing one. **81 pass.**

## libei: the actual successor, and why it is not reachable on this box (2026-10-11)

The full research answer to *"how should a Wayland desktop be driven"*, measured
rather than quoted. **libei is the answer**, and almost everything for it is
already installed here - which makes the part that is missing worth stating
precisely.

**The sanctioned chain**, from libei's own README and Chromium's own tool:

- the compositor starts a **libeis** socket at `$XDG_RUNTIME_DIR/eis-0`;
- `xdg-desktop-portal` exposes **`org.freedesktop.portal.RemoteDesktop.ConnectToEIS`**,
  authenticates the client and hands over **a file descriptor**;
- the client speaks the binary `ei-packet` protocol over it and asks for devices
  with `pointer` / `keyboard` / `touch` capabilities.

**Chromium ships this exact tool** - `remoting/tools/wdotool/wdotool.py`, *"a
simple replacement for xdotool suitable for Wayland desktops ... uses Gnome
Remote Desktop and libEI APIs"* - and `cushycush/wdotool` is a maintained
xdotool-compatible CLI with `libei` / `wlroots` / `kde` / `gnome` / `uinput`
backends. A project's own input tool is the strongest available statement that
this is the sanctioned route.

### Measured on this machine

| thing | result |
|---|---|
| `ConnectToEIS` on the portal | **present** in `RemoteDesktop` (alongside `NotifyPointerMotion`, `NotifyPointerButton`, `NotifyPointerAxis`, ...) |
| `libei.so.1` (client) / `libeis.so.1` (server) | **both installed**, 1.5.0 |
| mutter | 50.1 - far past the GNOME 45 floor for the XTEST bridge |
| Xwayland | 24.1.10, and its own `--help` says `-enable-ei-portal  use the XDG portal for input emulation` |
| gnome-shell | **links libeis**; libmutter has **94** EI symbols |
| `$XDG_RUNTIME_DIR/eis-0` | **absent** |
| `ConnectToEIS` reply | **fd 0** - the method is advertised and returns no connection |
| `xdg-desktop-portal-gnome` | 50.0; knows the string `ConnectToEIS`, and links **0** libeis |

**So: everything is installed and nothing is connected.** The compositor half is
real (gnome-shell links libeis, mutter carries the symbols) and the **portal
half is the missing piece** - `xdg-desktop-portal-gnome` exports the method name
and does not link the library. That is the *"a name in an interface that answers
with nothing"* class this file records elsewhere, and it is why a lookup for
`eis-0` is **not** the test: the socket is absent *and* the fd route returns 0,
so the socket was never the door.

**Corrects an earlier entry in this file**, which recorded *"no `eis-0` socket
exists here yet, so the portal's fd handover is the route"* as though the socket
were the blocker. It was never the blocker, and the fd route is **also** closed.

**What this leaves.** Portal `NotifyPointerMotion` / `NotifyPointerButton` and
uinput are what actually work here, which is what `_pointer_routes` uses. Both
are **session-wide** - they deliver to whatever the compositor has focused - so
**the focus step is not optional on either**, and that is the same finding the
RemoteDesktop portal raised earlier, now confirmed from the other direction.

**On a machine with the full stack the ranking changes**, and it is worth
recording so this is not mistaken for a permanent verdict: libei > portal > uinput,
because libei goes through the compositor's own input path *and* can be given
`pointer absolute` devices, so it is the only route that both targets correctly
and needs no `input` group membership.

## Why the cue layer was never visible, and the 116-process leak (2026-10-11)

The person watching said *"the feedback cursor isn't visible"*, which this file
had flagged as unverified three times. It was not a subtle bug. **The cue layer
was never capable of being seen.**

**A plain `Gtk.Window` cannot draw above another application on Wayland.**
`main()` built one with `set_decorated(False)` and `set_default_size(...)` -
and **`set_default_size` sets a size, not a stacking level**. A Wayland
compositor puts ordinary windows at the **bottom** of the stack, so the layer
drew underneath the app it was supposed to annotate. Nothing in GTK4 or Xlib
can raise one; the only mechanism Wayland offers is a **layer-shell surface**,
and it is compositor-specific.

**Measured: `GtkLayerShell` is not installed here** (`Namespace GtkLayerShell
not available`, no `libgtk-layer-shell.so`), so there is no fallback that
works. `_make_it_a_layer()` now configures a real layer-shell surface when the
typelib is there and **returns `False` when it is not**, and `main()` says so
on stderr rather than pretending. `start_cue()` returns the same caveat, so a
caller that asks can tell its driving is real and its *feedback is invisible*.

**`present()` was the second half of it**, and it is worth separating from the
first because both are wrong in the same direction. `present()` maps **and
focuses**. Focusing a click-through layer steals the keyboard from the app being
driven - the one thing this module exists to avoid. Now `show()`, never
`present()`, and the rule is in `_show_unlayered()` where it can be tested.

### 116 processes, and why

`start_cue()` reused a layer from `_CUES`, **a per-Python-process dict** - so a
layer started by one driving process was invisible to the next, and every
process spawned its own, all racing to `bind()` the same path. **Only the first
can bind.** Measured after one session: **116 processes**, the socket owned by
the oldest of them, hours stale, the other 115 running forever having drawn
nothing.

Now `start_cue()` **asks the socket** (`_cue_answers()` pings it) rather than
consulting a dict - the check that matches what is actually true - and
`show_window()` no longer unpacks `start_cue`'s `(proc, why)` into a variable
called `socket` and sends the highlight to `""`.

**A `pgrep -f 'chronoa-cue-container'` killed my own shell**, because the
pattern matched its own command line - the workspace AGENTS.md trap, reached
again by a different route. `[c]hronoa-cue-container` fixes the self-match and
excluding `$$` is what makes it safe.

**10 tests, 7 mutations. Two survived the first pass and both were tests that
could not fail**: `main()` opens a GTK main loop, so no test in the file
reaches the `present()` decision - it is now `_show_unlayered()`, separately
callable. And the mutation detector itself was one: it counted `FAILED` lines,
so a mutation producing an `ERROR` read as "survived". It counts both now.

### The cue layer **does** draw - measured, and I nearly said it did not twice

Correcting two wrong claims from this same session, both of which I stated
before checking properly.

**Wrong #1: "the cue is still not drawing".** A before/after full-screen diff
found the change was a diffuse `(41,41,41) -> (34,34,34)` darkening over
`x 92..835, y 80..1049` - not a drawn border - so I concluded the layer was not
drawing what it was told, and I checked the two rows I had asked for and found 0
differing pixels of 1920. **That second check was the broken one**: it took "the
two most recent `Screenshot-*.png` by mtime", which were not the before/after
pair, so it compared two unrelated captures.

**What the whole-screen search actually shows**, searching every pixel rather
than a region I had guessed:

```
   76118 px   (34, 34, 34)   -> (44, 44, 44)
    5845 px   (10, 10, 10)   -> (53, 132, 228)     <- CUE_BLUE
```

`CUE_BLUE` is `0x35 / 0x84 / 0xE4` - **the cue's own colour out of this
module**. 5,845 pixels changed to exactly it when a highlight was sent. **The
cue layer draws.** A threshold like `> 200 changed pixels` reported it as
success; a region I picked myself reported it as failure. Neither measured the
thing.

**Wrong #2: "you will not see it".** An over-claim, and it is now corrected in
`_CUE_LAYER_CAVEAT`. The layer draws **and** `present()` raises it. What it
cannot do is **stay** on top: it is an ordinary `Gtk.Window`, and an ordinary
window is raised over by anything else that asks. Measured as the same probe
returning **0** cue pixels on a second run with nothing changed except that a
terminal - running the probe - had raised itself since. **The probe that proves
the cue is visible is the same probe that hides it**, which is the whole problem
in one sentence.

**So the honest state:** visible until something raises itself; and the thing
that reliably does is a terminal, including this agent's own. Only a
**layer-shell surface** cannot be covered, and `GtkLayerShell` is measured
absent. `_make_it_a_layer()` is the fix on any machine that has it.

### "chronoa name not in the blue box" - it was never drawn, not misplaced

Reported against the highlight's name tab. **It is not a positioning fault:
there was nothing in the box to be misplaced.** `draw_highlight` measured the
label, filled and outlined a box around that measurement, and never drew the
label.

**And it could not draw it.** `_pango_layout()` reached for
`Pango.FontMap.get_default()` - and **this PyGObject has no static `get()` or
`get_default()` at all** (measured: both `AttributeError`, and
`dir(Pango.FontMap)` confirms neither exists). So the function hit its own
`except Exception` and returned `None` **on every call**, and `layout_size()`
fell through to its character-count guess.

**The guess is why nobody noticed**: for this label it returns **47x13** against
a real **43x10**. Close enough that the box looked right in every screenshot,
and the tab had been an empty blue rectangle for as long as it existed.

A widget's own `create_pango_layout` is the route that works, and it is measured
(43x10). `layout_size` uses that now.

**Verified by rendering the real cue and counting pixels**, which is the only
way to see this - `blue pixels above the window's top edge: 1498`, `white
pixels above the window's top edge: 191`. Before the fix the second number was
**0**. The pointer's own tag was checked the same way: **83 px inside its box**,
with the **44 white px outside it correctly identified as the cursor arrow's own
white centre** - my first probe counted those and reported "text is OUTSIDE the
box" while the name was sitting in it. A third wrong verdict from the same
measurement, and the same lesson: **count the thing, in the place it should be.**

Two other bugs on the way, both of which had been hiding this one:

- `_CueArea.do_snapshot` built a **fresh layout every frame** and measured it
  immediately, and a layout that has never been through a render pass reports
  **`get_pixel_extents() == 1x1`**. It is built once now.
- `get_font_description()` returns **None** on a layout nobody has styled, so the
  guard that was there meant the 11px bold was **silently never applied**.

**29 tests in the overlay file, 5 mutations. Two survived and both were
mutations I had aimed at a file I had not changed** - the first pair's anchors
did not exist, which the runner reported as an assertion rather than silently.

### The cue layer is now on screen, and it was three separate faults

Measured on the live desktop at the end, through the driver's own
`show_window()` and a `glide`, then a full-screen capture:

```
cue-blue pixels on the live screen : 12632   (x 11..1906, y 13..1046)
white pixels in the name-tab strip :   450   <- the word "Chronoa"
VERDICT: THE NAME IS VISIBLE ON SCREEN
```

Three faults stood between that and an empty screen, and **each one hid the
next**, which is why each was found only after the previous was fixed:

1. **`Pango.FontMap.get_default()` does not exist in this PyGObject** (nor
   `get()`), so the highlight's tab drew a box from a character-count guess and
   **never the name**. See the entry above.
2. **`do_snapshot` built a fresh layout every frame** and measured it before
   its first render pass, which reports `get_pixel_extents() == 1x1`.
3. **The window got raised over** by anything else that asked - measured as the
   same probe returning 0 cue pixels on a second run with nothing changed except
   that a terminal had raised itself since. Now `apply()` sets a flag for any
   message that draws, and `_frame()` re-presents **at most once per tick** -
   coalesced because a 0.55s glide is ~33 frames, and on the main loop because
   `apply()` runs on the socket thread where touching a GTK window is unsafe.

**One mutation survived the first pass and it was the same failure as the
session before**: `main()` opens a `GLib.MainLoop`, so no test could reach the
tick, and removing `service_window()` from it left the file green. The tick is
now `_frame()`, separately callable, and asserts both that it presents and that
it returns `True` - **a falsy return silently stops the GLib timeout**, so the
layer would draw one frame and then go quiet for good, with nothing logged.

**Still the honest limit:** this is still an ordinary window. It is raised, and
it comes back when a cue asks - but a window raised *while nothing is being
drawn* covers it, and only a layer-shell surface cannot be covered.
`gir1.2-gtklayershell-0.1` and `libgtk-layer-shell0` (0.10.0-1) are available
in `resolute/universe` and `_make_it_a_layer()` already implements the whole
protocol; **installing them is a root action and is the person's call.**

### gtk-layer-shell: three faults, none of which was "it is not installed"

Installed on this box and measured. **It still did not work**, and the code was
wrong three times over rather than the machine:

1. **`require_version("GtkLayerShell", "1")` - the namespace version is
   `"0.1"`.** The typelib on disk is `GtkLayerShell-0.1.typelib`, so asking for
   `"1"` raises `Namespace GtkLayerShell not available for version 1`, which an
   `except Exception` swallowed and reported as *"gtk-layer-shell is not
   installed"* on a machine where it **was** installed.
2. **Ubuntu's `gir1.2-gtklayershell-0.1` is the GTK 3 build**, and it cannot
   load in a GTK 4 process at all:
   `ImportError: Requiring namespace 'Gtk' version '3.0', but '4.0' is already
   loaded`. The package for a GTK 4 app is **`gir1.2-gtk4layershell-0.1`** with
   **`libgtk4-layer-shell0`** (1.3.0-1) - a different pair entirely, and
   `libgtk-layer-shell.so.0` is measurably `ldd`'d against `libgtk-3.so.0`.
3. **Calling the C library with ctypes is not a workaround**, and the failure
   looks like success until you read the warnings. Loading
   `libgtk-layer-shell.so.0` pulls GTK 3's GObject type system into a GTK 4
   process: `cannot register existing type 'GtkWidget'`, `cannot add class
   private field to invalid type`, and the window never becomes a layer
   surface. **Two GObject type systems in one process is not a degraded mode.**

**Also measured, because it looked like the cause and was not:** layer-shell is
a **Wayland** protocol, so forcing `GDK_BACKEND=x11` in a probe makes
`init_for_window` a no-op and `get_layer` return `TOP` - the window looked
layer-shaped and was not.

`_make_it_a_layer()` now logs **the reason**, not just the fact, because "it is
not installed" and "it is the wrong major version" need opposite fixes. The
test asserts the message names the GTK 3/4 distinction, and the first version of
that test captured `records == []` - **`logging.info` is filtered at the root
logger (default level WARNING) before any handler sees it**, so attaching a
handler is not enough and the test passed for the wrong reason until it did not.

`sudo apt install gir1.2-gtk4layershell-0.1 libgtk4-layer-shell0` is the
remaining root action.

### The GTK4 typelib package is `gir1.2-gtk4layershell-1.0` - a different *namespace*

Measured on Ubuntu 26.04 (`resolute`) by reading both debs' file lists rather
than by trying names:

| typelib file | namespace | package | GTK |
|---|---|---|---|
| `GtkLayerShell-0.1.typelib` | `GtkLayerShell` **0.1** | `gir1.2-gtklayershell-0.1` | **3** (`Depends: gir1.2-gtk-3.0`) |
| `Gtk4LayerShell-1.0.typelib` | `Gtk4LayerShell` **1.0** | `gir1.2-gtk4layershell-1.0` | **4** |

**So it was never a version typo - it is a different namespace name.** The GTK 3
typelib cannot load in a GTK 4 process (`ImportError: Requiring namespace 'Gtk'
version '3.0', but '4.0' is already loaded`), so asking for `GtkLayerShell` in a
GTK 4 app is asking for the wrong library, not the wrong version of the right
one. Both spellings are tried, GTK 4 first, because some distributions ship a
GTK 4 build under the older name.

The runtime package is `libgtk4-layer-shell0` (1.3.0-1, `Depends: libgtk-4-1`).
Both are in `resolute/universe`:

    sudo apt install gir1.2-gtk4layershell-1.0 libgtk4-layer-shell0

**Neither name is guessable.** The four candidates I checked
(`gir1.2-gtk4layershell-0.1`, `-0.4`, `gir1.2-gtk4-layer-shell-0.1`,
`gtk4-layer-shell-gir`) all returned **no candidate at all** - only
`apt-cache dumpavail` surfaced the real one, because it is named from the
namespace rather than the library. The typelib inside is 5,140 bytes and
installs exactly one file.

### The answer, at last: Mutter does not implement layer-shell, and never has

With `gir1.2-gtk4layershell-1.0` and `libgtk4-layer-shell0` installed, the GTK 4
namespace loads cleanly and `_make_it_a_layer()` runs - and then the library
says:

```
Failed to initialize layer surface, it appears your Wayland compositor doesn't
support Layer Shell
GtkWindow is not a layer surface. Make sure you called gtk_layer_init_for_window()
```

**`zwlr_layer_shell_v1` is a wlroots/KDE protocol. GNOME's Mutter has never
implemented it.** No package installs that, and none ever will - which makes the
whole layer-shell path a dead end on this desktop and not a matter of versions.

**Two real bugs fell out of the attempt, both worth keeping:**

- **`gtk4-layer-shell` must be linked *before* `libwayland-client`**, or the
  layer surface silently fails to initialise. Its own warning says so, and it is
  not advisory - the protocol needs a `wl_registry` that is still open when the
  library initialises. This **cannot be fixed from inside a running process**
  (the loader decides before `main()`), so `usr/bin/shani-chronoa-overlay`
  **re-execs with `LD_PRELOAD`**, guarded on a marker so it cannot loop, and
  skipped entirely when the library is absent. Measured: the warning goes from
  present to **0 occurrences**.
- **`set_keyboard_interactivity()` does not exist in 1.x** - the GTK 3 way of
  saying "this layer takes no keys" became a *mode*. Calling it was an
  `AttributeError` that took the whole layer down: **`exit=1`, socket never
  created**, so the driver got no cue layer rather than a degraded one.

**`get_zwlr_layer_surface_v1()` is now the success test**, because
`init_for_window()` returning is not - it returns whether the *call* worked, not
whether the *compositor* agreed. Returning True over an unlayered window is the
one lie this function must never tell, and it is exactly what it used to do.

**So the answer on this desktop is the fallback**: an ordinary window, raised
again on every cue. Verified live - 12,632 cue pixels and 450 of them the word
"Chronoa" - and the re-raise means it survives the terminal that would otherwise
cover it. On a wlroots or KDE compositor the same code takes the layer path with
no changes at all.

### The 30-second pair WAS the flake's neighbour, and both are fixed (2026-10-11)

Following the unexplained correlation rather than shrugging at it.

`type_text()` called **`find_window()` before it consulted the communication
gate**, so with no window present it sat in `_WINDOW_TIMEOUT` - **30 seconds,
measured** - and only then refused. Two tests spent exactly 30 s each on it.

**A consent gate that takes 30 seconds to say no is a gate that looks like a
hang**, and that gate exists precisely to stop a keystroke leaving the machine.
`_comms_app()` reads the app's frame title from the accessibility tree, so it can
answer "is this a chat surface?" **without a window** - and whether that answer
changes while a window search runs is not a question worth 30 seconds.

The gate is now settled first. `tests/test_ui_driver.py`: **62.1 s -> 0.7 s**,
and the whole three-file sweep **65 s -> 4.6 s**.

**That is very likely the flake too, and it is not proven.** The two recorded
failures were both in *fast* (~4.5 s) runs, which is the mode where those tests
refused without hitting the 30 s timeout. The mode no longer exists - every run
is now the fast one - and **8 of 8 full sweeps pass**. The condition that
correlated with the failure is gone, which is evidence rather than proof, so it
is recorded that way.

Mutation-checked: moving `find_window()` back in front of the gate fails
`test_the_consent_gate_never_waits_for_a_window`, which asserts the search is
never made at all.


### Ubuntu's dock and top bar are in the geometry, and not in anything else

Asked directly - *"are you taking into account Ubuntu's sidebar and top bar?"* -
after hours of clicking that did nothing. **They were accounted for
geometrically and nowhere else.** Measured off a real capture, not a screenshot
of the developer's memory:

| | extent | colour at that spot | in the window box? |
|---|---|---|---|
| Ubuntu Dock (`ubuntu-dock@ubuntu.com`) | `x 0..~64` | `(94,39,80)` | yes - Slack's client starts at x=**67** |
| Top bar | `y 0..~30` | `(19,19,19)` | yes - Slack's client starts at y=**106** |

`window_box()` returns the **client**, so both offsets are already in it, and a
click at x=214 is 150px clear of the dock. **Two things that is not enough for:**

- **`dash-to-dock intellihide = true`, `autohide = true`, `dock-fixed = true`.**
  Conflicting settings where the fixed dock wins, but `intellihide` means the
  dock **slides out over content**. A glide that passes within `x < 64` can pop
  it out over Slack's left edge - which is where the sidebar is. Nothing in the
  driver accounts for that today.
- **Both are drawn by GNOME Shell**, so the cue layer's ordinary window draws
  **underneath** them: anything the layer paints in `x < 64` or `y < 30` is
  invisible. That is a third concrete reason the layer wants to be a real
  layer-shell surface, and Mutter cannot provide one.

**And the thing they do _not_ explain**, recorded so it is not conflated with
them: the click blocker is compositor focus. Measured at the same moment:

    getmouselocation at (214,874) : WINDOW=1024    (not Slack's client)
    X active window               : 2097155        (not Slack's 12582916)

**Neither the dock nor the top bar is at fault for a click at (214,874) whose
pointer is provably on the target.** The dock can cover the left edge; the top
bar can cover the top; **focus is what decides where a portal or uinput click
actually goes**, because both are session-wide.

## Four defects the full-suite sweep found, all of them real (2026-10-11)

A whole-suite run after the `gsettings` fix: **7843 passed, 6 failed**. Two were
the environment, four were not, and all four are the same shape this file keeps
recording — a test that was **describing a machine that does not exist**.

### 1. The Wayland clipboard argv was X11's, so the whole skill was broken there

`clipboard.py` built `[tool, "-selection", "clipboard", "-o"]` — xclip's form —
for whichever tool `_detect_backend()` returned, at **all three** call sites
(`get_clipboard`, `set_clipboard`, and the post-condition that verifies the
write). On Wayland that runs `wl-paste -selection clipboard -o`, and
`wl-paste --help` has neither flag: it takes `--no-newline` and `--type`.
Measured on a real Wayland session:

    /usr/bin/wl-paste: invalid option -- 'o'

So `get_clipboard` answered *"The clipboard is empty."* for a clipboard that had
just been written — an rc-non-zero is read as "empty", which is the
absence-shaped wrong answer — and the **post-condition then verified the read it
had just failed**, so a successful write was reported as a failed round trip.
Two defects in one argv: the flags are wrong, and the failure is misread as
data. **Wayland is ShaniOS's own session type**, so this was the shipped path,
not an edge case; it was invisible because the unit tests stub the backend and
the two live round-trip tests only run where the tools are installed.

`_read_argv` / `_write_argv` now hold each session's flags in one place, and
`TestTheClipboardArgvIsPerBackend` (6 tests) pins them — including that the two
backends never share an argv, which is the property that was violated. Mutation
confirmed: reverting the read argv to xclip's form fails 2 of the live tests.
Verified end to end on the real tools: write → read back → `verified`.

### 2. A gate label that drifted by one word

`capabilities.GATE_NAMES["comms-typing-enabled"]` said *"Typing into chat and
mail windows"*; the Settings row says *"Let Chronoa **type** into chat and mail
windows"*. `test_help_gate_labels_match_settings` builds the real window and
checks the Help label appears among its rows, so it caught it. The rule is
written above the table — *"named here with the label that row actually carries,
not a second wording"* — and one entry drifted. The Help window was therefore
sending someone looking for a switch whose title they would not recognise.

### 3. A `which()` fixture that could not see its own machine

`test_input_control_ydotool.py`'s `fake_bin` **prepended** its directory to
`PATH`, so `_only(bindir, "ydotool")` deleted the *fake* wtype while
`shutil.which("wtype")` still resolved `/usr/bin/wtype` — which is really
installed here. The assertion "only ydotool on PATH → ydotool chosen" was a
statement about the host's packages. `PATH` is the bindir and nothing else now.
Same class as the `GSETTINGS_BACKEND` note: **a probe that does not control its
environment is describing the machine it ran on.**

### 4. A refusal that named the wrong absence

`test_ocr_sense` set `PATH=/nonexistent` and expected *"tesseract is not
installed"*. `resolve_tesseract()` also falls back to a fixed
`FALLBACK_TESSERACT`, and `/usr/bin/tesseract` really exists here, while
`/usr/share/tessdata` does not — so the sense answered *"tesseract has no
language data"*, which is **true**. The fixture was describing a machine that
does not exist; it now neutralises the fallback path too.

### And one that is genuinely environmental, closed honestly

`test_routines_live` failed because the loaded `Qwen3-1.7B-Q4_K_M` calls only
`get_datetime` for a two-part request and then *narrates* "Let me check the
weather in Pune" with no tool call behind it — the assistant's loop correctly
ends a turn whose reply carries none, so the user is left holding a promise.
That is a property of the live model, and AGENTS.md already recorded it as
measured both ways. It is now a **SKIP that names the resolved file**, because a
live-model test is the one kind whose result changes with what happens to be
loaded.

Three ways of identifying the loaded model were tried and **two are wrong**,
which is worth recording before someone re-derives it: `local_llm.active()`
reads the symlink through `model_dir()` → `XDG_DATA_HOME`, which this file's
`gsettings_env` fixture redirects, so it answers `""` under test; `/v1/models`
reports the generic `current.gguf` for every model on the machine, so it
identifies nothing; and `/props`' `model_path` is the **symlink**, so it must be
`realpath`d before it names the file behind it — the first version of the check
matched nothing and stayed a red test. Control run both ways: a tag that
matches nothing makes the test **run** and fail on the 1.7B, so the skip is
conditional on the model rather than permanent.

### Closed the same way a round later: the last one was a staging gap (2026-10-11)

`test_every_launcher_in_usr_bin_is_executable` failed with
*"shani-chronoa-overlay is not tracked in git at all"*. Not a defect in the
launcher: it asserts against **git's recorded mode**, deliberately, because a
working-tree `chmod` papers over a 644 file that a fresh checkout still produces
— and this class killed `shani-chronoa-lab-network` once already, on the only
enforcement point for a security claim. The manifest half was already done
(`shani-pkgbuilds/shani-chronoa/PKGBUILD` line 240 installs it), so it was the
mode half only: staged. All eight launchers are `100755` and the suite is
**28 passed**.

**And the whole suite, after everything above: 7850 passed, 3 failed.** The three
are all in `tests/test_sidebar_toggle.py`, and each one is green on its own —
the file is 8/8 alone and **959 passed, 0 failed** in its own 45-file chunk. It
is recorded at 3931 as flaky with no compositor (4 of 6 runs green with
`window.py` unmodified from HEAD, which is how you show the residue is nobody's
bug). Re-run failures serially and in their own chunk before believing them.

**One thing that went wrong here, recorded because it cost real work.** The
record above was written, then lost: the working tree rolled back 40 lines
(section intact in a `/tmp` backup, absent from both the tree and HEAD). Nothing
in git was lost, but a file another session is also editing can be reverted
underneath you — so a record that matters is worth a copy outside the tree
before it is handed on, and worth checking for after any `git checkout` in a
shared working tree.

### THE ANSWER: `NotifyPointerMotion` is the RELATIVE method (2026-10-11)

**This is why the clicks did nothing.** Not the coordinate space, not the frame
origin, not the window, not the app. The method was wrong.

`portal.py`'s `move()` called `org.freedesktop.portal.RemoteDesktop`'s
**`NotifyPointerMotion`** with an absolute `(x, y)`. That method is **relative**:
it moves the pointer *by* that much. The portal also exports
**`NotifyPointerMotionAbsolute`**, and the two are different methods with
different signatures - the absolute one takes a `u stream` first.

Measured, by asking the pointer where it ended up after each move:

| pointer was at | asked to move to | pointer ended at | |
|---|---|---|---|
| `(688, 1079)` | `(214, 874)` | **`(902, 1079)`** | 688 + **214** |
| `(902, 1079)` | `(600, 400)` | **`(1502, 1079)`** | 902 + **600** |
| `(214, 874)` | `(214, 874)` | `(214, 874)` | XTEST, exact |

`y` never moved in either case because it was already clamped at the bottom edge
of a 1080-tall screen. So **every "click on a Slack sidebar row" was a click at
the bottom of the screen**, delivered, and reported as delivered - which is
exactly the failure shape this file keeps recording, reached through a method
call that is perfectly well formed.

**`NotifyPointerMotionAbsolute` is real but cannot be used to drive.** It needs a
`stream`, and the portal answers `Failed: Invalid position (0)` for a literal 0.
It is for a client that has opened an InputCapture session, not for one that
wants a click.

**So `move()` sends the difference from where the pointer already is**, which is
the whole of what the relative method is. Read with a new
`portal.pointer_position()`; the portal has no "where is the pointer" method, and
reading through X is not a shortcut around the compositor - every motion the
portal made showed up there to the pixel. Measured after, four targets, **every
one exact**: `(214,874) (600,400) (1200,300) (214,874)`.

When the position cannot be read, `move()` **raises rather than nudging** - the
original bug was sending coordinates as though they were a delta.

**A click now lands.** Through the driver's own `click()`, on this desktop's own
Slack: `JoeL (DM)` -> `deepu (DM)` -> `vinod sivashankaran (DM)`, the last one
confirmed by reading the window title *and* by looking at a full-screen capture
of the conversation it opened. The pointer was verified to be **at** `(252,165)`
when the press was sent; it reads as its old position afterwards because closing
a RemoteDesktop session restores it, which is correct behaviour and not a
failure.

**And `NotifyPointerScroll` is not a method the portal has at all.** Introspecting
`RemoteDesktop` lists `NotifyPointerAxis` and `NotifyPointerAxisDiscrete`; grepping
the interface for the name the code was calling returns **0**. `scroll()` now
uses `NotifyPointerAxis`, which takes the same `(oa{sv}dd)` and whose `dx`/`dy`
are the relative amounts a wheel notch is.

**The general guard, because two names in one file were wrong the same way.**
`tests/test_portal_input.py` carries the real interface - recorded from this
machine's introspection - and asserts that **every method `portal.py` calls on
`RemoteDesktop` exists in it**, with a control that the scan is not silently
finding nothing. A well-formed call to a method the portal does not have cannot
be caught by reading, by a fake portal on a private bus, or by pyflakes.

### Three smaller faults behind it, all found the same way

1. **`raise_app` guessed the app id and all three guesses were wrong.** It tried
   `slack`, `com.slack` and `slack.desktop` on a machine where the app is
   **`com.slack.Slack`**. It also returned `True` on the first `Popen` that did
   not raise, and `Popen` raises only when the *binary* is missing - so every
   "raise" launched a program that did not exist and reported success. It now
   resolves the id from `flatpak list --app --columns=application,name` and
   returns **the id it started**, or `None`.
2. **A raise holds focus for about three seconds, and `click()` spent twenty
   before pressing.** The "what is under this point" read is a full-screen
   capture plus OCR; measured, the raise's focus window is ~3s. Raising at the
   start of a call and reading afterwards therefore **loses the window before the
   press**, which no amount of coordinate accuracy could fix. `ensure_focused()`
   now runs *after* the read and immediately before the press, and polls every
   0.15s rather than 0.4s.
3. **`frame_state() is None` is not "not focused".** That is a container's X11
   app, or any app publishing no accessible frame; treating it as unfocused would
   refuse every legitimate container click - the same over-fix as removing XTEST
   from a Wayland session's ladder.

**A claim in the code was wrong and is corrected.** `click()`'s comment said the
frame's AT-SPI `ACTIVE` state "disagrees with X focus", which is why neither
candidate guard shipped. Measured while fixing this, **the two agree on every
sample** - both named Slack's client for the ~3s a raise held it, and both named
the same 1x1 override-redirect window at `(-100,-100)` when it did not. So
`ACTIVE` *can* distinguish the cases, and `ensure_focused()` does guard on it.
The other candidate is still rejected on the reason given at the time: a DM is
called `deepu`, not `slack`.

**Still open, and not to be read as a verdict.** Scrolling delivered **0 changed
pixels** on every route tried - the portal (both axis methods) and uinput - but
**no target was used that certainly scrolls**, so the check could not have failed
and the result means nothing either way. Two probes over the Slack sidebar and a
message pane, then over a 3,000-line document in `gnome-text-editor`; the last
was disturbed by the person using the desktop while it ran. Settling it needs a
pane known to overflow. Likewise, clicks on Slack **channel** rows report the
right coordinates and the right text under the point but have not been seen to
navigate; DM rows have, three times.

**11 new tests, 11 mutations run, 11 caught** - including two that first
**survived** and were tests that could not fail: the ordering test now records
the sequence `read, raise, press` rather than asserting each happened, and the
poll-interval test captures the sleep delays instead of stubbing `sleep` to a
no-op, which is what made the interval invisible. **117 pass** across the three
driver suites.

## open-intelligent-ui: a plan a person can correct, and the model being told (2026-10-11)

Studied `harness-study/open-intelligent-ui` (ChatGPT's *Intelligent UI*
reproduced with OpenUI — the model streams a component DSL and the app renders a
map, photos and an editable itinerary). Full write-up:
**`../harness-study/HARVEST-OIU.md`**. Two things transferred, one did not.

**Not taken: generative UI.** A model-emitted widget DSL is a second rendering
path beside 21 individually-tested typed panels, and it widens what a model can
*cause* past the fixed whitelist — the boundary AGENTS.md's own Boundaries
section rules out. Recorded as a design decision, not a gap.

**Taken: the edit round-trip.** Its route panel lets a person remove stops and
add suggestions, that state is saved with the message, and the next turn rewrites
it as a sentence the model reads:

```ts
return content.slice(0, at) + (edits.length
    ? `\n\n(User's edits to this route: ${edits.join("; ")}.)` : "");
```

> Route edits: an earlier answer may end with "(User's edits to this route: …)".
> Treat that edited route as the current one in follow-ups.

Without that, a plan a person corrected is indistinguishable from one the model
wrote whole, and a model asked to continue re-adds what was taken out.

**What Chronoa already had, and this file had wrong.** `goals.py`,
`skills/manage_goals.py` (producer), `gui/surfaces/goals.py` (consumer) and
`tests/test_goals_wired.py` were all wired — while three places in this file
said `goals.py` was "unwired on purpose (no producer yet)". **All three are
corrected above.** The stale label is the "a stale 'dead' label is worse than a
missing 'live' one" failure in a new place, and it was found by reading the
module's own docstring, not by running it.

**What was actually missing:** the Goals panel's only destructive control was
"Remove this goal", so one wrong step meant discarding a whole plan and asking
again. Built now, all in `goals.py` plus the panel:

- `remove_step(run, variable_name, now)` and
  `move_step(run, variable_name, to_index, now)` — both return `(run, why-not)`.
- `downstream_of` — the **transitive** closure of what stops being runnable.
- `GoalRun.edits` — what a person changed by hand, persisted, newest last.
- `describe_edits(run)` — the sentence, appended by `manage_goals`' `list`,
  which is the only place a model ever reads a saved plan.

**The constraint open-intelligent-ui never faces:** a `PlannedStep` names a
`variable_name` other steps depend on, so removing one can orphan a later one.
The refusal names what is in the way and says "Nothing was removed", and it
covers the whole downstream set rather than the direct dependents — **which is
what my own tests caught**: the first version used direct dependents, and
`test_the_refusal_names_every_dependent` failed on `$c`, because removing `$a`
from `a → b → c → d` parks all three and naming only `$b` sends the person back
for a second refusal and a third.

Verified end to end through the real dispatch (a sandboxed child reading the
store), save three steps, remove the last by hand, then:

```
g1  [running]  plan a trip  (0/2 steps)

(A person edited this plan by hand: removed $c. Treat the edited plan as the one
they want, and do not put those steps back unless they ask.)
```

`tests/test_goal_edits.py` (26), **12 of 12 mutations confirmed to fail**. Two
of those first survived and both are the recorded traps in new clothes:

- Deleting `line += goals.describe_edits(run)` from `manage_goals.describe` left
  the suite **green**, because the tests asserted `goals.describe_edits`
  directly — the "patch the name where it is read, not where it is re-exported"
  rule. `TestTheSkillReportsTheEdits` now drives the skill's own `describe`.
- Walking the built panel found no controls, because the test saved the run
  *after* the refresh and the panel renders on refresh, not on save.

**Not taken: the prompt generated from one spec.** `generateSystemPrompt({library:
spec})` keeps the model's menu and the prose rules from disagreeing, but Chronoa
already has the working half — `_GROUPS` labels, a generated `CAPABILITIES.md`,
and `test_capabilities_doc.py` — and the failure mode this session actually hit
was "a skill landed and four hand-kept lists are not updated yet", which the
tests correctly report. Generating more prose would not have caught it.

## Model tiers: what a machine can run is bytes-per-token, not RAM or a GPU (2026-10-11)

Three designs for "which model does this machine get", and the first two were
wrong in opposite directions. **Both are now pinned by
`tests/test_model_requirements.py`.**

1. **RAM only** — `recommended()` returned `qwen3-14b` on a CPU-only box with
   30 GB of RAM, where it decodes at ~2 tok/s. *Fitting* and *being usable* are
   different questions.
2. **`gpu_devices()`** — correct for a laptop, wrong for a workstation: a
   Threadripper 7980X's 8 channels of DDR5 are on the order of **86–129 GiB/s**
   of effective bandwidth where this i7-1165G7 measures **16.5**, so a 14B runs
   at 1.8 tok/s here and 10–15 there. A GPU gate would tell that machine to run
   a 1.7B.
3. **bytes-per-token against measured bandwidth** — what it is now.

`local_llm.effective_bandwidth()` measures it from a live decode rather than a
datasheet: `predicted_per_second × model_path size`. Two traps it had to learn:

- **`n_predict: 1` returns `predicted_per_second: 1000000.0`** — llama.cpp
  dividing a single millisecond token. The probe generates 16 tokens and the
  reader refuses any rate above 1000 tok/s outright, so an artefact cannot become
  a machine's measured bandwidth.
- **`os` was patched globally** by the first test fixture, changing `cpu_count`
  for everything else in the process. A shim now.

**`REQUIRES` carries the table the panel needs** — `cpu`, `ram`, `vram` and
`min_tps` per tier, so a refusal can say *"16 GB of RAM (this machine has 6.0)"*
instead of "not recommended". And one field the whole thing turns on:

**`active_bytes`** — what one generated token actually reads. A dense model reads
its whole file. **A mixture-of-experts reads a fraction of it**, and that is
edge0's central accounting: peak memory tracks the parameter count, decode cost
tracks the *active* set. `Qwen3-30B-A3B-Q4_K_M` stores 17.3 GiB and activates
~3.3B of 30.5B parameters, so it reads ~2 GiB per token — which is why a 30B can
decode at 8 tok/s on this laptop while a 14B dense manages 2.

**An estimate is never a default.** `active_bytes` for the 30B comes from the
architecture, not a decode on this machine, so `REQUIRES[...]["measured"]` is
`False` and `requirements_met()` refuses it — with the reason named. The tier is
offered and its speed is shown as an estimate; flipping the flag after measuring
makes it the default, and a mutation that re-adds the GPU gate fails three of
these tests.

MMD
echo "recorded"; echo; echo "=== final state ==="; PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_model_requirements.py -q --tb=no 2>&1 | tail -1; git status --short | head -8

## Model tiers: what a machine can run is bytes-per-token, not RAM or a GPU (2026-10-11)

Three designs for "which model does this machine get", and the first two were
wrong in opposite directions. **Both are now pinned by
`tests/test_model_requirements.py`.**

1. **RAM only** — `recommended()` returned `qwen3-14b` on a CPU-only box with
   30 GB of RAM, where it decodes at ~2 tok/s. *Fitting* and *being usable* are
   different questions.
2. **`gpu_devices()`** — correct for a laptop, wrong for a workstation: a
   Threadripper 7980X's 8 channels of DDR5 are on the order of **86-129 GiB/s**
   of effective bandwidth where this i7-1165G7 measures **16.5**, so a 14B runs
   at 1.8 tok/s here and 10-15 there. A GPU gate would tell that machine to run
   a 1.7B.
3. **bytes-per-token against measured bandwidth** — what it is now.

`local_llm.effective_bandwidth()` measures it from a live decode rather than a
datasheet: `predicted_per_second x model_path size`. Two traps it had to learn:

- **`n_predict: 1` returns `predicted_per_second: 1000000.0`** — llama.cpp
  dividing a single millisecond token. The probe generates 16 tokens and the
  reader refuses any rate above 1000 tok/s outright, so an artefact cannot become
  a machine's measured bandwidth.
- **`os` was patched globally** by the first test fixture, changing `cpu_count`
  for everything else in the process. A shim now.

**`REQUIRES` carries the table the panel needs** — `cpu`, `ram`, `vram` and
`min_tps` per tier, so a refusal can say *"16 GB of RAM (this machine has 6.0)"*
instead of "not recommended". And one field the whole thing turns on:

**`active_bytes`** — what one generated token actually reads. A dense model reads
its whole file. **A mixture-of-experts reads a fraction of it**, and that is
edge0's central accounting: peak memory tracks the parameter count, decode cost
tracks the *active* set. `Qwen3-30B-A3B-Q4_K_M` stores 17.3 GiB and activates
~3.3B of 30.5B parameters, so it reads ~2 GiB per token — which is why a 30B can
decode at 8 tok/s on this laptop while a 14B dense manages 2.

**An estimate is never a default.** `active_bytes` for the 30B comes from the
architecture, not a decode on this machine, so `REQUIRES[...]["measured"]` is
`False` and `requirements_met()` refuses it — with the reason named. The tier is
offered and its speed is shown as an estimate; flipping the flag after measuring
makes it the default, and a mutation that re-adds the GPU gate fails three of
these tests.

### …and `safeUrl`: a reply's links are clickable, and they open in Chronoa's own browser

The second transfer, and the one that closed a gap the first pass did not see.
OIU's `safeUrl` renders `https:` and `http:` and returns undefined for
everything else, so a URL a model emits is either a link a person can follow or
text. Chronoa's `markdown_lite.to_pango` supported bold, italic, code, fences
and tables and **no links at all** — `[example](https://example.org)` rendered
as those literal characters and a bare URL was dead text. Safe by omission, and
every answer lost its URLs.

`markdown_lite.safe_url` is the same allowlist in Python, and `to_pango` now
renders `[label](url)` and bare addresses as `<a>` elements. A destination
outside the allowlist keeps its words and loses its link.

**The click goes to Chronoa's own browser** (`_ensure_browser_window` +
`load_url`), not the system's: sending a reply's links elsewhere would move the
person out of the conversation they are reading, and out of that window's egress
checks, activity strip and stop button. The system handler is the fallback only,
for a machine with no WebKitGTK.

**Three defects were found, and all three were in the spoken path** — which is
the one that matters most, because Chronoa speaks every reply:

| defect | measured |
|---|---|
| `_SPOKEN_LINK` stopped at the first `)` | `[Foo](…/wiki/Foo_(bar))` spoken as `Foo)`; `[x](javascript:alert(1))` as `x) link.` |
| trailing punctuation was swallowed by `\S+` | `see (https://x.org/a) ok` spoken as `see (link ok` |
| a URL inside a code span ate the closing backtick | ``run `curl https://x.org/a` please`` spoken as ``run `curl link please`` |

Wikipedia addresses carry a real parenthesis, so the character cannot be banned
from a URL: both regexes now allow one level of nesting, and the trim decides
what the *address* is while the punctuation stays in the sentence. A URL inside
code is reduced to "link" as well, per this file's own measurement of 205,654
bytes of audio for one address against 67,080 for the word.

**Two of my mistakes here, both the shape that recurs:**

- **I opened the person's browser while verifying this.** The first version put
  the allowlist check and `Gio.AppInfo.launch_default_for_uri` in one function,
  so exercising it meant emitting `activate-link` on a real label — which ran
  the real launcher and opened browsers on the desktop. The decision
  (`blocks.link_target`) is separate from the launch
  (`blocks.show_in_own_browser`) now, and **a handler that cannot be tested
  without causing the thing it does is a handler nobody should be testing by
  hand.**
- `launch_default_for_uri` is **synchronous and blocks**; the handler runs on
  the GTK main loop, so it uses `launch_default_for_uri_async`.

`tests/test_reply_links.py` (22) plus `tests/test_markdown_lite.py` (now 138
with the new classes). The in-app path is asserted with a stub application whose
`_ensure_browser_window` returns a recorder, and every "emit" test patches the
launcher so **nothing is ever opened** — the in-app test raises if the system
handler is reached.

### The Web Speech API: measured absent from the engine that runs our pages

Asked where `SpeechRecognition` / `speechSynthesis` could be useful here. **They
are not usable, for two independent reasons, and the first is a measurement.**

**WebKitGTK implements none of it.** Measured on this box - WebKitGTK 6.0
(WebKit 2.52.6), the engine behind `gui/browser.py` - by evaluating JavaScript
in a real `WebKit.WebView` over **`http://127.0.0.1`**, a *secure context*:

| in the same page | |
|---|---|
| `navigator.mediaDevices` | **object**  <- the control |
| `navigator.mediaDevices.getUserMedia` | **true** |
| `SpeechRecognition` | **undefined** |
| `webkitSpeechRecognition` | **undefined** |
| `speechSynthesis` | **undefined** |
| `SpeechSynthesisUtterance` | **undefined** |
| `speechSynthesis.getVoices()` | 0 |

**The control is what makes that table worth reading.** The first run reported
`mediaDevices` undefined as well - on a `data:` URL, because `isSecureContext`
was false - which is indistinguishable from a probe that cannot see anything.
Re-running over localhost made `mediaDevices` appear while every speech entry
stayed undefined. WebKit is Safari's engine; Safari never shipped
`SpeechRecognition`, and WebKitGTK has implemented neither half.

**And even where it does exist it is the wrong shape for this project.**
Chromium's `SpeechRecognition` streams microphone audio to a cloud service.
Chronoa is local-first with 53 consent-gated senses, and `cloud_voice.CloudSTT`
sits behind **both** `cloud-stt-enabled` and privacy mode. A page's own Web
Speech would move a microphone stream off the machine with no consent key at all
- precisely what `egress` exists to record. The one idea on that page worth
keeping is MDN's **on-device** recognition (`SpeechRecognition.available()` /
`.install()`, behind the `on-device-speech-recognition` Permissions-Policy),
which *would* fit; it is still absent here, so it is a "if the engine ever gets
it", not a plan. `SpeechRecognitionPhrase`'s contextual biasing is also the idea
whisper.cpp's initial prompt already is.

**What does follow is a limitation, not a feature.** `browse` clicks controls on
real pages, and every page feature built on this API is **inert in the in-app
browser**: Voice Search's mic button, Wikipedia's "Listen to article", web
transcription sites, accessibility read-aloud widgets. `browse` would click the
mic, the page would do nothing, and the driver would report success - the same
"a click that lands on something inert reports success" failure recorded several
times above. **The remedy is not the Web Speech API but the browser knowing the
page lacks it**: there is already a JS seam (`browser.py:705`,
`evaluate_javascript` -> `VISIBLE_TEXT_JS`), and a `page_capabilities()` probe
beside it would let `browse` say *"this page's voice search cannot work in this
browser"* instead of clicking a dead button. Not built - it is small, and it
wants doing on a machine where such a page can be shown.

### …and the third: the OpenStreetMap cache, and "a missing result" is data

`maps.py` hits Nominatim, FOSSGIS's OSRM and Overpass. All three ask for the
same thing in their usage policies - a valid User-Agent, one request per second,
**no heavy use** - and `maps.py`'s own docstring already spelled out the first
two (`netjson`'s named UA, `_polite`'s cross-process flock). The third was
missing: every identical "where is Shaniwar Wada" was a fresh request, held a
second by the lock, and the answer was the same answer.

`maps._cached_get` now sits in front of the single `_get` seam - a `sha256` of
the URL plus **sorted** params, a day TTL, 500 entries with the oldest evicted
by mtime, all under `0700` in the user's own state dir. **The line worth
copying is one of open-intelligent-ui's comments:**

> A missing article is a real answer (cached); any other failure is retried next
> time.

`netjson.get_json` already raises on a transport failure and returns a body
otherwise, so the distinction was there - the cache only had to respect it.
Nominatim's "does not know this place" is `[]`, a body like any other, so asking
about a typo twice costs one request; a 500 from OSRM writes nothing, so the next
call retries. **A cache is not a memoised outage.**

Details taken for the same reason: **mtime** for eviction (each skill call is its
own sandboxed child, so an in-memory order would be wrong every time), a
**version field** so a bad format is invalidated by a constant rather than by
deleting a directory a running process may be reading, and a **hash** for the
key so a filename never carries somebody's search words. A cache hit produces
**no egress record**, because nothing left the machine.

**What it exposed in this repo's own tests:** `test_maps_skill.py`'s `osm`
fixture did not isolate `XDG_STATE_HOME`, so the cache wrote real entries into
the developer's `~/.local/state` during the suite - and
`test_a_network_failure_names_the_service_and_the_error` then failed, because a
cached answer from an earlier test was served during a simulated outage. That is
`triggers_dir()`/`rules_file()`'s reason in a new place. The fixture isolates now.

`tests/test_maps_cache.py` — 21 tests, **12 of 12 mutations confirmed to fail**.

**Not taken:** a cache for `browse`/`webtext` (the person asked to see *that*
page now, which is a different question), and `Cache-Control` headers (Chronoa is
the client, not the origin). Chronoa's User-Agent story was already better than
this repo's.

### …and the fourth: `markdown_lite.clip`, because a slice draws a box

open-intelligent-ui's `pinEmoji` takes **one grapheme or the default** - `Intl.Segmenter`,
then Extended_Pictographic - "so a word or letter from the model can't overflow the
pin". Chronoa's version of the problem was a hard slice: `summary[:57] + "..."` in
`blocks.py`'s tool card and `body[:80]` in `phone.py`'s message preview.

Python 3 strings are code points, so a surrogate pair is never split - but
"👨‍👩‍👧‍👦" is **seven** code points held together by ZWJ, and `('e' + U+0301)[:1]`
is `'e'` with the accent gone. Measured: `'Family photo 👨‍👩‍👧‍👦'[:i+2]` ends in
U+200D. Each case draws the "missing glyph" box in text a model wrote.

`markdown_lite.graphemes` and `markdown_lite.clip(width)` now cut on a cluster
boundary and count **columns**, ellipsis included. The stdlib has no grapheme
segmenter, so `_joins` is built from the characters that cannot *start* a cluster -
the half that matters for a truncation - and the approximation is stated in the
docstring rather than implied. Both call sites use `clip`, and `phone.py`'s preview
became a named `_preview()` because the only way to test the inline expression was
to build the whole panel.

**Five things the tests got wrong first, each found by running:**

- a cluster's width is **one measurement, not a sum** - `_display_width` sums a
  code point at a time, so the family emoji measured 11 columns (four emoji at 2,
  three ZWJ at 1) instead of 2, and a string that fitted was clipped anyway;
- `_stranded` flagged **correct** output - it treated any trailing combining mark
  as stranded, and `'café...'` ends in one legitimately. The failure is a *leading*
  mark whose base was cut. A control that fails on correct output is the same
  defect as one that cannot fail;
- a grep test **matched its own comment** - `assert "[:57]" not in blocks.py`
  matched the docstring explaining why the slice was removed, the trap this file
  has already recorded once;
- the mutation survived **twice for padding** - `[:57]` only strands a joiner when
  the cluster straddles the cut, and padding with prose left the emoji whole. The
  offset is asserted now;
- **counting ZWJs passes the wrong case** - `count("\\u200d") % 3 == 0` passes when
  a slice keeps all three joiners and drops only the last emoji, which is exactly
  what `[:57]` does.

`tests/test_clip.py` - 33 tests, **11 of 11 mutations confirmed to fail**. Four
joining rules were untested at first and three of them were removed without a
single test failing; `TestGraphemes` asserts each directly.

### Application streams: the half that was missing (2026-10-11)

**"Listen to this meeting and transcribe it" needed one thing nobody had: a list of
the audio an *application* is carrying.** The graph could show it, and the driver
could not point at it.

`pipewire._devices()` matches `media.class` **exactly** to `Audio/Source` and
`Audio/Sink`, so an app's `Stream/Output/Audio` node was never in any list -
`list_inputs()` returned microphones, `list_outputs()` speakers, and a running
application's audio was neither. Measured, `pw-dump` through the module's own
`_graph_nodes()` showed `media.class='Stream/Output/Audio' node.name='pw-play'
media.name='/tmp/tone.wav'` while a tone played.

**New `pipewire.list_streams()`**, and it measures rather than asserts:

| what | measured |
|---|---|
| `pw-record --target=<stream node name>` | **-27.4 dBFS** of real audio, 6.02 s, from a name `list_streams()` reported |
| `pw-record --target=<the sink node>` | 11.9 s captured, **all zeros** |

So the route is **per-application stream**, not "record the system output" - the
obvious recipe produces silence. The `AudioStream` type carries `node.name` (what
`--target` takes), `direction`, `media.name`, and a `recordable` flag.

**Only `Stream/Output` is recordable.** `Stream/Input` is the app *consuming*
audio - its own microphone - and is already what `list_inputs()` is for. Offering
it as a capture target would be a dead control, so `recordable` is False and
`recording action=streams` says "this app's own microphone" instead of handing
over a name that cannot work.

**`media.name` is the label, not `node.name`.** The latter is the binary - a
Chromium stream's is "chromium", which tells a person nothing about the call -
and `media.name` is what said `/tmp/tone.wav`. `node.name` is the fallback,
because `pw-play` had no `nick` and no `description` either.

**Wired as `recording action=streams`, reachable and advertised** through the real
dispatch path, and **behind `heard-sound`**. A stream list is a list of what is
playing: `media.name` measured a filename here and would read a meeting title on
a real desktop. Reading a node name is not listening, but the names are about the
user's world in exactly the way the microphone is, so the same switch covers it.
Verified in all three states by execution: consent off refuses by name, a quiet
machine reports itself quiet ("a quiet machine and not a failure"), a playing
application is listed as "1 of 1 can be captured ... (capture as: pw-play)".

**7 tests, 7 mutations, 6 caught** and one *deliberate* survivor planted as a
no-op control, to prove the runner can tell a real break from nothing.

### From the CLI matrices: three candidates, and two were already answered

Asked to add skills from `chronoa-matrix.json`. **`open_surfaces` measures calls,
not answers** - this file has warned about that before and the warning held here.

| command | verdict |
|---|---|
| `quota` / `repquota` | **Genuine gap** ("df says there's room but I can't write"), installed on both images via `shani-storage`. **Missing on this dev box** - a slot run, not a parser written from memory. |
| `lstopo` / `hwloc-info` | **Already answered**: `system_info._cpus()` shells `lscpu` and falls back to `/proc/cpuinfo` with an honest `topology is UNKNOWN`. |
| `mokutil` | **Corrects this file.** It records `mokutil` as "server profile only, so no desktop image ships a tool". The matrix measures `pulled_in_by=['shani-core']` on the **GNOME** image, and here `mokutil --sb-state` answered **`SecureBoot disabled`, rc 0, unprivileged** - while `driver_dev._secure_boot_state` reads `/sys/firmware/efi/efivars` (root-only 0400) and reports UNKNOWN. Enriching `driver_status` to ask `mokutil` first is the change this measurement points at; not made yet, because the efivarfs reading is the one this file already measured on a real boot and the two need agreeing. |

`pkcheck` was tried and its only flags are `--enable-internal-agent`,
`--list-temp`, `--revoke-temp` - I guessed `--list-actions` and it was **refused
with `Unexpected argument`**, which is the "a contract you cannot measure is a
contract you do not have" trap again. Left alone rather than guessed at twice.

## llama.cpp was given the wrong thread count, in six places, and it cost a third of the speed (2026-10-11)

`server_args()` passed `os.cpu_count() - 1` as llama.cpp's `-t`. On this
i7-1165G7 (4 cores, 8 threads) that is **7**. Measured with `llama-bench`, quiet
box, `Qwen3-8B-Q4_K_M`, pure decode, `-r 2`:

| `-t` | 3 | **4** | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|
| decode tok/s | 3.23 | **3.73** | 3.70 | 3.62 | 2.50 | 2.32 |
| prompt tok/s | 16.2 | 19.7 | 18.8 | 19.9 | 19.0 |

**`-t 8` is 1.61x slower than `-t 4`,** and the value the code chose sat in the
worst part of the curve - about a third of the available speed, thrown away on
every server start, for the life of the product. Confirmed end to end: the
server answers 3.58 tok/s at `-t 4`.

Not a private discovery. ggerganov in ggml-org/llama.cpp#34: *"speed with
command line config -t 4. If use -t 8, half speed"*. The upstream
performance-tuning page: incorrect thread settings are *"the #1 cause of slow
inference"*. Discussion #394: keep threads at or below the **actual** cores.

**Six call sites had the line**, which is why this is a property and not a patch:

| module | binary | how it hid |
|---|---|---|
| `local_llm` | llama-server | `-t cpu_count()-1` = 7 |
| `local_vision` | llama-server | same |
| `imagegen` | stable-diffusion.cpp | same |
| `local_embed` | llama-server | `min(4, ...)` |
| `recordings` | whisper.cpp | `min(4, ...)` |
| `wakeword` | whisper.cpp | `min(4, ...)` |
| `tts` | piper | `min(4, ...)` |

**The `min(4, ...)` four looked correct on this box.** `min(4, 7)` is 4, which
happens to be the physical core count here - so a value right by accident on the
machine you tested on is the one that is wrong everywhere else, and on a
16-thread machine it is half the cores. Measured: restoring
`min(4, cpu_count()-1)` in `local_embed` leaves the argv test **green**, because
the module still returns `-t 4` here. Only a source-level guard catches it, so
`tests/test_llama_threads_are_physical_cores.py` has one.

`hardware_profile.physical_cores()` counts unique
`thread_siblings_list` groups from sysfs rather than shelling to `lscpu` (a
binary a minimal image may lack, and the reason `skills/system_info.py` reads
the kernel). Fallback is `os.cpu_count()`, never zero: `0` becomes `-t 0`,
which llama-server rejects, so an unreadable `/sys` would leave the machine with
**no server at all** rather than a slow one.

**Two levers that did NOT work, so nobody re-derives them:** `GGML_CPU_REPACK=1`
is 3.52 against a 3.61 baseline (no gain - the workload is memory-bound, not
GEMM-bound); and this Debian build has **no `-tb`/`--threads-batch`** flag
(newer upstream only), so batch threads are not reachable here. OpenMP and BLAS
are not linked, so the 2-3x OpenBLAS figure in most write-ups never applied
here in the first place.

**The bandwidth model had a 2.5x error, and the first 8B measurement was wrong
twice.** Measured minutes apart on the same box: 1.7B 6.81 tok/s x 1.03 GiB =
**7.03 GiB/s**, 8B 3.69 x 4.68 = **17.29 GiB/s**. A small model is not
bandwidth-*saturated* - it is bound by per-token compute - so it moves less
memory per second than the hardware can, and caching whichever measurement
arrived first locked in a figure 2.5x too low for every prediction about a
larger model. `effective_bandwidth()` now keeps the **largest** figure seen, and
re-probes when the loaded model changes (keyed on the realpath, because
`/props` reports the **symlink**). Validated rather than argued: predicted
**3.5 tok/s** for the 8B, measured **3.69**.

The first 8B reading was **0.20 tok/s**, and it was wrong: two llama-servers
(7 threads each on 8 cores) with 732 MiB free. Pausing one with SIGSTOP was not
enough either - this is a shared desktop. "Measured on a real machine" means
measured with nothing else competing, and a bandwidth figure taken under
contention is a number about the contention.

**What actually buys a bigger model is fewer bytes per token**, which is why
`active_bytes` is the load-bearing column: `Qwen3-30B-A3B` reads ~2.2 GB/token
against the 8B's 4.7 GB, so it decodes *faster* than a model a third its size.
21 + 9 + 5 tests; 12 of 13 thread mutations caught, the survivor documented in
the file.

## open-intelligent-ui, fifth pass: the three-state pattern, and a regression I shipped (2026-10-11)

Studied the rest of `harness-study/open-intelligent-ui` (map.tsx, root.tsx,
`useStreetRoute`, `useDialog`, `response-theme`, the two proxy routes). Write-up
in **`../harness-study/HARVEST-OIU.md`**, sections 1-4 already recorded; this is
the fifth.

### Already here, verified rather than assumed

Three of the remaining ideas were checked and **found already implemented** — each
recorded so nobody re-derives them:

- **`userMoved.current`** ("stop auto-behaviour once the person has taken over"):
  `widgets.py`'s `_note_position` + `_scroll_to_end` do this, and better — the
  position is recorded *before* the content changes (measured: taken after the
  fact, a first reply that made the transcript 3000px tall arrived with the view
  at the top), there is a `STICKY_PX` tolerance and a `_follow_generation`, and
  following resumes when the person scrolls back to the bottom by hand.
- **`reducedMotion()`**: `gui/style.py` carries `.reduce-motion` rules for every
  animation the sheet declares.
- **`safeUrl`'s "one grapheme or the default"**: shipped last pass as
  `markdown_lite.clip`.

### Taken: the three-state pattern — and it exposed a shipped regression

`useStreetRoute` returns **`undefined` while loading, `null` when the request
failed, and points otherwise**, so a caller cannot confuse them. Chronoa's goal
run loop had one state and a comment claiming otherwise:

```python
except Exception:  # noqa: BLE001 - shown by the refresh, not raised
    logger.warning(...)
```

**That comment is true of a step failing** — `goals.advance` already converts a
step exception into `phase=FAILED`, and the panel renders `_PHASE_WORD[FAILED]`
as "failed - n of m steps". Measured: the first version of the tests asserted the
opposite, ran, and got `WARNING goals.py ... failed at step a` plus an empty
`_run_error` — the failure had been handled one layer down all along.

What *was* silent is a **store** failure: `load`/`save` raising leaves the run
exactly as it was, so the refresh shows it unchanged and the person presses a
button that does nothing. `run()` now records why it stopped, separately from the
phase, and `_card` renders it on the run's own card.

### The regression, and the reason no test caught it

While building `drop_step` (last pass) I **replaced the body of `remove` rather
than adding beside it**, so "Remove this goal" was left calling
`self.remove(run.id)` with no `remove` method. A concurrent session committed it.
Measured on the built surface: `hasattr(surface, 'remove')` -> **False**,
`surface.remove('r1')` -> **AttributeError**.

**No test failed, because no test clicked it.** The tests drove `drop_step`
directly — this repo's own "a test that calls the function is not a test that the
skill is reachable" rule, missed by one entry point out of two.
`TestTheGoalLevelRemoveStillWorks` closes it, and the mutation that re-deletes
the method is confirmed to fail it.

Two mistakes of mine in the same pass, both worth the recording:

- a `python3 str.replace` patch dropped the closing `"""` of `run()`'s docstring,
  producing `SyntaxError: unterminated string literal` — the documented trap, and
  it took `ast.parse` plus a cumulative `"""` count to find. `py_compile` on the
  edited file catches it in one command; the script had run `pyflakes`, which
  does not.
- the test file was reverted underneath me by the concurrent session's commit,
  and my next edits were a patchwork against a file that no longer held what I
  thought it held. Reset to HEAD and rebuilt rather than continuing to edit over it.

`tests/test_goal_edits.py` - 35 tests, **13 of 13 mutations confirmed to fail**.

## VoxCPM: the 2B engine is not a tier, and the text normaliser it implied is (2026-10-11)

`harness-study/voxcpm` is **VoxCPM2**, a tokenizer-free TTS: 2B parameters on a
MiniCPM-4 backbone, 30 languages, 48 kHz, voice cloning and "voice design"
(a new voice from a text description). Apache-2.0, 35.4k stars, upstream is
`OpenBMB/VoxCPM` — there are at least seven forks on GitHub and that is the
original.

### Not taken: the engine, and the reason is a measurement

VoxCPM needs **CUDA >= 12, PyTorch >= 2.5, ~2B parameters of weights**, and it
is a diffusion autoregressive model on the GPU. Chronoa's speech path is a
**four-engine CPU cascade** (Kokoro opt-in, Piper, RHVoice, espeak-ng) whose
whole design is that it degrades one step at a time and works on every image —
`AGENTS.md` records Kokoro being *removed* as the default because 6.39 s of wall
clock for 5.225 s of audio (RTF 1.22) on an i7-1165G7 put ~6 s of silence in
front of every answer. VoxCPM is two orders of magnitude past that on a machine
with no CUDA at all, and adding a fifth tier behind a GPU requirement would
break the property the cascade exists to hold. **A model is not a tier.**

### Taken: the normaliser, which was a measured gap

`src/voxcpm/utils/text_normalize.py` exists because every phoneme front-end
carries no vocabulary of units. Chronoa had **no text normalisation at all**
before this — raw model output went to whichever engine won. Measured with
`espeak-ng -X` on a sentence a language model actually writes:

    It cost $5.00 at 42km/h, i.e. 3 items.
    -> Unpronouncable? 'km'
    -> It k'0st d'0l3 f'aIv pOInt z'i@roU z'i@roU at f'o@ti t'u: k,eI'Em sl'aS 'eItS

"**It cost dollar five point zero zero at forty two K E M slash H**" — and
espeak itself reports `km` as unpronounceable and spells it. Piper and Kokoro are
no better; they are phoneme front-ends too.

`tts_text.py` now fixes the cases that are *provably* wrong, and only those. It is
deliberately **not** VoxCPM's implementation, which needs `inflect` and `wetext`
and is aimed at Chinese text normalisation — this is the English subset against
the standard library, because a hard dependency for a voice line is not a trade
this app makes (`sox` is an optdepend for the same reason).

What it does: units (`42km/h`, `8GB`, `100ms`), currency (`$5.00`, `$0.99`,
`$4.50`), abbreviations, and initialisms (`API` read as "ay-pee" becomes "A P I").
What it deliberately does **not** do: spell out numbers in general. `3.14`,
`2026`, `1st`, `53,312` were all measured reading correctly through espeak-ng
*before* the module was written, and a normaliser that touches more than it fixes
is one nobody can review. `_LEFT_ALONE` records the words for exactly this
reason.

Verified end to end the same way the defect was: `espeak-ng -X` on the input and
the output. `8 gigabytes` where it said "eight G B"; `A P I` where it said
"ay-pee"; `that is` where the sentence had been abandoned at the unpronounceable
`km`.

### Three bugs in my own first version, each found by running it

- **`\bkm` cannot match `42km`.** `2` and `k` are both word characters, so there
  is no boundary between them — the anchor has to be `(?<![A-Za-z])`. This is the
  case that matters most, since `42km/h` is the sentence that started the module,
  and the first version passed on `5 km` while failing on `5km`.
- **`5km` became `5kilometres`.** Replacing just the abbreviation runs two words
  together, so the number is captured and re-emitted with the space the author did
  not type.
- **The change report printed a function repr.** `str(repl)` on a callable gives
  `<function _mk_money_repl.<locals>.repl at 0x...>`, a log line that cannot be
  checked against the sentence that was spoken. Each callable is now matched
  against the pattern that produced it so the report quotes the words the listener
  hears.

### The seam, and why it is not in an engine

Normalisation runs in `synthesize()` beside `apply_timbre`, because both are the
steps that are not an engine. Putting it in `_synthesize` would put it four times
in one file, once per engine, which is how a normaliser comes to disagree with
itself. A normaliser that raises **never costs a reply** — the call site catches
it and speaks the raw text, because an engine that says "forty two K E M slash H"
is better than no engine at all. That guarantee is tested by driving the real
method with the normaliser forced to raise, and the companion test asserts the
rewritten text *does* reach the engine, so neither passes against a call site
that ignores the normaliser entirely.

## VoxCPM, third pass: the repo is exhausted, and the gate it implied found two rotted citations (2026-10-11)

Second and third passes over `harness-study/voxcpm`. The engine stays rejected
for the measured reason in the section above. Reading what is left settles it:
`utils/` holds **only** `text_normalize.py` (already taken), `timestamps/` is a
179-line adapter over the optional `stable-whisper`, `conf/` is fine-tuning
hyperparameters, and `audio_vae_v2.py` / `scalar_quantization_layer.py` /
`modules/` are pure `torch.nn`. **There is no third transfer, and inventing one
to justify another pass is the confident-wrong-answer shape this file records.**

What did come out of it was a different finding, and it is in the same class as
the `_speak` note: a citation that no longer resolves.

**`distill.py`'s `Router` promised something it could not be checked against.**
> "Nothing here can authorise a call. It ranks names that were already on offer;
> the whitelist that permits them is `tools._HANDLERS`, and it is checked there
> and nowhere else."

**`_HANDLERS` does not exist in this repository** — measured, not read: the only
occurrence anywhere is that sentence. The whitelist is `tools._HANDLER_FNS`
(**255 entries**, counted) and the check is `_dispatch_inner`'s
`_HANDLER_FNS.get(name)` → `"Unknown tool"`. So the *promise* held; the
*citation* was unfollowable, and a promise nobody can check is a promise nobody
can rely on. It now names both real symbols, and
`test_the_router_docstring_whitelist_promise_is_true_at_runtime` **runs** the
claim instead of asserting it (an unlisted name is refused: `ran=False`,
`UNVERIFIED`).

**`audio.py`'s `play_file` cited `app._speak` — a method with no caller at all.**
Verified over the whole package: **no `._speak` attribute access and no
`getattr(x, "_speak")` anywhere.** `speech.SpeechQueue` (a synth thread and a
playback thread) replaced it; the conclusion the paragraph draws — two replies
can overlap, so `stop()` needs the newest process — is still true, and now says
what actually runs.

### The gate, and the four bugs that shaped it

`tests/test_stale_cross_references.py` asserts no comment or docstring cites a
symbol that does not exist. Writing it was four bugs in a row, each of which is
the same lesson this file already keeps stating:

- **The first version could not have caught the bug it was written for.** `app`
  is a **package** (`app/`) with no `app.py`, so the resolver skipped `app._speak`
  and reported "1 violation" for a tree with two. A check that silently skips
  the case it exists for is worse than no check.
- **The second reported 68 violations on a tree with none.** `_defined_names`
  used `ast.walk`, which descends into function bodies, so a **local variable**
  named `config` in `senses/ocr.py` made `config` look like a name that file
  defined; the class-member branch then fired, found no class `config`, and
  flagged a correct citation. **That single bug was all 68.**
- **Subpackages were invisible.** `sing._verify_sung` is `skills/sing.py`, and a
  package's `__init__.py` is filed under `__init__`, not under the package name
  — so `skills._USER_SKILLS_DIR` and `triggers._ensure_state_dir` both
  "failed". Both are now indexed the way Python imports them.
- **One control failed to bite, and it was the important one.** Citing a module
  that was *deleted* — the commonest rot — was skipped, because "no module
  matches" was treated as undecidable. A **lowercase-initial** part is written
  the way a module is imported, so a missing one is a deletion; an
  **uppercase-initial** part is a class, which may live anywhere.

Undecidable cases are skipped, not failed, and the two that needed a real
decision are a **documented two-entry allowlist**: `a11y_client` (prose names
"shani-testbed", and `../shani-testbed/lib/a11y_client.py` exists) and
`downloads_watchdog` (prose says "ported from browser-use"). Removing either
entry makes the gate fire again, so the allowlist is not a blank cheque.

**The gate caught this paragraph's own author.** The first `distill.py` fix
explained the rotted citation by quoting it in backticks — making the fix its
own first violation, which is correct behaviour. The name is now written without
backticks, and the docstring says why.

**The stale tree is currently unimportable**, which is the other session's
in-flight work and recorded rather than touched: `learning.py:1566` runs two
statements together on one line, so `tools.py` → `outcome_model` → `learning.py`
raises `SyntaxError` and **every** import of the package fails. Verified against
a repaired copy outside the tree, never by editing their file.

## Screen share: the sixth dead module, and it needed a worker, not a button (2026-10-11)

Second pass of the same audit: **15 of 186 modules are referenced by no GUI, no
skill and no sense.** `phone_screen` needs an Android app that does not exist
here, `bluez_battery` is plumbing that feeds bluez so GNOME and Plasma draw it.
One is neither: `skills/screen_to_phone.py`, driving a module
(`screen_stream.py`) that was **fully built and reached by nothing** - the same
"who calls this" scan that has found five dead modules here. `gui/surfaces/screen_share.py`
is the page it could be used from.

**It goes through `tools.execute_tool`, not a copy** - the gate
(`screen-share-enabled`), the sandbox and the audit origin are the chat path's.
**The probe takes up to 90 seconds** (the launcher's `--check` imports GStreamer
and reads the kernel's address table), so every action runs on a worker and
reports on an idle, and the answer is shown **in full**: a truncated label would
remove exactly the part naming the missing package.

Three mutations, all failing the file: bypassing the gate to call the skill
directly, dropping the origin so an unattended action reads as a typed one, and
swallowing a refusal.

## The Routines surface, and the row that escaped nothing in either field (2026-10-11)

Asked for the GUI for needed things, so the audit ran before the build: **all 57
gated consent keys have a label** (clean), and **42 of 186 modules are referenced
by no GUI at all**. Most of those are legitimately backend - engines and helpers.
One is not: `routines`, which is Siri Shortcuts and Google Assistant routines as a
saved *request*, reachable **only by a phrase you had to already remember**.

`skills/routines.py` saved one and `routines.expand` ran it, and nothing let a
person list them, see their trigger phrases, or see what each one asks for.
`gui/surfaces/routines.py` is that page.

**Run goes through the window's own `submit_text()`**, the same path `--ask=` and
the desktop entry use, because a routine is a saved *request* the model plans -
not a saved tool list. A panel that ran it another way would be a second
implementation of "turn a request into tools", and it would run the request
*outside* the consent gates.

### `common.row` escaped nothing, in either field, and said it escaped one

Its docstring: *"a model-supplied title goes through `set_markup` after escaping."*
Measured - **it escaped nothing**:

    set_title("weather & news")   -> Gtk-WARNING: Entity did not end with a
                                     semicolon; escape ampersand as &amp;
    set_subtitle("a & b")          -> the same parse error

Both the title *and* the subtitle are Pango markup. An ampersand is an ordinary
character in a name somebody typed, and **three surfaces had each hand-rolled
`GLib.markup_escape_text`** before calling the helper that claimed to do it - one
contract, three workarounds, every new caller left to find out. Both fields are
escaped in `common.row` now and the three workarounds are removed, because
escaping in both places double-escapes and a rules file's `&` renders as `&amp;`.

**Two controls of mine could not fail before this was measurable**, both of which
passed while the escaping was removed:

- comparing `get_label()` - it returns the source string whether or not the text
  was parsed, so an escaped row and an unescaped one compare equal;
- a Python `logging` handler - **GLib does not log through Python.**

The measurement that works redirects **fd 2** to a file and looks for Pango's own
refusal.

### Two bugs found by building the page, not by reading it

- `refresh()` appended a **new add-form on every call**, so a page left open
  stacked one per refresh. The whole body is rebuilt now, the shape `goals.py`
  uses - which also avoids `common.clear()` on an `Adw.PreferencesGroup`, which
  logs *"tried to remove non-child"*.
- The icon test caught a duplicate: `view-list-ordered-symbolic` was Goals'. It is
  `appointment-soon-symbolic`, checked against the installed Adwaita theme rather
  than guessed.

### And the destructive trap, hit once

I ran `git checkout -- senses.py` to undo a control mutation. It is a path
operation in a shared tree, it silently reverted my own edit to that file, and the
rule against it exists for exactly that reason. Re-applied by hand, nothing else
touched, recorded here so the cost is visible.

### Not my failures, proven rather than assumed

12 GUI suites: 3 failures and 95 errors, all the concurrent session's in-flight
`background.py` (it calls a missing `inventory`), `my_files` and `meetings`. The
proof is that **`goals` - a long-standing surface - errors identically to
`routines`**, because the module-scoped fixture builds every surface and raises on
`background` before reaching any of them.

## Meeting notes have never once worked, and this is how it stayed hidden (2026-10-11)

Asked whether the 1000-char hearing cap applies to meeting transcription. It does
not - `MAX_TRANSCRIPT_CHARS` is read at **exactly one site**,
`senses/hearing.py:355`, and only ever in `_transcript_percept`. The meeting path
has its own limits, all different: `NOTES_MAX_CHARS = 12000`,
`MAX_MEETING_SECONDS = 7200`, `recordings.MAX_SECONDS = 1800`. A meeting
transcript is **sectioned** at 12000 characters and every section is summarised,
so the whole meeting is used - measured on a 48,889-character transcript: **5
sections, all summarised**.

Checking that by running it found something much bigger than the question.

### `meeting.notes()` raised on every call, and nothing said so

    TypeError: LocalLLM.__new__() got an unexpected keyword argument 'config'

Two defects on consecutive lines, and the second is why it survived:

    engine = local_llm.LocalLLM          # __new__ takes (model_key,) alone
    engine(config=config, transport=transport).chat(...)   -> TypeError
    except TypeError:
        engine(config=config).chat(...)                    -> TypeError, uncaught

The `except TypeError` handler was **the same wrong call without `transport`**,
so it re-raised the identical error outside any handler - which is why no message
ever said "no local model is configured". `.chat()` exists only on `OllamaLLM`;
`chat_message()` is the interface every backend here has.

**`distill.py`'s `_ask_chat_message` documents this exact bug in full** - a first
version called `.chat()`, llama.cpp and the cloud chain raised, and a machine
with a configured provider reported "no language model is configured" while the
key sat right there. This is the **second instance of it** in the tree.

### The reason the suite stayed green, which is the part worth keeping

The test's fake engine was shaped like the **call**, not like the backend:

    class _Engine:
        def __init__(self, config=None, transport=None): pass
        def chat(self, messages, stream=False, **kw): ...

It encoded the bug and agreed with it for the whole of its life. Now
`__init__(model_key="")` plus an async `chat_message()`, and three guards pin the
contract: notes actually come back, the constructor is not given `config` or
`transport` (checked against `LocalLLM`'s **real** signature, not a fake's), and
`.chat()` raises if it is ever used.

**The transferable rule: a fake shaped like the call rather than like the thing
being called tests the bug and passes.** Both defects were found only by driving
the real `notes()` with a backend copied from `OpenAICompatibleLLM`'s actual
signature - `def chat_message(self, messages, tools=None, stream=False)` is
`async`, and `LocalLLM.__new__(cls, model_key="")` is the constructor the two
other call sites (`app/brain.py`, `distill.py`) already use.

The coroutine is driven with `asyncio.run` in a helper that **refuses loudly if a
loop is already running** - a coroutine created and never awaited is the failure
this file already records for `app.py`'s `async def` handlers, where the whole
chat pipeline quietly became dead code.

`config` and `transport` are still accepted by `notes()` because three callers
pass them, but no `def chat(` in this package takes either, so they are no longer
forwarded to anything. Recorded rather than silently dropped.

One pre-existing failure left alone and verified by restoring the file to HEAD:
`test_a_silent_room_is_reported_as_clean` expects `wav="/tmp/meeting.wav"` and
gets `None`, a `capture()` recorder-stub matter unrelated to notes.

## Voicebox: the recogniser loops, and the guard could not see the form it emits (2026-10-11)

`harness-study/voicebox` is `jamiepine/voicebox` — a local-first voice studio
(MIT, 56k stars), Tauri + FastAPI, 7 TTS engines, Whisper for STT. Cloned
shallow at `8af7efe`. Read for what a project built around the same problem
already solved.

**Already here, and in two places better than theirs — checked, not assumed.**

- *Sentence splitting.* `utils/chunked_tts.py` has a 30-entry abbreviation set
  and splits at sentence → clause → whitespace → hard cut. `speech.py`'s
  `SentenceBuffer` has the same set **plus** three things it does not:
  `MIN_SENTENCE_CHARS` ("1." or "Hi." alone sounds clipped), a code fence spoken
  whole, and single-letter initials treated as abbreviations via `len(w) == 1`.
  Its `MAX_SENTENCE_CHARS` is their `max_chunk_chars`.
- *Underscores in tool names.* 232 of 256 tool names contain one, and a person
  says "set volume". Suspecting a gap, `_words()` turned out to already do
  `.replace("_", " ")` before tokenising — so the whole 232 are handled.

**Taken: `collapse_repetitive_artifacts`.** Whisper's characteristic failure is
not a different wrong sentence, it is the right one repeated, and
`clean_transcription`'s hallucination filter matched only the unrepeated form:

    'thanks for watching'                  -> dropped   (what the regex is for)
    ' '.join(['thanks for watching'] * 6)  -> 119 chars forwarded to the model

**119 characters of a phrase the code already knew how to refuse**, arriving at
the model as one emphatic person — and the model then answers it. Voicebox
collapses loops in two passes for the same reason and notes the failure mode
their own approach has: smaller refine models truncate real output "to make
room" for the loop, bigger ones echo it verbatim, so they strip deterministically
first. The collapse now runs **before** the hallucination filter, which is what
lets a repeated "thanks for watching" be dropped outright.

**Two bugs in my first version, both of which produced a plausible collapse:**

- **A multi-word phrase defeated both passes.** "thanks for watching" repeated
  has no two identical adjacent tokens, and its character period is the phrase
  *plus its separator* — **twenty**, not nineteen — so a sixfold repeat is 119
  characters and holds only five whole units. It works in tokens now, on the
  smallest period that tiles the run, which removes the separator from the
  question entirely.
- **Stopping at the threshold left most of the run.** Eight repeats of a
  four-character CJK unit collapsed to **three** copies, because the scan broke
  once `min_run` was met and then kept the tail. Measured, not reasoned: that
  is what the first version printed.

The threshold is 6, and `test_rhetorical_repetition_survives` guards it —
**"no, no, no, no, no" is somebody emphatic**, and a collapse that eats real
speech is a worse defect than the loop it was written for. The load-bearing
assertion is the mixed case: *"what time is it" + a sixfold loop → "what time
is it thanks for watching"* — the question survives, the artefact does not.

11 tests, 4 mutations all failing the file: the sequence pass removed (4), stop
at the threshold (2), threshold lowered to 3 (2), the collapse not wired into
`clean_transcription` at all (3).

**A third path, found by the same question and deliberately left.**
`meeting.transcribe` goes through `recordings.segments_for`, which also returns
raw engine text with no collapse. It is a different severity and so a different
decision: the segments are **summarised by the local LLM** before anything reaches
a model, so a loop costs context rather than being forwarded as a fact - and the
one place that would catch it (`recordings.segments_for`) is shared with the
`recordings` skill, where collapsing a transcript someone asked to read is a
change to what they are shown. Recorded rather than fixed, because the fix wants
a decision about whether a transcript a person reads may be shortened.

**Also measured, and taken as a difference rather than a gap:** their CLI makes
text normalisation an **opt-in `--normalize` flag**, and their backends carry a
`supports_instruct` capability flag because "Base model drops instruct
silently" — per-engine text preparation keyed off a declared capability.
`tts_text.normalize_for_speech` is engine-agnostic by design here, because every
engine in the cascade is a phoneme front-end that wants the same rewriting, and
a caller who cannot express the preference should not be asked to.

## open-intelligent-ui, sixth pass: nothing new taken, and why that is the finding

Read the rest of the repo (`page.tsx`, `layout.tsx`, all 219 lines of `map.tsx`,
`vector-basemap.ts`, the generated library spec). **No sixth transfer**, recorded
in `../harness-study/HARVEST-OIU.md` section 7 with the evidence, because a
transfer invented to justify another pass is the "confident wrong answer" shape
this file keeps recording.

Each remaining idea was checked against the code and found present:

| OIU | Chronoa already |
|---|---|
| `starters` — short label carrying a longer prompt | `SuggestionBar`, built from the **live** registry so an unloaded skill is never offered, ellipsised, and clicking *fills* rather than submits so the suggestion stays editable |
| the streaming-readiness gate — `(!streaming \\|\\| !!stop.emoji)`, so `-1` mid-stream cannot move a map | no such hazard: tool arguments arrive already-parsed, and partial markdown was measured safe |
| `routeKind`'s four states, with "loading" among them | the status line naming a running tool (`add_tool_call`'s docstring) |
| lazy-load + raster fallback | `browser.is_available()` / `unavailable_reason()`, and `maps.py`'s OSRM/Overpass choices |
| design tokens | `STATE_PALETTE`, 11.61:1 light / 13.88:1 HighContrast / 17.58:1 settings |

The one structural difference worth naming: OUI can put a loading state in the
same widget that will hold the answer, because the token stream goes straight
into the page. Chronoa's skills are subprocesses returning a string. A mid-run
progress mechanism would be a real design decision, not a harvest item, and it is
left as one.

## ECC: the skill dashboard that goes green when its writer breaks (2026-10-11)

`../harness-study/ecc` (`affaan-m/everything-claude-code`) is a harness-performance
system: 71 agents, 311 skills, 97 commands, and its real substance is *runtime
enforcement* — Node hooks interposed on every tool call. Most of what it has,
Chronoa already has and is further along with: `usage.py` counts tokens against a
price table and refuses to turn "unknown" into `$0.00`; `provenance.py` fences
untrusted content with a **per-turn nonce** so a document cannot forge its own
terminator; `guardrail.py` is deterministic argument validation on the stated
principle that "a prompt is a promise, a fact is the only reason the check is
worth having"; `learning.py` is wired live into dispatch, selection, reflexes and
distillation. **Do not re-propose any of those** — the harvest is in
`../harness-study/HARVEST-*.md` and Chronoa's own `usage.py` docstring already
names opencode as its source.

**The one thing taken, and it is the #2463 lesson.** ECC's skill dashboard counted
a never-run skill as *healthy*, so a broken telemetry writer produced a green
report across every skill and no alert ever fired. Its own fix comment is the
transferable sentence:

> *A skill with no runs in the window is not healthy, it is unmeasured. Folding
> it into "healthy" turned a missing telemetry writer into a green dashboard
> across every skill (#2463) — false confidence that never triggers an alert.*

Chronoa had the same shape from the other side. `gui/surfaces/activity.py`'s
per-skill block called `per_skill()`, which returns **only skills that have
calls** — so three rows read as a complete account of skill health while the other
252 skills were absent from the screen as well as from the log. "Absent" and
"nothing to report" are different claims, and nothing on screen said which one it
was.

`skill_coverage(records, known)` returns `(skills, measured, unmeasured)` with
`total = measured + unmeasured`, and the "By skill" subtitle now states it:

```
254 of 255 skills have never run here, so this block is what ran, not what works.
```

Three rules it keeps, each for a reason:

- **The denominator is the registry, not the log.** An unreadable registry
  returns `(0, 0, 0)` rather than counting the skills that happened to appear —
  a number over the wrong universe is worse than no number.
- **An `unverified` call is not a measurement.** It is most calls on a real
  machine and says nothing about whether the skill works.
- **A name in the log is not necessarily a skill.** `liar` and `unver` are
  fixtures that no longer exist; they are not two more skills the whitelist is
  missing (this is `installed_skills()`'s existing job, reused).

`tools/mutations/activity.py`: **4 mutations, 4 caught, 0 survivors, 0
equivalent.** The two that matter are the fold itself (`total` → `measured`) and
`unmeasured → 0`, each of which leaves the panel reading perfectly healthy.

## ECC, second pass: the history that survives a restart (2026-10-11)

From `../harness-study/ecc` again. Its `skill-run-tracker` + `cost-tracker` pair
turned on a detail that is easy to miss and that Chronoa had wrong: **the durable
record and the per-request read were two different things, and the fast one read
the empty one.**

`weights_from_tracker(tracker)` read `tracker.get_calls()` - the tracker's **ring**,
which is `deque(maxlen=100)` held in *this process's* memory and which
`ToolTracker.__init__` never hydrates from the log. So the learned tool multipliers
were `{}` until a hundred calls had accumulated in the current process. Measured:
a fresh `ToolTracker` over a directory holding 150 recorded calls answered
`get_calls() == []` and `weights_from_tracker() == {}`, while `learning.load()` read
all 150 from the same file. **Every restart discarded the whole history for the
hot-path advisory** - and on this machine the log is 18.9 MB / 19,103 lines / 14k+
verdicts, so "100 calls in this process" is a small fraction of one session.

It is the outcome `tool_select._tracker()`'s own docstring says it exists to
prevent, reached by a route it did not consider:

    a fresh `ToolTracker()` would be an empty history - learning from nothing
    and concluding every tool is unproven

**The ring was deliberately not fixed instead.** `settings_window/activity`
renders those rows marked `live=True` - "this process ran it" - so hydrating them
would make that panel claim a process executed calls it never saw. Two readers
want two different things, and the ring keeps the narrower, truer one. So the
durable history gets its own read: `_durable_records()` seeks the **tail** of the
log (the log is append-only and unbounded, so the newest records are the ones a
rate cares about), capped at `HISTORY_LIMIT = 2000`, and cached per
`(size, mtime)` exactly as `load()` already does. Measured on the real log:
**16.3 ms** to seek and parse the tail, **~0.1 ms** for the cached read, and
**115 tools** get a learned weight after a restart instead of zero.

Three of my own defects on the way, each caught by running rather than reading:

- The first `_read_tail` `partition`ed at the first newline **unconditionally**,
  so it dropped a valid record whenever the whole file fitted in the byte budget.
  A one-record log read back zero records and every weight vanished - the bug
  being fixed, reintroduced inside its own reader.
- The first version of `test_a_history_larger_than_the_limit...` asserted
  `"old_tool" not in weights`, which is false: with 2,430 records and a 2,000
  limit, 430 head records go and 1,970 `old_tool` stay. A green-looking
  assertion about which records survived.
- The first version of the no-log test pointed `log_file` at an *absent file*,
  which raises inside `stat()` and never reaches the `except` it was written to
  test - so the mutation it guards survived until the tracker's `log_file` was
  made to raise outright.

`tools/mutations/history.py`: **5 mutations, 4 caught, 0 survivors, 1
equivalent.** The equivalent one is kept and documented: dropping the seek's first
byte and dropping the partial line it landed in reach the same set of records,
because a record missing its opening `{` does not parse and the reader already
skips unparseable lines. It looks like a defect and is not one, which is exactly
why it is written down rather than deleted.

Two mutations first reported **survivors**, and both were my tests not reaching
the code they claimed: the tail test wrote records far under the 4 MiB byte
budget so the seek path was never taken, and the no-log test took the wrong
branch. Both fixed in the test, not in the set.

## ECC, third pass: a sense that never answered stopped every sense (2026-10-11)

From `../harness-study/ecc` a third time, into
`skills/continuous-learning-v2/hooks/observe.sh`. Its comment states the rule:

> `signal.alarm(8)` - self-terminate **before** the async hook 10s timeout can
> orphan us (#2278)

and, on the scrubber it guards: *"Linear-time secret matcher. Bounded
quantifiers and a fixed set of auth schemes (instead of a generic
`[A-Za-z]+\s+` that overlapped the value class) prevent the catastrophic
backtracking that pegged python at 100% CPU (#2278)."* Verified by reading the
file itself rather than a summary: the scrubber exists in **two** copies (the
parse-error fallback path and the main capture path), both carrying the same
alarm.

**Chronoa had the hole that alarm exists to close.** `AmbientScheduler.poll()`
called `sense.run(...)` guarded only by `except Exception` - which catches a
sense that *raises* and does nothing whatever about one that *hangs*. Measured:
a sense whose `run` blocked on `threading.Event().wait()` left `poll_due()`
blocked forever, and the healthy sense registered next to it was **never
polled**. One hung sense stopped the entire ambient layer for the life of the
process, which is every sense in it. The near-miss is recorded in this file
already - `kernel_log` drains `/dev/kmsg` non-blocking because a blocking read
"waits for a message that may never come and burns the whole skill budget" - but
that was one instance fixed by hand, not the general defence.

`_run_with_budget()` runs the sense in a worker and joins with a timeout. The
budget is the sense's **own `poll_interval`**, floored at 30s and capped at 120s,
because `poll_due()` already defines a sense slower than its own interval as one
that is too slow ("would make `poll_interval` mean 'interval between *starts*'
for a sense slower than its own interval"). A constant would silently decide
which of a 2s audio sense and a 10-minute snapshot check may be slow.

**Three decisions worth knowing before changing this:**

- **`signal.alarm` does not port.** It can only be armed on the **main** thread
  and the scheduler is not on it - it would either do nothing or take down the
  whole application. That is why this is a join rather than an alarm.
- **The sense's thread is abandoned, not killed.** There is no safe way to kill
  it. A sense stuck in a blocking read keeps its thread and its fd; what stops
  is everything downstream of it. That is a real cost and a much smaller one
  than the ~50 senses it replaces.
- **A late result is discarded, not stored.** Collecting it would mean a percept
  with a timestamp from before it was produced, and a store write racing the
  sense that follows.

A timeout goes through `_note_failure`, so the senses' own failure tracking sees
it, and returns `ok=False` with the budget named - `:g`, not `:.0f`, so a 0.25s
budget does not print "timed out after 0s".

Two of my own defects on the way: the first `_read_tail`-style mistake recurred
here as an unconditional `partition` dropping a valid record (in the *other*
fix), and the first mutation run **timed out at 300s** because with the budget
removed the tests blocked on the calling thread - which proves the mutation is
caught but cannot be observed. The hang is bounded at 45s now, with the reason
written next to it: an infinite wait makes a broken build hang until the runner
kills it, so the mutation it exists to catch is reported as a timeout rather than
a failure.

`tools/mutations/sense_budget.py`: **7 mutations, 6 caught, 0 survivors, 1
equivalent** (445s). The equivalent is kept and documented - no sense in the
suite raises a `BaseException`, so narrowing the worker's `except
BaseException` to `except Exception` reaches the same outcome for every input
supplied, and the wider clause is the deliberate choice.

## The harvest said this guard was missing. It exists - and had no test (2026-10-11)

Studying `harness-study`'s existing harvests rather than only new clones, because
a harvest is a *claim about the tree* and these are dated.
`HARVEST-2/E-memory-mcp-special.md` records mem0's "threshold gate before
combining" as a Chronoa gap:

> *"a correctness guard Chronoa's hybrid search lacks (reciprocal rank has no such
> floor)"*

**The claim is false, and the way it is false is the finding.**
`conversation_store.MEANING_FLOOR = 0.55` has existed all along, and
`_by_meaning` applies it at `:717` - inside the candidate path, **before** the
reciprocal-rank fusion, which is precisely where mem0 applies its gate.

What Chronoa actually lacked was a test. `test_conversation_store.py` has 17 tests
and none mentions the floor; no other file covers it either. So the guard was real
and unpinned, which in this repository is the same statement as *the guard might
not be real* - a floor somebody can delete without turning the suite red is a
floor nobody has.

`tests/test_search_meaning_floor.py` now pins the contract through a fake `embed`
whose vectors are normalised word indicators, so cosine similarity is exact
arithmetic rather than a model output. A message containing **every** keyword of
the query while being about something else entirely scores 0.38 - past the keyword
side and below the meaning side - and must be labelled `words`, never
`words and meaning`. Each test states the number it depends on, so a fixture that
drifts fails loudly instead of quietly changing what is being tested.

Three things building this taught, each the same shape as the last three passes:

- **A claim in a harvest doc is a claim about the tree, and the tree moves.**
  Reading this one as a gap would have built a second floor beside the first - the
  duplicate-guard failure `00-MASTER-BACKLOG.md`'s new banner warns about, in a
  second document, on the same day.
- **A witness that cannot fail is the same defect as a witness that always
  passes.** Two of the four mutations were first named against witnesses that pass
  for both the fixed and the broken code, and the harness correctly reported
  "NOT caught by" beside "caught by". Dropping them is what makes the set a
  control.
- **A hand-written `.jsonl` is not a session.** `append()` writes the file but
  does not register it; `list_sessions()` reads the index; `search()` then drops
  every hit at `if not row or row[0] not in titles` and returns an empty list,
  which reads exactly like the code being broken. The fixture builds through
  `active_path()`/`new_session()` now, with the reason written next to it.

## AirLLM: residency and runnability are different questions (2026-10-11)

`harness-study/airllm` (`lyogavin/airllm`, cloned this session). Its one-line
thesis: *"AirLLM only ever keeps one layer on the GPU at a time, so the VRAM you
need depends on the model's layer size - not its total size."*

Chronoa was telling a machine short on RAM **"20 GB of RAM (this machine has
6.0)"** - which, on a card that also says *"reads 2.2 GB per token"*, reads as
"cannot run". For the tier where the two numbers diverge most that is the wrong
reading, and the divergence is in our own table:

| tier | `ram` | `reads` (per token) |
|---|---|---|
| `qwen3-8b` | 16 GB | 4.68 GiB (dense: the file *is* the working set) |
| `qwen3-30b-a3b` | **20 GB** | **2.05 GiB** (MoE: ~10x apart) |

`bytes_per_token()` already computed the right number - the module's own docstring
says it is *"the number decode speed and memory pressure both follow, and the two
differ for a mixture-of-experts"* - and the refusal never used it.

The RAM reason now names what the figure is **for** when the per-token cost fits:

> 20 GB of RAM to keep every weight resident (this machine has 6.0). Each
> generated token only reads 2.05 GiB, which fits, so this is a residency limit
> and not a cap on whether it runs.

Two rules this keeps, both for the same reason:

- **When the per-token cost does *not* fit, the plain sentence is kept.**
  `qwen3-14b` reads 9 GiB per token; telling a 6 GB box that is "not a cap on
  whether it runs" is a softer claim than the truth, which is the same defect in
  the other direction.
- **It claims no decode rate.** Nobody here has measured a larger-than-RAM model
  on a small box, and `ram / active_bytes` is a ratio of two unrelated quantities
  rather than a tok/s. A refusal that invents a number is the confident wrong
  answer the sentence exists to avoid, and the mutation set carries a mutation
  for it.

**Not applicable from the same repo, recorded so nobody re-derives them** (full
table in `../harness-study/HARVEST-AIRLLM.md`): the layer-streaming mechanism is
unnecessary because llama.cpp mmaps and the OS already pages a too-big model; the
per-expert streaming idea is already `active_bytes`; and AirLLM's *"never force
fp16, it overflows to inf/NaN"* has no target here because `server_args()` passes
no dtype at all.

**The one thing worth copying later is not a mechanism.** `max_pinned_layer_bytes
= 2 GB` with its comment - pinned memory cannot be paged out, so with prefetching
two in flight a ~17GB layer would lock ~34GB of RAM *"for a copy that is dwarfed
by the disk read anyway"*, and it falls back to pageable rather than failing - is
the discipline for any "keep this hot" branch added here later: bound a
non-reclaimable cost by the size of what needs it, state the number that
justified the bound, and always have the fallback.

## The cross-check that was never performed (2026-10-11)

edge0's `HARVEST-EDGE0.md` identifies the one place Chronoa does arithmetic on a
third-party file format and proposes a second, independent derivation of
`kv_bytes_per_token` from the tensors rather than the metadata, because a test
written from the same reading as the module agrees with the module whether or not
the reading is right.

**That work had already landed** - `ctxfit.kv_width_from_shapes` exists, with
edge0's own reasoning quoted in its docstring. And it had **no caller in `usr/` at
all**. Documented, unit tested, and never compared against anything: a
cross-check nobody performs is not a cross-check, and the module could have been
misreading every convention in the GGUF header for its whole life with nothing
noticing, because the two routes never met.

`ctxfit.kv_width_disagreement*()` is the meeting, and `local_llm._context_arg()`
is the caller that logs what it says. On a doctored header, on a CPU-only box:

```
LOG WARNING llama-server context: the GGUF header and the model's own tensors
disagree - the GGUF header says the KV width is 384 elements but the K/V
projection tensors are 192 wide; one of the two readings is wrong, and the header
is the one being used
```

Three decisions in it:

- **A disagreement is reported and not acted on.** The header stays the authority
  because it is the only route that knows about shared caches and hybrid layer
  patterns; silently switching on one file would trade a visible wrong number for
  an invisible right one. Pinned by `test_a_disagreement_does_not_change_the_
  decision` - the returned context is byte-identical either way.
- **`None` means "could not compare", never "they agree".** A caller reading a
  None as agreement has a check that passes by not running. `test_no_comparison_
  is_not_the_same_answer_as_agreement` holds that, including the per-layer
  `head_count_kv` list case where the shape reader legitimately refuses.
- **The scoping mistake I made first is pinned by its own test.** The check was
  wired inside `if info and weights and total:`, and `total` is
  `total_vram_bytes()` - None on a CPU-only machine. Chronoa's default local
  brain runs on CPU, so as first wired the check never ran where it matters most,
  and no test would have failed because they would all have skipped on the
  missing model anyway. `test_it_runs_on_a_machine_with_no_vram` fails if it is
  scoped back inside.

`tools/mutations/kv_parity.py`: **4 mutations, 4 caught, 0 survivors, 0
equivalent**, run with `CHRONOA_KV_PARITY_MODEL` set so the real-file route is
exercised and not only the shapes fixture. The real-GGUF tests skip and say so
without the variable - the convention `test_ctxfit.py`'s parity test already set,
and deliberately **no** `Path.home()` fallback, because the suite redirects HOME
per test and a path resolved at fixture time is the scratch directory.

## airllm's chunked cross-entropy, and a 297x win on the inference path (2026-10-11)

Second pass over `harness-study/airllm`, into `air_llm/airllm/chunked_ce.py` - the
file the first pass never opened:

> *Fused linear-cross-entropy that never materialises `[N, vocab]` logits. ...
> This walks the vocabulary in chunks, keeps a running log-sum-exp, and recomputes
> the same chunks in backward to get `d_hidden`.*

Chronoa computes no cross-entropy, but the **discipline** transfers exactly, and
it had a target here. `vectorise()` allocated a `_N_FEATURES`-wide list, filled
it, normalised it into a *second* such list, and every caller then called
`_active()` to walk all 16,384 slots looking for the handful that were set. Its
own docstring described the trade as deliberate: *"the 16384-wide list is built
once per example and then read sparsely"*.

Measured on this machine's own log before the change:

| | 19,103 examples |
|---|---|
| `_active(vectorise(features(r)))` | **54.7 s** |
| `active_vector(features(r))` | **0.18 s** |

And `predict_proba()` - the live inference path, called on every tool call - paid
1.43 ms for a shape whose sparse form was the answer all along.

`learning.active_vector()` builds it directly from the set of distinct indices:
`sqrt(number of distinct indices)` is the norm, and the pairs come back sorted by
index. **297x faster, byte-identical output.**

Equivalence was checked two ways rather than assumed, and the second is the one
that matters: **all 577 distinct feature sets in the real log** compare equal, plus
hand-built cases including hash collisions. That last one is a real trap - two
feature names hashing to the same index are *one* column at 1.0, not 2.0, so a
sparse implementation that counts occurrences rather than distinct indices
normalises differently and passes every input that happens not to collide. The
test searches 4,000 probes for a collision to prove the case exists at all.

Three more decisions in it, each with a mutation behind it:

- **The norm is `sqrt(distinct columns)`, never `sqrt(occurrences)`.** The
  collision test is what catches it; without it the bug is invisible.
- **The order is by index, so the output is deterministic.** `collapse()` keys a
  dict on the tuple - *"11,590 examples carry only 344 distinct vectors"* - so a
  set's iteration order would silently break the 34x dedup the whole layer rests
  on, while every value-level assertion still passed.
- **`vectorise()` is kept**, for the tests that pin `active_vector` against it and
  for any caller that genuinely wants the dense form. Both hot call sites
  (`load()` and `predict_proba()`) now take the sparse route.

## unsloth: the KV cache was a measurement, and it should have been a lever (2026-10-11)

`harness-study/unsloth` (`unslothai/unsloth`, cloned this session - 221 MB, 118k
lines of core Python). It is the fast-finetuning library and now also a local
run/train desktop app, so `studio/backend/core/inference/llama_cpp.py` is 42k
lines of llama.cpp integration - the closest thing in any of these clones to
Chronoa's `local_llm.py`.

The transfer is one decision. `kv_bytes_per_token` hardcoded `* 2` with a comment
saying it assumed llama.cpp's f16 default, and `context_that_fits()` walked a
ladder of *contexts* with the KV cost fixed - so the only way to fit more context
was to shrink it. unsloth ranks llama.cpp's cache types by bytes per element and
picks the lightest that fits:

```python
min(_planned_main_cache_types(cache_type_kv, extra_args, env), key=_kv_bytes_per_elem)
```

with an exact table - `f32` 4.0, `f16`/`bf16` 2.0, `q8_0` 34/32, `q5_1` 0.75,
`q5_0` 0.6875, `q4_1` 0.625, `q4_0`/`iq4_nl` 0.5625.

**The KV cache is one of only two things a context decision can trade away** -
shrink the context, or compress the cache the context is made of - and Chronoa had
never used the second. `ctxfit` now carries the table, `kv_bytes_per_token()`
takes the type, and `cache_type_for_context()` walks the ladder
**best-first** so the first type that fits is also the least lossy one that fits.

Measured on Qwen3-8B's shape, 6 GiB of VRAM and weights taking 90% of it:

| target context | chosen | why |
|---|---|---|
| 2048 | `q8_0` | f16 does not fit, `q8_0` does - the context is kept |
| 4096 | `q5_0` | `q8_0` no longer fits, so the next lighter |
| 8192 | `q4_0` | nothing lighter exists; cheapest tried |

Three rules it keeps, each with a mutation behind it:

- **`f16` is the default and is unchanged.** The parameter defaults to what the
  function always did, so anything that fitted before still fits at the same
  context, and `server_args()` emits **no** `-ctk`/`-ctv` unless the decision
  actually moved - a machine that fits keeps the exact command line it had.
- **An unknown type costs the f16 default, not the cheapest.** The unsafe
  direction would promise a context the VRAM cannot hold.
- **When nothing fits, the cheapest tried is returned - never `f16`.** Returning
  `f16` there would claim a quality level the numbers do not support.

## unsloth, second pass: no tool result may eat the conversation (2026-10-11)

Second pass over `harness-study/unsloth`, into `studio/backend/core/inference/
tools.py` - **23,072 lines**. Most of it is a shell-command safety analyser
(`_sed_*`, `_redirection_span`, `_unquoted_expansion_indexes`) guarding a generic
shell tool that Chronoa deliberately does not have and must not grow. Off-limits by
the repo's own Boundaries section.

One function in it transfers, and it is a one-line answer to a real hole:
`_tool_result_max_chars()`.

**Chronoa had no floor under a tool result at all.** `MAX_HISTORY_MESSAGES = 40`
bounds the *count* of messages, not their size, and the path from `_run_tool` to the
history entry is `provenance.fence(result, source).text` with nothing between.
Every skill that can return something large bounds itself - `read_text_file` refuses
a file over 512 KiB, `webtext` caps page text at 4,000 characters,
`search_file_contents` stops at 60 matches - but that discipline is per-module, so a
new skill, a changed one, or an unbounded path in an old one had nothing under it.

What that costs: one skill putting tens of MiB into `_history`, which Ollama
*drops* rather than errors on - so the conversation silently loses its earlier
turns and nothing on screen says why.

`_clip_tool_result()` now bounds every result at 1 MiB with a note naming what was
dropped and what the limit was. The floor is **twice the largest deliberate bound**
(512 KiB), so it is a backstop rather than a policy: it does not truncate anything a
skill was designed to return, and only fires on something genuinely unbounded.

**The ordering is the part that matters, and it is a security property.** The clip
runs *before* the fence, not after. Measured both ways on a 2.4 MB page:

    clip-then-fence : (1 open, 1 close)   <- the shipped order
    fence-then-clip : (1 open, 0 close)   <- the close marker is gone

Clipped after fencing, a cut can take the closing marker with it, and the model is
then shown untrusted content with no marker on it - trading a context problem for
the exact hole `provenance` exists to close. `test_the_clip_happens_before_the_fence`
asserts both directions, so the control demonstrates the hole rather than restating
the rule.

`tools/mutations/tool_result.py`: 4 mutations, all caught - including the one that
moves the clip inside the fence.

## personaplex: the loudness target that is not needed, measured (2026-10-12)

`harness-study/personaplex` (NVIDIA's full-duplex speech-to-speech model, cloned this
session). Its voice conditioning is normalised to a target before use -
`normalize_audio(raw_audio, self._sample_rate, -24.0)`, *"Normalize to -24 LUFS
(mono-safe)"* - so a persona's voice is consistent whatever recording it came from.

Chronoa has four engines with measured-different character and no loudness
normalisation at all (the only `gain` in the chain is `voice_style._eq`'s EQ). That
looked like a real defect. **Measured, and it is not one.**

The same sentence through each named style's own SoX effect list:

    unstyled -19.89 dBFS ... calm -19.95 (-0.06) ... warm -18.92 (+0.97)

**Spread: -0.06 to +0.97 dB.** The just-noticeable-difference for loudness is about
1 dB, so the whole style table sits inside it. Choosing a style changes the timbre
and not the volume - which is what the design intends and already works.

The axis PersonaPlex's normalisation is actually *for* - variance between input voice
recordings - has no Chronoa equivalent, because Chronoa's voices are engine ids, not
embeddings. The axis that would matter here, **the four engines' relative loudness**,
is not measurable on this box: only `espeak-ng` resolves, and `piper`/`kokoro` are not
installed. That is a slot run, not a guess.

**What would settle it, and what to do then:** in a slot with a Piper voice and the
Kokoro model both installed, synthesise the same sentence through each engine and
compare RMS. Under ~1 dB, close it. Several dB, put the target at the *engine
boundary* (`PiperTTS.synthesize()`, after the engine and before SoX) - measuring what
was produced rather than what was played.

**Nothing was built.** An unmeasurable loudness target is a number nobody can check,
and `gain` applied to a signal whose level is already fine is pure risk. Full
write-up with the tables: `../harness-study/HARVEST-PERSONAPLEX.md`.

The one discipline here worth remembering is not the loudness: `server.py:170` guards
an expensive reload with a change check (`if self.lm_gen.voice_prompt !=
voice_prompt_path`) and `:157` **raises** for a voice prompt that does not exist
rather than defaulting. Both are already the house rule - guard the reload, refuse
rather than default - and both are the things to check first if a persona/voice
switch is ever built here.

## personaplex: the loudness target, and it turned out to be needed (2026-10-12)

First pass over `harness-study/personaplex` (NVIDIA's full-duplex speech-to-speech
model) concluded the loudness normalisation it does - `normalize_audio(...,
-24.0)` on every voice prompt - was **not** a Chronoa gap: the style table's
loudness spread measured -0.06 to +0.97 dB, inside the 1 dB JND, so the harness
never got the measurement that mattered.

`cache/data-home` has what the first pass lacked: a Piper voice, the Kokoro model
and the sherpa-onnx binary, all installed once into the git-ignored cache. Measured
through `PiperTTS.synthesize_to_bytes`, one sentence per engine:

| engine | RMS before | RMS after | peak after |
|---|---|---|---|
| espeak-ng | -19.89 | -19.90 | -4.06 |
| piper | **-18.86** | -20.00 | -1.51 |
| kokoro | **-24.69** | -20.00 | -4.56 |

**A 5.83 dB spread,** against a just-noticeable-difference of about 1 dB - so
choosing a voice engine changed perceived loudness by roughly a factor of two, and
`piper` arrived at **-0.00 dBFS peak**: full scale, no headroom, so a
positive-gain EQ from a voice style would clip it. Both are the axis PersonaPlex
normalises for, and neither had a Chronoa equivalent.

`PiperTTS.apply_loudness()` now runs beside `apply_timbre()`, at the boundary where
the "not an engine" steps already live (the text normaliser's own comment says why):
`TARGET_RMS_DBFS = -20.0`, `PEAK_CEILING_DBFS = -3.0`, a 1 dB deadband so an engine
already on target is not re-encoded. **After: 0.10 dB spread across all three
engines.** A failure never costs a reply - it returns a reason naming what could not
be measured, exactly as `apply_timbre` does.

**The bug the measurement caught, in my own first version.** The peak ceiling was
applied to *every* gain, so `piper` - which needs a **cut** - was held 1.9 dB below
target for no reason the numbers explained. A cut never raises a peak, so the
ceiling belongs to lifts only. `test_a_cut_is_not_held_back_by_the_ceiling` pins it,
and a mutation restores the wrong version to prove the test can fail.

The second trap was the fixture: `tests/conftest.py`'s `_hermetic_env` redirects
`XDG_DATA_HOME` to a scratch dir, so the engines physically installed in
`cache/data-home` are invisible to any test that just looks - and the engine
measurements skipped on a box that had all three. `in_that_graph` already links the
cache in for the other measured-engine tests; the new test does the same without
the PipeWire graph it does not use.

## Correction: `learning.load()` is live, and my earlier claim it was dead was wrong

Earlier this session I reported that `learning.load()` had **no caller in `usr/`**
and that the live reader was `_entries()`. That was wrong, and it was a grep that
did not cover the call form: I searched for `learning.load(` and missed the
**unqualified** in-module call. `load()` has three real callers:

| caller | line | what it is |
|---|---|---|
| `train_and_save` | 2220 | the training entry point |
| `merge_models` | 3023 | the merge verdict path, from the Models panel |
| `train_and_report` | 4173 | the "is there signal in this log at all" report |

`_entries()` is live too, but on a different surface - `unknown_tool_examples()` and
`lessons()`, which want raw dicts. Two readers, two shapes, both called.

**Why the mistake mattered.** It made the `active_vector` fix look like dead-code
cleanup with a live side effect, when the truth is the opposite: `load()` is the
reader for every training, merge and report path, and the 297x speedup
(`245b1d8`) was on all of them. Measured after the fix, on this machine's 18.9 MB
log: **0.23 s cold, 0.000 s warm, 18,787 examples** - against the 24 s this file
records for the pre-fix path on a smaller 16 MB log.

So the same lesson as the `cache/data-home` miss, from the other side: a grep for
`module.function(` finds cross-module callers and silently drops the ones inside
the defining module. For a function in a large file, the search has to include the
bare `function(` form.

## mimic: the generated skill, and the rule that already refused it (2026-10-12)

`harness-study/mimic` (`littledivy/mimic`, `8cf998a`, 743 lines, MIT) is the
whole premise of this repo turned inside out: capture an app's traffic, and have
a model write the Python client, so nobody writes the client.

    capture traffic   ->   extract auth   ->   generate client
      (mitmproxy)         (mimic.Session)      (an AI reads the endpoints)

**Five candidates checked, none taken, and the headline one was already
refused.** Write-up: **`../harness-study/HARVEST-MIMIC.md`**.

The refusal is not mine and predates the clone. `ARCHITECTURE-TARGET.md:547`:
*"Skill learning | 🚫 | new skills are code a person adds; a self-written skill
would break the whitelist"*, and `:736` repeats it. `mimic.App` is subclassed
with `self.get()/self.post()` and a `HOST` — which is **how a whitelist member
is defined**. A model emitting one is not a feature; it is a new member of the
whitelist, chosen and parameterised by the same pass that read network traffic.
Same reason `Boundaries` refuses a generic shell-exec tool, and same reason it
marks an MCP *client* ⚠️ ask-first: it needs its own trust story first.

The other four closed on measurement, not on taste:

- **`DROP` (headers the HTTP layer owns) is already structural.** `netjson.py`
  sets exactly `{"User-Agent": ...}` and lets httpx own the rest; no call site
  in the tree accepts caller-supplied headers. Narrowing a path that never
  widens is this file's own recorded failure in the other direction.
- **"Retry only when the credential actually changed" has nothing to apply to.**
  No retry loop on any HTTP path — the `retry|attempt` grep hits are `Thread.run`
  and `asyncio.run` by attribute name. `cloud_llm` is further along anyway,
  classifying payment-required / auth / rate-limit and never probing a provider
  it cannot authenticate.
- **"Find the newest authenticated request"** has no analogue: Chronoa's tokens
  are env-supplied and the *bridge's*, per `channel_bridge.py:13` ("Chronoa
  never sees a Telegram or WhatsApp token"), so none can go stale the way a
  replayed capture does.
- **The dependency-chaining prompt rule is already enforced rather than
  asked for.** `goals.py:378` *refuses to start* a step whose `depends_on`
  variables do not exist; mimic's prompt only says "chain the calls".

`unpin.py` has no analogue at all — it hooks TLS verification inside an iOS app
so a proxy can read its plaintext. Not a gap and not a TODO: Chronoa is a client
of *published* public interfaces, and that code defeats a security control on
someone else's device.

**What would move it**, recorded so the next pass starts there rather than at
the clone: a digest a person then reviews and commits by hand keeps the rule
exactly as written — it says skills are code a *person writes*, not code a person
*typed*. The existing seam of that shape is `cli_matrix.py --scaffold`, which
writes a skill from a command's man-page flags and marks anything not read-only
generated-disabled. Same shape, documented source instead of observed traffic.

One adjacent question this raised and **did not verify**, so it is not a claim:
`CloudLLMChain.__init__` reads its keys once (`cloud_llm.py:906-935`) and bakes
them into the backends, so a key rotated while the app runs waits for the chain
to be rebuilt. Whether that is a defect depends on `_maybe_enable_cloud_fallback`
re-running on a key change — a question about this tree, with nothing to do with
mimic.

## The MoE tier's bytes-per-token is derived now, not declared (2026-10-12)

`local_llm.REQUIRES` carried `active_bytes: 2_200_000_000` for the one
mixture-of-experts tier, marked `measured: False`, and `requirements_met()` refuses
that tier as a default on exactly that basis - *"a number nobody measured is the
confident wrong answer this repo keeps recording"*. Nothing could improve it,
because **nothing read the checkpoint to find out**: `bytes_per_token()` returned
the declared constant, or the file size for a dense model, and there was nothing in
between.

A GGUF carries its own routing topology, and the field names are gguf-py's own
(`gguf/constants.py`), not remembered from a model card:
`expert_count`, `expert_used_count`, `expert_feed_forward_length`,
`expert_shared_feed_forward_length`, `feed_forward_length`,
`leading_dense_block_count`. `ctxfit.expert_bytes_per_token()` derives what one
token touches - the attention projections plus **only the routed experts** - and
`bytes_per_token()` now prefers it whenever the checkpoint is on disk, with the
declared constant remaining the fallback for a tier that is not installed.

**The cross-check that made it a measurement rather than a different guess.** The
AGENTS.md records independently that Qwen3-30B-A3B stores 17.3 GiB and activates
~3.3B of 30.5B parameters, so it reads **~2 GiB per token**. The declared constant
is 2.05 GiB. The derivation lands at **1.52 GiB** - 74% of the declared figure,
and the same order as the independently-recorded one.

**The first version was wrong by 8x, and only the cross-check showed it.** The
bytes-per-weight cost was divided by the *active* parameter count, but the file
stores **every expert** - a twelve-fold undercount for this model - so the derived
figure came out at 17.28 GiB, above the file's own size, which is the kind of wrong
no amount of reading the arithmetic reveals. Two counts now: everything the file
holds, which sets the per-weight cost, and what one token touches.

`measured` stays `False`, and deliberately so: the flag gates the **decode rate**,
which still needs a live model. What this changes is that the tier's blocker is now
honestly the decode rate alone, rather than the decode rate *and* an estimate
nothing could improve.

Two of my own test bugs on the way, both the shape that recurs: the override helper
applied its overrides **unprefixed**, so every override silently changed nothing and
three tests passed for the wrong reason; and the fixture used `spec.filename.name`
on a plain string.

## A pasted cloud key does not take effect now, and two of three groups said it did (2026-10-12)

Raised by the `mimic` study (`../harness-study/HARVEST-MIMIC.md`), which asked
what reconstructs an HTTP client from observed traffic and turned up a live
question in this tree while being answered. **Full write-up is Part 4 there;
this is the finding.**

`CloudLLMChain.__init__` reads `cloud_llm_api_keys()` **once** and bakes each key
into the backend it constructs (`cloud_llm.py:906-935`). Measured by reading the
callers, not assumed - exactly four things rebuild that chain:

| trigger | site |
|---|---|
| application startup | `app/application.py:566` |
| privacy toggle, **and only when no local model answers** | `app/brain.py:60` |
| switching the cloud fallback on | `app/brain.py:325` |
| `set_cloud_choice("cloud")` - which *does* re-read the keys | `app/brain.py:272-289` |

Typing a key into Settings is not on that list. The row saves it, the Settings
window shows it saved, and the running chain keeps what it was built with.

**And this is a copy defect, not a new requirement.** "Free cloud providers" has
always carried the clause:

> Applies when the cloud fallback next activates, **not to a running session**.

The two groups below it said *"Tried ahead of the free providers when set"* and
*"Tried first when the cloud fallback is on"*. Both read as immediate, and the
gap is widest on **"Cloud providers that require a key"** - there a key is the
difference between the chain having an Anthropic/OpenAI/Google/Groq member and
having none at all, and `distill.py` already records what that looks like from
the user's side: *"a machine with a configured provider reported 'no language
model is configured' while the key sat right there."* "Your own model server" had
the same gap, and it is the group `app/brain.py:249` itself warns is chosen
**once at startup**.

All three carry the clause now. **`tests/test_settings_credential_timing.py`
(4), 6 mutations, 6 caught** - and two of the three checks exist because of what
the controls found, which is the part worth keeping:

- **The reverse check was vacuous until the control ran it.** It asserted only
  that no group *outside* `CREDENTIAL_GROUPS` was found - which a scan matching
  **nothing** satisfies trivially. Replacing `_CREDENTIAL_WORDS` with a phrase no
  description uses left it green. It asserts **both** directions now, so the scan
  is its own control. *An absence presented as a pass is the same defect as an
  absence presented as a failure.*
- **The word list was wrong in a way only that control exposed.** It matched on
  `"api key"`, which is a **row title**, not a description: all 28 group
  descriptions were read off the built window and **two of the three** groups
  never matched. Corrected to what the text actually says.

There is also a **false positive worth keeping as a control**: "Test the keys"
contains "keys" and "server", and it is deliberately excluded - it asks for
nothing and exercises keys already stored, so demanding the activation clause of
it would be the same defect in the opposite direction. A mutation widening the
word list to catch it fails two tests.

**The test file this first went into was rewritten underneath me** (302 -> 841
lines, a different subject, the concurrent session's), which is the hazard this
section already records once. Restoring over their work was not an option and a
path operation was not on the table, so it is a **standalone file** with its own
harness instead. And **that same session's commit `ef19d0f` swept this file's
uncommitted `mimic` note into their commit** - the identical measurement already
recorded for `ec6b295` above. Nothing is lost and the note is in `HEAD`; the
split needs a history rewrite under a live session, which is a person's decision
and not a mechanical fix.

## heretic: the comparison the Rebuild result advertised and nobody made (2026-10-12)

`harness-study/heretic` (`p-e-w/heretic`, `49b56e5`, 6,748 lines, AGPL-3.0).
Write-up: **`../harness-study/HARVEST-HERETIC.md`**. One transfer, and it is not
the thing the repository is named for.

**First, a correction worth having: it is not an adversarial-prompt tool.** It
does not craft suffixes and never asks a model to say what it was trained not to
say. It does **abliteration** — it moves the model's internal refusal
directions, and picks how far by **multi-objective TPE/Optuna search** over two
scorers: refusals ↓ *together with* KL-divergence-from-the-original ↓. Its claim
is the same suppression at lower damage (their table: 3/100 refusals at KL 0.16,
against 1.04 and 0.45 for two hand-made ablations).

### Not taken, and the reason is architectural rather than squeamish

Chronoa's security **does not rest on the model refusing anything**, which is
what makes this a non-risk rather than a suppressed one. `tools._dispatch_inner`
calls `permissions.decide(name, scoped_resource, consent_key, …)` at
`tools.py:1066` **in Python, before the skill runs**; `guardrail.py`, the sandbox,
`provenance.py` and the consent keys are all code. `ARCHITECTURE-TARGET.md` says
it outright: *"A learned component that authorises anything. It advises.
Authority stays with the consent keys."* A decensored model changes which
**answers** Chronoa gives and nothing about which **actions** it can take.

And nothing is lost by refusing it, measured rather than assumed. Its case is
**over-refusal**; `tools/task_eval.py` scores **56/62** with the harness
narrowing the tool set, and this file records `bare` at **0/62** with the cause
named — 19,700 tokens against an 8k context, so the request is *rejected*, not
refused. **What would change the verdict is a measurement, not a model**: a
refusal-rate scorer over `tools/eval_cases.json` reported beside `task_eval`'s
score, so "this model declines things it should answer" becomes a number instead
of an impression.

### Taken: heretic's central claim is not about abliteration

Every scorer is reported **beside its baseline**: `Scorer` declares
`get_baseline_score()`, `kl_divergence.py` captures `baseline_logits` *before any
modification*, and the README puts the original model's row first. And Chronoa had
the exact gap, measured:

`calibrated_quantize()` builds **two** files on purpose — same target, same
source, one fitted with an importance matrix and one without — and says so
itself: *"the uncalibrated build of the same target is **beside it for
comparison**"*. Measured over the whole tree: **`naive_path` had no production
reader at all.** Two occurrences — the line producing it, and
`test_calibrated_quantization.py:142` asserting the two paths *differ*, which is a
fact about the filesystem and not about quality.

So the person pressing Rebuild saw:

    Built Q4_K_M at /home/.../-calibrated.gguf
    In use now: Q4_K_M

`adopt_path`'s gate is right and does something else: it refuses a model >2% worse
than the one being **replaced**, and promotes an unmeasured one while recording
the promotion as unverified. **Was fitting the matrix worth the ten seconds it
took?** was nobody's question — so a rebuild that produced a *worse* file than the
naive build of the same source reported "Built" and stopped.

`local_llm.compare_calibration()` is that measurement, in `quality_verdict`'s own
two-shape form: **measured** (`helped`/`hurt`/`unchanged`, with **both absolute
numbers and the percentage** in the sentence) or **unmeasured** (names *which*
build, and says "unknown" — never "the matrix did nothing"). Three guards that
are reasons rather than tidiness: **`None` is never a measured zero** (a missing
measurement and a measured no-change are different facts, and `perplexity()`
returns `None` on every failure path, so this is the easiest substitution to make
**by accident**); a **non-positive baseline is refused, not divided**; and **two
paths that are one path is not a comparison**.

Settings → Models → Rebuild now prints it, between `Built` and `adopt_path`, and
prints `The matrix's effect was NOT measured.` when it could not be read — so the
absence is visible rather than silently absent. It runs in the existing
`_off_main` worker, so two more `llama-perplexity` runs cost no frame.

`tests/test_calibration_comparison.py` (13). `tools/mutations/calibration.py` —
**7 mutations, 7 caught, 0 survivors, 0 equivalent**.

### Two things the controls caught, and both are the recorded traps

- **A witness naming a class is a witness that never ran.** The set's first
  `expect` for the unmeasurable-build mutation was
  `["TestWhatIsNotAMeasurement"]`, and the runner called it a **SURVIVOR** while
  printing *"4 failed"* — every one the right test. A named witness matching no
  test id is the same defect as a check that cannot fail; both are test ids now.
- **Ten of the thirteen tests call `compare_calibration` directly**, measuring the
  behaviour behind the widget. The runner proved it: a mutation replacing the call
  in `window.py` with a hardcoded dict left those ten **green** and failed exactly
  the two that drive the real handler. Three tests press the control and read the
  lines a person would see, and that mutation fails **2**.

### Read, checked, and not taken

Multi-objective **Pareto** instead of a weighted sum (Chronoa's guards are
deliberate *conjunctions* — `Report.honest()` needs recall lift **and** precision
lift — and a front has no question to attach itself to for a binary decision);
`Score`'s contract that a value *"must be of the order of magnitude 1"* (every
Chronoa score is already ratio-based or scale-separated); `_is_match`'s
normalisation set, including **counting an empty response as a match** so silence
cannot be optimised into a perfect score (Chronoa refuses the degenerate answer in
`Report.honest()` and `evaluate_bandit`, but has no measurement this would
protect); resumable Optuna studies (**Chronoa's fits are 24 s, not hours** — that
is where it pays off); and `is_builtin_plugin` disabling reproducibility for
external plugins (would refuse the exact case
`~/.config/shani-chronoa/skills/` exists for).

AGPL-3.0, unlike everything else in `harness-study/`. **No code was taken** —
the transfer is a discipline, and `compare_calibration` shares no structure with
any line of heretic's.

## personaplex's quantizer is the audio codec, and the KV ladder has no semantic floor (2026-10-12)

Asked to check PersonaPlex's quantization. `moshi/moshi/quantization/` exists and
is exhaustive - `vq.py`, `core_vq.py`, `base.py`, `SplitResidualVectorQuantizer` -
but it is **not weight quantization**. It is the residual vector quantizer of the
Mimi/SoundStream audio codec: `dimension`, `bins`, `codebook_offset`, an EMA decay
over codebooks, `codes` of shape `[B, K, T]`. It turns waveform into discrete
tokens. Nothing in it transfers to a GGUF/llama.cpp weight path, and nothing in it
needs to.

What *does* transfer is the shape of `set_num_codebooks`:

```python
def set_num_codebooks(self, n: int):
    assert n >= self.n_q_semantic and n <= self.total_codebooks
```

**A two-sided bound, where the lower one is semantic.** The RVQ can trade acoustic
fidelity away, never meaning - so a quality knob that is bounded by what the thing
it compresses is *for*, not only by what it costs.

`CACHE_TYPE_LADDER` in `ctxfit.py` has the upper half and not the lower one:
`cache_type_for_context` will walk all the way to `q4_0`, and nothing refuses to.
That is the control this repo would want, and it is **not built**, because the
floor would be a quality claim nobody here has measured. `q8_0` is the
widely-reported near-lossless step and `q4_0` the visible one; asserting either
from someone else's write-up is the confident-wrong-answer shape. Recorded at the
ladder instead, so the next reader sees the missing floor and why it is missing.

**What the pass did settle: the ladder's lower end is supported, measured.** Every
entry reaches `server_args()` as `-ctk <type>`, and a type this build did not have
would fail llama-server's argument parser and take the model down at start - the
only symptom being a server that never comes up. Probed against the real
`/usr/bin/llama-server`; the probe has power because a bogus type is refused at
argument-parse time (`error while handling argument "--cache-type-k":
Unsupported cache type: nonsense_xyz`), before any model is loaded, so the test
could distinguish accepted-from-rejected. **All nine llama.cpp types** - `f16`,
`bf16`, `q8_0`, `q5_1`, `q5_0`, `q4_1`, `q4_0`, `iq4_nl`, `f32` - are accepted, so
every entry in the ladder is safe to pass on this build.

## `no tool` was four different things, and the count that gates the harness could not be repaired (2026-10-12)

Both of these came from following the heretic study's own open item - *"what would
change the verdict is a measurement, not a model"* - and the first attempt at it
found a pre-existing red test that had been red for a while.

### A refusal, a promise, and silence all scored `no tool`

`tools/task_eval.py`'s `score()` reports `"no tool"` for every case with no tool
call, and `run_case` returned that string and **threw the reply away**. So
"I can't tell you the time" and "Let me check that for you" are the same result -
and they are opposite failures with opposite fixes. The second is already recorded
in this file as a live one (`test_routines_live` on "weather in Pune and what time
is it").

`classify_reply()` separates them into `empty` / `refusal` / `narration` /
`answer`, using heretic's `scorers/keyword_rate.py` applied to this harness's own
cases: normalise, then match markers.

**`empty` is its own state because of a measurement, not a hunch.** Qwen3 is a
thinking model, and with `max_tokens: 80` the ordinary eval case "What time is it?"
came back with `content == ""` - the budget spent on `reasoning_content` before it
spoke. An empty string contains no marker, so every other branch passes it
through **as an answer**. I nearly recorded that as the model declining; it was
the harness, and the two readings are indistinguishable from the reply alone.
heretic reaches the same conclusion from the other side (counting an empty
response as a match "to avoid optimizing for them"); here it is its own state,
because "did not speak", "declined to speak" and "spoke plainly" are three
different facts about a run.

The normalisation is load-bearing rather than decorative - both of its steps were
found by a reply that needed it: markdown emphasis splits a marker's own word
apart (`I *can't*`), and a phone gives U+2019 where the marker list has U+0027.

`markdown()` now carries a refusal/narration/empty/answer table and names the
cases in each. `tests/test_task_eval.py` (+6).

### `skills_total` was 250 against a registry of 257, and no tool could fix it

`tests/test_task_eval.py::test_the_recorded_coverage_is_still_the_coverage` was
**red** - not on this work's account, verified against HEAD: `tools/eval_cases.json`
says `skills_total: 250` and the registry has **257**. The gate was working; the
concurrent session's seven new skills had not been counted.

The finding is **why nobody fixed it**. `tools/gen_eval_cases.py` is the only thing
that writes that block, and it rewrites it inside `if added:` - so only when a
skill is missing a case - setting `skills_total = skills_asserted`, because its job
is to *fill the gaps* and leave coverage complete. Correct for what it does, and
useless for this: **running it to correct one stale number would have appended 47
generated cases and flipped `about` to "COVERAGE IS COMPLETE".** A repair tool
that cannot repair is a reason the repair does not happen.

`--refresh-coverage` recomputes the three numbers from the registry and the cases,
writes **no** cases, and leaves `about` alone. The three are pure functions of two
facts already in this repo, so there is no state in which the recorded value is
right and the derived one wrong - which is the whole argument for generating them,
and the same one this file makes about not repeating a number in prose.

**`gated_unguarded: 56` is retired rather than recomputed.** It matches neither
`len(capabilities.GATED)` (**78**) nor the gated skills with no case (**9**),
nothing read it and nothing checked it, so its meaning is not recoverable and
recomputing it would mean inventing one. Retired *with the reason kept*, per this
file's rule that knowledge is retired with the reason rather than discarded.

### Four controls that could not fail, and what each one was

Every one of these ran **green** against the fix before I changed the test:

- **A marker list is only as tested as its least-covered member.** Emptied three of
  the thirteen refusal markers and the file stayed green, because the one measured
  refusal contains "i don't have access" and "i can't". A property that iterates
  the *current* list cannot see a marker being **deleted** - a deleted marker
  simply stops being iterated. Fixed by testing each marker **bare**, because a
  reply that is *only* the deleted marker is the input that distinguishes them.
- **Both marker lists are now pinned**, with the shape argued rather than assumed:
  adding a phrase to a list of ways a model declines is an editorial judgement and
  going green on it is fine; **removing one makes a decline read as an answer**, so
  that direction is pinned. A literal list is the right instrument for a judgement
  being *reviewed* and the wrong one for a fact being *derived* - `skills_total` is
  the latter and is generated for exactly that reason.
- **The `--check`-writes-nothing test compared parsed JSON**, and a rewrite that
  only reformats leaves parsed JSON identical. The file's **bytes** are what
  "wrote nothing" means.
- **The report tests built their rows by hand**, so they proved `markdown` renders a
  classification but not that anything produces one. `run_case` is now driven with
  a reply that declines.

`tests/test_eval_coverage_refresh.py` (9), `tests/test_task_eval.py` (+6).
`tools/mutations/task_eval_reply.py` and `tools/mutations/eval_coverage.py` -
**13 mutations, 13 caught, 0 survivors, 0 equivalent**. 34 sets / 288 anchors.

## The KV cache's quality floor, measured rather than quoted (2026-10-12)

Last round ended with the KV ladder having a physical ceiling but no *quality*
floor - `cache_type_for_context` would walk to `q4_0` and nothing refused to. The
refusal was not built because it would have been someone else's write-up, and the
repo's rule is that a number nobody measured is the confident wrong answer.

It turns out to be measurable here, with the tool this repo already has.
`llama-perplexity` is installed, accepts `-ctk`/`-ctv`, and `local_llm.perplexity()`
already feeds it a fixed corpus written into the module. Same corpus, same model
(SmolLM2-135M-Q4_K_M), both cache types set to the same value, two runs per type -
and the numbers repeat exactly, so the tool is deterministic on this input:

| type | PPL | delta vs f16 |
|---|---|---|
| f16 | 59.1719 | — |
| q8_0 | 59.1499 | -0.022 |
| q5_0 | 59.2380 | +0.066 (0.11%) |
| q4_0 | **62.7689** | **+3.597 (+6.1%)** |

So `q8_0` and `q5_0` are **free** - inside the run-to-run noise of a corpus this
size - and `q4_0` costs 6.1%. `KV_CACHE_FLOOR = "q5_0"` now clamps the ladder
before the walk, so the dial can be compressed to the point that measurably does
not hurt and no further.

That is PersonaPlex's exact control: `set_num_codebooks` asserts
`n >= self.n_q_semantic` - the RVQ can trade acoustic fidelity away, never meaning.

**The honest limits, because a floor taken further than it deserves is worse than
no floor:** one **small** model, one corpus, and perplexity - which is not the same
thing as what a listener notices. A larger model may hold up differently under a
4-bit cache. What the measurement does settle is the direction and the order of
magnitude, and `q5_0` is the step below which this box can demonstrate a real cost.
A better model is a slot run with a bigger checkpoint, and the note says so.

Two of my own errors on the way, both the recurring shape: a docstring claimed the
clamp adds types it only removes, and a test asserted a one-entry ladder would
return a type it did not contain.

## The KV dial's speed axis is unmeasured, and here is why (2026-10-12)

Follow-on to the quality floor. The same dial has a speed axis and
`cache_type_for_context` ignores it, choosing on VRAM fit alone. That matters more
than it looks: a quantized KV cache pays a dequantization cost, so a ladder chosen
to fit more context could partly give back in `tok/s` — and `min_tps` is what
`requirements_met` gates the tiers on. The floor I measured last round bounds the
quality direction; nothing bounds this one.

Tried to measure it, and the failure is worth writing down so nobody re-derives
it:

- **`llama-perplexity` does no real generation** — 0.69 ms for 2 tokens on this
  model. It reports a perplexity figure and no decode rate at all.
- **`llama-bench` is the right tool and takes `-ctk`, but this build refuses to
  create a context with a quantized cache type.** Measured with a negative control
  that the failure is the quantized path and not the flag: `ctk=f16` rows=1
  errors=0, `ctk=bf16` rows=1 errors=0, `ctk=q8_0` rows=0 errors=1. Both types
  that work are 2 bytes per element.
- Meanwhile `llama-server` and `llama-perplexity` both accept `-ctk q8_0` without
  complaint, so the limit is this benchmark binary rather than the runtime — the
  ladder is safe to *pass* (which was the earlier measurement), it just cannot be
  benchmarked here.

So the code now says it plainly: quality measured, VRAM measured, **speed not**,
and why. If a quantized cache turns out to cost decode rate, the lever's tradeoff
is worse than the numbers imply, and the place to settle it is a slot run with a
`llama-bench` that will accept the flag.

## The refusal rate, measured: zero, on both populations (2026-10-12)

`../harness-study/HARVEST-HERETIC.md` Part 0 said the verdict on heretic's
capability rested on an **inference** - `task_eval` scoring 56/62 argues the model
does not over-refuse, but that measures tool *selection*, not refusal. This is the
measurement that replaces it, run against the real `llama-server` with
**Qwen3-1.7B-Q4_K_M**, `select` config, 60 cases sampled every 3rd across the
corpus.

| population | n | right tool **and** arguments | **refusals** | narrations |
|---|---:|---:|---:|---:|
| **hand-written** (what a person says) | 24 | 22 — **92%** | **0** | **0** |
| machine-generated | 36 | 31 — 86% | **0** | **0** |

**So heretic's premise does not hold here, and it is now measured rather than
argued.** Its case is *over-refusal*, and this model declined **zero** of sixty -
and the same on the 24 cases whose wording a person actually typed. The tool
accuracy (92% hand-written) sits inside the 90% this file already records for
`select`, so the run is ordinary, not anomalous.

**And the seven misses are a different problem, which the split makes visible:**

    processes                     empty     process_cost({})
    chat-explain                  empty     called ask_user
    generated-git-inspect         empty     vcs_status({"path": "."})
    generated-login-history       empty     find_recently_modified({...})
    generated-ask-user            answer    no tool
    generated-convert-media       answer    no tool
    generated-move-or-copy-file   answer    no tool

Not one is a refusal. Four are the **wrong tool for the request** (`process_cost`
for "what is running", `vcs_status` for "which branch is this work on") and three
are no call at all. **This model is not over-cautious here; it is imprecise** -
so the effort belongs in `tool_select`'s descriptions and the corpus, not in model
choice. That is the opposite of what an over-refusal finding would have implied,
and it is the reason the measurement was worth running.

**And one more fact, which is why `classify_reply` has an `empty` state at all:**
**57 of 60 replies carried no text whatsoever.** The model emits
`reasoning_content` and either calls a tool or nothing, leaving `content` empty. A
refusal-marker check that scored silence as compliance would have reported this
model as perfectly compliant *and* as never having spoken — and `markdown` now
prints that number on its own line rather than inside the miss table, because a
row that did nothing wrong is not a miss.

Raw output: `test-env/eval-out/` convention — the JSON carries `reply` and `text`
per case, which is what made the split possible after the fact.

## The KV floor reproduced on a second, 50x larger model — and one claim got weaker (2026-10-12)

Last round set `KV_CACHE_FLOOR = "q5_0"` from **one small model** (SmolLM2-135M,
98 MiB) and said the number's limit was that it was one model. `Qwen3-8B-Q4_K_M`
sits in this repo's own `cache/data-home`, is 50x the size, and is the model the
`-t` thread measurement in this file already used. Same corpus, same method:

| type | SmolLM2-135M | Qwen3-8B |
|---|---|---|
| q8_0 | -0.04% | **+0.32%** |
| q5_0 | +0.11% | **+1.35%** |
| q4_0 | +6.08% | **+5.81%** |

**The ordering is the same on both and `q4_0` costs ~6% on both.** The floor did
not move - a demonstrable cliff reproduced across a 50x size range is what it was
set from, and now it is that rather than one box's accident.

**But one claim got weaker, and it is recorded as weaker.** The first measurement
concluded *"`q8_0` and `q5_0` are free"*. On the 8B, `q5_0` measures **+1.35%** -
not free. Only `q8_0` holds up on both (-0.04% and +0.32%). The floor stayed at
`q5_0` anyway, for a reason worth stating: 1.35% is a measurable cost, not a cliff,
and `q5_0` is sometimes the only step that makes a model fit at all - excluding it
could mean a model that would run does not. What changed is the wording, from
"free" to "a known cost, below the cliff".

`KV_CACHE_MEASUREMENTS` holds both models' figures, and a new test class pins the
two properties their union establishes: the ordering is non-decreasing in
compression from `q8_0` down, and `q4_0` is the cliff on both. One correction to
that test on the way: I first asserted strict monotonicity including `f16`, which
is wrong - `q8_0` measured **-0.04%** on the small model, i.e. *better* than f16,
which is the noise a compression cannot buy. The claim is non-decreasing from
`q8_0`, and that is what it asserts now.

`tools/mutations/kv_floor.py`: 5 mutations, 5 caught, 0 survivors, including a
new pair that pins the second model's figure - dropping its `q4_0` below the
cliff, and making a *lighter* compression cost more than a heavier one.

## A registry vocabulary prompt makes transcription worse, and the worst case is a perfect one (2026-10-12)

whisper.cpp's `--prompt` is the only lever for teaching a general model this
system's vocabulary, and it is the kind of thing that appears to work. Measured
against the real binary and the real model — `ggml-base-q5_1.bin` from
`shani-install-media/test-env/cache/whisper-models/`, ten sentences rendered by
`espeak-ng`, three prompt conditions over **the same audio**.

The corpus is deliberately **two populations**: sentences whose hard words come
from this system's own catalogue (`archive`, `screen reader`, `symbolic links`,
`btrfs`), and ordinary English with nothing to teach. A vocabulary prompt that
works should lift the first and leave the second alone; one that lifts both is
changing the decoding rather than recognising anything, and this reports the split
rather than an average that would hide it.

| prompt | domain WER | ordinary WER | all |
|---|---:|---:|---:|
| none | 31.4% | 26.5% | **29.0%** |
| `"Chronoa."` | 42.5% | 21.5% | 32.0% |
| registry vocabulary (77 words) | 36.8% | 37.2% | **37.0%** |

**Per sentence, the registry prompt helped 1, harmed 4, and tied 5** — and the two
largest harms are on a **domain** sentence and on a sentence it transcribed
*perfectly*:

| sentence | no prompt | 77-word prompt |
|---|---:|---:|
| Extract the **archive** into my downloads folder | 29% | **57%** |
| Remind me to buy milk at six | **0%** | **29%** |
| Lock the screen now | 25% | 50% |
| List the broken **symbolic links** in my home | 12% | 25% |
| Dismiss the **screen reader** for a moment | 29% | **14%** |

So it does work *as a dictionary* — "a green reader" → "a screen reader",
"in my poem" → "in my home" — and costs more than it gains, because **whisper
mirrors the prompt's shape as well as its words**. 77 period-separated fragments
produced `extract. archive in to my download folder.` on the sentence whose
vocabulary it was supposed to be helping. A prompt is a style transfer with a
dictionary attached.

**Do not ship this.** The repo's own rule is that a measurement which says no is
worth recording so nobody re-derives it, and this is the clearest instance in the
file: the idea is plausible, the implementation is trivial, and it is net-negative.

### What this does NOT establish, stated rather than implied

**A length sweep over the same content prefix was run and it is noise.** Domain
WER moved +2.5, −0.4, **−5.4**, −0.4, +5.4 across prompt lengths of 2, 6, 16, 40
and 77 words — not a curve, and not monotonic in either population. With five
sentences per cell a 5-point swing is under two words. **So no prompt length is
shown to help**, including the 16-word one that looked best on the domain
population. The honest summary is two conditions, not six.

The absolute numbers are also **not comparable to the voice measurements elsewhere
in this file**: this uses `espeak-ng` defaults, where the file records `+f3` at
0.39 and `+m3` at 0.27 on a different corpus. Only the *relative* comparison
holds — same audio, same voice, same model, one variable.

### Three API facts, each found by running the binary rather than remembering it

- **`-oj` writes `<input-filename>.json`, extension included** — `s00.wav.json`,
  not `s00.json`. `with_suffix(".json")` is the wrong call and looks right.
- **`transcription` is a list of segments**, each a dict with a `text`, not the
  string its key name suggests. Joined with a space; each carries a leading one.
- **`discover_skills()` returns `(schemas, handlers)`**, a two-tuple. Indexing the
  first element as a list of schemas works; treating the return as the list does
  not.

**Durability gap, recorded rather than papered over:** the experiment is a
throwaway script, and this repository's rule is that a one-off is lost. It needs
`shani-testbed/slot-tests/` with the corpus and the two conditions, since that
repo already holds the whisper model cache. Not built here — a slot run to verify
it is a different piece of work from the question, and the answer is already
negative enough to act on.

## Two models can't answer the size question, and here is what blocks the third (2026-10-12)

The KV floor is measured on SmolLM2-135M and Qwen3-8B, and `q5_0`'s cost rose
12x between them (0.11% → 1.35%). Two points cannot distinguish a size trend from
one model being an outlier, and the answer changes what the floor should be: a
smooth trend means `q5_0` costs more on the 30B MoE tier the ladder actually
exists for, and an outlier means the 8B is the odd one.

`Qwen3-1.7B-Q4_K_M.gguf` is present in this repo's own
`shani-install-media/test-env/cache/llm-models/` and would have settled it. It
cannot be read:

    -rw------- 1 root root  1107409472  Qwen3-1.7B-Q4_K_M.gguf

Root-owned, mode 0600, and this is an unprivileged dev box. All four
`llama-perplexity` runs failed with `Permission denied` - which is not a
measurement result and is not one I can work around without a privilege I should
not take. The same holds for `SmolVLM2-500M`, `mmproj-…` and `nomic-embed-text`.

The remaining readable models are `Qwen3-0.6B-Q8_0` (measuring now) and the two
already measured. `0.6B` sits between 135M and 8B but far closer to the bottom, so
it splits the difference only partly - it can show whether the cost is flat across
small models and jumps at the 8B, or climbs steadily. It cannot bound the 30B
figure.

**What would actually settle it:** the ladder exists to buy VRAM headroom on the
one MoE tier, and that tier is 17.3 GB - larger than anything measurable here. A
slot run with a 30B-class checkpoint is the only way to price `q5_0` where the
ladder is used, and that is recorded rather than approximated.

## Every protection on the sandbox's escape path belongs to somebody else (2026-10-12)

From `../harness-study/HARVEST-BWRAP.md`. bubblewrap's `SECURITY.md` is the
clearest division of responsibility in sandboxing anywhere in the tree:

> bubblewrap is not a complete, ready-made sandbox with a specific security
> policy. [...] the level of protection between the sandboxed processes and the
> host system is entirely determined by the arguments passed to bubblewrap.
> Whatever program constructs the command-line arguments [...] is responsible
> for defining its own security model.

Its worked example is **CVE-2017-5226** — a Flatpak app injected keystrokes into
the parent's terminal with `ioctl(TIOCSTI)` — and bubblewrap's position was that
this was a **Flatpak** vulnerability, not a bubblewrap one. The primitive
declines to speak for whoever builds its argv and points at them instead.

Applied to `sandbox/`, which is genuinely good — namespaces, Landlock, and a
29-syscall seccomp filter — the finding is about what is **not** in it.
`executor.py:643` passes `--dev-bind /dev /dev`, so **every** device node on the
host reaches every sandbox; `landlock.py:494` grants `/dev` and
`_LANDLOCK_ACCESS_FS_IOCTL_DEV`; and `seccomp.py` filters 29 syscalls and
**never mentions `ioctl`**. Landlock does not mediate DAC. So the safety is
entirely downstream, and none of it says so anywhere in the source.

**Measured, not reasoned:**

| escape | what actually stops it | whose decision |
|---|---|---|
| `ioctl(TIOCSTI)` → the user's terminal | `dev.tty.legacy_tiocsti = 0`; call returns **`EIO`**, bytes never reach the pty | the **kernel's** sysctl |
| raw block devices (`/dev/nvme0n1`) | `brw-rw---- root:disk`, this user is **not** in `disk`; `O_RDWR` → **`EACCES`** | the **admin's** group membership |
| `/dev/mapper/control` | `crw------- root:root` → `EACCES` | the **kernel's** device mode |
| `/dev/snd/controlC0` | nothing — and correctly so, a voice assistant needs audio | — |

Two ordinary pieces of desktop administration flip it, and neither is exotic:
`sysctl -w dev.tty.legacy_tiocsti=1`, and adding the user to **`disk`** —
routine on full-disk-encryption installs, which this ecosystem ships.

**Not taken: a seccomp rule blocking `TIOCSTI`.** The machinery is already in the
file (`seccomp.py` inspects `arg0` for the `ptrace`/`PTRACE_TRACEME` carve-out at
`:274`), so the rule is ~10 lines. Refused **on the measurement**: the call is
unreachable on any kernel ≥ 6.2 with the default sysctl, so a rule for it would be
*counted* as protection while changing nothing — which is the exact mistake this
entry exists to describe. It becomes worth adding the moment the property lapses,
and the new test is what notices.

**What was taken is the dependency itself, made tested and per-machine.**
`tests/test_sandbox_external_properties.py` (3) asserts each external property by
performing the real operation — a real `ioctl(TIOCSTI)` against a **freshly opened
pty of its own** (never the user's terminal; injected bytes would be typed into
whatever they have running), and a real `O_RDWR` on a real block device found by
globbing `nvme`/`sd`/`vd`/`dm`.

The third assertion is the one that keeps the file honest: **it asserts the
sandbox does *not* itself block `ioctl`.** Without it a reader could reasonably
assume the other two describe protections this repository enforces. If someone
later adds the rule from above, that test fails and says the dependency can be
retired — which is the moment it stops being a risk.

**Four controls, all run, all biting.** Pretending the kernel accepts `TIOCSTI`;
pointing the block-device check at an openable node; adding `ioctl` to a copy of
`seccomp.py`; and denying with **`ENXIO`** instead of `EACCES` — which is the one
that proves the check distinguishes *"no permission"* from *"there is no device"*,
a distinction two identical skips would have hidden.

**Two of my own bugs, both the unreachable-path shape this file records most:**

- **The block-device assertion originally `skip`ped when the property *held*.**
  The strongest of the three claims never asserted — and a machine with no block
  device at all skipped for the identical reason, so the two cases were
  indistinguishable in the output. Holding is a **pass**; absent is a skip; open
  is a fail.
- **It picked `/dev/acpi_thermal_rel`**, a 0600 *char* device, and skipped on
  that. Only block devices are the thing at issue, and the glob accepts any device
  node. A third bug was in the skip message itself: it quoted `patterns`, which is
  local to the helper — so the one machine the skip exists for raised `NameError`
  instead of skipping. All three found by running, the last two only by reading the
  path after the first was fixed.

## The "environmental" failure list is stale: all eleven of those files pass (2026-10-12)

The table above lists eleven files as failing because of this machine. Measured,
each file run on its own, 2026-10-12:

| file | recorded as | measured |
|---|---|---|
| `test_browser_window_probe.py` (7) | WebKitGTK absent | **7 passed, 2 skipped** *(already corrected above)* |
| `test_setup_extras.py` (6) | `ConsentRequired`, downloads gated | **27 passed** |
| `test_sense_idle.py` (3) | real clock and real X, headless | **20 passed, 3 skipped** |
| `test_sandbox_seccomp.py` (2) | Landlock/seccomp need the real kernel | **61 passed, 1 skipped** |
| `test_permission_decisions.py` (2) | `grim` missing | **19 passed** *(already corrected above)* |
| `test_everyday_skills.py` (2) | xdotool / X11 not present | **53 passed** |
| `test_sandbox_profiles_live.py` (1) | needs the real kernel | **37 passed** |
| `test_input_skills.py` (1) | xdotool / X11 not present | **23 passed** |
| `test_portal_input.py` (1) | no RemoteDesktop backend | **14 passed** |
| `test_vision_sense.py` (1) | no Wayland capture tool | **53 passed, 1 skipped** |
| `test_recordings.py` (1) | consent switch off | **19 passed** |

**Zero failures across all eleven.** The list is not partially stale; it is
wholly stale, and it is the second time this file has had to correct it.

### What replaced it is strictly better, and that is the part worth reading

Seven tests still skip, and **every one names a measured cause** rather than a
guess about the machine:

```
WebKit's web process cannot start here; no page can be loaded
the X server here does not offer MIT-SCREEN-SAVER
an LSM on this machine refuses to execute a file with no pathname
  (memfd_create + execve is killed before Python sees it)
a display environment is advertised here, but a real capture does not
  work: gnome-screenshot falls back and writes nothing
```

Those are exactly the shapes this file later records as *correct*: the guard asks
the component rather than the environment, and a check that **refuses to certify
itself when its premise is absent** is the right behaviour rather than a failure
to fix. So the old table was not describing a machine — it was describing a
version of these files that no longer exists.

**Corrected rather than deleted, deliberately.** These files can legitimately
fail on a machine that genuinely lacks X, a real capture tool, or a permissive
LSM, and the reasons are still the right ones to try first. What was wrong was
the claim that they fail *here*, asserted without re-measurement.

**The rule, which is the transferable half:** *a failure list nobody re-measures
is worse than no list*, because it teaches the next reader that eleven files do
not work — which is the "a stale **dead** label is worse than a missing **live**
one" shape in a new place. It is only corrected by measuring, and two entries
had already been corrected that way in this file, which is what made the other
nine worth re-checking. **`environmental` is a claim, not a verdict.**

## The third model moved the KV floor, and the two that set it were luck (2026-10-12)

Third and last measurement available on this box. `Qwen3-0.6B-Q8_0` is smaller
than the 8B and larger than the 135M, so it splits the size question:

| type | SmolLM2-135M | Qwen3-8B | Qwen3-0.6B |
|---|---|---|---|
| q8_0 | -0.04% | +0.32% | +0.40% |
| q5_0 | +0.11% | +1.35% | **+8.53%** |
| q4_0 | +6.08% | +5.81% | **+457%** |

`q5_0` costs **8.53%** on the 0.6B - worse than `q4_0` costs on either of the
other two. Both extremes reproduced exactly on a confirmation run, so it is not a
fluke. **The two-model evidence that put the floor at `q5_0` was luck.**

`KV_CACHE_FLOOR` is now `q8_0` - the only step cheap on all three - and the ladder
is `("f16", "q8_0")`. The rule it encodes is stronger than "the worst measured
step": **a cache type is usable only if it is cheap on every model the ladder
might be applied to.** That is PersonaPlex's `n_q_semantic` taken properly - the
semantic set is what the *worst* case allows, not what the average suggests.

`q4_0` at +457% on the 0.6B is worth its own line: that is not degradation, that
is the model breaking. A 4-bit KV cache at 0.6B parameters leaves too little
redundancy to absorb it.

**The survivor this round's mutation set found first, and why it mattered.** With
two models the suite was entirely green, including the tests that name the floor
- because with two, `q5_0` at 1.35% still reads as "a cost below the cliff" and
nothing errors. Deleting the third measurement changes no assertion. The fix is a
count: the floor rests on **three** models, and that is the assertion that catches
a deletion, because the failure mode is silent rather than loud.

Two old mutation entries were retired: their anchors moved when the floor did, and
a mutation whose anchor no longer exists is reported rather than silently
no-opped.

## The dead-code list conflated three states, and the instrument that made it a question is gone (2026-12)

The unwired list above warns about itself: *"note it still rots, and the scan
has to be re-run rather than believed."* The scan that produced it was an AST
pass run from a scratchpad, so it is gone, and what remained was a hand-kept
list — the failure this file records for `capabilities._GROUPS`, `GATED`,
`GATE_NAMES` and `test_skill_gates_are_enforced._IMPL`.

**Grepping the list proved it cannot be re-measured that way.** Each of these
reads as "wired" and is not:

| name | what a text search found |
|---|---|
| `price` | 51 hits, all the English word |
| `collapse` | 55 hits, mostly `border-collapse:collapse` in CSS |
| `describe_captured` | a **local variable** in `senses/vision.py:285` |
| `capability_for` | a string in `capabilities.py:40`, not a call |
| `within` | `tools/singing_demo.py` only, and `tools/` is **not packaged** |

### Three states, and the list used one word for all of them

Re-measured by `tools/deadcode.py` (new; AST, with string constants skipped so
prose is not a caller):

| | count |
|---|---:|
| public names in the shipped tree | **2,171** |
| referenced in the shipped tree | 2,000 |
| referenced by a test but not the shipped tree | 86 |
| referenced by nothing at all | **80** |

Of the **17** names the list carried: **6 are still dead**, and 11 are not — but
*not dead* is not *wired*, and the first comparison I ran printed "no longer
dead" for all eleven, which was itself the error. **Eight** are **test-referenced**
(proven to work, unreachable at runtime) and only **three** have a shipped-tree
caller. So **eleven of seventeen entries were wrong**, in the direction this file
already names as the dangerous one.

### The tool's own first run had three defects, and its own test caught the worst

- **Every dunder read as dead.** `__add__`, `__repr__`, `__call__`,
  `__post_init__` are invoked by the interpreter, never by name — a list of dead
  code that is a statement about Python. Public-names-only is the filter that
  removes them.
- **`usr/bin/` was scanned as zero files.** All ten launchers are **extensionless**
  (`shani-chronoa-lab-network`, no `.py`), and they are the privileged half:
  `netprovision.revalidate` and `read_envelope` are called by exactly one of them
  and by nothing else, which is the false positive this file already records from
  an earlier scan. **Found by `test_usr_bin_counts_as_a_caller_and_not_as_an_afterthought`**,
  which is the only reason it is not still broken; it moved the dead count from
  91 to 83.
- **Every `HTMLParser` callback read as dead** — `handle_data`, `handle_endtag`
  and `handle_startendtag` in `webtext.py`'s `_TextExtractor`. The class **is**
  live: it is constructed at `webtext.py:367` and fed a page, and `HTMLParser`
  calls its callbacks **by name**. Same false positive as a dunder, arrived at
  from a different direction, and it was found by checking the tool's dead list
  against names this file independently records — 83 to **80**.
- Two documented false-positive classes are allowlisted **with their reasons**:
  `apply_filesystem_allowlist` is called from the source string
  `get_landlock_wrapper()` returns, and `do_command_line` is a `GApplication`
  vfunc the bus dispatches by string — a scan that misses the second calls the
  app's own entry point dead, and this file records it as wired.

`pkg/` is excluded because it is a stale copy, not the canonical package, and
counting both doubles every number.

`tests/test_deadcode_tool.py` (7) pins the two that matter: a docstring naming a
function is not a caller, and a real call is. Both directions, because a scanner
that counts everything as wired is as useless as one that counts nothing.

### A scanner that cries wolf is worse than no scanner, so that is the test

`tools/deadcode.py`'s output is only worth reading if it does not report live
code as dead, and **three separate false-positive classes turned up in its first
two runs** — dunders, framework vfuncs, and `HTMLParser` callbacks. All three
are the same shape: something invokes the name without an identifier appearing
anywhere in our own source.

So the check is against **ground truth the scan was not derived from** — names
this file independently records as wired, written out rather than recomputed,
because a check generated from its own input is circular:

| name | what this file records |
|---|---|
| `analyze_table` | a skill the matrix-skills suite drives through dispatch |
| `merge_models` | a Merge models button on the Models panel |
| `list_streams` | wired as `recording action=streams`, 2026-10-11 |
| `Router` | distill's router, reached through `tools._HANDLER_FNS` |

**And the control that makes those four mean something:** `check_health` is
asserted to *still* be reported dead, which this file also records ("genuinely
uncalled, and consistently so"). An empty dead list satisfies every "is nothing
live being reported dead?" question, so without that assertion the four tests
above would pass on a scan that had stopped working entirely.

Verified: dropping `handle_data` from the framework allowlist turns the suite
red with *"is an html.parser callback and is not allowlisted, so every HTML
parser in this tree reports dead"*.
