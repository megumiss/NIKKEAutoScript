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

from module.logger import HTMLConsole, Highlighter, WEB_THEME
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


def _build_svg_line_chart(
    title: str,
    labels: List[str],
    values: List[int],
    unit_text: str,
    sum_text: str,
    max_text: str,
    avg_text: str,
    tone_class: str = '',
) -> str:
    import html

    if not labels or not values:
        labels = ['-']
        values = [0]

    width = 860
    height = 220
    left = 52
    right = 18
    top = 18
    bottom = 36
    plot_w = width - left - right
    plot_h = height - top - bottom

    max_val = max(max(values), 1)
    count = len(values)
    x_step = plot_w / max(count - 1, 1)

    points = []
    for i, value in enumerate(values):
        x = left + i * x_step
        y = top + plot_h * (1 - (value / max_val))
        points.append((x, y, value))

    point_str = ' '.join(f'{x:.2f},{y:.2f}' for x, y, _ in points)
    if len(points) == 1:
        px, py, _ = points[0]
        point_str = f'{left:.2f},{py:.2f} {left + plot_w:.2f},{py:.2f}'

    area_points = point_str + f' {left + plot_w:.2f},{top + plot_h:.2f} {left:.2f},{top + plot_h:.2f}'

    y_ticks = []
    for i in range(5):
        ratio = i / 4
        value = int(round(max_val * (1 - ratio)))
        y = top + plot_h * ratio
        y_ticks.append((value, y))

    tick_count = min(6, len(labels))
    x_tick_idx = {
        int(round(i * (len(labels) - 1) / max(tick_count - 1, 1)))
        for i in range(tick_count)
    }

    circles = []
    x_labels = []
    for i, (x, y, value) in enumerate(points):
        circles.append(
            (
                f'<circle cx="{x:.2f}" cy="{y:.2f}" r="2.6" class="interception-chart-point">'
                f'<title>{html.escape(labels[i])}: {value} {html.escape(unit_text)}</title>'
                f'</circle>'
            )
        )
        if i in x_tick_idx:
            label = html.escape(labels[i])
            x_labels.append(
                f'<text x="{x:.2f}" y="{height - 12}" class="interception-chart-axis-text" text-anchor="middle">{label}</text>'
            )

    y_grid = []
    for value, y in y_ticks:
        y_grid.append(f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_w}" y2="{y:.2f}" class="interception-chart-grid" />')
        y_grid.append(
            f'<text x="{left - 8}" y="{y + 4:.2f}" class="interception-chart-axis-text" text-anchor="end">{value}</text>'
        )

    total = sum(values)
    peak = max(values)
    average = total / len(values) if values else 0
    range_text = labels[0] if len(labels) == 1 else f'{labels[0]} - {labels[-1]}'
    latest_marker = ''
    if points:
        latest_x, latest_y, latest_value = points[-1]
        latest_marker = (
            f'<circle cx="{latest_x:.2f}" cy="{latest_y:.2f}" r="4.4" class="interception-chart-point-latest">'
            f'<title>{html.escape(labels[-1])}: {latest_value} {html.escape(unit_text)}</title>'
            f'</circle>'
        )

    return (
        f'<div class="interception-chart-card {html.escape(tone_class)}">'
        f'<div class="interception-chart-head">'
        f'<div class="interception-chart-title">{html.escape(title)}</div>'
        f'<div class="interception-chart-range">{html.escape(range_text)}</div>'
        f'</div>'
        f'<svg class="interception-chart-svg" viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet">'
        f'{"".join(y_grid)}'
        f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" class="interception-chart-axis" />'
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" class="interception-chart-axis" />'
        f'<polygon points="{area_points}" class="interception-chart-area" />'
        f'<polyline points="{point_str}" class="interception-chart-line" />'
        f'{"".join(circles)}'
        f'{latest_marker}'
        f'{"".join(x_labels)}'
        f'</svg>'
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

    name: str = kwargs["name"]

    nkasgui = local.gui
    config = nkasgui.nkas_config.read_file(nkasgui.nkas_name)

    csv_path = deep_get(
        config,
        keys=["InterceptionTaskStats", "InterceptionDropStats", "CsvPath"],
        default=DEFAULT_STONE_CSV_PATH,
    )

    rows = load_interception_stone_rows(csv_path, config_name=nkasgui.nkas_name)
    outputs: List[Output] = []
    unit_text = t("Gui.Text.InterceptionStoneUnit")
    no_data_text = t("Gui.Text.InterceptionNoData")
    daily_title = t("Gui.Text.InterceptionChartDaily")
    weekly_title = t("Gui.Text.InterceptionChartWeekly")
    monthly_title = t("Gui.Text.InterceptionChartMonthly")
    sum_text = t("Gui.Text.InterceptionChartSum")
    max_text = t("Gui.Text.InterceptionChartMax")
    avg_text = t("Gui.Text.InterceptionChartAvg")

    if not rows:
        outputs.append(put_text(no_data_text))
        scope = put_scope(f"arg_container-interception-chart-{name}", outputs)
        scope.style("display: grid; grid-template-columns: 1fr; gap: 0.75rem;")
        return scope

    day_labels, day_values = build_daily_series(rows, days=30)
    week_labels, week_values = build_weekly_series(rows, weeks=12)
    month_labels, month_values = build_monthly_series(rows, months=12)

    chart_html = (
        '<div class="interception-chart-grid">'
        + _build_svg_line_chart(
            daily_title,
            day_labels,
            day_values,
            unit_text,
            sum_text,
            max_text,
            avg_text,
            tone_class='interception-tone-daily',
        )
        + _build_svg_line_chart(
            weekly_title,
            week_labels,
            week_values,
            unit_text,
            sum_text,
            max_text,
            avg_text,
            tone_class='interception-tone-weekly',
        )
        + _build_svg_line_chart(
            monthly_title,
            month_labels,
            month_values,
            unit_text,
            sum_text,
            max_text,
            avg_text,
            tone_class='interception-tone-monthly',
        )
        + '</div>'
    )
    outputs.append(put_html(chart_html))
    scope = put_scope(f"arg_container-interception-chart-{name}", outputs)
    scope.style("display: grid; grid-template-columns: 1fr; gap: 0.75rem;")
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

