# serpent:reject SPT1031
# serpent:at HERE
# serpent:message a contract module's top level may only contain imports, module-level chain constants, and decorated classes/helpers
# serpent:doc-title unsupported top-level statement
from serpent import Env, U32, contract

ADMIN: U32 = U32(1)  # HERE


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        return x
