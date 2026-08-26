"""Minimal exec entry point for quality bootstrap workers.

This module deliberately imports only the standard library until inherited file
descriptors have been closed.  The parent submits work by path and maps only
stdin/stdout/stderr, so a worker never needs to retain descriptor 3 or above.
"""

from __future__ import annotations

import errno
import os
import resource
import sys


def _close_inherited_file_descriptors() -> None:
    """Close the post-exec descriptor snapshot without changing parent flags."""

    descriptors = None
    for descriptor_directory in ("/dev/fd", "/proc/self/fd"):
        try:
            descriptors = tuple(
                sorted(
                    int(name)
                    for name in os.listdir(descriptor_directory)
                    if name.isascii() and name.isdigit() and int(name) > 2
                )
            )
            break
        except OSError:
            continue
    if descriptors is None:
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft_limit == resource.RLIM_INFINITY:
            soft_limit = 1_048_576
        os.closerange(3, max(3, int(soft_limit)))
        return

    # PEP 446 makes newly-created Python descriptors non-inheritable by default,
    # but explicitly inheritable and legacy descriptors can cross exec.  After
    # exec this process is single-threaded.  A descriptor-directory entry can
    # still be the enumeration descriptor itself, already closed by listdir;
    # EBADF is the only benign race.  Other failures are fatal before imports.
    for descriptor in descriptors:
        try:
            os.close(descriptor)
        except OSError as error:
            if error.errno != errno.EBADF:
                raise


if __name__ == "__main__":
    _close_inherited_file_descriptors()
    from equipment_quality.quality_intervals import _module_main

    raise SystemExit(_module_main(sys.argv[1:]))
