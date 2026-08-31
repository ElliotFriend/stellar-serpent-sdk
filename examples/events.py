"""Events: both publish spellings, a topics-marked event, and an all-data one.

`tests/fixtures/token_style.py` already shows the authoring form
(`Transfer(...).publish(env)`) and `tests/fixtures/token_style_canonical.py`
shows the equivalent canonical spelling (`env.events().publish(topics,
data)`) for the SAME event, in two separate fixtures. This example puts both
spellings in one file, on two DIFFERENT events, so a reader sees the choice
made side by side rather than across a diff.

* `Scored` is **topics-marked**: `player` rides in the topic list
  (`Annotated[Address, topic]`), and `score` is the lone data field
  (`data_format="single-value"` -- the host publishes it bare, not wrapped in a
  container). `record_score` publishes it through the AUTHORING form:
  `Scored(player=player, score=score).publish(env)`. Kwargs-only construction,
  every field required -- the same rule `@contracttype` enforces.
* `Tally` is **all-data**: no field is marked `topic`, so its only topic is the
  default prefix (the snake-cased class name, `tally`). `record_tally`
  publishes it through the CANONICAL form, spelled out by hand to match
  exactly what `Tally(...).publish(env)` would desugar to:
  `env.events().publish((Symbol("tally"),), Vec(U32, [wins, losses]))`.

**Why `Tally`'s prefix topic is kept short, on purpose.** A DECLARED prefix
topic may be up to 32 characters (`Event.publish`'s own docstring --
`examples/structs.py` shows the linear-memory consequence of a long name
elsewhere), but the CANONICAL spelling's hand-written topics tuple is held to
the stricter 9-character `SymbolSmall` bound at compile time (`SPT3019`,
S11's convention for a hand-written topic list). Reaching `Tally` through
BOTH spellings in this file -- which is the whole point of putting it next to
`env.events().publish(...)` -- means its prefix has to satisfy the tighter of
the two rules, so `tally` (5 characters) was chosen instead of a longer, more
descriptive name that only the authoring form could have carried.

**`data_format="vec"` is `Tally`'s other reason for existing.** `wins` and
`losses` publish as one `Vec[U32]`, not a `Map` (the default) and not two
separate values -- and M1 restricts `"vec"` to a UNIFORM element type across
every data field (`_check_data_format`), which is why both fields here are the
same chain type. A mixed payload (a `U32` next to a `String`, say) has to use
the default `"map"` format instead; `tests/unit/test_emitter_end_to_end.py`'s
throwaway `Formats` contract covers that combination, so this example does not
repeat it.

`tests/unit/test_examples.py` proves the two things worth proving about this
file: the tier-1 and WASM legs agree on both published events, and the
canonical spelling in `record_tally` really does produce the identical
`(topics, data)` that `Tally(wins=..., losses=...).publish(env)` would -- the
equivalence claim, checked on the events THIS file ships rather than only on
`token_style.py`'s pair.

Run it two ways -- `tests/unit/test_examples.py` does both and asserts the two
legs agree:

    from serpent.env import Env, deploy

    env = Env()
    scoreboard = deploy(Scoreboard, env)
    with env.frame():
        scoreboard.record_score(env, player, U32(7))
        scoreboard.record_tally(env, U32(3), U32(1))
        env.published_events   # -> the two (topics, data) snapshots
"""

from serpent import (
    U32,
    Address,
    Annotated,
    Env,
    Event,
    Symbol,
    Vec,
    contract,
    contractevent,
    topic,
)


@contractevent(topics=("scored",), data_format="single-value")
class Scored(Event):
    """Topics-marked: `player` is a topic, `score` is the bare data value."""

    player: Annotated[Address, topic]
    score: U32


@contractevent(data_format="vec")
class Tally(Event):
    """All-data: no `topic`-marked field, so the topic list is just the
    default prefix (`tally`, derived from the class name). Both data fields
    are `U32`, which `"vec"` requires."""

    wins: U32
    losses: U32


@contract
class Scoreboard:
    """Two entry points, one per event, one per publish spelling."""

    def record_score(self, env: Env, player: Address, score: U32) -> None:
        """The AUTHORING form."""
        Scored(player=player, score=score).publish(env)

    def record_tally(self, env: Env, wins: U32, losses: U32) -> None:
        """The CANONICAL form, hand-written to match `Tally(wins=wins,
        losses=losses).publish(env)`'s desugar exactly: the same prefix topic
        naming the event, and the same `Vec[U32]` data."""
        env.events().publish((Symbol("tally"),), Vec(U32, [wins, losses]))
