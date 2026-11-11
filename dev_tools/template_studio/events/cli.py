"""
活动模板工作台（CLI）：剧情活动模板的场景标注、提案收集与导出。

配合 dev_tools/template_studio/data/event_template_rules.yaml（模板清单/场景规则库）与
tmp/event_studio/<event_id>/session.json（会话）使用。
校对界面是 Web 版：python dev_tools/template_studio.py

典型流程：
    1. 用户截好各场景整屏截图（template_capture 或手动）
    2. 用户在 Web 界面新建会话、登记截图及所属场景，生成 agent 任务
    3. agent 写回裁剪草稿，用户在 Web 界面逐个校对（确认/打回）
    4. export 导出 confirmed 模板到 assets/zh-CN/event_dated/<event_id>/ 并重新生成 assets.py

CLI:
    python dev_tools/template_studio/events/cli.py new --event-id event_20261029 \
        --name "New Event" --base-event event_20261008 --position 1 --phase day1
    python dev_tools/template_studio/events/cli.py import <截图路径> [--scene ...] [--state ...]
    python dev_tools/template_studio/events/cli.py checklist
    python dev_tools/template_studio/events/cli.py propose --name ... --file <截图> --box x0,y0,x1,y1
    python dev_tools/template_studio/events/cli.py set-status --name STORY_1_NORMAL --value confirmed
    python dev_tools/template_studio/events/cli.py export
    python dev_tools/template_studio/events/cli.py gui          # 启动 Web 校对界面

除 new 外，子命令默认作用于最近使用的会话（tmp/event_studio/.current），可用 --event-id 覆盖。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from dev_tools.template_studio.events import model  # noqa: E402

STATUS_LABEL = {'missing': '缺失', 'draft': '草稿', 'confirmed': '已确认', 'rejected': '打回'}


def current_event_id():
    path = os.path.join(model.SESSION_ROOT, '.current')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read().strip() or None
    except OSError:
        return None


def set_current_event_id(event_id):
    path = os.path.join(model.SESSION_ROOT, '.current')
    if event_id is None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return
    os.makedirs(model.SESSION_ROOT, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(event_id)


def rule_info_map(rules, event_type, phase):
    return {t['name']: t for t in model.checklist(rules, event_type, phase)}


def parse_box(text):
    parts = [int(v) for v in text.split(',')]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError('box 格式: x0,y0,x1,y1')
    return parts


def cli_session(args):
    event_id = getattr(args, 'event_id', None) or current_event_id()
    if not event_id:
        raise SystemExit('未指定会话：使用 --event-id 或先运行 new')
    return model.load_session(event_id)


def main():
    parser = argparse.ArgumentParser(description='活动模板工作台')
    sub = parser.add_subparsers(dest='cmd')

    p = sub.add_parser('new', help='新建会话')
    p.add_argument('--event-id', required=True)
    p.add_argument('--name', required=True, help='活动名称')
    p.add_argument('--base-event', required=True, help='要复制的历史活动 ID，类型随该活动继承')
    p.add_argument('--position', type=int, required=True, choices=[1, 2], help='EVENTS 序号：1=Event，2=Event2')
    p.add_argument('--phase', default='day1', help='如 day1 / story2 / story2_hard / hard')

    p = sub.add_parser('import', help='登记截图及其场景')
    p.add_argument('files', nargs='+')
    p.add_argument('--event-id')
    p.add_argument('--scene', default='')
    p.add_argument('--state', default='')
    p.add_argument('--note', default='')

    p = sub.add_parser('checklist', help='打印模板清单与状态')
    p.add_argument('--event-id')

    p = sub.add_parser('propose', help='登记模板裁剪提案')
    p.add_argument('--event-id')
    p.add_argument('--name', required=True)
    p.add_argument('--file', required=True)
    p.add_argument('--box', type=parse_box, required=True, help='x0,y0,x1,y1')
    p.add_argument('--mode', choices=list(model.VALID_MODE), default=None)
    p.add_argument('--proposer', default='agent')

    p = sub.add_parser('set-status', help='设置模板状态')
    p.add_argument('--event-id')
    p.add_argument('--name', required=True)
    p.add_argument('--value', required=True, choices=list(model.VALID_STATUS))

    p = sub.add_parser('export', help='导出 confirmed 模板并重新生成 assets.py')
    p.add_argument('--event-id')
    p.add_argument('--out-root', default=None, help='覆盖输出目录（默认 assets/zh-CN/event_dated/<event_id>）')
    p.add_argument('--no-extract', action='store_true', help='不重新生成 assets.py')
    p.add_argument('--allow-partial', action='store_true', help='显式导出尚未完成的阶段（仅 CLI）')

    p = sub.add_parser('phase', help='追加更新阶段，保留已有截图和校对结果')
    p.add_argument('--event-id')
    p.add_argument('--phase', required=True)

    p = sub.add_parser('prepare', help='根据场景标注生成 agent 任务文件')
    p.add_argument('--event-id')

    p = sub.add_parser('apply-result', help='将 agent 结果批量写入会话，全部保持草稿')
    p.add_argument('--event-id')
    p.add_argument('--file', required=True)

    sub.add_parser('gui', help='启动 Web 校对界面（同 template_studio.py）')

    args = parser.parse_args()

    if args.cmd in (None, 'gui'):
        from dev_tools.template_studio.app import main as run_web
        run_web([])
        return

    rules = model.load_rules()

    if args.cmd == 'new':
        if os.path.isfile(model.session_path(args.event_id)):
            raise SystemExit('会话已经存在，请打开已有会话或使用 phase 追加更新阶段')
        session = model.create_event_session(
            args.event_id, args.name, args.base_event, args.position, args.phase, rules)
        set_current_event_id(args.event_id)
        print(f'已创建会话: {model.session_path(args.event_id)}')
        print(f'模板清单 {len(session["templates"])} 项')
        return

    session = cli_session(args)

    if args.cmd == 'phase':
        model.advance_phase(session, args.phase, rules)
        model.save_session(session)
        print(f'已追加阶段: {args.phase}')
    elif args.cmd == 'import':
        if args.scene and f'{session["phase"]}:{args.scene}' not in model.session_scenes(session, rules):
            raise SystemExit('无效场景')
        for f in args.files:
            model.add_screenshot(session, f, scene=args.scene, state=args.state, note=args.note)
            print(f'已登记: {f} (scene={args.scene or "-"})')
        model.save_session(session)
    elif args.cmd == 'checklist':
        rule_map = {t['name']: t for t in model.session_checklist(session, rules)}
        for t in session['templates']:
            rule = rule_map.get(t['name'], {})
            flags = []
            if rule.get('optional'):
                flags.append('可选')
            if rule.get('requires'):
                flags.append(rule['requires'])
            src = os.path.basename(t['source']) if t.get('source') else '-'
            print(f'[{STATUS_LABEL[t["status"]]}] {t["name"]}  phase={t["phase"]}  scene={t.get("scene") or "-"}'
                  f'  mode={t["mode"]}  src={src}  box={t.get("box") or "-"}'
                  + (f'  ({"; ".join(flags)})' if flags else ''))
        todo = model.missing(session)
        print(f'\n未确认必需项: {len(todo)}' + (f': {", ".join(todo)}' if todo else ''))
    elif args.cmd == 'propose':
        t = model.propose(session, args.name, args.file, args.box, mode=args.mode, proposer=args.proposer)
        model.save_session(session)
        print(f'已登记提案: {t["name"]} box={t["box"]} (draft)')
    elif args.cmd == 'prepare':
        result = model.write_agent_request(session, rules)
        print(result['prompt'])
        for warning in result['warnings']:
            print(f'缺少截图: {warning}')
    elif args.cmd == 'apply-result':
        result = model.apply_agent_result(session, args.file)
        model.save_session(session)
        print(f'已写入 {result["proposals"]} 个草稿，{result["unresolved"]} 个待补充项，请刷新工作台校对')
    elif args.cmd == 'set-status':
        model.set_status(session, args.name, args.value)
        model.save_session(session)
        print(f'{args.name} -> {args.value}')
    elif args.cmd == 'export':
        result = model.export_session(session, rules, out_root=args.out_root,
                                      run_extract=False if args.no_extract else None, allow_partial=args.allow_partial)
        print(f'已导出 {len(result["exported"])} 个: {", ".join(result["exported"])}')
        print(f'输出目录: {result["out_dir"]}')
        if result['missing']:
            print(f'未确认必需项: {", ".join(result["missing"])}')


if __name__ == '__main__':
    main()
