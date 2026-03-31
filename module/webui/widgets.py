import copy
import json
import random
import string
from typing import Any, Callable, Dict, Generator, List, Optional, TYPE_CHECKING, Union

from pywebio.exceptions import SessionException
from pywebio.io_ctrl import Output
from pywebio.output import *
from pywebio.session import eval_js, local, run_js
from rich.console import ConsoleRenderable

from module.logger import HTMLConsole, Highlighter, WEB_THEME, logger
from module.webui.lang import t
from module.webui.pin import put_checkbox, put_input, put_select, put_textarea
from module.webui.process_manager import ProcessManager
from module.webui.setting import State
from module.webui.utils import (
    DARK_TERMINAL_THEME,
    LIGHT_TERMINAL_THEME,
    LOG_CODE_FORMAT,
    Switch,
)

if TYPE_CHECKING:
    from module.webui.app import NKASGUI


class ScrollableCode:
    """
    https://github.com/pywebio/PyWebIO/discussions/21
    Deprecated
    """

    def __init__(self, keep_bottom: bool = True) -> None:
        self.keep_bottom = keep_bottom

        self.id = "".join(random.choice(string.ascii_letters) for _ in range(10))
        self.html = (
                """<pre id="%s" class="container-log"><code style="white-space:break-spaces;"></code></pre>"""
                % self.id
        )

    def output(self):
        # .style("display: grid; overflow-y: auto;")
        return put_html(self.html)

    def append(self, text: str) -> None:
        if text:
            run_js(
                """$("#{dom_id}>code").append(text);
            """.format(
                    dom_id=self.id
                ),
                text=str(text),
            )
            if self.keep_bottom:
                self.scroll()

    def scroll(self) -> None:
        run_js(
            r"""$("\#{dom_id}").animate({{scrollTop: $("\#{dom_id}").prop("scrollHeight")}}, 0);
        """.format(
                dom_id=self.id
            )
        )

    def reset(self) -> None:
        run_js(r"""$("\#{dom_id}>code").empty();""".format(dom_id=self.id))

    def set_scroll(self, b: bool) -> None:
        # use for lambda callback function
        self.keep_bottom = b


class RichLog:
    def __init__(self, scope, font_width="0.559") -> None:
        self.scope = scope
        self.font_width = font_width
        self.console = HTMLConsole(
            force_terminal=False,
            force_interactive=False,
            width=80,
            color_system="truecolor",
            markup=False,
            record=True,
            safe_box=False,
            highlighter=Highlighter(),
            theme=WEB_THEME,
        )
        # self.callback_id = output_register_callback(
        #     self._callback_set_width, serial_mode=True)
        # self._callback_thread = None
        # self._width = 80
        self.keep_bottom = True
        if State.theme == "dark":
            self.terminal_theme = DARK_TERMINAL_THEME
        else:
            self.terminal_theme = LIGHT_TERMINAL_THEME

    def render(self, renderable: ConsoleRenderable) -> str:
        with self.console.capture():
            self.console.print(renderable)

        html = self.console.export_html(
            theme=self.terminal_theme,
            clear=True,
            code_format=LOG_CODE_FORMAT,
            inline_styles=True,
        )
        # print(html)
        return html

    def extend(self, text):
        if text:
            run_js(
                """$("#pywebio-scope-{scope}>div").append(text);
            """.format(
                    scope=self.scope
                ),
                text=str(text),
            )
            if self.keep_bottom:
                self.scroll()

    def reset(self):
        run_js(f"""$("#pywebio-scope-{self.scope}>div").empty();""")

    def scroll(self) -> None:
        run_js(
            """$("#pywebio-scope-{scope}").scrollTop($("#pywebio-scope-{scope}").prop("scrollHeight"));
        """.format(
                scope=self.scope
            )
        )

    def set_scroll(self, b: bool) -> None:
        # use for lambda callback function
        self.keep_bottom = b

    def get_width(self):
        js = """
        let canvas = document.createElement('canvas');
        canvas.style.position = "absolute";
        let ctx = canvas.getContext('2d');
        document.body.appendChild(canvas);
        ctx.font = `16px Menlo, consolas, DejaVu Sans Mono, Courier New, monospace`;
        document.body.removeChild(canvas);
        let text = ctx.measureText('0');
        ctx.fillText('0', 50, 50);

        ($('#pywebio-scope-{scope}').width()-16)/\
        $('#pywebio-scope-{scope}').css('font-size').slice(0, -2)/text.width*16;\
        """.format(
            scope=self.scope
        )
        width = eval_js(js)
        return 80 if width is None else 128 if width > 128 else int(width)

    # def _register_resize_callback(self):
    #     js = """
    #     WebIO.pushData(
    #         ($('#pywebio-scope-log').width()-16)/$('#pywebio-scope-log').css('font-size').slice(0, -2)/0.55,
    #         {callback_id}
    #     )""".format(callback_id=self.callback_id)

    # def _callback_set_width(self, width):
    #     self._width = width
    #     if self._callback_thread is None:
    #         self._callback_thread = Thread(target=self._callback_width_checker)
    #         self._callback_thread.start()

    # def _callback_width_checker(self):
    #     last_modify = time.time()
    #     _width = self._width
    #     while True:
    #         if time.time() - last_modify > 1:
    #             break
    #         if self._width == _width:
    #             time.sleep(0.1)
    #             continue
    #         else:
    #             _width = self._width
    #             last_modify = time.time()

    #     self._callback_thread = None
    #     self.console.width = int(_width)

    def put_log(self, pm: ProcessManager) -> Generator:
        yield
        try:
            while True:
                last_idx = len(pm.renderables)
                html = "".join(map(self.render, pm.renderables[:]))
                self.reset()
                self.extend(html)
                counter = last_idx
                while counter < pm.renderables_max_length * 2:
                    yield
                    idx = len(pm.renderables)
                    if idx < last_idx:
                        last_idx -= pm.renderables_reduce_length
                    if idx != last_idx:
                        html = "".join(map(self.render, pm.renderables[last_idx:idx]))
                        self.extend(html)
                        counter += idx - last_idx
                        last_idx = idx
        except SessionException:
            pass


