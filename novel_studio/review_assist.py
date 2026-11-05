"""Targeted AI completion; keep known speakers, voices and edited text intact."""
from .casting import merge_characters, resolve_speakers, auto_cast
from .project import save_project
from .workflow import chapter_issues, update_statistics
from .voice_batch import design_batch
from .api_analysis import AnalysisCancelled
from pathlib import Path


def complete_chapter(project, folder, index, analyzer, config, app_dir, status, cancel):
    chapter = project['chapters'][index]
    names = {c['name'] for c in project['characters']}
    unresolved = [i for i,s in enumerate(chapter['segments']) if s.get('kind') == 'dialogue'
                  and s.get('speaker') not in names and s.get('speaker') != '__narrator__']
    resolved = 0
    if unresolved:
        if analyzer is None:
            raise ValueError('请先配置 NewAPI，再补全说话人。')
        status(f'正在结合上下文补全 {len(unresolved)} 段说话人，保留已确认台词…')
        try:
            analyzed, portraits = analyzer.analyze_chapter(chapter['text'], project['characters'], status,
                chapter_id=chapter['id'], context='\n'.join(c.get('summary', c['text'][-1000:])
                    for c in project['chapters'][max(0,index-2):index]), segments_override=chapter['segments'], cancel=cancel)
        except AnalysisCancelled:
            save_project(folder, project)
            return {'project': project, 'message': '已停止当前 NewAPI 请求，项目已有内容已保留。', 'error': None}
        merge_characters(project, portraits)
        for i in unresolved:
            target, suggestion = chapter['segments'][i], analyzed[i]
            target['speaker_reason'] = suggestion.speaker_reason
            if suggestion.kind == 'narration' or suggestion.speaker:
                target.update(kind=suggestion.kind, speaker=suggestion.speaker, audio_path=None,
                              render_signature=None, review_state='not_rendered', quality=None)
                chapter.update(audio_path=None, script_approval=None)
                resolved += 1
        resolve_speakers(project)
        update_statistics(project)
        save_project(folder, project)
    assigned, _ = auto_cast(project, folder, Path(app_dir) / 'voice-library')
    save_project(folder, project)
    needed = {s.get('speaker') for s in chapter['segments'] if s.get('kind') == 'dialogue'}
    jobs = [(c['id'], {**c, 'role': '角色'}) for c in project['characters']
            if c['name'] in needed and not c['voice_profile'].get('reference_audio')]
    if any(s.get('kind') == 'narration' for s in chapter['segments']) and not project.get('narrator_voice_profile', {}).get('reference_audio'):
        jobs.insert(0, ('narrator', {'name': '故事旁白', 'role': '旁白', 'age': '中年', 'timbre': '沉稳'}))
    error = None
    designed = 0
    if jobs and not cancel.is_set():
        outcome = design_batch(project, folder, app_dir, jobs, config, status, cancel)
        error, designed = outcome['error'], outcome['completed']
    remaining = chapter_issues(project, chapter)
    message = f'已补全 {resolved} 段说话人，匹配 {len(assigned)} 个声音，新设计 {designed} 个声音。'
    message += f' 剩余 {len(remaining)} 处待检查，可点击定位；声卡试听确认由你完成。'
    if error:
        message += ' 声音设计未完成：' + error
    return {'project': project, 'message': message, 'error': error}
