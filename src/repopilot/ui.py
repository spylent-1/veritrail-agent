"""Terminal-only adapters. Core runtime knows nothing about Rich or stdin."""

import asyncio
import sys

from rich.console import Console
from rich.prompt import Confirm

from repopilot.safety import PreparedAction
from repopilot.sanitize import clean


class TerminalApproval:
    def __init__(self, console: Console) -> None:
        self.console = console

    async def confirm(self, action: PreparedAction) -> bool:
        if not sys.stdin.isatty():
            return False
        self.console.print(clean(action.preview))
        self.console.print(f"Action: {action.risk} / {action.fingerprint()[:12]}")
        try:
            return Confirm.ask("Approve this exact operation?", default=False, console=self.console)
        except (EOFError, KeyboardInterrupt):
            raise asyncio.CancelledError from None
