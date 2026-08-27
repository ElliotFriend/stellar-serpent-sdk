# serpent:reject SPT1011
# serpent:at HERE
# serpent:message use Map.has(k) or Vec.first_index_of(v) instead of `in`
# serpent:doc-title `in` / `not in` comparison
from serpent import Bool, Env, U32, Vec, contract


@contract
class Contract:
    def compute(self, env: Env, v: Vec[U32], x: U32) -> Bool:
        return Bool(x not in v)  # HERE
