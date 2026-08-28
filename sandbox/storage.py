from serpent import Env, Symbol

from .hello_world import Error


def set_greeting_salutation(env: Env, greeting: Symbol) -> Symbol:
    if greeting == Symbol("Hello"):
        raise Error.Unimaginative

    env.storage().instance().set(Symbol("GREETING"), greeting)
    return greeting
