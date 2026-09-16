"""Shared type aliases for resolver modules."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import AnyMessage

ResolutionResult = Tuple[
    Optional[Dict[str, Any]],
    List[str],
    List[AnyMessage],
    Optional[Dict[str, Any]],
]