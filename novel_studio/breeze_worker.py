"""One GPU load per production queue, fixed references and checkpointed results."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys

from .breeze_render import file_hash, instruction, technical_check


def run(request_path):
    request = json.loads(Path(request_path).read_text(encoding='utf-8'))
    result_path = Path(request['result_file'])
    payload = {'rows': []}
    def progress(detail):
        print('PROGRESS:' + json.dumps({'detail': detail, 'done': len(payload['rows']),
              'total': len(request['segments']), 'unit': '段'}, ensure_ascii=False), flush=True)
    def save():
        tmp = result_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(result_path)
    try:
        import subprocess
        gpu = subprocess.run(['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits'], capture_output=True, text=True)
        if gpu.returncode == 0 and int(gpu.stdout.splitlines()[0]) < 8500:
            raise RuntimeError('Breeze 需要约 8.5GB 空闲显存，请先关闭其他语音模型服务。')
        sys.path.insert(0, request['runtime'])
        from breeze_infer.runtime import load_runtime, update_generation_config_for_breeze, set_all_seeds
        from breeze_infer.templates import get_template, prepare_inputs
        from models.fast_streaming import FastBreezeStreamingRuntime, FastStreamingConfig
        import soundfile as sf
        from .quality import compare_voice, compare_transcript
        print('PROGRESS:正在加载 Breeze，声音身份固定，使用 Voice Direction。', flush=True)
        tokenizer, model, codec = load_runtime(Path(request['model_dir']), device='cuda:0', attn_implementation='eager')
        update_generation_config_for_breeze(model)
        runtime = FastBreezeStreamingRuntime(model, codec, FastStreamingConfig(max_new_tokens=1500, max_seq_len=2048,
                                            fast_all=False, repetition_penalty=1.1), tokenizer=tokenizer)
        for index, item in enumerate(request['segments']):
            if Path(request['cancel_file']).exists():
                break
            progress(f"第 {item['chapter_index']+1} 章 · 第 {item['segment_index']+1} 段：正在生成对白…")
            reference = Path(item['reference_audio'])
            if file_hash(reference) != item['reference_sha256']:
                raise RuntimeError('固定声卡录音已被修改，停止生成。')
            target = Path(item['output_path'])
            target.parent.mkdir(parents=True, exist_ok=True)
            receipt = target.with_suffix('.result.json')
            if receipt.exists() and target.exists():
                cached = json.loads(receipt.read_text(encoding='utf-8'))
                if (cached.get('signature') == item['signature'] and cached.get('audio_sha256') == file_hash(target)
                        and cached.get('review_state') in ('passed', 'unverified')
                        and cached.get('quality', {}).get('technical', {}).get('state') == 'passed'):
                    cached.update(chapter_index=item['chapter_index'], segment_index=item['segment_index'])
                    payload['rows'].append(cached)
                    save()
                    progress('已复用并核验已有音频。')
                    continue
            row = {k: item[k] for k in ('chapter_index', 'segment_index', 'signature')}
            if item.get('render_take'):
                row['render_take'] = item['render_take']
            for attempt in (1, 2):
                try:
                    set_all_seeds(int(item['seed']))
                    source = {'text': item['text'], 'speaker': 'S0', 'ref_audio_path': str(reference),
                              'ref_text': item['reference_transcript'], 'instruction': instruction(item, retry=attempt==2)}
                    inputs = prepare_inputs(tokenizer, codec, model, [source], get_template('ref_edit_tata'),
                                            guidance_scale=4., guidance_scale_ref=None, guidance_scale_ins=None)
                    with sf.SoundFile(target, mode='w', samplerate=runtime.sample_rate, channels=1, subtype='PCM_16') as wav:
                        for chunk in runtime.iter_audio_chunks(inputs, request_id=f'{index}-{attempt}', seed=int(item['seed'])):
                            wav.write(chunk.audio)
                    technical = technical_check(target, item['text'])
                    if technical['state'] != 'passed':
                        raise ValueError(technical['message'])
                    progress('对白已生成，正在检查声纹和台词完整性…')
                    voice = compare_voice(reference, target)
                    transcript = compare_transcript(target, item['text'], request.get('asr_model'))
                    if attempt == 1 and ('failed' in (voice['state'], transcript['state'])):
                        progress('声纹或台词比对异常，保持声卡重试一次…')
                        continue
                    quality = {'technical': technical, 'voice': voice, 'transcript': transcript,
                               'score': voice.get('score'), 'message': voice['message'] + '；' + transcript['message']}
                    state = 'passed' if voice['state'] == transcript['state'] == 'passed' else 'failed' if 'failed' in (voice['state'], transcript['state']) else 'unverified'
                    row.update(audio_path=str(target), review_state=state, quality=quality, attempts=attempt,
                               audio_sha256=file_hash(target), instruction=source['instruction'])
                    break
                except Exception as exc:
                    row.update(audio_path=None, review_state='failed', attempts=attempt,
                               quality={'technical': {'state': 'failed', 'message': str(exc)}, 'message': str(exc)})
                    if attempt == 1:
                        print('PROGRESS:当前片段异常，保持声卡并降低表达强度重试一次。', flush=True)
            receipt.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
            payload['rows'].append(row)
            save()
            progress('本段已保存。' if row.get('review_state') == 'passed' else '本段已记录，需人工检查或重做。')
        save()
        return 0
    except Exception as exc:
        payload['error'] = str(exc)
        save()
        return 1


if __name__ == '__main__':
    raise SystemExit(run(sys.argv[1]))
