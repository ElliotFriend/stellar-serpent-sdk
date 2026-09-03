"""Deploy, the ambient invocation frame, and the recorded-auth model.

Three tier-1 answers live here, and all three are about states the CHAIN cannot
produce (dossier risk 13 -- "a tier-1-only state" is this file's whole subject):

* **deploy runs the constructor once, at deploy** (ruling E7(i), S12). On chain
  `__init__` compiles to `__constructor`, which the host runs exactly once as
  part of the deploy operation. `serpent.env.deploy` is where that becomes
  visible in a test, and it is where S12's ERROR LAUNDERING is modelled: the
  author's error code does NOT reach the deployer, `ConstructorFailed` does.
* **nothing reaches the host outside a frame, and no frame opens before
  deploy** (ruling E7(ii)). Both refusals are loud `RuntimeError`s naming
  `deploy` and `env.frame()`, because a silent accept here is exactly the
  "silent false green" S5 warns about: a test asserting on storage a deploy
  would have had to write, or on an authorization no invocation asked for.
* **auth is RECORDING** (S4, mock-all-auths). `auths=None` records every
  `require_auth` and succeeds; a non-`None` allow-set refuses a non-member with
  `AuthorizationFailed`. There are no auth trees, no nonces and no signatures
  anywhere in this repo -- `serpent.env`'s header docstring says so, and the
  real host is the gate for anything this file appears to prove about
  authorization: a refusal there is a host trap that records nothing
  (HOST_FACTS row `a_refused_auth_is_an_auth_trap_and_records_nothing`).

Two honest pins carried here from Task 2 rather than left to F: the args
snapshot that the frontend's escape exemption for `require_auth_for_args`
RESTS on (`recognize.note_escapes`), and the publish-then-raise non-rollback.
"""

import pytest

from serpent import (
    U32,
    Address,
    ContractError,
    Env,
    Symbol,
    Vec,
    _frame,
    contract,
    contracterror,
    errorcode,
)
from serpent import env as env_module
from serpent.env import AuthorizationFailed, ConstructorFailed, deploy
from serpent.errors import RESERVED_CODE_MIN
from tests.unit.conftest import deployed_env

ACCOUNT = "GA7QYNF7SOWQ3GLR2BGMZEHXAVIRZA4KVWLTJJFC7MGXUA74P7UJVSGZ"
OTHER_ACCOUNT = "GCUNZ4XXN2LPHSGWPGCVZAZ4GUWL6HMXLJ7NCHCPB3I23EPY6JCVISSY"

OWNER = Symbol("OWNER")
COUNT = Symbol("COUNT")


@contracterror
class Error:
    Refused = errorcode(7)


@contract
class Counter:
    """The authoring form: a constructor that writes, and one export."""

    def __init__(self, env: Env, start: U32) -> None:
        env.storage().instance().set(COUNT, start)

    def bump(self, env: Env) -> U32:
        total = env.storage().instance().get(COUNT, U32) + U32(1)
        env.storage().instance().set(COUNT, total)
        return total


@contract
class Authorizing:
    """A constructor that authorizes -- legal on chain, inside the deploy frame."""

    def __init__(self, env: Env, owner: Address) -> None:
        owner.require_auth()
        env.storage().instance().set(OWNER, owner)


@contract
class Refusing:
    def __init__(self, env: Env) -> None:
        raise Error.Refused


@contract
class WritesThenRaises:
    """A constructor that WRITES and then fails: the poison-flag repro."""

    def __init__(self, env: Env) -> None:
        env.storage().instance().set(COUNT, U32(41))
        raise Error.Refused


@contract
class ReadsInTheConstructor:
    def __init__(self, env: Env) -> None:
        env.storage().instance().set(COUNT, env.storage().instance().get(COUNT, U32, U32(0)))


@contract
class Overflows:
    """A constructor that fails the plain-Python way, not with a code."""

    def __init__(self, env: Env) -> None:
        raise ValueError("a constructor can fail without a contract error code")


class Counted:
    """A plain (undecorated) class counting its own constructor runs."""

    runs = 0

    def __init__(self, env: Env) -> None:
        type(self).runs += 1


