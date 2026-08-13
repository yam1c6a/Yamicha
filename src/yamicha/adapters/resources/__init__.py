"""Read-only adapters for bounded external resources."""

from .filesystem import BoundedTextFileReader

__all__ = ["BoundedTextFileReader"]
