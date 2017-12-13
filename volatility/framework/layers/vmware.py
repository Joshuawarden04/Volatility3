import os
import struct

from volatility.framework import interfaces
from volatility.framework.configuration import requirements
from volatility.framework.layers import physical, segmented
from volatility.framework.symbols import native


class VmwareLayer(segmented.SegmentedLayer):
    provides = {"type": "physical"}
    priority = 22

    header_structure = "<4sII"
    group_structure = "64sQQ"

    def __init__(self, context, config_path, name):
        # Construct these so we can use self.config
        self._context = context
        self._config_path = config_path
        self._page_size = 0x1000
        self._base_layer, self._meta_layer = self.config["base_layer"], self.config["meta_layer"]
        # Then call the super, which will call load_segments (which needs the base_layer before it'll work)
        super().__init__(context, config_path = config_path, name = name)

    def _load_segments(self):
        """Loads up the segments from the meta_layer"""
        self._read_header()

    def _read_header(self):
        """Checks the vmware header to make sure it's valid"""
        if "vmware" not in self._context.symbol_space:
            self._context.symbol_space.append(native.NativeTable("vmware", native.std_ctypes))

        meta_layer = self.context.memory.get(self._meta_layer, None)
        header_size = struct.calcsize(self.header_structure)
        data = meta_layer.read(0, header_size)
        magic, unknown, groupCount = struct.unpack(self.header_structure, data)
        if magic not in [b"\xD2\xBE\xD2\xBE"]:
            raise ValueError("Wrong magic bytes for Vmware layer: {}".format(repr(magic)))

        # TODO: Change certain structure sizes based on the version
        version = magic[1] & 0xf

        group_size = struct.calcsize(self.group_structure)

        groups = {}
        for group in range(groupCount):
            name, tag_location, _unknown = struct.unpack(self.group_structure,
                                                         meta_layer.read(header_size + (group * group_size),
                                                                         group_size))
            name = name.rstrip(b"\x00")
            groups[name] = tag_location
        memory = groups[b"memory"]

        tags_read = False
        offset = memory
        tags = {}
        index_len = self._context.symbol_space.get_type("vmware!unsigned int").size
        while not tags_read:
            flags = ord(meta_layer.read(offset, 1))
            name_len = ord(meta_layer.read(offset + 1, 1))
            tags_read = (flags == 0) and (name_len == 0)
            if not tags_read:
                name = self._context.object("vmware!string", layer_name = self._meta_layer, offset = offset + 2,
                                            max_length = name_len)
                indicies_len = (flags >> 6) & 3
                indicies = []
                for index in range(indicies_len):
                    indicies.append(
                        self._context.object("vmware!unsigned int",
                                             offset = offset + name_len + 2 + (index * index_len),
                                             layer_name = self._meta_layer))
                data = self._context.object("vmware!unsigned int", layer_name = self._meta_layer,
                                            offset = offset + 2 + name_len + (indicies_len * index_len))
                tags[(name, tuple(indicies))] = (flags, data)
                offset += 2 + name_len + (indicies_len * index_len) + self._context.symbol_space.get_type(
                    "vmware!unsigned int").size

        if tags[("regionsCount", ())][1] == 0:
            raise ValueError("VMware VMEM is not split into regions")
        for region in range(tags[("regionsCount", ())][1]):
            offset = tags[("regionPPN", (region,))][1] * self._page_size
            mapped_offset = tags[("regionPageNum", (region,))][1] * self._page_size
            length = tags[("regionSize", (region,))][1] * self._page_size
            self._segments.append((offset, mapped_offset, length))

    @property
    def dependencies(self):
        return [self._base_layer, self._meta_layer]

    @classmethod
    def get_requirements(cls):
        """This vmware translation layer always requires a separate metadata layer"""
        return [requirements.TranslationLayerRequirement(name = 'base_layer',
                                                         optional = False),
                requirements.TranslationLayerRequirement(name = 'meta_layer',
                                                         optional = False)
                ]


class VmwareStacker(interfaces.automagic.StackerLayerInterface):
    @classmethod
    def stack(cls, context, layer_name, progress_callback = None):
        """Attempt to stack this based on the starting information"""
        if not isinstance(context.memory[layer_name], physical.FileLayer):
            return
        location = context.memory[layer_name].location
        if location.endswith(".vmem"):
            vmss = location[:-5] + ".vmss"
            vmsn = location[:-5] + ".vmsn"
            current_layer_name = context.memory.free_layer_name("VmwareMetaLayer")
            current_config_path = interfaces.configuration.path_join("automagic", "layer_stacker", "stack",
                                                                     current_layer_name)
            if os.path.exists(vmss):
                context.config[interfaces.configuration.path_join(current_config_path, "location")] = vmss
                context.memory.add_layer(physical.FileLayer(context, current_config_path, current_layer_name))
            elif os.path.exists(vmsn):
                context.config[interfaces.configuration.path_join(current_config_path, "location")] = vmss
                context.memory.add_layer(physical.FileLayer(context, current_config_path, current_layer_name))
            else:
                return
            new_layer_name = context.memory.free_layer_name("VmwareLayer")
            context.config[interfaces.configuration.path_join(current_config_path, "base_layer")] = layer_name
            context.config[
                interfaces.configuration.path_join(current_config_path, "meta_layer")] = current_layer_name
            new_layer = VmwareLayer(context, current_config_path, new_layer_name)
            return new_layer
