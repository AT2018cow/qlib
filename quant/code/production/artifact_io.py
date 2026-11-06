"""Strict JSON boundary for persisted financial artifacts."""

import json
import math


def _reject_constant(value):
    raise ValueError(f"non-finite JSON constant: {value}")


def loads(value):
    return json.loads(value, parse_constant=_reject_constant, parse_float=_parse_float)


def _parse_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("JSON number overflow")
    return number


def dumps(value, **kwargs):
    kwargs["allow_nan"] = False
    return json.dumps(value, **kwargs)
