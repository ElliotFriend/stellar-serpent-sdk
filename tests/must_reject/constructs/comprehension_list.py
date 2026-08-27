# serpent:reject SPT1003
# serpent:at HERE
# serpent:message comprehensions are not supported
# serpent:doc-title list comprehension
from serpent import U32, Env, Vec, contract


@contract
class Contract:
    def totals(self, env: Env, v: Vec[U32]) -> Vec[U32]:
        return Vec(U32, [x + U32(1) for x in v])  # HERE
