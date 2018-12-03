"""A module for scanning translation layers looking for Windows PDB records from loaded PE files.

    This module contains a standalone scanner, and also a :class:`~volatility.framework.interfaces.layers.ScannerInterface`
    based scanner for use within the framework by calling :func:`~volatility.framework.interfaces.layers.DataLayerInterface.scan`.
"""

import logging
import math
import os
import struct
import typing

from volatility.framework import constants, exceptions, layers, validity
from volatility.framework.configuration import requirements
from volatility.framework.interfaces import configuration
from volatility.framework.layers import intel, scanners
from volatility.framework.symbols import intermed, native

if __name__ == "__main__":
    import sys

    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from volatility.framework import interfaces

vollog = logging.getLogger(__name__)

ValidKernelsType = typing.Dict[str, typing.Tuple[int, typing.Dict]]
KernelsType = typing.Iterable[typing.Dict[str, typing.Any]]


class PdbSignatureScanner(interfaces.layers.ScannerInterface):
    """A :class:`~volatility.framework.interfaces.layers.ScannerInterface` based scanner use to identify Windows PDB records

    Args:
        pdb_names: A list of bytestrings, used to match pdb signatures against the pdb names within the records.

    .. note:: The pdb_names must be a list of byte strings, unicode strs will not match against the data scanned
    """
    overlap = 0x4000
    """The size of overlap needed for the signature to ensure data cannot hide between two scanned chunks"""
    thread_safe = True
    """Determines whether the scanner accesses global variables in a thread safe manner (for use with :mod:`multiprocessing`)"""

    _RSDS_format = struct.Struct("<16BI")

    def __init__(self, pdb_names: typing.List[bytes]) -> None:
        super().__init__()
        self._pdb_names = pdb_names

    def __call__(self, data: bytes, data_offset: int) \
            -> typing.Generator[typing.Tuple[str, typing.Any, bytes, int], None, None]:
        sig = data.find(b"RSDS")
        while sig >= 0:
            null = data.find(b'\0', sig + 4 + self._RSDS_format.size)
            if null > -1:
                if (null - sig - self._RSDS_format.size) <= 100:
                    name_offset = sig + 4 + self._RSDS_format.size
                    pdb_name = data[name_offset:null]
                    if pdb_name in self._pdb_names:

                        ## this ordering is intentional due to mixed endianness in the GUID
                        (g3, g2, g1, g0, g5, g4, g7, g6, g8, g9, ga, gb, gc, gd, ge, gf, a) = \
                            self._RSDS_format.unpack(data[sig + 4:name_offset])

                        GUID = (16 * '{:02X}').format(g0, g1, g2, g3, g4, g5, g6, g7, g8, g9, ga, gb, gc, gd, ge, gf)
                        if sig < self.chunk_size:
                            yield (GUID, a, pdb_name, data_offset + sig)
            sig = data.find(b"RSDS", sig + 1)


def scan(ctx: interfaces.context.ContextInterface,
         layer_name: str,
         page_size: int,
         progress_callback: validity.ProgressCallback = None,
         start: typing.Optional[int] = None,
         end: typing.Optional[int] = None) \
        -> typing.Generator[typing.Dict[str, typing.Optional[typing.Union[bytes, str, int]]], None, None]:
    """Scans through `layer_name` at `ctx` looking for RSDS headers that indicate one of four common pdb kernel names
       (as listed in `self.pdb_names`) and returns the tuple (GUID, age, pdb_name, signature_offset, mz_offset)

       .. note:: This is automagical and therefore not guaranteed to provide correct results.

       The UI should always provide the user an opportunity to specify the
       appropriate types and PDB values themselves
    """
    min_pfn = 0
    pdb_names = [bytes(name + ".pdb", "utf-8") for name in constants.windows.KERNEL_MODULE_NAMES]

    if start is None:
        start = ctx.memory[layer_name].minimum_address
    if end is None:
        end = ctx.memory[layer_name].maximum_address

    for (GUID, age, pdb_name, signature_offset) in ctx.memory[layer_name].scan(ctx, PdbSignatureScanner(pdb_names),
                                                                               progress_callback = progress_callback,
                                                                               sections = [(start, end - start)]):
        mz_offset = None
        sig_pfn = signature_offset // page_size

        for i in range(sig_pfn, min_pfn, -1):
            if not ctx.memory[layer_name].is_valid(i * page_size, 2):
                break

            data = ctx.memory[layer_name].read(i * page_size, 2)
            if data == b'MZ':
                mz_offset = i * page_size
                break
        min_pfn = sig_pfn

        yield {'GUID': GUID,
               'age': age,
               'pdb_name': str(pdb_name, "utf-8"),
               'signature_offset': signature_offset,
               'mz_offset': mz_offset}


