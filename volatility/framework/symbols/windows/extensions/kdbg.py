from volatility.framework import objects
from volatility.framework import constants

class _KDDEBUGGER_DATA64(objects.Struct):

    def get_build_lab(self):
        """Returns the NT build lab string from the KDBG"""

        layer_name = self.vol.layer_name
        nt_symbol_name = self.get_symbol_table().table_mapping["nt_symbols"]

        kvo = self._context.memory[layer_name].config['kernel_virtual_offset']
        ntkrnlmp = self._context.module(nt_symbol_name, layer_name=layer_name, offset=kvo)

        return ntkrnlmp.object(type_name="string",
                               offset=self.NtBuildLab,
                               max_length=32,
                               errors="replace")

    def get_csdversion(self):
        """Returns the CSDVersion as an integer (i.e. Service Pack number)"""

        layer_name = self.vol.layer_name
        symbol_table_name = self.get_symbol_table().name

        csdresult = self._context.object(symbol_table_name + constants.BANG + "unsigned long",
                                         layer_name=layer_name,
                                         offset=self.CmNtCSDVersion)

        return (csdresult >> 8) & 0xffffffff