"""Unit tests for the DNS provider registry — twin of
`atlas/atlas/providers/test_registry.py`. Stubs `frappe.get_doc` so the registry
shape is exercised without requiring the `Domain Provider` DocType to exist."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from atlas.atlas import dns
from atlas.atlas.dns.base import AuthResult, DnsProvider, WildcardTargets


class _StubDnsProvider(DnsProvider):
	provider_type = "Stub"

	def authenticate(self) -> AuthResult:
		return AuthResult(ok=True, account_label="stub")

	def credential_env(self) -> dict[str, str]:
		return {"STUB": "1"}

	def certbot_authenticator(self) -> str:
		return "stub"

	def upsert_wildcard(self, domain: str, targets: WildcardTargets) -> list[str]:
		return [f"A *.{domain}"]


class TestDnsProviderRegistry(IntegrationTestCase):
	def setUp(self) -> None:
		dns._REGISTRY["Stub"] = _StubDnsProvider

	def tearDown(self) -> None:
		dns._REGISTRY.pop("Stub", None)

	def test_register_decorator_stores_class(self) -> None:
		@dns.register
		class _DecoratorStub(DnsProvider):
			provider_type = "DecoratorStub"

			def authenticate(self) -> AuthResult:
				return AuthResult(ok=True)

			def credential_env(self) -> dict[str, str]:
				return {}

			def certbot_authenticator(self) -> str:
				return "decorator-stub"

			def upsert_wildcard(self, domain: str, targets: WildcardTargets) -> list[str]:
				return []

		try:
			self.assertIs(dns._REGISTRY["DecoratorStub"], _DecoratorStub)
		finally:
			dns._REGISTRY.pop("DecoratorStub", None)

	def test_for_domain_provider_instantiates_active_class(self) -> None:
		row = SimpleNamespace(is_active=1, provider_type="Stub", name="route53-prod")
		with (
			patch.object(dns, "_load_implementations", lambda: None),
			patch.object(frappe, "get_doc", return_value=row),
		):
			instance = dns.for_domain_provider("route53-prod")
		self.assertIsInstance(instance, _StubDnsProvider)

	def test_for_domain_provider_throws_on_archived(self) -> None:
		row = SimpleNamespace(is_active=0, provider_type="Stub", name="route53-prod")
		with (
			patch.object(dns, "_load_implementations", lambda: None),
			patch.object(frappe, "get_doc", return_value=row),
		):
			with self.assertRaises(frappe.ValidationError) as raised:
				dns.for_domain_provider("route53-prod")
		self.assertIn("archived", str(raised.exception))

	def test_for_domain_provider_throws_on_unknown_type(self) -> None:
		row = SimpleNamespace(is_active=1, provider_type="Unregistered", name="x")
		with (
			patch.object(dns, "_load_implementations", lambda: None),
			patch.object(frappe, "get_doc", return_value=row),
		):
			with self.assertRaises(frappe.ValidationError) as raised:
				dns.for_domain_provider("x")
		self.assertIn("No implementation", str(raised.exception))
