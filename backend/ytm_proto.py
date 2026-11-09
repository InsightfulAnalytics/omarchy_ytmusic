"""Protocol pieces shared by the worker (ytm.py) and its area modules.

Area modules (backend/ytm_<area>.py) raise ``WorkerError`` and read their
parameters with these helpers. They must never ``import ytm``: the worker
runs as ``__main__``, so that import would load a second copy of it with its
own ``WorkerError`` class.
"""

CURSOR_MAX = 4096


class WorkerError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def as_dict(self):
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


def req_str(params, key):
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise WorkerError("BAD_REQUEST", f"missing or empty '{key}'")
    return value.strip()


def opt_str(params, key, high=4096):
    """An optional non-empty string (``None`` when absent or empty)."""
    value = params.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > high:
        raise WorkerError("BAD_REQUEST", f"'{key}' must be a string")
    return value


def opt_int(params, key, default, low=1, high=1000):
    value = params.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkerError("BAD_REQUEST", f"'{key}' must be an integer")
    return max(low, min(high, value))


def opt_limit(params, key, default, high):
    """Like ``opt_int``, but ``"all"`` means no limit and comes back as ``None``."""
    if params.get(key) == "all":
        return None
    return opt_int(params, key, default, high=high)


def opt_bool(params, key, default=False):
    value = params.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise WorkerError("BAD_REQUEST", f"'{key}' must be true or false")
    return value


def opt_cursor(params):
    """The opaque continuation cursor of a later page, or ``None`` for the first.

    Cursors are account-bound tokens: never log, record or cache them.
    """
    value = params.get("cursor")
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > CURSOR_MAX:
        raise WorkerError("BAD_REQUEST", "'cursor' must be a string from an earlier reply")
    return value


def opt_order(params, options):
    """``params.order`` when it is one of ``options`` (echoed back as served), else ``None``."""
    value = params.get("order")
    if value is None:
        return None
    if isinstance(value, bool) or value not in options:
        raise WorkerError("BAD_REQUEST", "'order' is not one of this page's sort options")
    return value


def req_list(params, key):
    value = params.get(key)
    if not isinstance(value, list) or not value:
        raise WorkerError("BAD_REQUEST", f"'{key}' must be a non-empty list")
    return value


def choice(params, key, options, default=None):
    value = params.get(key, default)
    if value is None or value in options:
        return value
    raise WorkerError("BAD_REQUEST", f"'{key}' must be one of {', '.join(options)}")


# ---- INVALIDATES sources -------------------------------------------------------------
# The srcKey of an INVALIDATES triple is a params key, None (every entry of
# the method), or one of these, for cached entries keyed by something other
# than a request param as is. They return one value or a list of values.


class Fixed:
    """Always the same ``values``, e.g. the library pages an edit changes."""

    def __init__(self, *values):
        self.values = values

    def __call__(self, params):
        return list(self.values)

    def __repr__(self):
        return f"Fixed{self.values!r}"


class Prefixed:
    """``prefix`` + ``params[key]`` (kept as is when it already starts with the
    prefix), e.g. the browsePage ``VL<playlistId>`` of a playlist edit."""

    def __init__(self, prefix, key):
        self.prefix, self.key = prefix, key

    def __call__(self, params):
        value = params.get(self.key)
        if not isinstance(value, str) or not value:
            return None
        return value if value.startswith(self.prefix) else self.prefix + value

    def __repr__(self):
        return f"Prefixed({self.prefix!r}, {self.key!r})"