class BinarySwitchButton(Switch):
    def __init__(
            self,
            get_state,
            label_on,
            label_off,
            onclick_on,
            onclick_off,
            scope,
            color_on="success",
            color_off="secondary",
    ):
        """
        Args:
            get_state:
                (Callable):
                    return True to represent state `ON`
                    return False tp represent state `OFF`
                (Generator):
                    yield True to change btn state to `ON`
                    yield False to change btn state to `OFF`
            label_on: label to show when state is `ON`
            label_off:
            onclick_on: function to call when state is `ON`
            onclick_off:
            color_on: button color when state is `ON`
            color_off:
            scope: scope for button, just for button **only**
        """
        self.scope = scope
        status = {
            0: {
                "func": self.update_button,
                "args": (
                    label_off,
                    onclick_off,
                    color_off,
                ),
            },
            1: {
                "func": self.update_button,
                "args": (
                    label_on,
                    onclick_on,
                    color_on,
                ),
            },
        }
        super().__init__(status=status, get_state=get_state, name=scope)

    def update_button(self, label, onclick, color):
        clear(self.scope)
        put_button(label=label, onclick=onclick, color=color, scope=self.scope)


# aside buttons


def put_icon_buttons(
        icon_html: str,
        buttons: List[Dict[str, str]],
        onclick: Union[List[Callable[[], None]], Callable[[], None]],
) -> Output:
    value = buttons[0]["value"]
    return put_column(
        [
            output(put_html(icon_html)).style(
                "z-index: 1; margin-left: 8px;text-align: center"
            ),
            put_buttons(buttons, onclick).style(f"z-index: 2; --aside-{value}--;"),
        ],
        size="0",
    )


def put_none() -> Output:
    return put_html("<div></div>")


T_Output_Kwargs = Dict[str, Union[str, Dict[str, Any]]]


def get_title_help(kwargs: T_Output_Kwargs) -> Output:
    title: str = kwargs.get("title")
    help_text: str = kwargs.get("help")

    if help_text:
        res = put_column(
            [
                put_text(title).style("--arg-title--"),
                put_text(help_text).style("--arg-help--"),
            ],
            size="auto 1fr",
        )
    else:
        res = put_text(title).style("--arg-title--")

    return res


# args input widget
def put_arg_input(kwargs: T_Output_Kwargs) -> Output:
    name: str = kwargs["name"]
    options: List = kwargs.get("options")
    if options is not None:
        kwargs.setdefault("datalist", options)

    return put_scope(
        f"arg_container-input-{name}",
        [
            get_title_help(kwargs),
            put_input(**kwargs).style("--input--"),
        ],
    )


def product_stored_row(kwargs: T_Output_Kwargs, key, value):
    kwargs = copy.copy(kwargs)
    kwargs["name"] += f'_{key}'
    kwargs["value"] = value
    return put_input(**kwargs).style("--input--")


def put_arg_stored(kwargs: T_Output_Kwargs) -> Output:
    name: str = kwargs["name"]
    kwargs["disabled"] = True

    values = kwargs.pop("value", {})
    time_ = values.pop("time", "")

    rows = [product_stored_row(kwargs, key, value) for key, value in values.items() if value]
    if time_:
        rows += [product_stored_row(kwargs, "time", time_)]
    return put_scope(
        f"arg_container-stored-{name}",
        [
            get_title_help(kwargs),
            put_scope(
                f"arg_stored-stored-value-{name}",
                rows,
            )
        ]
    )


def put_arg_select(kwargs: T_Output_Kwargs) -> Output:
    name: str = kwargs["name"]
    value: str = kwargs["value"]
    options: List[str] = kwargs["options"]
    options_label: List[str] = kwargs.pop("options_label", [])
    disabled: bool = kwargs.pop("disabled", False)
    _: str = kwargs.pop("invalid_feedback", None)

    if disabled:
        option = [{
            "label": next((opt_label for opt, opt_label in zip(options, options_label) if opt == value), value),
            "value": value,
            "selected": True,
        }]
    else:
        option = [{
            "label": opt_label,
            "value": opt,
            "select": opt == value,
        } for opt, opt_label in zip(options, options_label)]
    kwargs["options"] = option

    return put_scope(
        f"arg_container-select-{name}",
        [
            get_title_help(kwargs),
            put_select(**kwargs).style("--input--"),
        ],
    )


def put_arg_state(kwargs: T_Output_Kwargs) -> Output:
    name: str = kwargs["name"]
    value: str = kwargs["value"]
    options: List[str] = kwargs["options"]
    options_label: List[str] = kwargs.pop("options_label", [])
    _: str = kwargs.pop("invalid_feedback", None)
    bold: bool = value in kwargs.pop("option_bold", [])
    light: bool = value in kwargs.pop("option_light", [])

    option = [{
        "label": next((opt_label for opt, opt_label in zip(options, options_label) if opt == value), value),
        "value": value,
        "selected": True,
    }]
    if bold:
        kwargs["class"] = "form-control state state-bold"
    elif light:
        kwargs["class"] = "form-control state state-light"
    else:
        kwargs["class"] = "form-control state"
    kwargs["options"] = option

    return put_scope(
        f"arg_container-select-{name}",
        [
            get_title_help(kwargs),
            put_select(**kwargs).style("--input--"),
        ],
    )


