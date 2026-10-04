"""Trigger -> actuator engine: a rule store that lets a percept fire a
whitelisted skill unattended, with per-rule opt-in consent.

A "trigger rule" is the ambient-scheduler's answer to the question "what
should happen when I perceive X?". It is deliberately *not* a way to let the
LLM write actions: the user arms a rule, and the rule names one already
whitelisted skill with fixed, user-supplied arguments. Nothing here resolves a
skill name at fire time, accepts a shell command, or takes a free-form prompt
- those are the three things this module exists to prevent, and they are the
design boundary that makes an unattended action safe to run at all.

The shape, in one sentence: **a percept matches a rule, the rule's producing
sense and target actuator both pass their own consent gates, and the actuator
runs through `tools.execute_tool` - the single dispatch point that already
records every call.**

What this module is NOT:

- It is not a sense. It emits no `Percept`, so it is not registered in
  `SENSES` and `tests/test_sense_manifest.py` has nothing to assert about it.
  It is infrastructure over the senses layer, in the same relationship
  `AmbientScheduler` holds to `Sense`.
- It does not author rules. `build_rule()` validates a dict a *user* (or a
  CLI) supplied; no path here accepts an LLM-authored rule, and the CLI only
  exposes `add`/`remove`/`list`/`clear`, never "let the model write one".
- It does not create an audit trail. Every actuation goes through
  `tools.execute_tool`, which records to the existing `ToolTracker` ring and
  `~/.local/share/shani-chronoa/logs/tool_calls.log`. The only addition is
  one field on that record - `origin` - so an unattended call is
  distinguishable from a user-initiated one.
"""

