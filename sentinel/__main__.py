"""Entry point for `python -m sentinel`."""

import asyncio

from sentinel.engine import run

asyncio.run(run())
