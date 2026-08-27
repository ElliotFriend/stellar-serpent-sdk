# serpent:reject SPT1028
# serpent:at HERE
# serpent:message a non-void expression cannot be a statement on its own; assign it or discard it explicitly
# serpent:doc-title discarded non-void expression statement
from serpent import Env, U32, contract


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        x + U32(1)  # HERE
        return x
