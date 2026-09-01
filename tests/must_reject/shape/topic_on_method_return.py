# serpent:reject SPT4026
# serpent:at HERE
# serpent:message the `topic` marker only means something on a @contractevent field
# serpent:doc-title topic marker on a contract method return type
from serpent import Annotated, Env, U32, contract, topic


@contract
class Contract:
    def compute(self, env: Env) -> Annotated[U32, topic]:  # HERE
        return U32(0)
