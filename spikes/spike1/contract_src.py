from serpent_stub import Env, String, Symbol, U32, contract, contracterror, contracttype


@contracterror
class Error:
    LimitExceeded = 7


@contracttype
class Settings:
    counter_limit: U32      # 13 chars -> forces SymbolObject via linear memory
    display_name: String    # forces a string literal + data section


@contract
class Spike:
    def setup(env: Env, counter_limit: U32) -> None:
        """Store settings with a long-named field and a string literal."""
        settings = Settings(
            counter_limit=counter_limit,
            display_name=String("serpent phase zero"),
        )
        env.storage().instance().set(Symbol("SETTINGS"), settings)

    def bump(env: Env) -> U32:
        """Increment a persistent counter; raise LimitExceeded above the limit."""
        settings = env.storage().instance().get(Symbol("SETTINGS"), Settings)
        count = env.storage().persistent().get(Symbol("COUNT"), U32, default=U32(0))
        count = count + U32(1)
        if count > settings.counter_limit:
            raise Error.LimitExceeded
        env.storage().persistent().set(Symbol("COUNT"), count)
        return count
