# serpent:reject SPT3012
# serpent:at HERE
# serpent:message and/or are restricted to Bool-typed and comparison operands
# serpent:doc-title `and`/`or` with a non-Bool operand
from serpent import Bool, Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32, flag: Bool) -> Bool:
        return Bool(x and flag)  # HERE
