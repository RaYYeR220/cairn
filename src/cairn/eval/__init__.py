"""A graded evaluation of the integrity gate against a labelled corpus of injections.

The corpus is held out from the detector code: it is a set of realistic log-borne attacks across
twelve families, plus benign lines chosen because they look suspicious but are legitimate. The
runner scores the gate and reports the misses, not just the hits.
"""
