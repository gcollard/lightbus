import asyncio
from typing import Optional, Callable

from lightbus.client.utilities import queue_exception_checker
from lightbus.utilities.async_tools import cancel

# Was handler
class InternalConsumer:
    """Handle commands coming from the transport invoker

    These are commands which are being sent by the client for
    consumption by the transport
    """

    def __init__(self, queue: asyncio.Queue, error_queue: asyncio.Queue):
        self._consumer_task: Optional[asyncio.Task] = None
        self._running_commands = set()
        self._ready = asyncio.Event()
        self.queue = queue
        self.error_queue = error_queue

    def start(self, handler: Callable):
        """Set the handler function and start the invoker

        Use `stop()` to shutdown the invoker.
        """
        self._consumer_task = asyncio.ensure_future(self._consumer_loop(self.queue, handler))
        self._consumer_task.add_done_callback(queue_exception_checker(self.error_queue))
        self._running_commands = set()

    async def stop(self, wait_seconds=1):
        """Shutdown the invoker and cancel any currently running tasks

        The shutdown procedure will stop any new tasks for being created,
        then wait `wait_seconds` to allow any running tasks to finish normally.
        Tasks still running after this period will be cancelled.
        """

        # Stop consuming commands from the queue
        # (this will also stop *new* tasks being created)
        if self._consumer_task is not None:
            await cancel(self._consumer_task)
            self._consumer_task = None
            self._ready = asyncio.Event()

        for _ in range(100):
            if self._running_commands:
                await asyncio.sleep(wait_seconds / 100)

        # Now we have stopped consuming commands we can
        # cancel any running tasks safe in the knowledge that
        # no new tasks will get created
        await cancel(*self._running_commands)
        self._running_commands = set()

    async def _consumer_loop(self, queue, handler):
        """Continually fetch commands from the queue and handle them"""
        self._ready.set()

        while True:
            on_done: asyncio.Event
            command, on_done = await queue.get()
            self.handle_in_background(queue, handler, command, on_done)

    def handle_in_background(self, queue: asyncio.Queue, handler, command, on_done: asyncio.Event):
        """Handle a received command by calling self.handle

        This execution happens in the background.
        """

        def when_task_finished(fut: asyncio.Future):
            self._running_commands.remove(fut)
            queue.task_done()
            on_done.set()

        background_call_task = asyncio.ensure_future(handler(command))
        self._running_commands.add(background_call_task)
        background_call_task.add_done_callback(queue_exception_checker(self.error_queue))
        background_call_task.add_done_callback(when_task_finished)