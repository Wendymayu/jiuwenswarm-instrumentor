from jiuwenswarm_instrumentor._version import __version__


def setup():
    """Explicit in-process activation hook. Call once at the earliest startup point."""
    from jiuwenswarm_instrumentor.activate import activate
    return activate()


__all__ = ["__version__", "setup"]
