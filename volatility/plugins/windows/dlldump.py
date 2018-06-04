import logging, ntpath

import volatility.framework.interfaces.plugins as interfaces_plugins
import volatility.plugins.windows.pslist as pslist
import volatility.plugins.windows.vadinfo as vadinfo
from volatility.framework import interfaces
from volatility.framework import renderers
from volatility.framework.objects import utility
import volatility.framework.constants as constants
from volatility.framework.symbols.windows.pe import PEIntermedSymbols

vollog = logging.getLogger(__name__)

class DllDump(interfaces_plugins.PluginInterface):
    """Dumps process memory ranges as DLLs"""

    @classmethod
    def get_requirements(cls):
        # Since we're calling the plugin, make sure we have the plugin's requirements
        return vadinfo.VadInfo.get_requirements()

    def _generator(self, procs):
        pe_table_name = PEIntermedSymbols.create(self.context,
                                                 self.config_path,
                                                 "windows",
                                                 "pe")

        vadinfo_plugin = vadinfo.VadInfo(self.context, self.config_path)

        for proc in procs:
            process_name = utility.array_to_string(proc.ImageFileName)
            # TODO: what kind of exceptions could this raise and what should we do?
            proc_layer_name = proc.add_process_layer()

            for vad in vadinfo_plugin.list_vads(proc):

                # this parameter is inherited from the VadInfo plugin. if a user specifies
                # an address, then it bypasses the DLL identification heuristics
                if self.config.get("address", None) is None:

                    # rather than relying on the PEB for DLLs, which can be swapped,
                    # it requires special handling on wow64 processes, and its
                    # unreliable from an integrity standpoint, let's use the VADs instead
                    protection_string = vad.get_protection(vadinfo_plugin.protect_values(),
                                                           vadinfo.winnt_protections)

                    # DLLs are write copy...
                    if protection_string != "PAGE_EXECUTE_WRITECOPY":
                        continue

                    # DLLs have mapped files...
                    if isinstance(vad.get_file_name(), interfaces.renderers.BaseAbsentValue):
                        continue

                try:
                    filedata = interfaces_plugins.FileInterface(
                        "pid.{0}.{1}.{2:#x}.dmp".format(proc.UniqueProcessId,
                                                        ntpath.basename(vad.get_file_name()),
                                                        vad.get_start()))

                    dos_header = self.context.object(pe_table_name + constants.BANG +
                                                     "_IMAGE_DOS_HEADER", offset=vad.get_start(),
                                                     layer_name=proc_layer_name)

                    for offset, data in dos_header.reconstruct():
                        filedata.data.seek(offset)
                        filedata.data.write(data)

                    self.produce_file(filedata)
                    result_text = "Stored {}".format(filedata.preferred_filename)
                except Exception:
                    result_text = "Unable to dump PE at {0:#x}".format(vad.get_start())

                yield (0, (proc.UniqueProcessId,
                           process_name,
                           result_text))

    def run(self):
        plugin = pslist.PsList(self.context, self.config_path)

        return renderers.TreeGrid([("PID", int),
                                   ("Process", str),
                                   ("Result", str)],
                                  self._generator(plugin.list_processes()))
