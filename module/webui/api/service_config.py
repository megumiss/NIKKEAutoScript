"""Configuration writes shared by the legacy UI and the SPA."""

import json
from typing import Any, Dict
from urllib.parse import urlparse

import yaml

from module.config.account import save_account
from module.config.deep import deep_get, deep_set
from module.logger import logger
from module.webui.api.models import PatchResult
from module.webui.utils import parse_pin_value, re_fullmatch


TEXTAREA_MODES = {'text', 'path', 'url', 'yaml', 'json', 'lines'}


def _validate_textarea_mode(argument: Dict[str, Any], value: Any):
    if argument.get('type') != 'textarea' or value in (None, ''):
        return None

    mode = argument.get('mode', 'text')
    text = str(value)
    if mode in {'text', 'path', 'lines'}:
        return None
    if mode == 'json':
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            return f'Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}'
        return None
    if mode == 'yaml':
        try:
            list(yaml.safe_load_all(text))
        except yaml.YAMLError as exc:
            mark = getattr(exc, 'problem_mark', None)
            location = f' at line {mark.line + 1}, column {mark.column + 1}' if mark else ''
            return f'Invalid YAML{location}: {getattr(exc, "problem", str(exc))}'
        return None
    if mode == 'url':
        for line_number, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            parsed = urlparse(line)
            if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
                return f'Invalid URL at line {line_number}: {line}'
        return None
    if mode not in TEXTAREA_MODES:
        return f'Unsupported textarea mode: {mode}'
    return None


class ConfigService:
    def __init__(self, args: Dict[str, Any] = None, config_updater=None):
        if args is None:
            from module.config.utils import filepath_args, read_file
            args = read_file(filepath_args('args', 'nkas'))
        if config_updater is None:
            from module.webui.setting import State
            config_updater = State.config_updater
        self.args = args
        self.config_updater = config_updater

    def patch(self, config_name: str, key: str, raw_value: Any) -> PatchResult:
        argument = deep_get(self.args, key, default=None)
        if not isinstance(argument, dict):
            return PatchResult(False, {}, {}, True, f'Unknown configuration key: {key}')

        try:
            value = parse_pin_value(raw_value, argument.get('valuetype'))
        except (TypeError, ValueError, KeyError) as exc:
            return PatchResult(False, {}, {}, True, f'Invalid value: {exc}')

        default = argument.get('value')
        if value is None or value == '':
            value = default
        validate = argument.get('validate')
        if validate and not re_fullmatch(validate, str(value)):
            logger.warning(f'Invalid value {value!r} for key {key}, skip saving.')
            return PatchResult(False, {}, {}, True, 'The value does not match the required format.')
        mode_error = _validate_textarea_mode(argument, value)
        if mode_error:
            logger.warning(f'Invalid {argument.get("mode")} value for key {key}: {mode_error}')
            return PatchResult(False, {}, {}, True, mode_error)

        try:
            config = self.config_updater.read_file(config_name)
            applied = {}
            derived = {}
            if key in ('NKAS.Account.Account', 'NKAS.Account.Password'):
                if key.endswith('Account'):
                    save_account(config_name, account=value)
                else:
                    save_account(config_name, password=value)
                value = '******'

            deep_set(config, key, value)
            applied[key] = value
            if key not in ('NKAS.Account.Account', 'NKAS.Account.Password'):
                for derived_key, derived_value in self.config_updater.save_callback(key, value):
                    deep_set(config, derived_key, derived_value)
                    applied[derived_key] = derived_value
                    derived[derived_key] = derived_value
            self.config_updater.write_file(config_name, config)
            logger.info(f'Save config {config_name}, {key}')
            return PatchResult(True, applied, derived)
        except OSError as exc:
            logger.exception(exc)
            return PatchResult(False, {}, {}, False, str(exc))
