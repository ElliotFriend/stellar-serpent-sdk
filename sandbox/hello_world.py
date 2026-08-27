from serpent import Env, Symbol, Vec, contract, contracterror, errorcode
# from .storage import set_greeting_salutation

def set_greeting_salutation(env: Env, greeting: Symbol) -> Symbol:
    if greeting == Symbol("Hello"):
        raise Error.Unimaginative

    env.storage().instance().set(Symbol("GREETING"), greeting)
    return greeting

@contracterror
class Error:
    Unimaginative = errorcode(1)

@contract
class HelloWorld:
    def __init__(self, env: Env, greeting: Symbol) -> None:
        _ = set_greeting_salutation(env, greeting)

    def set_greeting(self, env: Env, greeting: Symbol) -> Symbol:
        return set_greeting_salutation(env, greeting)

    def get_greeting(self, env: Env) -> Symbol:
        return env.storage().instance().get(Symbol("GREETING"), Symbol)

    def hello(self, env: Env, name: Symbol) -> Vec[Symbol]:
        greeting = env.storage().instance().get(Symbol("GREETING"), Symbol, default=Symbol("Hola"))
        my_vec = Vec(Symbol, [greeting, name])

        return my_vec
