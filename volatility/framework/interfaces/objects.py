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
"""Objects are the core of volatility, and provide pythonic access to interpreted values of data from a layer.
"""
import abc
import collections
import collections.abc
import logging
from abc import ABCMeta, abstractmethod
from typing import Any, Dict, List, Mapping, Optional

from volatility.framework import constants, interfaces
from volatility.framework.interfaces import context as interfaces_context

vollog = logging.getLogger(__name__)


class ReadOnlyMapping(collections.abc.Mapping):
    """A read-only mapping of various values that offer attribute access as well

    This ensures that the data stored in the mapping should not be modified, making an immutable mapping.
    """

    def __init__(self, dictionary: Mapping[str, Any]) -> None:
        self._dict = dictionary

    def __getattr__(self, attr: str) -> Any:
        """Returns the item as an attribute"""
        if attr == '_dict':
            return super().__getattribute__(attr)
        if attr in self._dict:
            return self._dict[attr]
        raise AttributeError("Object has no attribute: {}.{}".format(self.__class__.__name__, attr))

    def __getitem__(self, name: str) -> Any:
        """Returns the item requested"""
        return self._dict[name]

    def __iter__(self):
        """Returns an iterator of the dictionary items"""
        return self._dict.__iter__()

    def __len__(self) -> int:
        """Returns the length of the internal dictionary"""
        return len(self._dict)


class ObjectInformation(ReadOnlyMapping):
    """Contains common information useful/pertinent only to an individual object (like an instance)

    This typically contains information such as the layer the object belongs to, the offset where it was constructed,
    and if it is a subordinate object, its parent.

    This is primarily used to reduce the number of parameters passed to object constructors and keep them all together
    in a single place.  These values are based on the :class:`ReadOnlyMapping` class, to prevent their modification.
    """

    def __init__(self,
                 layer_name: str,
                 offset: int,
                 member_name: Optional[str] = None,
                 parent: Optional['ObjectInterface'] = None,
                 native_layer_name: Optional[str] = None):
        super().__init__({
            'layer_name': layer_name,
            'offset': offset,
            'member_name': member_name,
            'parent': parent,
            'native_layer_name': native_layer_name or layer_name
        })


class ObjectInterface(metaclass = ABCMeta):
    """A base object required to be the ancestor of every object used in volatility"""

    def __init__(self, context: 'interfaces_context.ContextInterface', type_name: str, object_info: 'ObjectInformation',
                 **kwargs) -> None:
        # Since objects are likely to be instantiated often,
        # we're reliant on type_checking to ensure correctness of context, offset and parent
        # Everything else may be wrong, but that will get caught later on

        # Add an empty dictionary at the start to allow objects to add their own data to the vol object
        #
        # NOTE:
        # This allows objects to MASSIVELY MESS with their own internal representation!!!
        # Changes to offset, type_name, etc should NEVER be done
        #

        # Normalize offsets
        mask = context.layers[object_info.layer_name].address_mask
        normalized_offset = object_info.offset & mask

        self._vol = collections.ChainMap({}, object_info, {'type_name': type_name, 'offset': normalized_offset}, kwargs)
        self._context = context

    def __getattr__(self, attr: str) -> Any:
        """Method for ensuring volatility members can be returned"""
        raise AttributeError

    @property
    def vol(self) -> ReadOnlyMapping:
        """Returns the volatility specific object information"""
        # Wrap the outgoing vol in a read-only proxy
        return ReadOnlyMapping(self._vol)

    @abstractmethod
    def write(self, value: Any):
        """Writes the new value into the format at the offset the object currently resides at"""

    def validate(self) -> bool:
        """A method that can be overridden to validate this object.  It does not return and its return value should not be used.

        Raises InvalidDataException on failure to validate the data correctly.
        """

    def get_symbol_table(self) -> 'interfaces.symbols.SymbolTableInterface':
        """Returns the symbol table for this particular object

        Returns none if the symbol table cannot be identified.
        """
        if constants.BANG not in self.vol.type_name:
            raise ValueError("Unable to determine table for symbol: {}".format(self.vol.type_name))
        table_name = self.vol.type_name[:self.vol.type_name.index(constants.BANG)]
        if table_name not in self._context.symbol_space:
            raise KeyError("Symbol table not found in context's symbol_space for symbol: {}".format(self.vol.type_name))
        return self._context.symbol_space[table_name]

    def cast(self, new_type_name: str, **additional) -> 'ObjectInterface':
        """Returns a new object at the offset and from the layer that the current object inhabits

        .. note:: If new type name does not include a symbol table, the symbol table for the current object is used
        """
        # TODO: Carefully consider the implications of casting and how it should work
        if constants.BANG not in new_type_name:
            symbol_table = self.vol['type_name'].split(constants.BANG)[0]
            new_type_name = symbol_table + constants.BANG + new_type_name
        object_template = self._context.symbol_space.get_type(new_type_name)
        object_template = object_template.clone()
        object_template.update_vol(**additional)
        object_info = ObjectInformation(
            layer_name = self.vol.layer_name,
            offset = self.vol.offset,
            member_name = self.vol.member_name,
            parent = self.vol.parent,
            native_layer_name = self.vol.native_layer_name)
        return object_template(context = self._context, object_info = object_info)

    def has_member(self, member_name: str) -> bool:
        """Returns whether the object would contain a member called member_name"""
        return False

    class VolTemplateProxy(metaclass = abc.ABCMeta):
        """A container for proxied methods that the ObjectTemplate of this object will call.  This is primarily to keep
        methods together for easy organization/management, there is no significant need for it to be a separate class.

        The methods of this class *must* be class methods rather than standard methods, to allow for code reuse.
        Each method also takes a template since the templates may contain the necessary data about the
        yet-to-be-constructed object.  It allows objects to control how their templates respond without needing to write
        new templates for each and every potental object type."""
        _methods = []  # type: List[str]

        @classmethod
        @abc.abstractmethod
        def size(cls, template: 'Template') -> int:
            """Returns the size of the template object"""

        @classmethod
        @abc.abstractmethod
        def children(cls, template: 'Template') -> List['Template']:
            """Returns the children of the template"""
            return []

        @classmethod
        @abc.abstractmethod
        def replace_child(cls, template: 'Template', old_child: 'Template', new_child: 'Template') -> None:
            """Substitutes the old_child for the new_child"""
            raise KeyError("Template does not contain any children to replace: {}".format(template.vol.type_name))

        @classmethod
        @abc.abstractmethod
        def relative_child_offset(cls, template: 'Template', child: str) -> int:
            """Returns the relative offset from the head of the parent data to the child member"""
            raise KeyError("Template does not contain any children: {}".format(template.vol.type_name))

        @classmethod
        @abc.abstractmethod
        def has_member(cls, template: 'Template', member_name: str) -> bool:
            """Returns whether the object would contain a member called member_name"""
            return False


