"""Guestbook: Can we reinvent the wheel?
"""

from serpent import Address, Bool, Env, Symbol, String, U32, contract, contracttype, contracterror, errorcode

@contracttype
class Message:
    author: Address
    ledger: U32
    title: String
    text: String

@contracttype
class MessageKey:
    message: U32

ADMIN = Symbol("ADMIN")
COUNT = Symbol("COUNT")

@contracterror
class Error:
    InvalidMessage = errorcode(1) # The provided message is malformed in some way.
    NoSuchMessage = errorcode(2) # The message requested does not exist.
    UnauthorizedToEdit = errorcode(3) # Address is not allowed to edit this message.
    NoDonations = errorcode(4) # Contract has no donations to claim.

def save_message(env: Env, message: Message) -> U32:
    message_count = env.storage().instance().get(COUNT, U32, default=0)
    message_count += U32(1)

    env.storage().persistent().set(MessageKey(message=message_count), message)
    env.storage().instance().set(COUNT, message_count)

    return message_count

def get_message(env: Env, message_id: U32) -> Message:
    message_key = MessageKey(message=message_id)
    if env.storage().persistent().has(message_key) != Bool(True):
        raise Error.NoSuchMessage

    return env.storage().persistent().get(message_key, Message)

@contract
class GuestbookContract:
    def __init__(self, env: Env, admin: Address, title: String, text: String) -> None:
        """Initializes the guestbook with a warm welcome message for prospective
        signers to read.

        # Arguments
        * `admin` - The address which will be the owner and administrator of the
        guestbook.
        * `title` - The title or subject of the welcome message.
        * `text` - The body or contents of the welcome message.

        # Panics
        * If the `title` argument is empty or missing.
        * If the `text` argument is empty or missing.
        """
        if title == String("") or text == String(""):
            raise Error.InvalidMessage

        env.storage().instance().set(ADMIN, admin)

        first_message = Message(
            author=admin,
            ledger=env.ledger().sequence(),
            title=title,
            text=text,
        )
        _ = save_message(env, first_message)

    def write_message(self, env: Env, author: Address, title: String, text: String) -> U32:
        """Write a message to the guestbook.

        # Arguments
        * `author` - The sender of the message.
        * `title` - The title or subject of the guestbook message.
        * `text` - The body or contents of the guestbook message.

        # Panics
        * If the `title` argument is empty or missing.
        * If the `text` argument is empty or missing.
        """
        if title == String("") or text == String(""):
            raise Error.InvalidMessage

        new_message = Message(
            author=author,
            ledger=env.ledger().sequence(),
            title=title,
            text=text,
        )

        return save_message(env, new_message)

    def edit_message(self, env: Env, message_id: U32, title: String, text: String) -> None:
        """Edit a specified message in the guestbook.

        # Arguments
        * `message_id` - The ID number of the message to edit.
        * `title` - The title or subject of the guestbook message.
        * `text` - The body or contents of the guestbook message.

        # Panics
        * If both the `title` AND `text` arguments are empty or missing.
        * If there is no authorization from the original message author.
        """
        if title == String("") and text == String(""):
            raise Error.InvalidMessage

        # retrieve the message from storage and authenticate
        message = get_message(env, message_id)
        message.author.require_auth()

        mod_message = Message(
            author=message.author,
            ledger=env.ledger().sequence(),
            title=title if title != String("") else message.title,
            text=text if text != String("") else message.text,
        )

        env.storage().persistent().set(MessageKey(message=message_id), mod_message)


    def read_message(self, env: Env, message_id: U32) -> Message:
        """Read a specified message from the guestbook.

        # Arguments
        * `message_id` - The ID number of the message to retrieve.

        # Panics
        * If the message ID is not associated with a message.
        """
        return get_message(env, message_id)

    def read_latest(self, env: Env) -> Message:
        """Read the latest message to be sent to the guestbook."""
        latest_id = env.storage().instance().get(COUNT, U32)
        return get_message(env, latest_id)
