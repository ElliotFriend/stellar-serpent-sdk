# serpent:reject SPT5005
# serpent:at HERE
# serpent:message an exported method may have at most 32 parameters
# serpent:doc-title over-many export parameters
from serpent import Env, U32, contract


@contract
class Contract:
    def act(self, env: Env, p0: U32, p1: U32, p2: U32, p3: U32, p4: U32, p5: U32, p6: U32, p7: U32, p8: U32, p9: U32, p10: U32, p11: U32, p12: U32, p13: U32, p14: U32, p15: U32, p16: U32, p17: U32, p18: U32, p19: U32, p20: U32, p21: U32, p22: U32, p23: U32, p24: U32, p25: U32, p26: U32, p27: U32, p28: U32, p29: U32, p30: U32, p31: U32, p32: U32) -> U32:  # HERE
        return p0
