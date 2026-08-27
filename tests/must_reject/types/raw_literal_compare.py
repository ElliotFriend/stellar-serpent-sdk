# serpent:reject SPT3016
# serpent:at HERE
# serpent:message compare against the chain type's constructor, e.g. Symbol('abc'), not the raw literal
# serpent:doc-title comparing a chain value to a raw str/bytes literal
from serpent import Bool, Env, Symbol, contract


@contract
class Contract:
    def compute(self, env: Env, s: Symbol) -> Bool:
        return Bool(s == "abc")  # HERE