def put_arg_textarea(kwargs: T_Output_Kwargs) -> Output:
    name: str = kwargs["name"]
    mode: str = kwargs.pop("mode", None)
    kwargs.setdefault(
        "code", {"lineWrapping": True, "lineNumbers": False, "mode": mode}
    )

    # For PC client executable path fields, provide a native file picker button.
    path_picker_accept_map = {
        "PCClient_PCClientInfo_LauncherPath": ".exe",
        "PCClient_PCClientInfo_GamePath": ".exe",
    }
    accept = path_picker_accept_map.get(name)
    if accept is not None:
        def _pick_file():
            from pywebio.pin import pin
            import os

            def _normalize_windows_path(path: str) -> str:
                raw = str(path or '').strip()
                if raw == '':
                    return ''
                normalized = os.path.normpath(raw)
                # Keep UI/config consistent on Windows.
                return normalized.replace('/', '\\')

            selected = ''
            root = None
            try:
                import tkinter as tk
                from tkinter import filedialog

                root = tk.Tk()
                root.withdraw()
                try:
                    root.attributes('-topmost', True)
                    root.update()
                except Exception:
                    pass

                selected = filedialog.askopenfilename(
                    title=t("Gui.Text.ChooseFile"),
                    filetypes=[
                        ('Executable Files', '*.exe'),
                        ('All Files', '*.*'),
                    ],
                )
            except Exception as e:
                logger.warning(f'Native file picker unavailable for {name}: {e}')
                toast('File picker unavailable, please input path manually.', color='error')
                return
            finally:
                if root is not None:
                    try:
                        root.destroy()
                    except Exception:
                        pass

            selected = _normalize_windows_path(selected)
            if not selected:
                return

            pin[name] = selected
            nkasgui: "NKASGUI" = local.gui
            nkasgui.modified_config_queue.put(
                {"name": ".".join(name.split("_")), "value": selected}
            )
            logger.info(f'Path picker selected: {name}={selected}')

            # Keep the same autofill behavior as startup flow:
            # selecting LauncherPath auto-populates GamePath.
            if name == "PCClient_PCClientInfo_LauncherPath":
                config = nkasgui.nkas_config.read_file(nkasgui.nkas_name)

                def _safe_pin_value(pin_name, fallback=''):
                    try:
                        val = pin[pin_name]
                    except Exception:
                        val = fallback
                    return val

                client = str(
                    _safe_pin_value(
                        "PCClient_PCClientInfo_Client",
                        config.get("PCClient", {}).get("PCClientInfo", {}).get("Client", "intl"),
                    )
                    or "intl"
                ).strip()
                auto_fill_raw = _safe_pin_value(
                    "PCClient_PCClientInfo_AutoFillName",
                    config.get("PCClient", {}).get("PCClientInfo", {}).get("AutoFillName", True),
                )
                if isinstance(auto_fill_raw, list):
                    auto_fill_name = bool(auto_fill_raw)
                else:
                    auto_fill_name = bool(auto_fill_raw)

                game_process_pin = str(
                    _safe_pin_value(
                        "PCClient_PCClientInfo_GameProcessName",
                        config.get("PCClient", {}).get("PCClientInfo", {}).get("GameProcessName", ""),
                    )
                    or ""
                ).strip()

                default_game_process_map = {
                    "intl": "nikke.exe",
                    "hmt": "nikke.exe",
                }
                default_game_process = default_game_process_map.get(client, "nikke.exe")
                game_process = default_game_process if auto_fill_name else (game_process_pin or default_game_process)

                game_pin_name = "PCClient_PCClientInfo_GamePath"
                current_game_path = _normalize_windows_path(
                    _safe_pin_value(
                        game_pin_name,
                        config.get("PCClient", {}).get("PCClientInfo", {}).get("GamePath", ""),
                    )
                    or ""
                )

                # Only auto-fill when GamePath is currently empty.
                if current_game_path:
                    logger.info(
                        f'Skip launcher autofill because {game_pin_name} is not empty: {current_game_path}'
                    )
                    return

                launcher_dir = os.path.dirname(os.path.normpath(selected))
                game_path = _normalize_windows_path(
                    os.path.join(launcher_dir, "..", "NIKKE", "game", game_process)
                )
                pin[game_pin_name] = game_path
                nkasgui.modified_config_queue.put(
                    {"name": ".".join(game_pin_name.split("_")), "value": game_path}
                )
                logger.info(f'Auto-filled from launcher path: {game_pin_name}={game_path}')

        button_scope = f"arg_path_picker_btn_{name}"
        row = put_row(
            [
                put_textarea(**kwargs),
                put_scope(
                    button_scope,
                    [
                        put_button(
                            label=t("Gui.Text.ChooseFile"),
                            onclick=_pick_file,
                            color="primary",
                            small=True,
                        ),
                    ],
                ).style(
                    "display: flex; align-items: center; justify-content: flex-start; "
                    "height: 100%; min-height: 2.2rem;"
                ),
            ],
            size="1fr auto",
        ).style("align-items: stretch; column-gap: .45rem;")

        scope = put_scope(
            f"arg_contianer-textarea-{name}",
            [
                get_title_help(kwargs),
                row,
            ],
        )

        run_js(
            """
(() => {
    const scopeName = button_scope_name;
    const applyStyle = (attempt) => {
        const scope = document.getElementById(`pywebio-scope-${scopeName}`);
        if (!scope) {
            if (attempt < 20) setTimeout(() => applyStyle(attempt + 1), 50);
            return;
        }
        const btn = scope.querySelector('button');
        if (!btn) {
            if (attempt < 20) setTimeout(() => applyStyle(attempt + 1), 50);
            return;
        }
        scope.style.display = 'flex';
        scope.style.alignItems = 'center';
        scope.style.justifyContent = 'flex-start';
        scope.style.height = '100%';
        btn.classList.remove('btn-block');
        btn.style.width = 'auto';
        btn.style.minHeight = '1.75rem';
        btn.style.padding = '.2rem .58rem';
        btn.style.lineHeight = '1.1';
        btn.style.fontSize = '.84rem';
        btn.style.whiteSpace = 'nowrap';
        btn.style.display = 'inline-flex';
        btn.style.alignItems = 'center';
        btn.style.justifyContent = 'center';
        btn.style.marginTop = '0';
        btn.style.marginLeft = '.25rem';
    };
    applyStyle(0);
})();
            """,
            button_scope_name=button_scope,
        )

        return scope

    return put_scope(
        f"arg_contianer-textarea-{name}",
        [
            get_title_help(kwargs),
            put_textarea(**kwargs),
        ],
    )