class Template:
    """Class for all Factories that take offsets, and data layers and produce objects

    This is effectively a class for currying object calls.  It creates a callable that can be called with the following
    parameters:

    Args:
        context: The context containing the memory layers and symbols required to construct the object
        object_info: Basic information about the object, see the ObjectInformation class for more information

    Returns:
        The constructed object

    The keyword arguments handed to the constructor, along with the type_name are stored for later retrieval.
    These will be access as `object.vol.<keyword>` or `template.vol.<keyword>` for each object and should contain
    as least the basic information that each object will require before it is instantiated (so `offset` and `parent`
    are explicitly not recorded here).  This dictionary can be updated after construction, but any changes made
    after that point will *not* be cloned.  This is so that templates such as those for string objects may
    contain different length limits, without affecting all other strings using the same template from a SymbolTable,
    constructed at resolution time and then cached.
    """

    def __init__(self, type_name: str, **arguments) -> None:
        """Stores the keyword arguments for later use"""
        # Allow the updating of template arguments whilst still in template form
        super().__init__()
        self._arguments = arguments
        empty_dict = {}  # type: Dict[str, Any]
        self._vol = collections.ChainMap(empty_dict, self._arguments, {'type_name': type_name})

    @property
    def vol(self) -> ReadOnlyMapping:
        """Returns a volatility information object, much like the :class:`~volatility.framework.interfaces.objects.ObjectInformation` provides"""
        return ReadOnlyMapping(self._vol)

    @property
    def children(self) -> List['Template']:
        """The children of this template (such as member types, sub-types and base-types where they are relevant).
        Used to traverse the template tree.
        """
        return []

    @property
    @abstractmethod
    def size(self) -> int:
        """Returns the size of the template"""

    @abstractmethod
    def relative_child_offset(self, child: str) -> int:
        """Returns the relative offset of the `child` member from its parent offset"""

    @abstractmethod
    def replace_child(self, old_child: 'Template', new_child: 'Template') -> None:
        """Replaces `old_child` with `new_child` in the list of children"""

    @abstractmethod
    def has_member(self, member_name: str) -> bool:
        """Returns whether the object would contain a member called member_name"""

    def clone(self) -> 'Template':
        """Returns a copy of the original Template as constructed (without `update_vol` additions having been made)"""
        clone = self.__class__(**self._vol.parents.new_child())
        return clone

    def update_vol(self, **new_arguments) -> None:
        """Updates the keyword arguments with values that will **not** be carried across to clones"""
        self._vol.update(new_arguments)

    def __getattr__(self, attr: str) -> Any:
        """Exposes any other values stored in ._vol as attributes (for example, enumeration choices)"""
        if attr != '_vol':
            if attr in self._vol:
                return self._vol[attr]
        raise AttributeError("{} object has no attribute {}".format(self.__class__.__name__, attr))

    def __call__(self, context: 'interfaces_context.ContextInterface',
                 object_info: ObjectInformation) -> ObjectInterface:
        """Constructs the object"""
