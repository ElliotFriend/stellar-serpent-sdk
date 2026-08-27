# serpent:reject SPT1021
# serpent:at HERE
# serpent:message only raise <ErrorEnum>.<Member> is supported; contract errors are u32 codes, not exception instances
# serpent:doc-title raise of a non-error-enum form
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        if x == U32(0):
            raise ValueError("bad")  # HERE
        return x
