"""The sole composition route for a complete Yamicha configuration."""

from .composition import YamichaComposition, make_stage1_composition
from .stage2 import Stage2System, make_stage2_system
from .stage3 import Stage3System, make_stage3_system
from .stage4 import Stage4System, make_stage4_system
from .stage5 import Stage5System, make_stage5_system
from .stage6 import Stage6System, make_stage6_system
from .stage7 import Stage7System, make_stage7_system
from .stage8 import Stage8System, make_stage8_system
from .stage9 import Stage9System, make_stage9_system
from .stage10 import Stage10System, make_stage10_system
from .interactive_console import InteractiveConsole, run_interactive_console

__all__ = [
    "Stage2System",
    "Stage3System",
    "Stage4System",
    "Stage5System",
    "Stage6System",
    "Stage7System",
    "Stage8System",
    "Stage9System",
    "Stage10System",
    "InteractiveConsole",
    "YamichaComposition",
    "make_stage1_composition",
    "make_stage2_system",
    "make_stage3_system",
    "make_stage4_system",
    "make_stage5_system",
    "make_stage6_system",
    "make_stage7_system",
    "make_stage8_system",
    "make_stage9_system",
    "make_stage10_system",
    "run_interactive_console",
]
