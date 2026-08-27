# serpent:reject SPT1024
# serpent:at HERE
# serpent:message structural pattern matching is not supported
# serpent:doc-title match statement
from serpent import Env, U32, contract


@contract
class Contract:
    def classify(self, env: Env, x: U32) -> U32:
        match x:  # HERE
            case _:
                return x
