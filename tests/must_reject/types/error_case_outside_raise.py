# serpent:reject SPT3002
# serpent:at HERE
# serpent:message an error case is not a value; it may only appear in raise <ErrorEnum>.<Member>
# serpent:doc-title error case referenced outside a raise statement
from serpent import Env, U32, contract, contracterror, errorcode


@contracterror
class Err:
    NotFound = errorcode(1)


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        y = Err.NotFound  # HERE
        return x