def put_arg_checkbox(kwargs: T_Output_Kwargs) -> Output:
    # Not real checkbox, use as a switch (on/off)
    name: str = kwargs["name"]
    value: str = kwargs["value"]
    _: str = kwargs.pop("invalid_feedback", None)

    kwargs["options"] = [{"label": "", "value": True, "selected": value}]
    return put_scope(
        f"arg_container-checkbox-{name}",
        [
            get_title_help(kwargs),
            put_checkbox(**kwargs).style("text-align: center"),
        ],
    )


def put_arg_datetime(kwargs: T_Output_Kwargs) -> Output:
    name: str = kwargs["name"]
    return put_scope(
        f"arg_container-datetime-{name}",
        [
            get_title_help(kwargs),
            put_input(**kwargs).style("--input--"),
        ],
    )


def put_arg_storage(kwargs: T_Output_Kwargs) -> Optional[Output]:
    name: str = kwargs["name"]
    if kwargs["value"] == {}:
        return None

    kwargs["value"] = json.dumps(
        kwargs["value"], indent=2, ensure_ascii=False, sort_keys=False, default=str
    )
    kwargs.setdefault(
        "code", {"lineWrapping": True, "lineNumbers": False, "mode": "json"}
    )

    def clear_callback():
        nkasgui: "NKASGUI" = local.gui
        nkasgui.modified_config_queue.put(
            {"name": ".".join(name.split("_")), "value": {}}
        )
        # https://github.com/pywebio/PyWebIO/issues/459
        # pin[name] = "{}"

    return put_scope(
        f"arg_container-storage-{name}",
        [
            put_textarea(**kwargs),
            put_html(
                f'<button class="btn btn-outline-warning btn-block">{t("Gui.Text.Clear")}</button>'
            ).onclick(clear_callback),
        ],
    )


def _to_static_path(path: str) -> str:
    if not path:
        return ""
    if path.startswith("./assets/"):
        return "/static/" + path[len("./assets/") :]
    return path


def _format_display_count(value) -> str:
    text = str(value).strip()
    if text == "":
        return "-"
    try:
        number = int(text.replace(",", ""))
        return f"{number:,}"
    except Exception:
        return text


