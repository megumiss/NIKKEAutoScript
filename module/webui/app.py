import queue
import threading
from datetime import datetime
from functools import partial
from typing import List, Optional, Dict

from pywebio.io_ctrl import Output
from pywebio.output import use_scope, put_text, put_row, put_scope, put_scrollable, put_button, toast, put_html, \
    put_column, clear, put_buttons, put_collapse, put_table, put_loading
from pywebio.pin import pin_on_change, pin
from pywebio.platform.fastapi import webio_routes
from pywebio.session import local, set_env, run_js, register_thread
from starlette.applications import Starlette

from module.common.enum.webui import CssPath, ICON
from module.config.config import NikkeConfig, Function
from module.config.utils import read_file, filepath_args, deep_iter, deep_get, deep_set, filepath_config, dict_to_kv
from module.logger import logger
from module.webui.base import Frame
from module.webui.lang import t
from module.webui.process_manager import ProcessManager
from module.webui.setting import State
from module.webui.updater import updater
from module.webui.utils import add_css, get_nkas_config_listen_path, parse_pin_value, re_fullmatch, \
    Switch, TaskHandler
from module.webui.widgets import RichLog, put_icon_buttons, BinarySwitchButton, T_Output_Kwargs, put_output

task_handler = TaskHandler()


class NikkeAutoScriptGUI(Frame):
    def init(self) -> None:
        self.NKAS_MENU = read_file(filepath_args("menu", self.nkas_mod))
        self.NKAS_ARGS = read_file(filepath_args("args", self.nkas_mod))
        self._init_nkas_config_watcher()

    def __init__(self) -> None:
        super().__init__()
        self.nkas_mod = "nkas"
        self.nkas_config = NikkeConfig("template")
        self.modified_config_queue = queue.Queue()
        self.init()

    @use_scope("aside", clear=True)
    def set_aside(self) -> None:
        put_icon_buttons(ICON.Menu, onclick=self.ui_dashboard, id='Dashboard'),
        put_icon_buttons(ICON.Setting, onclick=self.ui_setting, id='Setting'),
        put_icon_buttons(ICON.Link, onclick=self.ui_link, id='Link'),
        put_icon_buttons(ICON.Refresh, onclick=self.restart, id='Link2'),

    def restart(self):
        def trigger():
            # with open("./config/reloadflag", mode="w"):
            #     # app ended here and uvicorn will restart whole app
            #     pass
            State.restart_event.set()

        toast('restart uvicorn', position='right', color='#c082fb', duration=1)
        timer = threading.Timer(1, trigger)
        timer.start()
        clearup()

    def show(self):
        self._show()
        self.set_aside()
        self.ui_dashboard()

    def ui_dashboard(self) -> None:
        # 创建一个新的进程
        self.nkas = ProcessManager.get_manager('nkas')
        """
            NikkeConfig.modified不为空时，才会创建nkas.json
        """
        self.nkas_config = NikkeConfig('nkas')
        self.init_aside(name='Dashboard')
        self.dashboard_set_menu()
        self.dashboard_set_content()

    @use_scope("menu", clear=True)
    def dashboard_set_menu(self) -> None:

        put_scope("menu-container"),

        task = 'nkas'

        '''
            ProcessManager.start(func, ev)
            ev: 进程同步标识
            func: 通过创建的线程的执行的方法，在Alas中，默认为执行
            AzurLaneAutoScript(config_name='alas').loop()

        '''
        with use_scope('menu-container'):
            put_scope("scheduler"),
            with use_scope('scheduler'):
                put_row([put_text(t('Gui.Menu.Scheduler')), put_scope("scheduler_btn")])
                with use_scope('scheduler_btn'):
                    switch_scheduler = BinarySwitchButton(
                        label_on=t("Gui.Button.Stop"),
                        label_off=t("Gui.Button.Start"),
                        onclick_on=lambda: self.nkas.stop(),
                        onclick_off=lambda: self.nkas.start(None, updater.event),
                        get_state=lambda: self.nkas.alive,
                        color_on="off",
                        color_off="on",
                        scope="scheduler_btn",
                    )

                    self.task_handler.add(switch_scheduler.g(), 1, True)

            # 显示任务队列
            with use_scope('running'):
                put_row([put_text(t('Gui.Overview.Running'))])
                put_scrollable([put_scope('running_list')], height=None, keep_bottom=False)

            put_scope("pending"),
            with use_scope('pending'):
                put_row([put_text(t('Gui.Overview.Pending'))])
                put_scrollable([put_scope('pending_list')], height=None, keep_bottom=False)

            put_scope("waiting"),
            with use_scope('waiting'):
                put_row([put_text(t('Gui.Overview.Waiting'))])
                put_scrollable([put_scope('waiting_list')], height=None, keep_bottom=False)

    @use_scope("content", clear=True)
    def dashboard_set_content(self):
        log = RichLog("log")
        put_scrollable([put_scope("log", [put_html("")])], height=None, keep_bottom=True).style(
            '--log-scrollable--')

        '''
            task_handler: 子任务处理器
            通过task_handler.add添加的func
            会被带有yield的生成器循环执行，直到该任务被移除

            log.put_log(ProcessManager): 向web渲染通过logger输出的日志
        '''
        self.task_handler.add(log.put_log(self.nkas), delay=0.25, pending_delete=True)

        """
            更新任务队列状态
        """
        self.task_handler.add(self.nkas_update_overview_task, 10, True)

    def ui_setting(self) -> None:
        self.init_aside(name='Setting')
        self.setting_set_menu()

    @use_scope("menu", clear=True)
    def setting_set_menu(self) -> None:
        put_scope("menu_list"),
        with use_scope('menu_list'):
            for key, tasks in deep_iter(self.NKAS_MENU, depth=2):
                menu = key[1]
                task_btn_list = []
                for task in tasks:
                    task_btn_list.append(
                        put_buttons(
                            [
                                {
                                    "label": t(f"Task.{task}.name"),
                                    "value": task,
                                    "color": "menu",
                                }
                            ],
                            onclick=self.nkas_set_group,
                        ).style(f"--menu-{task}--")
                    )
                put_collapse(title=t(f"Menu.{menu}.name"), content=task_btn_list)

    @use_scope("content", clear=True)
    def nkas_set_group(self, task: str) -> None:
        """
        Set arg groups from dict
        """
        put_scope("_groups",
                  [put_scrollable([put_scope("groups")], height=None, keep_bottom=False).style(
                      '--groups-scrollable--'), put_scope("navigator")])
        run_js(
            '''
            $("div[style*='--groups-scrollable--']").addClass('groups-scrollable');
            $('.groups-scrollable > .webio-scrollable').addClass('_groups-scrollable');
            '''
        )
        task_help: str = t(f"Task.{task}.help")
        if task_help:
            put_scope(
                "group__info",
                scope="groups",
                content=[put_text(task_help).style("font-size: 1rem")],
            )

        config = self.nkas_config.read_file('nkas')
        for group, arg_dict in deep_iter(self.NKAS_ARGS[task], depth=1):
            if self.set_group(group, arg_dict, config, task):
                self.set_navigator(group)

    @use_scope("groups")
    def set_group(self, group, arg_dict, config, task):
        group_name = group[0]

        output_list: List[Output] = []
        for arg, arg_dict in deep_iter(arg_dict, depth=1):
            output_kwargs: T_Output_Kwargs = arg_dict.copy()

            # Skip hide
            display: Optional[str] = output_kwargs.pop("display", None)
            if display == "hide":
                continue
            # Disable
            elif display == "disabled":
                output_kwargs["disabled"] = True
            # Output type
            output_kwargs["widget_type"] = output_kwargs.pop("type")

            arg_name = arg[0]  # [arg_name,]
            # Internal pin widget name
            output_kwargs["name"] = f"{task}_{group_name}_{arg_name}"
            # Display title
            output_kwargs["title"] = t(f"{group_name}.{arg_name}.name")

            # Get value from config
            value = deep_get(
                config, [task, group_name, arg_name], output_kwargs["value"]
            )
            # idk
            value = str(value) if isinstance(value, datetime) else value
            # Default value
            output_kwargs["value"] = value
            # Options
            output_kwargs["options"] = options = output_kwargs.pop("option", [])
            # Options label
            options_label = []
            for opt in options:
                options_label.append(t(f"{group_name}.{arg_name}.{opt}"))
            output_kwargs["options_label"] = options_label
            # Help
            arg_help = t(f"{group_name}.{arg_name}.help")
            if arg_help == "" or not arg_help:
                arg_help = None
            output_kwargs["help"] = arg_help
            # Invalid feedback
            output_kwargs["invalid_feedback"] = t("Gui.Text.InvalidFeedBack", value)

            o = put_output(output_kwargs)
            if o is not None:
                # output will inherit current scope when created, override here
                o.spec["scope"] = f"#pywebio-scope-group_{group_name}"
                output_list.append(o)

        if not output_list:
            return 0

        with use_scope(f"group_{group_name}"):
            put_text(t(f"{group_name}._info.name"))
            group_help = t(f"{group_name}._info.help")
            if group_help != "":
                put_text(group_help)
            put_html('<hr class="hr-group">')
            for output in output_list:
                output.show()

        return len(output_list)

    @use_scope("navigator")
    def set_navigator(self, group):
        js = f"""
            $(".groups-scrollable > .webio-scrollable").scrollTop(
                $("#pywebio-scope-group_{group[0]}").position().top
                + 19
            )
            """
        put_button(
            label=t(f"{group[0]}._info.name"),
            onclick=lambda: run_js(js),
            color="navigator",
        )

    def ui_link(self) -> None:
        self.init_aside(name='Link')
        self.link_set_menu()
        self.link_set_content()

    @use_scope("menu", clear=True)
    def link_set_menu(self) -> None:
        put_scope("update"),
        with use_scope('update'):
            put_row([put_text(t('Gui.Menu.Update')).onclick(self.link_set_content)])

        self.link_set_content()

    @use_scope("content", clear=True)
    def link_set_content(self) -> None:
        put_row(
            content=[put_scope("updater_loading"), None, put_scope("updater_state")],
            size="auto .25rem 1fr",
        )

        put_scope("updater_btn")
        put_scope("updater_info")
        put_scope("updater_detail")

        def update_table():
            with use_scope("updater_info", clear=True):
                local_commit = updater.get_commit(short_sha1=True)
                upstream_commit = updater.get_commit(
                    f"origin/{updater.Branch}", short_sha1=True
                )
                put_table(
                    [
                        [t("Gui.Update.Local"), *local_commit],
                        [t("Gui.Update.Upstream"), *upstream_commit],
                    ],
                    header=[
                        "",
                        "SHA1",
                        t("Gui.Update.Author"),
                        t("Gui.Update.Time"),
                        t("Gui.Update.Message"),
                    ],
                )
            with use_scope("updater_detail", clear=True):
                put_text(t("Gui.Update.DetailedHistory"))
                history = updater.get_commit(
                    f"origin/{updater.Branch}", n=20, short_sha1=True
                )
                put_scrollable([put_table(
                    [commit for commit in history],
                    header=[
                        "SHA1",
                        t("Gui.Update.Author"),
                        t("Gui.Update.Time"),
                        t("Gui.Update.Message"),
                    ],
                )], height=220, keep_bottom=False).style(
                    '--commit-history-scrollable--')
                run_js(
                    '''
                        $("div[style*='--commit-history-scrollable--']").children("div").css({"max-height": "61vh"});
                    '''
                )

        def u(state):
            if state == -1:
                return
            clear("updater_loading")
            clear("updater_state")
            clear("updater_btn")
            if state == 0:
                '''
                    状态：已是最新版本，按钮：检查更新
                '''
                put_loading("border", "secondary", scope="updater_loading").style(
                    "--loading-border-fill--"
                )
                put_text(t("Gui.Update.UpToDate"), scope="updater_state")
                put_button(
                    t("Gui.Button.CheckUpdate"),
                    onclick=updater.check_update,
                    color="info",
                    scope="updater_btn",
                )
                update_table()
            elif state == 1:
                '''
                    状态：非最新版本，按钮：开始更新
                '''
                put_loading("grow", "success", "updater_loading").style(
                    "--loading-grow--"
                )
                put_text(t("Gui.Update.HaveUpdate"), scope="updater_state")
                put_button(
                    t("Gui.Button.ClickToUpdate"),
                    onclick=updater.run_update,
                    color="success",
                    scope="updater_btn",
                )
                update_table()
            elif state == "checking":
                '''
                    状态：检查更新中，按钮：无
                '''
                put_loading("border", "primary", "updater_loading").style(
                    "--loading-border--"
                )
                put_text(t("Gui.Update.UpdateChecking"), scope="updater_state")
            elif state == "failed":
                '''
                    状态：更新失败，按钮：尝试重新更新
                '''
                put_loading("grow", "danger", "updater_loading").style(
                    "--loading-grow--"
                )
                put_text(t("Gui.Update.UpdateFailed"), scope="updater_state")
                put_button(
                    t("Gui.Button.RetryUpdate"),
                    onclick=updater.run_update,
                    color="primary",
                    scope="updater_btn",
                )
            elif state == "start":
                '''
                    状态：开始更新，按钮：取消更新
                '''
                put_loading("border", "primary", "updater_loading").style(
                    "--loading-border--"
                )
                put_text(t("Gui.Update.UpdateStart"), scope="updater_state")
                put_button(
                    t("Gui.Button.CancelUpdate"),
                    onclick=updater.cancel,
                    color="danger",
                    scope="updater_btn",
                )
            elif state == "wait":
                '''
                    状态：等待任务结束，按钮：取消更新
                '''
                put_loading("border", "primary", "updater_loading").style(
                    "--loading-border--"
                )
                put_text(t("Gui.Update.UpdateWait"), scope="updater_state")
                put_button(
                    t("Gui.Button.CancelUpdate"),
                    onclick=updater.cancel,
                    color="danger",
                    scope="updater_btn",
                )
            elif state == "run update":
                '''
                    状态：更新中，按钮：取消更新(禁用)
                '''
                put_loading("border", "primary", "updater_loading").style(
                    "--loading-border--"
                )
                put_text(t("Gui.Update.UpdateRun"), scope="updater_state")
                put_button(
                    t("Gui.Button.CancelUpdate"),
                    onclick=updater.cancel,
                    color="danger",
                    scope="updater_btn",
                    disabled=True,
                )
            elif state == "reload":
                '''
                    状态：更新成功，即将热重启，按钮：无
                '''
                put_loading("grow", "success", "updater_loading").style(
                    "--loading-grow--"
                )
                put_text(t("Gui.Update.UpdateSuccess"), scope="updater_state")
                update_table()
            elif state == "finish":
                '''
                    状态：更新成功，需要手动重启，按钮：无
                '''
                put_loading("grow", "success", "updater_loading").style(
                    "--loading-grow--"
                )
                put_text(t("Gui.Update.UpdateFinish"), scope="updater_state")
                update_table()
            elif state == "cancel":
                '''
                    状态：取消更新，重启调度器，按钮：取消更新(禁用)
                '''
                put_loading("border", "danger", "updater_loading").style(
                    "--loading-border--"
                )
                put_text(t("Gui.Update.UpdateCancel"), scope="updater_state")
                put_button(
                    t("Gui.Button.CancelUpdate"),
                    onclick=updater.cancel,
                    color="danger",
                    scope="updater_btn",
                    disabled=True,
                )
            else:
                '''
                    状态：发生错误，按钮：无
                '''
                put_text(
                    "Something went wrong, please contact develops",
                    scope="updater_state",
                )
                put_text(f"state: {state}", scope="updater_state")

        updater_switch = Switch(
            status=u, get_state=lambda: updater.state, name="updater"
        )
        self.task_handler.add(updater_switch.g(), delay=0.5, pending_delete=True)

        u('checking')

    def nkas_update_overview_task(self):
        self.nkas_config.load()
        self.nkas_config.get_next_task()

        if len(self.nkas_config.pending_task) >= 1:
            if self.nkas.alive:
                running = self.nkas_config.pending_task[:1]
                pending = self.nkas_config.pending_task[1:]
            else:
                running = []
                pending = self.nkas_config.pending_task[:]
        else:
            running = []
            pending = []
        waiting = self.nkas_config.waiting_task

        def put_task(func: Function):
            with use_scope(f"overview-task_{func.command}"):
                put_column(
                    [
                        put_text(t(f"Task.{func.command}.name")).style("--arg-title--"),
                        put_text(str(func.next_run)).style("--arg-help--"),
                    ],
                    size="auto auto",
                )

        clear("running_list")
        clear("pending_list")
        clear("waiting_list")

        with use_scope("running_list"):
            if running:
                for task in running:
                    put_task(task)

        with use_scope("pending_list"):
            if pending:
                for task in pending:
                    put_task(task)

        with use_scope("waiting_list"):
            if waiting:
                for task in waiting:
                    put_task(task)

    def _init_nkas_config_watcher(self) -> None:
        def put_queue(path, value):
            self.modified_config_queue.put({"name": path, "value": value})

        for path in get_nkas_config_listen_path(self.NKAS_ARGS):
            """
                pin_on_change会监听name属性为"_".join(path)的输入组件的值
                在213行，通过 output_kwargs["name"] = f"{task}_{group_name}_{arg_name} 定义
                在改变被pin_on_change监听的属性时，会调用onchange的方法，更改的值为该方法的最后一个参数？
            """
            pin_on_change(
                name="_".join(path), onchange=partial(put_queue, ".".join(path))
            )
        logger.info("Init config watcher done.")

    def _nkas_thread_update_config(self) -> None:
        modified = {}
        while self.alive:
            try:
                d = self.modified_config_queue.get(timeout=10)
                config_name = 'nkas'
                read = self.nkas_config.read_file
                write = self.nkas_config.write_file
            except queue.Empty:
                continue
            modified[d["name"]] = d["value"]
            while True:
                try:
                    d = self.modified_config_queue.get(timeout=1)
                    modified[d["name"]] = d["value"]
                except queue.Empty:
                    self._save_config(modified, config_name, read, write)
                    modified.clear()
                    break

    def _save_config(
            self,
            modified: Dict[str, str],
            config_name: str,
            read=State.config_updater.read_file,
            write=State.config_updater.write_file,
    ) -> None:
        try:
            valid = []
            invalid = []
            config = read(config_name)
            for k, v in modified.copy().items():
                valuetype = deep_get(self.NKAS_ARGS, k + ".valuetype")
                v = parse_pin_value(v, valuetype)
                validate = deep_get(self.NKAS_ARGS, k + ".validate")
                if not len(str(v)):
                    default = deep_get(self.NKAS_ARGS, k + ".value")
                    modified[k] = default
                    deep_set(config, k, default)
                    valid.append(k)
                    pin["_".join(k.split("."))] = default

                elif not validate or re_fullmatch(validate, v):
                    deep_set(config, k, v)
                    modified[k] = v
                    valid.append(k)

                else:
                    modified.pop(k)
                    invalid.append(k)
                    logger.warning(f"Invalid value {v} for key {k}, skip saving.")
            self.pin_remove_invalid_mark(valid)
            self.pin_set_invalid_mark(invalid)
            if modified:
                toast(
                    t("Gui.Toast.ConfigSaved"),
                    duration=1,
                    position="right",
                    color="success",
                )
                logger.info(
                    f"Save config {filepath_config(config_name)}, {dict_to_kv(modified)}"
                )
                write(config_name, config)
        except Exception as e:
            logger.exception(e)

    def run(self) -> None:
        # 标签标题，加载时显示渲染动画
        set_env(title="NKAS", output_animation=False)
        add_css(CssPath.NKAS)

        run_js(
            """
        reload = 1;
        WebIO._state.CurrentSession.on_session_close(
            ()=>{
                setTimeout(
                    ()=>{
                        if (reload == 1){
                            location.reload();
                        }
                    }, 4000
                )
            }
        );
        """
        )

        _thread_save_config = threading.Thread(target=self._nkas_thread_update_config)
        register_thread(_thread_save_config)
        _thread_save_config.start()

        # aside = get_localstorage("aside")
        self.show()

        def goto_update():
            self.ui_link()

        update_switch = Switch(
            status={
                1: lambda: toast(
                    t("Gui.Toast.ClickToUpdate"),
                    duration=0,
                    position="right",
                    color="success",
                    onclick=goto_update,
                )
            },
            get_state=lambda: updater.state,
            name="update_state",
        )

        '''
            task_handler: 子任务处理器
            task_handler.start
            通过子线程在后台运行TaskHandler.loop()方法，循环运行添加的任务，直到调用stop
        '''
        self.task_handler.add(update_switch.g(), 1)
        self.task_handler.start()


def startup():
    # 初始化多进程数据共享
    State.init()
    updater.event = State.manager.Event()
    if updater.delay > 0:
        task_handler.add(updater.check_update, updater.delay)
    task_handler.add(updater.schedule_update(), 86400)
    task_handler.start()


def clearup():
    """
    Notice: Ensure run it before uvicorn reload app,
    all process will NOT EXIT after close electron app.
    """

    logger.info("Start clearup")
    for nkas in ProcessManager._processes.values():
        nkas.stop()
    State.clearup()


def app():
    def index():
        gui = NikkeAutoScriptGUI()
        local.gui = gui
        gui.run()

    instances: List[str] = []

    routes = webio_routes(applications=[index])
    app = Starlette(
            routes=routes,
            debug=True,
            on_startup=[
                startup,
                lambda: ProcessManager.restart_processes(
                    instances=instances, ev=updater.event
                ),
            ],
            on_shutdown=[clearup]
    )
    
    return app
