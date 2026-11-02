"""Tiny fintech codebase used by the eBPF code-RCA demo.

It intentionally contains ten independent, realistic financial bugs. The RCA
engine reads eBPF stack samples, not these comments; the test suite is the
ground truth for the remediation loop.
"""