def put_arg_item_table(kwargs: T_Output_Kwargs) -> Output:
    """
    Render inventory item list table (read-only).
    """
    import html

    from module.config.deep import deep_get
    from module.warehouse_stats.data import (
        DEFAULT_CSV_PATH,
        DEFAULT_ITEM_MAP_PATH,
        load_item_groups,
        load_latest_counts,
        resolve_csv_path,
        resolve_item_asset_path,
        resolve_item_prefix,
    )

    name: str = kwargs["name"]

    nkasgui = local.gui
    config = nkasgui.nkas_config.read_file(nkasgui.nkas_name)

    item_map_path = deep_get(
        config,
        keys=["WarehouseStats", "WarehouseStats", "ItemMapPath"],
        default=DEFAULT_ITEM_MAP_PATH,
    )
    csv_path = deep_get(
        config,
        keys=["WarehouseStats", "WarehouseStats", "CsvPath"],
        default=DEFAULT_CSV_PATH,
    )
    csv_path = resolve_csv_path(csv_path, config_name=nkasgui.nkas_name)

    groups = load_item_groups(item_map_path)
    counts = load_latest_counts(csv_path)

    def finalize(outputs: List[Output]) -> Output:
        scope = put_scope(f"arg_container-item-table-{name}", outputs)
        scope.style("display: grid; grid-auto-flow: row; grid-template-columns: 1fr;")
        return scope

    content: List[Output] = []
    if not groups:
        content.append(put_text(t("Gui.Text.WarehouseNoMap")))
        return finalize(content)

    tone_classes = ['warehouse-tone-1', 'warehouse-tone-2', 'warehouse-tone-3']
    for group_index, group in enumerate(groups):
        tone_class = tone_classes[group_index % len(tone_classes)]
        cards = []
        for item in group.get("items", []):
            item_id = item.get("id", "")
            prefix = resolve_item_prefix(item)
            icon_path = _to_static_path(resolve_item_asset_path(prefix, "ICON"))
            latest_row = counts.get(item_id, {})
            count = latest_row.get("count", "")
            count_text = _format_display_count(count)

            display_name = (
                str(item.get("display_name", "")).strip()
                or str(latest_row.get("item_name", "")).strip()
                or str(item.get("name", item_id))
            )
            name_text = html.escape(display_name)
            owned_label = html.escape(t("Gui.Text.WarehouseOwned"))
            count_text = html.escape(str(count_text))

            if icon_path:
                icon_html = (
                    f'<img src="{html.escape(icon_path)}" '
                    f'loading="lazy" decoding="async" '
                    f'class="warehouse-item-icon-img" />'
                )
            else:
                icon_html = '<div class="warehouse-item-icon-placeholder">-</div>'

            cards.append(
                f'<div class="warehouse-item-card">'
                f'<div class="warehouse-item-icon">{icon_html}</div>'
                f'<div class="warehouse-item-info">'
                f'<div class="warehouse-item-name">{name_text}</div>'
                f'<div class="warehouse-item-count">'
                f'<span class="warehouse-owned-label">{owned_label}</span>'
                f'<span class="warehouse-owned-value">{count_text}</span>'
                f'</div>'
                f'</div>'
                f'</div>'
            )

        if cards:
            group_title = html.escape(str(group.get("name", "")))
            grid_html = (
                f'<div class="warehouse-group {tone_class}">'
                f'<div class="warehouse-group-head">'
                f'<div class="warehouse-group-title">{group_title}</div>'
                f'<div class="warehouse-group-total">{len(cards)}</div>'
                f'</div>'
                f'<div class="warehouse-items-grid">{"".join(cards)}</div>'
                f'</div>'
            )
            content.append(put_html(grid_html))

    if not counts:
        content.append(put_text(t("Gui.Text.WarehouseNoData")))

    return finalize(content)


def put_arg_interception_stone_import(kwargs: T_Output_Kwargs) -> Output:
    from pywebio.pin import pin

    from module.config.deep import deep_get
    from module.interception.data import DEFAULT_STONE_CSV_PATH
    from module.interception.interception import import_interception_stone_records_from_screenshots

    name: str = kwargs["name"]

    def _read_context():
        nkasgui = local.gui
        config = nkasgui.nkas_config.read_file(nkasgui.nkas_name)
        default_path = deep_get(
            config,
            keys=["Interception", "Interception", "DropScreenshotPath"],
            default='',
        )
        csv_path = deep_get(
            config,
            keys=["InterceptionTaskStats", "InterceptionDropStats", "CsvPath"],
            default=DEFAULT_STONE_CSV_PATH,
        )
        boss = deep_get(
            config,
            keys=["Interception", "Interception", "Boss"],
            default='',
        )
        return nkasgui, str(default_path or ''), str(csv_path or ''), str(boss or '')

    def _import_callback():
        current_gui, current_default_path, current_csv_path, current_boss = _read_context()
        path_pin_name = f"interception_import_path_{''.join(random.choice(string.ascii_letters) for _ in range(8))}"

        def _submit_import():
            import_path = str(pin[path_pin_name] or '').strip()
            if not import_path:
                toast(t("Gui.Text.InterceptionImportPathPlaceholder"), color='error')
                return

            close_popup()
            popup(
                t("Gui.Text.InterceptionImportLoading"),
                [
                    put_loading_text(
                        t("Gui.Text.InterceptionImportLoading"),
                        color='primary',
                    ),
                ],
            )

            result = {'ok': False}
            try:
                result = import_interception_stone_records_from_screenshots(
                    import_path=import_path,
                    csv_path=current_csv_path,
                    config_name=current_gui.nkas_name,
                    boss=current_boss,
                )
            finally:
                close_popup()

            if not result.get('ok'):
                toast(str(result.get('message') or t("Gui.Text.InterceptionImportFailed")), color='error')
                return

            toast(
                t(
                    "Gui.Text.InterceptionImportDone",
                    imported=int(result.get('imported', 0)),
                    skipped=int(result.get('skipped', 0)),
                    failed=int(result.get('failed', 0)),
                ),
                color='success',
                duration=5,
            )
            current_gui.nkas_set_group('InterceptionTaskStats')

        popup(
            t("Gui.Text.InterceptionImportDialogTitle"),
            [
                put_input(
                    label=t("Gui.Text.InterceptionImportPathLabel"),
                    name=path_pin_name,
                    value=current_default_path,
                    placeholder=t("Gui.Text.InterceptionImportPathPlaceholder"),
                ).style("--input--"),
                put_row(
                    [
                        put_button(t("Gui.AppManage.Import"), onclick=_submit_import, color='primary'),
                        put_button(t("Gui.AppManage.Back"), onclick=close_popup, color='danger'),
                    ],
                    size='auto auto 1fr',
                ),
            ],
        )

    return put_scope(
        f"arg_container-input-{name}",
        [
            get_title_help(kwargs),
            put_button(
                label=t("Gui.Text.InterceptionImportButton"),
                onclick=_import_callback,
                color='primary',
            ).style("--input--; display: block; width: max-content; margin-left: auto; margin-right: .2rem; white-space: nowrap; writing-mode: horizontal-tb;"),
        ],
    ).style("display: grid; width: 100%; gap: .14rem; margin-bottom: .22rem;")


