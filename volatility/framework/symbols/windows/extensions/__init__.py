import collections.abc

from volatility.framework import constants, objects
from volatility.framework.symbols import generic
from volatility.framework import exceptions

# Keep these in a basic module, to prevent import cycles when symbol providers require them

class _EX_FAST_REF(objects.Struct):
    """This is a standard Windows structure that stores a pointer to an
    object but also leverages the least significant bits to encode additional
    details. When dereferencing the pointer, we need to strip off the extra bits."""
    
    def dereference(self):
    
        if constants.BANG not in self.vol.type_name:
            raise ValueError("Invalid symbol table name syntax (no {} found)".format(constants.BANG))
    
        # the mask value is different on 32 and 64 bits 
        symbol_table_name = self.vol.type_name.split(constants.BANG)[0]
        if self._context.symbol_space.get_type(symbol_table_name + constants.BANG + "pointer").size == 4:
            max_fast_ref = 7
        else:
            max_fast_ref = 15
    
        return self._context.object(symbol_table_name + constants.BANG + "pointer", layer_name = self.vol.layer_name, offset = self.Object & ~max_fast_ref)

class ExecutiveObject(object):
    """This is used as a "mixin" that provides all kernel executive 
    objects with a means of finding their own object header."""
    
    def object_header(self):
        if constants.BANG not in self.vol.type_name:
            raise ValueError("Invalid symbol table name syntax (no {} found)".format(constants.BANG))
        symbol_table_name = self.vol.type_name.split(constants.BANG)[0]
        body_offset = self._context.symbol_space.get_type(symbol_table_name + constants.BANG + "_OBJECT_HEADER").relative_child_offset("Body") 
        return self._context.object(symbol_table_name + constants.BANG + "_OBJECT_HEADER", layer_name = self.vol.layer_name, offset = self.vol.offset - body_offset)

class _CM_KEY_BODY(objects.Struct):
    """This represents an open handle to a registry key and 
    is not tied to the registry hive file format on disk."""
    
    @property
    def helper_full_key_name(self):
        output = []
        kcb = self.KeyControlBlock
        while kcb.ParentKcb:
            if kcb.NameBlock.Name == None:
                break
            output.append(kcb.NameBlock.Name.cast("string", 
                          encoding = "utf8", 
                          max_length = kcb.NameBlock.NameLength, 
                          errors = "replace"))
            kcb = kcb.ParentKcb
        return "\\".join(reversed(output))

class _DEVICE_OBJECT(objects.Struct, ExecutiveObject):
    @property
    def helper_device_name(self):
        header = self.object_header()
        return header.NameInfo.Name.String

class _FILE_OBJECT(objects.Struct, ExecutiveObject):
    def file_name_with_device(self):
        name = ""
        if self._context.memory[self.vol.layer_name].is_valid(self.DeviceObject):
            name = "\\Device\\{}".format(self.DeviceObject.helper_device_name)
        
        try:    
            name += self.FileName.String
        except exceptions.PagedInvalidAddressException:
            pass
        
        return name

class _OBJECT_HEADER(objects.Struct):
    @property
    def NameInfo(self):
        if constants.BANG not in self.vol.type_name:
            raise ValueError("Invalid symbol table name syntax (no {} found)".format(constants.BANG))
            
        symbol_table_name = self.vol.type_name.split(constants.BANG)[0]
    
        try:
            header_offset = ord(self.NameInfoOffset)
        except AttributeError:
            #http://codemachine.com/article_objectheader.html (Windows 7 and later)
            name_info_bit = 0x2 
            
            layer = self._context.memory[self.vol.layer_name]            
            kvo = layer.config.get("kernel_virtual_offset", None)
                    
            if kvo == None:
                raise AttributeError("Could not find kernel_virtual_offset for layer: {}".format(self.vol.layer_name))
            
            ntkrnlmp = self._context.module(symbol_table_name, layer_name = self.vol.layer_name, offset = kvo)
            address = ntkrnlmp.get_symbol("ObpInfoMaskToOffset").address
            calculated_index = ord(self.InfoMask) & (name_info_bit | (name_info_bit - 1))
                        
            header_offset = ord(self._context.object(symbol_table_name + constants.BANG + "unsigned char", 
                                                layer_name = self.vol.layer_name, 
                                                offset = kvo + address + calculated_index))
                                                                                                                                                               
        header = self._context.object(symbol_table_name + constants.BANG + "_OBJECT_HEADER_NAME_INFO", 
                                      layer_name = self.vol.layer_name, 
                                      offset = self.vol.offset - header_offset)
        return header
        

