# serpent:reject SPT4026
# serpent:at HERE
# serpent:message the `topic` marker only means something on a @contractevent field
# serpent:doc-title topic marker on a @contracttype field
from serpent import Annotated, Env, U32, contract, contracttype, topic


@contracttype
class Point:
    x: Annotated[U32, topic]  # HERE


@contract
class Contract:
    def compute(self, env: Env, x: U32) -> U32:
        return x
