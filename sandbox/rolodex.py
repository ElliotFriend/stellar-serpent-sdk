"""Rolodex: a very simple address<->name mapper contract
"""

from serpent import Address, Bool, Env, String, contract, contracterror, contracttype, errorcode

@contracterror
class Error:
    AddressExists = errorcode(1)
    NameExists = errorcode(2)
    MissingAddress = errorcode(3)
    MissingName = errorcode(4)
    CannotChangeAdminName = errorcode(5)
    AddressAlreadyMatches = errorcode(6)
    NameAlreadyMatches = errorcode(7)
    InvalidAddress = errorcode(8)
    InvalidName = errorcode(9)


@contracttype
class NameKey:
    """The Name(Address) storage key: one persistent entry per address. value stored is the `String` name."""
    address: Address

@contracttype
class AddyKey:
    """The Addy(String) storage key: one persistent entry per name. value stored is the `Address`"""
    name: String

@contract
class RolodexContract:
    def __init__(self, env: Env, admin: Address) -> None:
        admin.require_auth()

        # create and store the Name(Address) = "Admin" key
        # in persistent storage, so that the admin address can't make more entries for itself.
        name_key = NameKey(address=admin)
        env.storage().persistent().set(name_key, String("Admin"))

        # create and store the Address(String) = Address key
        address_key = AddyKey(name=String("Admin"))
        env.storage().instance().set(address_key, admin)

    def add_record(self, env: Env, name: String, addy: Address) -> None:
        addy.require_auth()

        if name == String("Admin"):
            raise Error.InvalidName

        # check for an existing name
        if env.storage().persistent().has(AddyKey(name=name)):
            raise Error.NameExists

        # check for an existing address
        if env.storage().persistent().has(NameKey(address=addy)):
            raise Error.AddressExists

        # Set the Name(Address) -> String entry
        env.storage().persistent().set(NameKey(address=addy), name)
        # Set the Addy(String) -> Address entry
        env.storage().persistent().set(AddyKey(name=name), addy)

    def get_name_for_address(self, env: Env, addy: Address) -> String:
        key = NameKey(address=addy)

        if env.storage().persistent().has(key):
            return env.storage().persistent().get(key, String)
        else:
            raise Error.MissingAddress

    def set_name_for_address(self, env: Env, addy: Address, new_name: String) -> None:
        # get auth for the address connected to the entry
        addy.require_auth()

        addy_key = NameKey(address=addy)
        new_name_key = AddyKey(name=new_name)

        # hehe, nice try!
        if new_name == String("Admin"):
            raise Error.InvalidName

        # make sure the address already has an entry
        if env.storage().persistent().has(addy_key) == Bool(False):
            raise Error.MissingAddress

        # watch out for "stealing" an existing name
        if env.storage().persistent().has(new_name_key):
            raise Error.NameExists

        # no need to proceed if it's already set as desired
        old_name = env.storage().persistent().get(addy_key, String)
        if old_name == new_name:
            raise Error.NameAlreadyMatches

        if old_name == String("Admin"):
            raise Error.CannotChangeAdminName

        # Update the Name(Address) -> String entry
        env.storage().persistent().set(addy_key, new_name)
        # Set the (new) Addy(String) -> Address entry
        env.storage().persistent().set(AddyKey(name=new_name), addy)
        # Delete the (old) Addy(String) -> Address entry
        env.storage().persistent().del_(AddyKey(name=old_name))


    def get_address_for_name(self, env: Env, name: String) -> Address:
        key = AddyKey(name=name)

        if env.storage().persistent().has(key):
            return env.storage().persistent().get(key, Address)
        else:
            raise Error.MissingAddress

    def set_address_for_name(self, env: Env, name: String, new_addy: Address) -> None:
        new_addy_key = NameKey(address=new_addy)
        name_key = AddyKey(name=name)

        # admin should use the `set_admin_address` function
        if name == String("Admin"):
            raise Error.InvalidName

        # make sure the name already has an entry
        if env.storage().persistent().has(name_key) == Bool(False):
            raise Error.MissingName

        # watch out for "stealing" an existing address
        if env.storage().persistent().has(new_addy_key):
            raise Error.AddressExists

        # no need to proceed if it's already set as desired
        old_addy = env.storage().persistent().get(name_key, Address)
        if old_addy == new_addy:
            raise Error.AddressAlreadyMatches

        # (FINALLY) require_auth from the currently set address
        old_addy.require_auth()

        # Update the Addy(String) -> Address entry
        env.storage().persistent().set(name_key, new_addy)
        # Set the (new) Name(Address) -> String entry
        env.storage().persistent().set(NameKey(address=new_addy), name)
        # Delete the (old) Name(Address) -> String entry
        env.storage().persistent().del_(NameKey(address=old_addy))

    def get_admin_address(self, env: Env) -> Address:
        admin_key = AddyKey(name=String("Admin"))
        return env.storage().instance().get(admin_key, Address)

    def set_admin_address(self, env: Env, new_admin: Address) -> None:
        admin_key = AddyKey(name=String("Admin"))
        old_admin = env.storage().instance().get(admin_key, Address)
        old_admin.require_auth()

        # Set the Addy(String) -> Address instance entry
        env.storage().instance().set(admin_key, new_admin)
        # Set the (new) Name(Address) -> String entry
        env.storage().persistent().set(NameKey(address=new_admin), String("Admin"))
        # Delete the (old) Name(Address) -> String entry
        env.storage().persistent().del_(NameKey(address=old_admin))