class _ETHREAD(objects.Struct):
    def owning_process(self, kernel_layer = None):
        """Return the EPROCESS that owns this thread"""
        return self.ThreadsProcess.dereference(kernel_layer)


class _UNICODE_STRING(objects.Struct):
    @property
    def helper_string(self):
        # We explicitly do *not* catch errors here, we allow an exception to be thrown
        # (otherwise there's no way to determine anything went wrong)
        # It's up to the user of this method to catch exceptions
        return self.Buffer.dereference().cast("string", max_length = self.Length, errors = "replace",
                                              encoding = "utf16")

    String = helper_string


class _EPROCESS(generic.GenericIntelProcess):
    def add_process_layer(self, context, config_prefix = None, preferred_name = None):
        """Constructs a new layer based on the process's DirectoryTableBase"""

        parent_layer = context.memory[self.vol.layer_name]
        # Presumably for 64-bit systems, the DTB is defined as an array, rather than an unsigned long long
        if isinstance(self.Pcb.DirectoryTableBase, objects.Array):
            dtb = self.Pcb.DirectoryTableBase.cast("unsigned long long")
        else:
            dtb = self.Pcb.DirectoryTableBase
        dtb = dtb & ((1 << parent_layer.bits_per_register) - 1)

        # Add the constructed layer and return the name
        return self._add_process_layer(context, dtb, config_prefix, preferred_name)

    def load_order_modules(self):
        """Generator for DLLs in the order that they were loaded"""

        if constants.BANG not in self.vol.type_name:
            raise ValueError("Invalid symbol table name syntax (no {} found)".format(constants.BANG))

        proc_layer_name = self.add_process_layer(self._context)

        proc_layer = self._context.memory[proc_layer_name]
        if not proc_layer.is_valid(self.Peb):
            raise StopIteration

        sym_table = self.vol.type_name.split(constants.BANG)[0]
        peb = self._context.object("{}{}_PEB".format(sym_table, constants.BANG), layer_name = proc_layer_name,
                                   offset = self.Peb)

        for entry in peb.Ldr.InLoadOrderModuleList.to_list(
                "{}{}_LDR_DATA_TABLE_ENTRY".format(sym_table, constants.BANG), "InLoadOrderLinks"):
            yield entry


class _LIST_ENTRY(objects.Struct, collections.abc.Iterable):
    def to_list(self, symbol_type, member, forward = True, sentinel = True, layer = None):
        """Returns an iterator of the entries in the list"""

        if layer is None:
            layer = self.vol.layer_name

        relative_offset = self._context.symbol_space.get_type(symbol_type).relative_child_offset(member)

        direction = 'Blink'
        if forward:
            direction = 'Flink'
        link = getattr(self, direction).dereference()

        if not sentinel:
            yield self._context.object(symbol_type, layer, offset = self.vol.offset - relative_offset)

        seen = {self.vol.offset}
        while link.vol.offset not in seen:

            obj = self._context.object(symbol_type, layer, offset = link.vol.offset - relative_offset)
            yield obj

            seen.add(link.vol.offset)
            link = getattr(link, direction).dereference()

    def __iter__(self):
        return self.to_list(self.vol.parent.vol.type_name, self.vol.member_name)