class NoConstructor:
    __slots__ = ()


class NoEnvParameter:
    """The habit-shaped mistake: a constructor that forgot the env."""

    def __init__(self) -> None:  # pragma: no cover - never actually called
        raise AssertionError("deploy must refuse this before calling it")


# --- deploy: once, at deploy, inside a frame --------------------------------


def test_deploy_runs_the_constructor_exactly_once() -> None:
    """`__init__` IS `__constructor` (S12): the host runs it as part of the
    deploy operation, never again."""
    Counted.runs = 0
    env = Env()
    instance = deploy(Counted, env)
    assert isinstance(instance, Counted)
    assert Counted.runs == 1


def test_deploy_forwards_positional_and_keyword_arguments() -> None:
    env = Env()
    positional = deploy(Counter, env, U32(5))
    assert isinstance(positional, Counter)
    with env.frame():
        assert env.storage().instance().get(COUNT, U32) == U32(5)

    other = Env()
    deploy(Counter, other, start=U32(9))
    with other.frame():
        assert other.storage().instance().get(COUNT, U32) == U32(9)


def test_the_constructor_runs_inside_an_invocation_frame() -> None:
    """The constructor has full storage access and can authorize, because the
    host runs it in a real frame -- so `deploy` enters one for it."""
    env = Env()
    owner = Address(ACCOUNT)
    deploy(Authorizing, env, owner)
    assert env.recorded_auths == ((owner, None),)
    with env.frame():
        assert env.storage().instance().get(OWNER, Address) == owner


def test_the_frame_deploy_opened_is_closed_again() -> None:
    """`deploy` is not a way to leave a frame standing: the accessors refuse
    again the moment it returns."""
    env = Env()
    deploy(Counter, env, U32(1))
    assert _frame.current() is None
    with pytest.raises(RuntimeError, match="frame"):
        env.storage()


def test_a_class_with_no_constructor_deploys() -> None:
    """S12: a 0-arg constructor may be absent -- nothing runs, and the env is
    deployed all the same."""
    env = Env()
    deploy(NoConstructor, env)
    with env.frame():
        env.storage().instance().set(COUNT, U32(1))


def test_deploy_refuses_arguments_when_there_is_no_constructor() -> None:
    """S12's third rule: args-without-constructor is an error, not a silent
    drop -- on chain the deploy operation itself fails."""
    env = Env()
    with pytest.raises(TypeError, match="no constructor"):
        deploy(NoConstructor, env, U32(1))
    assert not env.published_events
    with pytest.raises(RuntimeError, match="deploy"):
        env.storage()


def test_deploy_reports_a_signature_mistake_as_itself() -> None:
    """A wrong-arity or unknown-keyword deploy is the TEST AUTHOR's mistake, so
    it surfaces as a plain `TypeError` naming the signature -- not laundered
    into `ConstructorFailed`, which would blame the contract for it. The args
    are bound before the constructor is ever entered."""
    env = Env()
    with pytest.raises(TypeError, match="cannot take these arguments"):
        deploy(Counter, env)
    with pytest.raises(TypeError, match="cannot take these arguments"):
        deploy(Counter, env, U32(1), U32(2))
    with pytest.raises(TypeError, match="cannot take these arguments"):
        deploy(Counter, env, nope=U32(1))
    # Nothing was deployed, so nothing can be invoked. And nothing RAN, so the
    # env is not poisoned either -- a bad call is not a failed deploy.
    with pytest.raises(RuntimeError, match="deploy"):
        env.storage()
    deploy(Counter, env, U32(1))


def test_a_constructor_that_forgot_the_env_is_told_so() -> None:
    """`def __init__(self)` is the shape a Python author writes by habit, and
    the bare bind error for it ("too many positional arguments") names the
    symptom. `deploy` always passes the env, because `__constructor` runs with a
    live host env -- so the message says that."""
    with pytest.raises(TypeError, match="cannot take these arguments") as excinfo:
        deploy(NoEnvParameter, Env())
    assert "def __init__(self, env: Env" in str(excinfo.value)
    # ...and the hint is not attached to a constructor that DOES take an env and
    # was merely called wrong.
    with pytest.raises(TypeError) as counter_excinfo:
        deploy(Counter, Env())
    assert "env: Env" not in str(counter_excinfo.value)


