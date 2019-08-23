# This file was contributed to the Volatility Framework Version 3.
# Copyright (C) 2018 Volatility Foundation.
#
# THE LICENSED WORK IS PROVIDED UNDER THE TERMS OF THE Volatility Contributors
# Public License V1.0("LICENSE") AS FIRST COMPLETED BY: Volatility Foundation,
# Inc. ANY USE, PUBLIC DISPLAY, PUBLIC PERFORMANCE, REPRODUCTION OR DISTRIBUTION
# OF, OR PREPARATION OF SUBSEQUENT WORKS, DERIVATIVE WORKS OR DERIVED WORKS BASED
# ON, THE LICENSED WORK CONSTITUTES RECIPIENT'S ACCEPTANCE OF THIS LICENSE AND ITS
# TERMS, WHETHER OR NOT SUCH RECIPIENT READS THE TERMS OF THE LICENSE. "LICENSED
# WORK,” “RECIPIENT" AND “DISTRIBUTOR" ARE DEFINED IN THE LICENSE. A COPY OF THE
# LICENSE IS LOCATED IN THE TEXT FILE ENTITLED "LICENSE.txt" ACCOMPANYING THE
# CONTENTS OF THIS FILE. IF A COPY OF THE LICENSE DOES NOT ACCOMPANY THIS FILE, A
# COPY OF THE LICENSE MAY ALSO BE OBTAINED AT THE FOLLOWING WEB SITE:
# https://www.volatilityfoundation.org/license/vcpl_v1.0
#
# Software distributed under the License is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License for the
# specific language governing rights and limitations under the License.
#
"""Volatility 3 - An open-source memory forensics framework"""
import sys
from importlib import abc
from typing import List, TypeVar, Callable

_T = TypeVar("_T")
_S = TypeVar("_S")


class classproperty(object):
    """Class property decorator

    Note this will change the return type """

    def __init__(self, func: Callable[[_S], _T]) -> None:
        self._func = func

    def __get__(self, _owner_self, owner_cls: _S) -> _T:
        return self._func(owner_cls)


class WarningFindSpec(abc.MetaPathFinder):
    """Checks import attempts and throws a warning if the name shouldn't be used"""

    @staticmethod
    def find_spec(fullname: str, path, target = None):
        """Mock find_spec method that just checks the name, this must go first"""
        if fullname.startswith("volatility.framework.plugins."):
            warning = "Please do not use the volatility.framework.plugins namespace directly, only use volatility.plugins"
            # Pyinstaller uses pkgutil to import, but needs to read the modules to figure out dependencies
            # As such, we only print the warning when directly imported rather than being run from a script
            if 'pkgutil' not in sys.modules:
                raise Warning(warning)


warning_find_spec = [WarningFindSpec()]  # type: List[abc.MetaPathFinder]
sys.meta_path = warning_find_spec + sys.meta_path

# We point the volatility.plugins __path__ variable at BOTH
#   volatility/plugins
#   volatility/framework/plugins
# in that order.
#
# This will allow our users to override any component of any plugin without monkey patching,
# but it also allows us to clear out the plugins directory to get back to proper functionality.
# This offered the greatest flexibility for users whilst allowing us to keep the core separate and clean.
#
# This means that all plugins should be imported as volatility.plugins (otherwise they'll be imported twice,
# once as volatility.plugins.NAME and once as volatility.framework.plugins.NAME).  We therefore throw an error
# if anyone tries to import anything under the volatility.framework.plugins.* namespace
#
# The remediation is to only ever import form volatility.plugins instead.
