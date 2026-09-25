"""Global search over the script's settings catalog.

The task/group/field metadata in ``args.json`` and ``menu.json`` is a static
schema shared by every instance - only field *values* live in the per-instance
config.  So the catalog is built once per language and returned without an
instance name; the SPA fans a hit out over its own instance list.  That keeps
this endpoint cheap regardless of how many instances are configured.
"""

from starlette.requests import Request
from starlette.responses import JSONResponse

import module.webui.lang as lang
from module.config.utils import filepath_args, read_file
from module.webui.api.routes_config import _label

DEFAULT_LIMIT = 30
MAX_LIMIT = 100

_catalog_cache = {}


def _entry(context, kind, title, help_text, group=None, group_name=None, field=None):
    # Precompute the lowercase haystack so a query costs one substring scan per
    # entry instead of a handful of .lower() calls on every keystroke.
    haystack = ' '.join(filter(None, (title, help_text, group_name, context['menu_name'], context['task_name'])))
    return {
        'kind': kind, 'title': title, 'help': help_text,
        'group': group, 'group_name': group_name, 'field': field,
        'haystack': haystack.lower(),
        **context,
    }


def _catalog():
    """Return the flat searchable catalog, rebuilt when the UI language changes."""
    language = lang.LANG
    cached = _catalog_cache.get(language)
    if cached is not None:
        return cached

    # task -> (menu_key, menu_name, page).  Tasks missing from the menu are
    # still reachable by URL (the task view renders whatever the schema holds),
    # so they stay searchable and default to the task page.
    task_menu = {}
    for menu_key, item in read_file(filepath_args('menu', 'nkas')).items():
        menu_name = _label(f'Menu.{menu_key}.name', menu_key)
        page = 'tool' if item.get('page') == 'tool' else 'task'
        for task in item.get('tasks', []):
            task_menu[task] = (menu_key, menu_name, page)

    entries = []
    for task, groups in read_file(filepath_args('args', 'nkas')).items():
        menu_key, menu_name, page = task_menu.get(task, ('', '', 'task'))
        context = {'task': task, 'task_name': _label(f'Task.{task}.name', task),
                   'menu': menu_key, 'menu_name': menu_name, 'page': page}
        entries.append(_entry(context, 'task', context['task_name'], _label(f'Task.{task}.help', '')))
        for group, fields in groups.items():
            group_name = _label(f'{group}._info.name', group)
            for arg, spec in fields.items():
                # Mirrors the schema endpoint: hidden fields and script-managed
                # record dumps are not user-facing settings.
                if spec.get('display') in ('hide', 'hide_keep') or spec.get('type') == 'storage':
                    continue
                entries.append(_entry(context, 'field', _label(f'{group}.{arg}.name', arg),
                                      _label(f'{group}.{arg}.help', ''), group=group,
                                      group_name=group_name, field=f'{task}.{group}.{arg}'))

    _catalog_cache[language] = entries
    return entries


def _score(entry, query):
    """Rank a hit by where it matched; None means no match at all."""
    title = entry['title'].lower()
    if title == query:
        return 0
    if title.startswith(query):
        return 1
    if query in title:
        return 2
    if query in entry['task_name'].lower():
        return 3
    return 4 if query in entry['haystack'] else None


async def search(request: Request):
    query = request.query_params.get('q', '').strip().lower()
    if not query:
        return JSONResponse({'query': '', 'total': 0, 'settings': []})
    try:
        limit = min(max(int(request.query_params.get('limit', DEFAULT_LIMIT)), 1), MAX_LIMIT)
    except ValueError:
        limit = DEFAULT_LIMIT

    scored = []
    for index, entry in enumerate(_catalog()):
        score = _score(entry, query)
        if score is not None:
            scored.append((score, index, entry))
    # Stable within a score band so schema order (menu order) is preserved.
    scored.sort(key=lambda item: (item[0], item[1]))

    settings = [{key: value for key, value in entry.items() if key != 'haystack'}
                for _, _, entry in scored[:limit]]
    return JSONResponse({'query': query, 'total': len(scored), 'settings': settings})
