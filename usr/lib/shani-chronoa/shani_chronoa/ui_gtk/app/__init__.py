"""Main application module for Shani Chronoa.

Orchestrates the STT, LLM, TTS, GUI, and MCP components.
"""

# The package's public API - every name the module had, from where it now lives.
from .voice import (  # noqa: F401
    _is_echo,
)
from .common import (  # noqa: F401
    _set_log_level,
)
from .application import (  # noqa: F401
    ChronoaApplication,
    launch_paths,
    main,
)
from .voice import VoiceMixin  # noqa: F401
from .brain import BrainMixin  # noqa: F401
from .conversation import ConversationMixin  # noqa: F401
from .desktop_integration import DesktopIntegrationMixin  # noqa: F401
from shani_chronoa import stt_provision  # noqa: F401
from shani_chronoa import conversation_store  # noqa: F401
