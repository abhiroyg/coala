from contextlib import contextmanager
import re
import shutil

from coalib.bearlib.abstractions.DefaultLinterInterface import (
    DefaultLinterInterface)
from coalib.bears.LocalBear import LocalBear
from coalib.misc.ContextManagers import make_temp
from coalib.misc.Decorators import assert_right_type, enforce_signature
from coalib.misc.Shell import run_shell_command
from coalib.results.Diff import Diff
from coalib.results.Result import Result
from coalib.results.RESULT_SEVERITY import RESULT_SEVERITY
from coalib.settings.FunctionMetadata import FunctionMetadata


@enforce_signature
def Linter(executable: str,
           provides_correction: bool=False,
           use_stdin: bool=False,
           use_stderr: bool=False,
           config_suffix: str="",
           **options):
    """
    Decorator that creates a ``LocalBear`` that is able to process results from
    an external linter tool.

    The main functionality is achieved through the ``create_arguments()``
    function that constructs the command-line-arguments that get parsed to your
    executable.

    >>> @Linter("xlint")
    ... class XLintBear:
    ...     @staticmethod
    ...     def create_arguments(filename, file, config_file):
    ...         return "--lint", filename

    Requiring settings is possible like in ``Bear.run()`` with supplying
    additional keyword arguments (and if needed with defaults).

    >>> @Linter("xlint")
    ... class XLintBear:
    ...     @staticmethod
    ...     def create_arguments(filename,
    ...                          file,
    ...                          config_file,
    ...                          lintmode: str,
    ...                          enable_aggressive_lints: bool=False):
    ...         arguments = ("--lint", filename, "--mode=" + lintmode)
    ...         if enable_aggressive_lints:
    ...             arguments += ("--aggressive",)
    ...         return arguments

    Sometimes your tool requires an actual file that contains configuration.
    ``Linter`` allows you to just define the contents the configuration shall
    contain via ``generate_config()`` and handles everything else for you.

    >>> @Linter("xlint")
    ... class XLintBear:
    ...     @staticmethod
    ...     def generate_config(filename,
    ...                         file,
    ...                         lintmode,
    ...                         enable_aggressive_lints):
    ...         modestring = ("aggressive"
    ...                       if enable_aggressive_lints else
    ...                       "non-aggressive")
    ...         contents = ("<xlint>",
    ...                     "    <mode>" + lintmode + "</mode>",
    ...                     "    <aggressive>" + modestring + "</aggressive>",
    ...                     "</xlint>")
    ...         return "\\n".join(contents)
    ...
    ...     @staticmethod
    ...     def create_arguments(filename,
    ...                          file,
    ...                          config_file,
    ...                          lintmode: str,
    ...                          enable_aggressive_lints: bool=False):
    ...         return "--lint", filename, "--config", config_file

    :param executable:          The linter tool.
    :param provides_correction: Whether the underlying executable provides as
                                output the entirely corrected file instead of
                                issue messages.
    :param use_stdin:           Whether the input file is sent via stdin
                                instead of passing it over the
                                command-line-interface.
    :param use_stderr:          Whether stderr instead of stdout should be
                                grabbed for the executable output.
    :param config_suffix:       The suffix-string to append to the filename
                                of the configuration file created when
                                ``generate_config`` is supplied.
                                Useful if your executable expects getting a
                                specific file-type with specific file-ending
                                for the configuration file.
    :param output_regex:        The regex expression as a string that is used
                                to parse the output generated by the underlying
                                executable. It should use as many of the
                                following named groups (via ``(?P<name>...)``)
                                to provide a good result:

                                - line - The line where the issue starts.
                                - column - The column where the issue starts.
                                - end_line - The line where the issue ends.
                                - end_column - The column where the issue ends.
                                - severity - The severity of the issue.
                                - message - The message of the result.
                                - origin - The origin of the issue.

                                Needs to be provided if ``provides_correction``
                                is ``False``.
    :param severity_map:        A dict used to map a severity string (captured
                                from the ``output_regex`` with the named group
                                ``severity``) to an actual
                                ``coalib.results.RESULT_SEVERITY`` for a
                                result.
                                By default maps the values ``error`` to
                                ``RESULT_SEVERITY.MAJOR``, ``warning`` to
                                ``RESULT_SEVERITY.NORMAL`` and ``info`` to
                                ``RESULT_SEVERITY.INFO``.
                                Only needed if ``provides_correction`` is
                                ``False``.
                                A ``ValueError`` is raised when the named group
                                ``severity`` is not used inside
                                ``output_regex``.
    :param diff_severity:       The severity to use for all results if
                                ``provides_correction`` is set. By default this
                                value is
                                ``coalib.results.RESULT_SEVERITY.NORMAL``. The
                                given value needs to be defined inside
                                ``coalib.results.RESULT_SEVERITY``.
    :param diff_message:        The message-string to use for all results if
                                ``provides_correction`` is set. By default this
                                value is ``"Inconsistency found."``.
    :raises ValueError:         Raised when invalid options are supplied.
    :raises TypeError:          Raised when incompatible types are supplied.
                                See parameter documentations for allowed types.
    :return:                    A ``LocalBear`` derivation that lints code
                                using an external tool.
    """
    options["executable"] = executable
    options["provides_correction"] = provides_correction
    options["use_stdin"] = use_stdin
    options["use_stderr"] = use_stderr
    options["config_suffix"] = use_stderr

    allowed_options = {"executable",
                       "provides_correction",
                       "use_stdin",
                       "use_stderr",
                       "config_suffix"}

    if options["provides_correction"]:
        if "diff_severity" not in options:
            options["diff_severity"] = RESULT_SEVERITY.NORMAL
        else:
            if options["diff_severity"] not in RESULT_SEVERITY.reverse:
                raise TypeError("Invalid value for `diff_severity`: " +
                                repr(options["diff_severity"]))

        if "diff_message" not in options:
            options["diff_message"] = "Inconsistency found."
        else:
            assert_right_type(options["diff_message"], (str,), "diff_message")

        allowed_options |= {"diff_severity", "diff_message"}
    else:
        if "output_regex" not in options:
            raise ValueError("No `output_regex` specified.")

        options["output_regex"] = re.compile(options["output_regex"])

        # Don't setup severity_map if one is provided by user or if it's not
        # used inside the output_regex. If one is manually provided but not
        # used in the output_regex, throw an exception.
        if "severity_map" in options:
            if "severity" not in options["output_regex"].groupindex:
                raise ValueError("Provided `severity_map` but named group "
                                 "`severity` is not used in `output_regex`.")
            assert_right_type(options["severity_map"], (dict,), "severity_map")
        else:
            if "severity" in options["output_regex"].groupindex:
                options["severity_map"] = {"error": RESULT_SEVERITY.MAJOR,
                                           "warning": RESULT_SEVERITY.NORMAL,
                                           "info": RESULT_SEVERITY.INFO}

        allowed_options |= {"output_regex", "severity_map"}

    # Check for illegal superfluous options.
    superfluous_options = options.keys() - allowed_options
    if superfluous_options:
        raise ValueError("Invalid keyword argument " +
                         repr(superfluous_options.pop()) + " provided.")

    def create_linter(klass):
        # Mixin the given interface into the default interface.
        class LinterInterface(klass, DefaultLinterInterface):
            pass

        class Linter(LocalBear):

            @staticmethod
            def get_interface():
                """
                Returns the class that contains the interface methods (like
                ``create_arguments()``) this class uses.

                :return: The interface class.
                """
                return LinterInterface

            @staticmethod
            def get_executable():
                """
                Returns the executable of this class.

                :return: The executable name.
                """
                return options["executable"]

            @classmethod
            def check_prerequisites(cls):
                """
                Checks whether the linter-tool the bear uses is operational.

                :return: True if available, otherwise a string containing more
                         info.
                """
                if shutil.which(cls.get_executable()) is None:
                    return repr(cls.get_executable()) + " is not installed."
                else:
                    return True

            @classmethod
            def get_metadata(cls):
                # TODO Merge with generate_config? So you could independently
                # TODO get settings you need there and in
                # TODO create_arguments the ones needed there. Though arg-
                # TODO forwarding gets complicated then, but this would be
                # TODO cool^^
                return FunctionMetadata.from_function(
                    cls.get_interface().create_arguments,
                    omit={"filename", "file", "config_file"})

            @classmethod
            def _execute_command(cls, args, stdin=None):
                """
                Executes the underlying tool with the given arguments.

                :param args:  The argument sequence to pass to the executable.
                :param stdin: Input to send to the opened process as stdin.
                :return:      A tuple with ``(stdout, stderr)``.
                """
                return run_shell_command(
                    (cls.get_executable(),) + tuple(args),
                    stdin=stdin)

            def _convert_output_regex_match_to_result(self, match, filename):
                """
                Converts the matched named-groups of ``output_regex`` to an
                actual ``Result``.

                :param match:    The regex match object.
                :param filename: The name of the file this match belongs to.
                """
                # Pre process the groups
                groups = match.groupdict()
                if (
                        isinstance(options["severity_map"], dict) and
                        "severity" in groups and
                        groups["severity"] in options["severity_map"]):
                    groups["severity"] = (
                        options["severity_map"][groups["severity"]])

                for variable in ("line", "column", "end_line", "end_column"):
                    if variable in groups and groups[variable]:
                        groups[variable] = int(groups[variable])

                if "origin" in groups:
                    groups["origin"] = "{} ({})".format(
                        str(klass.__name__),
                        str(groups["origin"]))

                # TODO self -> cls ? We don't differentiate origin instances
                # TODO and classes in fact...

                # Construct the result.
                return Result.from_values(
                    origin=groups.get("origin", self),
                    message=groups.get("message", ""),
                    file=filename,
                    severity=int(groups.get("severity",
                                            RESULT_SEVERITY.NORMAL)),
                    line=groups.get("line", None),
                    column=groups.get("column", None),
                    end_line=groups.get("end_line", None),
                    end_column=groups.get("end_column", None))

            if options["provides_correction"]:
                def _process_output(self, output, filename, file):
                    for diff in Diff.from_string_arrays(
                            file,
                            output.splitlines(keepends=True)).split_diff():
                        yield Result(self,
                                     options["diff_message"],
                                     affected_code=(diff.range(filename),),
                                     diffs={filename: diff},
                                     severity=options["diff_severity"])
            else:
                def _process_output(self, output, filename, file):
                    for match in options["output_regex"].finditer(output):
                        yield self._convert_output_regex_match_to_result(
                            match, filename)

            if options["use_stderr"]:
                @staticmethod
                def _grab_output(stdout, stderr):
                    return stderr
            else:
                @staticmethod
                def _grab_output(stdout, stderr):
                    return stdout

            if options["use_stdin"]:
                @staticmethod
                def _pass_file_as_stdin_if_needed(file):
                    return file
            else:
                @staticmethod
                def _pass_file_as_stdin_if_needed(file):
                    return None

            @classmethod
            @contextmanager
            def _create_config(cls, filename, file, **kwargs):
                """
                Provides a context-manager that creates the config file if the
                user provides one and cleans it up when done with linting.

                :param filename: The filename of the file.
                :param file:     The file contents.
                :param kwargs:   Section settings passed from ``run()``.
                :return:         A context-manager handling the config-file.
                """
                content = cls.get_interface().generate_config(filename,
                                                              file,
                                                              **kwargs)
                if content is None:
                    yield None
                else:
                    tmp_suffix = options["config_suffix"]
                    with make_temp(suffix=tmp_suffix) as config_file:
                        with open(config_file, mode="w") as fl:
                            fl.write(content)
                        yield config_file

            def run(self, filename, file, **kwargs):
                with self._create_config(filename,
                                         file,
                                         **kwargs) as config_file:
                    stdout, stderr = self._execute_command(
                        self.get_interface().create_arguments(filename,
                                                              file,
                                                              config_file,
                                                              **kwargs),
                        stdin=self._pass_file_as_stdin_if_needed(file))
                    output = self._grab_output(stdout, stderr)
                    return self._process_output(output, filename, file)

        return Linter

    return create_linter
