# SPDX-FileCopyrightText: 2026 lbuqe for LILYGO
#
# SPDX-License-Identifier: MIT

class LpmError(Exception):
    """An expected, user-facing command failure."""


class CommandError(LpmError):
    def __init__(self, command: list[str], returncode: int) -> None:
        self.command = command
        self.returncode = returncode
        super().__init__(f"command failed with exit code {returncode}: {' '.join(command)}")
