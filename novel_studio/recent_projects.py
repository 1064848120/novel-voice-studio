"""Recoverable deletion limited to immediate project folders."""
from pathlib import Path
from uuid import uuid4


def remove_project(folder, root):
    folder, root = Path(folder).resolve(), Path(root).resolve()
    if folder.parent != root or not (folder / 'project.json').is_file():
        raise ValueError('只能删除小说讲书目录内的项目文件夹。')
    deleted = root / '.deleted'
    deleted.mkdir(exist_ok=True)
    if deleted.resolve().parent != root:
        raise ValueError('项目回收目录无效。')
    target = deleted / (folder.name + '-' + uuid4().hex[:8])
    folder.rename(target)
    return target
