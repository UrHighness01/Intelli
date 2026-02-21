"""Compatibility package to expose the hyphenated `agent-gateway` folder
as a valid Python package namespace `agent_gateway` for tests and imports.

This module adjusts the package `__path__` at import time to include the
on-disk `agent-gateway` directory so code can import `agent_gateway.*`.
"""
import os
import pkgutil

# Compute the sibling folder named 'agent-gateway' relative to this file.
_here = os.path.dirname(__file__)
_candidate = os.path.normpath(os.path.join(_here, '..', 'agent-gateway'))
if os.path.isdir(_candidate):
    # Prepend so local package modules take precedence.
    __path__.insert(0, _candidate)

# Allow normal package discovery for subpackages inside agent-gateway.