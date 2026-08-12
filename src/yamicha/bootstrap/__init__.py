"""The sole composition route for a complete Yamicha configuration."""

from .composition import YamichaComposition, make_stage1_composition
from .stage2 import Stage2System, make_stage2_system
from .stage3 import Stage3System, make_stage3_system
from .stage4 import Stage4System, make_stage4_system
from .stage5 import Stage5System, make_stage5_system
from .stage6 import Stage6System, make_stage6_system
from .interactive_console import InteractiveConsole, run_interactive_console

__all__ = [
    "Stage2System",
    "Stage3System",
    "Stage4System",
    "Stage5System",
    "Stage6System",
    "InteractiveConsole",
    "YamichaComposition",
    "make_stage1_composition",
    "make_stage2_system",
    "make_stage3_system",
    "make_stage4_system",
    "make_stage5_system",
    "make_stage6_system",
    "run_interactive_console",
]
