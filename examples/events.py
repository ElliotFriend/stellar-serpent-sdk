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
* `RoundClosed` is **all-data**: no field is marked `topic`, so its only topic
  is the default prefix (the snake-cased class name, `round_closed`).
  `record_round_closed` publishes it through the CANONICAL form, spelled out by
  hand to match exactly what `RoundClosed(...).publish(env)` would desugar to:
  `env.events().publish((Symbol("round_closed"),), Vec(U32, [wins, losses]))`.

**Why `round_closed` is spelled out at 12 characters.** A topic Symbol has ONE
bound -- the Symbol's own 32 characters -- and it is the same bound whether the
name comes from a DECLARED prefix topic or from a hand-written topics tuple
(`SPT3019` says only that `topics[0]` must be a Symbol). Past nine characters
the name stops fitting a `SymbolSmall` word and becomes a `SymbolObject` the
compiler pools through linear memory at the publish site --
`examples/structs.py` shows that same consequence for a long name elsewhere --
which costs a `symbol_new_from_linear_memory` call and nothing else. So
reaching this event through BOTH spellings, which is the whole point of putting
it next to `env.events().publish(...)`, does not force a terse name: an event
gets named for what happened.

**`data_format="vec"` is `RoundClosed`'s other reason for existing.** `wins` and
`losses` publish as one `Vec[U32]`, not a `Map` (the default) and not two
separate values -- and M1 restricts `"vec"` to a UNIFORM element type across
every data field (`_check_data_format`), which is why both fields here are the
same chain type. A mixed payload (a `U32` next to a `String`, say) has to use
the default `"map"` format instead; `tests/unit/test_emitter_end_to_end.py`'s
throwaway `Formats` contract covers that combination, so this example does not
repeat it.

`tests/unit/test_examples.py` proves the two things worth proving about this
file: the tier-1 and WASM legs agree on both published events, and the
canonical spelling in `record_round_closed` really does produce the identical
`(topics, data)` that `RoundClosed(wins=..., losses=...).publish(env)` would --
the equivalence claim, checked on the events THIS file ships rather than only
on `token_style.py`'s pair.

Run it two ways -- `tests/unit/test_examples.py` does both and asserts the two
legs agree:

    from serpent.env import Env, deploy

    env = Env()
    scoreboard = deploy(Scoreboard, env)
    with env.frame():
        scoreboard.record_score(env, player, U32(7))
        scoreboard.record_round_closed(env, U32(3), U32(1))
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
class RoundClosed(Event):
    """All-data: no `topic`-marked field, so the topic list is just the default
    prefix (`round_closed`, derived from the class name). Both data fields are
    `U32`, which `"vec"` requires."""

    wins: U32
    losses: U32


@contract
class Scoreboard:
    """Two entry points, one per event, one per publish spelling."""

    def record_score(self, env: Env, player: Address, score: U32) -> None:
        """The AUTHORING form."""
        Scored(player=player, score=score).publish(env)

    def record_round_closed(self, env: Env, wins: U32, losses: U32) -> None:
        """The CANONICAL form, hand-written to match `RoundClosed(wins=wins,
        losses=losses).publish(env)`'s desugar exactly: the same prefix topic
        naming the event, and the same `Vec[U32]` data."""
        env.events().publish((Symbol("round_closed"),), Vec(U32, [wins, losses]))
