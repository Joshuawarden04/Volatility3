"""A module containing a collection of plugins that produce data
typically found in mac's /proc file system.
"""

import datetime
import struct

from volatility.framework import constants, renderers, symbols
from volatility.framework.configuration import requirements
from volatility.framework.interfaces import plugins
from volatility.framework.layers import scanners
from volatility.framework.objects import utility
from volatility.plugins import timeliner
from volatility.plugins.mac import pslist

from volatility.framework.symbols.linux.bash import BashIntermedSymbols


class Bash(plugins.PluginInterface, timeliner.TimeLinerInterface):
    """Recovers bash command history from memory"""

    @classmethod
    def get_requirements(cls):
        return [requirements.TranslationLayerRequirement(name = 'primary',
                                                         description = 'Kernel Address Space',
                                                         architectures = ["Intel32", "Intel64"]),
                requirements.SymbolRequirement(name = "darwin",
                                               description = "mac Kernel")]

    def _generator(self, tasks):
        is_32bit = not symbols.symbol_table_is_64bit(self.context, self.config["darwin"])
        if is_32bit:
            pack_format = "I"
            bash_json_file = "bash32"
        else:
            pack_format = "Q"
            bash_json_file = "bash64"

        bash_table_name = BashIntermedSymbols.create(self.context,
                                                     self.config_path,
                                                     "linux",
                                                     bash_json_file)

        ts_offset = self.context.symbol_space.get_type(
            bash_table_name + constants.BANG + "hist_entry").relative_child_offset("timestamp")

        for task in tasks:
            task_name = utility.array_to_string(task.p_comm)
            if task_name not in ["bash", "sh", "dash"]:
                continue

            proc_layer_name = task.add_process_layer()
            if proc_layer_name == None:
                continue

            proc_layer = self.context.memory[proc_layer_name]

            bang_addrs = []

            # find '#' values on the heap
            for address in proc_layer.scan(self.context,
                                           scanners.BytesScanner(b"#"),
                                           sections = task.get_process_memory_sections(self.context, self.config['darwin'], rw_no_file = True)):
                bang_addrs.append(struct.pack(pack_format, address))

            history_entries = []

            for address, _ in proc_layer.scan(self.context,
                                              scanners.MultiStringScanner(bang_addrs),
                                              sections = task.get_process_memory_sections(self.context, self.config['darwin'], rw_no_file = True)):
                hist = self.context.object(bash_table_name + constants.BANG + "hist_entry",
                                           offset = address - ts_offset,
                                           layer_name = proc_layer_name)

                if hist.is_valid():
                    history_entries.append(hist)

            for hist in sorted(history_entries, key = lambda x: x.get_time_as_integer()):
                yield (0, (int(task.p_pid), task_name, hist.get_time_object(), hist.get_command()))

    def run(self):
        filter = pslist.PsList.create_filter([self.config.get('pid', None)])

        plugin = pslist.PsList.list_tasks

        return renderers.TreeGrid(
            [("PID", int),
             ("Process", str),
             ("CommandTime", datetime.datetime),
             ("Command", str)],
            self._generator(plugin(self.context,
                                   self.config['primary'],
                                   self.config['darwin'],
                                   filter = filter)))

    def generate_timeline(self):
        filter = pslist.PsList.create_filter([self.config.get('pid', None)])

        plugin = pslist.PsList.list_tasks

        for row in self._generator(plugin(self.context,
                                          self.config['primary'],
                                          self.config['darwin'],
                                          filter = filter)):
            _depth, row_data = row
            description = "{} ({}): \"{}\"".format(row_data[0], row_data[1], row_data[3])
            yield (description, timeliner.TimeLinerType.CREATED, row_data[2])
