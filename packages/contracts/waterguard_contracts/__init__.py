"""Shared JSON contracts and a deliberately fixture-only integration adapter."""

from .core import FixtureAdapter, load, validate, verify_fixture, check_bundle

__all__ = ['FixtureAdapter', 'load', 'validate', 'verify_fixture', 'check_bundle']
