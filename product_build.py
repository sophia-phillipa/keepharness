"""Setuptools backend with deterministic product metadata generation."""

from setuptools import build_meta

from control.product import generate


def __getattr__(name):
    hook = getattr(build_meta, name)

    def invoke(*args, **kwargs):
        generate()
        return hook(*args, **kwargs)

    return invoke