def _build_echarts_line_chart_card(
    title: str,
    labels: List[str],
    values: List[int],
    sum_text: str,
    max_text: str,
    avg_text: str,
    tone_class: str = '',
    dom_id: str = '',
) -> str:
    import html

    if not labels or not values:
        labels = ['-']
        values = [0]

    total = sum(values)
    peak = max(values)
    average = total / len(values) if values else 0
    range_text = labels[0] if len(labels) == 1 else f'{labels[0]} - {labels[-1]}'

    return (
        f'<div class="interception-chart-card {html.escape(tone_class)}">'
        f'<div class="interception-chart-head">'
        f'<div class="interception-chart-title">{html.escape(title)}</div>'
        f'<div class="interception-chart-range">{html.escape(range_text)}</div>'
        f'</div>'
        f'<div id="{html.escape(dom_id)}" class="interception-chart-echarts" style="width:100%; height:220px;"></div>'
        f'<div class="interception-chart-meta">'
        f'<span class="interception-chart-chip">{html.escape(sum_text)}: {total}</span>'
        f'<span class="interception-chart-chip">{html.escape(max_text)}: {peak}</span>'
        f'<span class="interception-chart-chip">{html.escape(avg_text)}: {average:.1f}</span>'
        f'</div>'
        f'</div>'
    )