# The package's public API: every name the module had, from where it now lives.
from .common import (  # noqa: F401
    ANTI_NOISE_LAYERS,
    BACKOFF_BASE_SECONDS,
    BACKOFF_CAP_SECONDS,
    BACKOFF_MAX_ATTEMPT,
    BACKOFF_MAX_CONSECUTIVE_FAILURES,
    BACKOFF_MAX_RULE_CONSECUTIVE_FAILURES,
    BACKOFF_RESTART_LIMIT,
    BACKOFF_RESTART_WINDOW_SECONDS,
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_DEBOUNCE_SECONDS,
    EVENT_AUDIODEVICE,
    EVENT_BTCONNECT,
    EVENT_CALENDAR,
    EVENT_CONTAINERRUN,
    EVENT_DBUSPROP,
    EVENT_EXPIRY,
    EVENT_FAILURE,
    EVENT_FSWATCH,
    EVENT_GIT,
    EVENT_JOURNALMATCH,
    EVENT_NETSTATE,
    EVENT_PHONE,
    EVENT_SOUND,
    EVENT_POWERSTATE,
    EVENT_SCHEDULE,
    EVENT_SCREENLOCK,
    EVENT_SLEEPWAKE,
    EVENT_TYPES,
    EVENT_UNITHEALTH,
    EVENT_USBPLUG,
    Event,
    EventSignal,
    FAILURE_ACTUATOR_DID_NOT_RUN,
    FAILURE_ACTUATOR_RAISED,
    FAILURE_KINDS,
    FAILURE_NONE,
    FAILURE_VERIFICATION_FAILED,
    HEARTBEAT_BUCKET_SECONDS,
    LAYER_COOLDOWN,
    LAYER_DEDUPE_WINDOW,
    LAYER_DUPLICATE_EVENT,
    LAYER_FILTER_MISMATCH,
    MATCH_ANY,
    MATCH_KEYWORDS,
    MATCH_SUBSTRING,
    MAX_ARGUMENTS,
    MAX_ARGUMENT_VALUE_CHARS,
    MAX_CONTENT_CHARS,
    MAX_COOLDOWN_SECONDS,
    MAX_DEBOUNCE_SECONDS,
    MAX_KEYWORDS,
    MAX_KEYWORD_CHARS,
    MAX_RULES_PER_USER,
    MAX_RULE_NAME_CHARS,
    MIN_COOLDOWN_SECONDS,
    MIN_DEBOUNCE_SECONDS,
    RETRY_POLICIES,
    RETRY_RETRYABLE,
    RETRY_TERMINAL,
    triggers_dir,
    SIGNAL_OK,
    SIGNAL_UNAVAILABLE,
    SIGNAL_WATCH_ERROR,
    TRIGGER_CONTROL_KEY,
    UNIT_BACKING_OFF,
    UNIT_GAVE_UP,
    UNIT_HEALTH_STATES,
    UNIT_STARTING,
    UNIT_STOPPED,
    UNIT_WORKING,
    _DESTRUCTIVE_ACTUATORS,
    _NOTIFY_ONLY_ACTUATORS,
    _NOTIFY_ONLY_EVENT_TYPES,
    _ORIGIN,
    _SIGNAL_TIMEOUT_SECONDS,
    _TERMINAL_HEALTH_STATES,
    _UNATTENDED_WRITE_ACTUATORS,
    _VALID_EVENT_MATCH_MODES,
    _VALID_MATCH_MODES,
    _actuator_problem,
    _clamped_seconds,
    _data_home,
    _ensure_state_dir,
    _fingerprint,
    _jsonable,
    _restrict_file,
    _stored_arguments_problem,
    _unavailable,
    _watch_error,
)
from .sources import (  # noqa: F401
    CONTAINER_ABSENT,
    CONTAINER_EXITED,
    CONTAINER_RUNNING,
    CONTAINER_STALLED,
    deadlines_dir,
    EXPIRY_THRESHOLDS,
    MAX_PORCELAIN_CODES,
    MAX_WATCH_ENTRIES,
    PollingDirWatcher,
    verdicts_dir,
    WatcherError,
    _FAILURE_COMMANDS,
    _GIT_DETERMINISM,
    _SYSTEMD_ACTIVE,
    _SYSTEMD_SUB,
    _TERMINAL_CONTAINER_STATES,
    _VERDICTS,
    _VERDICT_FAILED,
    _VERDICT_PASSED,
    _confine_to_home,
    _container_runtime,
    _git,
    _run_argv,
    read_container_state,
    read_expiry,
    read_failure_verdict,
    read_git_state,
    read_unit_health,
    read_watched_path,
    record_deadline,
    record_verdict,
)
from .desktop_sources import (  # noqa: F401
    MAX_JOURNAL_PATTERN,
    POWER_SUPPLY_DIR,
    SCHEDULE_GRACE_MINUTES,
    SCREENLOCK_SOURCES,
    USB_DEVICES_DIR,
    _BUS_NAME,
    _DAYS,
    _OBJ_PATH,
    _PROP,
    _audio_nodes,
    _graphical_session,
    _nmcli_fields,
    _read_power,
    _source_parts,
    _state_signal,
    _suspended_seconds,
    _usb_devices,
    last_occurrence,
    parse_schedule,
    read_audiodevice,
    read_btconnect,
    read_calendar,
    read_dbusprop,
    read_journalmatch,
    read_netstate,
    read_phone,
    read_sound,
    read_powerstate,
    read_schedule,
    read_screenlock,
    read_sleepwake,
    read_usbplug,
)
from .rules import (  # noqa: F401
    FireResult,
    rules_file,
    RuleStore,
    RuleStoreError,
    TriggerEngine,
    TriggerRule,
    _INPUT_ACTUATORS,
    _validate_rule_fields,
    build_rule,
)
from .event_rules import (  # noqa: F401
    BackoffPolicy,
    event_rules_file,
    EventRule,
    EventRuleStore,
    build_event_rule,
)
from .events import (  # noqa: F401
    DedupeWindow,
    DurableFingerprints,
    EventEngine,
    EventEvaluation,
    fingerprints_file,
    HeartbeatBucket,
    _PendingRun,
    read_event_signal,
)
