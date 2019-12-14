import logging
from datetime import datetime
from typing import Sequence, Tuple, List, Dict, TypeVar, Type, AsyncGenerator, TYPE_CHECKING

from lightbus.api import Api
from lightbus.exceptions import NothingToListenFor
from lightbus.message import RpcMessage, EventMessage, ResultMessage
from lightbus.serializers import ByFieldMessageSerializer, ByFieldMessageDeserializer
from lightbus.utilities.config import make_from_config_structure

if TYPE_CHECKING:
    # pylint: disable=unused-import,cyclic-import
    from lightbus.config import Config

T = TypeVar("T")
logger = logging.getLogger(__name__)


class TransportMetaclass(type):
    def __new__(mcs, name, bases, attrs, **kwds):
        cls = super().__new__(mcs, name, bases, attrs)
        if not hasattr(cls, f"{name}Config") and hasattr(cls, "from_config"):
            cls.Config = make_from_config_structure(
                class_name=name, from_config_method=cls.from_config
            )
        return cls


class Transport(metaclass=TransportMetaclass):
    @classmethod
    def from_config(cls: Type[T], config: "Config") -> T:
        return cls()

    async def open(self):
        """Setup transport prior to use


        Can be used for opening connections, initialisation, etc.
        """
        pass

    async def close(self):
        """Cleanup prior to termination

        Can be used for closing connections etc.
        """
        pass


class RpcTransport(Transport):
    """Implement the sending and receiving of RPC calls"""

    async def call_rpc(self, rpc_message: RpcMessage, options: dict):
        """Publish a call to a remote procedure"""
        raise NotImplementedError()

    async def consume_rpcs(self, apis: Sequence[Api]) -> Sequence[RpcMessage]:
        """Consume RPC calls for the given API"""
        raise NotImplementedError()


class ResultTransport(Transport):
    """Implement the send & receiving of results

    """

    def get_return_path(self, rpc_message: RpcMessage) -> str:
        raise NotImplementedError()

    async def send_result(
        self, rpc_message: RpcMessage, result_message: ResultMessage, return_path: str
    ):
        """Send a result back to the caller

        Args:
            rpc_message (): The original message received from the client
            result_message (): The result message to be sent back to the client
            return_path (str): The string indicating where to send the result.
                As generated by :ref:`get_return_path()`.
        """
        raise NotImplementedError()

    async def receive_result(
        self, rpc_message: RpcMessage, return_path: str, options: dict
    ) -> ResultMessage:
        """Receive the result for the given message

        Args:
            rpc_message (): The original message sent to the server
            return_path (str): The string indicated where to receive the result.
                As generated by :ref:`get_return_path()`.
            options (dict): Dictionary of options specific to this particular backend
        """
        raise NotImplementedError()


class EventTransport(Transport):
    """ Implement the sending/consumption of events over a given transport.
    """

    def __init__(
        self,
        serializer=ByFieldMessageSerializer(),
        deserializer=ByFieldMessageDeserializer(EventMessage),
    ):
        self.serializer = serializer
        self.deserializer = deserializer

    async def send_event(self, event_message: EventMessage, options: dict):
        """Publish an event"""
        raise NotImplementedError()

    async def consume(
        self, listen_for: List[Tuple[str, str]], listener_name: str, **kwargs
    ) -> AsyncGenerator[List[EventMessage], None]:
        """Consume messages for the given APIs

        Examples:

            Consuming events::

                listen_for = [
                    ('mycompany.auth', 'user_created'),
                    ('mycompany.auth', 'user_updated'),
                ]
                async with event_transport.consume(listen_for) as event_message:
                    print(event_message)

        """
        raise NotImplementedError(
            f"Event transport {self.__class__.__name__} does not support listening for events"
        )

    async def acknowledge(self, *event_messages):
        """Acknowledge that one or more events were successfully processed"""
        pass

    async def history(
        self,
        api_name,
        event_name,
        start: datetime = None,
        stop: datetime = None,
        start_inclusive: bool = True,
    ) -> AsyncGenerator[EventMessage, None]:
        """Return EventMessages for the given api/event names during the (optionally) given date range.

        Should return newest messages first
        """
        raise NotImplementedError(
            f"Event transport {self.__class__.__name__} does not support event history."
        )

    def _sanity_check_listen_for(self, listen_for):
        """Utility method to sanity check the `listen_for` parameter.

        Call at the start of your consume() implementation.
        """
        if not listen_for:
            raise NothingToListenFor(
                "EventTransport.consume() was called without providing anything "
                'to listen for in the "listen_for" argument.'
            )


class SchemaTransport(Transport):
    """ Implement sharing of lightbus API schemas
    """

    async def store(self, api_name: str, schema: Dict, ttl_seconds: int):
        """Store a schema for the given API"""
        raise NotImplementedError()

    async def ping(self, api_name: str, schema: Dict, ttl_seconds: int):
        """Keep alive a schema already stored via store()

        The defaults to simply calling store() on the assumption that this
        will cause the ttl to be updated. Backends may choose to
        customise this logic.
        """
        await self.store(api_name, schema, ttl_seconds)

    async def load(self) -> Dict[str, Dict]:
        """Load the schema for all APIs

        Should return a mapping of API names to schemas
        """
        raise NotImplementedError()
