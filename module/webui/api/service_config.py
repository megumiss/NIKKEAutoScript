"""Configuration writes shared by the legacy UI and the SPA."""

from typing import Any, Dict

from module.config.account import save_account
from module.config.deep import deep_get, deep_set
from module.logger import logger
from module.webui.api.models import PatchResult
from module.webui.utils import parse_pin_value, re_fullmatch


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
