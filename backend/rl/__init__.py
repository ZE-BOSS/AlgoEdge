"""The reinforcement-learning agent platform.

Built leak-guard first: see leakguard.py for why. Nothing in this package may
be trusted until `leakguard.self_check()` passes, because a probe that has
stopped detecting leaks is worse than no probe — it is believed.
"""
