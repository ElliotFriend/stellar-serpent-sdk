# serpent:reject SPT4008
# serpent:at HERE
# serpent:message error members must be declared NAME = errorcode(N)
# serpent:doc-title bare int error member
from serpent import contracterror


@contracterror
class MyError:
    BadThing = 1  # HERE