def test_a_second_deploy_into_the_same_env_is_a_loud_error() -> None:
    """One `Env` models ONE deployed contract instance (there is no
    cross-contract call in M1), so a second deploy is a test-authoring mistake
    worth naming rather than a silent second constructor run."""
    Counted.runs = 0
    env = Env()
    deploy(Counted, env)
    with pytest.raises(RuntimeError, match="already has"):
        deploy(Counted, env)
    with pytest.raises(RuntimeError, match="already has"):
        deploy(NoConstructor, env)
    assert Counted.runs == 1


# --- S12's laundering ------------------------------------------------------


def test_a_raising_constructor_surfaces_as_constructor_failed() -> None:
    """**S12, the caveat the spec says the docs "must say so, prominently".**

    The host launders constructor errors: a recoverable error raised in
    `__constructor` reaches the deployer as `Context(InvalidAction)`, NOT as the
    contract's own code. So the tier-1 model refuses to let the author's
    exception identity out either -- a test that caught `Error.Refused` here
    would be asserting something the chain never does.

    The original is still reachable as `__cause__`, which is what makes this a
    model of the laundering rather than a swallow.
    """
    env = Env()
    with pytest.raises(ConstructorFailed) as excinfo:
        deploy(Refusing, env)

    launderer = excinfo.value
    # The author's identity is NOT what surfaced...
    assert not isinstance(launderer, ContractError)
    assert not isinstance(launderer, Error.Refused)
    # ...but it is retrievable, and the message names it.
    cause = launderer.__cause__
    assert isinstance(cause, Error.Refused)
    assert isinstance(cause, ContractError)
    assert type(cause).code == 7
    assert "Refused" in str(launderer)


def test_the_authors_error_does_not_propagate_out_of_deploy() -> None:
    """The other half of the same rule, stated as the negative: catching the
    contract's own error class around a `deploy` catches nothing."""
    env = Env()
    with pytest.raises(ConstructorFailed):
        try:
            deploy(Refusing, env)
        except Error.Refused:  # pragma: no cover - must never be taken
            pytest.fail("the author's code escaped __init__; S12 says it cannot")


def test_constructor_failed_is_not_a_contract_error_and_has_no_code() -> None:
    """It models a HOST action (`Context(InvalidAction)`), not a contract error,
    so it carries no reserved code and cannot be raised from a contract."""
    assert issubclass(ConstructorFailed, RuntimeError)
    assert not issubclass(ConstructorFailed, ContractError)
    assert not hasattr(ConstructorFailed, "code")


def test_a_plain_python_failure_in_the_constructor_is_laundered_too() -> None:
    """S12 says "any recoverable error", not "any contract error": a constructor
    that fails the ordinary Python way is still a failed deploy, and the
    deployer still sees the host's laundered answer."""
    env = Env()
    with pytest.raises(ConstructorFailed) as excinfo:
        deploy(Overflows, env)
    assert isinstance(excinfo.value.__cause__, ValueError)


def test_an_auth_failure_in_the_constructor_is_NOT_laundered() -> None:
    """**The boundary of S12's laundering.** The rule is about RECOVERABLE
    errors: those reach the deployer as `Context(InvalidAction)`. An
    unauthorized invocation is not one of them -- the host TRAPS -- so
    `AuthorizationFailed` propagates from `deploy` as itself.

    Laundering it would model a host behaviour that does not exist, and would
    hide the one auth failure a constructor can actually have.
    """
    env = Env(auths=[])
    with pytest.raises(AuthorizationFailed, match="not authorized"):
        deploy(Authorizing, env, Address(ACCOUNT))
    assert _frame.current() is None
    # It failed, so the env is poisoned exactly as a laundered failure would
    # leave it: only the identity of the error differs.
    with pytest.raises(RuntimeError, match="already FAILED"):
        deploy(NoConstructor, env)


