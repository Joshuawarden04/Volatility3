from volatility.framework import renderers
from volatility.framework.interfaces import plugins
from volatility.framework.objects import utility
from volatility.plugins.mac import pslist
from volatility.framework.configuration import requirements

class PsTree(plugins.PluginInterface):
    """Plugin for listing processes in a tree based on their parent process ID """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._processes = {}
        self._levels = {}
        self._children = {}

    @classmethod
    def get_requirements(cls):
        return [requirements.TranslationLayerRequirement(name = 'primary',
                                                         description = 'Kernel Address Space',
                                                         architectures = ["Intel32", "Intel64"]),
                requirements.SymbolRequirement(name = "darwin",
                                               description = "Mac Kernel")]

    def find_level(self, pid):
        """Finds how deep the pid is in the processes list"""
        seen = set([])
        seen.add(pid)
        level = 0
        proc = self._processes.get(pid, None)
        while proc is not None and proc.vol.offset != 0 and proc.p_ppid != 0 and proc.p_ppid not in seen:
            ppid = int(proc.p_ppid)
            child_list = self._children.get(ppid, set([]))
            child_list.add(proc.p_pid)
            self._children[ppid] = child_list
            proc = self._processes.get(ppid, None)
            level += 1
        self._levels[pid] = level

    def _generator(self):
        """Generates the """
        for proc in pslist.PsList.list_tasks(self.context, self.config['primary'], self.config['darwin']):
            self._processes[proc.p_pid] = proc

        # Build the child/level maps
        for pid in self._processes:
            self.find_level(pid)

        def yield_processes(pid):
            proc = self._processes[pid]
            row = (proc.p_pid,
                   proc.p_ppid,
                   utility.array_to_string(proc.p_comm))

            yield (self._levels[pid] - 1, row)
            for child_pid in self._children.get(pid, []):
                yield from yield_processes(child_pid)

        for pid in self._levels:
            if self._levels[pid] == 1:
                yield from yield_processes(pid)
            

    def run(self):
        return renderers.TreeGrid([("PID", int),
                                   ("PPID", int),
                                   ("COMM", str)],
                                  self._generator())




