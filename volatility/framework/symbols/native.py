import copy
import typing

from volatility.framework import constants, interfaces, objects


class NativeTable(interfaces.symbols.NativeTableInterface):
    """Symbol List that handles Native types"""

    # FIXME: typing the native_dictionary as typing.Tuple[interfaces.objects.ObjectInterface, str] throws many errors
    def __init__(self,
                 name: str,
                 native_dictionary: typing.Dict[str, typing.Any]) -> None:
        super().__init__(name, self)
        self._native_dictionary = copy.deepcopy(native_dictionary)
        self._overrides = {}  # type: typing.Dict[str, interfaces.objects.ObjectInterface]
        for native_type in self._native_dictionary:
            native_class, _native_struct = self._native_dictionary[native_type]
            self._overrides[native_type] = native_class
        # Create this once early, because it may get used a lot
        self._types = set(self._native_dictionary).union(
            {'enum', 'array', 'bitfield', 'void', 'pointer', 'string', 'bytes', 'function'})

    def get_type_class(self, name: str) -> typing.Type[interfaces.objects.ObjectInterface]:
        ntype, _ = self._native_dictionary.get(name, (objects.Integer, None))
        return ntype

    @property
    def types(self) -> typing.Iterable[str]:
        """Returns an iterator of the symbol type names"""
        return self._types

    def get_type(self, type_name: str) -> interfaces.objects.Template:
        """Resolves a symbol name into an object template

           symbol_space is used to resolve any subtype symbols if they don't exist in this list
        """
        # NOTE: These need updating whenever the object init signatures change
        prefix = ""
        if constants.BANG in type_name:
            name_split = type_name.split(constants.BANG)
            if len(name_split) > 2:
                raise ValueError("SymbolName cannot contain multiple {} separators".format(constants.BANG))
            table_name, type_name = name_split
            prefix = table_name + constants.BANG

        additional = {}  # type: typing.Dict[str, typing.Any]
        obj = None  # type: typing.Optional[typing.Type[interfaces.objects.ObjectInterface]]
        if type_name == 'void' or type_name == 'function':
            obj = objects.Void
        elif type_name == 'array':
            obj = objects.Array
            additional = {"count": 0, "subtype": self.get_type('void')}
        elif type_name == 'enum':
            obj = objects.Enumeration
            additional = {"base_type": self.get_type('int'), "choices": {}}
        elif type_name == 'bitfield':
            obj = objects.BitField
            additional = {"start_bit": 0, "end_bit": 0, "base_type": self.get_type('int')}
        elif type_name == 'string':
            obj = objects.String
            additional = {"max_length": 0}
        elif type_name == 'bytes':
            obj = objects.Bytes
            additional = {"length": 0}
        if obj is not None:
            return objects.templates.ObjectTemplate(obj, type_name = prefix + type_name, **additional)

        _native_type, native_format = self._native_dictionary[type_name]
        if type_name == 'pointer':
            additional = {'subtype': self.get_type('void')}
        return objects.templates.ObjectTemplate(self.get_type_class(type_name),  # pylint: disable=W0142
                                                type_name = prefix + type_name,
                                                struct_format = native_format,
                                                **additional)


std_ctypes = {'int': (objects.Integer, '<i'),
              'long': (objects.Integer, '<i'),
              'unsigned long': (objects.Integer, '<I'),
              'unsigned int': (objects.Integer, '<I'),
              'char': (objects.Integer, '<b'),
              'byte': (objects.Bytes, '<c'),
              'unsigned char': (objects.Integer, '<B'),
              'unsigned short int': (objects.Integer, '<H'),
              'unsigned short': (objects.Integer, '<H'),
              'unsigned be short': (objects.Integer, '>H'),
              'short': (objects.Integer, '<h'),
              'long long': (objects.Integer, '<q'),
              'unsigned long long': (objects.Integer, '<Q'),
              'float': (objects.Float, "<d"),
              'double': (objects.Float, "<d"),
              'wchar': (objects.Integer, '<H')}
native_types = std_ctypes.copy()
native_types['pointer'] = (objects.Pointer, "<I")
x86NativeTable = NativeTable("native", native_types)
native_types['pointer'] = (objects.Pointer, '<Q')
x64NativeTable = NativeTable("native", native_types)
