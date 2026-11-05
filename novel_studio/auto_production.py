"""One-click production pipeline for non-technical users."""
from __future__ import annotations

import json
from pathlib import Path

from .api_analysis import AnalysisCancelled
from .casting import auto_cast, demote_uncertain_speakers, merge_characters, resolve_speakers
from .mastering import export_chapter_audio
from .project import save_project
from .voice_batch import design_batch
from .workflow import chapter_signature, ensure_workflow


def _set_state(chapter, state, error=None):
    chapter['production_state'] = state
    if error:
        chapter.setdefault('production_issues', []).append(error)


def _voice_jobs(project):
    jobs = []
    for character in project.get('characters', []):
        if not character.get('voice_profile', {}).get('reference_audio'):
            jobs.append((character['id'], {**{k: character.get(k, '') for k in
                         ('name', 'gender', 'age', 'timbre', 'personality', 'description')}, 'role': '角色'}))
    if not project.get('narrator_voice_profile', {}).get('reference_audio'):
        jobs.insert(0, ('narrator', {'name': '故事旁白', 'role': '旁白', 'gender': '未指定',
                                     'age': '中年', 'timbre': '沉稳', 'personality': '清晰自然，适合长篇讲述'}))
    return jobs


def _auto_approve_voice_profiles(project):
    """Generated/library voices are machine checked; no manual card approval is needed."""
    for profile in [project.get('narrator_voice_profile', {})] + [c['voice_profile'] for c in project.get('characters', [])]:
        if profile.get('reference_audio'):
            profile['sample_approved'] = True
            profile.setdefault('reference_transcript', '你好，很高兴在这里与你相遇。窗外的风渐渐停了。')


def run_auto_production(project, folder, start_chapter, end_chapter, analyzer, render_config,
                        app_dir, status, cancel):
    """Analyze, cast, render, mix and export a range with resumable chapter checkpoints."""
    folder = Path(folder)
    end_chapter = min(end_chapter, len(project.get('chapters', [])))
    selected = list(range(max(0, start_chapter), end_chapter))
    flow = ensure_workflow(project)
    result = {'completed': [], 'skipped': [], 'issues': [], 'outputs': []}
    if not selected:
        raise ValueError('没有可制作的章节范围。')

    # First pass: all selected chapters contribute to the global character library.
    for ci in selected:
        chapter = project['chapters'][ci]
        if cancel.is_set():
            return result | {'message': '已停止自动制作，已完成章节保留。'}
        _set_state(chapter, '分析中')
        status({'context': f'第 {ci + 1} 章：{chapter["title"]}', 'detail': 'AI 正在分析人物、旁白、对白和情绪…',
                'done': len(result['completed']), 'total': len(selected), 'unit': '章'})
        try:
            signature = chapter_signature(project, chapter) if chapter.get('segments') else None
            if chapter.get('production_state') == '已完成' and chapter.get('production_signature') == signature:
                result['skipped'].append(ci)
                continue
            segments, portraits = analyzer.analyze_chapter(
                chapter['text'], project.get('characters', []),
                lambda event: status({'context': f'第 {ci + 1} 章：{chapter["title"]}',
                                      'detail': event if isinstance(event, str) else event.get('detail', ''),
                                      'done': len(result['completed']), 'total': len(selected), 'unit': '章'}),
                chapter_id=chapter['id'], cancel=cancel)
            merge_characters(project, portraits)
            chapter['segments'] = [vars(segment) for segment in segments]
            chapter['analysis_state'] = 'analyzed'
            chapter['summary'] = getattr(analyzer, 'last_summary', '')
            chapter['mentioned_characters'] = getattr(analyzer, 'last_mentions', [])
            chapter['analysis_signature'] = chapter_signature(project, chapter)
            _set_state(chapter, '待生成')
            demote_uncertain_speakers(project)
            resolve_speakers(project)
            save_project(folder, project)
        except AnalysisCancelled:
            _set_state(chapter, '待分析')
            save_project(folder, project)
            return result | {'message': '已停止自动制作，已完成章节保留。'}
        except Exception as exc:
            _set_state(chapter, '有异常', str(exc))
            result['issues'].append({'chapter': ci, 'message': f'第 {ci + 1} 章分析失败：{exc}'})
            save_project(folder, project)

    # Design/bind every missing voice once for the selected range.
    auto_cast(project, folder, Path(app_dir) / 'voice-library')
    jobs = _voice_jobs(project)
    if jobs and not cancel.is_set():
        outcome = design_batch(project, folder, app_dir, jobs, render_config, status, cancel)
        if outcome.get('error'):
            result['issues'].append({'chapter': None, 'message': '声音设计失败：' + outcome['error']})
    _auto_approve_voice_profiles(project)
    save_project(folder, project)

    from .breeze_render import apply_results, render_queue
    for ci in selected:
        chapter = project['chapters'][ci]
        if cancel.is_set():
            break
        if chapter.get('production_state') == '有异常' and not chapter.get('segments'):
            continue
        _set_state(chapter, '生成中')
        snapshot = json.loads(json.dumps(project, ensure_ascii=False))
        try:
            render_config = dict(render_config)
            render_config['segment_indices'] = None
            payload = render_queue(snapshot, folder, [ci], render_config, status, cancel)
            apply_results(project, folder, payload)
            failed = [s for s in chapter.get('segments', []) if s.get('review_state') == 'failed' or not s.get('audio_path')]
            if failed:
                _set_state(chapter, '有异常', f'第 {ci + 1} 章有 {len(failed)} 段音频质检失败。')
                result['issues'].append({'chapter': ci, 'message': f'第 {ci + 1} 章有 {len(failed)} 段音频需要重做。'})
                save_project(folder, project)
                continue
            for segment in chapter.get('segments', []):
                if segment.get('review_state') == 'unverified':
                    segment['review_state'] = 'failed'
            chapter['script_approval'] = chapter_signature(project, chapter)
            _set_state(chapter, '质检中')
            exported = export_chapter_audio(project, folder, ci, status)
            chapter['audio_path'] = str(Path(exported['path']).relative_to(folder))
            chapter['master_sha256'] = __import__('novel_studio.breeze_render', fromlist=['file_hash']).file_hash(exported['path'])
            chapter['production_signature'] = chapter_signature(project, chapter)
            _set_state(chapter, '已完成')
            result['completed'].append(ci)
            result['outputs'].append(chapter['audio_path'])
            save_project(folder, project)
        except Exception as exc:
            _set_state(chapter, '有异常', str(exc))
            result['issues'].append({'chapter': ci, 'message': f'第 {ci + 1} 章未完成：{exc}'})
            save_project(folder, project)
    if not result['issues'] and len(result['completed']) + len(result['skipped']) == len(selected):
        try:
            from .mastering import export_m4b
            ordered = [(project['chapters'][ci]['title'], folder / project['chapters'][ci]['audio_path']) for ci in selected]
            book_path = folder / 'exports' / f"{project.get('title', '有声书')}-{start_chapter + 1}-{end_chapter}.m4b"
            export_m4b(ordered, book_path, project.get('title', '有声书'))
            result['m4b'] = str(book_path.relative_to(folder))
        except Exception as exc:
            result['issues'].append({'chapter': None, 'message': f'M4B 合成失败：{exc}'})
    result['message'] = f"自动制作完成：完成 {len(result['completed'])} 章，跳过 {len(result['skipped'])} 章，异常 {len(result['issues'])} 项。"
    return result
