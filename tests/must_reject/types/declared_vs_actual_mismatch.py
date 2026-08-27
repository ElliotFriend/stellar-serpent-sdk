# serpent:reject SPT3018
# serpent:at HERE
# serpent:message value's type does not match the declared/expected type
# serpent:doc-title annotated assignment disagreeing with its value's type
from serpent import Bool, Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env) -> U32:
        x: U32 = Bool(True)  # HERE
        return x
