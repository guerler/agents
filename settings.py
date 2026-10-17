"""The knobs of this repo, in one place: run budgets, Galaxy waits, watched runs and their videos.

Each is read where it is used. An environment variable named beside a knob overrides it per run.
"""

# Runs
SCENARIO_TIMEOUT_MS = 150_000  # the agent's budget for a scenario that states no `timeoutMs`
GALAXY_WAIT_SECONDS = 600  # any one wait on Galaxy, staging or jobs; OLIT_EVAL_GALAXY_WAIT
GALAXY_POLL_SECONDS = 3  # between reads of something Galaxy is still working on
MODEL_FORWARD_TIMEOUT_SECONDS = 300  # the recorder's wait on the model endpoint it forwards to

# Watched runs
WATCH_PORT = 5180  # the dev server `--watch` starts; OLIT_WATCH_PORT
WATCH_SETTLE_MS = 10_000  # idle this long after settled work before the next input is typed

# Videos of watched runs
VIDEO_FPS = 15
VIDEO_CRF = 23  # H.264 quality: lower is sharper and larger; 23 keeps the page's text sharp
VIDEO_DECIMATE = "hi=64*12:lo=64*5:frac=0.33"  # mpdecimate: drop frames that repeat the last one