def test_a_failed_deploy_poisons_the_env() -> None:
    """**A retry would hand the second instance the dead one's leftovers.**

    The reviewer's repro, as a test: the failed constructor wrote `COUNT = 41`
    and there is NO FRAME ROLLBACK in this model, so those bytes are still in
    the store. On chain the deploy operation is atomic -- a failed deploy
    publishes no instance and leaves no storage -- so a second deploy reading
    that 41 is a tier-1-only state, which is the whole class the E7(ii) gate
    exists to refuse. Poison, do not permit: the env is refused for good, and
    the message names `Env()` as the remedy.
    """
    env = Env()
    with pytest.raises(ConstructorFailed):
        deploy(WritesThenRaises, env)

    with pytest.raises(RuntimeError, match="already FAILED") as excinfo:
        deploy(ReadsInTheConstructor, env)
    assert "fresh Env()" in str(excinfo.value)
    # ...for every class shape, and for a re-deploy of the same class.
    with pytest.raises(RuntimeError, match="already FAILED"):
        deploy(NoConstructor, env)
    with pytest.raises(RuntimeError, match="already FAILED"):
        deploy(WritesThenRaises, env)

    # Nothing is deployed, so the frame refuses as well -- and the refusal now
    # says WHY this env is unusable rather than just "deploy first".
    with pytest.raises(RuntimeError, match="deploy") as frame_excinfo, env.frame():
        pytest.fail("a frame opened on a poisoned Env")  # pragma: no cover
    assert "already FAILED" in str(frame_excinfo.value)
    with pytest.raises(RuntimeError, match="already FAILED"):
        env.storage()

    # A fresh Env is the remedy, and it starts empty: the leftovers were the
    # poisoned env's, not the class's.
    clean = Env()
    deploy(ReadsInTheConstructor, clean)
    with clean.frame():
        assert clean.storage().instance().get(COUNT, U32) == U32(0)


def test_a_failed_deploy_leaves_the_env_undeployed_and_unframed() -> None:
    """A deploy whose constructor fails deploys nothing: the frame is gone
    (F.1.7) and the env still refuses to be invoked.

    What the failed constructor already wrote is still in the store, because
    the model has NO FRAME ROLLBACK (see `serpent.env`'s header docstring). The
    refusal is what keeps that unobservable at tier 1.
    """
    env = Env()
    with pytest.raises(ConstructorFailed):
        deploy(Refusing, env)
    assert _frame.current() is None
    with pytest.raises(RuntimeError, match="deploy"), env.frame():
        pytest.fail("a frame opened after a failed deploy")  # pragma: no cover
    with pytest.raises(RuntimeError, match="deploy"):
        env.storage()


# --- the pre-deploy refusal (ruling E7(ii)) --------------------------------


def test_every_host_accessor_refuses_before_deploy() -> None:
    """The state the chain cannot produce: an export running before the
    constructor did. ONE boolean, and a message naming `deploy`."""
    env = Env()
    for accessor in (env.storage, env.ledger, env.events):
        with pytest.raises(RuntimeError, match="deploy"):
            accessor()


def test_a_frame_refuses_to_open_before_deploy() -> None:
    """The frame is the only way in, so this is the refusal that makes the
    other ones unreachable rather than merely rude."""
    env = Env()
    with pytest.raises(RuntimeError, match="deploy"), env.frame():
        pytest.fail("a frame opened on an undeployed Env")  # pragma: no cover


def test_the_test_facing_inspection_surfaces_need_no_frame() -> None:
    """`advance`, `published_events` and `recorded_auths` are TEST surfaces, not
    host functions: a contract cannot reach them, so gating them would only
    make the model's own tests harder to write."""
    env = Env()
    env.advance(1)
    assert env.published_events == ()
    assert env.recorded_auths == ()


# --- the frame: outside it, nested, and across two envs --------------------


def test_every_host_accessor_refuses_outside_a_frame() -> None:
    env = Env()
    deploy(NoConstructor, env)
    for accessor in (env.storage, env.ledger, env.events):
        with pytest.raises(RuntimeError, match="frame"):
            accessor()


