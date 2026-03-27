import argparse
import base64
import json
import shutil
import threading
import time
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List, Optional

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / 'config' / 'warehouse_items.yaml'
DEFAULT_ASSETS = ROOT / 'assets' / 'zh-CN' / 'warehouse_stats'
YAML_RW = YAML()
YAML_RW.preserve_quotes = True
YAML_RW.width = 120
YAML_RW.indent(mapping=2, sequence=4, offset=2)


HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Warehouse Items Sorter</title>
  <style>
    body { margin: 0; font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; background:#f3f5f7; color:#1f2937; }
    .top { position: sticky; top: 0; z-index: 10; background:#fff; border-bottom:1px solid #e5e7eb; padding:10px 14px; display:flex; gap:10px; align-items:center; }
    button { border:0; background:#2563eb; color:#fff; padding:8px 14px; border-radius:8px; cursor:pointer; font-size:14px; }
    button.secondary { background:#6b7280; }
    button:disabled { opacity:.5; cursor:not-allowed; }
    .msg { font-size:13px; color:#374151; }
    .wrap { padding:12px; display:flex; flex-direction:column; gap:10px; }
    .group { background:#fff; border:1px solid #d1d5db; border-radius:10px; padding:10px; }
    .group.dragging, .item.dragging { opacity:.45; }
    .group.placeholder { border:2px dashed #93c5fd; background:#eff6ff; min-height:96px; }
    .group-head { display:flex; align-items:center; justify-content:space-between; padding-bottom:8px; border-bottom:1px dashed #d1d5db; margin-bottom:8px; }
    .group-title { font-weight:700; font-size:15px; }
    .group-id { font-size:12px; color:#6b7280; margin-left:8px; }
    .items { display:flex; flex-wrap:wrap; gap:8px; min-height:50px; align-items:stretch; }
    .item { width:220px; border:1px solid #e5e7eb; border-radius:8px; background:#fafafa; padding:8px; display:flex; gap:8px; align-items:center; }
    .item.placeholder { border:2px dashed #93c5fd; background:#eff6ff; min-height:60px; }
    .icon { width:44px; height:44px; border-radius:6px; background:#111827; object-fit:contain; }
    .meta { min-width:0; }
    .name { font-size:13px; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .sub { font-size:12px; color:#6b7280; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .hint { font-size:12px; color:#6b7280; margin-left:auto; }
  </style>
</head>
<body>
  <div class="top">
    <button id="saveBtn">保存到 YAML</button>
    <button id="reloadBtn" class="secondary">重新加载</button>
    <span class="msg" id="msg">拖动分组或物品卡片调整顺序</span>
  </div>
  <div class="wrap" id="groups"></div>
  <script>
    const groupsEl = document.getElementById("groups");
    const msgEl = document.getElementById("msg");
    const saveBtn = document.getElementById("saveBtn");
    const reloadBtn = document.getElementById("reloadBtn");
    let drag = null;
    const itemPlaceholder = document.createElement("div");
    itemPlaceholder.className = "item placeholder";
    const groupPlaceholder = document.createElement("div");
    groupPlaceholder.className = "group placeholder";

    function setMsg(text, isError=false) {
      msgEl.textContent = text;
      msgEl.style.color = isError ? "#b91c1c" : "#374151";
    }

    function getClosestItem(container, x, y) {
      const candidates = [...container.querySelectorAll(".item:not(.dragging)")];
      if (candidates.length === 0) return null;
      let best = null;
      let bestDist = Infinity;
      for (const el of candidates) {
        const r = el.getBoundingClientRect();
        const cx = r.left + r.width / 2;
        const cy = r.top + r.height / 2;
        const d = (x - cx) * (x - cx) + (y - cy) * (y - cy);
        if (d < bestDist) {
          bestDist = d;
          best = el;
        }
      }
      return best;
    }

    function makeItemCard(item) {
      const card = document.createElement("div");
      card.className = "item";
      card.draggable = true;
      card.dataset.itemId = item.id;
      card.innerHTML = `
        <img class="icon" src="${item.icon_url}" alt="${item.display_name}" />
        <div class="meta">
          <div class="name">${item.display_name}</div>
          <div class="sub">${item.id}</div>
          <div class="sub">${item.scan_method} · ${item.scan_page}</div>
        </div>
      `;
      card.addEventListener("dragstart", (e) => {
        drag = { type: "item", el: card };
        card.classList.add("dragging");
        itemPlaceholder.style.width = `${card.offsetWidth}px`;
        itemPlaceholder.style.height = `${card.offsetHeight}px`;
        card.parentElement?.insertBefore(itemPlaceholder, card.nextSibling);
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", item.id);
      });
      card.addEventListener("dragend", () => {
        if (itemPlaceholder.parentElement) {
          itemPlaceholder.replaceWith(card);
        }
        itemPlaceholder.remove();
        card.classList.remove("dragging");
        drag = null;
      });
      return card;
    }

    function makeGroup(group) {
      const box = document.createElement("div");
      box.className = "group";
      box.draggable = true;
      box.dataset.groupId = group.id;
      box.innerHTML = `
        <div class="group-head">
          <div><span class="group-title">${group.name}</span><span class="group-id">${group.id}</span></div>
          <span class="hint">拖动分组</span>
        </div>
        <div class="items"></div>
      `;
      const itemsEl = box.querySelector(".items");
      group.items.forEach((it) => itemsEl.appendChild(makeItemCard(it)));

      box.addEventListener("dragstart", (e) => {
        if (e.target !== box) return;
        drag = { type: "group", el: box };
        box.classList.add("dragging");
        groupPlaceholder.style.height = `${box.offsetHeight}px`;
        box.parentElement?.insertBefore(groupPlaceholder, box.nextSibling);
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", group.id);
      });
      box.addEventListener("dragend", () => {
        if (groupPlaceholder.parentElement) {
          groupPlaceholder.replaceWith(box);
        }
        groupPlaceholder.remove();
        box.classList.remove("dragging");
        drag = null;
      });
      box.addEventListener("dragover", (e) => {
        if (!drag || drag.type !== "group") return;
        if (drag.el === box) return;
        e.preventDefault();
        const rect = box.getBoundingClientRect();
        const after = e.clientY > rect.top + rect.height / 2;
        groupsEl.insertBefore(groupPlaceholder, after ? box.nextSibling : box);
      });

      itemsEl.addEventListener("dragover", (e) => {
        if (!drag || drag.type !== "item") return;
        e.preventDefault();
        const target = getClosestItem(itemsEl, e.clientX, e.clientY);
        if (!target) {
          itemsEl.appendChild(itemPlaceholder);
          return;
        }
        const rect = target.getBoundingClientRect();
        const before = (e.clientY < rect.top + rect.height / 2) || (
          Math.abs(e.clientY - (rect.top + rect.height / 2)) < rect.height * 0.35 &&
          e.clientX < rect.left + rect.width / 2
        );
        const ref = before ? target : target.nextSibling;
        if (itemPlaceholder.parentElement !== itemsEl || itemPlaceholder.nextSibling !== ref) {
          itemsEl.insertBefore(itemPlaceholder, ref);
        }
      });

      box.addEventListener("dragover", (e) => {
        if (!drag || drag.type !== "item") return;
        e.preventDefault();
        if (!itemsEl.contains(e.target)) {
          itemsEl.appendChild(itemPlaceholder);
        }
      });

      itemsEl.addEventListener("drop", (e) => {
        if (!drag || drag.type !== "item") return;
        e.preventDefault();
        const ref = itemPlaceholder.parentElement === itemsEl ? itemPlaceholder : null;
        if (ref) {
          itemsEl.insertBefore(drag.el, ref);
          itemPlaceholder.remove();
        } else {
          itemsEl.appendChild(drag.el);
        }
      });

      return box;
    }

    function collectPayload() {
      const groups = [...groupsEl.querySelectorAll(".group")].map((g) => ({
        id: g.dataset.groupId,
        items: [...g.querySelectorAll(".item")].map((it) => it.dataset.itemId),
      }));
      return { groups };
    }

    async function loadData() {
      const res = await fetch("/api/data");
      const data = await res.json();
      groupsEl.innerHTML = "";
      data.groups.forEach((g) => groupsEl.appendChild(makeGroup(g)));
      setMsg(`已加载 ${data.groups.length} 个分组，可直接拖动排序`);
    }

    groupsEl.addEventListener("dragover", (e) => {
      if (!drag || drag.type !== "group") return;
      e.preventDefault();
      const groups = [...groupsEl.querySelectorAll(".group:not(.dragging)")];
      const next = groups.find((g) => {
        const r = g.getBoundingClientRect();
        return e.clientY < r.top + r.height / 2;
      });
      if (next) groupsEl.insertBefore(groupPlaceholder, next);
      else groupsEl.appendChild(groupPlaceholder);
    });

    groupsEl.addEventListener("drop", (e) => {
      if (!drag || drag.type !== "group") return;
      e.preventDefault();
      const ref = groupPlaceholder.parentElement === groupsEl ? groupPlaceholder : null;
      if (ref) {
        groupsEl.insertBefore(drag.el, ref);
        groupPlaceholder.remove();
      } else {
        groupsEl.appendChild(drag.el);
      }
    });

    async function saveData() {
      saveBtn.disabled = true;
      try {
        const res = await fetch("/api/save", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(collectPayload()),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "保存失败");
        setMsg(`保存成功：${data.path}（已备份：${data.backup}）`);
      } catch (e) {
        setMsg(`保存失败：${e.message}`, true);
      } finally {
        saveBtn.disabled = false;
      }
    }

    saveBtn.addEventListener("click", saveData);
    reloadBtn.addEventListener("click", loadData);
    loadData().catch((e) => setMsg(`加载失败：${e.message}`, true));
  </script>
</body>
</html>
"""


def load_yaml(path: Path) -> dict:
    with path.open('r', encoding='utf-8') as f:
        return YAML_RW.load(f)


def dump_yaml(path: Path, data: dict) -> None:
    with path.open('w', encoding='utf-8', newline='\n') as f:
        YAML_RW.dump(data, f)


def icon_path_for_item(item: dict, assets_dir: Path) -> Optional[Path]:
    name = str(item.get('name', '')).strip()
    if not name:
        return None
    icon = assets_dir / f'{name}_ICON.png'
    if icon.exists():
        return icon
    tpl = assets_dir / f'{name}_TEMPLATE.png'
    if tpl.exists():
        return tpl
    return None


def _placeholder_icon_data_uri(label: str) -> str:
    short = (label or 'NO').strip().upper()[:6]
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256">'
        '<rect width="256" height="256" rx="24" fill="#1f2937"/>'
        f'<text x="50%" y="52%" fill="#9ca3af" text-anchor="middle" font-size="28" '
        f'font-family="Segoe UI, Arial, sans-serif">{short}</text>'
        '</svg>'
    )
    return 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode('utf-8')).decode('ascii')


def _png_data_uri(path: Path) -> str:
    raw = path.read_bytes()
    return 'data:image/png;base64,' + base64.b64encode(raw).decode('ascii')


def build_view_model(cfg: dict, assets_dir: Path) -> List[dict]:
    groups_out: List[dict] = []
    for g in cfg.get('groups', []):
        items_out = []
        for it in g.get('items', []):
            item_id = str(it.get('id', '')).strip()
            if not item_id:
                continue
            p = icon_path_for_item(it, assets_dir)
            if p and p.is_file():
                icon_url = _png_data_uri(p)
            else:
                icon_url = _placeholder_icon_data_uri(item_id)
            items_out.append(
                {
                    'id': item_id,
                    'display_name': str(it.get('display_name') or it.get('name') or item_id),
                    'scan_method': str(it.get('scan_method', 'direct')),
                    'scan_page': str(it.get('scan_page', 'default')),
                    'icon_url': icon_url,
                }
            )
        groups_out.append({'id': str(g.get('id', '')), 'name': str(g.get('name', '')), 'items': items_out})
    return groups_out


def apply_order(cfg: dict, payload_groups: List[dict]) -> dict:
    groups_old = cfg.get('groups', [])
    group_map = {str(g.get('id', '')): g for g in groups_old}
    item_map = {}
    old_group_items = {}
    for g in groups_old:
        gid = str(g.get('id', ''))
        old_group_items[gid] = []
        for it in g.get('items', []):
            iid = str(it.get('id', ''))
            if not iid:
                continue
            item_map[iid] = it
            old_group_items[gid].append(iid)

    ordered_groups: List[dict] = []
    ordered_group_ids: List[str] = []
    used_items = set()
    assigned_by_group = {}

    for pg in payload_groups:
        gid = str(pg.get('id', ''))
        if gid not in group_map:
            continue
        ordered_group_ids.append(gid)
        assigned = []
        for raw_iid in pg.get('items', []):
            iid = str(raw_iid)
            if iid in item_map and iid not in used_items:
                assigned.append(iid)
                used_items.add(iid)
        assigned_by_group[gid] = assigned

    for g in groups_old:
        gid = str(g.get('id', ''))
        if gid not in assigned_by_group:
            ordered_group_ids.append(gid)
            assigned_by_group[gid] = []

    for gid in ordered_group_ids:
        for iid in old_group_items.get(gid, []):
            if iid not in used_items:
                assigned_by_group[gid].append(iid)
                used_items.add(iid)

    for gid in ordered_group_ids:
        g = group_map[gid]
        g['items'] = [item_map[iid] for iid in assigned_by_group.get(gid, []) if iid in item_map]
        ordered_groups.append(g)

    cfg['groups'] = ordered_groups
    return cfg


class SorterServer:
    def __init__(self, config_path: Path, assets_dir: Path):
        self.config_path = config_path
        self.assets_dir = assets_dir

    def handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _send_json(self, obj: dict, status=HTTPStatus.OK):
                body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_text(self, text: str, status=HTTPStatus.OK):
                body = text.encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == '/':
                    return self._send_text(HTML)
                if parsed.path == '/api/data':
                    cfg = load_yaml(outer.config_path)
                    groups = build_view_model(cfg, outer.assets_dir)
                    return self._send_json({'groups': groups})
                self.send_error(HTTPStatus.NOT_FOUND, 'not found')

            def do_POST(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != '/api/save':
                    self.send_error(HTTPStatus.NOT_FOUND, 'not found')
                    return
                length = int(self.headers.get('Content-Length', '0'))
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode('utf-8'))
                    groups = payload.get('groups', [])
                    cfg = load_yaml(outer.config_path)
                    new_cfg = apply_order(cfg, groups)

                    ts = time.strftime('%Y%m%d_%H%M%S')
                    backup = outer.config_path.with_suffix(outer.config_path.suffix + f'.bak.{ts}')
                    shutil.copy2(outer.config_path, backup)
                    dump_yaml(outer.config_path, new_cfg)
                    self._send_json(
                        {
                            'ok': True,
                            'path': str(outer.config_path),
                            'backup': str(backup),
                        }
                    )
                except Exception as e:
                    self._send_json({'ok': False, 'error': str(e)}, status=HTTPStatus.BAD_REQUEST)

            def log_message(self, fmt, *args):
                return

        return Handler


def main():
    parser = argparse.ArgumentParser(description='Temporary drag-sort tool for config/warehouse_items.yaml')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    parser.add_argument('--assets', default=str(DEFAULT_ASSETS))
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    assets_dir = Path(args.assets).resolve()
    if not config_path.exists():
        raise FileNotFoundError(f'Config not found: {config_path}')
    if not assets_dir.exists():
        raise FileNotFoundError(f'Assets dir not found: {assets_dir}')

    app = SorterServer(config_path=config_path, assets_dir=assets_dir)
    server = ThreadingHTTPServer((args.host, args.port), app.handler())
    url = f'http://{args.host}:{args.port}/'
    print(f'[warehouse-items-sorter] Serving on {url}')
    print(f'[warehouse-items-sorter] Config: {config_path}')
    print(f'[warehouse-items-sorter] Assets: {assets_dir}')
    print('[warehouse-items-sorter] Press Ctrl+C to stop')

    if not args.no_open:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print('[warehouse-items-sorter] Stopped')


if __name__ == '__main__':
    main()
