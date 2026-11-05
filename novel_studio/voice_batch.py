"""Persist and bind each designed voice before progressing to the next person."""
from pathlib import Path
from .voice_design import design_voice
from .casting import import_voice, bind_library_voice, invalidate_character_audio, resolve_speakers
from .project import save_project


def design_batch(project, folder, app_dir, jobs, config, status, cancel):
    completed = 0
    total = len(jobs)
    errors = []
    def result(error=None):
        return {'project': project, 'completed': completed, 'total': total, 'error': error,
                'message': (f'已完成 {completed} / {total} 个声音，已保存并自动绑定对应人物。' +
                            (' 已停止，剩余人物可继续设计。' if cancel.is_set() else '') +
                            (f' 后续任务未完成：{error}' if error else ''))}
    for character_id, traits in jobs:
        if cancel.is_set():
            return result()
        def progress(detail):
            status({'context': f"当前人物：{traits['name']}", 'detail': detail,
                    'done': completed, 'total': total, 'unit': '个声音', 'label': '已完成'})
        progress('正在设计声音；完成后自动保存并绑定此人物的所有台词。')
        try:
            path, metadata = design_voice(config['breeze_python'], config['breeze_runtime'], config['breeze_models'],
                                          Path(app_dir) / 'voice-designs', traits, progress)
            voice = import_voice(Path(app_dir) / 'voice-library', path, {**traits, **metadata})
            if character_id is not None and project is not None:
                person = ({'id': 'narrator', 'voice_profile': project.setdefault('narrator_voice_profile', {})}
                          if character_id == 'narrator' else next(c for c in project['characters'] if c['id'] == character_id))
                bind_library_voice(folder, person, voice, Path(app_dir) / 'voice-library')
                invalidate_character_audio(project, person)
                resolve_speakers(project)
                save_project(folder, project)
            completed += 1
            progress('声音已保存并自动绑定对应人物。' if character_id else '声音已保存到音色库。')
        except Exception as exc:
                errors.append(f"{traits['name']}：{exc}")
                progress('本人物声音设计失败，已记录并继续其他人物。')
                continue
    return result('；'.join(errors) if errors else None)
