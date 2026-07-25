from typing import Optional
from backend.brokers.base import BaseBroker
from backend.utils.logger import get_logger

logger = get_logger(__name__)

class BrokerFactory:
    _instance = None
    _broker_instance: Optional[BaseBroker] = None
    
    @classmethod
    def get_broker(cls, broker_type: str = "MT5") -> BaseBroker:
        """
        Factory method to get the active broker instance.
        Currently defaults to MT5, but allows expansion for other platforms.
        """
        if cls._broker_instance is None:
            if broker_type.upper() == "MT5":
                from backend.brokers.mt5_broker import MT5Broker
                cls._broker_instance = MT5Broker()
                logger.info("Initialized MT5Broker via Factory.")
            else:
                raise ValueError(f"Unsupported broker type: {broker_type}")
        return cls._broker_instance

broker_factory = BrokerFactory()
