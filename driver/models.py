"""
美团业务数据模型
所有数据均为结构化 Python 对象，来源于 Frida Hook 拦截的 API 数据
"""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
from enum import Enum


# ─────────────────────────── 餐厅 ───────────────────────────

@dataclass
class Restaurant:
    id: str
    name: str
    rating: float           # 评分 0-10
    delivery_time: int      # 预计送达分钟数
    delivery_fee: float     # 配送费（元）
    min_order: float        # 起送金额（元）
    distance: float         # 距离（米）
    address: str
    category: str           # 分类（快餐/中式/西式...）
    is_open: bool = True
    monthly_orders: int = 0  # 月销量

    def __str__(self):
        return (f"[{self.name}] 评分:{self.rating} "
                f"距离:{self.distance:.0f}m 配送费:¥{self.delivery_fee} "
                f"起送:¥{self.min_order}")


# ─────────────────────────── 菜单 ───────────────────────────

@dataclass
class MenuItem:
    id: str
    name: str
    price: float            # 元
    original_price: float   # 原价（用于展示折扣）
    description: str = ""
    category: str = ""
    is_available: bool = True
    monthly_sales: int = 0
    image_url: str = ""

    def __str__(self):
        return f"[{self.name}] ¥{self.price} ({self.category})"


@dataclass
class MenuCategory:
    name: str
    items: list[MenuItem] = field(default_factory=list)


@dataclass
class Menu:
    restaurant_id: str
    restaurant_name: str
    categories: list[MenuCategory] = field(default_factory=list)

    def all_items(self) -> list[MenuItem]:
        """返回全部菜品（展平）"""
        return [item for cat in self.categories for item in cat.items]

    def find_item(self, name: str) -> Optional[MenuItem]:
        """按名称模糊查找菜品"""
        name_lower = name.lower()
        for item in self.all_items():
            if name_lower in item.name.lower():
                return item
        return None

    def available_items(self) -> list[MenuItem]:
        return [i for i in self.all_items() if i.is_available]


# ─────────────────────────── 购物车 ───────────────────────────

@dataclass
class CartItem:
    item: MenuItem
    quantity: int

    @property
    def subtotal(self) -> float:
        return self.item.price * self.quantity


@dataclass
class CartState:
    restaurant_id: str
    items: list[CartItem] = field(default_factory=list)

    @property
    def total(self) -> float:
        return sum(ci.subtotal for ci in self.items)

    @property
    def item_count(self) -> int:
        return sum(ci.quantity for ci in self.items)

    def __str__(self):
        lines = [f"购物车 共 {self.item_count} 件 合计 ¥{self.total:.2f}"]
        for ci in self.items:
            lines.append(f"  - {ci.item.name} x{ci.quantity} = ¥{ci.subtotal:.2f}")
        return "\n".join(lines)


# ─────────────────────────── 地址 ───────────────────────────

@dataclass
class DeliveryAddress:
    id: str
    name: str           # 收件人姓名
    phone: str          # 手机号
    address: str        # 详细地址
    is_default: bool = False

    def __str__(self):
        return f"{self.name} {self.phone} | {self.address}"


# ─────────────────────────── 订单 ───────────────────────────

class OrderStatus(Enum):
    PENDING_PAYMENT = "待付款"
    PAID = "已付款"
    MERCHANT_CONFIRMED = "商家确认"
    PREPARING = "备餐中"
    PICKED_UP = "骑手已取餐"
    DELIVERING = "配送中"
    DELIVERED = "已送达"
    CANCELLED = "已取消"


@dataclass
class Order:
    id: str
    restaurant_id: str
    restaurant_name: str
    items: list[CartItem]
    total_price: float
    delivery_address: DeliveryAddress
    status: OrderStatus
    created_at: datetime = field(default_factory=datetime.now)
    estimated_delivery_at: Optional[datetime] = None
    rider_name: Optional[str] = None
    rider_phone: Optional[str] = None

    def __str__(self):
        return (f"订单[{self.id}] {self.restaurant_name} "
                f"¥{self.total_price} 状态:{self.status.value}")


@dataclass
class OrderTracking:
    order_id: str
    status: OrderStatus
    status_description: str     # 详细描述
    estimated_minutes: int      # 预计剩余分钟
    rider_name: Optional[str] = None
    rider_phone: Optional[str] = None
    rider_location: Optional[tuple[float, float]] = None  # (lat, lng)
    events: list[dict] = field(default_factory=list)      # 时间线事件