def test_a_bucket_captured_inside_a_frame_refuses_after_it_closes() -> None:
    """The gate is on the OPERATIONS too, not only on `env.storage()`.

    A bucket is a wrapper over the env's store, so a test that held one across
    the end of a frame would otherwise have an ungated back door into the
    model -- a write with no invocation to attribute it to.
    """
    env = Env()
    deploy(NoConstructor, env)
    with env.frame():
        bucket = env.storage().persistent()
        bucket.set(COUNT, U32(1))
        events = env.events()
        ledger = env.ledger()
    # The ledger is read-only, and gated all the same: `get_ledger_timestamp` is
    # a host function, so reading one with no invocation open is the same
    # impossible state as a write with no invocation to attribute it to.
    with pytest.raises(RuntimeError, match="frame"):
        ledger.timestamp()
    with pytest.raises(RuntimeError, match="frame"):
        ledger.sequence()
    with pytest.raises(RuntimeError, match="frame"):
        bucket.set(COUNT, U32(2))
    with pytest.raises(RuntimeError, match="frame"):
        bucket.get(COUNT, U32)
    with pytest.raises(RuntimeError, match="frame"):
        bucket.has(COUNT)
    with pytest.raises(RuntimeError, match="frame"):
        bucket.del_(COUNT)
    with pytest.raises(RuntimeError, match="frame"):
        bucket.extend_ttl(COUNT, U32(1), U32(2))
    with pytest.raises(RuntimeError, match="frame"):
        events.publish((Symbol("e"),), U32(1))


def test_the_instance_buckets_keyless_extend_ttl_is_gated_too() -> None:
    env = Env()
    deploy(NoConstructor, env)
    with env.frame():
        bucket = env.storage().instance()
    with pytest.raises(RuntimeError, match="frame"):
        bucket.extend_ttl(U32(1), U32(2))


def test_frames_nest_on_the_same_env() -> None:
    """A contract calling its own method is one host frame deep or two; either
    way the ambient env is the same env, so nesting is fine (and the inner exit
    must not clear the outer frame)."""
    env = Env()
    deploy(NoConstructor, env)
    with env.frame():
        with env.frame():
            env.storage().instance().set(COUNT, U32(1))
        assert env.storage().instance().get(COUNT, U32) == U32(1)
    assert _frame.current() is None


def test_a_frame_for_another_env_while_one_is_active_is_refused() -> None:
    """There is no cross-contract call in M1 (S3), so two envs with overlapping
    frames is not a state the model has any semantics for -- and a silently
    nested one would record the inner contract's auth on the outer env."""
    first = Env()
    second = Env()
    deploy(NoConstructor, first)
    deploy(NoConstructor, second)
    with first.frame():
        with pytest.raises(RuntimeError, match="cross-contract"), second.frame():
            pytest.fail("two envs framed at once")  # pragma: no cover
        # ...and the outer frame is untouched by the refusal.
        first.storage().instance().set(COUNT, U32(1))
    assert _frame.current() is None


def test_deploy_is_refused_while_another_envs_frame_is_active() -> None:
    """The deploy frame is a frame, so it obeys the same cross-env rule -- and it
    obeys it for BOTH class shapes.

    The constructor-less path has nothing to run, but it still enters and leaves
    the frame: an early return that skipped it would have made a
    constructor-less contract the one thing that could be deployed from inside
    another contract's invocation, which is a cross-contract deploy and M1 has
    no such thing.
    """
    first = Env()
    deploy(NoConstructor, first)
    with first.frame():
        # The constructor-less shape...
        with pytest.raises(RuntimeError, match="cross-contract"):
            deploy(NoConstructor, Env())
        # ...and the one that has a constructor to run.
        with pytest.raises(RuntimeError, match="cross-contract"):
            deploy(Counter, Env(), U32(1))


def test_the_other_envs_accessors_are_refused_while_this_ones_frame_is_open() -> None:
    first = Env()
    second = Env()
    deploy(NoConstructor, first)
    deploy(NoConstructor, second)
    with first.frame(), pytest.raises(RuntimeError, match="another"):
        second.storage()


