import asyncio
import inspect
from typing import List, Tuple, Callable, Set

from lightbus import Parameter, EventMessage
from lightbus.client.bus_client import logger
from lightbus.client.subclients.base import BaseSubClient
from lightbus.client.utilities import validate_event_or_rpc_name
from lightbus.client.validator import validate_outgoing, validate_incoming
from lightbus.exceptions import (
    UnknownApi,
    EventNotFound,
    InvalidEventArguments,
    InvalidEventListener,
)
from lightbus.log import L, Bold
from lightbus.client import commands
from lightbus.client.commands import SendEventCommand, AcknowledgeEventCommand, ConsumeEventsCommand
from lightbus.utilities.async_tools import run_user_provided_callable
from lightbus.utilities.casting import cast_to_signature
from lightbus.utilities.deforming import deform_to_bus
from lightbus.utilities.singledispatch import singledispatchmethod


class EventClient(BaseSubClient):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._event_listeners: Set[str] = set()

    async def fire_event(self, api_name, name, kwargs: dict = None, options: dict = None):
        await self.lazy_load_now()

        kwargs = kwargs or {}
        try:
            api = self.api_registry.get(api_name)
        except UnknownApi:
            raise UnknownApi(
                "Lightbus tried to fire the event {api_name}.{name}, but no API named {api_name} was found in the "
                "registry. An API being in the registry implies you are an authority on that API. Therefore, "
                "Lightbus requires the API to be in the registry as it is a bad idea to fire "
                "events on behalf of remote APIs. However, this could also be caused by a typo in the "
                "API name or event name, or be because the API class has not been "
                "registered using bus.client.register_api(). ".format(**locals())
            )

        validate_event_or_rpc_name(api_name, "event", name)

        try:
            event = api.get_event(name)
        except EventNotFound:
            raise EventNotFound(
                "Lightbus tried to fire the event {api_name}.{name}, but the API {api_name} does not "
                "seem to contain an event named {name}. You may need to define the event, you "
                "may also be using the incorrect API. Also check for typos.".format(**locals())
            )

        parameter_names = {p.name if isinstance(p, Parameter) else p for p in event.parameters}

        if set(kwargs.keys()) != parameter_names:
            raise InvalidEventArguments(
                "Invalid event arguments supplied when firing event. Attempted to fire event with "
                "{} arguments: {}. Event expected {}: {}".format(
                    len(kwargs),
                    sorted(kwargs.keys()),
                    len(event.parameters),
                    sorted(parameter_names),
                )
            )

        kwargs = deform_to_bus(kwargs)
        event_message = EventMessage(
            api_name=api.meta.name, event_name=name, kwargs=kwargs, version=api.meta.version
        )

        validate_outgoing(self.config, self.schema, event_message)

        await self._execute_hook("before_event_sent", event_message=event_message)
        logger.info(L("📤  Sending event {}.{}".format(Bold(api_name), Bold(name))))

        await self.send_to_event_transports(
            SendEventCommand(message=event_message, options=options)
        ).wait()

        await self._execute_hook("after_event_sent", event_message=event_message)

    async def listen(
        self,
        events: List[Tuple[str, str]],
        listener: Callable,
        listener_name: str,
        options: dict = None,
    ):
        sanity_check_listener(listener)

        if listener_name in self._event_listeners:
            # TODO: Custom exception class
            raise Exception(f"Listener with name {listener_name} already registered")

        for api_name, name in events:
            validate_event_or_rpc_name(api_name, "event", name)

        queue = asyncio.Queue()

        async def listener():
            while True:
                await self._on_message(
                    event_message=await queue.get(), listener=listener, options=options
                )

        # TODO: Only do when server starts up
        await self.producer.send(
            ConsumeEventsCommand(
                events=events, destination_queue=queue, listener_name=listener_name
            )
        ).wait()

        self._event_listeners.add(listener_name)

    async def _on_message(self, event_message: EventMessage, listener: Callable, options: dict):

        # TODO: Check events match those requested
        # TODO: Support event name of '*', but transports should raise
        # TODO: an exception if it is not supported.
        logger.info(
            L(
                "📩  Received event {}.{} with ID {}".format(
                    Bold(event_message.api_name), Bold(event_message.event_name), event_message.id
                )
            )
        )

        validate_incoming(self.config, self.schema, event_message)

        await self.bus_client._execute_hook("before_event_execution", event_message=event_message)

        if self.config.api(event_message.api_name).cast_values:
            parameters = cast_to_signature(parameters=event_message.kwargs, callable=listener)
        else:
            parameters = event_message.kwargs

        # Call the listener.
        # Pass the event message as a positional argument,
        # thereby allowing listeners to have flexibility in the argument names.
        # (And therefore allowing listeners to use the `event` parameter themselves)
        await run_user_provided_callable(
            listener, args=[event_message], kwargs=parameters, error_queue=self.error_queue
        )

        # Acknowledge the successfully processed message
        await self.send_to_event_transports(
            AcknowledgeEventCommand(message=event_message, options=options)
        ).wait()

        await self.bus_client._execute_hook("after_event_execution", event_message=event_message)

    @singledispatchmethod
    async def handle(self, command):
        raise NotImplementedError(f"Did not recognise command {command.__name__}")

    @handle.register
    async def handle_receive_event(self, command: commands.ReceiveEventCommand):
        if command.listener_name not in self._event_listeners:
            logger.debug(
                f"Received an event for unknown listener '%s'. Had: %s",
                command.listener_name,
                self._event_listeners.keys(),
            )
            return

        listener = self._event_listeners[command.listener_name]
        await listener.incoming_events.put(command.message)


def sanity_check_listener(listener):
    if not callable(listener):
        raise InvalidEventListener(
            f"The specified event listener {listener} is not callable. Perhaps you called the function rather "
            f"than passing the function itself?"
        )

    total_positional_args = 0
    has_variable_positional_args = False  # Eg: *args
    for parameter in inspect.signature(listener).parameters.values():
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            total_positional_args += 1
        elif parameter.kind == inspect.Parameter.VAR_POSITIONAL:
            has_variable_positional_args = True

    if has_variable_positional_args:
        return

    if not total_positional_args:
        raise InvalidEventListener(
            f"The specified event listener {listener} must take at one positional argument. "
            f"This will be the event message. For example: "
            f"my_listener(event_message, other, ...)"
        )