# serpent:reject SPT7005
# serpent:at HERE
# serpent:message recursive and mutually-recursive calls are not supported
# serpent:doc-title call-graph cycle among module-level helpers
from serpent import Env, U32, contract


def _even(env: Env, x: U32) -> U32:
    return _odd(env, x)


def _odd(env: Env, x: U32) -> U32:
    return _even(env, x)  # HERE


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        return _even(env, x)
