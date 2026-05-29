"""Lightweight, dependency-free request-body validation.

A spec is a ``dict`` of ``field_name -> FieldSpec``. ``validate(payload, spec)``
returns a cleaned dict (coerced, trimmed, defaults applied) or raises
``ApiError('validation_error', ...)`` with field-level details. Unknown keys are
ignored by default for forward-compatibility with the frontend.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from archagent.api.errors import ApiError


@dataclass
class FieldSpec:
    type: type = str
    required: bool = False
    default: Any = None
    max_len: int | None = None
    min_val: float | None = None
    max_val: float | None = None
    choices: tuple | None = None
    strip: bool = True


def field(type=str, required=False, default=None, max_len=None,
          min_val=None, max_val=None, choices=None, strip=True) -> FieldSpec:
    return FieldSpec(type, required, default, max_len, min_val, max_val,
                     tuple(choices) if choices else None, strip)


def _coerce(value, spec: FieldSpec, name: str, errors: list):
    if spec.type in (int, float):
        try:
            value = spec.type(value)
        except (ValueError, TypeError):
            errors.append({'field': name, 'code': 'type', 'message': f'{name} must be a number'})
            return None
        if spec.min_val is not None and value < spec.min_val:
            errors.append({'field': name, 'code': 'min', 'message': f'{name} must be >= {spec.min_val}'})
        if spec.max_val is not None and value > spec.max_val:
            errors.append({'field': name, 'code': 'max', 'message': f'{name} must be <= {spec.max_val}'})
        return value
    if spec.type is bool:
        return bool(value)
    if spec.type in (list, dict):
        if not isinstance(value, spec.type):
            errors.append({'field': name, 'code': 'type', 'message': f'{name} has the wrong type'})
            return None
        return value
    # string
    value = str(value)
    if spec.strip:
        value = value.strip()
    if spec.max_len is not None and len(value) > spec.max_len:
        errors.append({'field': name, 'code': 'max_len', 'message': f'{name} exceeds {spec.max_len} chars'})
    if spec.choices is not None and value not in spec.choices:
        errors.append({'field': name, 'code': 'choices', 'message': f'{name} must be one of {list(spec.choices)}'})
    return value


def validate(payload: dict, spec: dict[str, FieldSpec]) -> dict:
    if not isinstance(payload, dict):
        raise ApiError('bad_request', 'request body must be a JSON object')
    cleaned: dict = {}
    errors: list = []
    for name, fspec in spec.items():
        present = name in payload and payload[name] not in (None, '')
        if not present:
            if fspec.required:
                errors.append({'field': name, 'code': 'required', 'message': f'{name} is required'})
            elif fspec.default is not None:
                cleaned[name] = fspec.default
            continue
        cleaned[name] = _coerce(payload[name], fspec, name, errors)
    if errors:
        raise ApiError('validation_error',
                       f'{len(errors)} field(s) failed validation',
                       details={'errors': errors})
    return cleaned
