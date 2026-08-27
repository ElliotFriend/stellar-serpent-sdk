# serpent:reject SPT5002
# serpent:at HERE
# serpent:message type name is too long (> 60) or uses characters outside [a-zA-Z0-9_]
# serpent:doc-title non-ASCII struct type name
from serpent import Env, U32, contract, contracttype


@contracttype
class éTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTT:  # HERE
    field: U32


@contract
class Contract:
    def act(self, env: Env) -> U32:
        return U32(0)
