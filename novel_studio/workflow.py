"""Versioned batch preparation, review gates and stable casting statistics."""
from __future__ import annotations
import hashlib
import json
from collections import Counter
from pathlib import Path
from uuid import uuid4

from .casting import merge_characters
from .api_analysis import AnalysisCancelled
from .project import save_project


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def ensure_workflow(project):
    flow = project.setdefault('workflow', {})
    for k, value in {'sample_size': 30, 'batch_size': 10, 'pilot_count': 2,
                     'batches': [], 'pilot_approval': None, 'protagonist_id': None}.items():
        flow.setdefault(k, value)
    return flow


def profile_for(project, segment):
    if segment.get('kind') == 'narration' or segment.get('speaker') == '__narrator__':
        return project.get('narrator_voice_profile', {})
    person = next((c for c in project['characters'] if c['name'] == segment.get('speaker')), None)
    return person.get('voice_profile', {}) if person else {}


def chapter_signature(project, chapter):
    return digest([{
        'text': s['text'], 'kind': s.get('kind'), 'speaker': s.get('speaker'),
        'emotion': s.get('emotion', 'neutral'), 'strength': s.get('emotion_strength', .35),
        'pace': s.get('pace', 1.), 'pause': s.get('pause_after', .25),
        'voice': profile_for(project, s),
    } for s in chapter.get('segments', [])])


def chapter_issues(project, chapter):
    issues = []
    if not chapter.get('segments'):
        return [{'row': None, 'code': 'empty', 'message': '章节没有剧本片段。', 'speaker': ''}]
    names = {c['name'] for c in project['characters']}
    for row, segment in enumerate(chapter['segments']):
        profile = profile_for(project, segment)
        narrator = segment.get('kind') == 'narration' or segment.get('speaker') == '__narrator__'
        speaker = '旁白' if narrator else segment.get('speaker') or '未确认'
        problem = None
        if not segment.get('text', '').strip():
            problem = ('text', '没有文字')
        elif len(segment['text']) > 180:
            problem = ('length', '片段过长，请整理分段')
        elif not narrator and segment.get('speaker') not in names:
            reason = (segment.get('speaker_reason') or '').strip()
            problem = ('speaker', '说话人未确认。' + (f'识别依据：{reason}。' if reason else
                                                        '模型无法从当前上下文唯一判断。可定位核对，或点击“AI 补全本章＋自动配声”重试。'))
        elif not profile.get('reference_audio'):
            problem = ('voice', f'{speaker} 没有固定声音，可自动匹配或设计')
        elif not profile.get('reference_transcript', '').strip():
            problem = ('transcript', f'{speaker} 的声卡缺少准确文字稿')
        elif not profile.get('sample_approved'):
            problem = ('sample', f'{speaker} 的固定声音尚未试听确认')
        if problem:
            issues.append({'row': row, 'code': problem[0], 'message': f'第 {row+1} 段：{problem[1]}', 'speaker': speaker})
    return issues


def review_chapter(project, chapter):
    issues = chapter_issues(project, chapter)
    if issues:
        raise ValueError(issues[0]['message'])
    chapter['script_approval'] = chapter_signature(project, chapter)


def is_reviewed(project, chapter):
    return bool(chapter.get('script_approval')) and chapter['script_approval'] == chapter_signature(project, chapter)


def pilot_signature(project):
    n = ensure_workflow(project)['pilot_count']
    return digest([{'script': chapter_signature(project, c),
                    'audio': [(s.get('audio_path'), s.get('audio_sha256'), s.get('review_state'))
                              for s in c.get('segments', [])]}
                   for c in project['chapters'][:n]])


def approve_pilot(project):
    for chapter in project['chapters'][:ensure_workflow(project)['pilot_count']]:
        if not is_reviewed(project, chapter):
            raise ValueError('请先确认试播章节的剧本。')
        if any(not s.get('audio_path') or s.get('review_state') not in ('passed', 'approved')
               for s in chapter['segments']):
            raise ValueError('请先生成试播章节，并逐段试听确认。')
    project['workflow']['pilot_approval'] = pilot_signature(project)


def pilot_ready(project):
    return ensure_workflow(project)['pilot_approval'] == pilot_signature(project)


