"""amp head knob maths: 0-10 knob positions -> what the box gets"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import amp_panel as A


def test_ranges():
    assert A.amp_db('amp_gain', 0) == 0 and abs(A.amp_db('amp_gain', 10) - 46) < 1e-9
    for k in ('amp_bass', 'amp_mid', 'amp_treble'):
        assert A.amp_db(k, 5) == 0 and abs(A.amp_db(k, 0) + 12) < 1e-9 and abs(A.amp_db(k, 10) - 12) < 1e-9
    assert abs(A.amp_db('amp_master', 10) - 6) < 1e-9 and A.amp_db('amp_master', 0) == -30
    assert A.amp_db('amp_gain', 99) == A.amp_db('amp_gain', 10)


def test_voice_defaults_round_trip():
    for v in A.VOICES:
        d = A.voice_defaults(v)
        assert abs(A.amp_db('amp_gain', d['amp_gain']) - A.VOICE_GAIN[v]) < 0.5
        assert abs(A.amp_db('amp_pres', d['amp_pres']) - A.VOICE_PRES[v]) < 0.15
        assert abs(A.amp_db('amp_master', d['amp_master']) - A.VOICE_MASTER[v]) < 0.4


def test_tone_override_leaves_input_alone():
    c = dict(low_f=80.0, low_g=3.0, amp_bass=8.0, amp_mid=5.0, amp_treble=2.0)
    t = A.tone_override(c)
    assert t['low_f'] == 150.0 and abs(t['low_g'] - 7.2) < 1e-9
    assert t['peak_f'] == 650.0 and t['peak_g'] == 0 and t['peak_q'] == 0.8
    assert t['high_f'] == 2800.0 and abs(t['high_g'] + 7.2) < 1e-9
    assert c['low_f'] == 80.0 and c['low_g'] == 3.0


if __name__ == '__main__':
    for n, f in list(globals().items()):
        if n.startswith('test_'):
            f()
            print('PASS', n)
