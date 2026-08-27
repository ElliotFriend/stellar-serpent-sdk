# serpent:reject SPT7001
# serpent:at HERE
# serpent:message not every path returns a value
# serpent:doc-title method with a path that does not return
from serpent import Bool, Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, flag: Bool, x: U32) -> U32:  # HERE
        if flag:
            return x
