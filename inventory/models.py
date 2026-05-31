from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


@dataclass
class Supplier:
    id: Optional[int]
    name: str
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    lead_time_days: int = 0

    def __post_init__(self):
        if not self.name or not self.name.strip():
            raise ValueError("name must not be empty")
        if self.lead_time_days < 0:
            raise ValueError("lead_time_days must be non-negative")


class OrderType(str, Enum):
    PURCHASE = "purchase"  # stock in
    SALE = "sale"          # stock out


class OrderStatus(str, Enum):
    PENDING = "pending"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"


@dataclass
class Product:
    id: Optional[int]
    sku: str
    name: str
    unit_price: float
    reorder_threshold: int = 10
    supplier_id: Optional[int] = None

    def __post_init__(self):
        if self.unit_price < 0:
            raise ValueError("unit_price must be non-negative")
        if self.reorder_threshold < 0:
            raise ValueError("reorder_threshold must be non-negative")


@dataclass
class StockLevel:
    product_id: int
    quantity: int = 0

    def __post_init__(self):
        if self.quantity < 0:
            raise ValueError("quantity must be non-negative")


@dataclass
class Order:
    id: Optional[int]
    product_id: int
    order_type: OrderType
    quantity: int
    unit_price: float
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    product_name: Optional[str] = None
    product_sku: Optional[str] = None

    @property
    def total(self) -> float:
        return round(self.quantity * self.unit_price, 2)

    def __post_init__(self):
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.unit_price < 0:
            raise ValueError("unit_price must be non-negative")


@dataclass
class Alert:
    id: Optional[int]
    product_id: int
    message: str
    quantity_at_alert: int
    created_at: datetime = field(default_factory=datetime.utcnow)
    acknowledged: bool = False
