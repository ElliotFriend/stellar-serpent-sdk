# serpent:reject SPT4015
# serpent:at HERE
# serpent:message every top-level class needs exactly one of @contract/@contracttype/@contracterror/@contractevent/@contractunion/@contractenum
# serpent:doc-title undecorated top-level class
from serpent import Env, U32, contract


class Helper:  # HERE
    x: U32


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        return x
