# serpent:reject SPT1004
# serpent:at HERE
# serpent:message f-strings are not supported
# serpent:doc-title f-string
from serpent import Env, String, Symbol, contract


@contract
class Contract:
    def greet(self, env: Env, name: Symbol) -> String:
        return String(f"hello {name}")  # HERE
