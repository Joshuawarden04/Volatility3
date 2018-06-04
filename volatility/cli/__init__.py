"""A CommandLine User Interface for the volatility framework

   User interfaces make use of the framework to:
    * determine available plugins
    * request necessary information for those plugins from the user
    * determine what "automagic" modules will be used to populate information the user does not provide
    * run the plugin
    * display the results
"""

import argparse
import inspect
import json
import logging
import os
import sys
import typing
from urllib import request, parse

import volatility.framework
import volatility.framework.configuration.requirements
import volatility.plugins
from volatility import framework
from volatility.cli import text_renderer
from volatility.framework import automagic, constants, contexts, interfaces, exceptions
from volatility.framework.configuration import requirements

# Make sure we log everything

vollog = logging.getLogger()
vollog.setLevel(0)
# Trim the console down by default
console = logging.StreamHandler()
console.setLevel(logging.WARNING)
formatter = logging.Formatter('%(levelname)-8s %(name)-12s: %(message)s')
console.setFormatter(formatter)
vollog.addHandler(console)


class PrintedProgress(object):
    def __init__(self):
        self._max_message_len = 0

    def __call__(self, progress, description = None):
        """ A sinmple function for providing text-based feedback

        .. warning:: Only for development use.

        :param progress: Percentage of progress of the current procedure
        :type progress: int or float
        """
        message = "\rProgress: {0: 7.2f}\t\t{1:}".format(round(progress, 2), description or '')
        message_len = len(message)
        self._max_message_len = max([self._max_message_len, message_len])
        print(message, end = ' ' * (self._max_message_len - message_len))


class MuteProgress(PrintedProgress):
    def __call__(self, progress, description = None):
        pass


