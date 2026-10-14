"""Record legal lifecycle transitions and exact, immutable replay snapshots."""

from collections import deque

from .schemas import EventType, SimulationEvent


class EventRecorder:
    def __init__(self, cashiers: int) -> None:
        if isinstance(cashiers, bool) or not isinstance(cashiers, int) or cashiers < 1:
            raise ValueError("cashiers must be a positive integer")
        self._capacity = cashiers
        self._events: list[SimulationEvent] = []
        self._waiting: deque[int] = deque()
        self._arrived: set[int] = set()
        self._active: dict[int, int] = {}

    @property
    def events(self) -> tuple[SimulationEvent, ...]:
        return tuple(self._events)

    def record(
        self,
        event_type: EventType,
        *,
        timestamp: float,
        customer_id: int,
        cashier_id: int | None = None,
    ) -> SimulationEvent:
        if self._events and timestamp < self._events[-1].timestamp:
            raise ValueError("Event timestamps cannot go backwards")
        queue_length = len(self._waiting)
        if event_type == "customer_arrived":
            if customer_id in self._arrived:
                raise ValueError("Customer has already arrived")
            queue_length += 1
        elif event_type == "customer_started":
            if cashier_id in self._active:
                raise ValueError("Cashier is busy")
            if not self._waiting or self._waiting[0] != customer_id:
                raise ValueError("Service start violates FIFO order")
            queue_length -= 1
        elif event_type == "customer_finished":
            if cashier_id not in self._active or self._active[cashier_id] != customer_id:
                raise ValueError("Completion does not match an active service")
        else:
            raise ValueError("Unknown event type")
        if cashier_id is not None and cashier_id > self._capacity:
            raise ValueError("Cashier ID exceeds capacity")
        # Validate every field before mutating any recorder state.
        event = SimulationEvent(
            sequence=len(self._events),
            timestamp=timestamp,
            event_type=event_type,
            customer_id=customer_id,
            cashier_id=cashier_id,
            queue_length_after_event=queue_length,
        )
        if event_type == "customer_arrived":
            self._arrived.add(customer_id)
            self._waiting.append(customer_id)
        elif event_type == "customer_started":
            assert cashier_id is not None
            self._waiting.popleft()
            self._active[cashier_id] = customer_id
        else:
            assert cashier_id is not None
            del self._active[cashier_id]
        self._events.append(event)
        return event
