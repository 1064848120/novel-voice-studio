"""IndexTTS 2.5 expression controls; speaker identity never changes here."""
import math


def expression_parameters(emotion='neutral', strength=.35, pace=1., settings=None):
    settings = settings or {}
    strength, pace = float(strength), float(pace)
    speed = float(settings.get('speed', 1.))
    pause_scale = float(settings.get('pause_scale', 1.))
    if not all(math.isfinite(v) for v in (strength, pace, speed, pause_scale)):
        raise ValueError('情绪与语速参数必须为有限数值。')
    if not 0 <= strength <= .8 or not .85 <= pace <= 1.15 or not .5 <= speed <= 2 or not .5 <= pause_scale <= 2:
        raise ValueError('情绪强度或语速超出支持范围。')
    # Official order: happy, angry, sad, afraid, disgusted, melancholic, surprised, calm.
    positions = {'happy': 0, 'angry': 1, 'sad': 2, 'afraid': 3, 'surprised': 6, 'calm': 7}
    if emotion not in {'neutral', *positions}:
        raise ValueError('不支持的情绪类型。')
    vector = [0.] * 8
    if emotion != 'neutral':
        vector[positions[emotion]] = strength
    return {'emo_vector': vector, 'emo_alpha': 1., 'use_emo_text': False,
            'use_random': False, 'duration_factor': 1. / (speed * pace),
            'interval_silence': round(200 * pause_scale)}
