# serpent:reject SPT1032
# serpent:at HERE
# serpent:message deferred to sub-plan E; use env.events().publish(topics, data)
# serpent:doc-title <Event instance>.publish(env)
from serpent import Env, Event, U32, contract, contractevent


@contractevent
class Transfer(Event):
    amount: U32


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        Transfer(amount=x).publish(env)  # HERE
        return x
