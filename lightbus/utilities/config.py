import inspect
import logging
import random
import string

from typing import Type, NamedTuple  # noqa

import itertools

logger = logging.getLogger(__name__)


def make_from_config_structure(class_name, from_config_method, extra_parameters=tuple()) -> Type:
    """
    Create a new named tuple based on the method signature of from_config_method.

    This is useful when dynamically creating the config structure for Transports
    and Plugins.
    """
    code = f"class {class_name}Config(NamedTuple):\n    pass\n"
    vars = dict(p={})

    parameters = inspect.signature(from_config_method).parameters.values()
    for parameter in itertools.chain(parameters, extra_parameters):
        if parameter.name == "config":
            # The config parameter is always passed to from_config() in order to
            # give it access to the global configuration (useful for setting
            # sensible defaults)
            continue

        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.VAR_POSITIONAL):
            logger.warning(
                f"Positional-only arguments are not supported in from_config() on class {class_name}"
            )
        elif parameter.kind in (parameter.VAR_KEYWORD,):
            logger.warning(
                f"**kwargs-style parameters are not supported in from_config() on class {class_name}"
            )
        else:
            name = parameter.name
            vars["p"][name] = parameter
            code += f"    {name}: p['{name}'].annotation = p['{name}'].default\n"

    globals_ = globals().copy()
    globals_.update(vars)
    exec(code, globals_)
    cls = globals_[f"{class_name}Config"]
    return enable_config_inheritance(cls)


def random_name(length: int) -> str:
    return "".join(random.choice(string.ascii_lowercase) for _ in range(length))


def enable_config_inheritance(cls):
    """Decorator to make classes as supporting inheritance"""
    cls._enable_config_inheritance = True
    return cls
