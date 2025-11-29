import json
import re
import time
from pathlib import Path

import requests

session = requests.Session()
dot_pat = re.compile(r"[·.]{2,4}")


def reformat_text(s: str) -> str:
    return dot_pat.sub("…", s.replace("{AccountData.NickName}", "")).strip("'")


def get_from_gamekee_wiki(skip_names: set[str]) -> dict[str, list[dict]]:
    # gamekee首页误写
    ret = dict()
    game_header = {"game-alias": "nikke"}
    entry_url = "https://nikke.gamekee.com/v1/wiki/entry"
    entry_json = session.get(entry_url, headers=game_header).json()
    characters = None
    entry_id = None
    for d in entry_json["data"]["entry_list"]:
        if d.get("name", None) == "妮姬图鉴":
            for l in d["child"]:
                if l.get("name", None) == "角色图鉴":
                    characters = l["child"]
                    entry_id = l["id"]
                    break
            break
    if characters == None:
        print("获取gamekee角色图鉴失败")
        return ret
    entry_filter = session.get(
        "https://nikke.gamekee.com/v1/entryFilter/getEntryFilter",
        headers=game_header,
        params={"entry_id": entry_id},
    ).json()
    invalid_pair = set()
    for f in entry_filter["data"]["entry_filter"]:
        if f["name"] == "企业":
            for c in f["children"]:
                if c["name"] == "反常":
                    invalid_pair.add((f["id"], c["id"]))
        elif f["name"] == "稀有度":
            for c in f["children"]:
                if c["name"] == "R":
                    invalid_pair.add((f["id"], c["id"]))

    def is_valid(nikke_entry):
        for attr in entry_filter["data"]["entry_filter_attr"].get(
            str(nikke_entry["id"]), []
        ):
            if len(attr["value"]) != 1:
                print(nikke_entry)
                print(
                    entry_filter["data"]["entry_filter_attr"].get(
                        str(nikke_entry["id"])
                    )
                )
                raise Exception("gamekee wiki parsing failed")
            if attr["value"][0] == "":
                continue
            if (attr["input_id"], int(attr["value"][0])) in invalid_pair:
                return False
        return True

    def get_single(base_data):
        index = next(
            i for i, d in enumerate(base_data) if d[0]["value"].startswith("问题")
        )
        # 从index开始，每3个为一组进行解析
        while base_data[index][0]["value"].startswith("问题"):
            group = dict(question="", answer=dict(false="", true=""))
            group["question"] = reformat_text(base_data[index][1]["value"])
            for j in (index + 1, index + 2):
                if base_data[j][0]["value"].startswith("100"):
                    group["answer"]["false"] = reformat_text(base_data[j][1]["value"])
                elif base_data[j][0]["value"].startswith("120"):
                    group["answer"]["true"] = reformat_text(base_data[j][1]["value"])
                else:
                    raise Exception("gamekee wiki parsing error")
            index += 3
            if not (
                len(group["question"])
                or len(group["answer"]["false"])
                or len(group["answer"]["true"])
            ):
                continue
            yield group

    for nikke in characters:
        if nikke["name"] in skip_names:
            continue
        # 编辑中词条
        if nikke["content_id"] == 0:
            continue
        if not is_valid(nikke):
            ret[nikke["name"]] = []
            continue
        data_json = session.get(
            f"https://nikke.gamekee.com/v1/content/detail/{nikke['content_id']}",
            headers=game_header,
        ).json()
        content_json = json.loads(data_json["data"]["content_json"])
        base_data = content_json.get("baseData", [])
        name = next((x for x in base_data if x[0]["value"] == "角色名称"), None)
        name = name[1]["value"] if name else nikke["name"]
        ret[name] = list(get_single(base_data))
        time.sleep(0.5)
    print("Gamekee Wiki:")
    keys = list(ret.keys())
    for i in range(0, len(keys), 5):
        print(", ".join(keys[i : i + 5]))
    return ret


if __name__ == "__main__":
    zh_cn_data: dict = dict()
    dialogue_json_path = (
        Path(__file__).parent.parent / "module" / "conversation" / "dialogue.zh-CN.json"
    )
    if not dialogue_json_path.exists():
        zh_cn_data = dict()
    else:
        zh_cn_data = json.loads(dialogue_json_path.read_text(encoding="utf-8"))

    zh_cn_data_extra = get_from_gamekee_wiki(set(zh_cn_data.keys()))
    # 添加到开头部分
    sorted_tuple = tuple(zh_cn_data_extra.items()) + tuple(zh_cn_data.items())
    zh_cn_data = dict(sorted_tuple)

    with dialogue_json_path.open("w", encoding="utf-8") as f:
        json.dump(zh_cn_data, f, ensure_ascii=False, indent=2)
