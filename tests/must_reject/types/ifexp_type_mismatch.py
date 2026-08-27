# serpent:reject SPT3010
# serpent:at HERE
# serpent:message both branches of a conditional expression must have the same type
# serpent:doc-title conditional expression with mismatched arm types
from serpent import Bool, Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, flag: Bool, a: U32) -> U32:
        return a if flag else Bool(True)  # HERE
