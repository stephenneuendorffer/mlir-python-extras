# Part of the LLVM Project, under the Apache License v2.0 with LLVM Exceptions.
# See https://llvm.org/LICENSE.txt for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

import io
import re

ADVERT_BEGIN = "// NOTE: Assertions have been autogenerated by "
ADVERT_END = """
// The script is designed to make adding checks to
// a test case fast, it is *not* designed to be authoritative
// about what constitutes a good test! The CHECK should be
// minimized and named to reflect the test intent.
"""

# Regex command to match an SSA identifier.
SSA_RE_STR = "[0-9]+|[a-zA-Z$._-][a-zA-Z0-9$._-]*"
SSA_RE = re.compile(SSA_RE_STR)


# Class used to generate and manage string substitution blocks for SSA value
# names.
class SSAVariableNamer:
    def __init__(self):
        self.scopes = []
        self.name_counter = 0

    # Generate a substitution name for the given ssa value name.
    def generate_name(self, ssa_name):
        variable = "VAL_" + str(self.name_counter)
        self.name_counter += 1
        self.scopes[-1][ssa_name] = variable
        return variable

    # Push a new variable name scope.
    def push_name_scope(self):
        self.scopes.append({})

    # Pop the last variable name scope.
    def pop_name_scope(self):
        self.scopes.pop()

    # Return the level of nesting (number of pushed scopes).
    def num_scopes(self):
        return len(self.scopes)

    # Reset the counter.
    def clear_counter(self):
        self.name_counter = 0


# Process a line of input that has been split at each SSA identifier '%'.
def process_line(line_chunks, variable_namer):
    output_line = ""

    # Process the rest that contained an SSA value name.
    for chunk in line_chunks:
        m = SSA_RE.match(chunk)
        ssa_name = m.group(0)

        # Check if an existing variable exists for this name.
        variable = None
        for scope in variable_namer.scopes:
            variable = scope.get(ssa_name)
            if variable is not None:
                break

        # If one exists, then output the existing name.
        if variable is not None:
            output_line += "%[[" + variable + "]]"
        else:
            # Otherwise, generate a new variable.
            variable = variable_namer.generate_name(ssa_name)
            output_line += "%[[" + variable + ":.*]]"

        # Append the non named group.
        output_line += chunk[len(ssa_name) :]

    return output_line.rstrip() + "\n"


# Process the source file lines. The source file doesn't have to be .mlir.
def process_source_lines(source_lines, note, args):
    source_split_re = re.compile(args.source_delim_regex)

    source_segments = [[]]
    for line in source_lines:
        # Remove previous note.
        if line == note:
            continue
        # Remove previous CHECK lines.
        if line.find(args.check_prefix) != -1:
            continue
        # Segment the file based on --source_delim_regex.
        if source_split_re.search(line):
            source_segments.append([])

        source_segments[-1].append(line + "\n")
    return source_segments


# Pre-process a line of input to remove any character sequences that will be
# problematic with FileCheck.
def preprocess_line(line):
    # Replace any double brackets, '[[' with escaped replacements. '[['
    # corresponds to variable names in FileCheck.
    output_line = line.replace("[[", "{{\\[\\[}}")

    # Replace any single brackets that are followed by an SSA identifier, the
    # identifier will be replace by a variable; Creating the same situation as
    # above.
    output_line = output_line.replace("[%", "{{\\[}}%")

    return output_line


def main(input, starts_from_scope=False, check_prefix="# CHECK", output=None):
    input = str(input)
    # Open the given input file.
    input_lines = [l.rstrip() for l in input.splitlines()]

    source_segments = None
    if output is None:
        output = io.StringIO()

    output_segments = [[]]
    # A map containing data used for naming SSA value names.
    variable_namer = SSAVariableNamer()
    for input_line in input_lines:
        if not input_line:
            continue
        lstripped_input_line = input_line.lstrip()

        # Lines with blocks begin with a ^. These lines have a trailing comment
        # that needs to be stripped.
        is_block = lstripped_input_line[0] == "^"
        if is_block:
            input_line = input_line.rsplit("//", 1)[0].rstrip()

        cur_level = variable_namer.num_scopes()

        # If the line starts with a '}', pop the last name scope.
        if lstripped_input_line[0] == "}":
            variable_namer.pop_name_scope()
            cur_level = variable_namer.num_scopes()

        # If the line ends with a '{', push a new name scope.
        if input_line[-1] == "{":
            variable_namer.push_name_scope()
            if cur_level == starts_from_scope:
                output_segments.append([])

        # Omit lines at the near top level e.g. "module {".
        if cur_level < starts_from_scope:
            continue

        if len(output_segments[-1]) == 0:
            variable_namer.clear_counter()

        # Preprocess the input to remove any sequences that may be problematic with
        # FileCheck.
        input_line = preprocess_line(input_line)

        # Split the line at the each SSA value name.
        ssa_split = input_line.split("%")

        # If this is a top-level operation use 'CHECK-LABEL', otherwise 'CHECK:'.
        if len(output_segments[-1]) != 0 or not ssa_split[0]:
            output_line = check_prefix + ": "
            # Pad to align with the 'LABEL' statements.
            output_line += " " * len("-LABEL")

            # Output the first line chunk that does not contain an SSA name.
            output_line += ssa_split[0]

            # Process the rest of the input line.
            output_line += process_line(ssa_split[1:], variable_namer)

        else:
            # Output the first line chunk that does not contain an SSA name for the
            # label.
            output_line = check_prefix + "-LABEL: " + ssa_split[0] + "\n"

            # Process the rest of the input line on separate check lines.
            for argument in ssa_split[1:]:
                output_line += check_prefix + "-SAME:  "

                # Pad to align with the original position in the line.
                output_line += " " * len(ssa_split[0])

                # Process the rest of the line.
                output_line += process_line([argument], variable_namer)

        # Append the output line.
        output_segments[-1].append(output_line)

    # Write the output.
    if source_segments:
        assert len(output_segments) == len(source_segments)
        for check_segment, source_segment in zip(output_segments, source_segments):
            for line in check_segment:
                output.write(line)
            for line in source_segment:
                output.write(line)
    else:
        for segment in output_segments:
            output.write("\n")
            for output_line in segment:
                output.write(output_line)
        output.write("\n")

    return output.getvalue()
