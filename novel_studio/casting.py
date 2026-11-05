"""Persistent voice library and deterministic, identity-preserving character casting."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4

from .project import add_character, bind_reference_audio
from .audio import read_wav

GENDERS = ['未指定', '男声', '女声', '其他']
AGES = ['未指定', '儿童', '少年', '青年', '中年', '老年']
TIMBRES = ['未指定', '低沉', '浑厚', '清亮', '温柔', '沙哑', '活泼', '沉稳']
TRAIT_FIELDS = ('gender', 'age', 'timbre', 'personality', 'description', 'aliases')


def load_library(root):
    path = Path(root) / 'catalog.json'
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, list):
        raise ValueError('音色库目录格式错误，请检查 catalog.json。')
    return payload


def save_library(root, voices):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / 'catalog.json'
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(voices, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(target)


def import_voice(root, source, traits):
    root, source = Path(root), Path(source)
    rate, samples = read_wav(source)
    if not rate or len(samples) < rate * 2:
        raise ValueError('请使用至少 2 秒的清晰单人 WAV 录音。')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    voices = load_library(root)
    if any(v['sha256'] == digest for v in voices):
        raise ValueError('这段音色已在音色库中，无需重复导入。')
    voice_id = str(uuid4())
    root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, root / (voice_id + '.wav'))
    voice = {**traits, 'id': voice_id, 'file': voice_id + '.wav', 'sha256': digest}
    voices.append(voice)
    save_library(root, voices)
    return voice


def merge_characters(project, portraits):
    """AI can fill missing traits, but never overwrite a binding or manual correction."""
    for portrait in portraits:
        name = portrait['name']
        character = next((c for c in project['characters']
                          if c['name'] == name or name in c.get('aliases', [])), None)
        character = character or add_character(project, name)
        if character.get('traits_locked'):
            continue
        for field in TRAIT_FIELDS:
            value = portrait.get(field)
            if not character.get(field) or character[field] == '未指定':
                if value:
                    character[field] = value


def bind_library_voice(folder, character, voice, library_root):
    source = Path(library_root) / voice['file']
    if hashlib.sha256(source.read_bytes()).hexdigest() != voice['sha256']:
        raise ValueError('音色库录音已被修改，请重新导入后再使用。')
    bind_reference_audio(folder, character, source, 'library')
    character['voice_profile'].update({
        'library_voice_id': voice['id'], 'voice_name': voice['name'],
        'locked': True, 'voice_traits': {k: voice.get(k) for k in TRAIT_FIELDS},
        'engine': 'breeze-tts-2', 'reference_transcript': voice.get('reference_transcript', ''),
        'seed': voice.get('seed', int(hashlib.sha256(voice['id'].encode()).hexdigest()[:8], 16)),
        'design_prompt': voice.get('design_prompt', ''), 'sample_approved': False,
    })


def invalidate_character_audio(project, character):
    """A deliberate recast invalidates only this person's lines across chapters."""
    for chapter in project.get('chapters', []):
        for segment in chapter.get('segments', []):
            narrator = segment.get('kind') == 'narration' or segment.get('speaker') == '__narrator__'
            if ((character['id'] == 'narrator' and narrator) or
                    (character['id'] != 'narrator' and segment.get('speaker') == character.get('name'))):
                segment.update(audio_path=None, render_signature=None, review_state='not_rendered', quality=None)
                chapter.update(audio_path=None, script_approval=None)


def resolve_speakers(project):
    """Resolve known aliases only; ambiguous/unidentified speakers stay reviewable."""
    names = {c['name'] for c in project['characters']}
    aliases = {}
    for character in project['characters']:
        for alias in character.get('aliases', []):
            aliases.setdefault(alias, set()).add(character['name'])
    for chapter in project.get('chapters', []):
        for segment in chapter.get('segments', []):
            speaker = segment.get('speaker')
            if segment.get('kind') == 'narration':
                segment['speaker'] = '__narrator__'
            elif speaker not in names and len(aliases.get(speaker, set())) == 1:
                segment['speaker'] = next(iter(aliases[speaker]))
                segment.update(audio_path=None, render_signature=None, review_state='not_rendered')
                chapter.update(audio_path=None, script_approval=None)


def demote_uncertain_speakers(project, threshold=0.70):
    """Keep uncertain dialogue safe by routing it to the stable narrator voice."""
    changed = []
    names = {c['name'] for c in project.get('characters', [])}
    for chapter in project.get('chapters', []):
        for index, segment in enumerate(chapter.get('segments', [])):
            if segment.get('kind') != 'dialogue':
                segment['speaker'] = '__narrator__'
                segment['speaker_resolution'] = 'narration'
                continue
            speaker = segment.get('speaker')
            confidence = float(segment.get('speaker_confidence', 0.0) or 0.0)
            if speaker not in names or confidence < threshold:
                if speaker != '__narrator__' or not segment.get('auto_demoted_to_narration'):
                    segment.update(speaker='__narrator__', kind='narration', speaker_resolution='narration',
                                  auto_demoted_to_narration=True, audio_path=None, render_signature=None,
                                  review_state='not_rendered', quality=None)
                    chapter.update(audio_path=None, script_approval=None)
                    changed.append((chapter.get('id'), index, speaker))
    return changed


def auto_cast(project, folder, library_root):
    resolve_speakers(project)
    voices = load_library(library_root)
    used = {c['voice_profile'].get('reference_sha256') for c in project['characters']}
    used.add(project.get('narrator_voice_profile', {}).get('reference_sha256'))
    assigned, pending = [], []
    # Allocate an independent narrator voice only from assets explicitly marked for narration.
    narrator = {'id': 'narrator', 'name': '旁白',
                'voice_profile': project.setdefault('narrator_voice_profile', {})}
    for character in [narrator] + project['characters']:
        profile = character['voice_profile']
        if profile.get('reference_audio'):
            continue  # Stable across chapters, edits, restarts and library changes.
        if not character['id'] == 'narrator' and character.get('tier') == 'A':
            pending.append(f"{character['name']}：A级人物需要独立设计或手动选声")
            continue
        is_narrator = character['id'] == 'narrator'
        candidates = [v for v in voices if v.get('role', '角色') == ('旁白' if is_narrator else '角色')
                      and v['sha256'] not in used and (Path(library_root) / v['file']).is_file()]
        if not is_narrator:
            if character.get('gender', '未指定') not in ('男声', '女声', '其他'):
                pending.append(f"{character['name']}：声线性别未确认")
                continue
            candidates = [v for v in candidates if v.get('gender') == character['gender']]
            # Child and adult voice ranges must not be substituted silently.
            candidates = [v for v in candidates if (character.get('age') != '儿童' or v.get('age') == '儿童')
                          and (character.get('age') in ('儿童', '未指定', None) or v.get('age') != '儿童')]
        if not candidates:
            pending.append(f"{character['name']}：缺少可用的独立音色")
            continue
        def score(voice):
            return sum(weight for field, weight in [('age', 5), ('timbre', 4), ('personality', 2)]
                       if character.get(field) not in (None, '', '未指定') and character.get(field) == voice.get(field))
        voice = sorted(candidates, key=lambda v: (-score(v), v['id']))[0]
        bind_library_voice(folder, character, voice, library_root)
        profile.setdefault('engine', 'breeze-tts-2')
        profile.setdefault('settings', {'speed': 1.0, 'emotion_strength': 0.35, 'pause_scale': 1.0})
        used.add(voice['sha256'])
        assigned.append(f"{character['name']} → {voice['name']}")
    return assigned, pending
