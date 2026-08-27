# serpent:reject SPT5001
# serpent:at HERE
# serpent:message name is too long (> 30) or uses characters outside [a-zA-Z0-9_]
# serpent:doc-title over-long constructor parameter name
from serpent import Env, U32, contract


@contract
class Contract:
    def __init__(self, env: Env, aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa: U32) -> None:  # HERE
        pass
