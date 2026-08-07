import re


TRACEBACK_HEADER = 'Traceback (most recent call last):'
TRACEBACK_FRAME_PATTERN = re.compile(r'^\s*(?:\|\s*)*File ".*", line \d+(?:, in .*)?$')
NUMBER_PATTERN = re.compile(r'^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$')


def split_traceback(text: str):
    """Keep the innermost frame visible and return all preceding context separately."""
    lines = text.rstrip().splitlines()
    frame_indices = [index for index, line in enumerate(lines) if TRACEBACK_FRAME_PATTERN.match(line)]
    if not frame_indices:
        return text.rstrip(), '', 0

    primary_index = frame_indices[-1]
    header_index = next(
        (index for index in range(primary_index, -1, -1) if TRACEBACK_HEADER in lines[index]),
        None,
    )
    if header_index is None:
        primary_lines = lines[primary_index:]
        collapsed_lines = lines[:primary_index]
    else:
        primary_lines = [lines[header_index], *lines[primary_index:]]
        collapsed_lines = [*lines[:header_index], *lines[header_index + 1 : primary_index]]

    collapsed = '\n'.join(collapsed_lines).strip()
    collapsed_frames = sum(1 for line in collapsed_lines if TRACEBACK_FRAME_PATTERN.match(line))
    return '\n'.join(primary_lines).strip(), collapsed, collapsed_frames


def attr_value_kind(value: str) -> str:
    value = value.strip()
    normalized = value.lower()
    if normalized in {'true', 'yes', 'on', 'online', 'enabled', 'success'}:
        return 'positive'
    if normalized in {'false', 'no', 'off', 'offline', 'disabled', 'failed', 'failure'}:
        return 'negative'
    if normalized in {'none', 'null', 'nil'}:
        return 'empty'
    if NUMBER_PATTERN.fullmatch(value):
        return 'number'
    if '://' in value or '\\' in value or re.match(r'^[A-Za-z]:/', value) or value.startswith(('/', './', '../')):
        return 'path'
    return 'text'