def test_a_raising_frame_leaves_no_stale_ambient_env() -> None:
    """**Dossier F.1.7.** The ambient env is the risk contextvars carry: a
    frame that raises before clearing it would authorize the NEXT call against
    the wrong contract, and mock-all-auths means that call SUCCEEDS -- a green
    test with the auth recorded in the wrong place.
    """
    env = Env()
    deploy(NoConstructor, env)
    with pytest.raises(Error.Refused), env.frame():
        raise Error.Refused
    assert _frame.current() is None
    with pytest.raises(RuntimeError, match="frame"):
        Address(ACCOUNT).require_auth()


def test_an_event_published_before_a_raise_is_not_rolled_back() -> None:
    """**NOT modelled: S9's frame rollback** (dossier F.2.8), pinned honestly.

    On chain the event and the storage write go away with the failed frame.
    Tier 1 keeps both, and the mini-host keeps both too, so NEITHER tier in
    this repo is evidence about rollback -- the real host is: proven in
    HOST_FACTS row `an_event_published_before_a_raise_is_rolled_back`
    (a declared `host_diverges`; adopting the rollback at tier 1 stays M2's).
    """
    env = Env()
    deploy(NoConstructor, env)
    with pytest.raises(Error.Refused), env.frame():
        env.events().publish((Symbol("e"),), U32(1))
        env.storage().persistent().set(COUNT, U32(1))
        raise Error.Refused
    assert len(env.published_events) == 1
    with env.frame():
        assert env.storage().persistent().get(COUNT, U32) == U32(1)


# --- auth: recording is the model (S4) ------------------------------------


def test_mock_all_auths_records_and_succeeds() -> None:
    """`auths=None`: every authorization is recorded and allowed. Recording IS
    the model -- there is no signature, no nonce and no auth tree here."""
    env = deployed_env()
    owner = Address(ACCOUNT)
    spender = Address(OTHER_ACCOUNT)
    owner.require_auth()
    spender.require_auth()
    assert env.recorded_auths == ((owner, None), (spender, None))


def test_an_allow_set_authorizes_its_members_and_refuses_the_rest() -> None:
    owner = Address(ACCOUNT)
    stranger = Address(OTHER_ACCOUNT)
    env = deployed_env(auths=[owner])
    owner.require_auth()
    with pytest.raises(AuthorizationFailed, match="not authorized"):
        stranger.require_auth()
    with pytest.raises(AuthorizationFailed):
        stranger.require_auth_for_args(Vec(U32, [U32(1)]))
    assert env.recorded_auths == ((owner, None),)


def test_an_empty_allow_set_authorizes_nobody() -> None:
    """`auths=[]` is NOT `auths=None`: an empty allow-set is a contract nobody
    has authorized, which is a state worth being able to test."""
    env = deployed_env(auths=[])
    with pytest.raises(AuthorizationFailed, match="not authorized"):
        Address(ACCOUNT).require_auth()
    assert env.recorded_auths == ()


def test_a_refused_authorization_is_not_recorded() -> None:
    """On chain the host TRAPS on an unauthorized invocation, so there is no
    invocation left to have recorded anything."""
    env = deployed_env(auths=[])
    with pytest.raises(AuthorizationFailed):
        Address(ACCOUNT).require_auth()
    assert env.recorded_auths == ()


def test_authorization_failed_is_not_a_contract_error_and_has_no_code() -> None:
    """The host's auth failure is a TRAP, not a contract error code: no reserved
    code describes it, and inventing one would put a number in a trace that the
    chain never produces."""
    assert issubclass(AuthorizationFailed, RuntimeError)
    assert not issubclass(AuthorizationFailed, ContractError)
    assert not hasattr(AuthorizationFailed, "code")
    assert not hasattr(ConstructorFailed, "code")
    # Neither one is one of the 256 reserved runtime codes.
    for reserved in vars(env_module).values():
        assert not (isinstance(reserved, int) and reserved >= RESERVED_CODE_MIN)


# --- require_auth_for_args: the snapshot the frontend depends on ----------


