# serpent:reject SPT3019
# serpent:at HERE
# serpent:message the first event topic must be a Symbol naming the event
# serpent:doc-title prefixless event whose first topic field is not a Symbol
from serpent import Annotated, Env, Event, U32, contract, contractevent, topic


@contractevent(topics=())
class Moved(Event):
    who: Annotated[U32, topic]  # HERE
    amount: U32


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        return x
