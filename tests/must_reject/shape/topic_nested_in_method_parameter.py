# serpent:reject SPT4026
# serpent:at HERE
# serpent:message the `topic` marker only means something on a @contractevent field
# serpent:doc-title topic marker nested inside a contract method parameter annotation
from serpent import Annotated, Env, U32, contract, topic


@contract
class Contract:
    # The marker is not on the WHOLE annotation: stripping the outer `| None`
    # would never find it, so it is refused rather than silently read as data.
    def compute(self, env: Env, x: Annotated[U32, topic] | None) -> U32:  # HERE
        return U32(0)