def test_require_auth_for_args_records_the_args() -> None:
    env = deployed_env()
    owner = Address(ACCOUNT)
    args = Vec(U32, [U32(1), U32(2)])
    owner.require_auth_for_args(args)
    one = env.recorded_auths
    assert len(one) == 1
    address, recorded = one[0]
    assert address == owner
    assert isinstance(recorded, Vec)
    assert recorded == Vec(U32, [U32(1), U32(2)])
    # A bare `require_auth` records `None`, so the two are distinguishable.
    owner.require_auth()
    both = env.recorded_auths
    assert both[1] == (owner, None)


def test_require_auth_for_args_snapshots_its_args() -> None:
    """**Ruling E5, and the frontend exemption that RESTS on this test.**

    `recognize.note_escapes` treats a container passed to
    `require_auth_for_args` as NOT escaping -- so the frontend lets the
    contract keep mutating it afterwards. That is only sound because the model
    (like the host, which serializes the args into the auth entry) takes a deep
    copy at the boundary. If this test ever has to be weakened, that exemption
    has to go with it.
    """
    env = deployed_env()
    args = Vec(U32, [U32(1)])
    Address(ACCOUNT).require_auth_for_args(args)
    args.push_back(U32(2))
    recorded = env.recorded_auths[0][1]
    assert isinstance(recorded, Vec)
    assert len(recorded) == 1


def test_recorded_auths_is_an_immutable_snapshot_view() -> None:
    """The inspection surface obeys the same deep-copy law as
    `published_events`: mutating what you just read cannot corrupt the record.
    """
    env = deployed_env()
    Address(ACCOUNT).require_auth_for_args(Vec(U32, [U32(1)]))
    first = env.recorded_auths
    Address(OTHER_ACCOUNT).require_auth()
    assert len(first) == 1
    assert len(env.recorded_auths) == 2

    args = env.recorded_auths[0][1]
    assert isinstance(args, Vec)
    args.push_back(U32(99))
    reread = env.recorded_auths[0][1]
    assert isinstance(reread, Vec)
    assert len(reread) == 1


def test_require_auth_outside_a_frame_is_loud() -> None:
    """A stray `require_auth()` cannot be a silent pass: mock-all-auths means it
    would SUCCEED, which is a green test asserting an authorization nothing
    asked for (dossier risk 7)."""
    address = Address(ACCOUNT)
    with pytest.raises(RuntimeError, match="deploy"):
        address.require_auth()
    with pytest.raises(RuntimeError, match="frame"):
        address.require_auth_for_args(Vec(U32, [U32(1)]))


# --- the authoring surface is unchanged -----------------------------------


def test_a_deployed_contract_is_ordinary_python() -> None:
    """Tier-1 invocation, end to end: methods are just methods, and the frame is
    what makes the call legal."""
    env = Env()
    counter = deploy(Counter, env, U32(0))
    with env.frame():
        assert counter.bump(env) == U32(1)
        assert counter.bump(env) == U32(2)
    # ...and outside a frame the same call is refused, wherever it touches the
    # host first.
    with pytest.raises(RuntimeError, match="frame"):
        counter.bump(env)


def test_the_frame_module_is_a_leaf() -> None:
    """**Review B4, pinned.** `env -> types -> address -> env` is a real cycle,
    so the ambient contextvar cannot live at either end of it: `_frame` imports
    the stdlib and NOTHING from serpent, which is what lets both ends import it.

    Read statically (`ast`), because importing it would only prove that the
    import order this test happens to run in works.
    """
    import ast
    import pathlib

    source = pathlib.Path(_frame.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".", 1)[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            # A relative import is serpent-internal by construction.
            imported.add("serpent" if node.level or not node.module else node.module)
    assert imported == {"contextvars", "__future__"}


def test_the_deploy_helpers_are_not_authoring_names() -> None:
    """Dossier C.1 point 5: `deploy`/`frame` are test surfaces. A contract
    resolves names against `serpent.__all__`, and the loader refuses anything
    else -- so these must not be there."""
    import serpent

    for name in ("deploy", "frame", "ConstructorFailed", "AuthorizationFailed"):
        assert name not in serpent.__all__
        assert name not in env_module.__all__