def put_arg_interception_stone_charts(kwargs: T_Output_Kwargs) -> Output:
    from module.config.deep import deep_get
    from module.interception.data import (
        DEFAULT_STONE_CSV_PATH,
        build_daily_series,
        build_monthly_series,
        build_weekly_series,
        load_interception_stone_rows,
    )
    from module.warehouse_stats.data import (
        DEFAULT_CSV_PATH,
        load_latest_counts,
        resolve_csv_path,
    )

    name: str = kwargs["name"]

    nkasgui = local.gui
    config = nkasgui.nkas_config.read_file(nkasgui.nkas_name)

    csv_path = deep_get(
        config,
        keys=["InterceptionTaskStats", "InterceptionDropStats", "CsvPath"],
        default=DEFAULT_STONE_CSV_PATH,
    )
    items_csv_path = deep_get(
        config,
        keys=["WarehouseStats", "WarehouseStats", "CsvPath"],
        default=DEFAULT_CSV_PATH,
    )
    items_csv_path = resolve_csv_path(items_csv_path, config_name=nkasgui.nkas_name)

    rows = load_interception_stone_rows(csv_path, config_name=nkasgui.nkas_name)
    item_counts = load_latest_counts(items_csv_path)
    outputs: List[Output] = []
    unit_text = t("Gui.Text.InterceptionStoneUnit")
    no_data_text = t("Gui.Text.InterceptionNoData")
    daily_title = t("Gui.Text.InterceptionChartDaily")
    weekly_title = t("Gui.Text.InterceptionChartWeekly")
    monthly_title = t("Gui.Text.InterceptionChartMonthly")
    sum_text = t("Gui.Text.InterceptionChartSum")
    max_text = t("Gui.Text.InterceptionChartMax")
    avg_text = t("Gui.Text.InterceptionChartAvg")
    owned_total_label = t("Gui.Text.InterceptionOwnedTotal")

    owned_row = item_counts.get('custom_module', {})
    owned_count = _format_display_count(owned_row.get('count', ''))
    owned_timestamp = str(owned_row.get('timestamp', '')).strip() or '-'
    scope_name = f"arg_container-interception-chart-{name}"
    run_js(
        """
(() => {
    const scopeName = scope_name;
    const label = owned_total_label;
    const countText = owned_count;
    const timestampText = owned_timestamp;

    const applyHeaderSummary = (attempt) => {
        const scope = document.getElementById(`pywebio-scope-${scopeName}`);
        if (!scope) {
            if (attempt < 30) {
                setTimeout(() => applyHeaderSummary(attempt + 1), 50);
            }
            return;
        }

        const group = scope.closest('[id^="pywebio-scope-group_"]');
        if (!group) {
            if (attempt < 30) {
                setTimeout(() => applyHeaderSummary(attempt + 1), 50);
            }
            return;
        }

        const groupLines = group.querySelectorAll(':scope > p');
        const anchorNode = groupLines.length > 1 ? groupLines[1] : groupLines[0];
        if (!anchorNode) {
            if (attempt < 30) {
                setTimeout(() => applyHeaderSummary(attempt + 1), 50);
            }
            return;
        }

        anchorNode.classList.add('interception-group-title-row');

        group.querySelectorAll('.interception-group-summary').forEach((node) => {
            if (node.parentElement !== anchorNode) {
                node.remove();
            }
        });

        let summary = anchorNode.querySelector('.interception-group-summary');
        if (!summary) {
            summary = document.createElement('span');
            summary.className = 'interception-group-summary';

            const main = document.createElement('span');
            main.className = 'interception-group-summary-main';
            summary.appendChild(main);

            const time = document.createElement('span');
            time.className = 'interception-group-summary-time';
            summary.appendChild(time);

            anchorNode.appendChild(summary);
        }

        const mainNode = summary.querySelector('.interception-group-summary-main');
        const timeNode = summary.querySelector('.interception-group-summary-time');
        if (mainNode) {
            mainNode.textContent = `${label}: ${countText}`;
        }
        if (timeNode) {
            timeNode.textContent = `更新时间: ${timestampText}`;
        }
    };

    applyHeaderSummary(0);
})();
        """,
        scope_name=scope_name,
        owned_total_label=owned_total_label,
        owned_count=str(owned_count),
        owned_timestamp=owned_timestamp,
    )

    if not rows:
        outputs.append(put_text(no_data_text).style("--arg-title--; margin: .1rem .25rem 0 !important;"))
        scope = put_scope(scope_name, outputs)
        scope.style("display: grid; grid-template-columns: 1fr; gap: 0.75rem;")
        return scope

    day_labels, day_values = build_daily_series(rows, days=30)
    week_labels, week_values = build_weekly_series(rows, weeks=12)
    month_labels, month_values = build_monthly_series(rows, months=12)

    chart_specs = [
        {
            'dom_id': f'{scope_name}-daily',
            'title': daily_title,
            'labels': day_labels,
            'values': day_values,
            'color': '#3498db',
            'area_color': 'rgba(52, 152, 219, 0.22)',
            'tone_class': 'interception-tone-daily',
        },
        {
            'dom_id': f'{scope_name}-weekly',
            'title': weekly_title,
            'labels': week_labels,
            'values': week_values,
            'color': '#1abc9c',
            'area_color': 'rgba(26, 188, 156, 0.22)',
            'tone_class': 'interception-tone-weekly',
        },
        {
            'dom_id': f'{scope_name}-monthly',
            'title': monthly_title,
            'labels': month_labels,
            'values': month_values,
            'color': '#f39c12',
            'area_color': 'rgba(243, 156, 18, 0.22)',
            'tone_class': 'interception-tone-monthly',
        },
    ]

    chart_html = (
        '<div class="interception-chart-grid">'
        + ''.join(
            _build_echarts_line_chart_card(
                spec['title'],
                spec['labels'],
                spec['values'],
                sum_text,
                max_text,
                avg_text,
                tone_class=spec['tone_class'],
                dom_id=spec['dom_id'],
            )
            for spec in chart_specs
        )
        + '</div>'
    )
    outputs.append(put_html(chart_html))
    scope = put_scope(scope_name, outputs)
    scope.style("display: grid; grid-template-columns: 1fr; gap: 0.75rem;")
    run_js(
        """
(() => {
    const scopeName = scope_name;
    const charts = chart_specs;
    const unitText = unit_text;
    const isDark = is_dark;

    const pickEChartsModule = (mod) => {
        if (mod && typeof mod.init === 'function') {
            return mod;
        }
        if (mod && mod.default && typeof mod.default.init === 'function') {
            return mod.default;
        }
        return null;
    };

    const resolveEChartsGlobal = () => {
        const candidates = [
            window.echarts,
            globalThis.echarts,
            window.exports,
            globalThis.exports,
            (window.module && window.module.exports) ? window.module.exports : null,
            (globalThis.module && globalThis.module.exports) ? globalThis.module.exports : null,
        ];
        for (const item of candidates) {
            const echarts = pickEChartsModule(item);
            if (echarts) {
                window.echarts = echarts;
                return echarts;
            }
        }
        return null;
    };

    const resolveEChartsFromRequireSync = () => {
        try {
            const req = (typeof window.require === 'function') ? window.require : null;
            if (!req) {
                return null;
            }
            const mod = req('echarts');
            const echarts = pickEChartsModule(mod);
            if (echarts) {
                window.echarts = echarts;
                return echarts;
            }
        } catch (err) {
            return null;
        }
        return null;
    };

    const resolveEChartsFromAmd = () => {
        return new Promise((resolve, reject) => {
            const req = (typeof window.require === 'function') ? window.require : null;
            if (!req) {
                reject(new Error('require() is unavailable'));
                return;
            }

            let settled = false;
            const doneResolve = (echarts) => {
                if (settled) {
                    return;
                }
                settled = true;
                window.echarts = echarts;
                resolve(echarts);
            };
            const doneReject = (err) => {
                if (settled) {
                    return;
                }
                settled = true;
                reject(err);
            };

            try {
                req(
                    ['echarts'],
                    (mod) => {
                        const echarts = pickEChartsModule(mod);
                        if (echarts) {
                            doneResolve(echarts);
                            return;
                        }
                        doneReject(new Error('AMD echarts has no init()'));
                    },
                    (err) => {
                        doneReject(err || new Error('AMD require echarts failed'));
                    },
                );
            } catch (err) {
                doneReject(err);
                return;
            }

            setTimeout(() => {
                doneReject(new Error('AMD require echarts timeout'));
            }, 1200);
        });
    };

    const loadECharts = () => {
        const existing = resolveEChartsGlobal();
        if (existing) {
            return Promise.resolve(existing);
        }
        if (window.__nkasEchartsPromise) {
            return window.__nkasEchartsPromise;
        }

        const cdnUrls = [
            '/static/gui/js/echarts.min.js',
        ];
        window.__nkasEchartsPromise = new Promise((resolve, reject) => {
            let index = 0;
            const failures = [];
            const tryLoad = () => {
                if (index >= cdnUrls.length) {
                    reject(new Error(`Failed to load ECharts (${failures.join(' | ') || 'unknown'})`));
                    return;
                }
                const src = cdnUrls[index++];
                const script = document.createElement('script');
                script.src = src;
                script.async = true;
                const amdDefine =
                    (typeof window.define === 'function' && window.define.amd)
                        ? window.define
                        : null;
                if (amdDefine) {
                    try {
                        window.define = undefined;
                    } catch (err) {
                        // Ignore and continue with default branch.
                    }
                }
                const restoreAmdDefine = () => {
                    if (amdDefine) {
                        try {
                            window.define = amdDefine;
                        } catch (err) {
                            // Ignore restoration errors.
                        }
                    }
                };
                script.onload = () => {
                    restoreAmdDefine();
                    const echarts = resolveEChartsGlobal();
                    if (echarts && typeof echarts.init === 'function') {
                        resolve(echarts);
                        return;
                    }

                    const reqSyncEcharts = resolveEChartsFromRequireSync();
                    if (reqSyncEcharts && typeof reqSyncEcharts.init === 'function') {
                        resolve(reqSyncEcharts);
                        return;
                    }

                    resolveEChartsFromAmd().then((amdEcharts) => {
                        resolve(amdEcharts);
                    }).catch((amdErr) => {
                        const reason = (amdErr && amdErr.message) ? amdErr.message : String(amdErr);
                        failures.push(`loaded: ${src}, but unresolved (${reason})`);
                        script.remove();
                        tryLoad();
                    });
                };
                script.onerror = () => {
                    restoreAmdDefine();
                    failures.push(`error: ${src}`);
                    script.remove();
                    tryLoad();
                };
                document.head.appendChild(script);
            };
            tryLoad();
        }).catch((err) => {
            window.__nkasEchartsPromise = null;
            throw err;
        });
        return window.__nkasEchartsPromise;
    };

    const applyCharts = (attempt) => {
        const scope = document.getElementById(`pywebio-scope-${scopeName}`);
        if (!scope) {
            if (attempt < 30) {
                setTimeout(() => applyCharts(attempt + 1), 50);
            }
            return;
        }

        loadECharts().then((echarts) => {
            const chartMap = window.__nkasInterceptionCharts || {};
            charts.forEach((spec) => {
                const el = document.getElementById(spec.dom_id);
                if (!el) {
                    return;
                }

                if (chartMap[spec.dom_id]) {
                    chartMap[spec.dom_id].dispose();
                }

                const chart = echarts.init(el);
                chart.setOption({
                    animation: true,
                    animationDuration: 300,
                    grid: {
                        left: 46,
                        right: 16,
                        top: 18,
                        bottom: 32,
                    },
                    tooltip: {
                        trigger: 'axis',
                        valueFormatter: (value) => `${value} ${unitText}`,
                    },
                    xAxis: {
                        type: 'category',
                        boundaryGap: false,
                        data: spec.labels,
                        axisTick: {
                            show: false,
                        },
                        axisLabel: {
                            hideOverlap: true,
                        },
                    },
                    yAxis: {
                        type: 'value',
                        min: 0,
                        minInterval: 1,
                        splitNumber: 5,
                        splitLine: {
                            show: true,
                            lineStyle: {
                                color: isDark ? 'rgba(143, 151, 165, 0.14)' : 'rgba(0, 0, 0, 0.10)',
                                width: 1,
                            },
                        },
                    },
                    series: [
                        {
                            type: 'line',
                            data: spec.values,
                            smooth: false,
                            symbol: 'circle',
                            symbolSize: 8,
                            lineStyle: {
                                width: 2,
                                color: spec.color,
                            },
                            itemStyle: {
                                color: spec.color,
                            },
                            areaStyle: {
                                color: spec.area_color,
                            },
                        },
                    ],
                });

                chartMap[spec.dom_id] = chart;
            });
            window.__nkasInterceptionCharts = chartMap;

            if (!window.__nkasInterceptionChartResizeBound) {
                window.__nkasInterceptionChartResizeBound = true;
                window.addEventListener('resize', () => {
                    const allCharts = window.__nkasInterceptionCharts || {};
                    Object.keys(allCharts).forEach((key) => {
                        const chart = allCharts[key];
                        if (chart && typeof chart.resize === 'function') {
                            chart.resize();
                        }
                    });
                });
            }
        }).catch((err) => {
            const reason = (err && err.message) ? err.message : String(err);
            console.error('Interception ECharts failed:', err);
            charts.forEach((spec) => {
                const el = document.getElementById(spec.dom_id);
                if (!el) {
                    return;
                }
                el.innerHTML = `<div style="padding: 1rem; color: #b94a48;">ECharts load failed: ${reason}</div>`;
            });
        });
    };

    applyCharts(0);
})();
        """,
        scope_name=scope_name,
        chart_specs=chart_specs,
        unit_text=unit_text,
        is_dark=(State.theme == 'dark'),
    )
    return scope


_widget_type_to_func: Dict[str, Callable] = {
    "input": put_arg_input,
    "lock": put_arg_state,
    "datetime": put_arg_input,  # TODO
    "select": put_arg_select,
    "textarea": put_arg_textarea,
    "checkbox": put_arg_checkbox,
    "storage": put_arg_storage,
    "state": put_arg_state,
    "stored": put_arg_stored,
    "item_table": put_arg_item_table,
    "interception_stone_import": put_arg_interception_stone_import,
    "interception_stone_charts": put_arg_interception_stone_charts,
}


def put_output(output_kwargs: T_Output_Kwargs) -> Optional[Output]:
    return _widget_type_to_func[output_kwargs["widget_type"]](output_kwargs)


def get_loading_style(shape: str, fill: bool) -> str:
    if fill:
        return f"--loading-{shape}-fill--"
    else:
        return f"--loading-{shape}--"


def put_loading_text(
        text: str,
        shape: str = "border",
        color: str = "dark",
        fill: bool = False,
        size: str = "auto 2px 1fr",
):
    loading_style = get_loading_style(shape=shape, fill=fill)
    return put_row(
        [
            put_loading(shape=shape, color=color).style(loading_style),
            None,
            put_text(text),
        ],
        size=size,
    )

