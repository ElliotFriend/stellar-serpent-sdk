"""Guestbook: Can we reinvent the wheel?

A duplicate smart contract from the Ye Olde Guestbook demo project.

Original source: https://github.com/ElliotFriend/ye-olde-guestbook
"""

from serpent import (
    U32,
    Address,
    ContractUnion,
    Env,
    String,
    contract,
    contracterror,
    contracttype,
    contractunion,
    errorcode,
    variant,
)


@contracttype
class Message:
    author: Address
    ledger: U32
    title: String
    text: String


@contractunion
class DataKey(ContractUnion):
    """Storage keys"""

    Admin = variant()
    MessageCount = variant()
    Message = variant(U32)


@contracterror
class Error:
    InvalidMessage = errorcode(1)  # The provided message is malformed in some way.
    NoSuchMessage = errorcode(2)  # The message requested does not exist.
    UnauthorizedToEdit = errorcode(3)  # Address is not allowed to edit this message.
    NoDonations = errorcode(4)  # Contract has no donations to claim.


def save_message(env: Env, message: Message) -> U32:
    message_count = env.storage().instance().get(DataKey.MessageCount, U32, default=0)
    message_count += U32(1)

    env.storage().persistent().set(DataKey.Message(message_count), message)
    env.storage().instance().set(DataKey.MessageCount, message_count)

    return message_count


def get_message(env: Env, message_id: U32) -> Message:
    message_key = DataKey.Message(message_id)
    if not env.storage().persistent().has(message_key):
        raise Error.NoSuchMessage

    return env.storage().persistent().get(message_key, Message)


@contract
class GuestbookContract:
    def __init__(self, env: Env, admin: Address, title: String, text: String) -> None:
        """Initializes the guestbook with a welcome message.

        Make it a warm one for prospective signers to read.

        Args:
            admin: The address which will be the owner and administrator of the guestbook.
            title: The title or subject of the welcome message.
            text: The body or contents of the welcome message.

        Raises:
            Error.InvalidMessage: If the `title` or `text` arguments are empty.
        """
        if title == String("") or text == String(""):
            raise Error.InvalidMessage

        admin.require_auth()
        env.storage().instance().set(DataKey.Admin, admin)

        first_message = Message(
            author=admin,
            ledger=env.ledger().sequence(),
            title=title,
            text=text,
        )
        _ = save_message(env, first_message)

    def write_message(self, env: Env, author: Address, title: String, text: String) -> U32:
        """Write a message to the guestbook.

        Args:
            author: The sender of the message.
            title: The title or subject of the guestbook message.
            text: The body or contents of the guestbook message.

        Returns:
            The message ID for the newly written message.

        Raises:
            Error.InvalidMessage: If the `title` or `text` arguments are empty.
        """
        if title == String("") or text == String(""):
            raise Error.InvalidMessage
        author.require_auth()

        new_message = Message(
            author=author,
            ledger=env.ledger().sequence(),
            title=title,
            text=text,
        )

        return save_message(env, new_message)

    def edit_message(self, env: Env, message_id: U32, title: String, text: String) -> None:
        """Edit a specified message in the guestbook.

        Args:
            message_id: The ID of the message to edit.
            title: The new title, or empty to keep the current one.
            text: The new body, or empty to keep the current one.

        Raises:
            Error.InvalidMessage: If both the `title` AND the `text` arguments
                are empty.
            Error.NoSuchMessage: If no message has that ID.
            AuthorizationFailed: If the original author has not authorized the
                call.
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

        env.storage().persistent().set(DataKey.Message(message_id), mod_message)

    def read_message(self, env: Env, message_id: U32) -> Message:
        """Read a specified message from the guestbook.

        Args:
            message_id: The ID of the message to retrieve.

        Returns:
            The message as recorded in the smart contract's storage entry.

        Raises:
            Error.NoSuchMessage: If no message has that ID.
        """
        return get_message(env, message_id)

    def read_latest(self, env: Env) -> Message:
        """Read the latest message to be sent to the guestbook.

        Returns:
            The latest message as recorded in the smart contract's storage
            entries.
        """
        latest_id = env.storage().instance().get(DataKey.MessageCount, U32)
        return get_message(env, latest_id)
