import logging
import struct
import typing

from volatility.framework import interfaces, constants, validity, layers
from volatility.framework import symbols
from volatility.framework.automagic import symbol_cache, symbol_finder
from volatility.framework.layers import intel, scanners
from volatility.framework.symbols import mac

vollog = logging.getLogger(__name__)


class MacBannerCache(symbol_cache.SymbolBannerCache):
    """Caches the banners found in the Mac symbol files"""
    os = "mac"
    symbol_name = "version"
    banner_path = constants.MAC_BANNERS_PATH


class MacSymbolFinder(symbol_finder.SymbolFinder):
    """Mac symbol loader based on uname signature strings"""

    banner_config_key = 'mac_banner'
    banner_cache = MacBannerCache
    symbol_class = "volatility.framework.symbols.mac.MacKernelIntermedSymbols"


class MacintelStacker(interfaces.automagic.StackerLayerInterface):
    stack_order = 12

    @classmethod
    def stack(cls,
              context: interfaces.context.ContextInterface,
              layer_name: str,
              progress_callback: validity.ProgressCallback = None) \
            -> typing.Optional[interfaces.layers.DataLayerInterface]:
        """Attempts to identify mac within this layer"""
        # Bail out by default unless we can stack properly
        layer = context.memory[layer_name]
        new_layer = None
        join = interfaces.configuration.path_join

        # Never stack on top of an intel layer
        # FIXME: Find a way to improve this check
        if isinstance(layer, intel.Intel):
            return None

        mac_banners = MacBannerCache.load_banners()

        for banner_offset, banner in layer.scan(context = context,
                                                scanner = scanners.MultiStringScanner([x for x in mac_banners if x]),
                                                progress_callback = progress_callback):
            dtb = None
            vollog.debug("Identified banner: {}".format(repr(banner)))

            symbol_files = mac_banners.get(banner, None)
            if symbol_files:
                isf_path = symbol_files[0]
                table_name = context.symbol_space.free_table_name('MacintelStacker')
                table = mac.MacKernelIntermedSymbols(context = context,
                                                     config_path = join('temporary', table_name),
                                                     name = table_name,
                                                     isf_url = isf_path)
                context.symbol_space.append(table)
                kaslr_shift = MacUtilities.find_aslr(context = context,
                                                     symbol_table = table_name,
                                                     layer_name = layer_name,
                                                     compare_banner = banner,
                                                     compare_banner_offset = banner_offset,
                                                     progress_callback = progress_callback)

                bootpml4_addr = MacUtilities.virtual_to_physical_address(table.get_symbol("BootPML4").address +
                                                                         kaslr_shift)

                new_layer_name = context.memory.free_layer_name("MacDTBTempLayer")
                config_path = join("automagic", "MacIntelHelper", new_layer_name)
                context.config[join(config_path, "memory_layer")] = layer_name
                context.config[join(config_path, "page_map_offset")] = bootpml4_addr

                layer = layers.intel.Intel32e(context, config_path = config_path,
                                              name = new_layer_name, metadata = {'os': 'Mac'})

                idlepml4_ptr = table.get_symbol("IdlePML4").address + kaslr_shift
                idlepml4_str = layer.read(idlepml4_ptr, 4)
                idlepml4_addr = struct.unpack("<I", idlepml4_str)[0]

                dtb = idlepml4_addr

                # Build the new layer
                new_layer_name = context.memory.free_layer_name("IntelLayer")
                config_path = join("automagic", "MacIntelHelper", new_layer_name)
                context.config[join(config_path, "memory_layer")] = layer_name
                context.config[join(config_path, "page_map_offset")] = dtb
                context.config[join(config_path, MacSymbolFinder.banner_config_key)] = str(banner, 'latin-1')

                new_layer = intel.Intel32e(context, config_path = config_path, name = new_layer_name)

            if new_layer and dtb:
                vollog.debug("DTB was found at: 0x{:0x}".format(dtb))
                return new_layer
        return None


class MacUtilities(object):
    """Class with multiple useful mac functions"""

    @classmethod
    def aslr_mask_symbol_table(cls,
                               context: interfaces.context.ContextInterface,
                               symbol_table: str,
                               layer_name: str,
                               aslr_shift = 0):

        sym_table = context.symbol_space[symbol_table]
        sym_layer = context.memory[layer_name]

        if aslr_shift == 0:
            if not isinstance(sym_layer, layers.intel.Intel):
                raise TypeError("Layer name {} is not an intel space")
            aslr_layer = sym_layer.config['memory_layer']
            _, aslr_shift = cls.find_aslr(context, symbol_table, aslr_layer)

        symbols.mask_symbol_table(sym_table, sym_layer.address_mask, aslr_shift)

    @classmethod
    def _scan_generator(cls, context, layer_name, progress_callback):
        darwin_signature = b"Darwin Kernel Version \d{1,3}\.\d{1,3}\.\d{1,3}: [^\x00]+\x00"

        for offset in context.memory[layer_name].scan(scanner = scanners.RegExScanner(darwin_signature),
                                                      context = context, progress_callback = progress_callback):

            banner = context.memory[layer_name].read(offset, 128)

            idx = banner.find(b"\x00")
            if idx != -1:
                banner = banner[:idx]

            yield offset, banner

    @classmethod
    def find_aslr(cls,
                  context: interfaces.context.ContextInterface,
                  symbol_table: str,
                  layer_name: str,
                  compare_banner: str = "",
                  compare_banner_offset: int = 0,
                  progress_callback: validity.ProgressCallback = None) \
            -> typing.Tuple[int, int]:
        """Determines the offset of the actual DTB in physical space and its symbol offset"""
        version_symbol = symbol_table + constants.BANG + 'version'
        version_json_address = context.symbol_space.get_symbol(version_symbol).address

        version_major_symbol = symbol_table + constants.BANG + 'version_major'
        version_major_json_address = context.symbol_space.get_symbol(version_major_symbol).address
        version_major_phys_offset = MacUtilities.virtual_to_physical_address(version_major_json_address)

        version_minor_symbol = symbol_table + constants.BANG + 'version_minor'
        version_minor_json_address = context.symbol_space.get_symbol(version_minor_symbol).address
        version_minor_phys_offset = MacUtilities.virtual_to_physical_address(version_minor_json_address)

        if compare_banner_offset == 0 or compare_banner == "":
            offset_generator = cls._scan_generator(context, layer_name, progress_callback)
        else:
            offset_generator = [(compare_banner_offset, compare_banner)]

        aslr_shift = 0

        for offset, banner in offset_generator:
            banner_major, banner_minor = [int(x) for x in banner[22:].split(b".")[0:2]]

            tmp_aslr_shift = offset - cls.virtual_to_physical_address(version_json_address)

            major_string = context.memory[layer_name].read(version_major_phys_offset + tmp_aslr_shift, 4)
            major = struct.unpack("<I", major_string)[0]

            if major != banner_major:
                continue

            minor_string = context.memory[layer_name].read(version_minor_phys_offset + tmp_aslr_shift, 4)
            minor = struct.unpack("<I", minor_string)[0]

            if minor != banner_minor:
                continue

            if aslr_shift & 0xfff != 0:
                continue

            aslr_shift = tmp_aslr_shift & 0xffffffff
            break

        vollog.debug("Mac ASLR shift value determined: {:0x}".format(aslr_shift))

        return aslr_shift

    @classmethod
    def virtual_to_physical_address(cls, addr: int) -> int:
        """Converts a virtual mac address to a physical one (does not account of ASLR)"""
        return addr - 0xffffff8000000000