class KernelPDBScanner(interfaces.automagic.AutomagicInterface):
    """Windows symbol loader based on PDB signatures

    An Automagic object that looks for all Intel translation layers and scans each of them for a pdb signature.
    When found, a search for a corresponding Intermediate Format data file is carried out and if found an appropriate
    symbol space is automatically loaded.

    Once a specific kernel PDB signature has been found, a virtual address for the loaded kernel is determined
    by one of two methods.  The first method assumes a specific mapping from the kernel's physical address to its
    virtual address (typically the kernel is loaded at its physical location plus a specific offset).  The second method
    searches for a particular structure that lists the kernel module's virtual address, its size (not checked) and the
    module's name.  This value is then used if one was not found using the previous method.
    """
    priority = 30

    # Make sure uncompressed/outside-framework takes precedence, so users can overload.
    prefixes = [os.path.join("..", "..", "..", "symbols", "windows"),
                os.path.join("..", "..", "symbols", "windows")]
    """Provides a list of prefixes that are searched when locating Intermediate Format data files"""
    suffixes = ['.json', '.json.xz']
    """Provides a list of supported suffixes for Intermediate Format data files"""

    def recurse_pdb_finder(self,
                           context: interfaces.context.ContextInterface,
                           config_path: str,
                           requirement: interfaces.configuration.RequirementInterface,
                           progress_callback: validity.ProgressCallback = None) \
            -> typing.Dict[str, KernelsType]:
        """Traverses the requirement tree, rooted at `requirement` looking for virtual layers that might contain a windows PDB.

        Returns a list of possible kernel locations in the physical memory

        Args:
            context: The context in which the `requirement` lives
            config_path: The path within the `context` for the `requirement`'s configuration variables
            requirement: The root of the requirement tree to search for :class:~`volatility.framework.interfaces.layers.TranslationLayerRequirement` objects to scan

        Returns:
            A list of (layer_name, scan_results)
        """
        sub_config_path = interfaces.configuration.path_join(config_path, requirement.name)
        results = {}  # type: typing.Dict[str, KernelsType]
        if isinstance(requirement, requirements.TranslationLayerRequirement):
            # Check for symbols in this layer
            # FIXME: optionally allow a full (slow) scan
            # FIXME: Determine the physical layer no matter the virtual layer
            virtual_layer_name = context.config.get(sub_config_path, None)
            layer_name = context.config.get(interfaces.configuration.path_join(sub_config_path, "memory_layer"), None)
            if layer_name and virtual_layer_name:
                memlayer = context.memory[virtual_layer_name]
                if isinstance(memlayer, intel.Intel):
                    page_size = memlayer.page_size  # type: int
                    results = {virtual_layer_name: scan(context,
                                                        layer_name,
                                                        page_size,
                                                        progress_callback = progress_callback)}
        else:
            for subreq in requirement.requirements.values():
                results.update(self.recurse_pdb_finder(context, sub_config_path, subreq))
        return results

    def recurse_symbol_fulfiller(self,
                                 context: interfaces.context.ContextInterface,
                                 valid_kernels: ValidKernelsType) -> None:
        """Fulfills the SymbolRequirements in `self._symbol_requirements` found by the `recurse_symbol_requirements`.

        This pass will construct any requirements that may need it in the context it was passed

        Args:
            context: Context on which to operate
        """
        join = interfaces.configuration.path_join
        for sub_config_path, requirement in self._symbol_requirements:
            # TODO: Potentially think about multiple symbol requirements in both the same and different levels of the requirement tree
            # TODO: Consider whether a single found kernel can fulfill multiple requirements
            suffix = ".json"
            if valid_kernels:
                # TODO: Check that the symbols for this kernel will fulfill the requirement
                kernel = None
                for virtual_layer in valid_kernels:
                    _kvo, kernel = valid_kernels[virtual_layer]
                    filter_string = os.path.join(kernel['pdb_name'], kernel['GUID'] + "-" + str(kernel['age']))
                    # Take the first result of search for the intermediate file
                    for value in intermed.IntermediateSymbolTable.file_symbol_url("windows", filter_string):
                        isf_path = value
                        break
                    else:
                        isf_path = ''
                    if isf_path:
                        vollog.debug("Using symbol library: {}".format(filter_string))
                        clazz = "volatility.framework.symbols.windows.WindowsKernelIntermedSymbols"
                        # Set the discovered options
                        context.config[join(sub_config_path, "class")] = clazz
                        context.config[join(sub_config_path, "isf_url")] = isf_path
                        # Construct the appropriate symbol table
                        config_path = interfaces.configuration.parent_path(sub_config_path)
                        requirement.construct(context, config_path)
                        break
                    else:
                        vollog.debug("Required symbol library path not found: {}".format(filter_string))
                else:
                    vollog.debug("No suitable kernel pdb signature found")

    def set_kernel_virtual_offset(self,
                                  context: interfaces.context.ContextInterface,
                                  valid_kernels: ValidKernelsType) -> None:
        """Traverses the requirement tree, looking for kernel_virtual_offset values that may need setting and sets
        it based on the previously identified `valid_kernels`.

        Args:
            context: Context on which to operate and provide the kernel virtual offset
        """
        for virtual_layer in valid_kernels:
            # Sit the virtual offset under the TranslationLayer it applies to
            kvo_path = interfaces.configuration.path_join(context.memory[virtual_layer].config_path,
                                                          'kernel_virtual_offset')
            kvo, kernel = valid_kernels[virtual_layer]
            context.config[kvo_path] = kvo
            vollog.debug("Setting kernel_virtual_offset to {}".format(hex(kvo)))

    def get_physical_layer_name(self, context, vlayer):
        return context.config.get(interfaces.configuration.path_join(vlayer.config_path, 'memory_layer'), None)

    def method_fixed_mapping(self,
                             context: interfaces.context.ContextInterface,
                             vlayer: layers.intel.Intel,
                             kernels: KernelsType,
                             progress_callback: validity.ProgressCallback = None) -> ValidKernelsType:
        # TODO: Verify this is a windows image
        valid_kernels = {}
        virtual_layer_name = vlayer.name
        physical_layer_name = self.get_physical_layer_name(context, vlayer)
        kvo_path = interfaces.configuration.path_join(vlayer.config_path, 'kernel_virtual_offset')
        for kernel in kernels:
            # It seems the kernel is loaded at a fixed mapping (presumably because the memory manager hasn't started yet)
            if kernel['mz_offset'] is None:
                # Rule out kernels that couldn't find a suitable MZ header
                continue
            if vlayer.bits_per_register == 64:
                kvo = kernel['mz_offset'] + (31 << int(math.ceil(math.log2(vlayer.maximum_address + 1)) - 5))
            else:
                kvo = kernel['mz_offset'] + (1 << (vlayer.bits_per_register - 1))
            try:
                kvp = vlayer.mapping(kvo, 0)
                if (any([(p == kernel['mz_offset'] and l == physical_layer_name) for (_, p, _, l) in
                         kvp])):
                    valid_kernels[virtual_layer_name] = (kvo, kernel)
                    # Sit the virtual offset under the TranslationLayer it applies to
                    context.config[kvo_path] = kvo
                    vollog.debug("Setting kernel_virtual_offset to {}".format(hex(kvo)))
                    break
                else:
                    vollog.debug(
                        "Potential kernel_virtual_offset did not map to expected location: {}".format(hex(kvo)))
            except exceptions.InvalidAddressException:
                vollog.debug("Potential kernel_virtual_offset caused a page fault: {}".format(hex(kvo)))
        return valid_kernels

    def method_module_offset(self,
                             context: interfaces.context.ContextInterface,
                             vlayer: layers.intel.Intel,
                             kernels: KernelsType,
                             progress_callback: validity.ProgressCallback = None) -> ValidKernelsType:
        """Method for finding a suitable kernel offset based on a module table"""
        valid_kernels = {}
        vollog.debug("Kernel base randomized, searching layer for base address offset")
        # If we're here, chances are high we're in a Win10 x64 image with kernel base randomization
        virtual_layer_name = vlayer.name
        physical_layer_name = self.get_physical_layer_name(context, vlayer)
        physical_layer = context.memory[physical_layer_name]
        # TODO:  On older windows, this might be \WINDOWS\system32\nt rather than \SystemRoot\system32\nt
        results = physical_layer.scan(context, scanners.BytesScanner(b"\\SystemRoot\\system32\\nt"),
                                      progress_callback = progress_callback)
        seen = set()  # type: typing.Set[int]
        # Because this will launch a scan of the virtual layer, we want to be careful
        for result in results:
            # TODO: Identify the specific structure we're finding and document this a bit better
            pointer = context.object("pdbscan!unsigned long long",
                                     offset = (result - 16 - int(vlayer.bits_per_register / 8)),
                                     layer_name = physical_layer_name)
            address = pointer & vlayer.address_mask
            if address in seen:
                continue
            seen.add(address)
            for kernel in kernels:
                try:
                    if vlayer.translate(address)[0] == kernel['mz_offset']:
                        valid_kernels[virtual_layer_name] = (address, kernel)
                except exceptions.InvalidAddressException:
                    pass
            if valid_kernels:
                break
        return valid_kernels

    def method_kdbg_offset(self,
                           context: interfaces.context.ContextInterface,
                           vlayer: layers.intel.Intel,
                           kernels: KernelsType,
                           progress_callback: validity.ProgressCallback = None) -> ValidKernelsType:
        valid_kernels = {}
        vollog.debug("Kernel base randomized, using KDBG structure for kernel offset")
        virtual_layer_name = vlayer.name
        physical_layer_name = self.get_physical_layer_name(context, vlayer)
        physical_layer = context.memory[physical_layer_name]
        results = physical_layer.scan(context, scanners.BytesScanner(b"KDBG"), progress_callback = progress_callback)

        seen = set()  # type: typing.Set[int]
        for result in results:
            # TODO: Identify the specific structure we're finding and document this a bit better
            pointer = context.object("pdbscan!unsigned long long",
                                     offset = result + 8,
                                     layer_name = physical_layer_name)
            address = pointer & vlayer.address_mask
            if address in seen:
                continue
            seen.add(address)
            for kernel in kernels:
                try:
                    if vlayer.translate(address)[0] == kernel['mz_offset']:
                        valid_kernels[virtual_layer_name] = (address, kernel)
                except exceptions.InvalidAddressException:
                    pass
            if valid_kernels:
                break

        return valid_kernels

    # List of methods to be run, in order, to determine the valid kernels
    methods = [method_fixed_mapping,
               method_kdbg_offset,
               method_module_offset]

    def determine_valid_kernels(self,
                                context: interfaces.context.ContextInterface,
                                potential_kernels: typing.Dict[str, KernelsType],
                                progress_callback: validity.ProgressCallback = None) -> ValidKernelsType:
        """Runs through the identified potential kernels and verifies their suitability

        This carries out a scan using the pdb_signature scanner on a physical layer.  It uses the
        results of the scan to determine the virtual offset of the kernel.  On early windows implementations
        there is a fixed mapping between the physical and virtual addresses of the kernel.  On more recent versions
        a search is conducted for a structure that will identify the kernel's virtual offset.

        Args:
            context: Context on which to operate
            potential_kernels: Dictionary containing `GUID`, `age`, `pdb_name` and `mz_offset` keys
            progress_callback: Function taking a percentage and optional description to be called during expensive computations to indicate progress

        Returns:
            A dictionary of valid kernels
        """
        valid_kernels = {}  # type: ValidKernelsType
        for virtual_layer_name in potential_kernels:
            kernels = list(potential_kernels[virtual_layer_name])
            vlayer = context.memory.get(virtual_layer_name, None)
            if isinstance(vlayer, layers.intel.Intel):
                for method in self.methods:
                    valid_kernels = method(self, context, vlayer, kernels, progress_callback)
                    if valid_kernels:
                        break
        if not valid_kernels:
            vollog.info("No suitable kernels found during pdbscan")
        return valid_kernels

    def __call__(self,
                 context: interfaces.context.ContextInterface,
                 config_path: str,
                 requirement: interfaces.configuration.RequirementInterface,
                 progress_callback: validity.ProgressCallback = None) -> None:
        if requirement.unsatisfied(context, config_path):
            if "pdbscan" not in context.symbol_space:
                context.symbol_space.append(native.NativeTable("pdbscan", native.std_ctypes))
            # TODO: check if this is a windows symbol requirement, otherwise ignore it
            self._symbol_requirements = self.find_requirements(context,
                                                               config_path,
                                                               requirement,
                                                               requirements.SymbolRequirement)
            for sub_config_path, symbol_req in self._symbol_requirements:
                parent_path = configuration.parent_path(sub_config_path)
                if symbol_req.unsatisfied(context, parent_path):
                    potential_kernels = self.recurse_pdb_finder(context, config_path, requirement, progress_callback)
                    valid_kernels = self.determine_valid_kernels(context, potential_kernels, progress_callback)
                    if valid_kernels:
                        self.recurse_symbol_fulfiller(context, valid_kernels)
                        self.set_kernel_virtual_offset(context, valid_kernels)
