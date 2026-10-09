"""Helpers to import resources.lib.library_sync modules without Kodi."""
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
LIBRARY_SYNC_PATH = REPO_ROOT / 'resources' / 'lib' / 'library_sync'
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _restore_attributes(module, attributes):
    for name in list(vars(module)):
        if name not in attributes:
            delattr(module, name)
    for name, value in attributes.items():
        setattr(module, name, value)


def isolate_modules(test_case):
    """Restore sys.modules and the resources.lib package after test_case."""
    modules = patch.dict(sys.modules)
    modules.start()
    test_case.addCleanup(modules.stop)
    package = importlib.import_module('resources.lib')
    test_case.addCleanup(_restore_attributes, package, dict(vars(package)))


def load_library_sync_module(name, stubs):
    """
    Imports resources.lib.library_sync.<name> without running the package
    __init__. stubs maps full module names to dicts of module attributes.
    Call isolate_modules() first.
    """
    sys.modules.pop('resources.lib.library_sync.%s' % name, None)
    package = types.ModuleType('resources.lib.library_sync')
    package.__path__ = [str(LIBRARY_SYNC_PATH)]
    sys.modules['resources.lib.library_sync'] = package
    for module_name, attributes in stubs.items():
        module = types.ModuleType(module_name)
        for key, value in attributes.items():
            setattr(module, key, value)
        sys.modules[module_name] = module
        parent_name, _, child = module_name.rpartition('.')
        if parent_name:
            setattr(importlib.import_module(parent_name), child, module)
    return importlib.import_module('resources.lib.library_sync.%s' % name)
