import logging
import typing

from volatility.framework import interfaces, renderers, layers
from volatility.framework.configuration import requirements
from volatility.framework.interfaces import plugins
from volatility.framework.renderers import format_hints

try:
    import yara

    has_yara = True
except:
    has_yara = False

vollog = logging.getLogger(__name__)


class YaraScanner(interfaces.layers.ScannerInterface):

    # yara.Rules isn't exposed, so we can't type this properly
    def __init__(self, rules) -> None:
        super().__init__()
        self._rules = rules

    def __call__(self, data: bytes, data_offset: int) -> typing.Iterable[typing.Tuple[int, str]]:
        for match in self._rules.match(data):
            for offset, name, value in match.strings:
                yield (offset + data_offset, name)


class YaraScan(plugins.PluginInterface):
    """Runs all relevant plugins that provide time related information and orders the results by time"""

    @classmethod
    def get_requirements(cls) -> typing.List[interfaces.configuration.RequirementInterface]:
        return [requirements.TranslationLayerRequirement(name = 'primary',
                                                         description = "Primary kernel address space",
                                                         architectures = ["Intel32", "Intel64"]),
                requirements.BooleanRequirement(name = "all",
                                                description = "Scan both process and kernel memory",
                                                default = False),
                requirements.BooleanRequirement(name = "insensitive",
                                                description = "Makes the search case insensitive",
                                                default = False),
                requirements.BooleanRequirement(name = "kernel",
                                                description = "Scan kernel modules",
                                                default = False),
                requirements.BooleanRequirement(name = "wide",
                                                description = "Match wide (unicode) strings",
                                                default = False),
                requirements.StringRequirement(name = "yara_rules",
                                               description = "Yara rules (as a string)",
                                               optional = True),
                requirements.URIRequirement(name = "yara_file",
                                            description = "Yara rules (as a file)",
                                            optional = True),
                requirements.IntRequirement(name = "max_size",
                                            default = 0x40000000,
                                            description = "Set the maximum size (default is 1GB)")
                ]

    def _generator(self):

        layer = self.context.memory[self.config['primary']]
        rules = None
        if self.config.get('yara_rules', None) is not None:
            rule = self.config['yara_rules']
            if rule[0] not in ["{", "/"]:
                rule = '"{}"'.format(rule)
            if self.config.get('case', False):
                rule += " nocase"
            if self.config.get('wide', False):
                rule += " wide ascii"
            rules = yara.compile(sources = {'n': 'rule r1 {{strings: $a = {} condition: $a}}'.format(rule)})
        elif self.config.get('yara_file', None) is not None:
            rules = yara.compile(file = layers.ResourceAccessor().open(self.config['yara_file'], "rb"))
        else:
            vollog.error("No yara rules, nor yara rules file were specified")

        for offset, name in layer.scan(YaraScanner(rules = rules), max_address = self.config['max_size']):
            yield format_hints.Hex(offset), name

    def run(self):
        if not has_yara:
            vollog.error("Please install Yara from https://plusvic.github.io/yara/")

        return renderers.TreeGrid([('Offset', format_hints.Hex),
                                   ('Rule', str)], self._generator())
