"""
美团 CLI 工具

用法:
    meituan search "麦当劳"
    meituan menu --rid <restaurant_id>
    meituan order --rid <restaurant_id> --items "巨无霸:1,薯条:1"
    meituan track --oid <order_id>
"""

import json
import sys
import logging
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import print as rprint

# 添加项目根路径
sys.path.insert(0, __file__.replace("/cli/main.py", ""))

from driver.meituan_driver import MeituanDriver
from driver.models import Restaurant, Menu, OrderTracking

app = typer.Typer(
    name="meituan",
    help="🍔 美团外卖 AI 驱动 CLI — 把外卖变成函数调用",
    add_completion=False,
)
console = Console()

# 全局 driver（延迟初始化）
_driver: Optional[MeituanDriver] = None


def get_driver() -> MeituanDriver:
    global _driver
    if _driver is None:
        _driver = MeituanDriver()
        with console.status("[bold green]正在连接手机..."):
            _driver.connect()
    return _driver


# ──────────────── 搜索餐厅 ────────────────

@app.command("search")
def search(
    keyword: str = typer.Argument(..., help="搜索关键词，如 '麦当劳'"),
    address: str = typer.Option("", "--address", "-a", help="送餐地址"),
    output_json: bool = typer.Option(False, "--json", help="输出 JSON 格式（供 AI 使用）"),
    limit: int = typer.Option(10, "--limit", "-n", help="最多显示几家"),
):
    """搜索附近外卖餐厅"""
    driver = get_driver()

    with console.status(f"[bold blue]正在搜索 '{keyword}'..."):
        results = driver.search_restaurants(keyword, address)

    results = results[:limit]

    if output_json:
        data = [
            {
                "id": r.id,
                "name": r.name,
                "rating": r.rating,
                "delivery_time": r.delivery_time,
                "delivery_fee": r.delivery_fee,
                "min_order": r.min_order,
                "distance": r.distance,
                "category": r.category,
                "monthly_orders": r.monthly_orders,
            }
            for r in results
        ]
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if not results:
        console.print("[yellow]未找到餐厅[/yellow]")
        return

    table = Table(title=f"🔍 搜索结果: {keyword}", show_header=True, header_style="bold cyan")
    table.add_column("ID", style="dim", width=12)
    table.add_column("名称", style="bold")
    table.add_column("评分", justify="center")
    table.add_column("配送时间", justify="center")
    table.add_column("配送费", justify="right")
    table.add_column("起送", justify="right")
    table.add_column("距离", justify="right")
    table.add_column("月销", justify="right")

    for r in results:
        table.add_row(
            r.id[:8] + "...",
            r.name,
            f"⭐ {r.rating:.1f}",
            f"⏱ {r.delivery_time}min",
            f"¥{r.delivery_fee:.1f}",
            f"¥{r.min_order:.0f}",
            f"{r.distance:.0f}m",
            f"{r.monthly_orders}",
        )

    console.print(table)


# ──────────────── 查看菜单 ────────────────

@app.command("menu")
def menu_cmd(
    restaurant_id: str = typer.Option(..., "--rid", "-r", help="餐厅 ID"),
    output_json: bool = typer.Option(False, "--json", help="输出 JSON 格式"),
    category: str = typer.Option("", "--cat", "-c", help="筛选分类"),
):
    """查看餐厅菜单"""
    driver = get_driver()

    with console.status("[bold blue]正在获取菜单..."):
        menu = driver.get_menu(restaurant_id)

    if output_json:
        data = {
            "restaurant_id": menu.restaurant_id,
            "restaurant_name": menu.restaurant_name,
            "categories": [
                {
                    "name": cat.name,
                    "items": [
                        {
                            "id": item.id,
                            "name": item.name,
                            "price": item.price,
                            "description": item.description,
                            "available": item.is_available,
                            "monthly_sales": item.monthly_sales,
                        }
                        for item in cat.items
                    ]
                }
                for cat in menu.categories
                if not category or category in cat.name
            ]
        }
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    console.print(Panel(
        f"[bold]{menu.restaurant_name}[/bold]",
        title="🍴 菜单",
        border_style="green"
    ))

    for cat in menu.categories:
        if category and category not in cat.name:
            continue

        table = Table(title=f"📂 {cat.name}", show_header=True, header_style="bold yellow")
        table.add_column("ID", style="dim", width=12)
        table.add_column("菜品名称", style="bold")
        table.add_column("价格", justify="right", style="green")
        table.add_column("月销量", justify="right")
        table.add_column("状态", justify="center")

        for item in cat.items:
            table.add_row(
                item.id[:8] + "...",
                item.name,
                f"¥{item.price:.1f}",
                str(item.monthly_sales),
                "✅" if item.is_available else "❌ 售罄",
            )

        console.print(table)


# ──────────────── 下单 ────────────────

