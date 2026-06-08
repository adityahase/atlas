"""DNS provider registry — twin of `atlas/atlas/providers/__init__.py`.

Vendors register their `DnsProvider` subclass via `@register`. Callers ask for an
instance via `for_domain_provider(provider_name)`, which looks up the `Domain
Provider` DocType row, checks `is_active`, and instantiates the registered class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe

# Re-exported for callers (the TLS Certificate controller builds `dns.WildcardTargets`).
# base.py is stdlib-only at import time, so this triggers no boto3 load.
from atlas.atlas.dns.base import WildcardTargets

if TYPE_CHECKING:
	from atlas.atlas.dns.base import DnsProvider

__all__ = ["WildcardTargets", "for_domain_provider", "register"]


_REGISTRY: dict[str, type["DnsProvider"]] = {}


def register(cls: type["DnsProvider"]) -> type["DnsProvider"]:
	"""Class decorator that records `cls` against its `provider_type`."""
	_REGISTRY[cls.provider_type] = cls
	return cls


def for_domain_provider(provider_name: str) -> "DnsProvider":
	"""Return an instantiated `DnsProvider` for the given `Domain Provider` row.

	Raises `frappe.ValidationError` if the row is archived (`is_active=0`)
	or if its `provider_type` has no registered implementation.
	"""
	_load_implementations()
	row = frappe.get_doc("Domain Provider", provider_name)
	if not row.is_active:
		frappe.throw(f"Domain Provider {provider_name!r} is archived")
	factory = _REGISTRY.get(row.provider_type)
	if factory is None:
		frappe.throw(f"No implementation for provider_type {row.provider_type!r}")
	return factory()


def _load_implementations() -> None:
	"""Import vendor modules so their `@register` decorators run. Idempotent —
	Python caches the import. Separate so tests that stub the registry can skip it."""
	import atlas.atlas.dns.route53
