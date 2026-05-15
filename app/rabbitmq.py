import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import aio_pika
from aio_pika import ExchangeType, Message, DeliveryMode

logger = logging.getLogger(__name__)


class RabbitMQBroker:
    def __init__(self) -> None:
        self.rabbitmq_url = os.getenv(
            "RABBITMQ_URL",
            "amqp://ridefleet:ridefleet@rabbitmq:5672/",
        )
        self.exchange_name = os.getenv(
            "RABBITMQ_EXCHANGE",
            "ridefleet.core.events",
        )
        self.connection: aio_pika.RobustConnection | None = None
        self.channel: aio_pika.RobustChannel | None = None
        self.exchange: aio_pika.RobustExchange | None = None

    async def connect(self) -> None:
        self.connection = await aio_pika.connect_robust(self.rabbitmq_url)
        self.channel = await self.connection.channel()

        self.exchange = await self.channel.declare_exchange(
            self.exchange_name,
            ExchangeType.TOPIC,
            durable=True,
        )

        await self._declare_queues()

        logger.info("RabbitMQ conectado com sucesso")

    async def close(self) -> None:
        if self.connection:
            await self.connection.close()
            logger.info("RabbitMQ desconectado")

    async def _declare_queues(self) -> None:
        if not self.channel or not self.exchange:
            raise RuntimeError("RabbitMQ ainda não foi conectado")

        queues = {
            "ridefleet.audit": "#",
            "ridefleet.observability": "#",
            "ridefleet.groups.ride_created": "ride_created",
            "ridefleet.groups.status": "ride_status_changed",
            "ridefleet.proposals": "proposal_submitted",
            "ridefleet.locks": "lock_event",
            "ridefleet.compensations": "compensation_triggered",
            "ridefleet.auction.requests": "auction_request",
        }

        for queue_name, routing_key in queues.items():
            queue = await self.channel.declare_queue(queue_name, durable=True)
            await queue.bind(self.exchange, routing_key=routing_key)

    async def publish_event(
        self,
        event_type: str,
        ride_id: str | None,
        service_id: str,
        logical_timestamp: int,
        payload: dict[str, Any],
    ) -> None:
        if not self.exchange:
            raise RuntimeError("RabbitMQ não está conectado")

        message_body = {
            "eventType": event_type,
            "rideId": ride_id,
            "serviceId": service_id,
            "logicalTimestamp": logical_timestamp,
            "wallClockTime": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }

        message = Message(
            body=json.dumps(message_body).encode("utf-8"),
            content_type="application/json",
            delivery_mode=DeliveryMode.PERSISTENT,
        )

        await self.exchange.publish(
            message,
            routing_key=event_type,
        )
        logger.info(
            "Evento publicado: tipo='%s' corrida=%s serviceId='%s' ts=%d",
            event_type,
            ride_id,
            service_id,
            logical_timestamp,
        )


rabbitmq_broker = RabbitMQBroker()