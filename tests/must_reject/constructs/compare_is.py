# serpent:reject SPT1012
# serpent:at HERE
# serpent:message identity has no on-chain meaning; use ==
# serpent:doc-title `is` / `is not` comparison
from serpent import Bool, Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, a: U32, b: U32) -> Bool:
        return Bool(a is b)  # HERE