class CommandLine(interfaces.plugins.FileConsumerInterface):
    """Constructs a command-line interface object for users to run plugins"""

    def __init__(self):
        self.output_dir = None

    def run(self):
        """Executes the command line module, taking the system arguments, determining the plugin to run and then running it"""
        sys.stdout.write("Volatility Framework {}\n".format(constants.PACKAGE_VERSION))

        volatility.framework.require_interface_version(0, 0, 0)

        parser = argparse.ArgumentParser(prog = 'volatility',
                                         description = "An open-source memory forensics framework")
        parser.add_argument("-c", "--config", help = "Load the configuration from a json file", default = None,
                            type = str)
        parser.add_argument("-e", "--extend", help = "Extend the configuration with a new (or changed) setting",
                            default = None,
                            action = 'append')
        parser.add_argument("-p", "--plugin-dirs", help = "Semi-colon separated list of paths to find plugins",
                            default = "", type = str)
        parser.add_argument("-v", "--verbosity", help = "Increase output verbosity", default = 0, action = "count")
        parser.add_argument("-o", "--output-dir", help = "Directory in which to output any generated files",
                            default = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')), type = str)
        parser.add_argument("-q", "--quiet", help = "Remove progress feedback", default = False, action = 'store_true')
        parser.add_argument("-l", "--log", help = "Log output to a file as well as the console", default = None,
                            type = str)
        parser.add_argument("-f", "--file", metavar = 'FILE', default = None, type = str,
                            help = "Shorthand for --single-location=file:// if single-location is not defined")
        parser.add_argument("--write-config", help = "Write configuration JSON file out to config.json",
                            default = False,
                            action = 'store_true')

        # We have to filter out help, otherwise parse_known_args will trigger the help message before having
        # processed the plugin choice or had the plugin subparser added.
        known_args = [arg for arg in sys.argv if arg != '--help' and arg != '-h']
        partial_args, _ = parser.parse_known_args(known_args)
        if partial_args.plugin_dirs:
            volatility.plugins.__path__ = partial_args.plugin_dirs.split(";") + constants.PLUGINS_PATH

        if partial_args.log:
            file_logger = logging.FileHandler(partial_args.log)
            file_logger.setLevel(0)
            file_formatter = logging.Formatter(datefmt = '%y-%m-%d %H:%M:%S',
                                               fmt = '%(asctime)s %(name)-12s %(levelname)-8s %(message)s')
            file_logger.setFormatter(file_formatter)
            vollog.addHandler(file_logger)
            vollog.info("Logging started")

        # Do the initialization
        ctx = contexts.Context()  # Construct a blank context
        framework.import_files(volatility.plugins)  # Will not log as console's default level is WARNING
        automagics = automagic.available(ctx)

        plugin_list = framework.list_plugins()

        seen_automagics = set()
        configurables_list = {}
        for amagic in automagics:
            if amagic in seen_automagics:
                continue
            seen_automagics.add(amagic)
            if isinstance(amagic, interfaces.configuration.ConfigurableInterface):
                self.populate_requirements_argparse(parser, amagic.__class__)
                configurables_list[amagic.__class__.__name__] = amagic

        subparser = parser.add_subparsers(title = "Plugins", dest = "plugin", action = HelpfulSubparserAction)
        for plugin in plugin_list:
            plugin_parser = subparser.add_parser(plugin, help = plugin_list[plugin].__doc__)
            self.populate_requirements_argparse(plugin_parser, plugin_list[plugin])
            configurables_list[plugin] = plugin_list[plugin]

        ###
        # PASS TO UI
        ###
        # Hand the plugin requirements over to the CLI (us) and let it construct the config tree

        # Run the argparser
        args = parser.parse_args()
        if args.plugin is None:
            parser.error("Please select a plugin to run")
        if args.verbosity < 3:
            console.setLevel(30 - (args.verbosity * 10))
        else:
            console.setLevel(10 - (args.verbosity - 2))

        vollog.log(constants.LOGLEVEL_VVV, "Cache directory used: {}".format(constants.CACHE_PATH))

        plugin = plugin_list[args.plugin]
        plugin_config_path = interfaces.configuration.path_join('plugins', plugin.__name__)

        # Special case the -f argument because people use is so frequently
        # It has to go here so it can be overridden by single-location if it's defined
        # NOTE: This will *BREAK* if LayerStacker, or the automagic configuration system, changes at all
        ###
        if args.file:
            file_name = os.path.abspath(args.file)
            if not os.path.exists(file_name):
                vollog.log(logging.INFO, "File does not exist: {}".format(file_name))
            else:
                single_location = "file:" + request.pathname2url(file_name)
                ctx.config['automagic.LayerStacker.single_location'] = single_location

        # UI fills in the config, here we load it from the config file and do it before we process the CL parameters
        if args.config:
            with open(args.config, "r") as f:
                json_val = json.load(f)
                ctx.config.splice(plugin_config_path, interfaces.configuration.HierarchicalDict(json_val))

        self.populate_config(ctx, configurables_list, args, plugin_config_path)

        if args.extend:
            for extension in args.extend:
                if '=' not in extension:
                    raise ValueError(
                        "Invalid extension (extensions must be of the format \"conf.path.value='value'\")")
                address, value = extension[:extension.find('=')], json.loads(extension[extension.find('=') + 1:])
                ctx.config[address] = value

        # It should be up to the UI to determine which automagics to run, so this is before BACK TO THE FRAMEWORK
        automagics = automagic.choose_automagic(automagics, plugin)
        self.output_dir = args.output_dir

        ###
        # BACK TO THE FRAMEWORK
        ###
        try:
            constructed = self.run_plugin(ctx,
                                          automagics,
                                          plugin,
                                          plugin_config_path,
                                          quiet = args.quiet,
                                          write_config = args.write_config)

            # Construct and run the plugin
            text_renderer.QuickTextRenderer().render(constructed.run())
        except UnsatisfiedException as excp:
            parser.exit(1, "Unable to validate the plugin requirements: {}\n".format(excp.unsatisfied))

    def run_plugin(self,
                   context: interfaces.context.ContextInterface,
                   automagics: typing.List[interfaces.automagic.AutomagicInterface],
                   plugin: typing.Type[interfaces.plugins.PluginInterface],
                   plugin_config_path: str,
                   write_config: bool = False,
                   quiet: bool = False):
        """Run the actual plugin based on the parameters

        Clever magic figures out how to fulfill each requirement that might not be fulfilled
        """
        progress_callback = PrintedProgress()
        if quiet:
            progress_callback = MuteProgress()
        errors = automagic.run(automagics, context, plugin, "plugins", progress_callback = progress_callback)

        # Check all the requirements and/or go back to the automagic step
        unsatisfied = plugin.unsatisfied(context, plugin_config_path)
        if unsatisfied:
            for error in errors:
                error_string = [x for x in error.format_exception_only()][-1]
                vollog.warning("Automagic exception occured: {}".format(error_string[:-1]))
                vollog.log(constants.LOGLEVEL_V, "".join(error.format(chain = True)))
            raise UnsatisfiedException(unsatisfied)

        print("\n\n")

        constructed = plugin(context, plugin_config_path, progress_callback = progress_callback)
        if write_config:
            vollog.debug("Writing out configuration data to config.json")
            with open("config.json", "w") as f:
                json.dump(dict(constructed.build_configuration()), f, sort_keys = True, indent = 2)
        constructed.set_file_consumer(self)
        return constructed

    def populate_config(self,
                        context: interfaces.context.ContextInterface,
                        configurables_list: typing.Dict[str, interfaces.configuration.ConfigurableInterface],
                        args: argparse.Namespace,
                        plugin_config_path: str):
        """Populate the context config based on the returned args
        We have already determined these elements must be descended from ConfigurableInterface"""
        vargs = vars(args)
        for configurable in configurables_list:
            for requirement in configurables_list[configurable].get_requirements():
                value = vargs.get(requirement.name, None)
                if value is not None:
                    if isinstance(requirement, requirements.URIRequirement):
                        if isinstance(value, str):
                            if not parse.urlparse(value).scheme:
                                if not os.path.exists(value):
                                    raise TypeError("Non-existant file {} passed to URIRequirement".format(value))
                                value = "file://" + request.pathname2url(os.path.abspath(value))
                    if isinstance(requirement, requirements.ListRequirement):
                        if not isinstance(value, list):
                            raise TypeError("Configuration for ListRequirement was not a list")
                        value = [requirement.element_type(x) for x in value]
                    if not inspect.isclass(configurables_list[configurable]):
                        config_path = configurables_list[configurable].config_path
                    else:
                        # We must be the plugin, so name it appropriately:
                        config_path = plugin_config_path
                    extended_path = interfaces.configuration.path_join(config_path, requirement.name)
                    context.config[extended_path] = value

    def consume_file(self, filedata: interfaces.plugins.FileInterface):
        """Consumes a file as produced by a plugin"""
        if self.output_dir is None:
            raise ValueError("Output directory has not been correctly specified")
        os.makedirs(self.output_dir, exist_ok = True)

        pref_name_array = filedata.preferred_filename.split('.')
        filename, extension = os.path.join(self.output_dir, '.'.join(pref_name_array[:-1])), pref_name_array[-1]
        output_filename = "{}.{}".format(filename, extension)

        if not os.path.exists(output_filename):
            with open(output_filename, "wb") as current_file:
                current_file.write(filedata.data.getvalue())
                vollog.log(logging.INFO, "Saved stored plugin file: {}".format(output_filename))
        else:
            vollog.warning("Refusing to overwrite an existing file: {}".format(output_filename))

    def populate_requirements_argparse(self,
                                       parser: typing.Union[argparse.ArgumentParser, argparse._ArgumentGroup],
                                       configurable: typing.Type[interfaces.configuration.ConfigurableInterface]):
        """Adds the plugin's simple requirements to the provided parser

        :param parser: The parser to add the plugin's (simple) requirements to
        :type parser: argparse.ArgumentParser
        :param configurable: The plugin object to pull the requirements from
        :type configurable: volatility.framework.interfaces.plugins.PluginInterface
        """
        if not issubclass(configurable, interfaces.configuration.ConfigurableInterface):
            raise TypeError("Expected ConfigurableInterface type, not: {}".format(type(configurable)))

        # Construct an argparse group

        for requirement in configurable.get_requirements():
            additional = {}  # type: typing.Dict[str, typing.Any]
            if not isinstance(requirement, interfaces.configuration.RequirementInterface):
                raise TypeError(
                    "Plugin contains requirements that are not RequirementInterfaces: {}".format(configurable.__name__))
            if isinstance(requirement, interfaces.configuration.InstanceRequirement):
                additional["type"] = requirement.instance_type
                if isinstance(requirement, requirements.IntRequirement):
                    additional["type"] = lambda x: int(x, 0)
                if isinstance(requirement, requirements.BooleanRequirement):
                    additional["action"] = "store_true"
                    if "type" in additional:
                        del additional["type"]
            elif isinstance(requirement, volatility.framework.configuration.requirements.ListRequirement):
                # This is a trick to generate a list of values
                additional["type"] = lambda x: x.split(',')
            elif isinstance(requirement, volatility.framework.configuration.requirements.ChoiceRequirement):
                additional["type"] = str
                additional["choices"] = requirement.choices
            else:
                continue
            parser.add_argument("--" + requirement.name.replace('_', '-'), help = requirement.description,
                                default = requirement.default, dest = requirement.name,
                                required = not requirement.optional, **additional)


# We shouldn't really steal a private member from argparse, but otherwise we're just duplicating code
class HelpfulSubparserAction(argparse._SubParsersAction):
    """Class to either select a unique plugin based on a substring, or identity the alternatives"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # We don't want the action self-check to kick in, so we remove the choices list, the check happens in __call__
        self.choices = None

    def __call__(self, parser, namespace, values, option_string = None):
        parser_name = values[0]
        arg_strings = values[1:]

        # set the parser name if requested
        if self.dest is not argparse.SUPPRESS:
            setattr(namespace, self.dest, parser_name)

        matched_parsers = [name for name in self._name_parser_map if parser_name in name]

        if len(matched_parsers) < 1:
            msg = 'invalid choice {} (choose from {})'.format(parser_name, ', '.join(self._name_parser_map))
            raise argparse.ArgumentError(self, msg)
        if len(matched_parsers) > 1:
            msg = 'plugin {} matches multiple plugins ({})'.format(parser_name, ', '.join(matched_parsers))
            raise argparse.ArgumentError(self, msg)
        parser = self._name_parser_map[matched_parsers[0]]
        setattr(namespace, 'plugin', matched_parsers[0])

        # parse all the remaining options into the namespace
        # store any unrecognized options on the object, so that the top
        # level parser can decide what to do with them

        # In case this subparser defines new defaults, we parse them
        # in a new namespace object and then update the original
        # namespace for the relevant parts.
        subnamespace, arg_strings = parser.parse_known_args(arg_strings, None)
        for key, value in vars(subnamespace).items():
            setattr(namespace, key, value)

        if arg_strings:
            vars(namespace).setdefault(argparse._UNRECOGNIZED_ARGS_ATTR, [])
            getattr(namespace, argparse._UNRECOGNIZED_ARGS_ATTR).extend(arg_strings)


class UnsatisfiedException(exceptions.VolatilityException):
    def __init__(self, unsatisfied):
        super().__init__()
        self.unsatisfied = unsatisfied


def main():
    """A convenience function for constructing and running the :class:`CommandLine`'s run method"""
    CommandLine().run()
