# serpent:reject SPT1015
# serpent:at HERE
# serpent:message there is no python list/dict/set on chain; build a Vec(T, [...]) or Map(K, V, [...])
# serpent:doc-title list display outside Vec(...)
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, a: U32, b: U32) -> U32:
        items = [a, b]  # HERE
        return a