def update_statistics(project):
    flow = ensure_workflow(project)
    stats = {c['id']: {'chapters': set(), 'dialogue_chars': 0, 'lines': 0, 'interactions': 0} for c in project['characters']}
    by_name = {c['name']: c['id'] for c in project['characters']}
    for ch in project['chapters']:
        sequence = []
        mentioned = set(ch.get('mentioned_characters', []))
        for s in ch.get('segments', []):
            cid = by_name.get(s.get('speaker')) if s.get('kind') == 'dialogue' else None
            if cid:
                stats[cid]['chapters'].add(ch['id'])
                stats[cid]['dialogue_chars'] += len(s['text'])
                stats[cid]['lines'] += 1
                sequence.append(cid)
        for name in mentioned:
            if name in by_name:
                stats[by_name[name]]['chapters'].add(ch['id'])
        lead = flow.get('protagonist_id')
        for left, right in zip(sequence, sequence[1:]):
            if left != right and lead in (left, right):
                stats[right if left == lead else left]['interactions'] += 1
    total = max(1, sum(s['dialogue_chars'] for s in stats.values()))
    for c in project['characters']:
        s = stats[c['id']]
        s['chapter_count'] = len(s.pop('chapters'))
        c['statistics'] = s
        proposed = ('A' if c['id'] == flow.get('protagonist_id') or s['dialogue_chars'] / total >= .15 and s['chapter_count'] >= 2
                    else 'B' if s['chapter_count'] >= 3 or s['lines'] >= 10 else 'C')
        c['suggested_tier'] = proposed
        if not c.get('tier_locked'):
            old = c.get('tier', 'C')
            c['tier'] = min(old, proposed)  # Promotion only. Never change voice_profile.


def next_batch(project):
    flow = ensure_workflow(project)
    pending = next((b for b in flow['batches'] if b['state'] == 'analyzing'), None)
    if pending:
        return pending
    start = max((b['end'] for b in flow['batches']), default=0)
    if start >= len(project['chapters']):
        raise ValueError('所有章节已完成批次分析。')
    if start and not pilot_ready(project):
        raise ValueError('继续增量分析前，请完成前 1～2 章试播并确认。')
    end = min(len(project['chapters']), start + (flow['sample_size'] if start == 0 else flow['batch_size']))
    batch = {'id': str(uuid4()), 'start': start, 'end': end, 'state': 'analyzing', 'completed': []}
    flow['batches'].append(batch)
    return batch


def analyze_batch(project, folder, analyzer, status, cancel):
    batch = next_batch(project)
    save_project(folder, project)
    for index in range(batch['start'], batch['end']):
        if cancel.is_set():
            return project, '已停止，完成的章节已保存，下次继续本批次。'
        if index in batch['completed']:
            continue
        chapter = project['chapters'][index]
        chapter_label = f"第 {index+1} 章：{chapter['title']}"
        total = batch['end'] - batch['start']
        status({'context': chapter_label, 'detail': '正在提交本章文本，请等待接口返回…',
                'done': len(batch['completed']), 'total': total, 'unit': '章'})
        def chapter_progress(event):
            data = {'detail': event if isinstance(event, str) else event.get('detail', ''),
                    'context': chapter_label, 'done': len(batch['completed']), 'total': total, 'unit': '章'}
            if isinstance(event, dict) and event.get('total'):
                data['subprogress'] = {'done': event.get('done', 0), 'total': event['total'],
                                       'unit': event.get('unit', '组'), 'phase': event.get('phase', 'running')}
            status(data)
        context = '\n'.join(c.get('summary', c['text'][-1200:]) for c in project['chapters'][max(0,index-2):index])[-2400:]
        try:
            segments, portraits = analyzer.analyze_chapter(chapter['text'], project['characters'], chapter_progress,
                                                            chapter_id=chapter['id'], context=context, cancel=cancel)
        except AnalysisCancelled:
            save_project(folder, project)
            status({'context': chapter_label, 'detail': '已停止当前 NewAPI 请求；此前完成的章节已保存。',
                    'done': len(batch['completed']), 'total': total, 'unit': '章'})
            return project, '已停止分析。已完成章节已保存，当前未完成章节下次可继续。'
        merge_characters(project, portraits)
        chapter.update(segments=[vars(s) for s in segments], script_approval=None, audio_path=None,
                       analysis_state='analyzed', summary=analyzer.last_summary,
                       mentioned_characters=analyzer.last_mentions)
        batch['completed'].append(index)
        update_statistics(project)
        save_project(folder, project)
        scripts = Path(folder) / 'scripts'
        scripts.mkdir(exist_ok=True)
        (scripts / f"{chapter['id']}.json").write_text(json.dumps(chapter, ensure_ascii=False, indent=2), encoding='utf-8')
        status({'context': chapter_label, 'detail': '本章分析完成，剧本与人物库已保存。',
                'done': len(batch['completed']), 'total': total, 'unit': '章'})
    batch['state'] = 'review'
    save_project(folder, project)
    return project, f"第 {batch['start']+1}～{batch['end']} 章分析完成，请校对人物与剧本。"
