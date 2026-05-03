"""
美团 Driver — 核心业务接口层

把 Frida Hook 拦截到的原始 API 数据，
转换成干净的 Python 对象供 AI 直接使用。
"""

import json
import time
import logging
import threading
from typing import Optional
from datetime import datetime, timedelta

from bridge.frida_bridge import FridaBridge
from driver.models import (
    Restaurant, Menu, MenuCategory, MenuItem,
    CartItem, CartState, DeliveryAddress,
    Order, OrderStatus, OrderTracking
)

logger = logging.getLogger(__name__)


class MeituanDriver:
    """
    美团外卖 AI 驱动接口

    用法:
        driver = MeituanDriver()
        driver.connect()

        restaurants = driver.search_restaurants("麦当劳", "望京")
        menu = driver.get_menu(restaurants[0].id)
        item = menu.find_item("巨无霸")
        driver.add_to_cart(item.id, quantity=1)
        order = driver.place_order(address_id="xxx")
    """

    def __init__(self):
        self.bridge = FridaBridge()
        self._cache: dict = {}          # 简单内存缓存
        self._current_cart = CartState(restaurant_id="")

    # ──────────────── 生命周期 ────────────────

    def connect(self) -> "MeituanDriver":
        """连接到手机并注入 Hook"""
        self.bridge.connect()
        self.bridge.inject_hooks()

        # 验证 Hook 是否工作
        result = self.bridge.rpc_call("ping")
        if result.get("ok"):
            logger.info("✓ Hook 验证成功，驱动就绪")
        else:
            raise RuntimeError("Hook 验证失败，请检查 frida-server 和手机连接")

        return self

    def disconnect(self):
        self.bridge.disconnect()

    # ──────────────── 搜索餐厅 ────────────────

    def search_restaurants(
        self,
        keyword: str,
        address: str = "",
        lat: float = 0.0,
        lng: float = 0.0
    ) -> list[Restaurant]:
        """
        搜索附近餐厅

        原理：
          触发美团 App 内部搜索 → Hook 拦截 /waimai/poi/search API 响应
          → 解析 JSON → 返回 Restaurant 列表

        Args:
            keyword: 搜索关键词（如 "麦当劳"、"汉堡"）
            address: 地址字符串（可选，优先用 GPS 坐标）
            lat/lng: GPS 坐标（比地址字符串更精准）

        Returns:
            按距离排序的餐厅列表
        """
        logger.info(f"搜索餐厅: {keyword}")

        # 注册 URL 监听器，等待搜索结果
        result_data = None
        event = threading.Event()

        def on_search_response(payload):
            nonlocal result_data
            url = payload.get("url", "")
            if any(k in url for k in ["/search", "/poi", "waimai"]):
                result_data = payload.get("body", {})
                event.set()

        self.bridge.on_url("/search", on_search_response)
        self.bridge.on_url("/poi", on_search_response)

        # 触发搜索（通过 RPC 调用 App 内部搜索）
        self.bridge.rpc_call("triggerSearch", keyword)

        # 等待响应（最多 10 秒）
        if not event.wait(timeout=10.0):
            logger.warning("搜索超时，返回空列表")
            return []

        return self._parse_restaurant_list(result_data)

    def _parse_restaurant_list(self, data: dict) -> list[Restaurant]:
        """解析美团搜索 API 响应 → Restaurant 列表"""
        if not data or not isinstance(data, dict):
            return []

        restaurants = []

        # 美团 API 常见路径（根据实际抓包调整）
        # 尝试不同的响应结构
        items = (
            data.get("data", {}).get("poiList", []) or
            data.get("data", {}).get("list", []) or
            data.get("poiInfoList", []) or
            data.get("data", []) or
            []
        )

        for item in items:
            try:
                r = Restaurant(
                    id=str(item.get("poiId") or item.get("id", "")),
                    name=item.get("name", ""),
                    rating=float(item.get("avgScore") or item.get("wm_poi_score", 0)),
                    delivery_time=int(item.get("deliveryTime") or item.get("shipping_time", 30)),
                    delivery_fee=float(item.get("deliveryFee") or item.get("shipping_fee", 0)) / 100,
                    min_order=float(item.get("minOrderAmount") or item.get("min_price", 0)) / 100,
                    distance=float(item.get("distance", 0)),
                    address=item.get("address", ""),
                    category=item.get("frontCategoryName") or item.get("category_name", ""),
                    is_open=bool(item.get("isOpen", True)),
                    monthly_orders=int(item.get("monthSaleNum") or item.get("month_sale_num", 0)),
                )
                restaurants.append(r)
            except Exception as e:
                logger.debug(f"解析餐厅数据失败: {e} | 数据: {item}")

        logger.info(f"找到 {len(restaurants)} 家餐厅")
        return restaurants

    # ──────────────── 获取菜单 ────────────────

    def get_menu(self, restaurant_id: str) -> Menu:
        """
        获取指定餐厅的完整菜单

        原理：
          触发 App 进入餐厅详情页 → Hook 拦截 /food/menu API
          → 解析分类和菜品数据

        Returns:
            Menu 对象，包含所有分类和菜品（含价格、库存）
        """
        logger.info(f"获取菜单: restaurant_id={restaurant_id}")

        # 检查缓存
        cache_key = f"menu_{restaurant_id}"
        if cache_key in self._cache:
            logger.debug("命中菜单缓存")
            return self._cache[cache_key]

        result_data = None
        event = threading.Event()

        def on_menu_response(payload):
            nonlocal result_data
            result_data = payload.get("body", {})
            event.set()

        self.bridge.on_url("/food/", on_menu_response)
        self.bridge.on_url("/menu", on_menu_response)
        self.bridge.on_url(f"poiId={restaurant_id}", on_menu_response)

        # 通过 Intent 打开餐厅详情页
        import subprocess
        subprocess.run([
            "adb", "shell", "am", "start",
            "-a", "android.intent.action.VIEW",
            "-d", f"meituan://waimai/poi/{restaurant_id}",
        ], capture_output=True)

        if not event.wait(timeout=10.0):
            logger.warning("获取菜单超时")
            return Menu(restaurant_id=restaurant_id, restaurant_name="")

        menu = self._parse_menu(restaurant_id, result_data)
        self._cache[cache_key] = menu
        return menu

    def _parse_menu(self, restaurant_id: str, data: dict) -> Menu:
        """解析菜单 API 响应"""
        if not data:
            return Menu(restaurant_id=restaurant_id, restaurant_name="")

        # 提取餐厅名称
        restaurant_name = (
            data.get("data", {}).get("poiInfo", {}).get("name", "") or
            data.get("poiName", "") or ""
        )

        # 提取分类列表
        categories_data = (
            data.get("data", {}).get("foodSpu", {}).get("categories", []) or
            data.get("data", {}).get("categories", []) or
            data.get("categories", []) or
            []
        )

        categories = []
        for cat_data in categories_data:
            items = []
            for item_data in cat_data.get("spus", []) or cat_data.get("foods", []) or []:
                try:
                    # 价格单位：分 → 元
                    price_raw = item_data.get("min_price") or item_data.get("price", 0)
                    orig_price_raw = item_data.get("origin_price") or price_raw

                    item = MenuItem(
                        id=str(item_data.get("food_id") or item_data.get("id", "")),
                        name=item_data.get("name", ""),
                        price=float(price_raw) / 100,
                        original_price=float(orig_price_raw) / 100,
                        description=item_data.get("description", ""),
                        category=cat_data.get("name", ""),
                        is_available=bool(item_data.get("sold_out", 0) == 0),
                        monthly_sales=int(item_data.get("month_sale_num", 0)),
                    )
                    items.append(item)
                except Exception as e:
                    logger.debug(f"解析菜品失败: {e}")

            categories.append(MenuCategory(
                name=cat_data.get("name", ""),
                items=items
            ))

        menu = Menu(
            restaurant_id=restaurant_id,
            restaurant_name=restaurant_name,
            categories=categories
        )

        total = sum(len(c.items) for c in categories)
        logger.info(f"菜单加载完成: {len(categories)} 分类, {total} 道菜")
        return menu

    # ──────────────── 购物车 ────────────────

    def add_to_cart(
        self,
        restaurant_id: str,
        item_id: str,
        quantity: int = 1,
        item_name: str = ""
    ) -> CartState:
        """
        添加菜品到购物车

        原理：直接通过 ADB + UI 操作点击"加入购物车"按钮
        （比 Hook 更稳定，加购是 UI 强依赖操作）

        Returns:
            更新后的购物车状态
        """
        logger.info(f"加购: {item_name or item_id} x{quantity}")

        result_data = None
        event = threading.Event()

        def on_cart_response(payload):
            nonlocal result_data
            result_data = payload.get("body", {})
            event.set()

        self.bridge.on_url("/cart", on_cart_response)

        # TODO: 实现 UI 点击加购逻辑
        # 方案A：通过 Accessibility API 找到对应菜品的"+"按钮
        # 方案B：Hook CartManager.addItem() 直接调用
        logger.warning("add_to_cart: UI 操作层待实现，当前为 stub")

        event.wait(timeout=5.0)

        return self._current_cart

    # ──────────────── 下单 ────────────────

    def place_order(
        self,
        address_id: str,
        note: str = "",
        dry_run: bool = True
    ) -> Order:
        """
        提交订单

        ⚠️ 安全策略：dry_run=True 时（默认），
           流程只进行到"确认订单"页面，不自动付款。
           必须人工点击支付。

        Args:
            address_id: 收货地址 ID
            note: 备注
            dry_run: 为 True 时不触发付款，只展示订单确认页

        Returns:
            Order 对象（付款前为 PENDING_PAYMENT 状态）
        """
        if dry_run:
            logger.info("⚠️  dry_run 模式：将显示订单确认页，不自动付款")

        result_data = None
        event = threading.Event()

        def on_order_response(payload):
            nonlocal result_data
            url = payload.get("url", "")
            if "/order/" in url and "create" in url.lower():
                result_data = payload.get("body", {})
                event.set()

        self.bridge.on_url("/order/", on_order_response)

        # TODO: 实现下单操作
        logger.warning("place_order: 待实现，需要先完成购物车功能")

        event.wait(timeout=15.0)

        # 返回 stub 订单对象
        return Order(
            id="",
            restaurant_id="",
            restaurant_name="",
            items=[],
            total_price=0.0,
            delivery_address=DeliveryAddress(
                id=address_id, name="", phone="", address=""
            ),
            status=OrderStatus.PENDING_PAYMENT,
        )

    # ──────────────── 订单跟踪 ────────────────

    def track_order(self, order_id: str) -> OrderTracking:
        """
        实时获取订单状态

        原理：Hook 美团骑手位置 API，返回结构化跟踪信息
        """
        result_data = None
        event = threading.Event()

        def on_tracking_response(payload):
            nonlocal result_data
            result_data = payload.get("body", {})
            event.set()

        self.bridge.on_url("/order/track", on_tracking_response)
        self.bridge.on_url(f"orderId={order_id}", on_tracking_response)

        event.wait(timeout=5.0)

        return self._parse_tracking(order_id, result_data)

    def _parse_tracking(self, order_id: str, data: dict) -> OrderTracking:
        if not data:
            return OrderTracking(
                order_id=order_id,
                status=OrderStatus.DELIVERING,
                status_description="跟踪数据加载中...",
                estimated_minutes=30
            )

        status_code = data.get("data", {}).get("status", 0)
        STATUS_MAP = {
            1: OrderStatus.PAID,
            2: OrderStatus.MERCHANT_CONFIRMED,
            3: OrderStatus.PREPARING,
            4: OrderStatus.PICKED_UP,
            5: OrderStatus.DELIVERING,
            8: OrderStatus.DELIVERED,
        }

        return OrderTracking(
            order_id=order_id,
            status=STATUS_MAP.get(status_code, OrderStatus.DELIVERING),
            status_description=data.get("data", {}).get("statusDesc", ""),
            estimated_minutes=data.get("data", {}).get("remainTime", 0),
            rider_name=data.get("data", {}).get("riderName", ""),
            rider_phone=data.get("data", {}).get("riderPhone", ""),
        )

    # ──────────────── 获取地址列表 ────────────────

    def get_addresses(self) -> list[DeliveryAddress]:
        """获取用户收货地址列表"""
        result_data = None
        event = threading.Event()

        def on_addr_response(payload):
            nonlocal result_data
            result_data = payload.get("body", {})
            event.set()

        self.bridge.on_url("/address", on_addr_response)
        self.bridge.on_url("/user/addr", on_addr_response)

        event.wait(timeout=5.0)

        if not result_data:
            return []

        addr_list = (
            result_data.get("data", {}).get("addressList", []) or
            result_data.get("data", []) or []
        )

        addresses = []
        for addr in addr_list:
            addresses.append(DeliveryAddress(
                id=str(addr.get("addressId") or addr.get("id", "")),
                name=addr.get("recipientName") or addr.get("name", ""),
                phone=addr.get("recipientPhone") or addr.get("phone", ""),
                address=addr.get("address", ""),
                is_default=bool(addr.get("isDefault", False)),
            ))

        return addresses

    # ──────────────── 上下文管理器 ────────────────

    def __enter__(self):
        return self.connect()

    def __exit__(self, *args):
        self.disconnect()
