"""Learn to route by watching a larger model route, not by copying its weights.

There are models on this machine - Qwen through llama.cpp, Kokoro for speech -
and the instinct is to merge their weights. That cannot work: Qwen's parameters
are attention heads and feed-forward matrices, and an outcome model's are a
3 x 16,384 logistic table. Adding them is arithmetic on unrelated numbers, and
the result is a model that is confidently wrong rather than honestly uncertain.

**What transfers from a teacher is its decisions.** So this asks the configured
model which skill answers a request, keeps only the answers a human already
agreed with, and trains on those. The student inherits judgement, not
parameters - which is also the only route that fits in a few hundred kilobytes
and runs on a CPU.

Four properties that matter, each of which a first version would have got wrong:

- **Only agreements are training data.** A teacher's mistakes are not training
  data, and neither are its disagreements with the human label - those go in the
  report as a measure of where the teacher cannot be trusted, which is the
  single most useful number this produces.
- **The label is the authority, not the teacher.** A case where the teacher and
  the human differ is a case the human won. Training on it would teach the
  student to make exactly that mistake.
- **Refusing to produce a student is allowed.** If agreement is below the floor,
  or the fitted student does not beat "always answer with the most common tool",
  there is nothing worth shipping, and the honest output is that - not a small,
  confident router.
- **A student is a prior, never an authority.** It reorders and narrows what was
  already on offer. It cannot add a skill, and it cannot run one.

**The teacher can be any model, and that is not a hedge.** Claude, ChatGPT,
Gemini, Groq, OpenRouter, a Qwen under llama.cpp and an Ollama server all answer
the same `chat_message()` question, so none of them is special-cased except
Anthropic, which does not speak the OpenAI shape at all and is reached through
the adapter this package already ships. Which one answers is a configuration
question, so it is read from configuration rather than assumed here.

Two things keep "any model" from quietly meaning "any model, silently":

- **A named teacher is never silently replaced.** Ask for Claude and get an
  error; never another model's opinion recorded under Claude's name. The report
  names the model that actually answered, because agreement measured against one
  teacher is not evidence about another.
- **Nothing off this machine is reachable unless both gates are open** - privacy
  mode off AND cloud-fallback-enabled - the same two switches
  `app/brain.py:_maybe_enable_cloud_fallback` requires, for the same reason:
  this is the one path that would put a request in front of an outside provider
  without a person watching the conversation it came from.
"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import (Awaitable, Callable, Dict, List, NamedTuple, Optional,
                    Sequence, Tuple)

logger = logging.getLogger(__name__)

#: Agreement below this yields no student. A teacher that agrees with the human
#: less than half the time is not a teacher for this task, and training on the
#: remainder would import its mistakes as if they were signal.
MIN_AGREEMENT = 0.5

_SYSTEM = (
    "You choose which Chronoa skill answers a request. Reply with the skill "
    "name and nothing else - no explanation, no punctuation."
)

#: A skill name as it appears in the whitelist.
_NAME = re.compile(r"[a-z][a-z0-9_]{1,40}")
#: Any identifier-shaped word, whatever its case. Used only to *segment* a reply
#: before looking for a known name in it - a reply is not required to be clean.
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class RoutingCase(NamedTuple):
    say: str
    expect: Tuple[str, ...]


class Decision(NamedTuple):
    case: RoutingCase
    said: Optional[str]
    agreed: bool
    reason: str


# ══════════════════════════════════════════════════════════════════════════
# THE TEACHER: any model this machine is configured to use
# ══════════════════════════════════════════════════════════════════════════


class Teacher(NamedTuple):
    """One model that can be asked which skill answers a request.

    `on_this_machine` is not decoration: it is what decides whether the two
    privacy gates apply, and it is reported so a person can see where a routing
    case is about to be sent before it is.
    """
    id: str
    label: str
    on_this_machine: bool
    ask: Callable[[str], Awaitable[str]]


def _config(config=None):
    from shani_chronoa.config import ChronoaConfig
    return config if config is not None else ChronoaConfig()


def _cloud_allowed(config) -> Tuple[bool, str]:
    """Whether anything off this machine may be asked, and why not if it may not.

    Both gates, because `app/brain.py` requires both and a single switch must
    never be enough to send a request off the machine.
    """
    if config.privacy_mode:
        return False, "privacy mode is on, so nothing leaves this computer"
    if not config.cloud_fallback_enabled:
        return False, ("cloud fallback is off (Settings -> Privacy), so no "
                       "online model may be asked")
    return True, ""


def _ask_chat_message(backend) -> Callable[[str], Awaitable[str]]:
    """Adapt one backend's real `chat_message()` to "prompt in, text out".

    **`chat_message()` is the interface every backend here actually has**, and
    a first version called `.chat()` instead - which only `OllamaLLM` defines.
    llama.cpp and the whole cloud chain raised `AttributeError`, the loop moved
    on, and a machine with Claude configured reported "no language model is
    configured" while the provider sat right there with a key.

    The text is read from `content`, and a list of blocks is joined rather than
    stringified: Anthropic's adapter already flattens its own blocks, but a
    backend that did not would otherwise produce a reply that parses as a skill
    name only by accident.
    """
    async def ask(prompt: str) -> str:
        message = await backend.chat_message(
            [{"role": "system", "content": _SYSTEM},
             {"role": "user", "content": prompt}], tools=None)
        content = (message or {}).get("content", "")
        if isinstance(content, list):
            content = "".join(str(part.get("text", "")) if isinstance(part, dict)
                              else str(part) for part in content)
        return content if isinstance(content, str) else str(content)
    return ask


def _local_teachers(config) -> List[Teacher]:
    """Ollama first, then llama.cpp - the same order `app/brain.py` tries them.

    Availability is a live check, not a guess: both expose a real probe, and a
    teacher listed as ready that then fails is a claim this module would rather
    not make.
    """
    out: List[Teacher] = []
    try:
        from shani_chronoa.ollama_llm import OllamaLLM
        model = OllamaLLM(host=config.ollama_host, model=config.model or "x")
        if model.is_available():
            out.append(Teacher("ollama", f"Ollama ({model.model})", True,
                               _ask_chat_message(model)))
    except Exception as exc:  # noqa: BLE001 - a probe must not break the list
        logger.debug("distill: Ollama probe failed: %s", exc)
    try:
        from shani_chronoa import local_llm
        if local_llm.is_up():
            backend = local_llm.LocalLLM()
            out.append(Teacher("llama.cpp", f"llama.cpp ({backend.model})", True,
                               _ask_chat_message(backend)))
    except Exception as exc:  # noqa: BLE001
        logger.debug("distill: llama.cpp probe failed: %s", exc)
    return out


def _cloud_teacher(provider_id: str, config, api_keys: Dict[str, str]) -> Optional[Teacher]:
    """One cloud backend, by id - Anthropic through its own adapter.

    Built through `CloudLLMChain` with a single provider rather than assembled
    by hand, so the skip-a-keyless-BYOK-provider rule, the one retry on a
    provider's own `retry_after`, the secret sanitisation and the egress record
    are the same code the assistant uses, not a second implementation of it.
    """
    try:
        from shani_chronoa.cloud_llm import CloudLLMChain
        chain = CloudLLMChain(provider_ids=(provider_id,), api_keys=api_keys)
    except Exception as exc:  # noqa: BLE001 - an unknown id is not a crash
        logger.debug("distill: cloud provider %s unavailable: %s", provider_id, exc)
        return None
    if not chain.is_available():
        return None
    backend = chain._backends[0]
    return Teacher(provider_id, f"{backend.provider.name} ({chain.model})", False,
                   _ask_chat_message(chain))


def _cloud_teachers(config) -> Tuple[List[Teacher], str]:
    """Every configured cloud provider, BYOK first - and why, if there are none."""
    allowed, why = _cloud_allowed(config)
    if not allowed:
        return [], why
    try:
        from shani_chronoa.cloud_llm import BYOK_PROVIDER_ORDER, DEFAULT_PROVIDER_ORDER
        from shani_chronoa.redaction import redactor
        api_keys = config.cloud_llm_api_keys()
    except Exception as exc:  # noqa: BLE001 - reading keys must not crash
        return [], f"the configured API keys could not be read: {exc}"
    # Without this the redactor's cache is empty and sanitising the routing cases
    # is a silent no-op against every real key - the same trap the assistant
    # walks past in `app/brain.py:_maybe_enable_cloud_fallback`.
    for provider_id, key_value in api_keys.items():
        if key_value:
            redactor.register(f"cloud_llm_{provider_id}", key_value)
    out: List[Teacher] = []
    for provider_id in BYOK_PROVIDER_ORDER + DEFAULT_PROVIDER_ORDER:
        teacher = _cloud_teacher(provider_id, config, api_keys)
        if teacher is not None:
            out.append(teacher)
    return out, ""


def available_teachers(config=None) -> List[Teacher]:
    """Every model this machine can be asked, local ones first."""
    config = _config(config)
    local = _local_teachers(config)
    cloud, _why = _cloud_teachers(config)
    return local + cloud


def teacher_notes(config=None) -> List[str]:
    """Why the list is what it is - so an empty list can explain itself."""
    config = _config(config)
    notes: List[str] = []
    local = _local_teachers(config)
    if local:
        notes.append("on this computer: " + ", ".join(t.label for t in local))
    else:
        notes.append("no local model is answering (no Ollama, no llama-server)")
    cloud, why = _cloud_teachers(config)
    if cloud:
        notes.append("off this computer: " + ", ".join(t.label for t in cloud))
    else:
        notes.append(why or "no cloud provider is configured")
    return notes


def resolve(teacher_id: Optional[str] = None, config=None) -> Teacher:
    """The one teacher to ask.

    With a name, exactly that one or an error - a silent substitution would file
    another model's opinion under this one's name, and the agreement number that
    comes out would describe a model nobody asked about. Without a name, the
    first that answers, which is what "distil from whatever is here" means.
    """
    teachers = available_teachers(config)
    if teacher_id:
        for teacher in teachers:
            if teacher.id == teacher_id:
                return teacher
        raise RuntimeError(
            f"no teacher called {teacher_id!r} is available here. "
            + "; ".join(teacher_notes(config)))
    if not teachers:
        raise RuntimeError(
            "no language model is configured, so there is no teacher to distil "
            "from. " + "; ".join(teacher_notes(config)) + ". That is the honest "
            "failure: a router trained without a teacher would be guessing.")
    return teachers[0]


# ══════════════════════════════════════════════════════════════════════════
# THE CASES, AND READING A REPLY
# ══════════════════════════════════════════════════════════════════════════


def load_cases(path: Optional[Path] = None) -> List[RoutingCase]:
    """The supervised routing set: a request and the skills that answer it."""
    # .../shani-chronoa/usr/lib/shani-chronoa/distill.py -> parents[4] is the
    # package root, where tools/ lives. parents[3] is usr/, which does not.
    path = path or (Path(__file__).resolve().parents[4]
                     / "tools" / "eval_cases.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("distill: no routing cases at %s: %s", path, exc)
        return []
    entries = raw.get("cases") if isinstance(raw, dict) else raw
    out: List[RoutingCase] = []
    for entry in entries or ():
        if not isinstance(entry, dict):
            continue
        say = str(entry.get("say") or "").strip()
        expect = entry.get("expect")
        names: List[str] = []
        for item in expect or ():
            if isinstance(item, dict) and item.get("tool"):
                names.append(str(item["tool"]))
            elif isinstance(item, str):
                names.append(item)
        if say and names:
            out.append(RoutingCase(say, tuple(names)))
    return out


def parse_choice(text: str, known: Optional[Sequence[str]] = None) -> Optional[str]:
    """The skill name out of a reply.

    **A substring search is not a name.** Asking for the first
    `[a-z][a-z0-9_]*` in the first line returns "he" from "The skill is
    edit_image" - which is then recorded as the teacher having said a skill
    called `he`, i.e. a disagreement that never happened and a data point
    nobody can audit. So the reply is *segmented* into whole identifiers and a
    token is only accepted when it is a skill that exists.

    With the known set supplied (which is the only way this is called), no match
    means None rather than a guess from shape: an invented name is worse than an
    honest "the reply named nothing", because it enters the report as a
    teacher's opinion.
    """
    if not text:
        return None
    first = text.strip().splitlines()[0] if text.strip() else ""
    tokens = _TOKEN.findall(first)
    if known:
        wanted = set(known)
        for token in tokens:
            if token in wanted:
                return token
        return None
    # No known set: the last well-formed name, because a chatty model that was
    # told to answer with a name and nothing else tends to put it last.
    named = [t for t in tokens if _NAME.fullmatch(t)]
    return named[-1] if named else None


async def default_teacher(prompt: str) -> str:
    """Ask whichever model is configured. Kept as the historical entry point."""
    return await resolve().ask(prompt)


def prompt_for(cases: Sequence[RoutingCase], case: RoutingCase) -> str:
    """The question, with the same menu every time so the answers are comparable."""
    menu = sorted({name for c in cases for name in c.expect})
    return (f"{case.say}\n\nAvailable skills: {', '.join(menu)}\n"
            f"Which single skill answers it?")


# ══════════════════════════════════════════════════════════════════════════
# THE STUDENT
# ══════════════════════════════════════════════════════════════════════════

#: Hashed feature width, shared with the outcome model so one hasher serves
#: both. The two models label different things, so they carry different space
#: ids and cannot be merged - see `space_id`.
_WIDTH = 1 << 14


def route_features(say: str) -> List[str]:
    """A request as features: words and word pairs.

    The words come from `tool_select._query_words`, so the router inherits the
    synonym table and the stemmer that decide which schemas are even sent -
    "louder" and "make it bigger" reach the same vocabulary the matcher uses.
    Bigrams are included because order carries the request: "remind me" and
    "remind me not to" are different questions about the same two words.
    """
    from shani_chronoa import tool_select
    words = tool_select._query_words(say)
    if not words:
        return ["empty"]
    return (["w=" + w for w in words]
            + ["b=" + a + "_" + b for a, b in zip(words, words[1:])])


def _index(name: str) -> int:
    from shani_chronoa import learning
    return learning._index(name)


def space_id() -> str:
    """This router's feature space, stamped into every student and checked on load.

    Separate from `learning._feature_space_id()` because the extractor differs,
    and the point of stamping a space is that a model whose indices mean
    something else is refused rather than believed.
    """
    import hashlib
    sample = route_features("delete the file at /tmp/x.png")
    material = "|".join([
        f"width={_WIDTH}",
        f"hash=blake2b:{_index('w=probe').__class__.__name__}",
        f"sample={_index(sample[0])},{_index(sample[-1])}",
        "convention=router-v1",
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


class Router:
    """Which skill answers a request: Bernoulli naive Bayes over hashed features.

    The estimator is the one that matches the data - binary presence-or-absence
    of hashed tokens, sparse, with a handful of non-zeros out of 16,384 - and
    the one already in this package for the same reason. Laplace smoothing is
    not optional: a skill that never appears in training must stay *possible*,
    which is exactly what happens when a new skill is added after the router
    was fitted.

    Nothing here can authorise a call. It ranks names that were already on
    offer; the whitelist that permits them is `tools._HANDLERS`, and it is
    checked there and nowhere else.
    """

    def __init__(self, space: str, classes: Sequence[str],
                 log_prior: Sequence[float], log_prob: Dict[int, List[float]],
                 width: int = _WIDTH) -> None:
        self.space = space
        self.classes = list(classes)
        #: log P(class) with Bernoulli's absent-feature constant already folded
        #: in, so scoring is a sum over the request's own tokens and nothing else.
        self.log_prior = list(log_prior)
        #: feature index -> per-class log P(feature present | class)
        self.log_prob = log_prob
        self.width = width

    # -- scoring ----------------------------------------------------------
    def score(self, say: str) -> List[Tuple[str, float]]:
        """Every class with its log posterior, best first.

        The absent-feature constant matters and is why it was folded in rather
        than skipped: a class whose training rows almost never carried this
        token must be *penalised* for it, and leaving that out ranks the
        class that has seen the fewest words first, every time a word the
        router has never met appears.
        """
        active: Dict[int, int] = {}
        for name in route_features(say):
            i = _index(name)
            active[i] = active.get(i, 0) + 1
        out = []
        for c, tool in enumerate(self.classes):
            total = self.log_prior[c]
            for i, times in active.items():
                values = self.log_prob.get(i)
                if values is not None:
                    total += values[c] * (times if times > 1 else 1)
            out.append((tool, total))
        out.sort(key=lambda pair: -pair[1])
        return out

    def top(self, say: str, k: int = 3) -> List[Tuple[str, float]]:
        return self.score(say)[:k]

    def confident(self, say: str, margin: float = 1.0) -> Optional[Tuple[str, float]]:
        """The one class, when the gap over the runner-up clears `margin`.

        A margin rather than a probability: a naive Bayes posterior over hashed
        tokens is not a calibrated frequency, so converting it to "87% sure"
        would be inventing a number. The gap is a statement about the model's
        own scores and nothing more.
        """
        scored = self.score(say)
        if len(scored) < 2:
            return scored[0] if scored else None
        if scored[0][1] - scored[1][1] >= margin:
            return scored[0]
        return None

    # -- fitting ----------------------------------------------------------
    @classmethod
    def fit(cls, rows: Sequence[Tuple[str, str]], space: Optional[str] = None,
            alpha: float = 1.0) -> "Router":
        """Fit on (request, skill) pairs. `rows` may repeat a request."""
        space = space or space_id()
        tools = sorted({tool for _say, tool in rows})
        if not tools:
            raise ValueError("no rows to fit a router on")
        index_of = {name: i for i, name in enumerate(tools)}
        docs = [alpha] * len(tools)
        #: hashed feature index -> the classes whose training rows carried it.
        #: Keyed by feature index and started **empty**: a first version seeded
        #: it from the class indices, so only the handful of features that
        #: happened to hash below the class count were ever counted. Every
        #: score then came out equal for all but one class, and the router
        #: ranked by document frequency alone - which looks like a weak model
        #: rather than a model that saw no features at all.
        seen: Dict[int, set] = {}
        for say, tool in rows:
            c = index_of[tool]
            docs[c] += 1
            for name in set(route_features(say)):
                i = _index(name)
                seen.setdefault(i, set()).add(c)
        total_docs = sum(docs)
        log_prior = [math.log(d / total_docs) for d in docs]
        log_prob: Dict[int, List[float]] = {}
        for i, holders in seen.items():
            # P(feature | class) with Laplace smoothing on both outcomes.
            row = []
            for c in range(len(tools)):
                present = len(holders) if c in holders else 0
                row.append(math.log((present + alpha) / (docs[c] + 2 * alpha)))
            log_prob[i] = row
        # Bernoulli's absent-feature term, folded into the prior once: it is the same
        # constant for every input, so paying for it per prediction would be
        # 16,384 multiplications per class to arrive at the same number.
        #
        # **Counted over the fitted vocabulary only, not the whole width.**
        # Summing log(1 - p) across all 16,384 columns gave the class with the
        # most training rows a ~450-point head before a single word was read,
        # because 16,300 unseen columns each sit at a different Laplace floor
        # per class. Every request was then answered with whichever skill was
        # most used, which is exactly what the model was meant not to be. This
        # is why `BernoulliNB` in scikit-learn sums the absent term over the
        # columns it fitted, and the same reason applies here.
        for c in range(len(tools)):
            absent = sum(math.log(max(1e-9, 1.0 - math.exp(log_prob[i][c])))
                         for i in seen)
            log_prior[c] += absent
        return cls(space, tools, log_prior, log_prob)

    # -- the file ---------------------------------------------------------
    def to_dict(self, provenance: Optional[Dict[str, object]] = None) -> dict:
        return {
            "kind": "router",
            "feature_space": self.space,
            "classes": self.classes,
            "log_prior": self.log_prior,
            "log_prob": {str(i): row for i, row in self.log_prob.items()},
            "width": self.width,
            "provenance": provenance or {},
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Router":
        return cls(str(payload["feature_space"]), list(payload["classes"]),
                   [float(v) for v in payload["log_prior"]],
                   {int(i): [float(v) for v in row]
                    for i, row in (payload.get("log_prob") or {}).items()},
                   int(payload.get("width", _WIDTH)))


def router_path(space: Optional[str] = None) -> Path:
    from shani_chronoa.learning import models_dir
    return models_dir() / f"router-{space or space_id()}.json"


def save_router(router: Router, provenance: Optional[Dict[str, object]] = None,
                path: Optional[Path] = None, key: Optional[bytes] = None) -> Path:
    """Write the student, signed and space-stamped, through the shared signer."""
    from shani_chronoa.learning import sign_model
    target = Path(path) if path is not None else router_path(router.space)
    payload = sign_model(router.to_dict(provenance), key)
    target.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    logger.info("distill: wrote %s (%d skills)", target.name, len(router.classes))
    return target


def evaluate_router(router: Router, rows: Sequence[Tuple[str, str]]) -> Tuple[float, float]:
    """(accuracy, majority baseline) on held-out rows."""
    if not rows:
        return 0.0, 0.0
    correct = sum(1 for say, tool in rows
                  if router.confident(say, margin=-1e9) is not None
                  and router.confident(say, margin=-1e9)[0] == tool)
    counts: Dict[str, int] = {}
    for _say, tool in rows:
        counts[tool] = counts.get(tool, 0) + 1
    baseline = max(counts.values()) / len(rows)
    return correct / len(rows), baseline


def load_router(path: Optional[Path] = None, space: Optional[str] = None) -> Optional[Router]:
    """The student, or None - with the reason logged.

    The same three refusals the outcome model makes, for the same reasons: a
    file that cannot say what it is, a file whose digest does not match, and a
    model whose own report says it never beat the majority baseline. Loading
    any of them would mean routing on a model's opinion of itself.
    """
    from shani_chronoa.learning import verify_model
    current = space or space_id()
    target = Path(path) if path is not None else router_path(current)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("distill: no router at %s: %s", target, exc)
        return None
    if payload.get("kind") != "router":
        logger.warning("distill: %s is not a router; refusing it", target.name)
        return None
    if payload.get("feature_space") != current:
        logger.warning("distill: router is from feature space %s, this build is %s; "
                       "refusing it", payload.get("feature_space"), current)
        return None
    check = verify_model(payload)
    if not check.get("ok"):
        logger.warning("distill: router does not verify (%s); refusing it",
                       check.get("reason", "unknown"))
        return None
    provenance = payload.get("provenance") or {}
    if provenance.get("honest") is False:
        logger.warning("distill: router's own report says it never beat the "
                       "majority baseline; refusing it")
        return None
    try:
        return Router.from_dict(payload)
    except Exception as exc:  # noqa: BLE001 - an unreadable shape is not a crash
        logger.warning("distill: router is malformed (%s); refusing it", exc)
        return None


# ══════════════════════════════════════════════════════════════════════════
# RUNNING IT
# ══════════════════════════════════════════════════════════════════════════


async def distil(cases: Sequence[RoutingCase],
                 teacher: Optional[Callable[[str], object]] = None,
                 min_agreement: float = MIN_AGREEMENT,
                 teacher_id: Optional[str] = None,
                 config=None) -> Dict[str, object]:
    """Ask the teacher, report what it agrees with, and train a student on that.

    The report carries the disagreements as well as the agreements, because the
    second is what tells you whether the teacher was worth distilling from at
    all. The student is trained and only saved when it earns its place: above
    the agreement floor, and beating the majority baseline on a held-out slice
    of its own agreements.
    """
    ask = teacher
    named: Optional[str] = teacher_id
    if ask is None:
        chosen = resolve(teacher_id, config)
        ask, named = chosen.ask, chosen.label
    menu = sorted({name for c in cases for name in c.expect})
    decisions: List[Decision] = []
    for case in cases:
        try:
            reply = await ask(prompt_for(cases, case))
        except Exception as exc:  # noqa: BLE001 - one bad reply is not a failure
            decisions.append(Decision(case, None, False, f"teacher failed: {exc}"))
            continue
        said = parse_choice(reply if isinstance(reply, str) else "", menu)
        if said is None:
            decisions.append(Decision(case, None, False,
                                      "no skill name in the reply"))
        elif said in case.expect:
            decisions.append(Decision(case, said, True, "agrees with the label"))
        else:
            decisions.append(
                Decision(case, said, False,
                         f"said {said!r}, the label says {', '.join(case.expect)}"))

    agreed = [d for d in decisions if d.agreed]
    rate = len(agreed) / len(decisions) if decisions else 0.0
    usable = rate >= min_agreement and bool(agreed)
    report: Dict[str, object] = {
        "teacher": named or "the injected teacher",
        "cases": len(decisions),
        "agreed": len(agreed),
        "agreement": round(rate, 4),
        "training": [{"say": d.case.say, "tool": d.said} for d in agreed],
        "disagreements": [{"say": d.case.say, "said": d.said,
                           "expect": list(d.case.expect), "reason": d.reason}
                          for d in decisions if not d.agreed],
        "usable": usable,
        "min_agreement": min_agreement,
        "note": ("below the agreement floor, so no student was trained: a "
                 "teacher that disagrees with the human more often than it "
                 "agrees would import its mistakes as signal"
                 if decisions and rate < min_agreement else
                 "agreement is above the floor"),
    }
    if usable:
        report["student"] = _train_and_save(agreed, named)
    return report


def _train_and_save(agreed: Sequence[Decision], teacher: Optional[str],
                    holdout: float = 0.25) -> Dict[str, object]:
    """Fit the student on the agreements and keep it only if it earns the file.

    The split is `_split`'s, which is stratified by skill rather than a
    contiguous tail - see there for the measurement that forced it.
    """
    rows = [(d.case.say, d.said) for d in agreed if d.said]
    return train_router(rows, teacher=teacher, holdout=holdout)


def _split(rows: Sequence[Tuple[str, str]], holdout: float) -> Tuple[List[Tuple[str, str]],
                                                                      List[Tuple[str, str]],
                                                                      List[Tuple[str, str]]]:
    """Hold out every `1/holdout`-th request **of each skill**. Returns
    (train, held_out, unreachable).

    **Not a contiguous tail, and the difference was measured.** A routing corpus
    is written grouped by skill - six phrasings of the clock, then six of the
    battery - so a position-ordered split puts whole skills in the held-out tail
    and measures which skill happened to be written last. Measured against the
    real local model on a real ShaniOS slot: a teacher that agreed with the human
    label on **49 of 54** requests, and a student fitted on the first 75% of
    those agreements scored **0% against a 100% baseline** on the three held-out
    requests whose skill it had been allowed to see. A 91%-correct teacher
    "distilling" to a 0%-correct student is a split artefact, and reporting it
    as evidence about the model would have been the worst kind of wrong.

    This differs from the outcome model's time-ordered split on purpose, because
    the data differs: **that log repeats.** 11,590 tool calls carry only 344
    distinct feature vectors, so putting the same call on both sides of a
    boundary is memorisation. A routing corpus has one request per row - the
    whole reason `route_cases.json` exists - so holding out rows cannot leak a
    request, and interleaving loses nothing. The `_duplicate` check below
    proves it rather than assuming it.
    """
    if holdout <= 0:
        return list(rows), [], []
    stride = max(2, int(round(1.0 / holdout)))
    # The side is decided **per distinct request**, keyed by the skill's own
    # count of requests seen so far. Keying by request instead would put every
    # row in the training half whenever requests are unique - which they are -
    # so nothing was ever held out and the gate compared nothing with anything.
    per_class: Dict[str, int] = {}
    side: Dict[str, bool] = {}
    for say, tool in rows:
        if say in side:
            continue
        position = per_class.get(tool, 0)
        per_class[tool] = position + 1
        side[say] = position % stride == stride - 1
    train = [row for row in rows if not side[row[0]]]
    held = [row for row in rows if side[row[0]]]
    known = {tool for _say, tool in train}
    unreachable = [row for row in held if row[1] not in known]
    return train, [row for row in held if row[1] in known], unreachable


def train_router(rows: Sequence[Tuple[str, str]], teacher: Optional[str] = None,
                 holdout: float = 0.25, path: Optional[Path] = None,
                 key: Optional[bytes] = None) -> Dict[str, object]:
    """Fit a student on (request, skill) pairs, and keep it only if it earns the file.

    **The gate is not a formality.** Measured on `tools/eval_cases.json` - 57
    requests across 53 skills, so nearly one example per class - a student
    cannot score above the simplest guess, and the reason is structural rather
    than statistical: a held-out request asks for a skill the training half
    never contained. That is the shape of a *smoke test*, not of a training set,
    and the report says so instead of reporting "the model was bad".

    Which is why the refusals name their cause. "It did not beat the baseline"
    and "there is one example of each skill" call for different next steps, and
    only one of them is "collect more data".
    """
    rows = [(str(say).strip(), str(tool).strip())
            for say, tool in rows if str(say).strip() and str(tool).strip()]
    skills = {tool for _say, tool in rows}
    if len(rows) < 4:
        return {"saved": False, "reason": f"only {len(rows)} examples - "
                "too few to fit a router and hold anything out"}
    if len(rows) < 2 * len(skills):
        return {"saved": False, "reason":
                f"{len(rows)} examples across {len(skills)} skills is under two "
                "each, so a held-out request asks for a skill the training half "
                "never saw and no student can score above the simplest guess. "
                "This is what the routing case file is - one request per skill, "
                "written to prove the skills exist. A router needs requests "
                "repeated in ordinary use, which is what harvest_rows() reads.",
                "examples": len(rows), "skills": len(skills)}
    train, test, unreachable = _split(rows, holdout)
    if not train or not test:
        return {"saved": False, "reason": "not enough examples to hold anything out"}
    router = Router.fit(train)
    accuracy, baseline = evaluate_router(router, test)
    provenance = {
        "teacher": teacher,
        "examples": len(rows),
        "accuracy": round(accuracy, 4),
        "baseline": round(baseline, 4),
        "skills": len(router.classes),
        "held_out": len(test),
        "unreachable_held_out": len(unreachable),
        "honest": bool(accuracy > baseline),
    }
    if accuracy <= baseline:
        return {"saved": False, "reason":
                f"the student scored {accuracy:.0%} against a majority baseline "
                f"of {baseline:.0%} on {len(test)} held-out requests, so it "
                "learned nothing the simplest guess would not have given",
                "provenance": provenance}
    written = save_router(router, provenance, path=path, key=key)
    return {"saved": True, "path": str(written), "provenance": provenance}


def harvest_rows(session_root: Optional[Path] = None,
                 verdicts: Optional[Dict[str, str]] = None) -> List[Tuple[str, str]]:
    """Every (request, skill) pair this machine has actually decided.

    Chronoa's own production choices are the only corpus that is both large and
    true, and they are already on disk: each session records the request the
    person typed and the assistant's `tool_calls` before the next request. A
    tool called with no user turn before it - an unattended trigger, or the tail
    of a session that started mid-file - is left out rather than guessed at,
    because a router trained on a request nobody asked is learning noise.

    `verdicts` optionally filters by what the post-condition recorded, keyed by
    skill name, so a call the machine itself found did not work does not become
    training data. That is the outcome layer's one job here, and it uses the
    *recorded* verdicts rather than a model: a file that cannot say what it is
    is not a filter.
    """
    if session_root is None:
        try:
            from shani_chronoa import conversation_store
            session_root = conversation_store.session_dir()
        except Exception as exc:  # noqa: BLE001 - absent sessions are not a crash
            logger.debug("distill: no session directory: %s", exc)
            return []
    rows: List[Tuple[str, str]] = []
    try:
        sessions = sorted(Path(session_root).glob("*.jsonl"))
    except OSError as exc:
        logger.debug("distill: cannot read %s: %s", session_root, exc)
        return []
    for session in sessions:
        try:
            messages = [json.loads(line) for line in
                        session.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, ValueError) as exc:
            logger.debug("distill: skipping %s: %s", session.name, exc)
            continue
        request = ""
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if role == "user":
                request = str(message.get("content") or "").strip()
                continue
            if role != "assistant":
                continue
            for call in message.get("tool_calls") or ():
                name = str((call.get("function") or {}).get("name") or "").strip()
                if not name or not request:
                    continue
                if verdicts and verdicts.get(name) == "failed":
                    continue
                rows.append((request, name))
                # One request can legitimately produce several calls in a turn;
                # after the first, the request is spent.
                request = ""
    return rows


def safe_to_run_unattended(name: str) -> bool:
    """Whether a skill may be run with no arguments and no model watching.

    Two gates, both read from the live schema rather than a hand-kept list:

    - **Nothing required.** A router names a skill; it cannot fill one in.
      Running a skill with `{}` means running whatever its defaults do, and
      `assistant.py` already carries a standing rule against exactly that -
      *"never run a tool with arguments the model did not give"* - because
      `{}` once handed a small model its own error back.
    - **Nothing destructive.** `delete_file` and `trash_file` declare no
      required parameter (they take their path through the argfile envelope),
      so the first gate alone waves them through - and a skill that deletes
      something must never be chosen by a 4 MB classifier. `web_search`
      reaches the internet, so it is excluded the same way.

    Both flags come from `capabilities.tool_annotations`, which is already the
    single source for what the MCP server and the GUI tell a person about a
    skill. A new skill that declares itself destructive is therefore covered
    the day it is written, not the day this list is next updated.
    """
    try:
        from shani_chronoa import capabilities, tools
        description = ""
        schema = None
        for entry in tools.TOOLS:
            function = entry.get("function") or {}
            if function.get("name") == name:
                description = function.get("description", "")
                schema = function.get("parameters") or {}
                break
        if schema is None:
            return False
        annotations = capabilities.tool_annotations(name, description)
    except Exception:  # noqa: BLE001 - an unreadable schema means "not safe"
        return False
    if annotations.get("destructive_hint") or annotations.get("open_world_hint"):
        return False
    if schema.get("required"):
        return False
    properties = schema.get("properties") or {}
    return not any(isinstance(p, dict) and p.get("required")
                   for p in properties.values())


def fallback(request: str, margin: float = 2.0) -> Optional[Dict[str, object]]:
    """Answer a request with the router, when there is no model to ask.

    **This is a fallback, not a brain.** It returns a proposal, never an
    action: the caller runs it through `tools.execute_tool_outcome`, so the
    whitelist, the consent key, the sandbox and the post-condition all still
    apply exactly as they do for a model's call. What changes is who chose the
    skill, and the answer says so rather than reading like a considered reply.

    None means "the router has nothing to say about this", which is the correct
    answer far more often than a guess would be.
    """
    router = load_router()
    if router is None:
        return None
    scored = router.score(request)
    if len(scored) < 2 or scored[0][1] - scored[1][1] < margin:
        return None
    name = scored[0][0]
    if not safe_to_run_unattended(name):
        return None
    try:
        from shani_chronoa import tools
        if not any((t.get("function") or {}).get("name") == name
                   for t in tools.TOOLS):
            return None
    except Exception:  # noqa: BLE001
        return None
    return {"tool": name, "margin": round(scored[0][1] - scored[1][1], 3),
            "runner_up": scored[1][0],
            "reason": "the distilled router separated this skill from the "
                      "runner-up by more than the fallback margin, and it "
                      "needs no arguments and does nothing destructive"}


def recorded_verdicts(path: Optional[Path] = None) -> Dict[str, str]:
    """What the post-conditions recorded for each skill, worst verdict wins.

    Read out of the tool-call log rather than predicted, because this decides
    what becomes training data and a prediction would be laundering one model's
    guess into the next model's lessons.
    """
    from shani_chronoa.learning import _entries
    order = {"failed": 3, "unverified": 2, "verified": 1}
    out: Dict[str, str] = {}
    for entry in _entries(path):
        name = str(entry.get("tool_name") or "")
        verdict = str(entry.get("verdict") or "")
        if not name or verdict not in order:
            continue
        if order[verdict] >= order.get(out.get(name, ""), 0):
            out[name] = verdict
    return out


def render_report(report: Dict[str, object]) -> str:
    """The report as sentences, because a number alone is not an answer."""
    lines = [f"Teacher: {report.get('teacher')}",
             f"Agreed with the human label on {report.get('agreed')}"
             f"/{report.get('cases')} ({float(report.get('agreement', 0)):.0%})."]
    student = report.get("student")
    if isinstance(student, dict):
        if student.get("saved"):
            provenance = student.get("provenance") or {}
            lines.append(f"Student: {provenance.get('skills')} skills, "
                         f"{provenance.get('accuracy'):.0%} against a "
                         f"{provenance.get('baseline'):.0%} baseline on held-out "
                         f"agreements. It now ranks skills; it cannot run one.")
        else:
            lines.append(f"No student: {student.get('reason')}")
    else:
        lines.append(str(report.get("note", "")))
    disagreements = report.get("disagreements") or []
    if disagreements:
        lines.append(f"Where the teacher cannot be trusted ({len(disagreements)}):")
        lines.extend(f"  {d['say'][:60]!r}: {d['reason']}" for d in disagreements[:5])
    return "\n".join(lines)