@app.command("order")
def order_cmd(
    restaurant_id: str = typer.Option(..., "--rid", "-r", help="餐厅 ID"),
    items: str = typer.Option(..., "--items", "-i", help="菜品列表，格式: '菜品名:数量,菜品名:数量'"),
    address_id: str = typer.Option("", "--addr", "-a", help="收货地址 ID（不填则使用默认地址）"),
    note: str = typer.Option("", "--note", "-n", help="订单备注"),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run", help="是否为演练模式（不实际付款）"),
):
    """下外卖订单（默认不自动付款，需人工确认）"""
    driver = get_driver()

    # 解析菜品列表
    item_list = []
    for part in items.split(","):
        if ":" in part:
            name, qty = part.rsplit(":", 1)
            item_list.append((name.strip(), int(qty.strip())))
        else:
            item_list.append((part.strip(), 1))

    console.print(f"📋 即将下单：")
    for name, qty in item_list:
        console.print(f"   - {name} x{qty}")

    if dry_run:
        console.print("\n[yellow]⚠️  演练模式：将显示订单确认页，不自动付款[/yellow]")

    # 获取菜单并加购
    with console.status("[bold blue]获取菜单并加购..."):
        menu = driver.get_menu(restaurant_id)

    for name, qty in item_list:
        item = menu.find_item(name)
        if not item:
            console.print(f"[red]❌ 未找到菜品: {name}[/red]")
            raise typer.Exit(1)

        driver.add_to_cart(restaurant_id, item.id, qty, item.name)

    # 下单
    with console.status("[bold blue]提交订单..."):
        order = driver.place_order(
            address_id=address_id,
            note=note,
            dry_run=dry_run
        )

    if dry_run:
        console.print(Panel(
            "[bold yellow]📱 请在手机上查看订单确认页并手动完成支付[/bold yellow]",
            border_style="yellow"
        ))
    else:
        console.print(f"[green]✅ 订单提交成功: {order.id}[/green]")


# ──────────────── 订单跟踪 ────────────────

@app.command("track")
def track(
    order_id: str = typer.Option(..., "--oid", "-o", help="订单 ID"),
    output_json: bool = typer.Option(False, "--json"),
    watch: bool = typer.Option(False, "--watch", "-w", help="持续刷新跟踪状态"),
):
    """实时跟踪订单状态"""
    driver = get_driver()

    def show_tracking(t: OrderTracking):
        if output_json:
            print(json.dumps({
                "order_id": t.order_id,
                "status": t.status.value,
                "description": t.status_description,
                "estimated_minutes": t.estimated_minutes,
                "rider_name": t.rider_name,
                "rider_phone": t.rider_phone,
            }, ensure_ascii=False))
        else:
            console.print(Panel(
                f"状态: [bold green]{t.status.value}[/bold green]\n"
                f"描述: {t.status_description}\n"
                f"预计剩余: {t.estimated_minutes} 分钟\n"
                f"骑手: {t.rider_name or 'N/A'} {t.rider_phone or ''}",
                title=f"🛵 订单跟踪 {order_id[:8]}...",
                border_style="blue"
            ))

    if watch:
        import time
        while True:
            tracking = driver.track_order(order_id)
            show_tracking(tracking)
            if tracking.status.value in ["已送达", "已取消"]:
                break
            time.sleep(30)
    else:
        tracking = driver.track_order(order_id)
        show_tracking(tracking)


# ──────────────── 调试命令 ────────────────

@app.command("ping")
def ping():
    """测试 Hook 是否正常工作"""
    driver = get_driver()
    result = driver.bridge.rpc_call("ping")
    if result.get("ok"):
        console.print("[green]✅ Hook 工作正常[/green]")
    else:
        console.print("[red]❌ Hook 异常[/red]")


@app.command("sniff")
def sniff(
    pattern: str = typer.Option("", "--url", "-u", help="URL 过滤模式"),
    count: int = typer.Option(0, "--count", "-n", help="捕获数量（0=无限）"),
):
    """监听并打印美团 API 数据（调试用）"""
    driver = get_driver()
    captured = 0

    def on_message(payload):
        nonlocal captured
        if pattern and pattern not in payload.get("url", ""):
            return
        console.print(f"[dim]{payload.get('type', 'msg')}[/dim] {payload.get('url', '')[:80]}")
        body = payload.get("body", {})
        console.print(json.dumps(body, ensure_ascii=False, indent=2)[:500])
        console.print("─" * 60)
        captured += 1
        if count > 0 and captured >= count:
            raise KeyboardInterrupt

    driver.bridge.on_any(on_message)

    console.print(f"[bold]👂 开始监听 API 数据... (Ctrl+C 停止)[/bold]")
    try:
        import time
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        console.print(f"\n[dim]共捕获 {captured} 条消息[/dim]")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app()
