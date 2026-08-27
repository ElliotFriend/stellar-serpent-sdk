# serpent:reject SPT4007
# serpent:at HERE
# serpent:message @contract methods may not be static or class methods
# serpent:doc-title staticmethod on a @contract class
from serpent import Env, U32, contract


@contract
class Contract:
    @staticmethod
    def compute(env: Env, x: U32) -> U32:  # HERE
        return x
