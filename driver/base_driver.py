"""
美团 AppDriver 抽象基类
所有 App Driver 必须实现此接口，保证框架可扩展性
"""

from abc import ABC, abstractmethod
from typing import Any


class BaseDriver(ABC):
    """
    统一 App Driver 接口协议

    设计原则：
      每个 App（美团/抖音/饿了么）实现此基类
      保证 AI / CLI 层对 App 无感知，可以统一调用
    """

    @abstractmethod
    def connect(self) -> "BaseDriver":
        """建立连接并注入 Hook"""
        ...

    @abstractmethod
    def disconnect(self):
        """断开连接，清理资源"""
        ...

    @abstractmethod
    def search(self, keyword: str, **kwargs) -> list[Any]:
        """通用搜索"""
        ...

    def __enter__(self):
        return self.connect()

    def __exit__(self, *args):
        self.disconnect()
