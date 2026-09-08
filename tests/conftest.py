"""Pytest configuration for the offline suite.

Keep `pytest tests/` free of credentials, private data and spend.

`test_e2e_real.py` and `test_model_bakeoff.py` are live benchmark SCRIPTS, not
unit tests. They contribute zero test functions, but they do real work at
MODULE level — which pytest executes merely by importing them during
collection:

  * `load_dotenv(~/.waffler-hosted/.env)` — pulls the user's real API keys
    into the test process;
  * `json.loads(~/.waffler-hosted/history.json)` — reads their private
    dictation history;
  * `test_e2e_real` additionally asserts GROQ_API_KEY is present and builds a
    live styler, so on a machine without a key collection ERRORS.

So running the "offline" suite silently depended on the maintainer's personal
credentials and recordings, and would fail for any contributor or CI runner.
Ignoring them at collection time prevents the import entirely; run them
deliberately with `python tests/test_e2e_real.py` when live testing is
intended and authorised.
"""

collect_ignore = [
    "test_e2e_real.py",
    "test_model_bakeoff.py",
]
