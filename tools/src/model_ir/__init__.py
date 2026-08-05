"""Shared Model IR types and JSON boundaries."""

from .json_io import dump_model_ir, load_model_ir
from .model import SCHEMA_VERSION, ModelEntry, ModelIR, ModelIRValidationError

__all__ = [
    "SCHEMA_VERSION",
    "ModelEntry",
    "ModelIR",
    "ModelIRValidationError",
    "dump_model_ir",
    "load_model_ir",
]
