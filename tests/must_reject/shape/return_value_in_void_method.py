# serpent:reject SPT4017
# serpent:at HERE
# serpent:message a method returning None may not return a value
# serpent:doc-title return with a value in a -> None method
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> None:
        return x  # HERE
