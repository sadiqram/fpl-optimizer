"""Business-logic entry points shared by the CLI (`cli.py`) and the web API (`api/routers/`).

Each function here is what a `cli._cmd_*` function used to do minus the `print`s — it
returns plain dicts/DataFrames instead. This is the one-code-path split (Architecture P2):
two front ends, one implementation, so `recommend` (say) can never behave differently
through the CLI than through the API.
"""
